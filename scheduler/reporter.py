# -*- coding: utf-8 -*-
"""
运行报告（reporter）— 模块 E

方案书 3.4.5：
- output/reports/run_{YYYYMMDD_HHMMSS}.json
- 每源 status / count / duration / error / stdout 尾部 + 汇总统计
- 调试期一眼定位失败源；未来映射到多维表格的"源状态"字段
"""
import json
import logging
from pathlib import Path
from typing import Dict, Optional

from config.settings import REPORT_DIR
from scheduler.result import CrawlerResult


def write_run_report(
    run_id: str,
    date: str,
    generated_at: str,
    results: Dict[str, CrawlerResult],
    stats: Dict[str, int],
    briefing_path: Optional[Path] = None,
    logger: Optional[logging.Logger] = None,
) -> Path:
    """写运行报告 JSON（run_{run_id}.json），返回文件路径。"""
    payload = {
        "schema_version": 1,
        "run_id": run_id,
        "date": date,
        "generated_at": generated_at,
        "sources": {code: r.to_report_dict() for code, r in results.items()},
        "stats": stats,
        "briefing": str(briefing_path) if briefing_path else None,
    }
    path = REPORT_DIR / f"run_{run_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if logger:
        logger.info(f"[报告] 已写入: {path}")
    return path
