# -*- coding: utf-8 -*-
"""
西山居游戏官网新闻采集器
"""
import sys
import io
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from urllib.parse import urljoin

sys.path.append(str(Path(__file__).parent.parent.parent))

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from collectors.base import BaseCrawler
from models.news import NewsItem
from config.settings import COLLECTORS


class XiaoyouCrawler(BaseCrawler):
    """西山居游戏官网采集器。"""

    source_name = "西山居游戏官网"
    source_code = "xiaoyou"
    credibility_base = "【官方资讯】"

    LIST_URL = "https://games.xoyo.com/news"
    BASE_URL = "https://games.xoyo.com"

    def __init__(self, enable_summary: bool = None, hours_window: int = None):
        """
        初始化

        Args:
            enable_summary: 是否启用 AI 摘要
            hours_window: 时间窗口（小时），默认24小时
        """
        config = COLLECTORS.get('xiaoyou', {})

        # 时间窗口（默认24小时）
        self.hours_window = hours_window or config.get('hours_window', 24)
        self.cutoff_time = datetime.now() - timedelta(hours=self.hours_window)
        self.logger_info = f"时间窗口: 过去{self.hours_window}小时 ({self.cutoff_time.strftime('%Y-%m-%d %H:%M')} 至今)"

        # 从配置读取 enable_summary
        if enable_summary is None:
            enable_summary = config.get('enable_summary', True)

        super().__init__(enable_summary=enable_summary)

    @staticmethod
    def _clean_text(text: str) -> str:
        """清理文本空白。"""
        if not text:
            return ""
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _parse_date(date_text: str) -> Optional[datetime]:
        """解析西山居页面日期。"""
        if not date_text:
            return None

        date_text = re.sub(r"\s+", " ", date_text.strip())
        date_text = date_text.replace("发布于", "").replace("发布时间：", "").strip()

        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
            "%Y/%m/%d",
            "%Y年%m月%d日 %H:%M:%S",
            "%Y年%m月%d日 %H:%M",
            "%Y年%m月%d日",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(date_text, fmt)
            except ValueError:
                continue

        return None

    def _get_detail_content(self, page, url: str) -> str:
        """进入详情页提取正文。"""
        try:
            page.goto(url, wait_until="networkidle", timeout=60000)

            try:
                page.wait_for_selector(
                    'div[class*="content"] div[class*="article"][class*="root"]',
                    timeout=15000,
                )
            except PlaywrightTimeoutError:
                pass

            content_selectors = [
                'div[class*="content"] div[class*="article"][class*="root"]',
                'div[class*="article"][class*="root"]',
                'article[class*="content"]',
                'div[class*="article"]',
                'div[class*="content"]',
                "article",
                "main",
            ]

            for selector in content_selectors:
                try:
                    elements = page.locator(selector)
                    for index in range(elements.count()):
                        text = self._clean_text(elements.nth(index).inner_text())
                        if len(text) >= 30:
                            return text
                except Exception:
                    continue

            try:
                paragraphs = page.locator(
                    'div[class*="content"] p, main p, article p'
                ).all_inner_texts()
                content = "\n".join(
                    self._clean_text(item)
                    for item in paragraphs
                    if self._clean_text(item)
                )
                if len(content) >= 30:
                    return content
            except Exception:
                pass

            self.logger.warning("未提取到正文: %s", url)
            return ""

        except Exception as exc:
            self.logger.warning("获取详情页失败: %s，原因: %s", url, exc)
            return ""

    def _build_summary(self, title: str, content: str) -> tuple:
        """优先使用 AI 摘要，失败时回退至正文或标题。"""
        summary = ""
        summary_generated_at = None

        if content:
            try:
                summary, summary_generated_at = self.generate_summary(title, content)
            except Exception as exc:
                self.logger.warning("生成摘要失败: %s", exc)

        summary = self._clean_text(summary)
        if summary:
            return summary[:150], summary_generated_at

        fallback = self._clean_text(content)
        if fallback:
            return fallback[:150], None

        return title, None

    def fetch(self) -> List[NewsItem]:
        """采集新闻列表及详情正文。"""
        items: List[NewsItem] = []
        seen_urls = set()

        # 批次目录保护：支持调度器注入 BATCH_DIR
        if not self._batch_dir:
            now = datetime.now()
            date_dir = now.strftime('%Y/%m/%d')
            batch_name = now.strftime(f'{self.source_code}_%Y%m%d_%H%M%S')
            self._batch_dir = f"{self.output_dir}/{self.source_code}/{date_dir}/{batch_name}"
            os.makedirs(self._batch_dir, exist_ok=True)

        self.logger.info(
            "开始采集%s，%s", self.source_name, self.logger_info
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            list_page = context.new_page()
            detail_page = context.new_page()

            try:
                list_page.goto(self.LIST_URL, wait_until="networkidle", timeout=60000)
                list_page.wait_for_selector(
                    'a[href^="/content/news/?id="]', timeout=20000
                )

                links = list_page.locator('a[href^="/content/news/?id="]')
                total = links.count()
                self.logger.info("列表页发现新闻链接：%s 条", total)

                for index in range(total):
                    try:
                        link = links.nth(index)
                        href = link.get_attribute("href") or ""
                        url = urljoin(self.BASE_URL, href)

                        if not href or not url or url in seen_urls:
                            continue

                        title_locator = link.locator('[class*="newsTitle"]')
                        time_locator = link.locator('[class*="newsTime"]')
                        title = (
                            self._clean_text(title_locator.first.inner_text())
                            if title_locator.count() > 0
                            else ""
                        )
                        date_text = (
                            self._clean_text(time_locator.first.inner_text())
                            if time_locator.count() > 0
                            else ""
                        )

                        if not title or not date_text:
                            self.logger.warning(
                                "跳过缺少标题或日期的新闻：title=%r, date=%r",
                                title,
                                date_text,
                            )
                            continue

                        publish_time = self._parse_date(date_text)
                        if not publish_time:
                            self.logger.warning(
                                "跳过无法解析日期的新闻：%s，标题：%s",
                                date_text,
                                title,
                            )
                            continue

                        if publish_time < self.cutoff_time:
                            self.logger.info(
                                "跳过超出时间范围新闻：%s - %s",
                                publish_time.strftime("%Y-%m-%d"),
                                title,
                            )
                            continue

                        seen_urls.add(url)
                        self.logger.info(
                            "采集详情（%s）：%s - %s",
                            len(items) + 1,
                            publish_time.strftime("%Y-%m-%d"),
                            title,
                        )

                        content = self._get_detail_content(detail_page, url)
                        summary, summary_generated_at = self._build_summary(title, content)

                        items.append(
                            NewsItem(
                                title=title,
                                date=publish_time.strftime("%Y-%m-%d"),
                                url=url,
                                source=self.source_name,
                                source_code=self.source_code,
                                credibility_tag=self.credibility_base,
                                category="产品动态",
                                publish_time=publish_time,
                                summary=summary,
                                summary_generated_at=summary_generated_at,
                                content=content,
                                raw_data={
                                    "list_url": self.LIST_URL,
                                    "original_date": date_text,
                                    "index": index,
                                },
                            )
                        )
                        time.sleep(0.5)

                    except Exception as exc:
                        self.logger.warning("处理第 %s 条新闻失败: %s", index + 1, exc)

            finally:
                context.close()
                browser.close()

        self.logger.info("西山居游戏官网新闻采集完成，共 %s 条", len(items))
        return items


def main():
    """测试运行"""
    import os

    hours_window = int(os.getenv('XIAOYOU_HOURS_WINDOW', '48'))
    crawler = XiaoyouCrawler(hours_window=hours_window)
    items = crawler.run()

    print(f"\n{'='*60}")
    print(f"{crawler.source_name}采集结果: {len(items)} 条")
    print(f"时间窗口: 过去{hours_window}小时")
    print('='*60)

    for i, item in enumerate(items, 1):
        print(f"\n{i}. [{item.category}] {item.title}")
        print(f"   日期: {item.date}")
        print(f"   链接: {item.url}")


if __name__ == "__main__":
    main()
