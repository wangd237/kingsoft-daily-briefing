# -*- coding: utf-8 -*-
"""
证券时报e公司采集器
使用 Playwright 模拟浏览器获取搜索结果
支持多关键词搜索、当天过滤、详情页正文抓取、AI 摘要
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote
import time
import os

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from collectors.base import BaseCrawler
from models.news import NewsItem
from config.settings import CATEGORIES, COLLECTORS


class StcnCrawler(BaseCrawler):
    """证券时报e公司采集器
    支持多关键词搜索、时间窗口过滤（默认24小时）、详情页正文抓取、AI 摘要
    """

    source_name = "证券时报e公司"
    source_code = "stcn"
    credibility_base = "【媒体报道】"

    def __init__(self, enable_summary: bool = True, hours_window: int = None, skip_date_filter: bool = False):
        """
        初始化

        Args:
            enable_summary: 是否启用 AI 摘要
            hours_window: 时间窗口（小时），默认24小时
            skip_date_filter: 是否跳过日期过滤（兼容旧参数）
        """
        # 从配置读取参数
        config = COLLECTORS.get('stcn', {})
        self.keywords = config.get('keywords', ['金山办公'])
        self.max_items_per_keyword = 5  # 每个关键词最多采集条数

        # 从配置读取 enable_summary
        config_enable_summary = config.get('enable_summary', True)
        final_enable_summary = enable_summary and config_enable_summary

        # 时间窗口（默认24小时）
        if skip_date_filter:
            self.hours_window = None
            self.cutoff_time = None
            self.logger_info = "⚠️ 时间过滤已禁用"
        else:
            self.hours_window = hours_window or config.get('hours_window', 24)
            self.cutoff_time = datetime.now() - timedelta(hours=self.hours_window)
            self.logger_info = f"时间窗口: 过去{self.hours_window}小时 ({self.cutoff_time.strftime('%Y-%m-%d %H:%M')} 至今)"

        super().__init__(enable_summary=final_enable_summary)

        self.base_url = "https://egs.stcn.com"

        # 已抓取的URL集合（用于去重）
        self.seen_urls: set[str] = set()

    def _auto_classify(self, title: str) -> str:
        """自动分类"""
        title_lower = title.lower()
        scores = {}
        for category, rules in CATEGORIES.items():
            score = sum(1 for kw in rules['keywords'] if kw in title_lower)
            scores[category] = score
        if scores and max(scores.values(), default=0) > 0:
            return max(scores, key=scores.get)
        return "资本动态"

    def _parse_stcn_time(self, time_str: str) -> datetime:
        """
        解析证券时报的时间字符串为 datetime
        支持格式: YYYY-MM-DD HH:MM:SS, YYYY-MM-DD, YYYY/MM/DD, MM-DD HH:MM 等
        解析失败返回当前时间（保留数据）
        """
        if not time_str:
            self.logger.warning(f"⚠️ 时间解析失败(空值)，已回退到今日时间")
            return datetime.now()

        time_str = time_str.strip()

        # 各种格式尝试
        formats = [
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d %H:%M',
            '%Y-%m-%d',
            '%Y/%m/%d %H:%M:%S',
            '%Y/%m/%d %H:%M',
            '%Y/%m/%d',
            '%m-%d %H:%M',
        ]

        for fmt in formats:
            try:
                parsed = datetime.strptime(time_str, fmt)
                if fmt == '%m-%d %H:%M':
                    # 补充年份
                    parsed = parsed.replace(year=datetime.now().year)
                return parsed
            except ValueError:
                continue

        # HH:MM 格式（假设今天）
        try:
            parsed = datetime.strptime(time_str, '%H:%M')
            now = datetime.now()
            return now.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
        except ValueError:
            pass

        # 解析失败，返回当前时间
        self.logger.warning(f"⚠️ 时间解析失败('{time_str}')，已回退到今日时间")
        return datetime.now()

    def _is_in_time_window(self, time_str: str) -> bool:
        """判断时间是否在时间窗口内"""
        if self.cutoff_time is None:
            return True

        parsed_time = self._parse_stcn_time(time_str)
        return parsed_time >= self.cutoff_time

    def _is_today(self, time_str: str) -> bool:
        """判断时间是否为当天（兼容旧方法）"""
        if self.cutoff_time is None:
            return True

        parsed_time = self._parse_stcn_time(time_str)
        return parsed_time.strftime('%Y-%m-%d') == datetime.now().strftime('%Y-%m-%d')

    def _parse_time(self, time_str: str) -> str:
        """解析时间字符串，返回 YYYY-MM-DD 格式"""
        if not time_str or time_str.strip() == '':
            return datetime.now().strftime('%Y-%m-%d')

        time_str = time_str.strip()

        formats = [
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d %H:%M',
            '%Y-%m-%d',
            '%Y/%m/%d %H:%M:%S',
            '%Y/%m/%d %H:%M',
            '%Y/%m/%d',
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(time_str, fmt)
                return dt.strftime('%Y-%m-%d')
            except ValueError:
                continue

        # 处理月-日 时间格式（如 "06-08 19:28"）
        month_day_match = re.match(r'^(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})$', time_str)
        if month_day_match:
            month, day = month_day_match.group(1), month_day_match.group(2)
            current_year = datetime.now().year
            return f"{current_year}-{month.zfill(2)}-{day.zfill(2)}"

        return self.today

    def _extract_search_results(self, page, keyword: str) -> list[dict]:
        """从搜索页面提取结果"""
        results = []

        try:
            # 访问搜索页面
            search_url = f"{self.base_url}/news/search.html?keyword={quote(keyword)}"
            self.logger.info(f"[{keyword}] 访问搜索页面: {search_url}")

            page.goto(search_url, wait_until='networkidle', timeout=30000)
            page.wait_for_load_state('domcontentloaded', timeout=10000)

            # 等待搜索结果异步加载 - 先等待资讯标签被点击后的内容
            try:
                # 等待资讯列表出现
                page.wait_for_selector('[data-type="news"] ul.list li', timeout=15000)
                self.logger.info(f"[{keyword}] 搜索结果列表已加载")
            except:
                self.logger.warning(f"[{keyword}] 等待列表超时，尝试点击资讯标签")
                # 尝试点击资讯标签
                try:
                    news_tab = page.locator('[data-type="news"]').first
                    if news_tab:
                        news_tab.click()
                        time.sleep(2)
                except:
                    pass

            time.sleep(2)  # 额外等待确保内容稳定

            # 调试：保存页面截图和 HTML
            debug_dir = f"output/logs/stcn_debug"
            os.makedirs(debug_dir, exist_ok=True)
            page.screenshot(path=f"{debug_dir}/{keyword}_search.png")
            with open(f"{debug_dir}/{keyword}_search.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            self.logger.info(f"[{keyword}] 调试文件已保存到 {debug_dir}")

            # 方式1：从 window.__INITIAL_STATE__ 提取
            initial_state = page.evaluate('''() => {
                return window.__INITIAL_STATE__ || window.initialState || null;
            }''')

            if initial_state:
                self.logger.info(f"[{keyword}] 找到 initialState")
                search_data = None
                if isinstance(initial_state, dict):
                    if 'search' in initial_state:
                        search_data = initial_state['search'].get('list', [])
                    elif 'newsSearch' in initial_state:
                        search_data = initial_state['newsSearch'].get('data', [])
                    elif 'searchResult' in initial_state:
                        search_data = initial_state['searchResult'].get('data', [])

                if search_data and isinstance(search_data, list):
                    self.logger.info(f"[{keyword}] 从 initialState 提取到 {len(search_data)} 条结果")
                    for item in search_data:
                        if not isinstance(item, dict):
                            continue
                        title = item.get('title', '').strip()
                        if not title:
                            continue

                        url = item.get('url', '')
                        if not url:
                            continue
                        if not url.startswith('http'):
                            url = self.base_url + url

                        results.append({
                            'title': title,
                            'url': url,
                            'time': item.get('time', '') or item.get('publishTime', '') or item.get('ctime', ''),
                        })

            # 方式2：从 DOM 提取
            if not results:
                self.logger.info(f"[{keyword}] 尝试从 DOM 提取结果")

                try:
                    # 使用 locator API 提取数据
                    list_items = page.locator('[data-type="news"] ul.list li').all()
                    self.logger.info(f"[{keyword}] 找到 {len(list_items)} 个列表项")

                    for item in list_items:
                        try:
                            title_el = item.locator('.title a').first
                            title = title_el.text_content() or ''
                            url = title_el.get_attribute('href') or ''

                            # 获取时间（最后一个 span）
                            time_spans = item.locator('.bottom .info span').all()
                            pub_time = ''
                            if time_spans:
                                pub_time = time_spans[-1].text_content() or ''
                                pub_time = pub_time.strip()

                            if title and url:
                                if not url.startswith('http'):
                                    url = self.base_url + url
                                results.append({
                                    'title': title.strip(),
                                    'url': url,
                                    'time': pub_time
                                })
                        except Exception as e:
                            self.logger.debug(f"解析列表项失败: {e}")
                            continue

                except Exception as e:
                    self.logger.error(f"[{keyword}] DOM 提取失败: {e}")

                self.logger.info(f"[{keyword}] 从 DOM 提取到 {len(results)} 条结果")
                # 调试：打印前几条结果
                for i, r in enumerate(results[:5]):
                    self.logger.info(f"  [{i+1}] 标题: {r['title'][:30]}... 时间: '{r['time']}'")

        except Exception as e:
            self.logger.error(f"[{keyword}] 提取搜索结果失败: {e}")

        # 过滤：关键词匹配 + 日期过滤
        filtered_results = []
        keyword_lower = keyword.lower()

        for item in results:
            title = item.get('title', '')
            time_str = item.get('time', '')

            # 检查关键词匹配
            if keyword_lower not in title.lower():
                self.logger.debug(f"[{keyword}] 过滤掉不相关结果: {title[:50]}...")
                continue

            # 检查时间窗口
            if self.cutoff_time and not self._is_in_time_window(time_str):
                self.logger.debug(f"[{keyword}] 时间过滤: {title[:50]}... (日期: {time_str})")
                continue

            filtered_results.append(item)

        filter_mode = "时间过滤已禁用" if self.cutoff_time is None else f"时间窗口: {self.hours_window}小时"
        self.logger.info(f"[{keyword}] 过滤后: {len(filtered_results)}/{len(results)} 条 ({filter_mode})")
        return filtered_results

    def _fetch_content_with_playwright(self, page, url: str) -> str:
        """使用 Playwright 获取详情页内容"""
        try:
            self.logger.info(f"  访问详情页: {url[:60]}...")

            page.goto(url, wait_until='networkidle', timeout=30000)
            page.wait_for_load_state('domcontentloaded', timeout=10000)
            time.sleep(2)

            # 尝试从 window.__INITIAL_STATE__ 提取
            initial_state = page.evaluate('''() => {
                return window.__INITIAL_STATE__ || window.initialState || null;
            }''')

            if initial_state:
                try:
                    article = None
                    if isinstance(initial_state, dict):
                        if 'article' in initial_state:
                            article = initial_state['article'].get('detail', {})
                        elif 'newsDetail' in initial_state:
                            article = initial_state['newsDetail']
                        elif 'detail' in initial_state:
                            article = initial_state['detail']

                    if article:
                        content = article.get('content', '')
                        if content:
                            return self._clean_html_content(content)
                except:
                    pass

            # 从 DOM 提取内容
            content = page.evaluate('''() => {
                const selectors = [
                    'article',
                    '.article-content',
                    '.content-detail',
                    '.news-content',
                    '.text-content',
                    '#content',
                    '.main-content',
                    '.detail-content',
                ];

                for (const selector of selectors) {
                    const el = document.querySelector(selector);
                    if (el && el.textContent.length > 100) {
                        return el.innerHTML;
                    }
                }

                // 备选：提取所有段落
                const paragraphs = document.querySelectorAll('p');
                const text = Array.from(paragraphs)
                    .map(p => p.textContent.trim())
                    .filter(t => t.length > 20)
                    .join('\\n');
                return text;
            }''')

            if content:
                return self._clean_html_content(content)

            return ""

        except Exception as e:
            self.logger.error(f"  获取详情页失败: {url} - {e}")
            return ""

    def _clean_html_content(self, html: str) -> str:
        """清理HTML内容"""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')

        # 移除script和style
        for script in soup(['script', 'style']):
            script.decompose()

        # 获取文本
        text = soup.get_text(separator='\n', strip=True)

        # 清理多余空行
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        return '\n'.join(lines)

    def fetch(self) -> list[NewsItem]:
        """采集数据"""
        from playwright.sync_api import sync_playwright

        all_items = []

        self.logger.info(f"开始采集 - 关键词: {self.keywords}, {self.logger_info}")

        # 创建批次目录
        now = datetime.now()
        date_dir = now.strftime('%Y/%m/%d')
        batch_name = now.strftime(f'{self.source_code}_%Y%m%d_%H%M%S')
        if not self._batch_dir:
            self._batch_dir = f"{self.output_dir}/{self.source_code}/{date_dir}/{batch_name}"
        os.makedirs(self._batch_dir, exist_ok=True)
        self.logger.info(f"批次目录: {self._batch_dir}")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={'width': 1280, 'height': 800},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )

            # 搜索页面
            search_page = context.new_page()

            for keyword in self.keywords:
                self.logger.info(f"开始搜索关键词: {keyword}")

                # 提取搜索结果
                results = self._extract_search_results(search_page, keyword)
                self.logger.info(f"[{keyword}] 找到 {len(results)} 条有效结果")

                # 限制数量
                results = results[:self.max_items_per_keyword]

                # 创建详情页标签页
                detail_page = context.new_page()

                for idx, news in enumerate(results, 1):
                    url = news['url']

                    # 去重检查
                    if url in self.seen_urls:
                        self.logger.info(f"[{idx}/{len(results)}] 跳过重复: {news['title'][:50]}...")
                        continue
                    self.seen_urls.add(url)

                    self.logger.info(f"[{idx}/{len(results)}] 处理: {news['title'][:50]}...")

                    # 获取正文
                    content = self._fetch_content_with_playwright(detail_page, url)

                    # 创建 NewsItem
                    item = NewsItem(
                        title=news['title'],
                        date=self._parse_time(news['time']),
                        url=url,
                        source=self.source_name,
                        source_code=self.source_code,
                        credibility_tag=self.credibility_base,
                        category=self._auto_classify(news['title']),
                        summary=news['title'][:150] if len(news['title']) > 150 else news['title'],
                        content=content,
                        raw_data={'keyword': keyword, 'search_time': news['time']},
                    )

                    # 生成 AI 摘要（如果有正文）
                    if content and len(content.strip()) > 50:
                        self.logger.info(f"  生成 AI 摘要...")
                        ai_summary, summary_time = self.generate_summary(news['title'], content)
                        if ai_summary:
                            item.summary = ai_summary
                            item.summary_generated_at = summary_time
                            self.logger.info(f"  ✓ 摘要生成成功")

                    all_items.append(item)
                    time.sleep(1)

                detail_page.close()
                time.sleep(2)

            search_page.close()
            browser.close()

        self.logger.info(f"采集完成: 共 {len(all_items)} 条")
        return all_items


def main():
    """测试运行"""
    import os

    enable_summary = os.getenv('STCN_ENABLE_SUMMARY', 'true').lower() == 'true'
    hours_window = int(os.getenv('STCN_HOURS_WINDOW', '24'))
    skip_date_filter = os.getenv('STCN_SKIP_DATE_FILTER', 'false').lower() == 'true'

    crawler = StcnCrawler(
        enable_summary=enable_summary,
        hours_window=hours_window if not skip_date_filter else None,
        skip_date_filter=skip_date_filter
    )
    items = crawler.run()

    print(f"\n{'='*70}")
    print(f"证券时报e公司采集结果: {len(items)} 条")
    if skip_date_filter:
        print("⚠️  时间过滤已禁用 - 采集所有日期")
    else:
        print(f"时间窗口: 过去{hours_window}小时")
        print(f"截止时间: {crawler.cutoff_time.strftime('%Y-%m-%d %H:%M')}")
    print('='*70)

    for i, item in enumerate(items, 1):
        print(f"\n{'─'*70}")
        print(f"{i}. [{item.category}] {item.title}")
        print(f"   时间: {item.date}")
        print(f"   链接: {item.url}")

        if item.summary:
            print(f"   AI摘要: {item.summary}")


if __name__ == "__main__":
    main()
