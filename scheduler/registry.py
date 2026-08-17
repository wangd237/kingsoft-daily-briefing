# -*- coding: utf-8 -*-
"""
源注册表（registry）

从 config.COLLECTORS 解析启用的采集器，并校验模块存在性。
增添/去除采集器只需改 config：见方案书 3.1.1。
"""
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from config.settings import COLLECTORS

# 项目根目录（scheduler/ 的上一级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class SourceInfo:
    """单个采集器源的描述"""
    code: str
    name: str
    config: dict

    @property
    def module_path(self) -> str:
        """模块路径，与 config key 保持一致：collectors/{code}/crawler"""
        return f"collectors.{self.code}.crawler"

    @property
    def crawler_file(self) -> Path:
        """crawler.py 文件路径"""
        return PROJECT_ROOT / "collectors" / self.code / "crawler.py"


def get_enabled_sources() -> List[SourceInfo]:
    """返回 enabled=True 的源清单，保持 config 定义顺序"""
    return [
        SourceInfo(code=code, name=cfg.get("name", code), config=cfg)
        for code, cfg in COLLECTORS.items()
        if cfg.get("enabled")
    ]


def validate_sources(sources: List[SourceInfo]) -> List[str]:
    """
    校验源模块存在性，返回缺失模块的 code 清单（空列表 = 全部通过）
    防止 config code 写错时静默失败
    """
    missing = []
    for s in sources:
        if not s.crawler_file.exists():
            missing.append(s.code)
    return missing


def resolve_sources(sources_arg: Optional[str]) -> List[SourceInfo]:
    """
    解析 --sources 参数为 SourceInfo 清单
    - None → 全部启用源
    - 逗号分隔列表 → 校验每个 code 为启用源，未知/未启用则抛 ValueError
    """
    enabled = {s.code: s for s in get_enabled_sources()}

    if not sources_arg:
        return list(enabled.values())

    codes = [c.strip() for c in sources_arg.split(",") if c.strip()]
    unknown = [c for c in codes if c not in enabled]
    if unknown:
        raise ValueError(
            f"未知或未启用的源: {', '.join(unknown)}；可用源: {', '.join(enabled)}"
        )
    return [enabled[c] for c in codes]
