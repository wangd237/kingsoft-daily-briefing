# Models 模块文档

本目录包含金山系资讯采集系统的核心模型模块说明。

---

## 模块列表

| 文件 | 模块 | 功能 | 关键类 |
|------|------|------|--------|
| [base.md](./base.md) | `base.py` | 数据模型和采集器基类 | `NewsItem`, `BaseCrawler` |
| [pdf_processor.md](./pdf_processor.md) | `pdf_processor.py` | PDF 下载和文本提取 | `PDFProcessor` |
| [ai_summarizer.md](./ai_summarizer.md) | `ai_summarizer.py` | AI 摘要生成 | `AISummarizer` |

---

## 数据流转

```
┌─────────────────────────────────────────────────────────────────┐
│                        采集流程                                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. 列表页抓取                                                    │
│     └── 标题、日期、详情URL                                        │
│                          ↓                                       │
│  2. PDF 处理 (pdf_processor)                                     │
│     ├── 提取 PDF URL                                              │
│     ├── 下载 PDF                                                  │
│     └── 提取文本 (PyMuPDF / pdfplumber)                           │
│                          ↓                                       │
│  3. AI 摘要 (ai_summarizer)                                      │
│     └── 调用大模型生成摘要                                         │
│                          ↓                                       │
│  4. 数据存储 (base.py)                                            │
│     ├── NewsItem 对象                                             │
│     ├── 分级存储：JSON + 内容文件                                  │
│     └── 保存到 output/data/                                       │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 快速参考

### 添加新的采集器

```python
from collectors.base import BaseCrawler, NewsItem
from typing import List

class MyCrawler(BaseCrawler):
    source_name = "我的采集器"
    source_code = "my_source"
    credibility_base = "【官方资讯】"

    def fetch(self) -> List[NewsItem]:
        items = []
        # 你的采集逻辑
        # 1. 抓取列表
        # 2. 遍历详情页
        # 3. 创建 NewsItem
        return items

# 运行
crawler = MyCrawler()
items = crawler.run()
```

### 使用 PDF 处理器

```python
from models.pdf_processor import PDFProcessor

processor = PDFProcessor()
pdf_path, text = processor.process(detail_url, stock_code="688111")
```

### 使用 AI 摘要

```python
from models.ai_summarizer import get_summarizer

summarizer = get_summarizer()
summary, generated_at = summarizer.summarize(title, content)
```

---

## 文件结构

```
models/
├── __init__.py
├── base.py              # 数据模型和基类
├── pdf_processor.py     # PDF 处理
├── ai_summarizer.py     # AI 摘要
└── news.py              # （如有其他模型）

docs/models/             # 本文档目录
├── README.md            # 本文件
├── base.md              # base.py 说明
├── pdf_processor.md     # pdf_processor.py 说明
└── ai_summarizer.md     # ai_summarizer.py 说明
```

---

## 依赖关系

```
base.py
    ↑ 被继承
    └─ cninfo/crawler.py
    └─ hkex/crawler.py
    └─ ...其他采集器

pdf_processor.py
    ↑ 被导入
    └─ cninfo/crawler.py

ai_summarizer.py
    ↑ 被导入
    └─ cninfo/crawler.py
    └─ ...需要摘要的地方
```

---

## 更新日志

| 日期 | 更新内容 |
|------|----------|
| 2026-07-31 | 创建模型文档，包含 base、pdf_processor、ai_summarizer |
