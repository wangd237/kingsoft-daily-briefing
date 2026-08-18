# WPS 多维表格同步 —— 跑通记录与部署指南

> 本文档记录"金山系资讯采集系统 → WPS 多维表格"这条链路**是如何跑通的**（含全部踩坑与最终结论），
> 并侧重给出**交给其他人部署时可能遇到的问题**与解决方案。
>
> 状态：✅ 已跑通（2026-08-18，授权/读取/创建/更新全链路验证通过）

---

## 1. 数据流全景

```
采集器 (collectors/*)  ──►  output/data/{source}/...（JSON + 附件 PDF）
                              │
                              ▼
总调度汇总 (scheduler)  ──►  output/latest.json
                             output/latest_attachments/*.pdf
                              │
                              ▼
同步层 (scheduler/sync_bitable.py)  ──►  WPS 多维表格「数据表」
                                              │
                                              ▼
                                       一天一行 upsert：
                                       日期 | 简报内容 | 附件 | 源状态 | 统计 | 运行id | 采集时间
```

- 同步层只消费两个产物：`output/latest.json` + `output/latest_attachments/`，与采集/汇总解耦。
- 同步失败**不影响本地产物**（全程 try/except 兜底，只记日志）。
- 入口命令：`python -m scheduler.main --sync-only`（只同步，不采集不汇总）。

---

## 2. 跑通的方法与关键结论（踩坑全记录）

### 2.1 授权模式：必须用「用户级 token」，应用 token 走不通

| 结论 | 说明 |
|---|---|
| dbsheet 是**用户级**数据接口 | 用应用 token（`client_credentials`）调任何 dbsheet 接口都返回 `403 unable to read user permission`，这条路**根本不通**，不是签名/参数问题 |
| 必须走 OAuth **授权码模式** | `scripts/wps_authorize.py` 完成浏览器授权 → 拿到用户 `access_token` + `refresh_token` |

### 2.2 scope 权限：app 与 user 是两套独立 scope

- 在开发者后台「权限管理」里，同一 scope 名会分 **`app` 类型**和 **`user` 类型**两行，分别对应应用 token 和用户授权。
- 我们用的是 `user` 类型：
  - `kso.dbsheet.read`（user）—— 已开通
  - `kso.dbsheet.readwrite`（user）—— **必须手动点「申请开通」**（填用途说明）
- 未申请时授权链接直接报 `40000005 invalid_scope`（官方文档：scope 需先在后台申请开通，否则授权服务器拒绝）。
- 授权脚本 scope 固定为 `kso.dbsheet.readwrite,kso.dbsheet.read`（配置在 `config/settings.py` 的 `BITABLE.scope`）。

### 2.3 回调地址

- 默认回调 `http://127.0.0.1:8765/callback`，必须先在后台「安全配置 → 用户授权回调配置」里添加，否则授权失败。
- 授权脚本本地起一个 HTTP 服务自动接收 code；若部署在远程机器无本地回调，可用 `python scripts/wps_authorize.py --code {code}` 手动传。

### 2.4 KSO-1 签名（所有数据接口必带）

除 `Authorization: Bearer {access_token}` 外，WPS 还要求三个请求头：

```text
X-Kso-Date:            RFC1123 GMT 时间，如 "Tue, 18 Aug 2026 07:00:00 GMT"
X-Kso-Authorization:   KSO-1 {app_id}:{signature}
Content-Type:          application/json
```

签名算法（`sync_bitable.BitableClient._sign()`）：

```text
signature = HMAC-SHA256(APP_KEY,
    "KSO-1" + METHOD + URI + CONTENT_TYPE + KSO_DATE + sha256(body).hexdigest()
).hexdigest()
```

> 注意：签名串拼接细节以 open.wps.cn 官方文档为准；代码已实测可用，**若更换账号/环境后报签名错误，优先核对这五行拼接顺序**。

### 2.5 token 生命周期与自动刷新

