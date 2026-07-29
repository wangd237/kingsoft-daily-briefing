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

from models.news import NewsItem, DailyBriefing
from config.settings import TIME_FILTER, BRIEFING_DIR


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
                # 检查日期
                if date not in json_file.name:
                    continue

                try:
                    with open(json_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
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
        filtered = [item for item in items
                   if item.publish_time and item.publish_time >= cutoff_time]

        print(f"时间过滤后(最近{self.hours}小时): {len(filtered)} 条")
        return filtered

    def deduplicate(self, items: List[NewsItem]) -> List[NewsItem]:
        """去重"""
        seen_urls = set()
        seen_titles = set()
        unique_items = []

        for item in items:
            # URL去重
            if item.url in seen_urls:
                continue

            # 标题归一化去重
            import hashlib
            norm_title = item.title.lower().replace(' ', '').replace('：', '')
            title_hash = hashlib.md5(norm_title.encode()).hexdigest()
            if title_hash in seen_titles:
                continue

            seen_urls.add(item.url)
            seen_titles.add(title_hash)
            unique_items.append(item)

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
