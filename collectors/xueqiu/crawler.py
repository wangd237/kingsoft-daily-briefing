# -*- coding: utf-8 -*-
"""
雪球采集器
通过股票页面直接获取资讯和公告
支持金山系三只股票：金山办公(SH688111)、金山软件(HK03888)、金山云(HK03896)
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from datetime import datetime, timedelta
from typing import List, Dict, Optional
from pathlib import Path
import time
import os
import re

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from collectors.base import BaseCrawler
from models.news import NewsItem
from config.settings import CATEGORIES, COLLECTORS


class XueqiuCrawler(BaseCrawler):
    """雪球采集器 - 股票页面直接采集模式"""

    source_name = "雪球"
    source_code = "xueqiu"
    credibility_base = "【投资者社区】"

    # 金山系股票列表
    STOCKS = [
        {'code': 'SH688111', 'name': '金山办公', 'market': '科创板'},
        {'code': '03888', 'name': '金山软件', 'market': '港股'},
        {'code': '03896', 'name': '金山云', 'market': '港股'},
    ]

    # Tab 配置
    TABS = [
        {'key': 'news', 'name': '资讯', 'credibility': '【投资者社区】'},
        {'key': 'notice', 'name': '公告', 'credibility': '【官方公告】'},
    ]

    def __init__(self, enable_summary: bool = None, hours_window: int = 24):
        # 时间窗口（默认24小时）
        self.hours_window = hours_window
        self.cutoff_time = datetime.now() - timedelta(hours=hours_window)
        self.logger_info = f"时间窗口: 过去{hours_window}小时 ({self.cutoff_time.strftime('%Y-%m-%d %H:%M')} 至今)"

        # 从配置读取 enable_summary
        config = COLLECTORS.get('xueqiu', {})
        if enable_summary is None:
            enable_summary = config.get('enable_summary', True)

        super().__init__(enable_summary=enable_summary)

        self.base_url = "https://xueqiu.com"

        # 已抓取的URL集合（去重用）
        self.seen_urls: set = set()

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

    def _parse_time(self, time_str: str, year: int | None = None) -> str:
        """
        解析时间字符串，返回 YYYY-MM-DD 格式
        支持: "08-04 17:16", "2025-08-04", "刚刚", "X分钟前", "X小时前", "昨天"
        """
        if not time_str:
            return datetime.now().strftime('%Y-%m-%d')

        time_str = time_str.strip()
        now = datetime.now()
        current_year = year or now.year

        # 处理 "刚刚"
        if time_str == "刚刚":
            return now.strftime('%Y-%m-%d')

        # 处理 "X分钟前"
        match = re.search(r'(\d+)\s*分钟前', time_str)
        if match:
            return now.strftime('%Y-%m-%d')

        # 处理 "X小时前"
        match = re.search(r'(\d+)\s*小时前', time_str)
        if match:
            return now.strftime('%Y-%m-%d')

        # 处理 "昨天"
        if '昨天' in time_str:
            yesterday = now - timedelta(days=1)
            return yesterday.strftime('%Y-%m-%d')

        # 匹配 YYYY-MM-DD
        match = re.search(r'(\d{4})-(\d{2})-(\d{2})', time_str)
        if match:
            return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"

        # 匹配 MM-DD HH:MM（雪球最常见格式）
        match = re.match(r'(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})', time_str)
        if match:
            month, day = int(match.group(1)), int(match.group(2))
            return f"{current_year}-{month:02d}-{day:02d}"

        return now.strftime('%Y-%m-%d')

    def _parse_datetime(self, time_str: str) -> datetime:
        """
        解析时间为 datetime 对象，用于时间窗口过滤
        支持: "08-04 17:16", "MM-DD HH:MM" 等格式
        """
        if not time_str:
            return datetime.now()

        time_str = time_str.strip()
        now = datetime.now()

        # 处理 "刚刚"
        if time_str == "刚刚":
            return now

        # 处理 "X分钟前"
        match = re.search(r'(\d+)\s*分钟前', time_str)
        if match:
            minutes = int(match.group(1))
            return now - timedelta(minutes=minutes)

        # 处理 "X小时前"
        match = re.search(r'(\d+)\s*小时前', time_str)
        if match:
            hours = int(match.group(1))
            return now - timedelta(hours=hours)

        # 处理 "昨天"
        if '昨天' in time_str:
            time_part = re.search(r'(\d{1,2}):(\d{2})', time_str)
            if time_part:
                hour, minute = int(time_part.group(1)), int(time_part.group(2))
                yesterday = now - timedelta(days=1)
                return yesterday.replace(hour=hour, minute=minute, second=0, microsecond=0)
            else:
                return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

        # 匹配 YYYY-MM-DD HH:MM
        match = re.search(r'(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})', time_str)
        if match:
            year, month, day, hour, minute = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4)), int(match.group(5))
            return datetime(year, month, day, hour, minute)

        # 匹配 YYYY-MM-DD
        match = re.search(r'(\d{4})-(\d{2})-(\d{2})', time_str)
        if match:
            year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            return datetime(year, month, day)

        # 匹配 MM-DD HH:MM（假设是今年）
        match = re.match(r'(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})', time_str)
        if match:
            month, day, hour, minute = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
            return datetime(now.year, month, day, hour, minute)

        return now

    def _is_in_time_window(self, time_str: str) -> bool:
        """判断时间是否在过去指定小时内"""
        try:
            parsed_time = self._parse_datetime(time_str)
            return parsed_time >= self.cutoff_time
        except Exception as e:
            self.logger.debug(f"时间解析失败 '{time_str}': {e}")
            return True  # 解析失败默认保留

    def _extract_notice_data(self, item, stock: dict) -> Optional[dict]:
        """
        提取公告数据
        包裹公告的元素: class="timeline__item__main"
        """
        try:
            # 时间 - .date-and-source
            time_str = ""
            try:
                time_el = item.locator('.date-and-source').first
                time_text = time_el.text_content(timeout=1000) or ""
                # 格式: "08-04 17:16· 来自公告"
                time_match = re.match(r'(\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2})', time_text.strip())
                if time_match:
                    time_str = time_match.group(1)
            except Exception as e:
                self.logger.debug(f"提取公告时间失败: {e}")

            # 内容 - .content--description
            content = ""
            try:
                content_el = item.locator('.content--description').first
                content = content_el.text_content(timeout=1000) or ""
                content = content.strip()
            except Exception as e:
                self.logger.debug(f"提取公告内容失败: {e}")

            # 提取链接 - 从 .date-and-source 的 href 或内容中的链接
            url = ""
            try:
                # 尝试从 date-and-source 获取链接
                link_el = item.locator('.date-and-source').first
                href = link_el.get_attribute('href') or ""
                if href:
                    url = f"{self.base_url}{href}" if not href.startswith('http') else href
            except Exception:
                pass

            # 如果上面的方法失败，尝试从内容中的 status-link 获取
            if not url:
                try:
                    link_el = item.locator('.status-link').first
                    url = link_el.get_attribute('href') or ""
                except Exception:
                    pass

            if content:
                return {
                    'title': content[:100] + ('...' if len(content) > 100 else ''),  # 公告没有独立标题，用内容前100字
                    'url': url or f"{self.base_url}/S/{stock['code']}",  # 保底URL
                    'time': time_str,
                    'summary': content,
                    'type': '公告',
                    'stock_name': stock['name'],
                }

        except Exception as e:
            self.logger.debug(f"解析公告项失败: {e}")

        return None

    def _extract_news_data(self, item, stock: dict) -> Optional[dict]:
        """
        提取资讯数据
        包裹资讯的元素: class="timeline__item__content timeline__item__content--longtext"
        """
        try:
            # 标题 - .timeline__item__title span
            title = ""
            try:
                title_el = item.locator('.timeline__item__title span').first
                title = title_el.text_content(timeout=1000) or ""
                title = title.strip()
            except Exception as e:
                self.logger.debug(f"提取资讯标题失败: {e}")

            # 摘要 - .content--description
            summary = ""
            try:
                desc_el = item.locator('.content--description').first
                summary = desc_el.text_content(timeout=1000) or ""
                summary = summary.strip()
                # 移除 "网页链接" 后缀
                summary = re.sub(r'\s*网页链接\s*$', '', summary)
            except Exception as e:
                self.logger.debug(f"提取资讯摘要失败: {e}")

            # 链接 - 从 fake-anchor 或 title 的父级 a 标签
            url = ""
            try:
                # 尝试 fake-anchor
                link_el = item.locator('a.fake-anchor').first
                href = link_el.get_attribute('href') or ""
                if href:
                    url = f"{self.base_url}{href}" if not href.startswith('http') else href
            except Exception:
                pass

            # 如果失败，尝试从父级获取
            if not url:
                try:
                    link_el = item.locator('a[data-id]').first
                    href = link_el.get_attribute('href') or ""
                    if href:
                        url = f"{self.base_url}{href}" if not href.startswith('http') else href
                except Exception:
                    pass

            # 时间 - 尝试从父级 timeline__item 获取
            time_str = ""
            try:
                # 资讯 item 可能需要在父元素中找时间
                parent = item.locator('..').first
                time_el = parent.locator('.date-and-source').first
                time_text = time_el.text_content(timeout=1000) or ""
                time_match = re.match(r'(\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2})', time_text.strip())
                if time_match:
                    time_str = time_match.group(1)
            except Exception:
                pass

            if title:
                return {
                    'title': title,
                    'url': url or f"{self.base_url}/S/{stock['code']}",
                    'time': time_str,
                    'summary': summary or title,
                    'type': '资讯',
                    'stock_name': stock['name'],
                }

        except Exception as e:
            self.logger.debug(f"解析资讯项失败: {e}")

        return None

    def _scroll_to_load(self, page, max_items: int = 50) -> int:
        """
        滚动页面加载更多内容
        返回最终加载的条目数
        """
        last_count = 0
        same_count_count = 0
        max_same_count = 3  # 连续3次数量不变，认为已加载完

        for i in range(20):  # 最多滚动20次
            # 获取当前条目数
            current_count = page.locator('.timeline__item').count()

            if current_count >= max_items:
                self.logger.info(f"已加载 {current_count} 条，达到上限")
                break

            if current_count == last_count:
                same_count_count += 1
                if same_count_count >= max_same_count:
                    self.logger.info(f"滚动 {i+1} 次后无新内容，共 {current_count} 条")
                    break
            else:
                same_count_count = 0

            last_count = current_count

            # 滚动到底部
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1500)  # 等待加载

        return page.locator('.timeline__item').count()

    def _fetch_stock_tab(self, page, stock: dict, tab: dict) -> List[dict]:
        """
        抓取单个股票的单个 tab 数据
        """
        results = []
        url = f"{self.base_url}/S/{stock['code']}"
        if tab['key'] != 'news':  # news 通常是默认 tab
            url = f"{url}?tab={tab['key']}"

        self.logger.info(f"[{stock['name']}-{tab['name']}] 访问: {url}")

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)  # 等待初始内容

            # 截图调试
            debug_file = f"output/logs/xueqiu_{stock['code']}_{tab['key']}.png"
            page.screenshot(path=debug_file)

            # 点击 tab（如果不是默认 tab）
            if tab['key'] != 'news':
                try:
                    # 尝试点击对应的 tab
                    tab_selector = f'a[href="?tab={tab["key"]}"], a[data-tab="{tab["key"]}"]'
                    if page.locator(tab_selector).count() > 0:
                        page.locator(tab_selector).first.click()
                        page.wait_for_timeout(2000)
                except Exception as e:
                    self.logger.debug(f"点击 tab 失败（可能已在该 tab）: {e}")

            # 等待 timeline 加载
            try:
                page.wait_for_selector('.timeline__item', timeout=10000)
            except:
                self.logger.warning(f"[{stock['name']}-{tab['name']}] 未找到 timeline__item")
                return results

            # 滚动加载更多
            total_items = self._scroll_to_load(page, max_items=50)
            self.logger.info(f"[{stock['name']}-{tab['name']}] 共加载 {total_items} 条")

            # 获取所有条目
            items = page.locator('.timeline__item').all()

            for idx, item in enumerate(items):
                try:
                    # 根据 tab 类型选择解析方式
                    if tab['key'] == 'notice':
                        # 公告类型
                        # 检查是否有 timeline__item__main
                        if item.locator('.timeline__item__main').count() > 0:
                            main_item = item.locator('.timeline__item__main').first
                            data = self._extract_notice_data(main_item, stock)
                        else:
                            data = self._extract_notice_data(item, stock)
                    else:
                        # 资讯类型
                        # 检查是否有 timeline__item__content--longtext
                        if item.locator('.timeline__item__content--longtext').count() > 0:
                            content_item = item.locator('.timeline__item__content--longtext').first
                            data = self._extract_news_data(content_item, stock)
                        else:
                            data = self._extract_news_data(item, stock)

                    if data:
                        # 检查时间窗口
                        if not self._is_in_time_window(data['time']):
                            self.logger.debug(f"时间过滤跳过: {data['title'][:50]}... ({data['time']})")
                            continue

                        results.append(data)

                except Exception as e:
                    self.logger.debug(f"解析第 {idx+1} 项失败: {e}")
                    continue

            self.logger.info(f"[{stock['name']}-{tab['name']}] 有效数据: {len(results)} 条")

        except Exception as e:
            self.logger.error(f"[{stock['name']}-{tab['name']}] 抓取失败: {e}")

        return results

    def _fetch_content_detail(self, page, url: str) -> str:
        """
        获取详情页正文
        雪球文章详情页结构
        """
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            # 雪球文章详情页常见内容选择器
            content = page.evaluate("""() => {
                const selectors = [
                    // 文章正文
                    '.article__bd__detail',
                    'article.article__bd',
                    '.article_body',
                    // 状态/公告内容
                    '.status-content',
                    '.detail__content',
                    // 通用
                    '[class*="content"]',
                    'article',
                ];

                for (const selector of selectors) {
                    const el = document.querySelector(selector);
                    if (el) {
                        const text = el.innerText?.trim();
                        if (text && text.length > 50) {
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

        self.logger.info(f"开始采集雪球 - 金山系股票资讯与公告")
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
                for stock in self.STOCKS:
                    self.logger.info(f"{'='*50}")
                    self.logger.info(f"开始采集: {stock['name']} ({stock['code']})")
                    self.logger.info(f"{'='*50}")

                    for tab in self.TABS:
                        results = self._fetch_stock_tab(page, stock, tab)

                        for news in results:
                            url = news['url']

                            # 去重
                            if url in self.seen_urls:
                                continue
                            self.seen_urls.add(url)

                            self.logger.info(f"处理: {news['title'][:50]}...")

                            # 获取详情页正文（如果有详情页链接）
                            content = ""
                            if '/S/' in url and url != f"{self.base_url}/S/{stock['code']}":
                                content = self._fetch_content_detail(page, url)
                                time.sleep(0.5)

                            # 创建 NewsItem
                            item = NewsItem(
                                title=news['title'],
                                date=self._parse_time(news['time']),
                                url=url,
                                source=self.source_name,
                                source_code=self.source_code,
                                credibility_tag=f"{tab['credibility']}｜{stock['name']}",
                                category=self._auto_classify(news['title']),
                                summary=news['summary'],
                                content=content,
                                raw_data={
                                    'stock_code': stock['code'],
                                    'stock_name': stock['name'],
                                    'tab': tab['key'],
                                    'tab_name': tab['name'],
                                    'pub_time': news['time'],
                                },
                            )

                            # AI 摘要
                            if content and len(content.strip()) > 50:
                                ai_summary, summary_time = self.generate_summary(news['title'], content)
                                if ai_summary:
                                    item.summary = ai_summary
                                    item.summary_generated_at = summary_time

                            all_items.append(item)

                        time.sleep(1)  # tab 间延迟

                    time.sleep(2)  # 股票间延迟

            finally:
                context.close()
                browser.close()

        self.logger.info(f"采集完成: 共 {len(all_items)} 条")
        return all_items


def main():
    """测试运行"""
    import os

    # 支持环境变量配置时间窗口（默认24小时）
    hours_window = int(os.getenv('XUEQIU_HOURS_WINDOW', '24'))

    crawler = XueqiuCrawler(hours_window=hours_window)
    items = crawler.run()

    print(f"\n{'='*70}")
    print(f"雪球采集结果: {len(items)} 条")
    print(f"时间窗口: 过去{hours_window}小时")
    print(f"截止时间: {crawler.cutoff_time.strftime('%Y-%m-%d %H:%M')}")
    print('='*70)

    for i, item in enumerate(items, 1):
        print(f"\n{'─'*70}")
        print(f"{i}. [{item.category}] {item.title}")
        print(f"   时间: {item.date}")
        print(f"   来源: {item.credibility_tag}")
        print(f"   链接: {item.url}")

        if item.summary:
            print(f"   摘要: {item.summary[:200]}...")


if __name__ == "__main__":
    main()
