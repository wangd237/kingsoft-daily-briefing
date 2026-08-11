# Agent Guide

## Project

Python-based news collection system for Kingsoft group (金山系). Collects announcements from financial sources, applies AI summaries, generates daily briefings.

## Commands

```bash
# Setup
pip install -r requirements.txt
playwright install chromium

# Run a single collector (via -m module syntax)
python -m collectors.cninfo.crawler
python -m collectors.stcn.crawler
python -m collectors.cls.crawler
python -m collectors.hkex.crawler
python -m collectors.zhidx.crawler
python -m collectors.gamelook.crawler
python -m collectors.gamersky.crawler

# Run pipeline (generate briefing from collected data)
python pipeline/__main__.py

# Diagnose AI summarizer
python scripts/diagnose_ai.py
```

## Key Quirks

- **No `main.py`**: README references it but it does not exist. Do not create it.
- **sys.path hacks everywhere**: Most collector modules add `parent.parent.parent` to `sys.path`. Follow this pattern when creating new modules.
- **Data stored in batch directories**: `output/data/{source}/{YYYY}/{MM}/{DD}/{source}_{YYYYMMDD}_{HHMMSS}/` — JSON + `contents/` subdirectory.
- **content_ref pattern**: JSON files store text content as separate files, not inline. Use `collectors.base.load_content_from_ref()` to read them.
- **Playwright required**: Some collectors (stcn, huxiu, eastmoney) use headless browser. Always `playwright install chromium` after setup.
- **AI summarizer graceful degradation**: If `.env` is not configured or API is unreachable, collectors skip summarization and continue.
- **Python 3.10+**: Type hints use `str | None` syntax (e.g. `stcn/crawler.py:34`).

## Configuration

- `config/settings.py` — single source of truth for all config (collectors, categories, time filter, notifiers)
- `.env` — AI_API_BASE, AI_API_KEY, AI_MODEL (Kimi/DeepSeek/OpenAI-compatible)
- `COLLECTORS` dict controls which sources are enabled; default is Python, not YAML

## Running Individual Collectors

Each collector has its own `__main__.py` (e.g. `collectors/cninfo/crawler.py`):
```bash
python -m collectors.cninfo.crawler
```

## Data Flow

1. Collectors → `output/data/{source}/` (JSON + content files)
2. Pipeline loads all data files for a given date → time filter → dedup → sort
3. `generate_briefing()` → `output/briefings/{YYYY}/{MM}/briefing_{YYYYMMDD}.md`

## Notes

- `tests/` directory exists but is empty — no test suite currently
- `node_modules/` contains playwright-core (no package.json — installed standalone)
- `.claude/settings.local.json` has Claude permission allowlist for common commands
- Windows paths: use backslash conventions; the codebase uses `Path` objects throughout
