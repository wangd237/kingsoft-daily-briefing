# -*- coding: utf-8 -*-
"""
第一财经采集器
使用 Playwright + 页面解析
支持多关键词搜索、详情页正文抓取、AI 摘要
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from datetime import datetime
from typing import List, Dict, Set
from pathlib import Path
import time
import os
import re

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from collectors.base import BaseCrawler
from models.news import NewsItem
from config.settings import CATEGORIES, COLLECTORS


class YicaiCrawler(BaseCrawler):
    """第一财经采集器"""

    source_name = "第一财经"
    source_code = "yicai"
    credibility_base = "【媒体报道】"

    def __init__(self, enable_summary: bool = None):
        # 从配置读取参数
        config = COLLECTORS.get('yicai', {})
        self.keywords = config.get('keywords', ['金山办公'])
        self.max_items_per_keyword = 5

        # 从配置读取 enable_summary
        if enable_summary is None:
            enable_summary = config.get('enable_summary', True)

        super().__init__(enable_summary=enable_summary)

        self.base_url = "https://www.yicai.com"

        # 已抓取的URL集合
        self.seen_urls: Set[str] = set()

    def _auto_classify(self, title: str) -> str:
        """自动分类"""
        title_lower = title.lower()
        scores = {}
        for category, rules in CATEGORIES.items():
            score = sum(1 for kw in rules['keywords'] if kw in title_lower)
            scores[category] = score
        if scores and max(scores.values()) > 0:
            return max(scores, key=scores.get)
        return "资本动态"

    def _parse_time(self, time_str: str) -> str:
        """解析时间字符串"""
        if not time_str:
            return datetime.now().strftime('%Y-%m-%d')

        time_str = time_str.strip()

        # 匹配 YYYY-MM-DD HH:MM
        match = re.search(r'(\d{4})-(\d{2})-(\d{2})', time_str)
        if match:
            return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"

        # 匹配 YYYY年MM月DD日
        match = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', time_str)
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"

        return datetime.now().strftime('%Y-%m-%d')

    def _search_keyword(self, page, keyword: str) -> List[Dict]:
        """搜索单个关键词"""
        results = []

        try:
            # 访问搜索页面
            search_url = f"{self.base_url}/search?keywords={keyword}"
            self.logger.info(f"[{keyword}] 访问搜索页: {search_url}")

            page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            # 截图调试
            page.screenshot(path=f"output/logs/yicai_{keyword}_search.png")

            # 提取搜索结果
            items = page.evaluate("""() => {
                const data = [];

                // 尝试多种选择器
                const selectors = [
                    '.search-list .item',
                    '.search-result .item',
                    '.news-list li',
                    '.search-item',
                    '.item',
                ];

                let items = [];
                for (const selector of selectors) {
                    items = document.querySelectorAll(selector);
                    if (items.length > 0) break;
                }

                items.forEach(item => {
                    const titleEl = item.querySelector('h3 a, .title a, a');
                    const timeEl = item.querySelector('.time, .date, .pub-time');
                    const summaryEl = item.querySelector('.summary, .desc, p');

                    if (titleEl) {
                        const title = titleEl.textContent?.trim() || '';
                        const url = titleEl.href || '';
                        const time = timeEl?.textContent?.trim() || '';
                        const summary = summaryEl?.textContent?.trim() || '';

                        if (title && url) {
                            data.push({title, url, time, summary});
                        }
                    }
                });

                return {
                    items: data,
                    pageTitle: document.title,
                    itemCount: data.length
                };
            }""")

            self.logger.info(f"[{keyword}] 页面: {items.get('pageTitle')}")
            self.logger.info(f"[{keyword}] 找到 {items.get('itemCount')} 条")

            results = items.get('items', [])[:self.max_items_per_keyword]

        except Exception as e:
            self.logger.error(f"[{keyword}] 搜索失败: {e}")

        return results

    def _fetch_content(self, page, url: str) -> str:
        """获取详情页正文"""
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            # 提取正文
            content = page.evaluate("""() => {
                const selectors = [
                    '.article-content',
                    '.content-detail',
                    '.news-content',
                    '.main-content',
                    'article',
                ];

                for (const selector of selectors) {
                    const el = document.querySelector(selector);
                    if (el) {
                        const text = el.innerText?.trim();
                        if (text && text.length > 100) {
                            return text;
                        }
                    }
                }

                // 备用：提取所有段落
                const paragraphs = document.querySelectorAll('p');
                const texts = [];
                paragraphs.forEach(p => {
                    const text = p.innerText?.trim();
                    if (text && text.length > 20) {
                        texts.push(text);
                    }
                });

                return texts.join('\\n');
            }""")

            return content or ""

        except Exception as e:
            self.logger.error(f"获取详情页失败: {url} - {e}")
            return ""

    def fetch(self) -> List[NewsItem]:
        """采集数据"""
        all_items = []

        self.logger.info(f"开始采集第一财经 - 关键词: {self.keywords}")

        # 创建批次目录
        now = datetime.now()
        date_dir = now.strftime('%Y/%m/%d')
        batch_name = now.strftime(f'{self.source_code}_%Y%m%d_%H%M%S')
        self._batch_dir = f"{self.output_dir}/{self.source_code}/{date_dir}/{batch_name}"
        os.makedirs(self._batch_dir, exist_ok=True)

        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            page = context.new_page()

            try:
                for keyword in self.keywords:
                    self.logger.info(f"搜索关键词: {keyword}")

                    results = self._search_keyword(page, keyword)

                    for news in results:
                        url = news['url']

                        # 去重
                        if url in self.seen_urls:
                            continue
                        self.seen_urls.add(url)

                        self.logger.info(f"获取正文: {news['title'][:50]}...")
                        content = self._fetch_content(page, url)

                        # 创建 NewsItem
                        item = NewsItem(
                            title=news['title'],
                            date=self._parse_time(news['time']),
                            url=url,
                            source=self.source_name,
                            source_code=self.source_code,
                            credibility_tag=self.credibility_base,
                            category=self._auto_classify(news['title']),
                            summary=news['summary'] or news['title'][:150],
                            content=content,
                            raw_data={'keyword': keyword},
                        )

                        # AI 摘要
                        if content and len(content.strip()) > 50:
                            ai_summary, summary_time = self.generate_summary(news['title'], content)
                            if ai_summary:
                                item.summary = ai_summary
                                item.summary_generated_at = summary_time

                        all_items.append(item)
                        time.sleep(1)

                    time.sleep(2)

            finally:
                context.close()
                browser.close()

        self.logger.info(f"采集完成: 共 {len(all_items)} 条")
        return all_items


def main():
    """测试运行"""
    crawler = YicaiCrawler()
    items = crawler.run()

    print(f"\n{'='*70}")
    print(f"第一财经采集结果: {len(items)} 条")
    print('='*70)

    for i, item in enumerate(items, 1):
        print(f"\n{'─'*70}")
        print(f"{i}. [{item.category}] {item.title}")
        print(f"   时间: {item.date}")
        print(f"   链接: {item.url}")

        if item.summary:
            print(f"   AI摘要: {item.summary[:200]}...")


if __name__ == "__main__":
    main()
