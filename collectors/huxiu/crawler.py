# -*- coding: utf-8 -*-
"""
虎嗅采集器
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
import random
import sys
from urllib.parse import urljoin
sys.path.append(str(Path(__file__).parent.parent.parent))

from collectors.base import BaseCrawler
from models.news import NewsItem
from config.settings import CATEGORIES, COLLECTORS

# 尝试导入 playwright-stealth
try:
    from playwright_stealth.stealth import Stealth
    STEALTH_AVAILABLE = True
except ImportError:
    STEALTH_AVAILABLE = False


class HuxiuCrawler(BaseCrawler):
    """虎嗅采集器"""

    source_name = "虎嗅"
    source_code = "huxiu"
    credibility_base = "【媒体报道】"

    # 虎嗅文章 id 范围（用于备选采集）
    # 最新的文章ID通常在 3000000 以上
    LATEST_ARTICLE_ID_MIN = 3100000
    LATEST_ARTICLE_ID_MAX = 3200000

    def __init__(self, enable_summary: bool = None, hours_window: int = 24, use_direct_ids: bool = False):
        # 从配置读取参数
        config = COLLECTORS.get('huxiu', {})
        self.keywords = config.get('keywords', ['金山办公'])
        self.max_items_per_keyword = 50  # 提高上限，过滤后保留足够数据
        self.use_direct_ids = use_direct_ids  # 是否使用直接访问文章ID的方式

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
        """如果搜索框未直接显示，尝试点击搜索图标展开。"""
        self.logger.info("  搜索框未直接显示，尝试点击搜索图标...")
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

    def _find_search_input(self, page):
        """尝试多种方式定位搜索输入框。"""
        # 1) 精确匹配虎嗅搜索框 placeholder
        locator = page.locator('input[placeholder="搜索文章"]')
        if locator.count() > 0 and locator.first.is_visible():
            return locator.first

        # 2) 通过 placeholder 模糊查找
        for placeholder in ["搜索", "搜", "请输入关键词", "Search"]:
            locator = page.get_by_placeholder(placeholder)
            if locator.count() > 0 and locator.first.is_visible():
                return locator.first

        # 3) 通过 role=searchbox 查找
        locator = page.get_by_role("searchbox")
        if locator.count() > 0 and locator.first.is_visible():
            return locator.first

        # 4) 通过通用 input 选择器过滤
        locator = page.locator('input[type="text"], input[type="search"]').filter(has_text=re.compile("搜索|搜"))
        if locator.count() > 0 and locator.first.is_visible():
            return locator.first

        return None

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

    def _search_keyword(self, page, keyword: str, retry_count: int = 0) -> List[Dict]:
        """搜索单个关键词（交互式搜索）"""
        results = []
        max_retries = 2

        try:
            self.logger.info(f"[{keyword}] 打开虎嗅首页")
            page.goto(self.base_url, wait_until="domcontentloaded", timeout=30000)
            self._human_delay(page, 1500, 2500)

            # 调试：记录首页状态
            home_title = page.title()
            home_url = page.url
            self.logger.info(f"[{keyword}] 首页标题: {home_title}")
            self.logger.info(f"[{keyword}] 首页URL: {home_url}")
            try:
                os.makedirs("output/logs", exist_ok=True)
                page.screenshot(path=f"output/logs/huxiu_{keyword}_home.png")
                self.logger.info(f"[{keyword}] 首页截图已保存")
            except Exception as e:
                self.logger.debug(f"首页截图失败: {e}")

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

            # 展开搜索框
            search_input = self._find_search_input(page)
            if not search_input:
                if not self._open_search_box(page):
                    self.logger.error(f"[{keyword}] 无法展开搜索框")
                    return results
                search_input = self._find_search_input(page)

            if not search_input:
                self.logger.error(f"[{keyword}] 无法定位搜索输入框")
                return results

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
            results = filtered_results

        except Exception as e:
            self.logger.error(f"[{keyword}] 搜索失败: {e}")

        return results

    def _fetch_recent_articles(self, page) -> List[Dict]:
        """
        备选方案：直接访问最近的文章ID
        当搜索功能被验证码拦截时使用
        """
        results = []
        self.logger.info("使用备选方案：直接访问最近文章")

        # 虎嗅文章ID范围（最近的文章ID）
        # 需要根据实际情况调整这个范围
        recent_ids = range(self.LATEST_ARTICLE_ID_MIN, self.LATEST_ARTICLE_ID_MAX, 100)  # 步长100，减少请求

        for article_id in recent_ids:
            if self.captcha_detected:
                break

            url = f"{self.base_url}/article/{article_id}.html"
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2000)

                # 检查是否触发验证码
                if self._check_captcha(page):
                    self.logger.warning(f"[ID:{article_id}] 触发验证码，停止备选采集")
                    self.captcha_detected = True
                    break

                # 检查页面是否存在（404检查）
                title = page.title()
                if '404' in title or '找不到' in title or '不存在' in title:
                    continue

                # 提取文章信息
                article_data = page.evaluate('''() => {
                    const data = {
                        title: document.title?.replace(' - 虎嗅网', '') || '',
                        time: '',
                        summary: ''
                    };

                    // 尝试从 meta 标签获取时间
                    const timeMeta = document.querySelector('meta[property="article:published_time"]');
                    if (timeMeta) {
                        data.time = timeMeta.content;
                    }

                    // 尝试从页面元素获取时间
                    if (!data.time) {
                        const timeEl = document.querySelector('.article-time, .publish-time, [class*="time"]');
                        if (timeEl) {
                            data.time = timeEl.textContent || '';
                        }
                    }

                    // 尝试获取摘要
                    const descMeta = document.querySelector('meta[name="description"]');
                    if (descMeta) {
                        data.summary = descMeta.content || '';
                    }

                    return data;
                }''')

                if article_data and article_data.get('title'):
                    title = article_data['title']

                    # 检查是否匹配关键词
                    if self._matches_keywords(title):
                        results.append({
                            'title': title,
                            'url': url,
                            'time': article_data.get('time', ''),
                            'summary': article_data.get('summary', ''),
                        })
                        self.logger.info(f"[ID:{article_id}] 找到匹配文章: {title[:40]}...")

                # 随机延迟，避免请求过快
                time.sleep(2)

            except Exception as e:
                self.logger.debug(f"[ID:{article_id}] 获取失败: {e}")
                continue

        self.logger.info(f"备选方案共找到 {len(results)} 条匹配文章")
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

        captcha_detected = False

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

                    # 如果检测到验证码，标记并跳出
                    if not results:
                        page_title = page.title()
                        if "验证" in page_title:
                            self.captcha_detected = True
                            captcha_detected = True
                            break

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

                # 如果搜索被验证码拦截，尝试备选方案
                if captcha_detected and not all_items and self.use_direct_ids:
                    self.logger.info("搜索被验证码拦截，尝试备选采集方案...")
                    backup_results = self._fetch_recent_articles(page)

                    for news in backup_results:
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
                            raw_data={'source': 'direct_id'},
                        )

                        # AI 摘要
                        if content and len(content.strip()) > 50:
                            ai_summary, summary_time = self.generate_summary(news['title'], content)
                            if ai_summary:
                                item.summary = ai_summary
                                item.summary_generated_at = summary_time

                        all_items.append(item)
                        time.sleep(1)

            finally:
                context.close()
                browser.close()

        if captcha_detected:
            self.logger.warning("虎嗅网触发了验证码保护，本次采集提前结束")

        self.logger.info(f"采集完成: 共 {len(all_items)} 条")
        return all_items


def main():
    """测试运行"""
    import os

    # 支持环境变量配置时间窗口（默认24小时）
    hours_window = int(os.getenv('HUXIU_HOURS_WINDOW', '1560'))
    # 是否启用备选方案（直接访问文章ID）
    use_direct_ids = os.getenv('HUXIU_USE_DIRECT_IDS', 'false').lower() == 'true'

    crawler = HuxiuCrawler(hours_window=hours_window, use_direct_ids=use_direct_ids)
    items = crawler.run()

    print(f"\n{'='*70}")
    print(f"虎嗅采集结果: {len(items)} 条")
    print(f"时间窗口: 过去{hours_window}小时")
    print(f"截止时间: {crawler.cutoff_time.strftime('%Y-%m-%d %H:%M')}")
    if crawler.captcha_detected:
        print("注意: 虎嗅网触发了验证码保护")
        print("建议: 1) 稍后重试 2) 设置 HUXIU_USE_DIRECT_IDS=true 启用备选方案")
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
