# -*- coding: utf-8 -*-
"""
WPS 多维表格同步（sync_bitable）— 模块 F（M2 对接）

独立对接层，只消费 output/latest.json（附件通过 item.pdf_path / content_ref 定位）：
- 每个采集器一行：有信息才建/更新行，无信息不动（历史行保留）
- 每行字段：信息源 | 总结 | 附件链接 | 原始链接 | 分类 | 可信度
- 序号、日期由表格字段类型自动生成（自动编号 / 创建时间），本模块不写入
- upsert 查重键：信息源 + 创建时间落在今天 → update，否则 create
- 附件（方案 A）：开放平台服务端无素材库上传接口，"附件"字段无法程序化写入；
  改为云文档三步上传（OAuth 可用的唯一上传通道）拿到 link_url，写入"附件链接"字段。
  有 PDF 传 PDF；无 PDF 读正文（content_ref）转 txt 上传。
- 同步失败不影响本地产物（try/except 兜底）

前置依赖（在 open.wps.cn 创建企业应用 + 授权目标表格后，填入 .env）：
    WPS_APP_ID      客户端 ID（client_id）
    WPS_APP_KEY     客户端密钥（client_secret）
    WPS_FILE_ID     目标多维表格 file_id（表格 URL 中可查）
    WPS_SHEET_ID    目标工作表 sheet_id

授权说明（重要）：
- dbsheet 是"用户级"数据接口：应用 token（client_credentials）只能换来
  403 unable to read user permission，必须使用用户 access_token（OAuth 授权码模式）。
- 首次运行 scripts/wps_authorize.py 完成一次浏览器授权，把用户 token 持久化到
  output/wps_token.json（access_token 约 2h，refresh_token 约 365 天）；
  之后脚本自动读文件、过期自动刷新，无需再次授权。

说明：
- 数据接口除 access_token 外还需 KSO-1 签名请求头，见 BitableClient._sign()。
- WPS 开放 API 的具体路径/参数以 open.wps.cn 官方文档（API 调试台）为准；
  本模块将接口常量集中定义（BASE_URL / API 路径），联调时如有出入只改常量。
"""
import hashlib
import hmac
import json
import logging
import time
from collections import Counter
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from config.settings import (
    BITABLE,
    CATEGORIES,
    COLLECTORS,
    LATEST_JSON,
    OUTPUT_DIR,
)

# ---------------------------------------------------------------- 常量

# 用户级 access_token 有效期约 2 小时，留 10 分钟余量提前刷新
TOKEN_TTL_SECONDS = 2 * 3600 - 600

# 附件方案（实测打通）：云文档三步上传后，附件结构体（source=cloud, uploadId=短链接后缀）
# 写入"附件"字段（原生卡片渲染+在线预览）；link_url 同时写"附件链接"字段作纯文本备份。
ATTACH_FIELD = "附件"
ATTACH_URL_FIELD = "附件链接"

# 记录接口路径（以官方文档为准，联调如有出入只改这里）
API_RECORDS_GET = "/v7/coop/dbsheet/{file_id}/sheets/{sheet_id}/records"
API_RECORDS_CREATE = "/v7/coop/dbsheet/{file_id}/sheets/{sheet_id}/records/create"
API_RECORDS_UPDATE = "/v7/coop/dbsheet/{file_id}/sheets/{sheet_id}/records/update"

# 云文档三步上传（服务端唯一可用的上传通道，产出 link_url；drive_id/parent_id 为路径参数）
API_DRIVES = "/v7/drives"
API_DRIVE_UPLOAD_REQUEST = "/v7/drives/{drive_id}/files/{parent_id}/request_upload"
API_DRIVE_UPLOAD_COMMIT = "/v7/drives/{drive_id}/files/{parent_id}/commit_upload"


class BitableSyncError(Exception):
    """同步过程错误（调用方捕获后仅告警，不影响本地产物）"""


