# -*- coding: utf-8 -*-
"""
数据模型
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum
from collections import defaultdict


# Jinja2 环境（单例，开启 autoescape 防 XSS）
_JINJA_ENV = None


def _get_jinja_env():
    global _JINJA_ENV
    if _JINJA_ENV is None:
        from jinja2 import Environment, BaseLoader, select_autoescape
        _JINJA_ENV = Environment(
            loader=BaseLoader(),
            autoescape=select_autoescape(['html', 'xml']),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        # 注册全局过滤器
        def credibility_class(tag: str) -> str:
            if '官方公告' in tag:
                return 'official-notice'
            elif '官方资讯' in tag:
                return 'official-news'
            return 'media-report'
        _JINJA_ENV.filters['credibility_class'] = credibility_class
    return _JINJA_ENV


class CredibilityLevel(Enum):
    """可信度等级"""
    OFFICIAL_NOTICE = "【官方公告】"   # 交易所/IR官网
    OFFICIAL_NEWS = "【官方资讯】"     # 官微/官博
    MEDIA_REPORT = "【媒体报道】"      # 二三级媒体


class NewsCategory(Enum):
    """资讯分类"""
    CAPITAL = "资本动态"
    PRODUCT = "产品动态"
    COOPERATION = "市场&政企合作"
    EVENT = "活动IP"
    HR = "人事&其他声明"


# 可信度只允许这三个值，数值越小越可信（用于去重择优）
CREDIBILITY_PRIORITY = {
    CredibilityLevel.OFFICIAL_NOTICE.value: 1,
    CredibilityLevel.OFFICIAL_NEWS.value: 2,
    CredibilityLevel.MEDIA_REPORT.value: 3,
}


def normalize_credibility(tag: str) -> str:
    """
    将可信度标签规范化为三值之一

    兼容历史/复合写法，例如：
        【媒体报道】【资讯】      -> 【媒体报道】
        【投资者社区】｜金山办公  -> 【媒体报道】
        【金融数据】              -> 【媒体报道】
    无法识别时按最低可信度处理，避免误标为官方公告。
    """
    if not tag:
        return CredibilityLevel.MEDIA_REPORT.value

    # 官方公告 > 官方资讯 > 媒体报道，取出现的最高级别
    for level in (CredibilityLevel.OFFICIAL_NOTICE,
                  CredibilityLevel.OFFICIAL_NEWS,
                  CredibilityLevel.MEDIA_REPORT):
        if level.value in tag:
            return level.value

    return CredibilityLevel.MEDIA_REPORT.value


@dataclass
class NewsItem:
    """资讯条目"""
    # 必填字段
    title: str
    date: str
    url: str

    # 来源信息
    source: str = ""                    # 信息源名称
    source_code: str = ""               # 信息源代码
    credibility_tag: str = "【官方公告】"

    # 分类信息
    category: str = ""

    # 时间
    publish_time: Optional[datetime] = None
    summary_generated_at: Optional[datetime] = None  # 摘要生成时间

    # 内容
    summary: str = ""
    content: str = ""
    content_ref: str = ""  # 正文文件引用路径

    # 原始数据（调试用）
    raw_data: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """初始化后处理"""
        if self.publish_time is None and self.date:
            self._parse_date()

        if not self.summary and self.title:
            self.summary = self.title[:150] + "..." if len(self.title) > 150 else self.title

    def _parse_date(self):
        """解析日期字符串，统一 date 字段为 YYYY-MM-DD 格式"""
        formats = [
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d %H:%M',
            '%Y-%m-%d',
            '%Y/%m/%d %H:%M:%S',
            '%Y/%m/%d',
            '%m/%d/%y',        # 5/27/26
            '%m/%d/%Y',        # 5/27/2026
            '%Y年%m月%d日',    # 2025年12月31日
            '%Y年%m月%d',      # 2025年12月31
        ]

        for fmt in formats:
            try:
                self.publish_time = datetime.strptime(self.date, fmt)
                # 统一 date 为 YYYY-MM-DD
                self.date = self.publish_time.strftime('%Y-%m-%d')
                return
            except ValueError:
                continue

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（content 存到外部文件，JSON 只存 content_ref）"""
        return {
            'title': self.title,
            'date': self.date,
            'url': self.url,
            'source': self.source,
            'source_code': self.source_code,
            'credibility_tag': self.credibility_tag,
            'category': self.category,
            'publish_time': self.publish_time.isoformat() if self.publish_time else None,
            'summary_generated_at': self.summary_generated_at.isoformat() if self.summary_generated_at else None,
            'summary': self.summary,
            # 'content': self.content,  # 内容存到外部文件，通过 content_ref 引用
            'content_ref': self.content_ref,
            'raw_data': self.raw_data,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'NewsItem':
        """从字典创建"""
        # strip 数字前缀：①资本动态 → 资本动态
        raw_category = data.get('category', '')
        category = raw_category.lstrip('①②③④⑤')

        item = cls(
            title=data.get('title', ''),
            date=data.get('date', ''),
            url=data.get('url', ''),
            source=data.get('source', ''),
            source_code=data.get('source_code', ''),
            credibility_tag=normalize_credibility(data.get('credibility_tag', '')),
            category=category,
            summary=data.get('summary', ''),
            content=data.get('content', ''),
            content_ref=data.get('content_ref', ''),
            raw_data=data.get('raw_data') or {},
        )

        # 处理 publish_time
        if 'publish_time' in data and data['publish_time']:
            try:
                item.publish_time = datetime.fromisoformat(data['publish_time'])
            except:
                pass

        # 处理 summary_generated_at
        if 'summary_generated_at' in data and data['summary_generated_at']:
            try:
                item.summary_generated_at = datetime.fromisoformat(data['summary_generated_at'])
            except:
                pass

        return item


@dataclass
class DailyBriefing:
    """每日简报"""
    date: str
    items: list = field(default_factory=list)
    generated_at: datetime = field(default_factory=datetime.now)

    # 统计
    total_count: int = 0
    official_count: int = 0

    def add_item(self, item: NewsItem):
        """添加资讯"""
        self.items.append(item)
        self.total_count += 1
        if '官方公告' in item.credibility_tag:
            self.official_count += 1

    def to_markdown(self) -> str:
        """生成Markdown格式"""
        from collections import defaultdict

        lines = [
            f"# 金山系资讯日报 - {self.date}",
            "",
            "## 📊 概览",
            f"- 采集时间：{self.generated_at.strftime('%Y-%m-%d %H:%M')}",
            f"- 资讯总数：**{self.total_count}** 条",
            f"- 官方公告：{self.official_count} 条",
            "",
            "---",
            "",
        ]

        # 按分类分组
        grouped = defaultdict(list)
        for item in self.items:
            grouped[item.category].append(item)

        # 按分类输出
        category_order = ['资本动态', '产品动态', '市场&政企合作', '活动IP', '人事&其他声明']

        for category in category_order:
            if category in grouped:
                lines.append(f"## {category} ({len(grouped[category])}条)")
                lines.append("")

                for i, item in enumerate(grouped[category], 1):
                    time_str = item.publish_time.strftime('%m-%d %H:%M') if item.publish_time else item.date
                    lines.append(f"### {i}. {item.credibility_tag} {item.title}")
                    lines.append(f"- **来源**：{item.source}")
                    lines.append(f"- **时间**：{time_str}")
                    lines.append(f"- **链接**：[查看原文]({item.url})")
                    lines.append(f"- **摘要**：{item.summary}")
                    lines.append("")
                    lines.append("---")
                    lines.append("")

        # 重点关注
        if self.items:
            lines.append("## ⭐ 重点关注")
            lines.append("")
            for item in self.items[:5]:
                time_str = item.publish_time.strftime('%m-%d %H:%M') if item.publish_time else item.date
                lines.append(f"- **{item.category}** | {item.title} （{time_str}）")
            lines.append("")

        lines.append("---")
        lines.append(f"*本简报由系统自动生成于 {self.generated_at.strftime('%Y-%m-%d %H:%M:%S')}*")

        return "\n".join(lines)

    def to_html(self) -> str:
        """生成HTML格式（内联CSS，适合邮件/网页直接渲染）"""
        env = _get_jinja_env()

        # 按分类分组
        grouped = defaultdict(list)
        for item in self.items:
            grouped[item.category].append(item)

        category_order = ['资本动态', '产品动态', '市场&政企合作', '活动IP', '人事&其他声明']

        template = env.from_string("""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>金山系资讯日报 - {{ date }}</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; line-height: 1.6; color: #333; max-width: 800px; margin: 0 auto; padding: 20px; background: #fafafa; }
        .container { background: #fff; padding: 30px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
        h1 { color: #1a1a1a; border-bottom: 2px solid #e8e8e8; padding-bottom: 10px; margin-bottom: 20px; font-size: 1.8rem; }
        .meta { color: #666; font-size: 0.9rem; margin-bottom: 20px; }
        .stats { display: flex; gap: 20px; margin: 20px 0; padding: 15px; background: #f5f5f5; border-radius: 6px; }
        .stat { font-weight: 600; }
        .stat-label { color: #999; font-weight: 400; margin-right: 6px; }
        hr { border: none; border-top: 1px solid #eee; margin: 24px 0; }
        .category { margin-top: 24px; }
        .category-title { color: #2c3e50; font-size: 1.3rem; margin-bottom: 16px; padding-bottom: 8px; border-bottom: 1px solid #eee; }
        .item { margin-bottom: 20px; padding: 16px; background: #fafafa; border-radius: 6px; border-left: 3px solid #3498db; }
        .item-title { font-size: 1.05rem; font-weight: 600; margin-bottom: 8px; color: #1a1a1a; }
        .credibility { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; margin-right: 8px; }
        .credibility-official-notice { background: #ffeaa7; color: #b8860b; }
        .credibility-official-news { background: #d4f6d4; color: #2d7d2d; }
        .credibility-media-report { background: #e8e8e8; color: #555; }
        .item-meta { color: #888; font-size: 0.85rem; margin-bottom: 8px; }
        .item-meta span { margin-right: 16px; }
        .item-meta a { color: #3498db; text-decoration: none; }
        .item-meta a:hover { text-decoration: underline; }
        .item-summary { color: #444; font-size: 0.95rem; }
        .focus { margin-top: 24px; }
        .focus-title { color: #e74c3c; font-size: 1.2rem; margin-bottom: 12px; }
        .focus-list { list-style: none; padding: 0; }
        .focus-list li { padding: 8px 12px; background: #fffbe6; border-radius: 4px; margin-bottom: 8px; border-left: 3px solid #f39c12; font-size: 0.9rem; }
        .footer { margin-top: 32px; padding-top: 16px; border-top: 1px solid #eee; color: #999; font-size: 0.8rem; text-align: center; }
    </style>
</head>
<body>
    <div class="container">
        <h1>金山系资讯日报 - {{ date }}</h1>
        <div class="meta">
            采集时间：{{ generated_at.strftime('%Y-%m-%d %H:%M') }}
        </div>
        <div class="stats">
            <span class="stat"><span class="stat-label">资讯总数：</span>{{ total_count }} 条</span>
            <span class="stat"><span class="stat-label">官方公告：</span>{{ official_count }} 条</span>
        </div>
        <hr>

        {% for category in category_order %}
        {% if category in grouped %}
        <div class="category">
            <h2 class="category-title">{{ category }} ({{ grouped[category]|length }}条)</h2>
            {% for item in grouped[category] %}
            <div class="item">
                <div class="item-title">
                    <span class="credibility credibility-{{ item.credibility_tag|credibility_class }}">{{ item.credibility_tag }}</span>
                    {{ item.title }}
                </div>
                <div class="item-meta">
                    <span>来源：{{ item.source }}</span>
                    <span>时间：{{ item.publish_time.strftime('%m-%d %H:%M') if item.publish_time else item.date }}</span>
                    <span><a href="{{ item.url }}" target="_blank" rel="noopener">查看原文</a></span>
                </div>
                <div class="item-summary">{{ item.summary }}</div>
            </div>
            {% endfor %}
        </div>
        <hr>
        {% endif %}
        {% endfor %}

        {% if items %}
        <div class="focus">
            <h2 class="focus-title">⭐ 重点关注</h2>
            <ul class="focus-list">
            {% for item in items[:5] %}
                <li><strong>{{ item.category }}</strong> | {{ item.title }} （{{ item.publish_time.strftime('%m-%d %H:%M') if item.publish_time else item.date }}）</li>
            {% endfor %}
            </ul>
        </div>
        {% endif %}

        <hr>
        <div class="footer">
            本简报由系统自动生成于 {{ generated_at.strftime('%Y-%m-%d %H:%M:%S') }}
        </div>
    </div>
</body>
</html>
""")

        return template.render(
            date=self.date,
            generated_at=self.generated_at,
            total_count=self.total_count,
            official_count=self.official_count,
            grouped=grouped,
            category_order=category_order,
            items=self.items,
        )
