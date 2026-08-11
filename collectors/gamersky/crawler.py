# -*- coding: utf-8 -*-
"""
游民星空采集器
使用 Playwright 模拟浏览器获取搜索结果
支持多关键词搜索、详情页正文抓取、AI 摘要
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from datetime import datetime, timedelta
from typing import List, Dict, Set, Optional
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


class GamerskyCrawler(BaseCrawler):
    """游民星空采集器

    页面结构：
    - 首页：https://www.gamersky.com/indexbeta/
    - 搜索框：首页顶部
    - 搜索流程：
      1. 首页点击搜索框，输入关键词，回车
      2. 进入搜索结果页：https://so.gamersky.com/?s=关键词
      3. 点击"新闻资讯"下的"更多"链接
      4. 进入新闻列表页：https://so.gamersky.com/all/news?s=关键词
    - 新闻列表：
      - 容器：<ul class="txtlist contentpaging">
      - 标题：<div class="tit"><div class="t2"><a>...</a></div></div>
      - 摘要：<div class="con">...</div>
      - 时间：<div class="bot"><div class="time">2026-04-23 15:00</div></div>
      - 链接：<div class="bot"><div class="link"><a href="...">...</a></div></div>
    """

    source_name = "游民星空"
    source_code = "gamersky"
    credibility_base = "【媒体报道】"

    def __init__(self, enable_summary: bool = None, hours_window: int = 168):
        # 从配置读取参数
        config = COLLECTORS.get('gamersky', {})
        self.keywords = config.get('keywords', ['西山居', '金山世游'])
        self.max_items_per_keyword = 30  # 只爬当前页，不需要太多

        # 时间窗口（默认7天，游戏媒体更新频率较低）
        self.hours_window = hours_window
        self.cutoff_time = datetime.now() - timedelta(hours=hours_window)
        self.logger_info = f"时间窗口: 过去{hours_window}小时 ({self.cutoff_time.strftime('%Y-%m-%d %H:%M')} 至今)"

        # 从配置读取 enable_summary
        if enable_summary is None:
            enable_summary = config.get('enable_summary', True)

        super().__init__(enable_summary=enable_summary)

        self.base_url = "https://www.gamersky.com"
        self.search_base_url = "https://so.gamersky.com"

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
        支持格式：YYYY-MM-DD HH:MM（如 2026-04-23 15:00）
        """
        if not time_str:
            return datetime.now().strftime('%Y-%m-%d')

        time_str = time_str.strip()

        # 匹配 YYYY-MM-DD HH:MM
        match = re.search(r'(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})', time_str)
        if match:
            return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"

        # 匹配 YYYY-MM-DD
        match = re.search(r'(\d{4})-(\d{2})-(\d{2})', time_str)
        if match:
            return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"

        # 匹配 YYYY年MM月DD日
        match = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', time_str)
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"

        return datetime.now().strftime('%Y-%m-%d')

    def _parse_gamersky_time(self, time_str: str) -> datetime:
        """
        解析游民星空的时间格式为 datetime 对象
        支持格式：YYYY-MM-DD HH:MM
        """
        if not time_str:
            return datetime.now()

        time_str = time_str.strip()

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

        return datetime.now()

    def _is_in_time_window(self, time_str: str) -> bool:
        """判断时间是否在过去指定小时内"""
        try:
            parsed_time = self._parse_gamersky_time(time_str)
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

    def _extract_news_results(self, page) -> List[Dict]:
        """
        从新闻资讯列表页提取内容
        """
        results = []

        try:
            # 等待新闻列表加载
            page.wait_for_selector('ul.txtlist.contentpaging li', timeout=10000)
        except Exception:
            self.logger.debug("未找到新闻列表")
            return results

        # 获取所有新闻项
        items = page.locator('ul.txtlist.contentpaging li').all()
        self.logger.info(f"找到 {len(items)} 条新闻")

        for item in items:
            try:
                # 提取标题
                title_el = item.locator('.tit .t2 a').first
                if title_el.count() == 0:
                    continue

                title = title_el.text_content() or ''
                title = title.strip()

                # 提取链接（优先从bot的link获取，更完整）
                href = ''
                link_el = item.locator('.bot .link a').first
                if link_el.count() > 0:
                    href = link_el.get_attribute('href') or ''

                # 如果上面的获取失败，从标题获取
                if not href:
                    href = title_el.get_attribute('href') or ''

                if href and not href.startswith('http'):
                    href = f"{self.base_url}{href}" if href.startswith('/') else f"{self.base_url}/{href}"

                # 提取摘要
                summary = ''
                con_el = item.locator('.con').first
                if con_el.count() > 0:
                    summary = con_el.text_content() or ''
                    summary = summary.strip()

                # 提取时间
                time_str = ''
                time_el = item.locator('.bot .time').first
                if time_el.count() > 0:
                    time_str = time_el.text_content() or ''
                    time_str = time_str.strip()

                if title and href:
                    results.append({
                        'title': title,
                        'url': href,
                        'time': time_str,
                        'summary': summary,
                    })
            except Exception as e:
                self.logger.debug(f"提取新闻项失败: {e}")
                continue

        return results

    def _search_keyword(self, page, keyword: str) -> List[Dict]:
        """
        搜索单个关键词
        流程：首页 → 搜索 → 结果页 → 点击新闻资讯"更多" → 新闻列表页
        """
        results = []

        try:
            # 步骤1：访问首页
            self.logger.info(f"[{keyword}] 访问首页: {self.base_url}/indexbeta/")
            page.goto(f"{self.base_url}/indexbeta/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

            # 步骤2：查找并点击搜索框
            # 搜索框特征：<input class="Sinput" type="text" name="s" placeholder="" autocomplete="new-password">
            self.logger.info(f"[{keyword}] 查找搜索框")

            # 先尝试点击搜索区域展开（如果搜索框默认收起）
            search_form = page.locator('#search-form, .Search').first
            if search_form.count() > 0:
                search_form.click()
                page.wait_for_timeout(500)

            search_input = page.locator('input.Sinput[name="s"]').first

            if search_input.count() == 0 or not search_input.is_visible():
                # 尝试直接访问搜索结果页
                self.logger.info(f"[{keyword}] 未找到搜索框，直接访问搜索结果页")
                encoded_keyword = quote(keyword)
                search_url = f"{self.search_base_url}/?s={encoded_keyword}"
                page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)
            else:
                # 输入关键词并搜索
                self.logger.info(f"[{keyword}] 输入关键词并搜索")
                search_input.click()
                page.wait_for_timeout(300)
                search_input.fill(keyword)
                page.wait_for_timeout(500)
                search_input.press('Enter')
                page.wait_for_timeout(3000)

            # 步骤3：进入新闻列表页
            # 搜索结果页有"资讯"标签，链接格式：//so.gamersky.com/all/news?s=关键词
            self.logger.info(f"[{keyword}] 进入新闻列表页")
            encoded_keyword = quote(keyword)
            news_url = f"{self.search_base_url}/all/news?s={encoded_keyword}"
            page.goto(news_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

            # 截图调试
            try:
                page.screenshot(path=f"output/logs/gamersky_{keyword}_news.png")
            except Exception:
                pass

            # 步骤4：提取新闻列表
            results = self._extract_news_results(page)
            self.logger.info(f"[{keyword}] 提取 {len(results)} 条新闻")

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

            # 游民星空文章页结构
            content = page.evaluate("""() => {
                const selectors = [
                    '.Mid2L_con',
                    '.article-content',
                    '.post-content',
                    '.content-detail',
                    '#content',
                    '.main-content',
                    'article',
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

        self.logger.info(f"开始采集游民星空 - 关键词: {self.keywords}")
        self.logger.info(self.logger_info)

        # 创建批次目录
        now = datetime.now()
        date_dir = now.strftime('%Y/%m/%d')
        batch_name = now.strftime(f'{self.source_code}_%Y%m%d_%H%M%S')
        self._batch_dir = f"{self.output_dir}/{self.source_code}/{date_dir}/{batch_name}"
        os.makedirs(self._batch_dir, exist_ok=True)

        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
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

    # 支持环境变量配置时间窗口（默认7天）
    hours_window = int(os.getenv('GAMERSKY_HOURS_WINDOW', '168'))

    crawler = GamerskyCrawler(hours_window=hours_window)
    items = crawler.run()

    print(f"\n{'='*70}")
    print(f"游民星空采集结果: {len(items)} 条")
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
