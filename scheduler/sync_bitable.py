# -*- coding: utf-8 -*-
"""
WPS 多维表格同步（sync_bitable）— 模块 F（M2 对接）

总调度器与汇总实施方案.md 模块 M2：
- 独立对接层，只消费 output/latest.json + output/latest_attachments/
- 一天一行 upsert：日期(主键) | 简报内容 | 附件 | 源状态 | 统计 | 采集时间
- 附件方案 A：当天全部 PDF 挂到一个"附件"字段（命名沿用 {source_code}_{原文件名}）
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
import base64
import hashlib
import hmac
import json
import logging
import mimetypes
import time
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from config.settings import (
    BITABLE,
    CATEGORIES,
    COLLECTORS,
    LATEST_ATTACHMENTS_DIR,
    LATEST_JSON,
)

# ---------------------------------------------------------------- 常量

# 用户级 access_token 有效期约 2 小时，留 10 分钟余量提前刷新
TOKEN_TTL_SECONDS = 2 * 3600 - 600

# base64 直写单文件上限（保守取值：官方单文件 20MB 限制通常指 base64 串长度，
# base64 膨胀约 1/3，故原始文件保守按 15MB 走直写，超出走"获取上传附件/图片 URL"流程）
MAX_BASE64_BYTES = 15 * 1024 * 1024

# 附件方案 A：全部 PDF 挂到该字段（需为"图片和附件"类型）
ATTACH_FIELD = "附件"

# 记录接口路径（以官方文档为准，联调如有出入只改这里）
API_RECORDS_GET = "/v7/coop/dbsheet/{file_id}/sheets/{sheet_id}/records"
API_RECORDS_CREATE = "/v7/coop/dbsheet/{file_id}/sheets/{sheet_id}/records/create"
API_RECORDS_UPDATE = "/v7/coop/dbsheet/{file_id}/sheets/{sheet_id}/records/update"


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
        body_str = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
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
            "Content-Type": content_type,
        }
        resp = requests.request(
            method, f"{self.base_url}{uri}", headers=headers, data=body_str, timeout=60
        )
        if resp.status_code != 200:
            raise BitableSyncError(
                f"{method} {uri} -> HTTP {resp.status_code}: {resp.text[:300]}"
            )
        return resp.json()

    # ---------------- 记录 CRUD ----------------

    def list_records(self, date: Optional[str] = None) -> List[dict]:
        """查记录。date 非空时仅返回该日期的记录（upsert 主键）。"""
        # 官方列举记录请求体：游标分页 page_size + page_token（首页不传/传空），filter 用 mode/criteria 结构
        body: Dict[str, Any] = {
            "prefer_id": False,
            "page_size": 100,
            "page_token": "",
            "fields": [],
            "filter": None,
        }
        if date:
            body["filter"] = {
                "mode": "AND",
                "criteria": [{"field": "日期", "operator": "Equals", "values": [date]}],
            }
        data = self._request("POST", API_RECORDS_GET, body)
        # 响应结构以官方为准：常见形态 data.records[]（含 record_id 与 fields）
        records = data.get("data", {}).get("records", [])
        return records or []

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

    # ---------------- 附件（方案 A） ----------------

    @staticmethod
    def _file_to_base64_value(pdf_path: Path) -> dict:
        """小文件附件：base64 Data URL 直写（官方上限单文件 20MB）"""
        mime = mimetypes.guess_type(pdf_path.name)[0] or "application/pdf"
        b64 = base64.b64encode(pdf_path.read_bytes()).decode("ascii")
        return {"fileData": f"data:{mime};base64,{b64}", "fileName": pdf_path.name}

    def _upload_large_file(self, pdf_path: Path) -> dict:
        """
        大文件附件：走"获取上传附件/图片 URL"流程（上传到素材库后拿 fileToken）。
        具体端点与参数需在 open.wps.cn API 调试台确认后填入；未配置时明确报错，
        避免静默丢失附件。
        """
        raise BitableSyncError(
            f"{pdf_path.name} 超过 base64 直写上限，需配置大文件上传流程"
            "（open.wps.cn API 调试台 → 获取上传附件/图片 URL），联调后补全"
        )

    def build_attachment_value(self, pdf_paths: List[Path]) -> List[dict]:
        """附件方案 A：当天全部 PDF 组装成一个附件字段的值"""
        values: List[dict] = []
        for pdf in pdf_paths:
            if pdf.stat().st_size > MAX_BASE64_BYTES:
                values.append(self._upload_large_file(pdf))
            else:
                values.append(self._file_to_base64_value(pdf))
        return values


# ---------------------------------------------------------------- 数据组装

def load_latest() -> Tuple[Dict[str, Any], List[Path]]:
    """
    读取主产物：latest.json + 归集附件目录。
    返回 (latest_dict, attachment_files)；主产物缺失时抛 BitableSyncError。
    """
    if not LATEST_JSON.exists():
        raise BitableSyncError(f"主产物缺失: {LATEST_JSON}，请先运行采集+汇总")
    latest = json.loads(LATEST_JSON.read_text(encoding="utf-8"))

    files: List[Path] = []
    if LATEST_ATTACHMENTS_DIR.exists():
        files = sorted(
            [p for p in LATEST_ATTACHMENTS_DIR.iterdir() if p.suffix.lower() == ".pdf"]
        )
    return latest, files


def _to_time_value(iso_value: str) -> str:
    """Time 类型字段只接受 HH:mm:ss；把 ISO 时间戳截取时刻部分（解析失败原样兜底）"""
    if not iso_value:
        return ""
    try:
        return datetime.fromisoformat(iso_value).strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return iso_value


# ---------------------------------------------------------------- 简报内容清洗（L1）

# 分类展示顺序（与 config/settings.py CATEGORIES 定义顺序一致）
CATEGORY_ORDER = list(CATEGORIES.keys())
# 未命中 5 类的条目归入该兜底分类（客观真实：不丢弃，单独列出）
FALLBACK_CATEGORY = "其他"

# 可信度优先级：官方公告 > 官方资讯 > 媒体报道（未知兜底排最后）
CREDIBILITY_ORDER = {"【官方公告】": 0, "【官方资讯】": 1, "【媒体报道】": 2}


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


def _build_cleaned_briefing(items: List[Dict[str, Any]]) -> str:
    """
    L1 简报内容清洗：按分类分组 + 组内按可信度/时间排序 + 编号 + 来源中文名。

    不改信息内容，只做组织还原：
      【资本动态】(3条)
        01 巨潮资讯网 标题
           · 摘要...
    """
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        cat = (it.get("category") or "").strip()
        if cat not in CATEGORY_ORDER:
            cat = FALLBACK_CATEGORY
        grouped.setdefault(cat, []).append(it)

    lines: List[str] = []
    for cat in CATEGORY_ORDER + [FALLBACK_CATEGORY]:
        group = grouped.get(cat, [])
        if not group:
            continue
        # 组内排序：可信度优先级升序，同级别按发布时间倒序（无有效时间放末尾）
        group = sorted(
            group,
            key=lambda it: (
                CREDIBILITY_ORDER.get((it.get("credibility_tag") or "").strip(), 99),
                -(_parse_publish_ts(it.get("publish_time")) or 0),
            ),
        )
        lines.append(f"【{cat}】({len(group)}条)")
        for idx, it in enumerate(group, 1):
            source = _source_display_name(it.get("source_code"))
            title = (it.get("title") or "").strip()
            lines.append(f"  {idx:02d} {source} {title}")
            summary = (it.get("summary") or "").strip()
            if summary:
                lines.append(f"    · {summary}")
    return "\n".join(lines) or "(无内容)"


def build_record(latest: Dict[str, Any], attachment_files: List[Path]) -> Dict[str, Any]:
    """
    构建"一天一行"记录（列名以建表时的字段名为准，见 BITABLE.field_map）。
    返回 {列名: 值}。
    """
    field_map = BITABLE.get("field_map", {})

    # 简报内容（L1 清洗）：按分类分组 + 组内可信度/时间排序 + 编号 + 来源中文名
    briefing = _build_cleaned_briefing(latest.get("items") or [])

    # 源状态：人类可读 + 机器可读并存（各源 status/count/duration_s/error）
    sources = latest.get("sources", {})
    sources_status_lines = []
    for code, s in sorted(sources.items()):
        status = s.get("status", "?")
        count = s.get("count", 0)
        err = s.get("error") or ""
        sources_status_lines.append(f"{code}: {status}({count})" + (f" {err}" if err else ""))
    sources_status = "\n".join(sources_status_lines) or "(无源状态)"

    stats = latest.get("stats", {})
    stats_text = " | ".join(f"{k}={v}" for k, v in stats.items())

    return {
        field_map.get("date", "日期"): latest.get("date", ""),
        field_map.get("briefing", "简报内容"): briefing,
        field_map.get("attachments", ATTACH_FIELD): attachment_files,
        field_map.get("sources_status", "源状态"): sources_status,
        field_map.get("stats", "统计"): stats_text,
        field_map.get("run_id", "运行id"): latest.get("run_id", ""),
        field_map.get("generated_at", "采集时间"): _to_time_value(
            str(latest.get("generated_at", ""))
        ),
    }


# ---------------------------------------------------------------- 主入口

def sync_to_bitable(dry_run: bool = False, logger: Optional[logging.Logger] = None) -> int:
    """
    M2 对接主入口：latest.json + latest_attachments/ → WPS 多维表格"一天一行"upsert。
    返回：0 成功（含 dry-run）；非 0 失败（仅告警，不影响本地产物）。
    """
    logger = logger or logging.getLogger("sync_bitable")
    cfg = BITABLE

    try:
        latest, files = load_latest()
        record = build_record(latest, files)
    except BitableSyncError as e:
        logger.error(f"[同步] 主产物读取失败: {e}")
        return 1

    # dry-run：只验证数据组装，不依赖凭证与 API
    if dry_run:
        logger.info("[同步] dry-run: 仅打印待写入记录，不调用 API")
        print(f"\n[dry-run] 日期: {record.get('日期')} | 附件 {len(files)} 个: "
              f"{', '.join(f.name for f in files) or '(无)'}")
        print(f"[dry-run] 源状态:\n{record.get('源状态')}")
        print(f"[dry-run] 统计: {record.get('统计')}")
        print(f"[dry-run] 简报内容:\n{record.get('简报内容')}")
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
        # 附件值（方案 A：全部 PDF 挂一个字段）
        attachment_value = client.build_attachment_value(files)
        fields = dict(record)
        fields[cfg.get("field_map", {}).get("attachments", ATTACH_FIELD)] = attachment_value

        # upsert：按日期查已有记录
        date_value = record.get("日期", "")
        existing = client.list_records(date=date_value)
        if existing:
            rid = existing[0].get("record_id") or existing[0].get("id")
            client.update_record(rid, fields)
            logger.info(f"[同步] 已更新记录: {date_value} (record_id={rid}, 附件 {len(files)} 个)")
        else:
            rid = client.create_record(fields)
            logger.info(f"[同步] 已创建记录: {date_value} (record_id={rid}, 附件 {len(files)} 个)")
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
