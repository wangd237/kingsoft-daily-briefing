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
from config.settings import CATEGORIES, COLLECTORS


class KingsoftIRCrawler(BaseCrawler):
    """
    金山软件IR官网采集器
    采集新闻活动三个栏目：公告、新闻稿、投资者活动
    """

    source_name = "金山软件IR官网"
    source_code = "kingsoft_ir"
    credibility_base = "【官方公告】"

    def __init__(self, enable_summary: bool = None):
        # 从配置读取 enable_summary
        config = COLLECTORS.get('kingsoft_ir', {})
        if enable_summary is None:
            enable_summary = config.get('enable_summary', False)

        # 初始化 PDF 处理器（用于下载公告附件）
        try:
            from models.pdf_processor import PDFProcessor
            self.pdf_processor = PDFProcessor()
        except Exception as e:
            self.logger.error(f"PDF 处理器初始化失败: {e}")
            self.pdf_processor = None

        super().__init__(enable_summary=enable_summary)
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

        # 模式4b：中文日期 2026年5月27日 或 2026 年 3 月31 日（阿拉伯数字，容忍空格）
        match = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', text)
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
        """
        抓取单个栏目
        不同栏目采用不同策略：
        - 公告：列表获取标题和详情链接，后续进入详情页提取HKEX-EPS PDF
        - 新闻稿：列表直接提取PDF链接（<a type="application/pdf">）
        - 投资者活动：列表获取详情链接，后续在详情页找PDF
        """
        self.logger.info(f"[{section_name}] 访问: {url}")

        try:
            page.goto(url, wait_until="commit", timeout=60000)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(8000)

            # 公告栏目：等待 iframe 加载
            if section_name == '公告':
                try:
                    target_frame_url = 'asia.tools.euroland.com/tools/pressreleases'
                    for _ in range(30):
                        if any(target_frame_url in frame.url for frame in page.frames):
                            break
                        page.wait_for_timeout(1000)
                except Exception:
                    pass

            # 根据不同栏目采用不同提取策略
            if section_name == '公告':
                return self._fetch_announcements(page, limit)
            elif section_name == '新聞稿':
                return self._fetch_press_releases(page, limit)
            elif section_name == '投資者活動':
                return self._fetch_investor_events(page, limit)
            else:
                return []

        except Exception as e:
            self.logger.warning(f"[{section_name}] 页面访问异常: {e}")
            return []

    def _fetch_announcements(self, page, limit: int) -> List[dict]:
        """
        公告栏目：从 iframe 中提取标题和详情链接
        列表仅获取标题、详情 ID 链接
        """
        contexts = [page] + list(page.frames[1:])
        best_result = {'items': [], 'pageTitle': '', 'url': '', 'itemCount': 0}

        for current_context in contexts:
            try:
                result = current_context.evaluate('''() => {
                    const data = [];
                    const seen = new Set();
                    const links = Array.from(document.querySelectorAll('a[href*="GetPressRelease"]'));

                    const extractDateFromElement = (link) => {
                        const row = link.closest('tr, .row, [class*="row"], .PressRelease-SingleLine-DataColumn');
                        if (row) {
                            const dateCell = row.querySelector('td:first-child, .date, [class*="date"]');
                            if (dateCell) {
                                return dateCell.textContent.trim();
                            }
                        }
                        const container = link.closest('li, article, .item, div[class*="PressRelease"]');
                        if (container) {
                            const dateEl = container.querySelector('.date, time, [class*="date"]');
                            if (dateEl) {
                                return dateEl.getAttribute('datetime') || dateEl.textContent.trim();
                            }
                        }
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
                        data.push({title, url: href, time: date, summary: '', type: 'announcement'});
                    });

                    return {items: data, pageTitle: document.title, url: window.location.href, itemCount: data.length};
                }''')

                if result and len(result.get('items', [])) > len(best_result.get('items', [])):
                    best_result = result
            except Exception as e:
                self.logger.debug(f"[公告] 提取失败: {e}")

        self.logger.info(f"[公告] 找到 {len(best_result.get('items', []))} 条公告")
        return best_result.get('items', [])[:limit]

    def _fetch_press_releases(self, page, limit: int) -> List[dict]:
        """
        新闻稿栏目：列表 DOM 直接筛选 <a type="application/pdf">
        直接提取PDF链接，并从列表容器中提取日期
        """
        try:
            result = page.evaluate('''() => {
                const data = [];
                const seen = new Set();

                const extractDate = (text) => {
                    if (!text) return '';
                    const patterns = [
                        /\\b(\\d{1,2})[\\/\\-](\\d{1,2})[\\/\\-](\\d{2,4})\\b/,
                        /\\b(\\d{4})[\\/\\-](\\d{1,2})[\\/\\-](\\d{1,2})\\b/,
                        /\\b(\\d{1,2})\\s+(January|February|March|April|May|June|July|August|September|October|November|December)[a-z]*\\s+(\\d{4})\\b/i,
                        /(\\d{4})年(\\d{1,2})月(\\d{1,2})日/
                    ];
                    for (const p of patterns) {
                        const m = text.match(p);
                        if (m) return m[0];
                    }
                    return '';
                };

                // 查找所有PDF链接（type="application/pdf"）
                const pdfLinks = Array.from(document.querySelectorAll('a[type="application/pdf"], a[href$=".pdf"], a[href$=".PDF"]'));

                pdfLinks.forEach(link => {
                    const title = (link.textContent || '').trim().replace(/\\s+/g, ' ');
                    let href = link.href || link.getAttribute('href') || '';

                    if (!title || title.length < 4 || seen.has(href)) {
                        return;
                    }

                    // 从父容器中提取日期
                    const container = link.closest('article, li, tr, .item, .news-item, .press-release, [class*="item"], [class*="row"], div');
                    let date = '';
                    if (container) {
                        // 优先查找带 date 类的元素
                        const dateEl = container.querySelector('.date, time, [class*="date"], [datetime]');
                        if (dateEl) {
                            date = dateEl.getAttribute('datetime') || dateEl.textContent.trim();
                        }
                        // 备选：从容器文本中正则提取
                        if (!date) {
                            date = extractDate(container.textContent || '');
                        }
                    }

                    seen.add(href);
                    data.push({
                        title,
                        url: href,
                        time: date,
                        summary: '',
                        type: 'press_release_pdf'
                    });
                });

                return {items: data, pageTitle: document.title, url: window.location.href, itemCount: data.length};
            }''')

            items = result.get('items', [])
            self.logger.info(f"[新聞稿] 找到 {len(items)} 个PDF链接")

            # 如果列表中没有找到PDF，尝试通用提取
            if not items:
                result = self._extract_items_from_context(page)
                items = result.get('items', [])
                for item in items:
                    item['type'] = 'press_release'

            return items[:limit]
        except Exception as e:
            self.logger.warning(f"[新聞稿] 提取失败: {e}")
            return []

    def _fetch_investor_events(self, page, limit: int) -> List[dict]:
        """
        投资者活动栏目：列表仅拿到活动详情页链接
        标记类型，后续在详情页找PDF
        """
        try:
            result = self._extract_items_from_context(page)
            items = result.get('items', [])

            for item in items:
                item['type'] = 'investor_event'

            self.logger.info(f"[投資者活動] 找到 {len(items)} 条活动")
            return items[:limit]
        except Exception as e:
            self.logger.warning(f"[投資者活動] 提取失败: {e}")
            return []

    def _extract_pdf_attachment(self, content: str, page) -> tuple:
        """
        从内容中提取 PDF 附件链接并下载

        Returns:
            (PDF本地路径, PDF文本内容)，无附件返回 (None, None)
        """
        if not self.pdf_processor or not content:
            return None, None

        # 检测 PDF 附件模式

        # HKEX-EPS PDF 文件名模式
        pdf_patterns = [
            r'(HKEX-EPS[_-]\d{8}[_-]\d+[_-]\d+)\.?PDF?',
            r'(HKEX-EPS[_-]\d{8}[_-]\d+[_-]\d+)',
            r'href=["\']([^"\']*\.PDF)["\']',
            r'href=["\']([^"\']*\.pdf)["\']',
        ]

        pdf_filename = None
        for pattern in pdf_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                pdf_filename = match.group(1)
                break

        if not pdf_filename:
            return None, None

        # 尝试构建 PDF URL (港交所公告PDF通常在同一域名)
        # 先从页面中获取可能的 PDF 链接
        try:
            pdf_links = page.evaluate('''() => {
                const links = Array.from(document.querySelectorAll('a[href*=".PDF"], a[href*=".pdf"]'));
                return links.map(a => a.href);
            }''')

            pdf_url = None
            for link in pdf_links:
                if pdf_filename.replace('_', '') in link.replace('_', '').upper():
                    pdf_url = link
                    break

            # 如果没找到具体链接，尝试通用模式
            if not pdf_url and pdf_filename.startswith('HKEX-EPS'):
                # 港交所PDF通常格式: https://www1.hkexnews.hk/...
                self.logger.debug(f"检测到HKEX公告PDF: {pdf_filename}")
        except Exception as e:
            self.logger.debug(f"提取PDF链接失败: {e}")

        return None, None

    def _extract_hkex_pdf(self, page, url: str) -> tuple:
        """
        公告栏目：访问详情页，正则提取 HKEX-EPS 文件名，构造港交所PDF URL并下载

        Returns:
            (PDF本地路径, PDF文本内容, 日期) 失败返回 (None, None, "")
        """
        if not self.pdf_processor or not url:
            return None, None, ""

        try:
            # 保存当前页面URL以便返回
            original_url = page.url

            # 访问详情页
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)

            # 获取页面HTML内容
            html_content = page.content()

            # 提取日期
            date_from_detail = ""
            try:
                result = page.evaluate('''() => {
                    let dateStr = '';
                    const dateSelectors = [
                        'time[datetime]',
                        '.date',
                        '.publish-date',
                        'meta[property="article:published_time"]',
                        'meta[name="publish-date"]',
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
                    return dateStr;
                }''')
                date_from_detail = result if result else ""
            except Exception:
                pass

            # 正则提取 HKEX-EPS 文件名
            # 匹配模式: HKEX-EPS_YYYYMMDD_XXXXX_X 或 HKEX-EPS-YYYYMMDD-XXXXX-X
            hkex_match = re.search(
                r'HKEX[-\s]?EPS[_-](\d{8})[_-](\d+)[_-](\d+)',
                html_content,
                re.IGNORECASE
            )

            if not hkex_match:
                self.logger.debug(f"未在详情页找到HKEX-EPS文件名: {url}")
                return None, None, date_from_detail

            # 从文件名中提取日期（YYYYMMDD -> YYYY-MM-DD）
            date_str = hkex_match.group(1)
            if not date_from_detail and len(date_str) == 8:
                date_from_detail = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
                self.logger.debug(f"  从HKEX文件名提取日期: {date_from_detail}")

            # 构建完整的文件名
            pdf_filename = f"HKEX-EPS_{hkex_match.group(1)}_{hkex_match.group(2)}_{hkex_match.group(3)}"

            # 构造港交所PDF下载URL
            # 根据HKEX公告系统，使用 GetPressRelease 接口
            pdf_url = f"https://www1.hkexnews.hk/listedco/listconews/advancedsearch/search_active_main.aspx/GetPressRelease?file={pdf_filename}.PDF"

            self.logger.info(f"  找到HKEX PDF: {pdf_filename}")

            # 下载PDF
            result = self.pdf_processor.download_pdf(pdf_url, stock_code="HKEX")
            if not result:
                self.logger.warning(f"  HKEX PDF下载失败: {pdf_url}")
                return None, None, date_from_detail

            pdf_path, file_id = result

            # 提取文本
            text = self.pdf_processor.extract_text(pdf_path)

            # 尝试返回原页面
            try:
                if original_url and original_url != url and original_url != 'about:blank':
                    page.goto(original_url, wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(3000)
            except Exception:
                pass

            return pdf_path, text, date_from_detail

        except Exception as e:
            self.logger.warning(f"提取HKEX PDF失败: {e}")
            return None, None, ""

    def _download_pdf_direct(self, pdf_url: str) -> tuple:
        """
        直接下载PDF文件并提取文本

        Returns:
            (PDF本地路径, PDF文本内容) 失败返回 (None, None)
        """
        if not self.pdf_processor or not pdf_url:
            return None, None

        try:
            # 防盗链：金山IR的 static-files 需要带 Referer
            extra_headers = {}
            if 'ir.kingsoft.com' in pdf_url:
                extra_headers['Referer'] = 'https://ir.kingsoft.com/zh-hant/news-events/press-releases'

            # 下载PDF
            result = self.pdf_processor.download_pdf(pdf_url, stock_code="kingsoft", extra_headers=extra_headers)
            if not result:
                return None, None

            pdf_path, file_id = result

            # 提取文本
            text = self.pdf_processor.extract_text(pdf_path)

            return pdf_path, text

        except Exception as e:
            self.logger.warning(f"直接下载PDF失败: {e}")
            return None, None

    def _extract_event_pdf(self, page, url: str, section_name: str) -> tuple:
        """
        投资者活动栏目：访问详情页搜索PDF链接

        Returns:
            (PDF本地路径, PDF文本内容, 页面文本, 日期) 失败返回 (None, None, "", "")
        """
        if not url:
            return None, None, "", ""

        try:
            # 保存当前页面URL以便返回
            original_url = page.url

            # 访问详情页
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)

            # 搜索PDF链接
            pdf_url = None
            try:
                pdf_links = page.evaluate('''() => {
                    // 查找所有可能的PDF链接
                    const selectors = [
                        'a[type="application/pdf"]',
                        'a[href$=".pdf"]',
                        'a[href$=".PDF"]',
                        'a[href*=".pdf"]',
                        'a[href*=".PDF"]'
                    ];
                    const links = [];
                    for (const selector of selectors) {
                        const found = document.querySelectorAll(selector);
                        found.forEach(a => {
                            links.push({
                                href: a.href || a.getAttribute('href'),
                                text: a.textContent.trim()
                            });
                        });
                    }
                    return links;
                }''')

                # 选择第一个有效的PDF链接
                for link_info in pdf_links:
                    href = link_info.get('href', '')
                    if href:
                        pdf_url = href if href.startswith('http') else urljoin(self.base_url, href)
                        link_text = link_info.get('text', '')[:30]
                        self.logger.info(f"  找到活动PDF链接: {link_text}")
                        break

            except Exception as e:
                self.logger.debug(f"搜索PDF链接失败: {e}")

            # 下载PDF（如果找到）
            pdf_path = None
            pdf_content = None
            if pdf_url and self.pdf_processor:
                result = self.pdf_processor.download_pdf(pdf_url, stock_code="kingsoft")
                if result:
                    pdf_path, file_id = result
                    pdf_content = self.pdf_processor.extract_text(pdf_path)

            # 获取页面文本内容（作为备选）
            content, old_summary, date_from_detail = self._get_detail_content(page, url, section_name)

            # 尝试返回原页面
            try:
                if original_url and original_url != url and original_url != 'about:blank':
                    page.goto(original_url, wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(3000)
            except Exception:
                pass

            return pdf_path, pdf_content, content, date_from_detail

        except Exception as e:
            self.logger.warning(f"提取活动PDF失败: {e}")
            return None, None, "", ""

    def fetch(self, max_pages: int = 1) -> List[NewsItem]:
        """
        采集新闻活动四个栏目数据（支持PDF附件下载和AI摘要）
        """
        import os

        items = []

        self.logger.info(f"开始采集金山软件IR官网")

        # 创建批次目录（参考CNInfo方案）
        now = datetime.now()
        date_dir = now.strftime('%Y/%m/%d')
        batch_name = now.strftime(f'{self.source_code}_%Y%m%d_%H%M%S')
        self._batch_dir = f"{self.output_dir}/{self.source_code}/{date_dir}/{batch_name}"
        os.makedirs(self._batch_dir, exist_ok=True)
        self.logger.info(f"批次目录: {self._batch_dir}")

        # 设置 PDF 下载目录
        if self.pdf_processor:
            pdf_dir = os.path.join(self._batch_dir, "pdfs")
            self.pdf_processor.set_download_dir(pdf_dir)

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

                    self.logger.info("")
                    self.logger.info('='*60)
                    self.logger.info(f"[{section_name}] 开始采集")
                    self.logger.info('='*60)

                    section_items = self._fetch_section(page, section_name, section_url, section_limit)

                    for idx, news in enumerate(section_items, 1):
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

                        self.logger.info(f"\n[{idx}/{len(section_items)}] 处理: {title[:50]}...")

                        # 根据类型采用不同的内容获取策略
                        content, old_summary, date_from_detail = "", "", ""
                        pdf_path = None
                        pdf_content = None
                        news_type = news.get('type', '')

                        if news_type == 'announcement':
                            # 策略1: 公告 - 访问详情页提取HKEX-EPS文件名，构造PDF URL下载
                            if url and self.pdf_processor:
                                self.logger.info(f"  公告: 访问详情页提取HKEX PDF...")
                                pdf_path, pdf_content, date_from_detail = self._extract_hkex_pdf(page, url)
                                if pdf_path:
                                    self.logger.info(f"  ✓ HKEX PDF下载成功: {os.path.basename(pdf_path)}")
                                else:
                                    self.logger.warning(f"  ✗ HKEX PDF提取失败，尝试获取页面文本...")
                                    content, old_summary, date_from_detail = self._get_detail_content(page, url, section_name)

                        elif news_type == 'press_release_pdf':
                            # 策略2: 新闻稿 - 直接使用列表页提取的PDF链接下载
                            if url and self.pdf_processor:
                                self.logger.info(f"  新闻稿: 直接下载PDF...")
                                # 处理相对路径
                                pdf_url = url if url.startswith('http') else urljoin(self.base_url, url)
                                pdf_path, pdf_content = self._download_pdf_direct(pdf_url)
                                if pdf_path:
                                    self.logger.info(f"  ✓ PDF下载成功: {os.path.basename(pdf_path)}")
                                else:
                                    self.logger.warning(f"  ✗ PDF下载失败")

                        elif news_type == 'investor_event':
                            # 策略3: 投资者活动 - 访问详情页搜索PDF链接
                            if url:
                                self.logger.info(f"  投资者活动: 访问详情页搜索PDF...")
                                pdf_path, pdf_content, content, date_from_detail = self._extract_event_pdf(page, url, section_name)
                                if pdf_path:
                                    self.logger.info(f"  ✓ 活动PDF下载成功: {os.path.basename(pdf_path)}")
                                elif content:
                                    self.logger.info(f"  ℹ 未找到PDF，使用页面文本")
                                else:
                                    self.logger.warning(f"  ✗ 无法获取活动内容")

                        else:
                            # 默认策略: 访问详情页获取内容
                            if url:
                                self.logger.info(f"  正在获取详情页...")
                                content, old_summary, date_from_detail = self._get_detail_content(page, url, section_name)

                        # 内容截断处理（防止超长）
                        if content and len(content) > 8000:
                            self.logger.debug(f"  内容过长，已截断: {len(content)} -> 8000字符")
                            content = content[:8000] + "\n...[内容过长已截断]"

                        # 优先使用PDF内容，其次使用网页内容
                        final_content = pdf_content if pdf_content else content

                        # 生成 AI 摘要（如果启用且内容足够）
                        summary = old_summary
                        summary_generated_at = None
                        if final_content and len(final_content.strip()) >= 50:
                            self.logger.info(f"  正在生成 AI 摘要...")
                            ai_summary, gen_time = self.generate_summary(title, final_content)
                            if ai_summary:
                                summary = ai_summary
                                summary_generated_at = gen_time
                                self.logger.info(f"  ✓ AI 摘要生成成功: {len(ai_summary)} 字")
                            else:
                                self.logger.warning(f"  ✗ AI 摘要生成失败")

                        # 如果没有获取到摘要，使用标题作为备选
                        if not summary:
                            summary = title

                        # 优先使用详情页的日期，如果为空则使用列表页的日期
                        list_date = news.get('time', '')
                        final_date = date_from_detail if date_from_detail else list_date

                        # 如果已有日期无法解析，或为空，尝试从内容和摘要中提取
                        if (not final_date or not self._parse_time(final_date)) and (final_content or summary):
                            extracted = self._extract_date_from_content(final_content + ' ' + summary)
                            if extracted:
                                final_date = extracted

                        # 构建 raw_data
                        raw_data = {
                            'section': section_name,
                            'section_url': section_url,
                        }
                        if pdf_path:
                            rel_pdf_path = os.path.relpath(pdf_path, self._batch_dir)
                            raw_data['pdf_path'] = rel_pdf_path

                        item = NewsItem(
                            title=title,
                            date=self._parse_time(final_date),
                            url=url,
                            source=self.source_name,
                            source_code=self.source_code,
                            credibility_tag=self.credibility_base,
                            category=self._auto_classify(title),
                            summary=summary,
                            summary_generated_at=summary_generated_at,
                            content=final_content,
                            raw_data=raw_data
                        )
                        items.append(item)
                        self.logger.info(f"  ✓ 已采集: {title[:40]}...")

                try:
                    page.screenshot(path=f"output/logs/kingsoft_ir_check.png")
                except Exception:
                    pass

            except Exception as e:
                self.logger.error(f"采集失败: {e}", exc_info=True)
            finally:
                context.close()
                browser.close()

        self.logger.info("")
        self.logger.info('='*60)
        self.logger.info(f"采集完成: {len(items)} 条")
        self.logger.info('='*60)
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
