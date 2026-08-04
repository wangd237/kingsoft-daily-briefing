# -*- coding: utf-8 -*-
"""
数据模型
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum


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
        """解析日期字符串"""
        formats = [
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d %H:%M',
            '%Y-%m-%d',
            '%Y/%m/%d %H:%M:%S',
            '%Y/%m/%d',
        ]

        for fmt in formats:
            try:
                self.publish_time = datetime.strptime(self.date, fmt)
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
        item = cls(
            title=data.get('title', ''),
            date=data.get('date', ''),
            url=data.get('url', ''),
            source=data.get('source', ''),
            source_code=data.get('source_code', ''),
            credibility_tag=data.get('credibility_tag', '【官方公告】'),
            category=data.get('category', ''),
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
        category_order = ['①资本动态', '②产品动态', '③市场&政企合作', '④活动IP', '⑤人事&其他声明']

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
