# -*- coding: utf-8 -*-
"""
GameLook 采集器
使用 Playwright 模拟浏览器获取搜索结果
支持多关键词搜索、详情页正文抓取、AI 摘要
"""
import sys
# 强制 UTF-8 输出：用 reconfigure 改编码，不替换 sys.stdout/stderr 对象
# （替换后原对象被 GC 会关闭共享 buffer，导致 print 抛 "I/O operation on closed file"）
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

from datetime import datetime, timedelta
from typing import List, Dict, Set
from pathlib import Path
import time
import os
import re
from urllib.parse import quote

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from collectors.base import BaseCrawler
from models.news import NewsItem
from config.settings import CATEGORIES, COLLECTORS


class GameLookCrawler(BaseCrawler):
    """GameLook 采集器

    页面结构：
    - 首页：http://www.gamelook.com.cn/
    - 搜索框：右上角 input[name="s"]
    - 搜索结果页：http://www.gamelook.com.cn/?s=关键词
    - 分页：http://www.gamelook.com.cn/page/2/?s=关键词
    - 结果列表：<ul class="article-list clearfix">
    - 标题：<h2 class="item-title"><a href="..." title="...">标题</a></h2>
    - 时间：<span class="item-meta-li date">2026-07-27</span>
    - 摘要：<div class="item-excerpt"><p>摘要</p></div>
    - 分页：<div class="pagination clearfix"><a href="..." class="next">下一页 »</a></div>
    """

    source_name = "GameLook"
    source_code = "gamelook"
    credibility_base = "【媒体报道】"

    def __init__(self, enable_summary: bool = None, hours_window: int = 168):
        # 从配置读取参数
        config = COLLECTORS.get('gamelook', {})
        # GameLook 是游戏媒体，使用游戏行业关键词
        self.keywords = config.get('keywords', ['金山', '西山居', '金山世游'])

        # 时间窗口（默认7天，游戏媒体更新频率较低）
        self.hours_window = hours_window
        self.cutoff_time = datetime.now() - timedelta(hours=hours_window)
        self.logger_info = f"时间窗口: 过去{hours_window}小时 ({self.cutoff_time.strftime('%Y-%m-%d %H:%M')} 至今)"

        # 从配置读取 enable_summary
        if enable_summary is None:
            enable_summary = config.get('enable_summary', True)

        super().__init__(enable_summary=enable_summary)

        self.base_url = "http://www.gamelook.com.cn"

        # 已抓取的URL集合
        self.seen_urls: Set[str] = set()

    def _auto_classify(self, title: str) -> str:
        """自动分类 - 针对游戏行业优化"""
        title_lower = title.lower()
        scores = {}

        # 游戏行业关键词映射
        game_keywords = {
            '产品动态': ['游戏', '手游', '端游', '新作', '上线', '测试', '版本',
                       '玩法', 'IP', '改编', '研发', '引擎', '画面', '美术'],
            '活动IP': ['展会', 'chinajoy', 'cj', '发布会', '活动', '嘉年华',
                     '赛事', '比赛', '电竞', '周年庆'],
            '市场&政企合作': ['合作', '联运', '代理', '发行', '出海', '全球化',
                           '战略', '签约'],
            '资本动态': ['财报', '收入', '流水', '融资', '上市', '并购', '投资'],
        }

        for category, rules in CATEGORIES.items():
            score = sum(1 for kw in rules['keywords'] if kw in title_lower)
            # 叠加游戏行业关键词
            if category in game_keywords:
                score += sum(1 for kw in game_keywords[category] if kw in title_lower)
            scores[category] = score

        if scores and max(scores.values()) > 0:
            return max(scores, key=scores.get)
        return "产品动态"  # 游戏媒体默认分类

    def _parse_time(self, time_str: str) -> str:
        """
        解析时间字符串，返回 YYYY-MM-DD 格式
        支持格式：YYYY-MM-DD（如 2026-07-27）
        """
        if not time_str:
            return datetime.now().strftime('%Y-%m-%d')

        time_str = time_str.strip()

        # 匹配 YYYY-MM-DD
        match = re.search(r'(\d{4})-(\d{2})-(\d{2})', time_str)
        if match:
            return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"

        # 匹配 YYYY年MM月DD日
        match = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', time_str)
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"

        return datetime.now().strftime('%Y-%m-%d')

    def _parse_gamelook_time(self, time_str: str) -> datetime:
        """
        解析 GameLook 的时间格式为 datetime 对象
        """
        if not time_str:
            return datetime.now()

        time_str = time_str.strip()

        # 匹配 YYYY-MM-DD
        match = re.search(r'(\d{4})-(\d{2})-(\d{2})', time_str)
        if match:
            year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            return datetime(year, month, day)

        return datetime.now()

    def _is_in_time_window(self, time_str: str) -> bool:
        """判断时间是否在过去指定小时内"""
        try:
            parsed_time = self._parse_gamelook_time(time_str)
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

    def _extract_search_results(self, page) -> List[Dict]:
        """
        从搜索结果页提取内容
        """
        results = []

        try:
            # 等待结果列表加载
            page.wait_for_selector('ul.article-list li.item', timeout=10000)
        except Exception:
            self.logger.debug("未找到搜索结果列表")
            return results

        # 获取所有结果项
        items = page.locator('ul.article-list li.item').all()
        self.logger.info(f"找到 {len(items)} 条搜索结果")

        for item in items:
            try:
                # 提取标题
                title_el = item.locator('h2.item-title a').first
                if title_el.count() == 0:
                    continue

                title = title_el.get_attribute('title') or ''
                if not title:
                    title = title_el.text_content() or ''
                title = title.strip()

                # 提取链接
                href = title_el.get_attribute('href') or ''
                if href and not href.startswith('http'):
                    href = f"{self.base_url}{href}" if href.startswith('/') else f"{self.base_url}/{href}"

                # 提取时间
                time_str = ''
                time_el = item.locator('span.item-meta-li.date').first
                if time_el.count() > 0:
                    time_str = time_el.text_content() or ''
                    time_str = time_str.strip()

                # 提取摘要
                summary = ''
                excerpt_el = item.locator('div.item-excerpt p').first
                if excerpt_el.count() > 0:
                    summary = excerpt_el.text_content() or ''
                    summary = summary.strip()

                if title and href:
                    results.append({
                        'title': title,
                        'url': href,
                        'time': time_str,
                        'summary': summary,
                    })
            except Exception as e:
                self.logger.debug(f"提取搜索结果项失败: {e}")
                continue

        return results

    def _search_keyword(self, page, keyword: str) -> List[Dict]:
        """
        搜索单个关键词，只抓取第一页结果（不翻页）
        """
        results = []

        try:
            # 直接访问搜索结果页
            encoded_keyword = quote(keyword)
            search_url = f"{self.base_url}/?s={encoded_keyword}"

            self.logger.info(f"[{keyword}] 开始搜索: {search_url}")
            page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

            # 截图调试
            try:
                page.screenshot(path=f"output/logs/gamelook_{keyword}_page1.png")
            except Exception:
                pass

            # 提取第一页结果
            page_results = self._extract_search_results(page)
            self.logger.info(f"[{keyword}] 提取到 {len(page_results)} 条搜索结果")

            # 过滤：关键词匹配 + 时间窗口
            for item in page_results:
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

                results.append(item)

            self.logger.info(f"[{keyword}] 过滤后保留: {len(results)} 条")

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

            # GameLook 文章页结构
            content = page.evaluate("""() => {
                const selectors = [
                    '.entry-content',
                    '.article-content',
                    '.post-content',
                    '.content-area',
                    'article',
                    '.main-content',
                    '#content',
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

        self.logger.info(f"开始采集 GameLook - 关键词: {self.keywords}")
        self.logger.info(self.logger_info)

        # 创建批次目录
        now = datetime.now()
        date_dir = now.strftime('%Y/%m/%d')
        batch_name = now.strftime(f'{self.source_code}_%Y%m%d_%H%M%S')
        if not self._batch_dir:
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
                            summary=news.get('summary', '') or news['title'][:150],
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

    # 支持环境变量配置时间窗口（默认7天，游戏媒体更新频率较低）
    hours_window = int(os.getenv('GAMELOOK_HOURS_WINDOW', '800'))

    crawler = GameLookCrawler(hours_window=hours_window)
    items = crawler.run()

    print(f"\n{'='*70}")
    print(f"GameLook 采集结果: {len(items)} 条")
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
