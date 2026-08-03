# PDF 处理模块 (pdf_processor.py)

## 概述

`PDFProcessor` 负责巨潮资讯网公告 PDF 的**下载**和**文本提取**。采用**双保险策略**确保 PDF 解析成功率。

---

## 完整流程

```
详情页 URL
    ↓
提取 PDF URL ─────────────────────────┐
（从 URL 参数构建）                      │
http://static.cninfo.com.cn/...        │
    ↓                                  │
下载 PDF ──────────────────────────────┤
requests 下载到                         │
output/data/pdfs/                      │
    ↓                                  │
PDF 文本提取 ──────────────────────────┘
├─ 首选: PyMuPDF (fitz)
│   fitz.open(pdf) → page.get_text()
│
└─ 备选: pdfplumber
    pdfplumber.open(pdf) → page.extract_text()

    ↓
纯文本内容 (最多10页，防止超长)
```

---

## 核心方法

### 1. `extract_pdf_url(detail_url, html_content=None)`

**功能：** 从详情页 URL 提取 PDF 下载链接

**实现逻辑：**
```python
# 方法1：从 URL 参数构建（推荐）
detail_url = "http://www.cninfo.com.cn/new/disclosure/detail?...&announcementId=1225444806&announcementTime=2026-07-29"

parsed = urlparse(detail_url)
params = parse_qs(parsed.query)
# announcement_id = "1225444806"
# announcement_time = "2026-07-29"

# 构造 PDF 直链
pdf_url = f"http://static.cninfo.com.cn/finalpage/2026-07-29/1225444806.PDF"
```

**规律：** 巨潮资讯网的 PDF 都托管在 `static.cninfo.com.cn`，路径格式固定：
```
http://static.cninfo.com.cn/finalpage/{日期}/{公告ID}.PDF
```

**方法2（备用）：** 从 HTML 内容中正则匹配 PDF 链接

---

### 2. `download_pdf(pdf_url, stock_code="688111")`

**功能：** 下载 PDF 文件到本地

**关键点：**
- **去重机制：** 用 URL 的 MD5 前12位作为文件名，避免重复下载
  ```python
  file_id = hashlib.md5(pdf_url.encode()).hexdigest()[:12]
  pdf_path = f"output/data/pdfs/688111_{file_id}.pdf"
  ```
- **断点续传：** 文件已存在直接返回，不重复下载
- **超时设置：** 30秒超时，防止卡住

**返回：** `(本地文件路径, 文件ID)` 或 `None`

---

### 3. `extract_text(pdf_path, max_pages=10)`

**功能：** 从 PDF 提取纯文本内容

**双保险策略：**

| 优先级 | 库 | 特点 | 适用场景 |
|--------|-----|------|---------|
| 首选 | PyMuPDF (fitz) | C实现，速度快5-10倍 | 大多数公告 |
| 备选 | pdfplumber | 对复杂表格更好 | PyMuPDF失败时 |

**为什么限制10页？**
- 绝大多数公告在 5-10 页以内
- 年报/半年报可能几百页，但通常只关心摘要
- 减少 AI Token 消耗

---

### 4. `process(detail_url, stock_code, html_content)`

**功能：** 完整处理流程（一键式）

```python
pdf_path, text = processor.process(detail_url, "688111")
# 返回: (PDF本地路径, 提取的文本内容)
```

---

## 存储结构

```
output/data/
├── pdfs/
│   ├── 688111_a1b2c3d4e5f6.pdf    # PDF文件
│   ├── 688111_x9y8z7w6v5u.pdf
│   └── ...
```

---

## 常见问题与优化

### 问题1：PDF 下载失败

**当前处理：** 直接返回 `None`

**优化建议：**
```python
# 添加重试机制
for attempt in range(3):
    try:
        response = requests.get(pdf_url, timeout=30)
        break
    except requests.Timeout:
        if attempt < 2:
            time.sleep(2 ** attempt)  # 指数退避
        continue
```

---

### 问题2：扫描版 PDF（图片形式）

**现象：** 提取到的文本为空或只有页眉页脚

**检测方法：**
```python
def is_scanned_pdf(doc):
    for page in doc:
        text = page.get_text()
        if len(text.strip()) > 50:
            return False  # 有文字，不是扫描版
    return True  # 可能是扫描版，需要 OCR
```

**解决方案：** 集成 OCR（如 PaddleOCR）

---

### 问题3：超长文档信息遗漏

**当前：** 只取前10页

**优化方案（分块摘要）：**
```python
# 1. 提取全部文本
full_text = extract_all_pages(pdf_path)

# 2. 分段
chunks = split_into_chunks(full_text, chunk_size=4000)

# 3. 每段生成摘要
chunk_summaries = [summarize(chunk) for chunk in chunks]

# 4. 合并摘要
final_summary = summarize("\n".join(chunk_summaries))
```

---

## 依赖安装

```bash
# 必选（二选一即可，推荐 PyMuPDF）
pip install pymupdf

# 或
pip install pdfplumber

# 推荐同时安装，作为备选
pip install pymupdf pdfplumber
```

---

## 使用示例

```python
from models.pdf_processor import PDFProcessor

# 初始化
processor = PDFProcessor(download_dir="output/data/pdfs")

# 处理单个公告
detail_url = "http://www.cninfo.com.cn/new/disclosure/detail?..."
result = processor.process(detail_url, stock_code="688111")

if result:
    pdf_path, text = result
    print(f"PDF路径: {pdf_path}")
    print(f"文本长度: {len(text)} 字符")
    print(f"预览: {text[:200]}...")
else:
    print("处理失败")

# 清理过期文件（7天前）
processor.cleanup(max_age_days=7)
```

---

## 未来优化方向

| 优化点 | 方案 | 优先级 |
|--------|------|--------|
| 失败重试 | 指数退避重试机制 | 高 |
| 扫描版 OCR | 集成 PaddleOCR / Tesseract | 中 |
| 分块摘要 | 超长文档分段处理 | 中 |
| 并发下载 | 多线程同时下载多个 PDF | 低 |
| 摘要缓存 | 相同 PDF 不再重复调用 AI | 高 |
