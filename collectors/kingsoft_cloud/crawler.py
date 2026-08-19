# -*- coding: utf-8 -*-
"""
金山云官网新闻采集器
列表页：https://www.ksyun.com/ns/news/p_1_10

摘要策略：
1. 优先使用 AI 摘要；
2. AI 未配置或生成失败时，使用清洗、截断后的列表摘要；
3. 列表摘要为空时，使用清洗后的正文前 150 字；
4. 正文为空时，回退为标题。
"""

import io
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

sys.path.append(str(Path(__file__).parent.parent.parent))

from collectors.base import BaseCrawler
from config.settings import CATEGORIES
from models.news import NewsItem


class KsyunCrawler(BaseCrawler):
    """金山云官网新闻采集器。"""

    source_name = "金山云官网"
    source_code = "ksyun"
    credibility_base = "【官网资讯】"

    def __init__(self, hours_window: int = 720, enable_summary: bool = True):
        super().__init__(enable_summary=enable_summary)
        self.hours_window = hours_window

        try:
            self.hours_window = int(
                os.getenv("KSYUN_HOURS_WINDOW", str(hours_window))
            )
        except ValueError:
            self.hours_window = hours_window

        self.cutoff_time = datetime.now() - timedelta(
            hours=self.hours_window
        )
        self.logger_info = (
            f"时间窗口：过去 {self.hours_window} 小时 "
            f"({self.cutoff_time.strftime('%Y-%m-%d %H:%M')} 至今)"
        )

        self.base_url = "https://www.ksyun.com"
        self.list_url = "https://www.ksyun.com/ns/news/p_1_10"
        self.seen_urls: Set[str] = set()

    def _auto_classify(self, title: str) -> str:
        """按配置关键词分类，未命中时归入产品动态。"""
        title_lower = (title or "").lower()
        scores = {}

        for category, rules in CATEGORIES.items():
            keywords = rules.get("keywords", [])
            scores[category] = sum(
                1 for keyword in keywords if keyword.lower() in title_lower
            )

        if scores and max(scores.values()) > 0:
            return max(scores, key=scores.get)

        return "产品动态"

    @staticmethod
    def _parse_datetime(time_text: str) -> Optional[datetime]:
        """解析日期；无法解析时返回 None。"""
        if not time_text:
            return None

        text = time_text.strip()
        patterns = [
            r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})",
            r"(\d{4})年(\d{1,2})月(\d{1,2})日?",
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    year, month, day = map(int, match.groups())
                    return datetime(year, month, day)
                except ValueError:
                    return None

        return None

    def _is_in_time_window(self, time_text: str) -> bool:
        """仅保留可解析且位于时间窗口内的新闻。"""
        published_time = self._parse_datetime(time_text)
        return published_time is not None and published_time >= self.cutoff_time

    @staticmethod
    def _clean_summary_text(text: str, max_length: int = 150) -> str:
        """清洗摘要文本，并在语句边界尽量截断。"""
        text = re.sub(r"\s+", " ", text or "").strip()
        if not text:
            return ""

        if len(text) <= max_length:
            return text

        truncated = text[:max_length]
        punctuation_positions = [
            truncated.rfind("。"),
            truncated.rfind("！"),
            truncated.rfind("？"),
            truncated.rfind("；"),
            truncated.rfind("."),
            truncated.rfind("!"),
            truncated.rfind("?"),
            truncated.rfind(";"),
        ]
        last_punctuation = max(punctuation_positions)

        if last_punctuation >= max_length // 2:
            return truncated[: last_punctuation + 1].strip()

        return truncated.rstrip("，,、:：；;。.!！？? ") + "…"

    def _build_fallback_summary(self, news: Dict, content: str) -> str:
        """构建 AI 摘要不可用时的本地回退摘要。"""
        list_summary = self._clean_summary_text(news.get("summary", ""), 150)
        if list_summary:
            return list_summary

        content_summary = self._clean_summary_text(content, 150)
        if content_summary:
            return content_summary

        return (news.get("title") or "金山云官网新闻").strip()

    def _generate_ai_summary(
        self,
        title: str,
        content: str,
    ) -> Tuple[str, Optional[object]]:
        """兼容基类返回“摘要”或“(摘要, 时间)”两种形式。"""
        if not content or len(content.strip()) < 50:
            return "", None

        try:
            result = self.generate_summary(title, content)

            if isinstance(result, tuple):
                summary = result[0] if len(result) > 0 else ""
                generated_at = result[1] if len(result) > 1 else None
            else:
                summary = result
                generated_at = None

            return (str(summary).strip() if summary else ""), generated_at

        except Exception as exc:
            self.logger.warning("AI 摘要生成失败，使用本地回退摘要：%s", exc)
            return "", None

    def _extract_list_items(self, page) -> List[Dict]:
        """从新闻列表页 DOM 提取新闻。"""
        self.logger.info("读取列表页：%s", self.list_url)

        try:
            page.goto(
                self.list_url,
                wait_until="domcontentloaded",
                timeout=30000,
            )
            page.wait_for_selector(
                "ul.news-list li.news-item[data-link]",
                timeout=15000,
            )
            page.wait_for_timeout(1000)
        except Exception as exc:
            self.logger.error("访问或加载金山云新闻列表页失败：%s", exc)
            return []

        items = page.evaluate(
            """() => {
                return Array.from(
                    document.querySelectorAll(
                        'ul.news-list li.news-item[data-link]'
                    )
                ).map((node) => {
                    const titleNode = node.querySelector('h3.item-tit');
                    const summaryNode = node.querySelector('p.item-con');
                    const dateNode = node.querySelector(
                        '.r-data span, .right-no-summary span'
                    );

                    return {
                        title: (titleNode?.innerText || titleNode?.textContent || '')
                            .trim()
                            .replace(/\\s+/g, ' '),
                        summary: (
                            summaryNode?.innerText ||
                            summaryNode?.textContent ||
                            ''
                        )
                            .trim()
                            .replace(/\\s+/g, ' '),
                        time: (dateNode?.innerText || dateNode?.textContent || '')
                            .trim(),
                        link: (node.getAttribute('data-link') || '').trim(),
                        id: (node.getAttribute('data-id') || '').trim()
                    };
                });
            }"""
        )

        valid_items: List[Dict] = []

        for item in items:
            title = (item.get("title") or "").strip()
            link = (item.get("link") or "").strip()
            time_text = (item.get("time") or "").strip()
            summary = (item.get("summary") or "").strip()

            if not title or not link or not time_text:
                continue

            if not link.startswith("/ns/news/detail/"):
                continue

            published_time = self._parse_datetime(time_text)
            if published_time is None:
                self.logger.warning(
                    "跳过日期无法解析的新闻：%s，日期文本：%s",
                    title,
                    time_text,
                )
                continue

            valid_items.append(
                {
                    "title": title,
                    "url": urljoin(self.base_url, link),
                    "time": time_text,
                    "summary": summary,
                    "id": item.get("id", ""),
                }
            )

        self.logger.info("列表页提取到有效新闻：%s 条", len(valid_items))
        return valid_items

    def _fetch_content(self, page, url: str) -> str:
        """访问详情页并提取正文。"""
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1000)

            content = page.evaluate(
                """() => {
                    const selectors = [
                        '.article-content',
                        '.news-content',
                        '.detail-content',
                        '.content-detail',
                        '.article-detail',
                        '.news-detail',
                        'article'
                    ];

                    for (const selector of selectors) {
                        const node = document.querySelector(selector);
                        if (!node) continue;

                        const text = (node.innerText || node.textContent || '')
                            .trim()
                            .replace(/\\n{3,}/g, '\\n\\n');

                        if (text.length >= 50) return text;
                    }

                    const paragraphs = Array.from(document.querySelectorAll('p'))
                        .map((p) => (p.innerText || p.textContent || '').trim())
                        .filter((text) => text.length >= 15);

                    return paragraphs.join('\\n');
                }"""
            )

            return (content or "").strip()

        except Exception as exc:
            self.logger.error("详情页抓取失败：%s，原因：%s", url, exc)
            return ""

    def fetch(self) -> List[NewsItem]:
        """采集金山云官网近期新闻。"""
        all_items: List[NewsItem] = []

        self.logger.info("开始采集：金山云官网")
        self.logger.info(self.logger_info)

        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()

            try:
                candidates = self._extract_list_items(page)

                for news in candidates:
                    url = news["url"]

                    if url in self.seen_urls:
                        continue
                    self.seen_urls.add(url)

                    if not self._is_in_time_window(news["time"]):
                        self.logger.info(
                            "时间过滤：%s（%s）",
                            news["title"],
                            news["time"],
                        )
                        continue

                    self.logger.info("抓取详情：%s", news["title"])
                    content = self._fetch_content(page, url)

                    published_time = self._parse_datetime(news["time"])
                    if published_time is None:
                        continue

                    fallback_summary = self._build_fallback_summary(news, content)
                    ai_summary, summary_time = self._generate_ai_summary(
                        news["title"],
                        content,
                    )
                    summary = ai_summary or fallback_summary

                    item = NewsItem(
                        title=news["title"],
                        date=published_time.strftime("%Y-%m-%d"),
                        url=url,
                        source=self.source_name,
                        source_code=self.source_code,
                        credibility_tag=self.credibility_base,
                        category=self._auto_classify(news["title"]),
                        summary=summary,
                        content=content,
                        raw_data={
                            "published_time_raw": news["time"],
                            "list_url": self.list_url,
                            "news_id": news.get("id", ""),
                        },
                    )

                    if ai_summary and summary_time:
                        item.summary_generated_at = summary_time

                    all_items.append(item)
                    time.sleep(0.6)

            finally:
                context.close()
                browser.close()

        self.logger.info("金山云采集完成，共保留 %s 条新闻", len(all_items))
        return all_items


def main():
    """模块运行入口。"""
    crawler = KsyunCrawler(enable_summary=True)
    items = crawler.run()

    crawler.logger.info(
        "金山云官网采集任务结束：%s 条，时间窗口：过去 %s 小时",
        len(items),
        crawler.hours_window,
    )


if __name__ == "__main__":
    main()
