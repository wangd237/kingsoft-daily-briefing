# -*- coding: utf-8 -*-
"""
主流程编排（orchestrator）

模块 C：采集编排
- 并发 subprocess 运行各源 crawler（默认 4 并发，--max-workers 可调）
- 注入 BATCH_DIR（_latest 批次目录，先清后写）
- 单源超时 kill（默认 5 分钟，SCHEDULER.timeout_seconds）
- 状态判定：ok / failed / warning / timeout（方案书 3.2.2）
- 失败提示 + 调度器日志（output/logs/scheduler/，方案书 3.4.6）

模块 D：汇总与主产物；模块 E：简报与运行报告（后续实现）
"""
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from config.settings import DATA_DIR, LOG_DIR, SCHEDULER
from scheduler.registry import SourceInfo, resolve_sources, validate_sources
from scheduler.result import (
    CrawlerResult,
    STATUS_FAILED,
    STATUS_OK,
    STATUS_TIMEOUT,
    STATUS_WARNING,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- 日志

def setup_logger(verbose: bool = False) -> logging.Logger:
    """调度器日志：控制台 + output/logs/scheduler/{YYYY-MM-DD}.log（3.4.6）"""
    logger = logging.getLogger("scheduler")
    if logger.handlers:  # 已初始化则复用
        return logger
    level = logging.DEBUG if verbose else logging.INFO
    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(fmt)
    logger.addHandler(console)

    log_dir = LOG_DIR / "scheduler"
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(log_dir / f"{datetime.now():%Y-%m-%d}.log", encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger


# ---------------------------------------------------------------- 批次目录

def build_batch_dir(source_code: str, now: Optional[datetime] = None) -> str:
    """
    构造 _latest 批次目录（方案书 3.3.2）：
    output/data/{source}/{YYYY}/{MM}/{DD}/{source}_{YYYYMMDD}_latest
    """
    now = now or datetime.now()
    date_dir = now.strftime("%Y/%m/%d")
    batch_name = f"{source_code}_{now.strftime('%Y%m%d')}_latest"
    return str(DATA_DIR / source_code / date_dir / batch_name)


def _clean_latest(batch_dir: str) -> None:
    """先清后写：运行某源前删除该源 _latest 批次目录（3.3.4），防止旧批次残留"""
    p = Path(batch_dir)
    if p.exists():
        shutil.rmtree(p)


# ---------------------------------------------------------------- 单源运行

def _decode_output(raw: Optional[bytes]) -> str:
    """Windows 编码兼容：按 UTF-8 解码，非法字节替换（3.2.4）"""
    if not raw:
        return ""
    return raw.decode("utf-8", errors="replace")


def _tail(raw: Optional[bytes], max_chars: int = 500) -> str:
    text = _decode_output(raw).strip()
    if len(text) > max_chars:
        text = "..." + text[-max_chars:]
    return text


def run_one(
    source: SourceInfo,
    batch_dir: str,
    timeout_seconds: int,
    cmd: Optional[List[str]] = None,
) -> CrawlerResult:
    """
    在独立 subprocess 中运行单个采集器。
    注入 BATCH_DIR，捕获 stdout/stderr，处理超时。
    cmd 参数供测试注入自定义命令，默认 python -m collectors.{code}.crawler。
    """
    result = CrawlerResult(
        source_code=source.code,
        source_name=source.name,
        batch_dir=batch_dir,
    )
    _clean_latest(batch_dir)

    cmd = cmd or [sys.executable, "-m", source.module_path]
    env = os.environ.copy()
    env["BATCH_DIR"] = batch_dir

    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as e:
        result.status = STATUS_TIMEOUT
        result.error = f"超过 {timeout_seconds}s 超时上限，已终止"
        result.stdout_tail = _tail(e.stdout)
        result.stderr_tail = _tail(e.stderr)
        result.duration_s = round(time.time() - start, 1)
        return result

    result.duration_s = round(time.time() - start, 1)
    result.exit_code = proc.returncode
    result.stdout_tail = _tail(proc.stdout)
    result.stderr_tail = _tail(proc.stderr)

    if proc.returncode != 0:
        # failed：退出码非 0（3.2.2）
        result.status = STATUS_FAILED
        result.error = result.stderr_tail or f"exit code {proc.returncode}"
        return result

    # exit 0：检查批次 JSON 是否产出
    json_path = Path(batch_dir) / f"{Path(batch_dir).name}.json"
    if json_path.exists():
        result.status = STATUS_OK
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            result.count = int(data.get("count", 0))
        except Exception:
            result.count = 0
    else:
        # warning：exit 0 但未产出数据（3.2.2）
        result.status = STATUS_WARNING
        result.error = "exit 0 但未产出批次 JSON"
    return result


# ---------------------------------------------------------------- 并发编排

def collect(
    sources: List[SourceInfo],
    max_workers: int,
    timeout_seconds: int,
    logger: logging.Logger,
) -> Dict[str, CrawlerResult]:
    """线程池并发采集全部源，返回 {code: CrawlerResult}"""
    results: Dict[str, CrawlerResult] = {}
    logger.info(
        f"开始采集 {len(sources)} 个源，并发 {max_workers}，单源超时 {timeout_seconds}s"
    )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(run_one, s, build_batch_dir(s.code), timeout_seconds): s
            for s in sources
        }
        for future in as_completed(future_map):
            source = future_map[future]
            try:
                r = future.result()
            except Exception as e:  # 防御：run_one 内未预期异常
                r = CrawlerResult(
                    source_code=source.code,
                    source_name=source.name,
                    status=STATUS_FAILED,
                    error=f"编排内部异常: {e}",
                )
            results[source.code] = r
            _announce_result(r, logger)

    return results


def _announce_result(r: CrawlerResult, logger: logging.Logger) -> None:
    """结果公告：成功/警告进日志，失败/超时进日志 + 控制台（3.2.3）"""
    if r.status == STATUS_OK:
        logger.info(
            f"[OK] {r.source_code}: {r.count} 条，耗时 {r.duration_s}s -> {r.batch_dir}"
        )
    elif r.status == STATUS_WARNING:
        logger.warning(f"[WARNING] {r.source_code}: {r.error}，耗时 {r.duration_s}s")
    elif r.status == STATUS_TIMEOUT:
        msg = f"[TIMEOUT] {r.source_code}: {r.error}"
        logger.error(msg)
        print(msg, file=sys.stderr)
    else:  # failed
        msg = f"[FAILED] {r.source_code}: 退出码 {r.exit_code}，错误: {r.error}"
        logger.error(msg)
        print(msg, file=sys.stderr)
        print(f"  该源日志: {LOG_DIR / 'collectors' / r.source_code}", file=sys.stderr)


def _summarize(results: Dict[str, CrawlerResult]) -> Dict[str, int]:
    stats: Dict[str, int] = {"total": len(results), "ok": 0, "failed": 0, "warning": 0, "timeout": 0}
    for r in results.values():
        stats[r.status] = stats.get(r.status, 0) + 1
    return stats


# ---------------------------------------------------------------- 主入口

def run(args) -> int:
    """调度器主流程（main.py 延迟引入调用）"""
    logger = setup_logger(args.verbose)

    # registry：源解析 + 模块存在性校验（3.1.1）
    try:
        sources = resolve_sources(args.sources)
    except ValueError as e:
        logger.error(str(e))
        return 1
    missing = validate_sources(sources)
    if missing:
        for code in missing:
            logger.error(f"源模块缺失: collectors/{code}/crawler.py")
        return 1

    # 超时：环境变量可覆盖（供测试），默认取 SCHEDULER 配置
    timeout_seconds = int(
        os.getenv("SCHEDULER_TIMEOUT_SECONDS", SCHEDULER.get("timeout_seconds", 300))
    )
    max_workers = args.max_workers or SCHEDULER.get("max_workers", 4)

    # --skip-collect：只汇总不采集（汇总逻辑属模块 D，尚未实现）
    if args.skip_collect:
        print("[INFO] --skip-collect: 汇总模块（模块 D）尚未实现")
        return 0

    # 采集编排（模块 C）
    results = collect(sources, max_workers, timeout_seconds, logger)

    # 汇总提示
    stats = _summarize(results)
    logger.info(f"采集完成: {stats}")
    print(
        f"\n采集完成: 共 {stats['total']} 个源 | 成功 {stats['ok']} | "
        f"失败 {stats['failed']} | 警告 {stats['warning']} | 超时 {stats['timeout']}"
    )

    # 汇总与主产物（模块 D）：--dry-run 与默认在模块 C 阶段行为一致（只采集）
    if args.dry_run:
        logger.info("--dry-run: 只采集不汇总（模块 D 落地后区分）")
    else:
        logger.info("汇总模块（模块 D）尚未实现，本次仅完成采集编排")
    return 0
