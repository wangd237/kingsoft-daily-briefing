# -*- coding: utf-8 -*-
"""
金山办公投资者关系官网采集器
使用 Playwright 浏览器自动化
采集新闻和公告栏目
"""
import io
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List

from playwright.sync_api import sync_playwright

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.append(str(Path(__file__).parent.parent.parent))

from collectors.base import BaseCrawler
from models.news import NewsItem
from config.settings import CATEGORIES


class KingsoftOfficeIRCrawler(BaseCrawler):
    """
    金山办公投资者关系官网采集器
    采集新闻和公告两个栏目
    """

    source_name = "金山办公IR官网"
    source_code = "kingsoft_office_ir"
    credibility_base = "【官方公告】"

    def __init__(self):
        super().__init__()
        self.base_url = "https://www.wps.cn/KINGSOFT"
        # 尝试多个URL模式
        self.news_urls = [
            "https://www.wps.cn/KINGSOFT/ir/news",
            "https://www.wps.cn/KINGSOFT/investor/news",
            "http://www.wps.cn/KINGSOFT/ir/news",
            "https://ir.wps.cn/news",
        ]
        self.announcements_urls = [
            "https://www.wps.cn/KINGSOFT/ir/announcements",
            "https://www.wps.cn/KINGSOFT/investor/announcements",
            "http://www.wps.cn/KINGSOFT/ir/announcements",
            "https://ir.wps.cn/announcements",
        ]

    def _auto_classify(self, title: str) -> str:
        """自动分类"""
        title_lower = title.lower()
        scores: dict[str, int] = {}
        for category, rules in CATEGORIES.items():
            score = sum(1 for kw in rules['keywords'] if kw in title_lower)
            scores[category] = score
        if scores and max(scores.values()) > 0:
            best_category = max(scores.items(), key=lambda x: x[1])[0]
            return best_category
        return '资本动态'

    def _fetch_from_page(self, page, urls: list, section_name: str) -> List[dict]:
        """从指定页面提取数据，支持多URL尝试"""
        last_error = None

        for url in urls:
            try:
                self.logger.info(f"[{section_name}] 尝试访问: {url}")
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(5000)

                # 检查页面是否正常加载
                title = page.title()
                self.logger.info(f"[{section_name}] 页面标题: {title}")

                if title and title != "about:blank":
                    break  # 成功加载
            except Exception as e:
                last_error = e
                self.logger.warning(f"[{section_name}] 访问失败: {e}")
                continue
        else:
            self.logger.error(f"[{section_name}] 所有URL都失败")
            return []

        # 提取新闻/公告列表
        results = page.evaluate('''() => {
            const data = [];

            // 尝试多种可能的选择器
            const selectors = [
                // 新闻列表常见选择器
                '.news-list .news-item',
                '.news-list .item',
                '.press-list .item',
                '.article-list .article-item',
                '.list-item',
                // 表格形式
                'table tbody tr',
                // 通用
                'article',
                '.content .item',
                '[class*="news"] [class*="item"]',
                '[class*="list"] > div',
            ];

            let elements = [];
            let matchedSelector = '';

            for (const sel of selectors) {
                elements = document.querySelectorAll(sel);
                if (elements.length > 0) {
                    matchedSelector = sel;
                    console.log(`找到选择器: ${sel}, 数量: ${elements.length}`);
                    break;
                }
            }

            // 如果没找到，尝试获取所有包含链接的列表项
            if (elements.length === 0) {
                const allLinks = document.querySelectorAll('a');
                const linkParents = new Set();
                allLinks.forEach(link => {
                    if (link.textContent.trim().length > 10) {
                        const parent = link.closest('li, .item, [class*="item"], tr');
                        if (parent) linkParents.add(parent);
                    }
                });
                elements = Array.from(linkParents);
                matchedSelector = 'link-parents';
                console.log(`备选方案找到: ${elements.length}`);
            }

            elements.forEach(el => {
                // 标题 - 尝试多种方式
                let titleEl = el.querySelector('h1, h2, h3, h4, .title, [class*="title"]');
                if (!titleEl) {
                    titleEl = el.querySelector('a');
                }

                // 时间 - 尝试多种方式
                let timeEl = el.querySelector('.date, .time, [class*="date"], [class*="time"]');
                if (!timeEl) {
                    // 尝试在文本中找日期
                    const text = el.textContent;
                    const dateMatch = text.match(/(\d{4}[-/]\d{1,2}[-/]\d{1,2})/);
                    if (dateMatch) {
                        timeEl = { textContent: dateMatch[1] };
                    }
                }

                // 链接
                let linkEl = el.querySelector('a');
                let url = '';
                if (linkEl) {
                    url = linkEl.href;
                }

                // 摘要
                let summaryEl = el.querySelector('.summary, .desc, .description, [class*="summary"], [class*="desc"]');

                if (titleEl) {
                    const title = titleEl.textContent.trim();
                    if (title.length > 5 && title.length < 200) {
                        data.push({
                            title: title,
                            url: url,
                            time: timeEl ? timeEl.textContent.trim() : '',
                            summary: summaryEl ? summaryEl.textContent.trim() : ''
                        });
                    }
                }
            });

            return {
                items: data,
                pageTitle: document.title,
                url: window.location.href,
                matchedSelector: matchedSelector,
                elementCount: elements.length
            };
        }''')

        self.logger.info(f"[{section_name}] 页面标题: {results.get('pageTitle', 'N/A')}")
        self.logger.info(f"[{section_name}] 匹配选择器: {results.get('matchedSelector', 'N/A')}")
        self.logger.info(f"[{section_name}] 元素数量: {results.get('elementCount', 0)}")
        self.logger.info(f"[{section_name}] 找到 {len(results.get('items', []))} 条原始数据")

        return results.get('items', [])

    def fetch(self, max_pages: int = 1) -> List[NewsItem]:
        """
        采集新闻和公告数据
        """
        items = []

        self.logger.info(f"开始采集金山办公IR官网")

        with sync_playwright() as p:
            # 启动浏览器，忽略HTTPS证书错误
            browser = p.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-web-security',
                    '--disable-features=IsolateOrigins,site-per-process',
                    '--ignore-certificate-errors',
                    '--ignore-ssl-errors',
                ]
            )

            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                ignore_https_errors=True,  # 忽略HTTPS错误
            )

            page = context.new_page()

            try:
                # 采集新闻栏目
                self.logger.info("=" * 60)
                self.logger.info("开始采集【新闻】栏目")
                self.logger.info("=" * 60)
                news_items = self._fetch_from_page(page, self.news_urls, "新闻")

                # 采集公告栏目
                self.logger.info("=" * 60)
                self.logger.info("开始采集【公告】栏目")
                self.logger.info("=" * 60)
                announcement_items = self._fetch_from_page(page, self.announcements_urls, "公告")

                # 合并数据
                all_raw_items = []

                for news in news_items:
                    news['section'] = '新闻'
                    all_raw_items.append(news)

                for ann in announcement_items:
                    ann['section'] = '公告'
                    all_raw_items.append(ann)

                self.logger.info(f"原始数据总计: {len(all_raw_items)} 条")

                # 过滤和转换
                seen_titles = set()
                nav_words = ['首页', '关于我们', '联系我们', '更多', 'Read more', '下一页', '上一页', '>>', '<<']

                for news in all_raw_items:
                    title = news.get('title', '')
                    url = news.get('url', '')
                    section = news.get('section', '')

                    if not title or len(title) < 10:
                        continue

                    # 去重
                    if title in seen_titles:
                        continue
                    seen_titles.add(title)

                    # 过滤导航
                    if any(w in title for w in nav_words):
                        continue

                    # 确保URL完整
                    if url and not url.startswith('http'):
                        url = self.base_url + url

                    item = NewsItem(
                        title=f"[{section}] {title}" if section else title,
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
                try:
                    page.goto(self.news_urls[0], wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(3000)
                    page.screenshot(path=f"output/logs/kingsoft_office_ir_news.png")
                except:
                    pass

                try:
                    page.goto(self.announcements_urls[0], wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(3000)
                    page.screenshot(path=f"output/logs/kingsoft_office_ir_announcements.png")
                except:
                    pass

                self.logger.info("截图已保存")

            except Exception as e:
                self.logger.error(f"采集失败: {e}", exc_info=True)
            finally:
                context.close()
                browser.close()

        self.logger.info(f"采集完成: {len(items)} 条")
        return items


def main():
    """测试运行"""
    crawler = KingsoftOfficeIRCrawler()
    items = crawler.run()

    print(f"\n{'='*60}")
    print(f"金山办公IR官网采集结果: {len(items)} 条")
    print('='*60)

    for i, item in enumerate(items[:15], 1):
        print(f"\n{i}. [{item.category}] {item.title}")
        print(f"   时间: {item.date}")
        print(f"   链接: {item.url}")


if __name__ == "__main__":
    main()
