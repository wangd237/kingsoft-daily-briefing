"""GameRes 游资网采集器：仅采集金山游戏业务相关新闻。"""

import json
import os
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
from config.settings import DATA_DIR, LOG_DIR
from models.news import NewsItem


class GameResCrawler(BaseCrawler):
    """通过 GameRes 站内搜索采集金山游戏业务资讯。"""

    source_name = "GameRes游资网"
    source_code = "gameres"
    credibility_base = "【媒体报道】"

    SEARCH_PAGE_URL = "https://www.gameres.com/search"
    SEARCH_API_URL = "https://www.gameres.com/api/v1/portal/search?q={keyword}&page={page}&page_size={page_size}"
    SITE_ROOT = "https://www.gameres.com"

    GAME_KEYWORDS = [
        "西山居", "金山世游", "剑网3", "剑网 3", "剑网三",
        "剑侠情缘", "剑侠世界", "剑侠世界3", "解限机",
        "尘白禁区", "鹅鸭杀", "双生视界", "最终幻想14", "FF14",
    ]

    CATEGORY_RULES = {
        "资本动态": ["融资", "投资", "上市", "股权", "收购", "财报", "营收", "估值"],
        "市场合作": ["合作", "联动", "授权", "发行", "代理", "签约", "战略"],
        "产品动态": [
            "上线", "测试", "公测", "首发", "版本", "更新", "发布", "预约",
            "开服", "新游", "游戏", "资料片", "DLC", "赛季",
        ],
    }

    def __init__(
        self,
        output_dir: Optional[str] = None,
        log_dir: Optional[str] = None,
        enable_summary: bool = True,
        keywords: Optional[List[str]] = None,
        hours_window: Optional[int] = None,
        max_pages: Optional[int] = None,
        page_size: int = 20,
    ) -> None:
        super().__init__(
            output_dir=str(output_dir or DATA_DIR),
            log_dir=str(log_dir or LOG_DIR),
            enable_summary=enable_summary,
        )
        self.keywords = keywords or self.GAME_KEYWORDS
        self.hours_window = hours_window or int(os.getenv("GAMERES_HOURS_WINDOW", "168"))
        self.max_pages = max_pages or int(os.getenv("GAMERES_MAX_PAGES", "100"))
        self.page_size = page_size
        self.cutoff_time = datetime.now() - timedelta(hours=self.hours_window)
        self._batch_dir: Optional[str] = None
        self.request_delay_min = float(os.getenv("GAMERES_REQUEST_DELAY_MIN", "1.5"))
        self.request_delay_max = float(os.getenv("GAMERES_REQUEST_DELAY_MAX", "3.0"))
        self.rate_limit_retries = int(os.getenv("GAMERES_RATE_LIMIT_RETRIES", "3"))

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

    def _get_batch_dir_and_json_path(self) -> tuple[str, str]:
        """复用本采集器既有批次目录；未创建时按日期和批次名创建。"""
        if not self._batch_dir:
            now = datetime.now()
            date_dir = now.strftime("%Y/%m/%d")
            batch_name = now.strftime(f"{self.source_code}_%Y%m%d_%H%M%S")
            self._batch_dir = str(Path(self.output_dir) / self.source_code / date_dir / batch_name)

        batch_dir = self._batch_dir
        json_filename = f"{self.source_code}.json"
        return batch_dir, str(Path(batch_dir) / json_filename)

    def save(self) -> str:
        """仅覆盖 GameRes 保存流程：先转换 datetime，再写入 JSON。"""
        if not self.items:
            self.logger.warning("没有数据可保存")
            return ""

        batch_dir, json_path = self._get_batch_dir_and_json_path()
        Path(batch_dir).mkdir(parents=True, exist_ok=True)

        content_dir = Path(batch_dir) / "contents"
        content_dir.mkdir(parents=True, exist_ok=True)

        for index, item in enumerate(self.items):
            content = getattr(item, "content", "") or ""
            if not content.strip():
                continue

            content_filename = f"ann_{index:03d}.txt"
            content_path = content_dir / content_filename
            try:
                content_path.write_text(content, encoding="utf-8")
                item.content_ref = f"contents/{content_filename}"
                self.logger.debug("正文已保存: %s", content_path)
            except OSError as exc:
                self.logger.error("保存正文失败: %s", exc)
                item.content_ref = ""

        data = {
            "source": self.source_name,
            "source_code": self.source_code,
            "fetch_time": datetime.now().isoformat(),
            "count": len(self.items),
            "items": [self._make_json_safe(item.to_dict()) for item in self.items],
        }

        try:
            with open(json_path, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
        except OSError as exc:
            self.logger.error("保存 JSON 失败: %s", exc)
            return ""

        self.logger.info("数据已保存: %s (批次目录: %s)", json_path, batch_dir)
        return json_path

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
        corpus = f"{title} {content}".lower()
        for category, words in self.CATEGORY_RULES.items():
            if any(word.lower() in corpus for word in words):
                return category
        return "行业动态"

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

    def _search_page(self, page: Any, keyword: str, page_no: int) -> List[Dict[str, Any]]:
        """访问站内搜索接口；遇频率限制时等待后重试当前页。"""
        api_url = self.SEARCH_API_URL.format(
            keyword=quote(keyword), page=page_no, page_size=self.page_size
        )

        for attempt in range(1, self.rate_limit_retries + 2):
            self._wait_between_requests()
            try:
                page.goto(api_url, wait_until="domcontentloaded", timeout=45_000)
                raw_text = page.locator("body").inner_text(timeout=10_000)
                payload = json.loads(raw_text)
            except Exception as exc:
                self.logger.warning(
                    "[%s] 第 %s 页搜索接口请求或解析失败（第 %s 次）：%s",
                    keyword, page_no, attempt, exc,
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
                    "[%s] 第 %s 页触发访问频率限制：%s；等待 %s 秒后重试（%s/%s）",
                    keyword,
                    page_no,
                    message,
                    wait_seconds + 1,
                    attempt,
                    self.rate_limit_retries,
                )
                time.sleep(wait_seconds + 1)
                continue

            self.logger.warning("[%s] 第 %s 页接口返回异常：%s", keyword, page_no, message)
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
            "时间窗口: 最近 %s 小时；单关键词最大页数: %s；每页: %s 条",
            self.hours_window,
            self.max_pages,
            self.page_size,
        )

        now = datetime.now()
        date_dir = now.strftime("%Y/%m/%d")
        batch_name = now.strftime(f"{self.source_code}_%Y%m%d_%H%M%S")
        self._batch_dir = str(Path(self.output_dir) / self.source_code / date_dir / batch_name)
        Path(self._batch_dir).mkdir(parents=True, exist_ok=True)
        self.logger.info("本次输出目录: %s", self._batch_dir)

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
                    for page_no in range(1, self.max_pages + 1):
                        results = self._search_page(page, keyword, page_no)
                        if not results:
                            self.logger.info("[%s] 第 %s 页无结果，停止翻页", keyword, page_no)
                            break

                        filtered_count = 0
                        page_has_in_window = False
                        for raw in results:
                            normalized = self._normalize_result(raw)
                            if not normalized:
                                continue

                            publish_time = normalized["publish_time"]
                            if publish_time < self.cutoff_time:
                                continue

                            page_has_in_window = True
                            if not self._is_relevant(normalized["title"], normalized["summary"]):
                                continue

                            filtered_count += 1
                            if normalized["url"] in seen_result_urls:
                                continue
                            seen_result_urls.add(normalized["url"])
                            candidates.append(normalized)

                        self.logger.info(
                            "[%s] 第 %s 页：接口 %s 条，时间窗口及关键词过滤后 %s 条",
                            keyword, page_no, len(results), filtered_count,
                        )

                        if not page_has_in_window or len(results) < self.page_size:
                            break

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
                        summary = self.generate_summary(news["title"], content) or ""
                    except Exception as exc:
                        self.logger.warning("摘要生成失败：%s", exc)
                        summary = ""
                    if not summary:
                        summary = news["summary"] or content[:150]

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
    hours_window = int(os.getenv("GAMERES_HOURS_WINDOW", "168"))
    crawler = GameResCrawler(hours_window=hours_window)
    items = crawler.run()

    print(f"\n{'=' * 70}")
    print(f"{crawler.source_name}采集结果: {len(items)} 条")
    print(f"时间窗口: 过去{hours_window}小时")
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
