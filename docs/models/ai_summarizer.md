# AI 摘要模块 (ai_summarizer.py)

## 概述

`AISummarizer` 负责调用大模型 API 生成公告摘要。支持**任何 OpenAI 兼容接口**（Kimi、DeepSeek、自研模型等）。

---

## 完整流程

```
公告标题 + 正文内容 (截断到6000字符)
    ↓
构造 Prompt
    ↓
openai.ChatCompletion.create(
    model="kimi-latest",
    messages=[
        {"role": "system", "content": "你是专业的财经资讯分析师..."},
        {"role": "user", "content": prompt}
    ],
    temperature=0.3,
    max_tokens=300
)
    ↓
解析响应 → 清理格式 → 返回 (摘要, 生成时间)
```

---

## 核心方法

### `summarize(title, content, max_length=150)`

**功能：** 生成公告摘要

**参数：**
| 参数 | 类型 | 说明 |
|------|------|------|
| `title` | str | 公告标题 |
| `content` | str | 公告正文（建议截断到5000-8000字符） |
| `max_length` | int | 摘要最大字数（默认150） |

**返回：** `(摘要文本, 生成时间)` 或 `None`

---

## Prompt 设计

```python
prompt = f"""请对以下上市公司公告生成一段中文摘要（{max_length}字以内）。

要求：
1. 说明公告的核心事项
2. 如有具体数字（金额、股份数量、时间），请包含
3. 语言简洁专业，适合投资者快速阅读
4. 不要包含"根据公告内容"等套话，直接陈述事实

公告标题：{title}

公告正文：
{content[:6000]}  # 截断防止超长

请直接输出摘要，不要有任何前缀、标题或解释："""
```

**设计要点：**
- **角色设定：** "专业的财经资讯分析师"
- **格式约束：** 直接输出，不要前缀
- **内容要求：** 包含具体数字
- **长度控制：** 150字以内

---

## 配置方式

### 环境变量 (.env)

```bash
# AI 服务配置（OpenAI 兼容接口）
AI_API_BASE=https://api.kimi.com/v1      # API 基础地址
AI_API_KEY=your_api_key_here              # API 密钥
AI_MODEL=kimi-latest                      # 模型名称

# 可选：代理设置
# AI_PROXY=http://127.0.0.1:7890
```

### 支持的模型

| 服务商 | AI_API_BASE | AI_MODEL |
|--------|-------------|----------|
| Kimi | `https://api.kimi.com/v1` | `kimi-latest` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o` |
| 自研模型 | 你的地址 | 你的模型名 |

---

## 关键技术细节

### 1. 内容截断

```python
content_truncated = content[:6000] if len(content) > 6000 else content
```

**为什么截断到6000字符？**
- Token 限制：中文约 1 字符 ≈ 1.5 Token
- 6000 字符 ≈ 9000 Token，加上 Prompt 刚好在安全范围
- 绝大多数公告核心信息在前几页

### 2. Temperature 设置

```python
temperature=0.3  # 低温度，更确定性
```

| Temperature | 效果 |
|-------------|------|
| 0.0 | 完全确定，重复输入得到相同输出 |
| 0.3 | 适度确定，适合摘要（推荐） |
| 0.7 | 有创造性，适合写作 |
| 1.0 | 完全随机 |

### 3. 超时控制

```python
timeout=30  # 30秒超时
```

防止某些复杂文档导致长时间等待。

---

## 错误处理

| 错误场景 | 处理策略 |
|----------|----------|
| API 未配置 | 记录警告，跳过摘要 |
| 内容太短 | < 50 字符不生成摘要 |
| API 调用失败 | 记录错误，返回 None |
| 响应解析失败 | 记录错误，返回 None |

---

## 单例模式

```python
from models.ai_summarizer import get_summarizer

# 全局单例
summarizer = get_summarizer()

# 多次调用，只初始化一次
summary1 = summarizer.summarize(title1, content1)
summary2 = summarizer.summarize(title2, content2)
```

---

## 使用示例

```python
from models.ai_summarizer import get_summarizer
from datetime import datetime

# 获取实例
summarizer = get_summarizer()

# 检查是否可用
if summarizer.is_available():
    # 生成摘要
    title = "金山办公关于完成市场主体变更登记并换发营业执照的公告"
    content = "北京金山办公软件股份有限公司..."  # 公告正文

    result = summarizer.summarize(title, content, max_length=150)

    if result:
        summary, generated_at = result
        print(f"摘要: {summary}")
        print(f"生成时间: {generated_at}")
    else:
        print("摘要生成失败")
else:
    print("AI 服务未配置")
```

---

## 优化建议

### 1. 摘要缓存

**问题：** 相同 PDF 重复调用 API，浪费 Token

**解决方案：**
```python
class CachedSummarizer:
    def __init__(self):
        self.cache = {}  # {content_hash: (summary, timestamp)}

    def summarize(self, title, content):
        content_hash = hashlib.md5(content.encode()).hexdigest()

        if content_hash in self.cache:
            return self.cache[content_hash]

        result = self._call_api(title, content)
        self.cache[content_hash] = result
        return result
```

### 2. 分块摘要（超长文档）

```python
def summarize_long_document(title, content, max_chunk=4000):
    """对超长文档分段摘要，再合并"""
    if len(content) <= max_chunk:
        return summarizer.summarize(title, content)

    # 分段
    chunks = [content[i:i+max_chunk] for i in range(0, len(content), max_chunk)]

    # 每段摘要
    chunk_summaries = []
    for i, chunk in enumerate(chunks):
        chunk_title = f"{title} (第{i+1}/{len(chunks)}部分)"
        result = summarizer.summarize(chunk_title, chunk)
        if result:
            chunk_summaries.append(result[0])

    # 合并摘要
    combined = "\n".join(chunk_summaries)
    return summarizer.summarize(f"{title} (汇总)", combined)
```

### 3. Prompt 优化实验

可以尝试不同 Prompt 风格：

```python
# 风格1：简洁型（当前）
"生成一段中文摘要（150字以内）..."

# 风格2：结构化
"请用以下格式总结公告：\n1. 核心事项：...\n2. 关键数字：...\n3. 影响分析：..."

# 风格3：投资视角
"从投资者角度，提炼该公告的：\n- 关键信息\n- 对股价的潜在影响\n- 建议关注要点"
```

---

## 依赖安装

```bash
pip install openai
```

可选（如果使用代理）：
```bash
pip install httpx
```

---

## 故障排查

### 问题：API 调用超时

**检查：**
1. 网络连接是否正常
2. 是否需要代理（配置 `AI_PROXY`）
3. API 服务是否可用

### 问题：摘要质量差

**优化方向：**
1. 调整 Prompt，明确输出格式
2. 降低 `temperature` 到 0.1
3. 增加 `max_tokens` 限制
4. 对内容预处理，去除页眉页脚

### 问题：Token 消耗过高

**优化：**
1. 减少 `content` 长度（从6000降到4000）
2. 添加缓存机制
3. 只对重要公告生成摘要
