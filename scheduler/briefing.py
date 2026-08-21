# -*- coding: utf-8 -*-
"""
简报生成（briefing）— 模块 E

方案书 3.4.4：
- 复用 pipeline.DataPipeline.generate_briefing（DailyBriefing + to_markdown + 落盘）
- 输出 output/briefings/{YYYY}/{MM}/briefing_{YYYYMMDD}.md/.html
- 同一天多次运行覆盖写同名简报（当天刷新语义）
- items 为空也生成（配合 latest.json sources 状态区分"当天无新闻"与"采集器失败"）
"""
import logging
from datetime import datetime
from pathlib import Path
from typing import List

from config.settings import BRIEFING_DIR, TIME_FILTER
from models.news import NewsItem
from pipeline import DataPipeline


def generate_briefing(items: List[NewsItem], logger: logging.Logger, format: str = 'both') -> Path:
    """复用 DataPipeline.generate_briefing 生成简报，返回落盘路径（优先返回 HTML 路径）。"""
    now = datetime.now()
    pipeline = DataPipeline(hours=TIME_FILTER.get("default_hours", 24))
    pipeline.items = list(items)
    pipeline.generate_briefing(date=now.strftime("%Y年%m月%d日"), quiet=True, format=format)

    base_dir = BRIEFING_DIR / now.strftime("%Y") / now.strftime("%m")
    base_name = now.strftime("briefing_%Y%m%d")

    # 优先返回 HTML 路径（如果生成了），否则返回 MD 路径
    if format in ('html', 'both'):
        html_path = base_dir / f"{base_name}.html"
        if html_path.exists():
            logger.info(f"[简报] {len(items)} 条 -> {html_path}")
            return html_path

    md_path = base_dir / f"{base_name}.md"
    logger.info(f"[简报] {len(items)} 条 -> {md_path}")
    return md_path
