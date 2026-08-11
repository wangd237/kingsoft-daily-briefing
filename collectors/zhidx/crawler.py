# -*- coding: utf-8 -*-
"""
智东西采集器
使用 Playwright 模拟浏览器获取搜索结果
支持多关键词搜索、详情页正文抓取、AI 摘要
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from datetime import datetime, timedelta
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


class ZhidxCrawler(BaseCrawler):
    """智东西采集器

    页面结构：
    - 首页：https://www.zhidx.com/
    - 搜索框在右上角
    - 搜索结果页：https://www.zhidx.com/?s=关键词
    - 结果列表：<ul class="info-list">
    - 标题：<div class="tag-info-left-title"><a>...</a></div>
    - 时间：<div class="iril-related-time">07-20</div>
    - 加载更多：<div class="info-left-other load-over">加载更多...</div>
    """

    source_name = "智东西"
    source_code = "zhidx"
    credibility_base = "【媒体报道】"

    def __init__(self, enable_summary: bool = None, hours_window: int = 24):
        # 从配置读取参数
        config = COLLECTORS.get('zhidx', {})
        self.keywords = config.get('keywords', ['金山办公'])
        self.max_items_per_keyword = 50

        # 时间窗口（默认24小时）
        self.hours_window = hours_window
        self.cutoff_time = datetime.now() - timedelta(hours=hours_window)
        self.logger_info = f"时间窗口: 过去{hours_window}小时 ({self.cutoff_time.strftime('%Y-%m-%d %H:%M')} 至今)"

        # 从配置读取 enable_summary
        if enable_summary is None:
            enable_summary = config.get('enable_summary', True)

        super().__init__(enable_summary=enable_summary)

        self.base_url = "https://www.zhidx.com"

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
        """
        解析时间字符串，返回 YYYY-MM-DD 格式
        支持格式：
        - "07-20" (今年)
        - "24/06/21" (2024年)
        - "23/10/25" (2023年)
        - "19/10/04" (2019年)
        """
        if not time_str:
            return datetime.now().strftime('%Y-%m-%d')

        time_str = time_str.strip()
        now = datetime.now()
        current_year = now.year

        # 匹配 "YY/MM/DD" 格式（如 "24/06/21"）
        match = re.match(r'(\d{2})/(\d{2})/(\d{2})', time_str)
        if match:
            year_short = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3))
            # 将短年份转换为完整年份（20xx）
            year = 2000 + year_short
            return f"{year}-{month:02d}-{day:02d}"

        # 匹配 "MM-DD" 格式（如 "07-20"，假设是今年）
        match = re.match(r'(\d{2})-(\d{2})', time_str)
        if match:
            month = int(match.group(1))
            day = int(match.group(2))
            return f"{current_year}-{month:02d}-{day:02d}"

        # 匹配完整日期格式 "YYYY-MM-DD"
        match = re.search(r'(\d{4})-(\d{2})-(\d{2})', time_str)
        if match:
            return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"

        return datetime.now().strftime('%Y-%m-%d')

    def _parse_zhidx_time(self, time_str: str) -> datetime:
        """
        解析智东西的时间格式为 datetime 对象
        用于时间窗口过滤
        """
        if not time_str:
            return datetime.now()

        time_str = time_str.strip()
        now = datetime.now()
        current_year = now.year

        # 匹配 "YY/MM/DD" 格式（如 "24/06/21"）
        match = re.match(r'(\d{2})/(\d{2})/(\d{2})', time_str)
        if match:
            year_short = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3))
            year = 2000 + year_short
            return datetime(year, month, day)

        # 匹配 "MM-DD" 格式（如 "07-20"，假设是今年）
        match = re.match(r'(\d{2})-(\d{2})', time_str)
        if match:
            month = int(match.group(1))
            day = int(match.group(2))
            return datetime(current_year, month, day)

        # 匹配完整日期格式
        match = re.search(r'(\d{4})-(\d{2})-(\d{2})', time_str)
        if match:
            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3))
            return datetime(year, month, day)

        return now

    def _is_in_time_window(self, time_str: str) -> bool:
        """判断时间是否在过去指定小时内"""
        try:
            parsed_time = self._parse_zhidx_time(time_str)
            return parsed_time >= self.cutoff_time
        except Exception as e:
            self.logger.debug(f"时间解析失败 '{time_str}': {e}")
            return True  # 解析失败默认保留

    def _matches_keywords(self, title: str) -> bool:
        """检查标题是否包含任一关键词"""
        if not title:
            return False
        title_lower = title.lower()
        return any(kw.lower() in title_lower for kw in self.keywords)

    def _click_load_more(self, page) -> bool:
        """
        点击"加载更多"按钮加载更多结果
        返回是否还有更多内容
        """
        try:
            # 查找加载更多按钮
            load_more_selector = '.info-left-other.load-over'
            load_more = page.locator(load_more_selector).first

            if load_more.count() == 0:
                return False

            if not load_more.is_visible():
                return False

            # 检查是否已加载完成（按钮文字变化）
            text = load_more.text_content() or ''
            if '加载更多' not in text:
                return False

            self.logger.info("点击'加载更多'按钮...")
            load_more.click()
            page.wait_for_timeout(2000)  # 等待内容加载

            return True
        except Exception as e:
            self.logger.debug(f"点击加载更多失败: {e}")
            return False

    def _extract_search_results(self, page) -> List[Dict]:
        """
        从搜索结果页提取内容
        """
        results = []

        try:
            # 等待结果列表加载
            page.wait_for_selector('ul.info-list li', timeout=10000)
        except Exception:
            self.logger.debug("未找到搜索结果列表")
            return results

        # 获取所有结果项
        items = page.locator('ul.info-list li').all()
        self.logger.info(f"找到 {len(items)} 条搜索结果")

        for item in items:
            try:
                # 提取标题
                title_el = item.locator('.tag-info-left-title a').first
                if title_el.count() == 0:
                    continue

                title = title_el.get_attribute('title') or ''
                if not title:
                    title = title_el.text_content() or ''

                # 提取链接
                href = title_el.get_attribute('href') or ''
                if href and not href.startswith('http'):
                    href = f"{self.base_url}{href}" if href.startswith('/') else f"{self.base_url}/{href}"

                # 提取时间
                time_str = ''
                time_el = item.locator('.iril-related-time').first
                if time_el.count() > 0:
                    time_str = time_el.text_content() or ''
                    time_str = time_str.strip()

                if title and href:
                    results.append({
                        'title': title.strip(),
                        'url': href,
                        'time': time_str,
                    })
            except Exception as e:
                self.logger.debug(f"提取搜索结果项失败: {e}")
                continue

        return results

    def _search_keyword(self, page, keyword: str) -> List[Dict]:
        """
        搜索单个关键词
        """
        results = []

        try:
            # 访问首页
            self.logger.info(f"[{keyword}] 访问首页: {self.base_url}")
            page.goto(self.base_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

            # 查找搜索框（右上角）
            # 智东西搜索框可能有不同的选择器，尝试多种方式
            search_selectors = [
                'input[name="s"]',
                'input[placeholder*="搜索"]',
                '.search-form input',
                'header input[type="text"]',
                'input[type="search"]',
            ]

            search_input = None
            for selector in search_selectors:
                try:
                    el = page.locator(selector).first
                    if el.count() > 0 and el.is_visible():
                        search_input = el
                        self.logger.info(f"[{keyword}] 找到搜索框: {selector}")
                        break
                except:
                    continue

            if not search_input:
                # 尝试直接访问搜索结果页
                self.logger.info(f"[{keyword}] 未找到搜索框，直接访问搜索结果页")
                search_url = f"{self.base_url}/?s={keyword}"
                page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)
            else:
                # 在搜索框中输入关键词
                search_input.fill(keyword)
                page.wait_for_timeout(500)

                # 按回车执行搜索
                search_input.press('Enter')
                page.wait_for_timeout(3000)

            # 截图调试
            try:
                page.screenshot(path=f"output/logs/zhidx_{keyword}_search.png")
            except Exception:
                pass

            # # 提取搜索结果
            # results = self._extract_search_results(page)
            # self.logger.info(f"[{keyword}] 初始提取 {len(results)} 条")

            # 点击"加载更多"直到没有更多内容或达到上限
            load_more_count = 0
            max_load_more = 1  # 最多点击1次加载更多（一次的结果已经足够）

            while len(results) < self.max_items_per_keyword and load_more_count < max_load_more:
                if not self._click_load_more(page):
                    break
                load_more_count += 1

                # 重新提取所有结果
                new_results = self._extract_search_results(page)
                if len(new_results) <= len(results):
                    self.logger.info("没有新内容加载，停止加载更多")
                    break

                results = new_results
                self.logger.info(f"[{keyword}] 加载更多后: {len(results)} 条")

            # 过滤：关键词匹配 + 时间窗口
            filtered_results = []
            for item in results:
                title = item.get('title', '')
                time_str = item.get('time', '')

                # 检查关键词匹配
                if not self._matches_keywords(title):
                    self.logger.debug(f"[{keyword}] 关键词过滤: {title[:50]}...")
                    continue

                # 检查时间窗口
                if not self._is_in_time_window(time_str):
                    self.logger.debug(f"[{keyword}] 时间过滤: {title[:50]}... (时间: {time_str})")
                    continue

                filtered_results.append(item)

            self.logger.info(f"[{keyword}] 过滤后: {len(filtered_results)}/{len(results)} 条")
            results = filtered_results

        except Exception as e:
            self.logger.error(f"[{keyword}] 搜索失败: {e}")

        return results

    def _fetch_content(self, page, url: str) -> str:
        """
        获取详情页正文
        """
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            # 提取正文 - 智东西文章页结构
            content = page.evaluate("""() => {
                const selectors = [
                    '.article-content',
                    '.content-detail',
                    '.post-content',
                    'article',
                    '.main-content',
                    '#content',
                    '.entry-content',
                    '[class*="content"]',
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
        """
        采集数据
        """
        all_items = []

        self.logger.info(f"开始采集智东西 - 关键词: {self.keywords}")
        self.logger.info(self.logger_info)

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
                            date=self._parse_time(news.get('time', '')),
                            url=url,
                            source=self.source_name,
                            source_code=self.source_code,
                            credibility_tag=self.credibility_base,
                            category=self._auto_classify(news['title']),
                            summary=news['title'][:150],
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
    import os

    # 支持环境变量配置时间窗口（默认24小时）
    hours_window = int(os.getenv('ZHIDX_HOURS_WINDOW', '24'))  # 默认24小时，智东西更新频率较低
    crawler = ZhidxCrawler(hours_window=hours_window)
    items = crawler.run()

    print(f"\n{'='*70}")
    print(f"智东西采集结果: {len(items)} 条")
    print(f"时间窗口: 过去{hours_window}小时")
    print(f"截止时间: {crawler.cutoff_time.strftime('%Y-%m-%d %H:%M')}")
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
