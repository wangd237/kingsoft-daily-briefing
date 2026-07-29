# -*- coding: utf-8 -*-
"""
金山软件IR官网采集器
使用 Playwright 浏览器自动化
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from datetime import datetime
from typing import List
from playwright.sync_api import sync_playwright
import time

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))

from collectors.base import BaseCrawler, NewsItem
from config.settings import CATEGORIES


class KingsoftIRCrawler(BaseCrawler):
    """
    金山软件IR官网采集器
    采集新闻发布页面
    """

    source_name = "金山软件IR官网"
    source_code = "kingsoft_ir"
    credibility_base = "【官方公告】"

    def __init__(self):
        super().__init__()
        self.base_url = "https://ir.kingsoft.com"
        self.news_url = "https://ir.kingsoft.com/zh-hans/news-events/press-releases"

    def _auto_classify(self, title: str) -> str:
        """自动分类"""
        title_lower = title.lower()
        scores = {}
        for category, rules in CATEGORIES.items():
            score = sum(1 for kw in rules['keywords'] if kw in title_lower)
            scores[category] = score
        if scores and max(scores.values()) > 0:
            return max(scores, key=scores.get)
        return "①资本动态"

    def _parse_time(self, time_str: str) -> str:
        """解析时间"""
        if not time_str:
            return datetime.now().strftime('%Y-%m-%d')

        time_str = time_str.strip()
        formats = [
            '%Y-%m-%d',
            '%Y/%m/%d',
            '%B %d, %Y',
            '%b %d, %Y',
            '%Y年%m月%d日',
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(time_str, fmt)
                return dt.strftime('%Y-%m-%d')
            except:
                continue

        return time_str

    def fetch(self, max_pages: int = 1) -> List[NewsItem]:
        """
        采集新闻数据
        """
        items = []

        self.logger.info(f"开始采集金山软件IR官网")

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-web-security',
                    '--disable-features=IsolateOrigins,site-per-process',
                ]
            )

            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )

            page = context.new_page()

            try:
                self.logger.info(f"访问: {self.news_url}")
                page.goto(self.news_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(5000)

                # 提取新闻列表
                results = page.evaluate('''() => {
                    const data = [];

                    // 尝试多种可能的选择器
                    const selectors = [
                        '.news-list .news-item',
                        '.press-releases .item',
                        '.view-content .views-row',
                        'article',
                        '.news .item',
                        '[class*="news"] [class*="item"]',
                        '.content .list-item'
                    ];

                    let elements = [];
                    for (const sel of selectors) {
                        elements = document.querySelectorAll(sel);
                        if (elements.length > 0) {
                            console.log(`找到选择器: ${sel}, 数量: ${elements.length}`);
                            break;
                        }
                    }

                    // 如果没找到，尝试获取所有链接
                    if (elements.length === 0) {
                        const links = document.querySelectorAll('a[href*="news"], a[href*="press"]');
                        console.log(`备选方案找到链接: ${links.length}`);
                        links.forEach(link => {
                            if (link.textContent.trim().length > 10) {
                                data.push({
                                    title: link.textContent.trim(),
                                    url: link.href,
                                    time: '',
                                    summary: ''
                                });
                            }
                        });
                    } else {
                        elements.forEach(el => {
                            // 标题
                            const titleEl = el.querySelector('h2, h3, .title, a');
                            // 时间
                            const timeEl = el.querySelector('.date, .time, [class*="date"]');
                            // 链接
                            const linkEl = el.querySelector('a');

                            if (titleEl && titleEl.textContent.trim().length > 5) {
                                data.push({
                                    title: titleEl.textContent.trim(),
                                    url: linkEl ? linkEl.href : '',
                                    time: timeEl ? timeEl.textContent.trim() : '',
                                    summary: ''
                                });
                            }
                        });
                    }

                    return {
                        items: data,
                        pageTitle: document.title,
                        url: window.location.href
                    };
                }''')

                self.logger.info(f"页面标题: {results.get('pageTitle', 'N/A')}")
                self.logger.info(f"当前URL: {results.get('url', 'N/A')}")

                news_list = results.get('items', [])
                self.logger.info(f"找到 {len(news_list)} 条新闻")

                # 过滤和转换
                for news in news_list:
                    title = news.get('title', '')
                    url = news.get('url', '')

                    if not title or len(title) < 10:
                        continue

                    # 过滤导航
                    nav_words = ['首页', '关于我们', '联系我们', '更多', 'Read more']
                    if any(w in title for w in nav_words):
                        continue

                    item = NewsItem(
                        title=title,
                        date=self._parse_time(news.get('time', '')),
                        url=url,
                        source=self.source_name,
                        source_code=self.source_code,
                        credibility_tag=self.credibility_base,
                        category=self._auto_classify(title),
                        summary=news.get('summary', '')
                    )
                    items.append(item)

                # 截图用于调试
                page.screenshot(path=f"output/logs/kingsoft_ir_check.png")

            except Exception as e:
                self.logger.error(f"采集失败: {e}", exc_info=True)
            finally:
                context.close()
                browser.close()

        self.logger.info(f"采集完成: {len(items)} 条")
        return items


def main():
    """测试运行"""
    crawler = KingsoftIRCrawler()
    items = crawler.run()

    print(f"\n{'='*60}")
    print(f"金山软件IR官网采集结果: {len(items)} 条")
    print('='*60)

    for i, item in enumerate(items[:10], 1):
        print(f"\n{i}. [{item.category}] {item.title}")
        print(f"   时间: {item.date}")
        print(f"   链接: {item.url}")


if __name__ == "__main__":
    main()
