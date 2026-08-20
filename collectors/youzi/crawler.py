"""GameRes 游资网采集器：仅采集金山游戏业务相关新闻。"""

import json
import random
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

# 让直接执行或 python -m 执行时都能导入项目模块
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from collectors.base import BaseCrawler
from config.settings import CATEGORIES, COLLECTORS, DATA_DIR, LOG_DIR
from models.news import NewsItem


class GameResCrawler(BaseCrawler):
    """通过 GameRes 站内搜索采集金山游戏业务资讯。"""

    source_name = "GameRes游资网"
    source_code = "youzi"
    credibility_base = "【媒体报道】"

    SEARCH_PAGE_URL = "https://www.gameres.com/search"
    # 只抓取第一页结果（不翻页）
    SEARCH_API_URL = "https://www.gameres.com/api/v1/portal/search?q={keyword}&page=1&page_size=20"
    SITE_ROOT = "https://www.gameres.com"

    def __init__(
        self,
        output_dir: Optional[str] = None,
        log_dir: Optional[str] = None,
        enable_summary: Optional[bool] = None,
        keywords: Optional[List[str]] = None,
        hours_window: Optional[int] = None,
    ) -> None:
        # 关键词唯一来源：settings.COLLECTORS['youzi']['keywords']
        config = COLLECTORS.get("youzi", {})
        self.keywords = keywords or list(config.get("keywords") or [])
        if not self.keywords:
            raise ValueError(
                "未配置采集关键词：请在 settings.COLLECTORS['youzi']['keywords'] 中填写"
            )
        self.hours_window = hours_window or config.get("hours_window", 168)

        if enable_summary is None:
            enable_summary = bool(config.get("enable_summary", True))

        super().__init__(
            output_dir=str(output_dir or DATA_DIR),
            log_dir=str(log_dir or LOG_DIR),
            enable_summary=enable_summary,
        )

        self.cutoff_time = datetime.now() - timedelta(hours=self.hours_window)
        self.request_delay_min = float(config.get("request_delay_min", 1.5))
        self.request_delay_max = float(config.get("request_delay_max", 3.0))
        self.rate_limit_retries = int(config.get("rate_limit_retries", 3))

    @staticmethod
    def _clean_text(value: Any) -> str:
        if not value:
            return ""
        text = re.sub(r"<[^>]+>", " ", str(value))
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _parse_timestamp(value: Any) -> Optional[datetime]:
        try:
            timestamp = int(float(value))
            if timestamp > 10_000_000_000:
                timestamp /= 1000
            return datetime.fromtimestamp(timestamp)
        except (TypeError, ValueError, OSError, OverflowError):
            return None

    @staticmethod
    def _make_json_safe(value: Any) -> Any:
        """递归转换 JSON 不支持的对象，特别是 datetime。"""
        if isinstance(value, (datetime, date)):
            return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
        if isinstance(value, dict):
            return {str(key): GameResCrawler._make_json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [GameResCrawler._make_json_safe(item) for item in value]
        if hasattr(value, "to_dict") and callable(value.to_dict):
            return GameResCrawler._make_json_safe(value.to_dict())
        return value

    @staticmethod
    def _rate_limit_wait_seconds(message: Any) -> Optional[int]:
        """从“操作过于频繁，请 43 秒后再试”中解析等待秒数。"""
        text = str(message or "")
        if "操作过于频繁" not in text and "频繁" not in text:
            return None
        match = re.search(r"请\s*(\d+)\s*秒后再试", text)
        return int(match.group(1)) if match else 5

    def _wait_between_requests(self) -> None:
        minimum = min(self.request_delay_min, self.request_delay_max)
        maximum = max(self.request_delay_min, self.request_delay_max)
        time.sleep(random.uniform(minimum, maximum))

    def _is_relevant(self, title: str, summary: str, content: str = "") -> bool:
        corpus = f"{title} {summary} {content}".lower()
        return any(keyword.lower() in corpus for keyword in self.keywords)

    def _categorize(self, title: str, content: str) -> str:
        """自动分类：复用全局 CATEGORIES + 游戏行业关键词叠加（与全项目统一）。"""
        corpus = f"{title} {content}".lower()
        scores: Dict[str, int] = {}
        for category, rules in CATEGORIES.items():
            scores[category] = sum(1 for keyword in rules["keywords"] if keyword in corpus)
        if not scores:
            return "产品动态"
        best = max(scores, key=lambda k: scores[k])
        return best if scores[best] > 0 else "产品动态"

    def _extract_article_content(self, page: Any, url: str) -> str:
        """可视化浏览器访问命中详情页，并从常见正文容器抽取文字。"""
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(700)
        except Exception as exc:
            self.logger.warning("详情页打开失败：%s - %s", url, exc)
            return ""

        selectors = [
            ".article-content", ".article_content", ".article-body", ".article_body",
            ".content-detail", ".detail-content", ".post-content", ".post_content",
            ".news-content", ".news_content", ".content", "article", "main",
        ]
        best_content = ""
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                text = self._clean_text(locator.inner_text(timeout=4_000))
                if len(text) > len(best_content):
                    best_content = text
            except Exception:
                continue

        if len(best_content) < 80:
            try:
                body_text = self._clean_text(page.locator("body").inner_text(timeout=5_000))
                if len(body_text) > len(best_content):
                    best_content = body_text
            except Exception:
                pass

        self.logger.info("正文提取完成：%s 字 - %s", len(best_content), url)
        return best_content

    def _search_page(self, page: Any, keyword: str) -> List[Dict[str, Any]]:
        """访问站内搜索接口第一页；遇频率限制时等待后重试。"""
        api_url = self.SEARCH_API_URL.format(keyword=quote(keyword))

        for attempt in range(1, self.rate_limit_retries + 2):
            self._wait_between_requests()
            try:
                page.goto(api_url, wait_until="domcontentloaded", timeout=45_000)
                raw_text = page.locator("body").inner_text(timeout=10_000)
                payload = json.loads(raw_text)
            except Exception as exc:
                self.logger.warning(
                    "[%s] 搜索接口请求或解析失败（第 %s 次）：%s",
                    keyword, attempt, exc,
                )
                if attempt <= self.rate_limit_retries:
                    time.sleep(min(5 * attempt, 15))
                    continue
                return []

            if payload.get("code") == 200:
                data = payload.get("data") or {}
                results = data.get("list") or []
                return results if isinstance(results, list) else []

            message = payload.get("msg") or "未知错误"
            wait_seconds = self._rate_limit_wait_seconds(message)
            if wait_seconds is not None and attempt <= self.rate_limit_retries:
                self.logger.warning(
                    "[%s] 触发访问频率限制：%s；等待 %s 秒后重试（%s/%s）",
                    keyword,
                    message,
                    wait_seconds + 1,
                    attempt,
                    self.rate_limit_retries,
                )
                time.sleep(wait_seconds + 1)
                continue

            self.logger.warning("[%s] 搜索接口返回异常：%s", keyword, message)
            return []

        return []

    def _normalize_result(self, raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        title = self._clean_text(raw.get("subject"))
        summary = self._clean_text(raw.get("summary"))
        publish_time = self._parse_timestamp(raw.get("dateline"))
        if not title or not publish_time:
            return None

        try:
            is_external = int(raw.get("is_wailian") or 0) == 1
        except (TypeError, ValueError):
            is_external = False

        external_url = self._clean_text(raw.get("wailian"))
        path = self._clean_text(raw.get("url"))
        if is_external and external_url:
            url = external_url
        elif path:
            url = path if path.startswith("http") else f"{self.SITE_ROOT}{path}"
        else:
            return None

        return {
            "title": title,
            "summary": summary,
            "publish_time": publish_time,
            "url": url,
            "raw": self._make_json_safe(raw),
        }

    def fetch(self) -> List[NewsItem]:
        """执行关键词搜索、详情正文抓取与 NewsItem 构造。"""
        self.cutoff_time = datetime.now() - timedelta(hours=self.hours_window)
        self.logger.info("开始采集 %s - 关键词: %s", self.source_name, self.keywords)
        self.logger.info(
            "时间窗口: 最近 %s 小时；每个关键词仅抓取第一页",
            self.hours_window,
        )

        collected: List[NewsItem] = []
        seen_urls = set()
        seen_result_urls = set()

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "未安装 Playwright。请执行：pip install playwright && playwright install chromium"
            ) from exc

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            context = browser.new_context(viewport={"width": 1440, "height": 960})
            page = context.new_page()

            try:
                self.logger.info("打开 GameRes 搜索页")
                page.goto(self.SEARCH_PAGE_URL, wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_timeout(800)

                candidates: List[Dict[str, Any]] = []
                for keyword in self.keywords:
                    self.logger.info("搜索关键词: %s", keyword)
                    results = self._search_page(page, keyword)
                    if not results:
                        self.logger.info("[%s] 搜索无结果，跳过", keyword)
                        continue

                    filtered_count = 0
                    for raw in results:
                        normalized = self._normalize_result(raw)
                        if not normalized:
                            continue

                        publish_time = normalized["publish_time"]
                        if publish_time < self.cutoff_time:
                            continue

                        if not self._is_relevant(normalized["title"], normalized["summary"]):
                            continue

                        filtered_count += 1
                        if normalized["url"] in seen_result_urls:
                            continue
                        seen_result_urls.add(normalized["url"])
                        candidates.append(normalized)

                    self.logger.info(
                        "[%s] 第一页：接口 %s 条，时间窗口及关键词过滤后 %s 条",
                        keyword, len(results), filtered_count,
                    )

                for news in candidates:
                    url = news["url"]
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)

                    self.logger.info("获取正文: %s", news["title"][:60])
                    content = self._extract_article_content(page, url)
                    if not content:
                        content = news["summary"]

                    if not self._is_relevant(news["title"], news["summary"], content):
                        self.logger.info("正文二次校验未通过，跳过：%s", news["title"])
                        continue

                    try:
                        ai_summary, _ = self.generate_summary(news["title"], content)
                        summary = ai_summary or ""
                    except Exception as exc:
                        self.logger.warning("摘要生成失败：%s", exc)
                        summary = ""
                    if not summary:
                        summary = str(news["summary"] or content[:150])

                    date_text = news["publish_time"].strftime("%Y-%m-%d %H:%M:%S")
                    item = NewsItem(
                        title=news["title"],
                        date=date_text,
                        url=url,
                        source=self.source_name,
                        source_code=self.source_code,
                        credibility_tag=self.credibility_base,
                        category=self._categorize(news["title"], content),
                        summary=summary,
                        content=content,
                        raw_data={
                            "search_result": self._make_json_safe(news["raw"]),
                            "search_summary": news["summary"],
                            "publish_time": date_text,
                        },
                    )
                    collected.append(item)
                    self.logger.info("采集成功：%s", news["title"][:80])

            finally:
                context.close()
                browser.close()

        self.logger.info("%s 采集完成，共 %s 条", self.source_name, len(collected))
        return collected


def main() -> None:
    """运行 GameRes 采集器并输出结果统计。"""
    crawler = GameResCrawler()
    items = crawler.run()

    print(f"\n{'=' * 70}")
    print(f"{crawler.source_name}采集结果: {len(items)} 条")
    print(f"时间窗口: 过去{crawler.hours_window}小时")
    print(f"截止时间: {crawler.cutoff_time.strftime('%Y-%m-%d %H:%M')}")
    print(f"输出目录: {crawler._batch_dir or '由 BaseCrawler.run() 确定'}")
    print('=' * 70)

    for index, item in enumerate(items, 1):
        print(f"\n{'─' * 70}")
        print(f"{index}. [{item.category}] {item.title}")
        print(f"   时间: {item.date}")
        print(f"   链接: {item.url}")
        if item.summary:
            print(f"   摘要: {item.summary[:200]}...")


if __name__ == "__main__":
    main()