- `access_token` 约 2 小时有效；`refresh_token` 约 365 天有效。
- 持久化在 `output/wps_token.json`（结构：`access_token / refresh_token / expires_at / refresh_expires_at`，`expires_at` 预留了 10 分钟余量提前刷新）。
- `sync_bitable` 每次请求自动：内存缓存 → 文件读取 → 过期用 refresh_token 刷新并回写文件。
- **refresh_token 过期后**需重新跑 `python scripts/wps_authorize.py` 再次浏览器授权。

### 2.6 upsert（一天一行）

1. `list_records` 带过滤：`日期 = Equals "YYYY-MM-DD"`（实测能正确匹配 WPS 内部存的 `YYYY/MM/DD`）。
2. 查得到 → `update_record`（按记录 `id`）；查不到 → `create_record`。
3. 同一天多次运行 = 覆盖当天那一行。

### 2.7 两个真实 bug（已修复，部署时注意不要回退）

| Bug | 现象 | 修复 |
|---|---|---|
| **采集时间格式** | 传 ISO 时间戳 `2026-08-18T15:33:52` 到 `Time` 类型列 → 400 `E_DBSHEET_INVALID_INPUT` | 新增 `_to_time_value()`：ISO 截取为 `HH:mm:ss` |
| **create 响应解析** | 创建其实成功，但代码取 `data.record_id` 不存在 → 误报失败 | 改为从 `data.records[0].id` 取真实 record_id |

### 2.8 「金蝶相关」报错之谜（重要经验）

- 创建记录时服务端报 `fieldKey: "金蝶相关"`，但表里**根本没有这个字段**，全项目也搜不到该词。
- 排查过程：确认 sheet_id 没错（`id=1` 是"数据表"，`id=2` 才是"仪表盘"）→ 字段管理里确实没有该列 → 逐字段二分 create 定位 → 发现**真凶是"采集时间"字段格式**。
- 结论：`recordId: "G"` 对应表里**被删除过的字段 ID 残留**，WPS 服务端报错时会把异常字段名写成一个**迷惑性/残留字段名**。遇到 `invalidCellsInfo` 报错，别被 `fieldKey` 带偏，要**逐字段二分定位真正非法的字段**。

### 2.9 附件写入（方案 A）

- 当天全部 PDF 挂到「附件」列（`Attachment` 类型），值为数组：`[{"fileData": "data:application/pdf;base64,...", "fileName": "xxx.pdf"}]`。
- 单文件 ≤15MB 走 base64 直写（官方上限 20MB，保守取值）；**>15MB 会明确报错**（大文件上传流程尚未实现，见 §4.8）。

---

## 3. 部署环境要求

| 项 | 要求 | 说明 |
|---|---|---|
| Python | **3.10+** | 代码大量使用 `str \| None` 类型语法，3.9 及以下直接语法报错 |
| 操作系统 | Windows / Linux 均可 | 本仓库在 Windows 上开发；部署文档以 Windows 任务计划为例 |
| 依赖 | `pip install -r requirements.txt` | requests / beautifulsoup4 / python-dotenv / opencc / playwright |
| 浏览器（可选） | `playwright install chromium` | 仅 stcn / huxiu / eastmoney 等无头浏览器采集源需要；若用 `--skip-collect` 只同步可跳过 |
| 网络 | 能访问 `open.wps.cn` | 内网代理需配置环境变量 `HTTP_PROXY / HTTPS_PROXY`（requests 默认读取） |

---

## 4. 部署步骤与常见问题

### 4.1 第一步：拉代码、装依赖

```bash
git clone <仓库地址>
cd Issue1
python -m venv .venv                 # 建议虚拟环境
.venv\Scripts\activate               # Windows（Linux: source .venv/bin/activate）
pip install -r requirements.txt
playwright install chromium          # 若只跑多维表格同步可跳过
```

### 4.2 第二步：配置 .env

```bash
copy .env.example .env               # Windows（Linux: cp .env.example .env）
```

必填项（`config/settings.py` 会读取这些环境变量）：

