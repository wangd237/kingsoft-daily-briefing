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

import sys
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
        """检查是否触发了验证码，返回True表示触发了验证码"""
        try:
            page_title = page.title()
            page_content = page.content()

            captcha_indicators = ['验证', 'captcha', 'aliyunCaptcha', '安全验证', '访问验证', '点击验证']
            for indicator in captcha_indicators:
                if indicator in page_title or indicator in page_content:
                    return True

            # 检查URL是否跳转到验证页面
            current_url = page.url
            if 'captcha' in current_url.lower() or 'verify' in current_url.lower():
                return True

            return False
        except:
            return False

    def _search_keyword(self, page, keyword: str, retry_count: int = 0) -> List[Dict]:
        """搜索单个关键词"""
        results = []
        max_retries = 2

        try:
            # 方案1: 直接访问搜索URL（更简单，减少交互）
            search_url = f"{self.base_url}/search?keyword={keyword}&s=relevance"
            self.logger.info(f"[{keyword}] 访问搜索页: {search_url}")

            page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            # 检查是否触发了验证码
            if self._check_captcha(page):
                self.logger.warning(f"[{keyword}] 检测到验证码保护")
                page.screenshot(path=f"output/logs/huxiu_{keyword}_captcha.png")

                if retry_count < max_retries:
                    self.logger.info(f"[{keyword}] 等待10秒后重试...")
                    time.sleep(10)
                    return self._search_keyword(page, keyword, retry_count + 1)
                else:
                    self.logger.error(f"[{keyword}] 重试次数用尽，跳过此关键词")
                    return results

            # 等待搜索结果加载 - 使用更宽松的条件
            try:
                # 等待页面稳定
                page.wait_for_load_state('networkidle', timeout=10000)
            except:
                pass

            page.wait_for_timeout(2000)

            # 截图调试
            page.screenshot(path=f"output/logs/huxiu_{keyword}_search.png")

            # 方式1：从 window.__INITIAL_STATE__ 提取
            initial_state = page.evaluate('''() => {
                return window.__INITIAL_STATE__ || window.initialState || null;
            }''')

            if initial_state:
                try:
                    self.logger.info(f"[{keyword}] 找到 initialState")
                    search_data = None
                    if isinstance(initial_state, dict):
                        # 虎嗅可能的数据结构
                        if 'search' in initial_state:
                            search_data = initial_state['search'].get('articleList', [])
                        elif 'searchResult' in initial_state:
                            search_data = initial_state['searchResult'].get('list', [])
                        elif 'articleList' in initial_state:
                            search_data = initial_state['articleList']

                    if search_data and isinstance(search_data, list):
                        self.logger.info(f"[{keyword}] 从 initialState 提取到 {len(search_data)} 条结果")
                        for item in search_data:
                            if not isinstance(item, dict):
                                continue
                            title = item.get('title', '').strip()
                            if not title:
                                continue

                            url = item.get('url', '') or item.get('shareUrl', '')
                            if not url:
                                item_id = item.get('aid', '') or item.get('id', '')
                                if item_id:
                                    url = f"{self.base_url}/article/{item_id}.html"
                            elif not url.startswith('http'):
                                url = f"{self.base_url}{url}"

                            if title and url:
                                results.append({
                                    'title': title.strip(),
                                    'url': url,
                                    'time': item.get('time', '') or item.get('publishTime', '') or item.get('ctime', ''),
                                    'summary': item.get('summary', '') or item.get('brief', ''),
                                })
                except Exception as e:
                    self.logger.debug(f"[{keyword}] 解析 initialState 失败: {e}")

            # 方式2：从 DOM 提取
            if not results:
                self.logger.info(f"[{keyword}] 尝试从 DOM 提取结果")

                try:
                    # 滚动页面触发懒加载
                    page.evaluate('''() => { window.scrollTo(0, 500); }''')
                    time.sleep(1)

                    # 虎嗅搜索结果可能的选择器 - 基于实际页面结构
                    selectors = [
                        '.search-list .search-item',
                        '.article-list .article-item',
                        '.search-wrap .item',
                        '.search-result-item',
                        '.article-card',
                        '[class*="search"] [class*="item"]',
                        '.article-item',
                        '.search-content .item',
                        '.article-wrap',
                        '.search-result',
                        '.content-list .content-item',
                    ]

                    list_items = []
                    for selector in selectors:
                        try:
                            items = page.locator(selector).all()
                            if items and len(items) > 0:
                                self.logger.info(f"[{keyword}] 使用选择器 '{selector}' 找到 {len(items)} 个列表项")
                                list_items = items
                                break
                        except Exception:
                            continue

                    # 如果上述选择器都没有找到，尝试通过链接找
                    if not list_items:
                        # 获取所有包含文章链接的元素
                        article_links = page.locator('a[href*="/article/"]').all()
                        self.logger.info(f"[{keyword}] 使用文章链接选择器找到 {len(article_links)} 个链接")

                        # 过滤出包含标题的链接
                        for link in article_links[:20]:  # 限制数量
                            try:
                                title = link.text_content(timeout=100) or ''
                                if title.strip() and len(title.strip()) > 5:
                                    list_items.append(link)
                            except:
                                continue

                    self.logger.info(f"[{keyword}] 共处理 {len(list_items)} 个列表项")

                    for item in list_items[:self.max_items_per_keyword]:
                        try:
                            # 提取标题
                            title = ''
                            try:
                                # 直接获取元素文本
                                title = item.text_content(timeout=100) or ''
                                if not title.strip():
                                    # 尝试子元素
                                    for title_selector in ['.title', 'h2', 'h3', '.article-title', '.article-card-title']:
                                        try:
                                            title_el = item.locator(title_selector).first
                                            title = title_el.text_content(timeout=100) or ''
                                            if title.strip():
                                                break
                                        except:
                                            continue
                            except:
                                pass

                            # 提取链接
                            url = ''
                            try:
                                url = item.get_attribute('href') or ''
                            except:
                                try:
                                    link_el = item.locator('a').first
                                    url = link_el.get_attribute('href') or ''
                                except:
                                    pass

                            if url and not url.startswith('http'):
                                url = f"{self.base_url}{url}"

                            # 提取时间 - 从文本中匹配
                            pub_time = ''
                            item_text = ''
                            try:
                                item_text = item.text_content(timeout=100) or ''
                                # 从文本中提取时间
                                time_patterns = [
                                    r'(\d{4}-\d{2}-\d{2})',
                                    r'(\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2})',
                                    r'(\d+)\s*分钟前',
                                    r'(\d+)\s*小时前',
                                    r'(刚刚)',
                                    r'(昨天)',
                                ]
                                for pattern in time_patterns:
                                    match = re.search(pattern, item_text)
                                    if match:
                                        pub_time = match.group(0)
                                        break
                            except:
                                pass

                            # 提取摘要
                            summary = ''
                            for summary_selector in ['.summary', '.brief', '.desc', '.article-desc', '.content-desc']:
                                try:
                                    summary_el = item.locator(summary_selector).first
                                    summary = summary_el.text_content(timeout=100) or ''
                                    if summary.strip() and summary != title:
                                        break
                                except:
                                    continue

                            if title.strip() and url and '/article/' in url:
                                results.append({
                                    'title': title.strip(),
                                    'url': url,
                                    'time': pub_time.strip(),
                                    'summary': summary.strip(),
                                })
                        except Exception as item_e:
                            self.logger.debug(f"解析列表项失败: {item_e}")
                            continue

                except Exception as e:
                    self.logger.error(f"[{keyword}] DOM 提取失败: {e}")

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
            # 使用更真实的浏览器配置
            browser = p.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled']
            )

            # 更真实的浏览器上下文
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
                locale='zh-CN',
                timezone_id='Asia/Shanghai',
                extra_http_headers={
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'DNT': '1',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                }
            )

            # 添加 Cookie 和 localStorage 来模拟真实用户
            context.add_cookies([
                {'name': 'huxiu', 'value': '1', 'domain': '.huxiu.com', 'path': '/'}
            ])

            # 创建页面
            page = context.new_page()

            # 使用 playwright-stealth 隐藏自动化特征
            if STEALTH_AVAILABLE:
                Stealth().apply_stealth_sync(page)
                self.logger.info("已启用 playwright-stealth 反检测模式")
            else:
                # 备用：手动注入基础 stealth 脚本
                page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                """)
                self.logger.warning("playwright-stealth 未安装，使用基础反检测模式")

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
    hours_window = int(os.getenv('HUXIU_HOURS_WINDOW', '24'))
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
