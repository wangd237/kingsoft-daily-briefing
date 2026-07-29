# -*- coding: utf-8 -*-
"""
金山软件IR官网采集器
使用 Playwright 浏览器自动化
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from datetime import datetime
from urllib.parse import urljoin
from typing import List
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.append(str(Path(__file__).parent.parent.parent))

from collectors.base import BaseCrawler, NewsItem
from config.settings import CATEGORIES


class KingsoftIRCrawler(BaseCrawler):
    """
    金山软件IR官网采集器
    采集新闻活动四个栏目：公告、新闻稿、投资者活动、网络直播
    """

    source_name = "金山软件IR官网"
    source_code = "kingsoft_ir"
    credibility_base = "【官方公告】"

    def __init__(self):
        super().__init__()
        self.base_url = "https://ir.kingsoft.com"
        self.sections = [
            {
                "name": "公告",
                "url": "https://ir.kingsoft.com/zh-hant/news-events/announcements",
                "limit": 5,
            },
            {
                "name": "新聞稿",
                "url": "https://ir.kingsoft.com/zh-hant/news-events/press-releases",
                "limit": 2,
            },
            {
                "name": "投資者活動",
                "url": "https://ir.kingsoft.com/zh-hant/news-events/event-calendar",
                "limit": 2,
            },
            {
                "name": "網絡直播",
                "url": "https://ir.kingsoft.com/zh-hant/news-events/webcasts",
                "limit": 2,
            },
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

    def _extract_items_from_context(self, context) -> dict:
        """从页面或 iframe 上下文中提取条目"""
        return context.evaluate('''() => {
            const data = [];
            const seen = new Set();

            const stopWords = [
                '閱讀更多', '阅读更多', '查看更多', '查看所有', '更多',
                'Read more', 'See All', 'Next', 'Previous', '上一頁', '下一頁', '上一页', '下一页',
                '<<', '>>', '返回', '首頁', '首页', 'www.kingsoft.com', '金山軟件有限公司'
            ];

            const containerSelectors = [
                'main article',
                'article',
                'tbody tr',
                'tr',
                'li',
                '.item',
                '.news-item',
                '.event-item',
                '.list-item',
                '[class*="item"]',
                '[class*="row"]'
            ];

            const extractDate = (text) => {
                if (!text) return '';
                const patterns = [
                    /\b\d{1,2}[\/-]\d{1,2}[\/-]\d{2,4}\b/,
                    /\b\d{4}[\/-]\d{1,2}[\/-]\d{1,2}\b/,
                    /\b[A-Za-z]+\s+\d{1,2},\s+\d{4}\b/,
                    /\b\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\b/
                ];

                for (const pattern of patterns) {
                    const match = text.match(pattern);
                    if (match) return match[0];
                }
                return '';
            };

            let containers = [];
            for (const selector of containerSelectors) {
                const found = Array.from(document.querySelectorAll(selector));
                const filtered = found.filter(el => el.querySelector('a[href]') || el.tagName.toLowerCase() === 'tr');
                if (filtered.length > 0) {
                    containers = filtered;
                    break;
                }
            }

            if (containers.length === 0) {
                containers = Array.from(document.querySelectorAll('a[href]'))
                    .map(link => link.closest('article, li, tr, .item, .news-item, .event-item, .list-item, [class*="item"], [class*="row"]') || link)
                    .filter((el, index, array) => array.indexOf(el) === index);
            }

            containers.forEach(container => {
                const links = Array.from(container.querySelectorAll('a[href]'));
                if (links.length === 0) {
                    return;
                }

                const link = links.find(item => {
                    const text = (item.textContent || '').trim().replace(/\s+/g, ' ');
                    if (!text || text.length < 4) return false;
                    return !stopWords.includes(text);
                }) || links[0];

                if (!link) {
                    return;
                }

                const title = (link.textContent || '').trim().replace(/\s+/g, ' ');
                if (!title || title.length < 4 || stopWords.includes(title)) {
                    return;
                }

                const url = link.href || '';
                if (!url || seen.has(url)) {
                    return;
                }

                const text = container.textContent || '';
                const date = extractDate(text);

                seen.add(url);
                data.push({
                    title,
                    url,
                    time: date,
                    summary: ''
                });
            });

            return {
                items: data,
                pageTitle: document.title,
                url: window.location.href,
                itemCount: data.length
            };
        }''')

    def _fetch_section(self, page, section_name: str, url: str, limit: int) -> List[dict]:
        """抓取单个栏目"""
        self.logger.info(f"[{section_name}] 访问: {url}")

        try:
            page.goto(url, wait_until="commit", timeout=60000)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(8000)

            if section_name == '公告':
                try:
                    target_frame_url = 'asia.tools.euroland.com/tools/pressreleases'
                    for _ in range(30):
                        if any(target_frame_url in frame.url for frame in page.frames):
                            break
                        page.wait_for_timeout(1000)
                except Exception:
                    pass

            contexts = [page] + list(page.frames[1:])
            best_result = {
                'items': [],
                'pageTitle': '',
                'url': url,
                'itemCount': 0,
            }

            for current_context in contexts:
                try:
                    if section_name == '公告':
                        result = current_context.evaluate('''() => {
                            const data = [];
                            const seen = new Set();
                            const links = Array.from(document.querySelectorAll('a[href*="GetPressRelease"]'));

                            const extractDate = (text) => {
                                if (!text) return '';
                                const patterns = [
                                    /\b\d{1,2}[\/-]\d{1,2}[\/-]\d{2,4}\b/,
                                    /\b\d{4}[\/-]\d{1,2}[\/-]\d{1,2}\b/
                                ];
                                for (const pattern of patterns) {
                                    const match = text.match(pattern);
                                    if (match) return match[0];
                                }
                                return '';
                            };

                            links.forEach(link => {
                                const title = (link.textContent || '').trim().replace(/\s+/g, ' ');
                                const href = link.href || '';
                                if (!title || title.length < 4 || seen.has(href)) {
                                    return;
                                }

                                const container = link.closest('.PressRelease-SingleLine-DataColumn, li, tr, article, div') || link.parentElement || document.body;
                                const date = extractDate(container.textContent || '');

                                seen.add(href);
                                data.push({
                                    title,
                                    url: href,
                                    time: date,
                                    summary: ''
                                });
                            });

                            return {
                                items: data,
                                pageTitle: document.title,
                                url: window.location.href,
                                itemCount: data.length
                            };
                        }''')
                    else:
                        result = self._extract_items_from_context(current_context)
                except Exception as e:
                    self.logger.debug(f"[{section_name}] 抽取上下文失败: {e}")
                    continue

                if len(result.get('items', [])) > len(best_result.get('items', [])):
                    best_result = result

            self.logger.info(f"[{section_name}] 页面标题: {best_result.get('pageTitle', 'N/A')}")
            self.logger.info(f"[{section_name}] 找到 {len(best_result.get('items', []))} 条原始数据")

            return best_result.get('items', [])[:limit]
        except Exception as e:
            self.logger.warning(f"[{section_name}] 页面访问异常: {e}")
            return []

    def fetch(self, max_pages: int = 1) -> List[NewsItem]:
        """
        采集新闻活动四个栏目数据
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
                seen_urls = set()

                for section in self.sections:
                    section_name = section['name']
                    section_url = section['url']
                    section_limit = section['limit']

                    section_items = self._fetch_section(page, section_name, section_url, section_limit)

                    for news in section_items:
                        title = news.get('title', '')
                        url = news.get('url', '')

                        if not title or len(title) < 4:
                            continue

                        if url and not url.startswith('http'):
                            url = urljoin(self.base_url, url)

                        dedup_key = url or f"{section_name}:{title}"
                        if dedup_key in seen_urls:
                            continue
                        seen_urls.add(dedup_key)

                        item = NewsItem(
                            title=title,
                            date=self._parse_time(news.get('time', '')),
                            url=url,
                            source=self.source_name,
                            source_code=self.source_code,
                            credibility_tag=self.credibility_base,
                            category=self._auto_classify(title),
                            summary=news.get('summary', '') or title,
                            raw_data={
                                'section': section_name,
                                'section_url': section_url,
                            }
                        )
                        items.append(item)

                try:
                    page.screenshot(path=f"output/logs/kingsoft_ir_check.png")
                except:
                    pass

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
        print(f"   日期: {item.date}")
        print(f"   链接: {item.url}")

if __name__ == "__main__":
    main()