```env
# WPS 多维表格（M2 对接）
WPS_APP_ID=        # open.wps.cn 企业应用 client_id
WPS_APP_KEY=       # client_secret
WPS_FILE_ID=       # 目标多维表格 file_id（表格 URL 中的长串 ID）
WPS_SHEET_ID=1     # 工作表 sheet_id：1=数据表，2=仪表盘，别填错！
WPS_SYNC_ENABLED=1 # 1=启用同步（默认 0 关闭）
WPS_REDIRECT_URI=  # 可选，默认 http://127.0.0.1:8765/callback

# AI 摘要（若用完整采集流程）
AI_API_BASE=
AI_API_KEY=
AI_MODEL=
```

> `.env` 已加入 `.gitignore`，**不要提交**；`WPS_APP_KEY` 和 `output/wps_token.json` 都是敏感信息。

### 4.3 第三步：WPS 开放平台准备（一次性，且只有表格管理员能做）

1. 在 [open.wps.cn](https://open.wps.cn) 创建/使用**企业应用**，拿到 `client_id` / `client_secret`。
2. **权限管理**：搜索 `kso.dbsheet`，确保 **`user` 类型**的 `kso.dbsheet.read` 和 `kso.dbsheet.readwrite` 都「已开通」。
   - ⚠️ 只申请 `app` 类型没用，dbsheet 必须 user 类型（见 §2.2）。
   - ⚠️ 没申请时，授权链接直接报 `invalid_scope`，**不是代码问题**。
3. **安全配置 → 用户授权回调配置**：添加 `http://127.0.0.1:8765/callback`。
4. 确认目标表结构（字段名必须一字不差）：

   | 列名 | 类型 |
   |---|---|
   | 日期 | Date |
   | 简报内容 | MultiLineText |
   | 附件 | Attachment（图片和附件） |
   | 源状态 | MultiLineText |
   | 统计 | MultiLineText |
   | 运行id | MultiLineText |
   | 采集时间 | **Time（钟表时刻，不是日期时间）** |

   列名在 `config/settings.py` 的 `BITABLE.field_map` 维护，改表或改配置要两边同步。

### 4.4 第四步：首次授权（跑通的关键一步）

```bash
python scripts/wps_authorize.py
```

- 脚本打印授权链接 → 自动打开浏览器 → **必须用表格所有者账号登录并同意授权**（token 权限跟着账号走，别人授权的 token 可能没权限）→ 回调后自动保存 token 并验证读表。
- 预期输出最后一行：`[4/4] 验证通过：已能读取表格记录 N 条`。
- 部署在**无浏览器/远程服务器**时：把打印的授权链接复制到本地浏览器完成授权，若回调地址不是本机，改用 `python scripts/wps_authorize.py --code {回调URL上的code}`。

### 4.5 第五步：验证同步

```bash
# 只打印待写入内容，不调 API
python -m scheduler.main --sync-only --sync-dry-run

# 真实写入
python -m scheduler.main --sync-only
```

成功标志：`[同步] 已创建记录: {日期} (record_id=..., 附件 N 个)`（或"已更新记录"）。

> 注意：`--sync-only` 默认不输出 INFO 日志（静默成功），退出码 0 = 成功。要看详情可带 `logging` 或直接看多维表格里是否新增了当天那一行。

### 4.6 第六步：设置定时任务

Windows 任务计划程序（每天 09:30 同步）：

```
程序:    C:\...\.venv\Scripts\python.exe
参数:    -m scheduler.main --sync-only
起始于:  C:\...\Issue1
```

Linux cron：

```cron
30 9 * * * cd /path/to/Issue1 && .venv/bin/python -m scheduler.main --sync-only >> output/logs/sync.log 2>&1
```

若需要"先采集汇总再同步"，用 `python -m scheduler.main --sync-bitable`（完整流程，需先配好 AI 摘要和网络访问目标站点）。

---

## 5. 部署高频问题排查表

| # | 现象 | 原因 | 处理 |
|---|---|---|---|
| 1 | 授权链接报 `40000005 invalid_scope` | user 类型 scope 未申请开通 | 后台申请 `kso.dbsheet.readwrite`（user），见 §4.3 |
| 2 | 任何 dbsheet 接口报 403 `unable to read user permission` | 用了应用 token / token 是别人授权的 | 重新跑 `wps_authorize.py` 用表格所有者账号授权 |
| 3 | 请求报 401/签名错误 | APP_ID/APP_KEY 不对，或 `_sign()` 与官方不一致 | 核对 .env 凭证；对照 open.wps.cn 官方文档检查签名串 |
| 4 | create/update 报 400 `E_DBSHEET_INVALID_INPUT` | 字段值类型不对（最常见：采集时间传了 ISO 而不是 `HH:mm:ss`） | 逐字段二分定位；`Time` 列只接受 `HH:mm:ss` |
| 5 | 报错字段名是表里不存在的（如"金蝶相关"） | WPS 服务端残留字段 ID 的迷惑性报错，字段名不可信 | 用 `scripts/debug_bitable_create.py` 这类脚本逐字段定位真凶 |
| 6 | 创建成功但脚本报"缺少 record_id" | 响应解析写错字段（老代码） | 从 `data.records[0].id` 取（当前代码已修复） |
| 7 | 同步成功但表格里没有当天行 | `WPS_SHEET_ID` 填错（如填成仪表盘 2） | 用 `python scripts/inspect_bitable_sheets.py` 列 sheet，数据表 id=1 |
| 8 | 同步后提示"用户 token 已失效，请重新授权" | refresh_token 过期（约 1 年） | 重跑 `wps_authorize.py` |
| 9 | 附件写入报"超过 base64 直写上限" | PDF >15MB | 大文件上传流程未实现，需联调补全（§4.8），或控制单文件大小 |
| 10 | 采集源全部失败/超时 | 目标站点网络不可达、反爬、代理 | 检查网络；`--skip-collect` 只同步现有产物不受影响 |
| 11 | 同步日志不输出 | `--sync-only` 默认静默 | 以退出码为准（0=成功），或加日志级别调试 |

---

## 6. 已知局限与后续待办（部署前务必周知）

1. **大文件附件（>15MB）未实现**：`_upload_large_file()` 目前是明确报错占位。若目标环境的公告 PDF 普遍 >15MB，需在 open.wps.cn 调试台确认"获取上传附件/图片 URL"流程后补全，否则当天附件会整单失败（报错而非静默丢附件，不会产生脏数据）。
2. **素材库容量**：每天 N 个 PDF × 365 天会持续占用企业素材库额度，目前无自动清理/归档策略，长期运行需评估。
3. **同步失败无告警**：目前仅本地日志（`[同步] ... 失败`），未接入企业微信/飞书机器人。
4. **token 敏感信息**：`output/wps_token.json` 内含用户级 token，部署机需做好文件权限/备份；refresh_token 过期需人工重授权（约 1 年一次）。
5. **测试数据残留**：当前表里可能有日期为 `2099-01-01` 的调试记录（record_id I~Q 附近），新环境联调时留意清理，别当成生产数据。
6. **AI 摘要**：采集器会调 AI 做摘要，`.env` 未配 API 或网络不通时**自动降级跳过摘要**（采集仍成功），属预期行为。

---

## 7. 命令速查

```bash
# 首次授权（一次性）
python scripts/wps_authorize.py

# 只同步不采集（日常定时用这个）
python -m scheduler.main --sync-only

# 只同步、只打印不调用 API（安全预览）
python -m scheduler.main --sync-only --sync-dry-run

# 完整流程：采集 + 汇总 + 同步
python -m scheduler.main --sync-bitable

# 诊断：列出 file_id 下所有 sheet（确认 WPS_SHEET_ID）
python scripts/inspect_bitable_sheets.py

# 诊断：逐字段二分定位 create 失败点（遇到 400 时用）
python scripts/debug_bitable_create.py
```

---

## 8. 本次跑通的关键文件

| 文件 | 作用 |
|---|---|
| `scripts/wps_authorize.py` | OAuth 授权码模式换取用户 token，持久化到 `output/wps_token.json` |
| `scheduler/sync_bitable.py` | 同步客户端：KSO-1 签名、token 自动刷新、记录 CRUD、附件 base64 直写、upsert |
| `scheduler/main.py` | CLI 入口，`--sync-only` / `--sync-bitable` 等 |
| `config/settings.py` | `BITABLE` 配置段（凭证、scope、field_map、开关），全部可被 `.env` 覆盖 |
| `.env.example` | 环境变量模板 |
