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
import re

sys.path.append(str(Path(__file__).parent.parent.parent))

from collectors.base import BaseCrawler
from models.news import NewsItem
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

    def _get_detail_content(self, page, url: str, section_name: str) -> tuple[str, str, str]:
        """
        访问详情页获取内容、摘要和日期
        返回: (内容, 摘要, 日期)
        """
        if not url or not url.startswith('http'):
            return "", "", ""

        try:
            # 保存当前页面URL以便返回
            original_url = page.url

            # 访问详情页
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)

            # 提取内容和日期
            result = page.evaluate('''() => {
                // 提取日期 - 尝试多种方式
                let dateStr = '';
                const dateSelectors = [
                    'time[datetime]',
                    '.date',
                    '.publish-date',
                    '.release-date',
                    '[class*="date"]',
                    'meta[property="article:published_time"]',
                    'meta[name="publish-date"]',
                    'meta[name="date"]',
                ];

                for (const selector of dateSelectors) {
                    const el = document.querySelector(selector);
                    if (el) {
                        dateStr = el.getAttribute('datetime') ||
                                  el.getAttribute('content') ||
                                  el.textContent.trim();
                        if (dateStr) break;
                    }
                }

                // 从页面文本中找日期（备用）
                if (!dateStr) {
                    const text = document.body ? document.body.innerText : '';
                    const patterns = [
                        /(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日?/,
                        /(\d{4})[\/](\d{1,2})[\/](\d{1,2})/,
                        /(\d{1,2})[\/](\d{1,2})[\/](\d{4})/,
                    ];
                    for (const pattern of patterns) {
                        const match = text.match(pattern);
                        if (match) {
                            dateStr = match[0];
                            break;
                        }
                    }
                }

                // 提取内容
                let content = '';
                const contentSelectors = [
                    '.PressReleaseBody',
                    '.press-release-content',
                    '.release-content',
                    '[class*="PressRelease"]',
                    'article',
                    '.content-area',
                    '.main-content',
                    '#content',
                    '.announcement-content',
                    '.news-content',
                    '.press-content',
                    '.content',
                    '.post-content',
                    '[class*="content"]',
                    'main',
                    '.main',
                    'body'
                ];

                for (const selector of contentSelectors) {
                    const el = document.querySelector(selector);
                    if (el) {
                        const text = el.innerText || el.textContent;
                        if (text && text.trim().length > 50) {
                            content = text.trim();
                            break;
                        }
                    }
                }

                return { content, date: dateStr };
            }''')

            content = result.get('content', '')
            date_from_detail = result.get('date', '')

            # 清理内容
            content = self._clean_content(content)

            # 生成摘要
            summary = self._generate_summary(content)

            # 尝试返回原页面
            try:
                if original_url and original_url != url and original_url != 'about:blank':
                    page.goto(original_url, wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(3000)
            except Exception:
                pass

            return content, summary, date_from_detail

        except Exception as e:
            self.logger.debug(f"[{section_name}] 获取详情页失败 {url}: {e}")
            return "", "", ""

    def _clean_content(self, content: str) -> str:
        """清理提取的文本内容"""
        if not content:
            return ""

        # 移除多余空白
        content = ' '.join(content.split())

        # 移除常见的无关文本
        noise_phrases = [
            '閱讀更多', '阅读更多', '查看更多', '查看所有', '更多',
            'Read more', 'See All', 'Next', 'Previous',
            '返回', '首頁', '首页', 'www.kingsoft.com',
            '金山軟件有限公司', '金山软件有限公司',
            '跳到内容', '搜索', '菜单', '导航',
            'Cookie Policy', 'Privacy Policy', 'Terms of Use',
            '投资者关系', 'Investor Relations',
            # 社交媒体分享按钮
            'WhatsApp', 'Email', 'LinkedIn', 'Facebook', 'WeChat',
            'Print', '分享', '關閉', '关闭', 'X',
            # 附件和PDF
            '附件', '.PDF', 'HKEX-EPS',
            # 服务声明
            '由© 提供', '服務條款', '服务条款', 'Cookie 政策', 'Cookie Policy',
            '隱私政策', '隐私政策',
        ]

        for phrase in noise_phrases:
            content = content.replace(phrase, ' ')

        # 移除看起来像PDF文件名的内容 (如 HKEX-EPS_20260728_12259563_0)
        import re
        content = re.sub(r'HKEX-EPS_\d+_\d+_\d+', ' ', content)
        content = re.sub(r'\S+\.pdf\b', ' ', content, flags=re.IGNORECASE)

        # 移除多余空格
        content = ' '.join(content.split())

        return content.strip()

    def _generate_summary(self, content: str, max_length: int = 200) -> str:
        """从内容生成摘要"""
        if not content:
            return ""

        # 移除HTML标签（如果有）
        import re
        content = re.sub(r'<[^>]+>', '', content)

        # 清理多余空白
        content = ' '.join(content.split())

        # 截取前max_length个字符，尽量在完整句子处截断
        if len(content) <= max_length:
            return content

        # 尝试在句子结束处截断
        truncated = content[:max_length]
        sentence_end = max(
            truncated.rfind('。'),
            truncated.rfind('．'),
            truncated.rfind('. '),
            truncated.rfind('；'),
            truncated.rfind(';')
        )

        if sentence_end > max_length * 0.5:  # 如果找到合适的句子结束点（至少保留一半长度）
            return content[:sentence_end + 1]

        # 否则在单词边界截断
        word_boundary = truncated.rfind(' ')
        if word_boundary > max_length * 0.7:
            return content[:word_boundary] + '...'

        return content[:max_length] + '...'

    def _extract_date_from_content(self, text: str) -> str:
        """
        从内容文本中提取日期
        支持格式：
        - HKE-EPS_YYYYMMDD_xxxxx (PDF文件名)
        - 美式日期 M/D/YY 或 MM/DD/YYYY
        - 繁体中文日期 二零二五年十二月三十一日
        """
        if not text:
            return ""

        import re

        # 模式1：PDF文件名 HKE-EPS_20260728_12259563_0 或 HKE -EPS_20260612_12201116_0
        # 注意：实际文本中可能是 "HKE -EPS_20260612" 有空格
        match = re.search(r'HKE\s*-?\s*EPS[_\s-](\d{4})(\d{2})(\d{2})[_\s-]', text)
        if match:
            year, month, day = match.groups()
            return f"{year}-{month}-{day}"

        # 模式2：英文完整日期 Wednesday, 27 May 2026
        month_map = {
            'january': '01', 'february': '02', 'march': '03', 'april': '04',
            'may': '05', 'june': '06', 'july': '07', 'august': '08',
            'september': '09', 'october': '10', 'november': '11', 'december': '12',
            'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04',
            'jun': '06', 'jul': '07', 'aug': '08', 'sep': '09', 'oct': '10', 'nov': '11', 'dec': '12'
        }
        match = re.search(r'(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})', text, re.IGNORECASE)
        if match:
            day, month_str, year = match.groups()
            month = month_map.get(month_str.lower()[:3], '01')
            return f"{year}-{month}-{int(day):02d}"

        # 模式3：美式日期带时间 5/27/26 7:00 PM
        match = re.search(r'(\d{1,2})/(\d{1,2})/(\d{2,4})\s+\d{1,2}:\d{2}', text)
        if match:
            month, day, year = match.groups()
            if len(year) == 2:
                year = '20' + year if int(year) < 50 else '19' + year
            return f"{year}-{int(month):02d}-{int(day):02d}"

        # 模式4：标准日期格式 2025-12-31 或 2025/12/31
        match = re.search(r'(\d{4})[\-/](\d{1,2})[\-/](\d{1,2})', text)
        if match:
            year, month, day = match.groups()
            month_int = int(month)
            day_int = int(day)
            if 1 <= month_int <= 12 and 1 <= day_int <= 31:
                return f"{year}-{month_int:02d}-{day_int:02d}"

        # 模式5：美式日期 5/27/26 (无时间，最后尝试)
        match = re.search(r'(\d{1,2})/(\d{1,2})/(\d{2,4})', text)
        if match:
            month, day, year = match.groups()
            if len(year) == 2:
                year = '20' + year if int(year) < 50 else '19' + year
            return f"{year}-{int(month):02d}-{int(day):02d}"

        # 模式6：繁体中文日期
        match = re.search(r'([零一二三四五六七八九十]{4})年([一二三四五六七八九十]{1,2})月', text)
        if match:
            year_str, month_str = match.groups()
            chinese_nums = {'零': '0', '一': '1', '二': '2', '三': '3', '四': '4',
                           '五': '5', '六': '6', '七': '7', '八': '8', '九': '9'}
            year = ''.join(chinese_nums.get(c, c) for c in year_str)
            month_map_cn = {'一': '01', '二': '02', '三': '03', '四': '04', '五': '05', '六': '06',
                           '七': '07', '八': '08', '九': '09', '十': '10', '十一': '11', '十二': '12'}
            month = month_map_cn.get(month_str, '01')
            return f"{year}-{month}-01"

        return ""

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

                            const extractDateFromElement = (link) => {
                                // 方法1：从相邻的日期单元格找
                                const row = link.closest('tr, .row, [class*="row"], .PressRelease-SingleLine-DataColumn');
                                if (row) {
                                    const dateCell = row.querySelector('td:first-child, .date, [class*="date"]');
                                    if (dateCell) {
                                        return dateCell.textContent.trim();
                                    }
                                }

                                // 方法2：从父容器找
                                const container = link.closest('li, article, .item, div[class*="PressRelease"]');
                                if (container) {
                                    const dateEl = container.querySelector('.date, time, [class*="date"]');
                                    if (dateEl) {
                                        return dateEl.getAttribute('datetime') || dateEl.textContent.trim();
                                    }
                                }

                                // 方法3：从URL提取日期
                                const url = link.href || '';
                                const dateMatch = url.match(/(\d{4})-?(\d{2})-?(\d{2})/);
                                if (dateMatch) {
                                    return dateMatch[0];
                                }

                                return '';
                            };

                            links.forEach(link => {
                                const title = (link.textContent || '').trim().replace(/\s+/g, ' ');
                                const href = link.href || '';
                                if (!title || title.length < 4 || seen.has(href)) {
                                    return;
                                }

                                const date = extractDateFromElement(link);

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

                        # 获取详情页内容、摘要和日期
                        content, summary, date_from_detail = "", "", ""
                        if url:
                            self.logger.info(f"[{section_name}] 获取详情: {title[:40]}...")
                            content, summary, date_from_detail = self._get_detail_content(page, url, section_name)

                        # 如果没有获取到摘要，使用标题作为备选
                        if not summary:
                            summary = title

                        # 优先使用详情页的日期，如果为空则使用列表页的日期
                        list_date = news.get('time', '')
                        final_date = date_from_detail if date_from_detail else list_date

                        # 如果还是为空，尝试从内容中提取
                        if not final_date and (content or summary):
                            final_date = self._extract_date_from_content(content + ' ' + summary)

                        item = NewsItem(
                            title=title,
                            date=self._parse_time(final_date),
                            url=url,
                            source=self.source_name,
                            source_code=self.source_code,
                            credibility_tag=self.credibility_base,
                            category=self._auto_classify(title),
                            summary=summary,
                            content=content,
                            raw_data={
                                'section': section_name,
                                'section_url': section_url,
                            }
                        )
                        items.append(item)
                        self.logger.info(f"[{section_name}] 已采集: {title[:50]}")

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
        if item.summary:
            summary_display = item.summary[:150] + '...' if len(item.summary) > 150 else item.summary
            print(f"   摘要: {summary_display}")

if __name__ == "__main__":
    main()
