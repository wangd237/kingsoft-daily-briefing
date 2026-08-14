# -*- coding: utf-8 -*-
"""
虎嗅采集器
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
import random
import sys
from urllib.parse import urljoin
sys.path.append(str(Path(__file__).parent.parent.parent))

from collectors.base import BaseCrawler
from models.news import NewsItem
from config.settings import CATEGORIES, COLLECTORS


class HuxiuCrawler(BaseCrawler):
    """虎嗅采集器"""

    source_name = "虎嗅"
    source_code = "huxiu"
    credibility_base = "【媒体报道】"

    def __init__(self, enable_summary: bool = None, hours_window: int = 24):
        # 从配置读取参数
        config = COLLECTORS.get('huxiu', {})
        self.keywords = config.get('keywords', ['金山办公'])
        self.max_items_per_keyword = 50  # 提高上限，过滤后保留足够数据

        # 时间窗口（默认24小时）
        self.hours_window = hours_window
        self.cutoff_time = datetime.now() - timedelta(hours=hours_window)
        self.logger_info = f"时间窗口: 过去{hours_window}小时 ({self.cutoff_time.strftime('%Y-%m-%d %H:%M')} 至今)"

        # 验证码检测标志
        self.captcha_detected = False

        # 从配置读取 enable_summary
        if enable_summary is None:
            enable_summary = config.get('enable_summary', True)

        super().__init__(enable_summary=enable_summary)

        self.base_url = "https://www.huxiu.com"

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
        return "产品动态"

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

    def _parse_huxiu_time(self, time_str: str) -> datetime:
        """
        解析虎嗅的时间格式
        支持: "刚刚", "X分钟前", "X小时前", "昨天", "昨天 15:30",
              "2025-08-04", "08-04 15:30"
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

        # 处理 "昨天" 或 "昨天 15:30"
        if '昨天' in time_str:
            time_part = re.search(r'(\d{1,2}):(\d{2})', time_str)
            if time_part:
                hour, minute = int(time_part.group(1)), int(time_part.group(2))
                yesterday = now - timedelta(days=1)
                return yesterday.replace(hour=hour, minute=minute, second=0, microsecond=0)
            else:
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

        # 处理纯数字时间戳（毫秒）
        if time_str.isdigit():
            try:
                timestamp = int(time_str) / 1000
                return datetime.fromtimestamp(timestamp)
            except:
                pass

        # 解析失败，返回当前时间（保留数据）
        return now

    def _is_in_time_window(self, time_str: str) -> bool:
        """判断时间是否在过去指定小时内"""
        try:
            parsed_time = self._parse_huxiu_time(time_str)
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

    def _check_captcha(self, page) -> bool:
        """检查是否触发了验证码，返回True表示触发了验证码。

        只根据页面标题和URL判断，避免扫描整个HTML内容导致误判。
        """
        try:
            page_title = page.title().lower()
            current_url = page.url.lower()

            # 标题类验证码标识
            title_indicators = [
                '验证', 'captcha', '安全验证', '访问验证', '点击验证',
                '滑块验证', '人机验证', '智能验证'
            ]
            for indicator in title_indicators:
                if indicator in page_title:
                    return True

            # URL类验证码标识
            url_indicators = ['captcha', 'verify', 'aliyun']
            for indicator in url_indicators:
                if indicator in current_url:
                    return True

            return False
        except Exception:
            return False

    def _human_delay(self, page, min_ms: int = 500, max_ms: int = 1500):
        """模拟真人操作间隔"""
        page.wait_for_timeout(random.randint(min_ms, max_ms))

    def _open_search_box(self, page) -> bool:
        """点击搜索图标展开搜索框"""
        selectors = [
            'i.icon-search',
            '.header-search',
            '.search-btn',
            '.search-icon',
            'header [class*="search"]',
        ]
        for sel in selectors:
            icon = page.locator(sel).first
            try:
                if icon.is_visible():
                    icon.click()
                    self._human_delay(page, 600, 1200)
                    return True
            except Exception:
                continue
        return False

    def _extract_search_results(self, page) -> List[Dict]:
        """从虎嗅搜索结果页提取内容"""
        results = []

        try:
            page.wait_for_selector(".search-result .pointer", timeout=10000)
        except Exception:
            self.logger.debug("未找到搜索结果容器 (.search-result .pointer)")
            return results

        items = page.locator(".search-result .pointer").all()
        self.logger.info(f"找到 {len(items)} 条搜索结果")

        for item in items:
            try:
                title = item.locator("h5.result-article__title").inner_text().strip()
                summary = item.locator("p.result-article__content").inner_text().strip()

                status_text = item.locator("div.result-article__status").inner_text().strip()
                parts = [p for p in status_text.split() if p]
                source = parts[0] if parts else ""
                pub_time = parts[-1] if len(parts) > 1 else ""

                # 链接：优先取 item 内第一个带 href 的 a 标签
                url = ""
                a_tag = item.locator("a[href]").first
                if a_tag.count() > 0:
                    url = a_tag.get_attribute("href") or ""
                url = urljoin(self.base_url, url)

                if title:
                    results.append({
                        'title': title,
                        'url': url,
                        'time': pub_time,
                        'summary': summary,
                        'source': source,
                    })
            except Exception as e:
                self.logger.debug(f"提取搜索结果项失败: {e}")
                continue

        return results

    def _get_url_by_click(self, page, title: str) -> str:
        """通过点击搜索结果项，在新标签页中获取真实文章 URL。"""
        self.logger.info(f"  尝试点击获取URL: {title[:50]}...")
        try:
            # 通过标题找到结果项的索引
            items = page.locator(".search-result .pointer").all()
            self.logger.debug(f"  当前结果页共 {len(items)} 条结果")

            target_idx = -1
            for idx, item in enumerate(items):
                try:
                    item_title = item.locator("h5.result-article__title").inner_text().strip()
                    if item_title == title:
                        target_idx = idx
                        break
                except Exception as e:
                    self.logger.debug(f"  读取第 {idx} 条标题失败: {e}")
                    continue

            if target_idx < 0:
                self.logger.warning(f"  未找到标题匹配的结果项: {title[:50]}")
                return ""

            self.logger.debug(f"  点击第 {target_idx + 1} 条结果")

            # 点击标题元素，监听新标签页
            title_locator = items[target_idx].locator("h5.result-article__title")
            with page.expect_popup() as popup_info:
                title_locator.click()
            new_page = popup_info.value
            new_page.wait_for_load_state("domcontentloaded", timeout=10000)
            url = new_page.url
            new_page.close()

            self.logger.info(f"  点击获取URL成功: {url}")
            return url
        except Exception as e:
            self.logger.error(f"  点击获取URL失败: {e}")
            return ""

    def _search_keyword(self, page, keyword: str, retry_count: int = 0) -> List[Dict]:
        """搜索单个关键词（交互式搜索）"""
        results = []
        max_retries = 2

        try:
            self.logger.info(f"[{keyword}] 打开虎嗅首页")
            page.goto(self.base_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector(".search-btn", timeout=20000)
            self._human_delay(page, 1200, 2000)

            # 检查验证码
            if self._check_captcha(page):
                self.logger.warning(f"[{keyword}] 检测到验证码保护")
                if retry_count < max_retries:
                    self.logger.info(f"[{keyword}] 等待10秒后重试...")
                    time.sleep(10)
                    return self._search_keyword(page, keyword, retry_count + 1)
                else:
                    self.logger.error(f"[{keyword}] 重试次数用尽，跳过此关键词")
                    return results

            # 展开搜索框并输入关键词
            self.logger.info(f"[{keyword}] 点击搜索图标")
            if not self._open_search_box(page):
                self.logger.error(f"[{keyword}] 无法展开搜索框")
                return results

            self.logger.debug(f"[{keyword}] 等待搜索输入框")
            try:
                page.wait_for_selector('input[placeholder="搜索文章"]', timeout=10000)
            except Exception:
                self.logger.error(f"[{keyword}] 未找到搜索输入框")
                return results

            search_input = page.locator('input[placeholder="搜索文章"]').first

            # 输入关键词并搜索
            self.logger.info(f"[{keyword}] 输入关键词并搜索")
            search_input.click()
            self._human_delay(page, 300, 800)
            search_input.fill("")
            search_input.type(keyword, delay=random.randint(80, 180))
            self._human_delay(page, 600, 1200)
            search_input.press("Enter")

            # 等待结果加载
            self._human_delay(page, 2000, 3500)

            # 提取结果
            results = self._extract_search_results(page)
            self.logger.info(f"[{keyword}] 成功提取 {len(results)} 条")

            # 截图调试
            try:
                page.screenshot(path=f"output/logs/huxiu_{keyword}_search.png")
                self.logger.debug(f"[{keyword}] 已保存搜索结果截图")
            except Exception:
                pass

            # 过滤：关键词匹配 + 时间窗口
            filtered_results = []
            for item in results:
                title = item.get('title', '')
                time_str = item.get('time', '')

                if not self._matches_keywords(title):
                    self.logger.debug(f"[{keyword}] 关键词过滤: {title[:50]}...")
                    continue

                if not self._is_in_time_window(time_str):
                    self.logger.debug(f"[{keyword}] 时间过滤: {title[:50]}... (时间: {time_str})")
                    continue

                filtered_results.append(item)

            self.logger.info(f"[{keyword}] 过滤后: {len(filtered_results)}/{len(results)} 条")

            # 对过滤后的结果点击获取真实 URL
            for item in filtered_results:
                title = item.get('title', '')
                if title:
                    url = self._get_url_by_click(page, title)
                    if url:
                        item['url'] = url

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
                        if 'articleDetail' in initial_state:
                            article = initial_state['articleDetail']
                        elif 'detail' in initial_state:
                            article = initial_state['detail'].get('article', {})
                        elif 'article' in initial_state:
                            article = initial_state['article']

                    if article:
                        content = article.get('content', '')
                        if content:
                            return self._clean_html_content(content)
                except:
                    pass

            # 从 DOM 提取内容
            content = page.evaluate("""() => {
                const selectors = [
                    '.article-content',
                    '.article-content-wrap',
                    '.content-detail',
                    '.article-detail',
                    'article',
                    '.main-content',
                    '#content',
                    '.article-text',
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

            # 检测内容限制（虎嗅部分文章需要登录/会员）
            if content and ("登录查看全文" in content or "成为会员，解锁全文" in content):
                self.logger.warning(f"内容被限制，仅保存摘要: {url}")
                return ""

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
        """采集数据"""
        all_items = []

        self.logger.info(f"开始采集虎嗅 - 关键词: {self.keywords}")
        self.logger.info(self.logger_info)  # 打印时间窗口信息

        # 创建批次目录
        now = datetime.now()
        date_dir = now.strftime('%Y/%m/%d')
        batch_name = now.strftime(f'{self.source_code}_%Y%m%d_%H%M%S')
        self._batch_dir = f"{self.output_dir}/{self.source_code}/{date_dir}/{batch_name}"
        os.makedirs(self._batch_dir, exist_ok=True)

        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            # 与 test.py 保持一致的最小浏览器配置
            browser = p.chromium.launch(headless=False)

            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                locale='zh-CN',
                timezone_id='Asia/Shanghai',
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

        if self.captcha_detected:
            self.logger.warning("虎嗅网触发了验证码保护，本次采集提前结束")

        self.logger.info(f"采集完成: 共 {len(all_items)} 条")
        return all_items


def main():
    """测试运行"""
    # 支持环境变量配置时间窗口（默认24小时）
    hours_window = int(os.getenv('HUXIU_HOURS_WINDOW', '1000'))

    crawler = HuxiuCrawler(hours_window=hours_window)
    items = crawler.run()

    print(f"\n{'='*70}")
    print(f"虎嗅采集结果: {len(items)} 条")
    print(f"时间窗口: 过去{hours_window}小时")
    print(f"截止时间: {crawler.cutoff_time.strftime('%Y-%m-%d %H:%M')}")
    if crawler.captcha_detected:
        print("注意: 虎嗅网触发了验证码保护")
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