class BitableClient:
    """WPS 多维表格 API 客户端（用户级 token + KSO-1 签名）"""

    def __init__(
        self,
        app_id: str,
        app_key: str,
        file_id: str,
        sheet_id: str,
        base_url: str = "https://openapi.wps.cn",
        token_file: Optional[Path] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.app_id = app_id
        self.app_key = app_key
        self.file_id = file_id
        self.sheet_id = sheet_id
        self.base_url = base_url.rstrip("/")
        self.token_file = token_file or Path(
            BITABLE.get("token_file", "output/wps_token.json")
        )
        self.logger = logger or logging.getLogger("sync_bitable")
        self._token: Optional[str] = None
        self._token_ts: float = 0.0
        self._drive_id: Optional[str] = None

    # ---------------- token ----------------
    # 说明：dbsheet 是"用户级"数据接口，必须用用户 access_token（OAuth 授权码模式），
    # 应用 token（client_credentials）只能换来 403 unable to read user permission。
    # 首次运行 scripts/wps_authorize.py 换取用户 token 并持久化到 token_file，
    # 之后 access_token 过期自动用 refresh_token 刷新（refresh_token 有效期 365 天）。

    def _load_token_file(self) -> Dict[str, Any]:
        """读取本地持久化的用户 token（默认 output/wps_token.json）"""
        path = self.token_file
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_token_file(self, data: Dict[str, Any]) -> None:
        """持久化用户 token，供授权脚本与同步脚本共享"""
        path = self.token_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _normalize_token_data(data: Dict[str, Any]) -> Dict[str, Any]:
        """把 oauth2/token 响应转成内部持久化结构（含过期时间戳，预留 10 分钟余量）"""
        now = time.time()
        return {
            "access_token": data.get("access_token", ""),
            "refresh_token": data.get("refresh_token", ""),
            "expires_at": now + float(data.get("expires_in", TOKEN_TTL_SECONDS)) - 600,
            "refresh_expires_at": now + float(data.get("refresh_expires_in", 31536000)),
        }

    def _refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        """用 refresh_token 刷新用户 access_token（refresh_token 有效期约 365 天）"""
        resp = requests.post(
            f"{self.base_url}/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "client_id": self.app_id,
                "client_secret": self.app_key,
                "refresh_token": refresh_token,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise BitableSyncError(
                f"刷新 access_token 失败: HTTP {resp.status_code} {resp.text[:300]}"
            )
        data = resp.json()
        if not data.get("access_token"):
            raise BitableSyncError(f"刷新 access_token 响应缺少 access_token: {data}")
        return self._normalize_token_data(data)

    def _get_token(self) -> str:
        """获取用户级 access_token：内存缓存 → 文件持久化 → refresh_token 自动刷新"""
        if self._token and (time.time() - self._token_ts) < TOKEN_TTL_SECONDS:
            return self._token
        stored = self._load_token_file()
        now = time.time()
        if stored.get("access_token") and now < float(stored.get("expires_at", 0)):
            self._token = stored["access_token"]
            self._token_ts = now
            return self._token
        if stored.get("refresh_token"):
            try:
                refreshed = self._refresh_token(stored["refresh_token"])
            except BitableSyncError as e:
                raise BitableSyncError(
                    f"用户 token 已失效，请重新授权: python scripts/wps_authorize.py（{e}）"
                ) from e
            self._save_token_file(refreshed)
            self._token = refreshed["access_token"]
            self._token_ts = time.time()
            return self._token
        raise BitableSyncError(
            "缺少用户 access_token，请先运行授权脚本: python scripts/wps_authorize.py"
        )

    # ---------------- KSO-1 签名 ----------------

    @staticmethod
    def _sign(app_key: str, method: str, uri: str, content_type: str, body: str, kso_date: str) -> str:
        """
        KSO-1 签名（除 access_token 外的必选请求头）：
        HMAC-SHA256(APP_KEY, "KSO-1" + Method + URI + ContentType + KsoDate + sha256(body))
        注：签名串拼接细节以 open.wps.cn 官方文档为准，联调时如有出入只改本方法。
        """
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        string_to_sign = (
            "KSO-1" + method.upper() + uri + content_type + kso_date + body_hash
        )
        signature = hmac.new(
            app_key.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return signature

    # ---------------- 请求 ----------------

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        """带签名与 token 的请求。path 为相对路径（不含 host），body 按 JSON 编码。"""
        if body is None:
            body = {}
        # GET 请求无请求体，签名与发送都按空 body 处理（KSO-1 签名串与之一致）
        is_get = method.upper() == "GET"
        body_str = "" if is_get else json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        content_type = "application/json"
        # Kso-Date 格式（GMT RFC1123，以官方为准）
        kso_date = format_datetime(datetime.now(timezone.utc), usegmt=True)
        token = self._get_token()

        uri = path.format(file_id=self.file_id, sheet_id=self.sheet_id)
        signature = self._sign(self.app_key, method, uri, content_type, body_str, kso_date)

        # 官方要求的请求头：Authorization: Bearer + X-Kso-Date + X-Kso-Authorization: KSO-1 {app_id}:{sig}
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Kso-Date": kso_date,
            "X-Kso-Authorization": f"KSO-1 {self.app_id}:{signature}",
            "X-Kso-Id-Type": "internal",
            "Content-Type": content_type,
        }
        # GET 不带请求体（部分接口如 /v7/drives 拒绝空 body）
        send_data = None if is_get else body_str
        resp = requests.request(
            method, f"{self.base_url}{uri}", headers=headers, data=send_data, timeout=60
        )
        if resp.status_code != 200:
            raise BitableSyncError(
                f"{method} {uri} -> HTTP {resp.status_code}: {resp.text[:300]}"
            )
        return resp.json()

    # ---------------- 记录 CRUD ----------------

    def list_records(self, criteria: Optional[List[dict]] = None) -> List[dict]:
        """
        查记录。criteria 非空时按字段过滤（[{field, operator, values}]）。
        游标分页拉全量（page_token 为空即末页；若响应无 page_token 则只拉一页）。
        """
        records: List[dict] = []
        page_token = ""
        for _ in range(100):  # 兜底防死循环（100 页 = 1 万条/源，远超单源每日 1 行的量级）
            body: Dict[str, Any] = {
                "prefer_id": False,
                "page_size": 100,
                "page_token": page_token,
                "fields": [],
                "filter": None,
            }
            if criteria:
                body["filter"] = {"mode": "AND", "criteria": criteria}
            data = self._request("POST", API_RECORDS_GET, body)
            page = data.get("data", {}).get("records") or []
            records.extend(page)
            page_token = data.get("data", {}).get("page_token") or ""
            if not page_token:
                break
        return records

    def create_record(self, fields: Dict[str, Any]) -> str:
        """创建记录，返回 record_id"""
        # 官方 create 请求体：records 数组包裹，fields_value 为 JSON 字符串；
        # 响应结构 data.records[]，记录 ID 在 records[0].id（实测不是 record_id 顶层字段）
        data = self._request(
            "POST", API_RECORDS_CREATE,
            {
                "prefer_id": False,
                "records": [{"fields_value": json.dumps(fields, ensure_ascii=False)}],
            },
        )
        records = (data.get("data") or {}).get("records") or []
        if not records:
            raise BitableSyncError(f"创建记录响应缺少 records: {data}")
        record_id = records[0].get("id") or records[0].get("record_id")
        if not record_id:
            raise BitableSyncError(f"创建记录响应缺少 record_id: {data}")
        return record_id

    def update_record(self, record_id: str, fields: Dict[str, Any]) -> None:
        """更新记录（按 record_id）"""
        # 官方 update 请求体：records 数组，记录 ID 字段名为 id（不是 record_id）
        self._request(
            "POST", API_RECORDS_UPDATE,
            {
                "records": [{
                    "id": record_id,
                    "fields_value": json.dumps(fields, ensure_ascii=False),
                }],
            },
        )

    # ---------------- 附件上传（方案 A：云文档三步上传取链接） ----------------

    def _get_drive_id(self) -> str:
        """获取默认云盘 drive_id（内存缓存，避免每次上传都拉一次盘列表）。
        注意：接口要求 allotee_type=user&page_size=100，缺参返回 400。"""
        if self._drive_id:
            return self._drive_id
        data = self._request("GET", API_DRIVES + "?allotee_type=user&page_size=100")
        items = (data.get("data") or {}).get("items") or []
        if not items:
            raise BitableSyncError("云盘列表为空，无法上传附件")
        self._drive_id = items[0].get("id") or items[0].get("drive_id")
        if not self._drive_id:
            raise BitableSyncError(f"云盘列表缺少 drive_id: {items[:1]}")
        return self._drive_id

    def upload_to_drive(self, file_name: str, content: bytes) -> Dict[str, Any]:
        """
        云文档三步上传（OAuth 可用）：request_upload → PUT 实体 → commit_upload。
        返回 cloud 附件结构体（可直接写多维表格"附件"字段，实测支持卡片渲染 + 在线预览）：
          {fileName, size, source:"cloud", type, uploadId, linkUrl}
        uploadId = link_url 短链接后缀（如 clbXHQnvCBzo），注意不是云文档 fileId/UniqueID。
        """
        drive_id = self._get_drive_id()
        parent_id = "0"  # 根目录（如需归档可改为云盘内文件夹 id）
        uri_request = API_DRIVE_UPLOAD_REQUEST.format(
            drive_id=drive_id, parent_id=parent_id
        )
        body = {
            "name": file_name,
            "size": len(content),
            "hashes": [{"sum": hashlib.md5(content).hexdigest(), "type": "md5"}],
            "on_name_conflict": "rename",
        }
        # C1: 请求上传信息（拿对象存储地址与 upload_id）
        data1 = self._request("POST", uri_request, body)
        d1 = data1.get("data") or {}
        upload_id = d1.get("upload_id")
        store = d1.get("store_request") or {}
        if not upload_id or not store.get("url"):
            raise BitableSyncError(f"request_upload 响应缺少 upload_id/store: {data1}")
        # C2: 上传实体到对象存储（需透传与申请接口相同的 KSO-1 鉴权头，实测必需）
        body_str = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        kso_date = format_datetime(datetime.now(timezone.utc), usegmt=True)
        token = self._get_token()
        signature = self._sign(
            self.app_key, "POST", uri_request, "application/json", body_str, kso_date
        )
        headers2 = {
            "Authorization": f"Bearer {token}",
            "X-Kso-Date": kso_date,
            "X-Kso-Authorization": f"KSO-1 {self.app_id}:{signature}",
            "X-Kso-Id-Type": "internal",
        }
        resp2 = requests.request(
            store.get("method", "PUT"), store["url"], data=content,
            headers=headers2, timeout=180,
        )
        if resp2.status_code >= 300:
            raise BitableSyncError(
                f"{file_name} 上传实体失败: HTTP {resp2.status_code} {resp2.text[:200]}"
            )
        # C3: 提交上传完成（拿 link_url）
        uri_commit = API_DRIVE_UPLOAD_COMMIT.format(
            drive_id=drive_id, parent_id=parent_id
        )
        data3 = self._request("POST", uri_commit, {"upload_id": upload_id})
        link_url = (data3.get("data") or {}).get("link_url")
        if not link_url:
            raise BitableSyncError(f"commit_upload 响应缺少 link_url: {data3}")
        upload_id = link_url.rstrip("/").rsplit("/", 1)[-1]
        ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else "txt"
        self.logger.info(f"[附件] 已上传云文档: {file_name} -> {link_url}")
        return {
            "fileName": file_name,
            "size": len(content),
            "source": "cloud",
            "type": ext,
            "uploadId": upload_id,
            "linkUrl": link_url,
        }


# ---------------------------------------------------------------- 数据组装

# 正文转 txt 单文件字符上限（base64 膨胀约 1/3，20KB 文本 ≈ 27KB base64，远低于直写阈值）
MAX_CONTENT_CHARS = 20000


def load_latest() -> Dict[str, Any]:
    """读取主产物 latest.json；缺失时抛 BitableSyncError。"""
    if not LATEST_JSON.exists():
        raise BitableSyncError(f"主产物缺失: {LATEST_JSON}，请先运行采集+汇总")
    return json.loads(LATEST_JSON.read_text(encoding="utf-8"))


def _source_display_name(source_code: str) -> str:
    """source_code -> 源中文名（settings.COLLECTORS 有配置则用之，否则保留代号）"""
    if not source_code:
        return "未知来源"
    cfg = COLLECTORS.get(source_code)
    return (cfg or {}).get("name") or source_code


def _parse_publish_ts(value) -> Optional[float]:
    """解析发布时间为时间戳；无效/缺失返回 None（排序时兜底放组内末尾）"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return None


def _read_content_text(it: Dict[str, Any]) -> str:
    """读正文全文（content_ref + batch_dir 定位）；无正文返回空串"""
    ref = (it.get("content_ref") or "").strip()
    batch_dir = (it.get("batch_dir") or "").strip()
    if not ref or not batch_dir:
        return ""
    p = Path(batch_dir) / ref
    if not p.exists():
        return ""
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        try:
            text = p.read_text(encoding="gbk", errors="replace")
        except OSError:
            return ""
    return text.strip()[:MAX_CONTENT_CHARS]


def _build_source_summary(group: List[Dict[str, Any]]) -> str:
    """单源总结：编号 + 标题 + 缩进摘要（按发布时间倒序，无有效时间放末尾）"""
    group = sorted(
        group, key=lambda it: -(_parse_publish_ts(it.get("publish_time")) or 0)
    )
    lines: List[str] = []
    for idx, it in enumerate(group, 1):
        title = (it.get("title") or "").strip()
        lines.append(f"{idx:02d} {title}")
        summary = (it.get("summary") or "").strip()
        if summary:
            lines.append(f"   · {summary}")
    return "\n".join(lines) or "(无内容)"


def _build_source_links(group: List[Dict[str, Any]]) -> str:
    """原始链接：该源全部条目 url，每条一行"""
    return "\n".join(
        (it.get("url") or "").strip() for it in group if (it.get("url") or "").strip()
    )


def _build_source_categories(group: List[Dict[str, Any]]) -> List[str]:
    """分类：全部列出，按命中条数降序（条数相同按 settings.CATEGORIES 定义顺序）"""
    counts = Counter(
        (it.get("category") or "").strip()
        for it in group
        if (it.get("category") or "").strip()
    )
    if not counts:
        return []
    order = {c: i for i, c in enumerate(CATEGORIES.keys())}
    return [c for c, _ in sorted(counts.items(), key=lambda kv: (-kv[1], order.get(kv[0], 999)))]


def _collect_source_attachments(
    source_code: str, group: List[Dict[str, Any]]
) -> Tuple[List[Path], List[Tuple[str, str]]]:
    """按源收集附件：返回 (pdf文件列表, [(txt文件名, 正文), ...])。
    条目优先用 PDF（item.pdf_path）；无 PDF 读正文转 txt；两者皆无则跳过。"""
    pdfs: List[Path] = []
    txts: List[Tuple[str, str]] = []
    for idx, it in enumerate(group, 1):
        pdf_rel = (it.get("pdf_path") or "").strip()
        if pdf_rel:
            p = OUTPUT_DIR / pdf_rel
            if p.exists() and p.suffix.lower() == ".pdf":
                pdfs.append(p)
                continue
        text = _read_content_text(it)
        if text:
            from utils import sanitize_filename
            title_short = sanitize_filename(it.get("title", ""))
            fname = f"{source_code}_{title_short}.txt" if title_short else f"{source_code}_{idx:03d}.txt"
            txts.append((fname, text))
    return pdfs, txts


def _upload_attachments(
    client: BitableClient, pdfs: List[Path], txts: List[Tuple[str, str]]
) -> List[Dict[str, Any]]:
    """把 PDF + 正文 txt 逐个上传云文档，返回 cloud 附件结构体数组（可写"附件"字段）。
    单个文件失败仅记日志并跳过（附件是增强信息，不阻断整行同步）。"""
    items: List[Dict[str, Any]] = []
    for pdf in pdfs:
        try:
            items.append(client.upload_to_drive(pdf.name, pdf.read_bytes()))
        except BitableSyncError as e:
            client.logger.error(f"[附件] 上传失败 {pdf.name}: {e}")
    for name, text in txts:
        try:
            items.append(client.upload_to_drive(name, text.encode("utf-8")))
        except BitableSyncError as e:
            client.logger.error(f"[附件] 上传失败 {name}: {e}")
    return items


def _build_source_record(source_code: str, group: List[Dict[str, Any]]) -> Dict[str, Any]:
    """按源组装一条记录（列名以 BITABLE.field_map 为准）。
    附件列为内部结构 {pdfs, txts}：dry-run 仅展示；同步时由 _upload_attachments
    上传云文档，写"附件"字段（卡片）+ "附件链接"字段（备份链接）。"""
    field_map = BITABLE.get("field_map", {})
    # 表格 SingleSelect 选项是"官方公告/官方资讯/媒体报道"，配置值带【】装饰需剥掉
    credibility = (
        (COLLECTORS.get(source_code) or {}).get("credibility", "").strip("【】")
    )
    pdfs, txts = _collect_source_attachments(source_code, group)
    return {
        field_map.get("source", "信息源"): _source_display_name(source_code),
        field_map.get("summary", "总结"): _build_source_summary(group),
        field_map.get("attachments", ATTACH_FIELD): {"pdfs": pdfs, "txts": txts},
        field_map.get("links", "原始链接"): _build_source_links(group),
        field_map.get("category", "分类"): _build_source_categories(group),
        field_map.get("credibility", "可信度"): [credibility] if credibility else [],
    }


def build_records(latest: Dict[str, Any]) -> List[Dict[str, Any]]:
    """按采集器分组构建"每源一行"记录列表；有信息的源才有记录（无信息源不建行）。"""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for it in latest.get("items") or []:
        code = it.get("source_code") or ""
        if not code:
            continue
        grouped.setdefault(code, []).append(it)
    return [_build_source_record(code, group) for code, group in sorted(grouped.items())]


# ---------------------------------------------------------------- 查重辅助

def _parse_created_ts(value) -> Optional[float]:
    """解析表格"创建时间"字段值为 epoch 秒（兼容毫秒/秒时间戳、ISO 字符串、斜杠日期）。
    注意：表格日期字段实际返回"2026/08/20 10:58:05"斜杠格式，ISO 解析会失败导致查重失效。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value / 1000.0 if value > 1e11 else value
    s = str(value).strip()
    if not s:
        return None
    # 斜杠格式：2026/08/20 10:58:05
    if "/" in s:
        for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
            try:
                return datetime.strptime(s, fmt).timestamp()
            except ValueError:
                continue
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _is_today_local(ts: float) -> bool:
    """epoch 秒是否落在本地时区今天"""
    return datetime.fromtimestamp(ts).date() == datetime.now().date()


# ---------------------------------------------------------------- 主入口

def _print_dry_run(record: Dict[str, Any], attach_field: str) -> None:
    """dry-run：打印一条源记录的可读形态（不含 base64 附件内容）"""
    source = record.get("信息源", "?")
    summary = (record.get("总结") or "").splitlines()
    print(f"\n【{source}】")
    print("[总结]")
    for line in summary:
        print(f"  {line}")
    attach = record.get(attach_field) or {}
    pdfs, txts = attach.get("pdfs", []), attach.get("txts", [])
    print("[原始链接]")
    for line in (record.get("原始链接") or "").splitlines():
        print(f"  {line}")
    print(f"[分类] {'、'.join(record.get('分类') or []) or '(无)'}")
    print(f"[可信度] {'、'.join(record.get('可信度') or []) or '(无)'}")
    pdf_desc = ", ".join(p.name for p in pdfs) or "(无)"
    txt_desc = ", ".join(f"{n}({len(t)}字)" for n, t in txts) or "(无)"
    print(f"[附件] PDF: {pdf_desc}")
    print(f"       正文txt: {txt_desc}")
    print("       (同步时将上传云文档取链接，写入「附件链接」字段)")


def sync_to_bitable(dry_run: bool = False, logger: Optional[logging.Logger] = None) -> int:
    """
    M2 对接主入口：latest.json → WPS 多维表格"每采集器一行"upsert。
    查重键：信息源 + 创建时间落在今天（创建时间由表格字段自动生成，本模块不写入）。
    返回：0 成功（含 dry-run）；非 0 失败（仅告警，不影响本地产物）。
    """
    logger = logger or logging.getLogger("sync_bitable")
    cfg = BITABLE
    field_map = cfg.get("field_map", {})
    attach_field = field_map.get("attachments", ATTACH_FIELD)
    url_field = field_map.get("attachment_url", ATTACH_URL_FIELD)
    source_field = field_map.get("source", "信息源")
    created_field = cfg.get("created_at_field", "创建时间")

    try:
        latest = load_latest()
        records = build_records(latest)
    except BitableSyncError as e:
        logger.error(f"[同步] 主产物读取失败: {e}")
        return 1

    # dry-run：只验证数据组装，不依赖凭证与 API
    if dry_run:
        logger.info("[同步] dry-run: 仅打印待写入记录，不调用 API")
        print(f"\n[dry-run] 共 {len(records)} 个采集器有信息（无信息源不建行）:")
        for rec in records:
            _print_dry_run(rec, attach_field)
        return 0

    if not cfg.get("enabled"):
        logger.info("[同步] BITABLE.enabled=False，跳过多维表格同步")
        return 0
    if not all([cfg.get("app_id"), cfg.get("app_key"), cfg.get("file_id"), cfg.get("sheet_id")]):
        logger.error("[同步] BITABLE 配置不完整（需 app_id/app_key/file_id/sheet_id），跳过")
        return 1

    client = BitableClient(
        app_id=cfg["app_id"],
        app_key=cfg["app_key"],
        file_id=cfg["file_id"],
        sheet_id=cfg["sheet_id"],
        base_url=cfg.get("base_url", "https://openapi.wps.cn"),
        logger=logger,
    )

    try:
        # 1. 查重：各源今天是否已有行（按信息源过滤 → 客户端判创建时间是否今天）
        existing: Dict[str, str] = {}
        for rec in records:
            src = rec.get(source_field, "")
            if not src or src in existing:
                continue
            recs = client.list_records(
                criteria=[{"field": source_field, "operator": "Equals", "values": [src]}]
            )
            for r in recs:
                rid = r.get("record_id") or r.get("id")
                raw_fields = r.get("fields") or "{}"
                try:
                    r_fields = (
                        json.loads(raw_fields) if isinstance(raw_fields, str) else raw_fields
                    )
                except json.JSONDecodeError:
                    continue
                ts = _parse_created_ts(r_fields.get(created_field))
                if ts is not None and _is_today_local(ts):
                    existing[src] = rid
                    break

        # 2. 逐源 upsert（空值字段不写入；附件上传云文档：写"附件"卡片 + "附件链接"备份）
        for rec in records:
            src = rec.get(source_field, "")
            if not src:
                continue
            fields = {k: v for k, v in rec.items() if v not in ("", [], None)}
            attach_info = fields.pop(attach_field, None) or {}
            if attach_info.get("pdfs") or attach_info.get("txts"):
                items = _upload_attachments(
                    client, attach_info.get("pdfs", []), attach_info.get("txts", [])
                )
                if items:
                    # 「附件」写 cloud 卡片（source=cloud，uploadId=短链接后缀，原生渲染+预览）
                    fields[attach_field] = items
                    # 「附件链接」写纯文本备份（值已现成，零额外成本，便于复制/归档）
                    fields[url_field] = "\n".join(it["linkUrl"] for it in items)
            if src in existing:
                client.update_record(existing[src], fields)
                logger.info(f"[同步] 已更新: {src} (record_id={existing[src]})")
            else:
                rid = client.create_record(fields)
                logger.info(f"[同步] 已创建: {src} (record_id={rid})")
        logger.info(f"[同步] 完成：{len(records)} 个采集器已同步")
        return 0
    except BitableSyncError as e:
        logger.error(f"[同步] 多维表格同步失败: {e}")
        return 1
    except Exception as e:  # 网络等未预期异常：兜底，不影响主流程
        logger.error(f"[同步] 未预期异常: {e}")
        return 1


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    raise SystemExit(sync_to_bitable(dry_run=False))
