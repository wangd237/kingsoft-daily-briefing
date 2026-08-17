# -*- coding: utf-8 -*-
"""
汇总与主产物（summarizer）— 模块 D

方案书 3.4：
- 只扫各源 _latest 批次加载 NewsItem（避开历史堆积，3.4.1）
- 时间过滤（24h 统一）→ 去重（L1 + L2）→ 排序（复用 DataPipeline）
- 附件归集：output/latest_attachments/（PDF 扁平拷贝 + pdf_path 更新，3.4.3）
- 主产物：output/latest.json（sources 状态 + stats + items，3.4.2）
"""
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from config.settings import DATA_DIR, LATEST_ATTACHMENTS_DIR, LATEST_JSON, TIME_FILTER
from models.news import NewsItem
from pipeline import DataPipeline
from scheduler.result import CrawlerResult, RunSummary, STATUS_PENDING


def find_latest_batch_json(source_code: str) -> Optional[Path]:
    """
    找到某源最新的 _latest 批次 JSON。
    同一源可能在多个日期目录留下 _latest 批次，按文件名倒序取最新。
    """
    base = DATA_DIR / source_code
    if not base.exists():
        return None
    candidates = sorted(base.rglob("*_latest.json"), key=lambda p: p.name, reverse=True)
    return candidates[0] if candidates else None


def load_latest_batches(source_codes: List[str]) -> List[NewsItem]:
    """
    只扫各源 _latest 批次（3.4.1），加载为 NewsItem 列表。
    在 raw_data 附加批次上下文（_batch_dir / _fetch_time），供附件归集与 latest.json 使用。
    """
    items: List[NewsItem] = []
    for code in source_codes:
        jf = find_latest_batch_json(code)
        if jf is None:
            continue
        try:
            with open(jf, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"加载批次失败 {jf}: {e}")
            continue
        batch_dir = str(jf.parent)
        fetch_time = data.get("fetch_time", "")
        for item_data in data.get("items", []):
            item = NewsItem.from_dict(item_data)
            item.raw_data.setdefault("_batch_dir", batch_dir)
            item.raw_data.setdefault("_fetch_time", fetch_time)
            items.append(item)
    return items


def collect_attachments(items: List[NewsItem], logger: logging.Logger) -> int:
    """
    附件归集（3.4.3）：
    - 去重后保留记录中 raw_data.pdf_path 指向的 PDF 拷贝到 output/latest_attachments/
    - 扁平命名防重名（{source_code}_{原文件名}，冲突追加序号）
    - 更新 raw_data.pdf_path 为归集后相对路径（相对 output/）
    - 每次运行清空重建；只归集 PDF（txt 为解析产物，不进入归集链路）
    """
    if LATEST_ATTACHMENTS_DIR.exists():
        shutil.rmtree(LATEST_ATTACHMENTS_DIR)
    LATEST_ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)

    used_names = set()
    count = 0
    for item in items:
        batch_dir = item.raw_data.get("_batch_dir", "")
        pdf_rel = (item.raw_data or {}).get("pdf_path", "")
        if not batch_dir or not pdf_rel:
            continue
        src = Path(batch_dir) / pdf_rel
        if not src.exists() or src.suffix.lower() != ".pdf":
            continue

        base = f"{item.source_code}_{src.name}"
        name = base
        i = 1
        while name in used_names:
            p = Path(base)
            name = f"{p.stem}_{i}{p.suffix}"
            i += 1
        used_names.add(name)

        dst = LATEST_ATTACHMENTS_DIR / name
        try:
            shutil.copy2(src, dst)
        except Exception as e:
            logger.warning(f"附件拷贝失败 {src} -> {dst}: {e}")
            continue

        # 更新为归集后相对路径（相对 output/，下游可用 OUTPUT_DIR / pdf_path 定位）
        item.raw_data["pdf_path"] = f"latest_attachments/{name}"
        count += 1
    return count


def _to_latest_item(item: NewsItem) -> dict:
    """latest.json items 段（3.4.2 精简结构，不含原始调试字段）"""
    return {
        "title": item.title,
        "url": item.url,
        "publish_time": item.publish_time.isoformat() if item.publish_time else item.date,
        "source_code": item.source_code,
        "credibility_tag": item.credibility_tag,
        "category": item.category,
        "summary": item.summary,
        "pdf_path": (item.raw_data or {}).get("pdf_path", ""),
        "fetch_time": item.raw_data.get("_fetch_time", ""),
    }


def run_summary(
    source_codes: List[str],
    results: Optional[Dict[str, CrawlerResult]],
    logger: logging.Logger,
    hours: Optional[int] = None,
) -> RunSummary:
    """
    汇总主流程（3.4.1）：加载 _latest → 24h 过滤 → 去重（L1+L2）→ 排序
    → 附件归集 → 写 latest.json（3.4.2/3.4.3）。
    results 为采集编排结果；None/缺省源标记 pending（未采集，如 --skip-collect）。
    """
    now = datetime.now()
    summary = RunSummary(
        run_id=now.strftime("%Y%m%d_%H%M%S"),
        date=now.strftime("%Y-%m-%d"),
        generated_at=now.isoformat(timespec="seconds"),
    )

    # 1. 加载 _latest 批次
    items = load_latest_batches(source_codes)
    raw = len(items)
    logger.info(f"[汇总] 加载 _latest 批次: {raw} 条")

    # 2. 时间过滤（24h 统一兜底，3.4.1）
    pipeline = DataPipeline(hours=hours or TIME_FILTER.get("default_hours", 24))
    items = pipeline.filter_by_time(items)
    after_time = len(items)
    logger.info(f"[汇总] 时间过滤后(最近{pipeline.hours}h): {after_time} 条")

    # 3. 去重（L1 URL + L2 归一化标题，3.5）
    items = pipeline.deduplicate(items)
    after_dedup = len(items)
    logger.info(f"[汇总] 去重后: {after_dedup} 条")

    # 4. 排序（时间倒序）
    items.sort(key=lambda x: x.publish_time or datetime.min, reverse=True)

    # 5. 附件归集（PDF，3.4.3）
    attachments = collect_attachments(items, logger)
    logger.info(f"[汇总] 附件归集: {attachments} 个 PDF")

    # 6. sources 状态：优先采集结果，缺省源标记 pending
    results = results or {}
    for code in source_codes:
        if code in results:
            summary.add_source(results[code])
        else:
            summary.add_source(CrawlerResult(source_code=code, status=STATUS_PENDING))

    # 7. stats + items
    summary.stats = {
        "raw": raw,
        "after_time": after_time,
        "after_dedup": after_dedup,
        "attachments": attachments,
    }
    summary.items = [_to_latest_item(i) for i in items]
    summary.news_items = items  # 模块 E 简报用（去重排序后的 NewsItem）

    # 8. 写主产物
    LATEST_JSON.write_text(
        json.dumps(summary.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(f"[汇总] 主产物已写入: {LATEST_JSON}")
    return summary
