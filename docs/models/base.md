# 数据模型 (base.py)

## 概述

`NewsItem` 是资讯采集系统的**核心数据模型**，定义了所有信息源统一的数据结构。`BaseCrawler` 是所有采集器的**抽象基类**，提供通用功能。

---

## NewsItem 数据结构

```python
@dataclass
class NewsItem:
    title: str                              # 标题（必选）
    date: str                               # 日期（必选）
    url: str                                # 原文链接（必选）
    source: str = ""                        # 信息源名称
    source_code: str = ""                   # 信息源代码
    credibility_tag: str = "【官方公告】"    # 可信度标签
    category: str = ""                      # 分类
    publish_time: datetime = None           # 发布时间（解析后的）
    summary: str = ""                       # AI 摘要
    summary_generated_at: datetime = None   # 摘要生成时间
    content: str = ""                       # 正文内容（运行时临时存储）
    content_ref: str = ""                   # 正文文件引用路径
    raw_data: dict = None                   # 原始数据（扩展字段）
```

---

## 字段说明

### 核心字段（必选）

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `title` | str | 资讯标题 | "金山办公：关于完成市场主体变更登记..." |
| `date` | str | 发布日期 | "2026-07-29" |
| `url` | str | 原文链接 | "http://www.cninfo.com.cn/..." |

### 元数据字段

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `source` | str | 信息源名称 | "巨潮资讯网" |
| `source_code` | str | 短标识 | "cninfo" |
| `credibility_tag` | str | 可信度 | "【官方公告】" |
| `category` | str | 自动分类 | "资本动态" |

**可信度标签枚举：**
- `【官方公告】` - 证监会、交易所公告
- `【官方资讯】` - 公司官微、公众号
- `【媒体报道】` - 财经媒体

**分类枚举：**
- `资本动态` - 财报、回购、股权激励
- `产品动态` - 产品发布、版本更新
- `市场合作` - 签约、战略合作
- `活动IP` - 发布会、展会
- `人事其他` - 高管变动、声明

### 内容字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `content` | str | **运行时**存储正文，不持久化到 JSON |
| `content_ref` | str | 正文文件相对路径，用于持久化 |
| `summary` | str | AI 生成的摘要（150字以内） |
| `summary_generated_at` | datetime | 摘要生成时间戳 |

**分级存储设计：**
```
运行时: content 字段持有完整内容
保存时: content 写入 .txt 文件，JSON 只存 content_ref
读取时: 通过 get_content() 按需加载
```

### 扩展字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `raw_data` | dict | 原始数据，各采集器可自定义 |

**示例：**
```python
# cninfo 采集器存储 PDF 路径
item.raw_data = {
    'pdf_path': 'output/data/pdfs/688111_a1b2c3d4.pdf',
    'announcement_id': '1225444806'
}
```

---

## 核心方法

### `to_dict(include_content=False)`

**功能：** 转换为字典，用于 JSON 序列化

**参数：**
- `include_content`: 是否包含 content 字段（默认否）

**示例：**
```python
# 保存时：不包含 content（精简 JSON）
json_data = item.to_dict(include_content=False)
# 结果: {"title": "...", "content_ref": "...", "summary": "..."}

# 调试时：包含完整 content
debug_data = item.to_dict(include_content=True)
# 结果: {"title": "...", "content": "完整正文...", "summary": "..."}
```

---

### `get_content(base_dir="output/data")`

**功能：** 根据 `content_ref` 加载正文内容

**使用场景：**
```python
# 从 JSON 读取数据后，需要查看正文
item = NewsItem(**json_data)
content = item.get_content("output/data")

# 逻辑：
# 1. 如果 item.content 已有值，直接返回
# 2. 否则根据 item.content_ref 读取文件
```

---

## BaseCrawler 基类

### 子类必须实现的属性

```python
class MyCrawler(BaseCrawler):
    source_name = "我的采集器"      # 显示名称
    source_code = "my_source"       # 短标识，用于文件名
    credibility_base = "【媒体报道】"  # 默认可信度
```

### 子类必须实现的方法

```python
@abstractmethod
def fetch(self) -> List[NewsItem]:
    """采集数据的主方法"""
    pass
```

