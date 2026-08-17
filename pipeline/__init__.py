# -*- coding: utf-8 -*-
"""
数据处理管道
整合所有处理步骤
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from datetime import datetime, timedelta
from typing import List
from collections import defaultdict
import json
import os
import re

from models.news import NewsItem, DailyBriefing, CREDIBILITY_PRIORITY
from config.settings import TIME_FILTER, BRIEFING_DIR


# ---- L2 去重增强（方案书 3.5）：标题通用前后缀规则集 ----
# 初始规则集，回测后可扩充；只匹配明确的新闻性修饰，避免误删正文关键词
_TITLE_PREFIX_PATTERNS = [
    re.compile(r'^(快讯|独家|最新|重磅|突发)\s*[:|｜]'),      # 快讯: xxx / 独家｜xxx
    re.compile(r'^【(快讯|独家|最新|重磅|突发)】'),           # 【快讯】xxx
    re.compile(r'^\[(快讯|独家|最新|重磅|突发)\]'),           # [快讯] xxx
]
_TITLE_SUFFIX_PATTERNS = [
    re.compile(r'[|｜]\s*来源[:：]?\s*\S+$'),                 # xxx | 来源：东方财富
    re.compile(r'\s*来源[:：]\s*\S+$'),                        # xxx 来源:东方财富
]

# opencc 实例缓存（繁转简），避免每次归一化都重建
_OPENCC = None


def _get_opencc():
    """获取 opencc t2s 转换器；opencc 不可用时返回 None（降级跳过繁转简）"""
    global _OPENCC
    if _OPENCC is None:
        try:
            from opencc import OpenCC
            _OPENCC = OpenCC('t2s')
        except Exception:
            _OPENCC = False
    return _OPENCC or None


class DataPipeline:
    """数据处理管道"""

    def __init__(self, hours: int = None):
        self.hours = hours or TIME_FILTER.get('default_hours', 24)
        self.items: List[NewsItem] = []

    def load_from_files(self, source_code: str = None, date: str = None) -> List[NewsItem]:
        """
        从数据文件加载
        :param source_code: 信息源代码，None表示加载所有
        :param date: 日期格式YYYYMMDD，None表示今天
        """
        from config.settings import DATA_DIR

        if date is None:
            date = datetime.now().strftime('%Y%m%d')

        items = []

        # 确定搜索目录
        if source_code:
            base_dirs = [DATA_DIR / source_code]
        else:
            base_dirs = [d for d in DATA_DIR.iterdir() if d.is_dir()]

        for base_dir in base_dirs:
            if not base_dir.exists():
                continue

            # 递归查找JSON文件
            for json_file in base_dir.rglob('*.json'):
                try:
                    with open(json_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)

                    # 日期匹配：优先文件名，fallback 到 fetch_time
                    fetch_time = data.get('fetch_time', '')
                    fetch_date = fetch_time[:10].replace('-', '') if fetch_time else ''
                    if date not in json_file.name and date != fetch_date:
                        continue

                    for item_data in data.get('items', []):
                        items.append(NewsItem.from_dict(item_data))
                except Exception as e:
                    print(f"加载文件失败 {json_file}: {e}")

        self.items = items
        print(f"从文件加载: {len(items)} 条")
        return items

    def filter_by_time(self, items: List[NewsItem] = None) -> List[NewsItem]:
        """时间过滤"""
        if items is None:
            items = self.items

        cutoff_time = datetime.now() - timedelta(hours=self.hours)
        filtered = []
        for item in items:
            # publish_time 优先，fallback 到 date 字段解析
            pub_time = item.publish_time
            if pub_time is None and item.date:
                try:
                    pub_time = datetime.strptime(item.date, '%Y-%m-%d')
                except ValueError:
                    pass
            if pub_time and pub_time >= cutoff_time:
                filtered.append(item)

        print(f"时间过滤后(最近{self.hours}小时): {len(filtered)} 条")
        return filtered

    @staticmethod
    def _normalize_title(title: str) -> str:
        """标题归一化（L2 增强，方案书 3.5）：
        1. 繁转简（opencc，不可用时降级）
        2. 去通用前后缀（快讯: / 独家| / 【快讯】 / | 来源：xxx）
        3. 去掉所有标点/空格，只保留中文、字母、数字
        """
        if not title:
            return ""
        t = title
        cc = _get_opencc()
        if cc:
            try:
                t = cc.convert(t)
            except Exception:
                pass  # 转换失败时保留原文
        for pat in _TITLE_PREFIX_PATTERNS:
            t = pat.sub('', t)
        for pat in _TITLE_SUFFIX_PATTERNS:
            t = pat.sub('', t)
        return re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', t.lower())

    def deduplicate(self, items: List[NewsItem]) -> List[NewsItem]:
        """
        去重：URL 与归一化标题各自分组，同组内保留可信度最高的一条
        （官方公告 > 官方资讯 > 媒体报道）
        """
        def better(a: NewsItem, b: NewsItem) -> NewsItem:
            """返回可信度更高的一条，同级保留先出现的"""
            pa = CREDIBILITY_PRIORITY.get(a.credibility_tag, 99)
            pb = CREDIBILITY_PRIORITY.get(b.credibility_tag, 99)
            return b if pb < pa else a

        # 第一轮：按 URL 归并
        by_url = {}
        order = []
        for item in items:
            key = item.url
            if key in by_url:
                by_url[key] = better(by_url[key], item)
            else:
                by_url[key] = item
                order.append(key)

        # 第二轮：按归一化标题归并（跨源同一篇报道）
        by_title = {}
        title_order = []
        for key in order:
            item = by_url[key]
            tkey = self._normalize_title(item.title)
            # 归一化后为空（如纯符号标题）时退化为 URL 分组，避免多条误归并
            if not tkey:
                tkey = f"__url__{item.url}"
            if tkey in by_title:
                by_title[tkey] = better(by_title[tkey], item)
            else:
                by_title[tkey] = item
                title_order.append(tkey)

        unique_items = [by_title[k] for k in title_order]

        print(f"去重后: {len(unique_items)} 条")
        return unique_items

    def process(self, items: List[NewsItem] = None) -> List[NewsItem]:
        """完整处理流程"""
        if items is None:
            items = self.items

        print(f"\n原始数据: {len(items)} 条")

        # 1. 时间过滤
        items = self.filter_by_time(items)

        # 2. 去重
        items = self.deduplicate(items)

        # 3. 排序（时间倒序）
        items.sort(key=lambda x: x.publish_time or datetime.min, reverse=True)

        self.items = items
        return items

    def generate_briefing(self, date: str = None) -> str:
        """生成简报"""
        if date is None:
            date = datetime.now().strftime('%Y年%m月%d日')

        briefing = DailyBriefing(date=date)

        for item in self.items:
            briefing.add_item(item)

        # 保存Markdown文件
        md_content = briefing.to_markdown()

        # 文件路径: output/briefings/2026/07/briefing_20260729.md
        now = datetime.now()
        year_dir = BRIEFING_DIR / now.strftime('%Y')
        month_dir = year_dir / now.strftime('%m')
        month_dir.mkdir(parents=True, exist_ok=True)

        filename = now.strftime('briefing_%Y%m%d.md')
        file_path = month_dir / filename

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(md_content)

        print(f"\n简报已保存: {file_path}")
        return md_content


def main():
    """测试运行"""
    pipeline = DataPipeline(hours=24)

    # 加载今日所有数据
    pipeline.load_from_files(date=datetime.now().strftime('%Y%m%d'))

    # 处理
    pipeline.process()

    # 生成简报
    if pipeline.items:
        md = pipeline.generate_briefing()
        print("\n" + "="*60)
        print("简报预览（前500字符）:")
        print("="*60)
        print(md[:500])
        print("...")
    else:
        print("\n没有数据可生成简报")


if __name__ == "__main__":
    main()
