# -*- coding: utf-8 -*-
"""
调度器 CLI 入口

用法:
    python -m scheduler.main [选项]

选项见 --help，完整设计见方案书 3.6。
"""
import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scheduler.main",
        description="金山系资讯采集总调度器",
    )
    parser.add_argument(
        "--sources", type=str, default=None,
        help="仅运行指定源（逗号分隔），默认全部启用源",
    )
    parser.add_argument(
        "--max-workers", type=int, default=4,
        help="并发采集数，默认 4",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只采集不汇总（与 --skip-collect 互斥）",
    )
    parser.add_argument(
        "--skip-collect", action="store_true",
        help="只汇总不采集（重跑汇总/调去重）",
    )
    parser.add_argument(
        "--no-briefing", action="store_true",
        help="跳过简报生成",
    )
    parser.add_argument(
        "--list-sources", action="store_true",
        help="打印启用源清单后退出，不运行",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="打开 DEBUG 日志",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    # 互斥校验
    if args.dry_run and args.skip_collect:
        print("[ERROR] --dry-run 与 --skip-collect 互斥，不能同时指定", file=sys.stderr)
        return 2

    # 源清单模式：启动时校验模块存在性，缺失则列出并报错退出（方案书 3.1.1）
    if args.list_sources:
        from scheduler.registry import get_enabled_sources, validate_sources
        sources = get_enabled_sources()
        missing = validate_sources(sources)
        if missing:
            print(
                f"[ERROR] 以下源模块缺失（{', '.join(f'collectors/{c}/crawler.py' for c in missing)}）",
                file=sys.stderr,
            )
            return 1
        print(f"启用源清单（{len(sources)} 个）:")
        for s in sources:
            print(f"  {s.code:<18}{s.name}")
        return 0

    # 其他模式：编排逻辑由 orchestrator 实现（模块 C）
    try:
        from scheduler.orchestrator import run
    except ImportError:
        print(
            "[ERROR] 编排模块 scheduler/orchestrator.py 尚未实现（模块 C）",
            file=sys.stderr,
        )
        return 1
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
