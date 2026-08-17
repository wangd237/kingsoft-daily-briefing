# -*- coding: utf-8 -*-
"""
运行结果数据结构（result）

- CrawlerResult: 单个采集器源的运行结果（状态语义见方案书 3.2.2）
- RunSummary: 一次调度运行的汇总（对应 output/latest.json，见方案书 3.4.2）
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# 源状态取值
STATUS_PENDING = "pending"   # 尚未运行
STATUS_OK = "ok"             # exit 0 且批次 JSON 存在
STATUS_FAILED = "failed"     # exit 非 0
STATUS_WARNING = "warning"   # exit 0 但未产出数据
STATUS_TIMEOUT = "timeout"   # 超过单源超时上限被 kill


@dataclass
class CrawlerResult:
    """单个采集器源的运行结果"""
    source_code: str
    source_name: str = ""
    status: str = STATUS_PENDING
    count: int = 0
    duration_s: float = 0.0
    error: Optional[str] = None
    exit_code: Optional[int] = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    batch_dir: Optional[str] = None  # 实际写入的批次目录

    def to_dict(self) -> dict:
        """latest.json 中 sources 段的形态（方案书 3.4.2）"""
        return {
            "status": self.status,
            "count": self.count,
            "duration_s": round(self.duration_s, 1),
            "error": self.error,
        }

    def to_report_dict(self) -> dict:
        """运行报告用：更详细，含退出码与输出尾部（方案书 3.4.5）"""
        d = self.to_dict()
        d.update({
            "source_code": self.source_code,
            "source_name": self.source_name,
            "exit_code": self.exit_code,
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
            "batch_dir": self.batch_dir,
        })
        return d


@dataclass
class RunSummary:
    """一次调度运行的汇总（与 output/latest.json 结构对应）"""
    run_id: str                 # 20260817_093000
    date: str                   # 2026-08-17
    generated_at: str           # ISO 时间
    sources: Dict[str, CrawlerResult] = field(default_factory=dict)
    stats: Dict[str, int] = field(default_factory=dict)
    items: List[dict] = field(default_factory=list)

    def add_source(self, result: CrawlerResult):
        self.sources[result.source_code] = result

    def to_dict(self) -> dict:
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "date": self.date,
            "generated_at": self.generated_at,
            "sources": {code: r.to_dict() for code, r in self.sources.items()},
            "stats": self.stats,
            "items": self.items,
        }