### 提供的通用方法

| 方法 | 功能 |
|------|------|
| `run()` | 完整流程：fetch() + save() |
| `save()` | 保存数据到 JSON（分级存储） |
| `_parse_time(time_str)` | 解析多种时间格式 |
| `_get_data_path()` | 生成数据文件路径 |
| `_get_log_path()` | 生成日志文件路径 |

---

## 分级存储机制

### 为什么需要分级存储？

**问题：** JSON 文件过大
- 每条公告正文可能 5000-10000 字
- 30 条公告 = 15-30 万字符
- JSON 文件达 **500KB-1MB**

**影响：**
- 加载慢
- 传输慢
- 整合时内存占用高

### 解决方案

```
输出结构：
output/data/
├── cninfo/2026/07/31/
│   ├── cninfo_20260731_155933.json     # 精简 JSON (~10KB)
│   └── contents/
│       ├── ann_000.txt                  # 正文文件
│       ├── ann_001.txt
│       └── ann_002.txt
└── pdfs/                                # PDF 文件（可选）
    └── 688111_a1b2c3d4.pdf
```

**JSON 内容：**
```json
{
  "title": "关于完成市场主体变更登记...",
  "date": "2026-07-29",
  "summary": "公司已完成变更登记，注册资本...",
  "content_ref": "cninfo/2026/07/31/contents/ann_000.txt",
  "raw_data": {
    "pdf_path": "output/data/pdfs/688111_a1b2c3d4.pdf"
  }
}
```

**体积对比：**
| 方式 | 30条公告大小 |
|------|-------------|
| 传统（全在 JSON） | ~600KB |
| 分级存储 | ~15KB JSON + 内容文件 |

---

## 使用示例

### 创建 NewsItem

```python
from collectors.base import NewsItem
from datetime import datetime

item = NewsItem(
    title="金山办公：关于完成市场主体变更登记...",
    date="2026-07-29",
    url="http://www.cninfo.com.cn/...",
    source="巨潮资讯网",
    source_code="cninfo",
    category="资本动态",
    content="北京金山办公软件股份有限公司...",  # 正文
    summary="公司已完成变更登记，注册资本变更为2.2亿元...",
    summary_generated_at=datetime.now()
)
```

### 保存数据

```python
from collectors.base import BaseCrawler

class MyCrawler(BaseCrawler):
    source_name = "我的采集器"
    source_code = "my"

    def fetch(self):
        items = []  # 采集逻辑
        return items

# 使用
crawler = MyCrawler()
items = crawler.run()  # 自动 fetch + save
```

### 读取数据

```python
import json

# 读取 JSON
with open('output/data/cninfo/2026/07/31/cninfo_20260731_155933.json') as f:
    data = json.load(f)

# 遍历
for item_data in data['items']:
    item = NewsItem(**item_data)

    # 获取正文
    content = item.get_content('output/data')
    print(f"标题: {item.title}")
    print(f"摘要: {item.summary}")
    print(f"正文长度: {len(content)}")
```

---

## 扩展指南

### 添加新字段

```python
@dataclass
class NewsItem:
    # ... 现有字段

    # 新增字段
    sentiment: str = ""           # 情感分析
    keywords: List[str] = None    # 关键词
    related_stocks: List[str] = None  # 相关股票
```

### 自定义存储逻辑

```python
class MyCrawler(BaseCrawler):
    def save(self):
        # 自定义保存逻辑
        # 例如：上传到数据库、发送到消息队列等
        pass
```

---

## 最佳实践

1. **content vs content_ref**
   - 运行时始终使用 `content` 字段
   - 保存后 `content` 会被清空，通过 `get_content()` 读取

2. **raw_data 使用**
   - 存储来源特有的数据（如 PDF 路径、原始 ID）
   - 不要存储大文本（用 content_ref）

3. **时间字段**
   - `date`: 字符串，用于显示
   - `publish_time`: datetime，用于排序和过滤
   - `summary_generated_at`: datetime，用于追溯

4. **空值处理**
   - 标题、URL 不能为空
   - 摘要、正文允许为空（生成失败时）
