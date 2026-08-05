# -*- coding: utf-8 -*-
"""
第一财经采集器
使用 Playwright + 页面解析
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


class YicaiCrawler(BaseCrawler):
    """第一财经采集器"""

    source_name = "第一财经"
    source_code = "yicai"
    credibility_base = "【媒体报道】"

    def __init__(self, enable_summary: bool = None, hours_window: int = 24):
        # 从配置读取参数
        config = COLLECTORS.get('yicai', {})
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

        self.base_url = "https://www.yicai.com"

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

    def _parse_yicai_time(self, time_str: str) -> datetime:
        """
        解析第一财经的时间格式
        支持: "10分钟前", "1小时前", "昨天", "昨天 15:30", "2025-08-04", "08-04 15:30"
        """
        if not time_str:
            return datetime.now()

        time_str = time_str.strip()
        now = datetime.now()

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
        """判断时间是否在过去24小时内"""
        try:
            parsed_time = self._parse_yicai_time(time_str)
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
            # 访问搜索页面
            search_url = f"{self.base_url}/search"
            self.logger.info(f"[{keyword}] 访问搜索页: {search_url}")

            page.goto(search_url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)

            # 在搜索框中输入关键词
            self.logger.info(f"[{keyword}] 输入搜索关键词")
            page.fill('#searchkeys2', keyword)
            page.wait_for_timeout(500)

            # 按回车执行搜索
            page.press('#searchkeys2', 'Enter')

            # 等待搜索结果加载
            page.wait_for_selector('#searchlist a', timeout=10000)
            page.wait_for_timeout(3000)

            # 截图调试
            page.screenshot(path=f"output/logs/yicai_{keyword}_search.png")

            # 获取结果数量
            try:
                result_count = page.locator('#resultcount').inner_text()
                self.logger.info(f"[{keyword}] 约找到 {result_count} 条结果")
            except:
                pass

            # 提取搜索结果 - 使用正确的选择器
            result_links = page.locator('#searchlist a').all()
            self.logger.info(f"[{keyword}] 页面上有 {len(result_links)} 个结果项")

            for result in result_links[:self.max_items_per_keyword]:
                try:
                    href = result.get_attribute('href')
                    if not href:
                        continue

                    # 确保URL是完整的
                    if href.startswith('/'):
                        href = f"{self.base_url}{href}"

                    title = result.locator('h2').inner_text()
                    summary = result.locator('p').inner_text()

                    # 获取时间信息
                    author_spans = result.locator('.author span').all()
                    time_text = author_spans[1].inner_text() if len(author_spans) > 1 else ''

                    if title and href:
                        results.append({
                            'title': title.strip(),
                            'url': href,
                            'time': time_text.strip(),
                            'summary': summary.strip()
                        })
                except Exception as e:
                    continue

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

                # 检查时间是否在24小时内
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

            # 提取正文
            content = page.evaluate("""() => {
                const selectors = [
                    '.article-content',
                    '.content-detail',
                    '.news-content',
                    '.main-content',
                    'article',
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
        """采集数据"""
        all_items = []

        self.logger.info(f"开始采集第一财经 - 关键词: {self.keywords}")
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
                            date=self._parse_time(news['time']),
                            url=url,
                            source=self.source_name,
                            source_code=self.source_code,
                            credibility_tag=self.credibility_base,
                            category=self._auto_classify(news['title']),
                            summary=news['summary'] or news['title'][:150],
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

    # 支持环境变量配置时间窗口（默认36小时，可配置为72小时等）
    hours_window = int(os.getenv('YICAI_HOURS_WINDOW', '36'))  # 默认36小时，便于测试

    crawler = YicaiCrawler(hours_window=hours_window)
    items = crawler.run()

    print(f"\n{'='*70}")
    print(f"第一财经采集结果: {len(items)} 条")
    print(f"时间窗口: 过去{hours_window}小时")
    print(f"截止时间: {crawler.cutoff_time.strftime('%Y-%m-%d %H:%M')}")
    if hours_window == 24:
        print("提示: 如需扩大时间窗口，设置环境变量 YICAI_HOURS_WINDOW=72")
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
