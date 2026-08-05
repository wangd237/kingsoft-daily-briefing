# -*- coding: utf-8 -*-
"""
财联社采集器
使用 Playwright 模拟浏览器获取搜索结果
支持多关键词搜索、详情页正文抓取、AI 摘要
参考第一财经采集器模式
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


class CLSCrawler(BaseCrawler):
    """财联社采集器"""

    source_name = "财联社"
    source_code = "cls"
    credibility_base = "【媒体报道】"

    def __init__(self, enable_summary: bool = None, hours_window: int = 24):
        # 从配置读取参数
        config = COLLECTORS.get('cls', {})
        self.keywords = config.get('keywords', ['金山办公'])
        self.max_items_per_keyword = 50  # 提高上限，过滤后保留足够数据

        # 时间窗口（默认24小时）
        self.hours_window = hours_window
        self.cutoff_time = datetime.now() - timedelta(hours=hours_window)
        self.logger_info = f"时间窗口: 过去{hours_window}小时 ({self.cutoff_time.strftime('%Y-%m-%d %H:%M')} 至今)"

        # 从配置读取 enable_summary
        if enable_summary is None:
            enable_summary = config.get('enable_summary', True)

        super().__init__(enable_summary=enable_summary)

        self.base_url = "https://www.cls.cn"

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
        """解析时间字符串，返回 YYYY-MM-DD 格式"""
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

        # 匹配 MM-DD HH:MM（如 "07-29 09:38"，假设是今年）
        match = re.match(r'(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})', time_str)
        if match:
            month, day = int(match.group(1)), int(match.group(2))
            current_year = datetime.now().year
            return f"{current_year}-{month:02d}-{day:02d}"

        return datetime.now().strftime('%Y-%m-%d')

    def _parse_cls_time(self, time_str: str) -> datetime:
        """
        解析财联社的时间格式
        支持: "刚刚", "X分钟前", "X小时前", "昨天", "昨天 15:30",
              "2025-08-04", "08-04 15:30", "今天 15:30"
        """
        if not time_str:
            return datetime.now()

        time_str = time_str.strip()
        now = datetime.now()

        # 处理 "刚刚"
        if time_str == "刚刚":
            return now

        # 处理 "X分钟前"
        match = re.search(r'(\d+)分钟前', time_str)
        if match:
            minutes = int(match.group(1))
            return now - timedelta(minutes=minutes)

        # 处理 "X小时前"
        match = re.search(r'(\d+)小时前', time_str)
        if match:
            hours = int(match.group(1))
            return now - timedelta(hours=hours)

        # 处理 "今天 HH:MM"
        match = re.search(r'今天\s*(\d{1,2}):(\d{2})', time_str)
        if match:
            hour, minute = int(match.group(1)), int(match.group(2))
            return now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # 处理 "昨天" 或 "昨天 15:30"
        if '昨天' in time_str:
            time_part = re.search(r'(\d{1,2}):(\d{2})', time_str)
            if time_part:
                hour, minute = int(time_part.group(1)), int(time_part.group(2))
                yesterday = now - timedelta(days=1)
                return yesterday.replace(hour=hour, minute=minute, second=0, microsecond=0)
            else:
                # 只有"昨天"，设为昨天00:00（会被保留，宁可多采）
                return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

        # 处理 "YYYY-MM-DD" 或 "YYYY/MM/DD"
        match = re.search(r'(\d{4})[-/](\d{2})[-/](\d{2})', time_str)
        if match:
            year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            return datetime(year, month, day)

        # 处理 "MM-DD HH:MM"（假设是今年）
        match = re.match(r'(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})', time_str)
        if match:
            month, day, hour, minute = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
            return datetime(now.year, month, day, hour, minute)

        # 解析失败，返回当前时间（保留数据）
        return now

    def _is_in_time_window(self, time_str: str) -> bool:
        """判断时间是否在过去指定小时内"""
        try:
            parsed_time = self._parse_cls_time(time_str)
            return parsed_time >= self.cutoff_time
        except:
            # 解析失败默认保留
            return True

    def _matches_keywords(self, title: str) -> bool:
        """检查标题是否包含任一关键词"""
        if not title:
            return False
        title_lower = title.lower()
        return any(kw.lower() in title_lower for kw in self.keywords)

    def _search_keyword(self, page, keyword: str) -> List[Dict]:
        """搜索单个关键词"""
        results = []

        try:
            # 直接访问电报搜索页面
            search_url = f"{self.base_url}/searchPage?keyword={keyword}&type=telegram"
            self.logger.info(f"[{keyword}] 访问电报搜索页: {search_url}")

            page.goto(search_url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(3000)

            # 检查是否触发了验证码或反爬虫机制
            page_title = page.title()
            page_content = page.content()

            if "验证" in page_title or "captcha" in page_content.lower() or "aliyunCaptcha" in page_content:
                self.logger.error(f"[{keyword}] 财联社触发了验证码保护，无法继续采集")
                self.logger.error("建议：1) 稍后重试 2) 使用其他数据源替代财联社")
                page.screenshot(path=f"output/logs/cls_{keyword}_captcha.png")
                return results

            # 截图调试
            page.screenshot(path=f"output/logs/cls_{keyword}_search.png")

            # 等待电报列表加载
            try:
                page.wait_for_selector('.search-content', timeout=10000)
                self.logger.info(f"[{keyword}] 电报列表已加载")
            except Exception as e:
                self.logger.warning(f"[{keyword}] 等待列表超时: {e}")

            # 使用 JavaScript 直接提取所有电报数据
            telegraph_data = page.evaluate('''() => {
                const items = [];
                const contentDivs = document.querySelectorAll('.search-content');
                contentDivs.forEach(div => {
                    try {
                        const content = div.textContent?.trim() || '';
                        if (!content) return;

                        // 获取链接
                        const link = div.closest('a');
                        const url = link?.getAttribute('href') || '';

                        // 获取时间 - 找父级div的第一个子div（时间div）
                        let time = '';
                        const parentDiv = link?.parentElement?.parentElement;
                        if (parentDiv) {
                            const timeDiv = parentDiv.querySelector('div:first-child');
                            time = timeDiv?.textContent?.trim() || '';
                        }

                        items.push({
                            content: content,
                            url: url,
                            time: time
                        });
                    } catch (e) {}
                });
                return items;
            }''')

            if telegraph_data and len(telegraph_data) > 0:
                self.logger.info(f"[{keyword}] 提取到 {len(telegraph_data)} 条电报")
                for item in telegraph_data:
                    content = item.get('content', '').strip()
                    if not content:
                        continue

                    title = content[:60] + "..." if len(content) > 60 else content
                    url = item.get('url', '')
                    if url and not url.startswith('http'):
                        url = f"{self.base_url}{url}"
                    if not url:
                        url = search_url

                    pub_time = item.get('time', '')

                    results.append({
                        'title': title.strip(),
                        'url': url,
                        'time': pub_time,
                        'summary': content.strip(),
                        'content': content.strip(),
                        'is_telegraph': True,
                    })

            self.logger.info(f"[{keyword}] 成功提取 {len(results)} 条")

            # 过滤：关键词匹配 + 时间窗口
            filtered_results = []

            for item in results:
                title = item.get('title', '')
                time_str = item.get('time', '')

                # 检查关键词匹配（标题必须包含关键词）
                if not self._matches_keywords(title):
                    self.logger.debug(f"[{keyword}] 关键词过滤: {title[:50]}...")
                    continue

                # 检查时间是否在窗口内
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
        """获取详情页正文"""
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            # 尝试从 window.__INITIAL_STATE__ 提取
            initial_state = page.evaluate('''() => {
                return window.__INITIAL_STATE__ || window.initialState || null;
            }''')

            if initial_state:
                try:
                    article = None
                    if isinstance(initial_state, dict):
                        if 'detail' in initial_state:
                            article = initial_state['detail'].get('articleDetail', {})
                        elif 'articleDetail' in initial_state:
                            article = initial_state['articleDetail']

                    if article:
                        content = article.get('content', '')
                        if content:
                            # 财联社内容可能是 HTML 格式
                            return self._clean_html_content(content)
                except:
                    pass

            # 从 DOM 提取内容
            content = page.evaluate("""() => {
                const selectors = [
                    '.article-content',
                    '.detail-content',
                    '.content-detail',
                    '.article-detail',
                    'article',
                    '.main-content',
                    '#content',
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

    def _clean_html_content(self, html: str) -> str:
        """清理HTML内容"""
        try:
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
        except ImportError:
            # 如果没有 bs4，简单清理
            import re
            text = re.sub(r'<[^>]+>', '', html)
            lines = [line.strip() for line in text.split('\n') if line.strip()]
            return '\n'.join(lines)

    def fetch(self) -> List[NewsItem]:
        """采集数据 - 只采集电报内容"""
        all_items = []

        self.logger.info(f"开始采集财联社电报 - 关键词: {self.keywords}")
        self.logger.info(self.logger_info)  # 打印时间窗口信息

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

                    # 如果检测到验证码，跳过后续关键词
                    if not results:
                        page_title = page.title()
                        if "验证" in page_title:
                            self.logger.warning("财联社触发了验证码保护，本次采集提前结束")
                            break

                    for news in results:
                        url = news['url']

                        # 去重
                        if url in self.seen_urls:
                            continue
                        self.seen_urls.add(url)

                        # 电报内容已在搜索结果中获取，不需要再进详情页
                        content = news.get('content', '')
                        if not content:
                            # 如果没有内容，尝试获取详情页
                            self.logger.info(f"获取详情页: {news['title'][:50]}...")
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
                            summary=news.get('summary', '') or news['title'][:150],
                            content=content,
                            raw_data={'keyword': keyword, 'is_telegraph': news.get('is_telegraph', True)},
                        )

                        # AI 摘要（如果内容足够长）
                        if content and len(content.strip()) > 100:
                            ai_summary, summary_time = self.generate_summary(news['title'], content)
                            if ai_summary:
                                item.summary = ai_summary
                                item.summary_generated_at = summary_time

                        all_items.append(item)
                        time.sleep(0.5)

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
    hours_window = int(os.getenv('CLS_HOURS_WINDOW', '1600'))

    crawler = CLSCrawler(hours_window=hours_window)
    items = crawler.run()

    print(f"\n{'='*70}")
    print(f"财联社采集结果: {len(items)} 条")
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