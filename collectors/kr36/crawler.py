# -*- coding: utf-8 -*-
"""
36氪采集器
使用 Playwright 模拟浏览器获取搜索结果
支持多关键词搜索、详情页正文抓取、AI 摘要
参考虎嗅采集器交互模式
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import re
import random
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

# 尝试导入 stealth，如果未安装则给出提示
try:
    from playwright_stealth import stealth_sync
    STEALTH_AVAILABLE = True
except ImportError:
    STEALTH_AVAILABLE = False


class Kr36Crawler(BaseCrawler):
    """36氪采集器
    支持多关键词搜索、时间窗口过滤（默认24小时）、详情页正文抓取、AI 摘要
    采用模拟手动检索方式：首页→点击搜索→输入关键词→提取结果
    """

    source_name = "36氪"
    source_code = "kr36"
    credibility_base = "【媒体报道】"

    def __init__(self, enable_summary: bool = None, hours_window: int = None):
        """
        初始化

        Args:
            enable_summary: 是否启用 AI 摘要
            hours_window: 时间窗口（小时），默认24小时
        """
        # 从配置读取参数
        config = COLLECTORS.get('kr36', {})
        self.keywords = config.get('keywords', ['金山办公'])
        self.max_items_per_keyword = 5  # 每个关键词最多采集条数

        # 时间窗口（默认24小时）
        self.hours_window = hours_window or config.get('hours_window', 24)
        self.cutoff_time = datetime.now() - timedelta(hours=self.hours_window)
        self.logger_info = f"时间窗口: 过去{self.hours_window}小时 ({self.cutoff_time.strftime('%Y-%m-%d %H:%M')} 至今)"

        # 从配置读取 enable_summary
        if enable_summary is None:
            enable_summary = config.get('enable_summary', True)

        super().__init__(enable_summary=enable_summary)

        self.base_url = "https://36kr.com"

        # 已抓取的URL集合（用于去重）
        self.seen_urls: set[str] = set()

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

    def _parse_kr36_time(self, time_str: str) -> datetime:
        """
        解析36氪的时间字符串为 datetime
        支持格式: 时间戳(毫秒), ISO格式, YYYY-MM-DD
        解析失败返回当前时间（保留数据）
        """
        if not time_str:
            self.logger.warning(f"⚠️ 时间解析失败(空值)，已回退到今日时间")
            return datetime.now()

        # 时间戳（毫秒）
        if isinstance(time_str, (int, float)) or (isinstance(time_str, str) and time_str.isdigit()):
            try:
                timestamp = int(time_str) / 1000  # 毫秒转秒
                return datetime.fromtimestamp(timestamp)
            except:
                pass

        # ISO格式
        try:
            return datetime.fromisoformat(time_str.replace('Z', '+00:00'))
        except:
            pass

        # 尝试正则匹配日期
        match = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', str(time_str))
        if match:
            year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            return datetime(year, month, day)

        self.logger.warning(f"⚠️ 时间解析失败('{time_str}')，已回退到今日时间")
        return datetime.now()

    def _parse_time(self, time_str: str) -> str:
        """解析时间字符串，返回 YYYY-MM-DD 格式（兼容旧方法）"""
        dt = self._parse_kr36_time(time_str)
        return dt.strftime('%Y-%m-%d')

    def _human_delay(self, page, min_ms: int = 500, max_ms: int = 1500):
        """模拟真人操作间隔"""
        page.wait_for_timeout(random.randint(min_ms, max_ms))

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

    def _click_search_button(self, page) -> bool:
        """点击右上角搜索按钮展开搜索框。"""
        self.logger.info("  尝试点击搜索按钮...")
        selectors = [
            'div.search-button',
            '.search-button',
            '.search-icon',
            'header [class*="search"]',
        ]
        for sel in selectors:
            btn = page.locator(sel).first
            try:
                if btn.is_visible():
                    btn.click()
                    self._human_delay(page, 600, 1200)
                    return True
            except Exception:
                continue
        return False

    def _extract_search_results_from_page(self, page, keyword: str) -> list[dict]:
        """从当前搜索页面提取结果"""
        results = []

        try:
            # 等待搜索结果加载
            self._human_delay(page, 2000, 3000)

            # 方式1：从 window.__INITIAL_STATE__ 提取
            initial_state = page.evaluate('''() => {
                return window.__INITIAL_STATE__ || window.initialState || null;
            }''')

            if initial_state:
                self.logger.info(f"[{keyword}] 找到 initialState")
                # 尝试多种可能的路径
                search_data = None
                if isinstance(initial_state, dict):
                    if 'search' in initial_state:
                        search_data = initial_state['search'].get('searchResult', {}).get('data', [])
                    elif 'articleSearch' in initial_state:
                        search_data = initial_state['articleSearch'].get('data', [])
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
                            item_id = item.get('id', '') or item.get('itemId', '')
                            if item_id:
                                url = f"{self.base_url}/p/{item_id}"
                        elif not url.startswith('http'):
                            url = f"{self.base_url}{url}"

                        results.append({
                            'title': title,
                            'url': url,
                            'summary': item.get('summary', '').strip(),
                            'time': item.get('published_at', '') or item.get('publishTime', ''),
                            'item_id': item.get('id', '') or item.get('itemId', ''),
                        })

            # 方式2：如果 initialState 没有，尝试从 DOM 提取
            if not results:
                self.logger.info(f"[{keyword}] 尝试从 DOM 提取结果")

                # 等待搜索结果元素出现
                try:
                    page.wait_for_selector('.search-result-list, .article-list, .kr-article-list, [class*="article"]', timeout=10000)
                except:
                    pass

                # 提取 DOM 数据
                dom_results = page.evaluate('''() => {
                    const results = [];

                    // 尝试多种可能的选择器
                    const selectors = [
                        '.search-result-list .article-item',
                        '.article-list .article-item',
                        '.kr-article-list .article-item',
                        '[class*="search"] [class*="article"]',
                        '[class*="article-list"] > div',
                        '.article-card',
                    ];

                    for (const selector of selectors) {
                        const items = document.querySelectorAll(selector);
                        if (items.length > 0) {
                            items.forEach(item => {
                                const titleEl = item.querySelector('h1, h2, h3, h4, .title, [class*="title"]');
                                const linkEl = item.querySelector('a[href*="/p/"]') || item.querySelector('a');
                                const summaryEl = item.querySelector('.summary, .description, [class*="summary"], [class*="desc"]');
                                const timeEl = item.querySelector('.time, .date, [class*="time"], [class*="date"]');

                                if (titleEl && linkEl) {
                                    results.push({
                                        title: titleEl.textContent?.trim() || '',
                                        url: linkEl.href || '',
                                        summary: summaryEl?.textContent?.trim() || '',
                                        time: timeEl?.textContent?.trim() || '',
                                        item_id: '',
                                    });
                                }
                            });
                            if (results.length > 0) break;
                        }
                    }

                    return results;
                }''')

                if dom_results and len(dom_results) > 0:
                    self.logger.info(f"[{keyword}] 从 DOM 提取到 {len(dom_results)} 条结果")
                    results = dom_results

        except Exception as e:
            self.logger.error(f"[{keyword}] 提取搜索结果失败: {e}")

        return results

    def _search_keyword(self, page, keyword: str, retry_count: int = 0) -> list[dict]:
        """搜索单个关键词（交互式搜索）

        流程：访问首页 → 点击搜索按钮 → 输入关键词 → 回车搜索 → 提取结果
        """
        results = []
        max_retries = 2

        try:
            # Step 1: 访问首页（随机延迟后访问，模拟真实用户）
            initial_delay = random.uniform(2, 5)
            self.logger.info(f"[{keyword}] 等待 {initial_delay:.1f} 秒后访问首页...")
            time.sleep(initial_delay)

            self.logger.info(f"[{keyword}] 访问首页: {self.base_url}")
            page.goto(self.base_url, wait_until="networkidle", timeout=30000)
            self.logger.info(f"[{keyword}] 页面 networkidle，等待内容渲染...")
            self._human_delay(page, 3000, 5000)  # 增加等待时间，确保动态内容加载

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

            # Step 2: 点击搜索按钮
            self.logger.info(f"[{keyword}] 点击搜索按钮")
            if not self._click_search_button(page):
                self.logger.error(f"[{keyword}] 无法找到搜索按钮")
                # 备用方案：直接访问搜索页
                self.logger.info(f"[{keyword}] 尝试直接访问搜索页...")
                search_url = f"{self.base_url}/search/articles/{quote(keyword)}"
                page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                self._human_delay(page, 2000, 3000)
            else:
                # Step 3: 等待搜索输入框出现
                self.logger.info(f"[{keyword}] 等待搜索输入框")
                try:
                    page.wait_for_selector('input[type="text"][placeholder*="关键词"]', timeout=10000)
                except Exception:
                    self.logger.error(f"[{keyword}] 未找到搜索输入框")
                    return results

                search_input = page.locator('input[type="text"][placeholder*="关键词"]').first

                # Step 4: 输入关键词并搜索（模拟真人打字，更长的思考时间）
                self.logger.info(f"[{keyword}] 输入关键词并搜索")
                search_input.click()
                self._human_delay(page, 800, 1500)  # 点击后停顿，像真人在看输入框
                search_input.fill("")
                search_input.type(keyword, delay=random.randint(100, 250))  # 打字更慢
                self._human_delay(page, 1500, 3000)  # 输入完成后思考，不立即搜索

                # 尝试点击搜索按钮提交，而不是按 Enter（更像真人行为）
                try:
                    search_btn = page.locator('button[type="submit"], .search-submit, [class*="search"] button').first
                    if search_btn.count() > 0 and search_btn.is_visible():
                        self.logger.info(f"[{keyword}] 点击搜索按钮提交")
                        search_btn.click()
                    else:
                        self.logger.info(f"[{keyword}] 使用回车提交搜索")
                        search_input.press("Enter")
                except:
                    search_input.press("Enter")

                # Step 5: 等待结果加载（更长的时间，避免被检测为快速请求）
                self._human_delay(page, 4000, 6000)

            # Step 6: 提取搜索结果
            results = self._extract_search_results_from_page(page, keyword)
            self.logger.info(f"[{keyword}] 成功提取 {len(results)} 条")

            # 截图调试
            try:
                page.screenshot(path=f"output/logs/kr36_{keyword}_search.png")
            except Exception:
                pass

            # 过滤：关键词匹配 + 时间窗口
            filtered_results = []
            keyword_lower = keyword.lower()

            for item in results:
                title = item.get('title', '')
                time_str = item.get('time', '')

                # 检查关键词匹配
                if keyword_lower not in title.lower():
                    self.logger.debug(f"[{keyword}] 关键词过滤: {title[:50]}...")
                    continue

                # 检查时间窗口
                item_datetime = self._parse_kr36_time(time_str)
                if item_datetime < self.cutoff_time:
                    self.logger.debug(f"[{keyword}] 时间过滤: {title[:50]}... (时间: {item_datetime.strftime('%Y-%m-%d')})")
                    continue

                filtered_results.append(item)

            self.logger.info(f"[{keyword}] 过滤后: {len(filtered_results)}/{len(results)} 条 (时间窗口: {self.hours_window}小时)")
            results = filtered_results

        except Exception as e:
            self.logger.error(f"[{keyword}] 搜索失败: {e}")

        return results

    def _fetch_content_with_playwright(self, page, url: str) -> str:
        """使用 Playwright 获取详情页内容"""
        try:
            self.logger.info(f"  访问详情页: {url[:60]}...")

            page.goto(url, wait_until='networkidle', timeout=30000)
            page.wait_for_load_state('domcontentloaded', timeout=10000)
            time.sleep(2)  # 等待内容加载

            # 尝试从 window.__INITIAL_STATE__ 提取
            initial_state = page.evaluate('''() => {
                return window.__INITIAL_STATE__ || window.initialState || null;
            }''')

            if initial_state:
                try:
                    # 尝试多种可能的路径
                    article = None
                    if isinstance(initial_state, dict):
                        if 'article' in initial_state:
                            article = initial_state['article'].get('articleDetail', {}).get('articleDetailData', {}).get('data', {})
                        elif 'articleDetail' in initial_state:
                            article = initial_state['articleDetail']

                    if article:
                        content = article.get('content', '')
                        if content:
                            return self._clean_html_content(content)
                except:
                    pass

            # 从 DOM 提取内容
            content = page.evaluate('''() => {
                const selectors = [
                    '.article-content',
                    '.article-detail-content',
                    '.content-detail',
                    '[class*="article-content"]',
                    '[class*="article-detail"]',
                    'article',
                    '.kr-article-body',
                ];

                for (const selector of selectors) {
                    const el = document.querySelector(selector);
                    if (el && el.textContent.length > 100) {
                        return el.innerHTML;
                    }
                }
                return '';
            }''')

            if content:
                return self._clean_html_content(content)

            return ""

        except Exception as e:
            self.logger.error(f"  获取详情页失败: {url} - {e}")
            return ""

    def _apply_stealth(self, page):
        """应用 stealth 伪装，隐藏浏览器自动化特征"""
        if STEALTH_AVAILABLE:
            try:
                stealth_sync(page)
                self.logger.info("  已应用 playwright-stealth 伪装")
            except Exception as e:
                self.logger.warning(f"  playwright-stealth 应用失败: {e}")
        else:
            self.logger.warning("  playwright-stealth 未安装，使用内置脚本伪装")

        # 额外的伪装脚本（覆盖更多检测点）
        page.add_init_script("""
            // 删除 webdriver 标志
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

            // 伪造 plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [
                    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
                    { name: 'Native Client', filename: 'native_client.nmf' },
                    { name: 'Widevine Content Decryption Module', filename: 'widevinecdmadapter.dll' }
                ]
            });

            // 伪造 chrome 对象
            window.chrome = {
                runtime: {
                    OnInstalledReason: { CHROME_UPDATE: 'chrome_update', EXTENSION_UPDATE: 'extension_update', INSTALL: 'install' },
                    OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
                    PlatformArch: { ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', MIPS64EL: 'mips64el', MIPSEL: 'mipsel', X86_32: 'x86-32', X86_64: 'x86-64' },
                    PlatformNaclArch: { ARM: 'arm', MIPS: 'mips', MIPS64: 'mips64', MIPS64EL: 'mips64el', MIPSEL: 'mipsel', X86_32: 'x86-32', X86_64: 'x86-64' },
                    PlatformOs: { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' },
                    RequestUpdateCheckStatus: { NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available' }
                },
                loadTimes: () => {
                    return {
                        commitLoadTime: performance.timing.domContentLoadedEventStart / 1000,
                        connectionInfo: 'h2',
                        finishDocumentLoadTime: performance.timing.domContentLoadedEventEnd / 1000,
                        finishLoadTime: performance.timing.loadEventEnd / 1000,
                        firstPaintAfterLoadTime: 0,
                        firstPaintTime: performance.timing.domContentLoadedEventStart / 1000,
                        navigationType: 'Other',
                        npnNegotiatedProtocol: 'h2',
                        requestTime: performance.timing.requestStart / 1000,
                        startLoadTime: performance.timing.domLoading / 1000,
                        wasAlternateProtocolAvailable: false,
                        wasFetchedViaSpdy: true,
                        wasNpnNegotiated: true
                    };
                },
                csi: () => {
                    return {
                        onloadT: performance.timing.loadEventEnd,
                        pageE: performance.timing.domContentLoadedEventEnd,
                        startE: performance.timing.navigationStart,
                        tran: performance.timing.fetchStart - performance.timing.navigationStart
                    };
                }
            };

            // 伪造 notification 权限
            const originalQuery = window.Notification.requestPermission;
            window.Notification.requestPermission = function(callback) {
                if (callback) callback('default');
                return Promise.resolve('default');
            };
            Object.defineProperty(window.Notification, 'permission', { get: () => 'default' });

            // 伪造 permissions
            const originalPermissions = window.navigator.permissions;
            window.navigator.permissions = {
                query: (parameters) => {
                    return Promise.resolve({ state: 'prompt', onchange: null });
                }
            };

            // 修改 canvas 指纹相关
            const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
            const originalGetImageData = CanvasRenderingContext2D.prototype.getImageData;

            // 修改 WebGL 参数
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) {
                    return 'Intel Inc.';
                }
                if (parameter === 37446) {
                    return 'Intel Iris OpenGL Engine';
                }
                return getParameter(parameter);
            };

            // 伪造 deviceMemory
            Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });

            // 伪造 hardwareConcurrency
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });

            // 伪造 languages
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });

            // 伪造 platform
            Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });

            // 伪造 vendor
            Object.defineProperty(navigator, 'vendor', { get: () => 'Google Inc.' });

            // 修改 iframe 创建（某些检测会检查 iframe）
            const originalCreateElement = document.createElement;
            document.createElement = function(tagName) {
                const element = originalCreateElement.call(this, tagName);
                if (tagName.toLowerCase() === 'iframe') {
                    try {
                        Object.defineProperty(element.contentWindow.navigator, 'webdriver', { get: () => undefined });
                    } catch (e) {}
                }
                return element;
            };

            // 禁用 AutomationControlled 特征（关键！）
            Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });

            // 伪造 PDF 查看器支持
            Object.defineProperty(navigator, 'pdfViewerEnabled', { get: () => true });

            // 伪造 Bluetooth
            Object.defineProperty(navigator, 'bluetooth', { get: () => undefined });

            // 伪造 USB
            Object.defineProperty(navigator, 'usb', { get: () => undefined });

            // 添加 Notification 支持检查
            Object.defineProperty(window, 'webkitNotifications', { get: () => window.Notification });

            // 修改 toString 避免暴露
            const originalToString = Function.prototype.toString;
            Function.prototype.toString = function() {
                if (this === Function.prototype.toString) {
                    return 'function toString() { [native code] }';
                }
                return originalToString.call(this);
            };

            // 覆盖 console 所有方法
            ['log', 'warn', 'error', 'info', 'debug', 'trace'].forEach(method => {
                console[method] = function(){};
            });

            // 清理自动化特征
            delete navigator.__proto__.webdriver;
        """)

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

        self.logger.info(f"开始采集36氪 - 关键词: {self.keywords}")
        self.logger.info(self.logger_info)

        # 创建批次目录
        now = datetime.now()
        date_dir = now.strftime('%Y/%m/%d')
        batch_name = now.strftime(f'{self.source_code}_%Y%m%d_%H%M%S')
        if not self._batch_dir:
            self._batch_dir = f"{self.output_dir}/{self.source_code}/{date_dir}/{batch_name}"
        os.makedirs(self._batch_dir, exist_ok=True)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                locale='zh-CN',
                timezone_id='Asia/Shanghai',
                # 禁用自动化特征
                bypass_csp=True,
            )

            page = context.new_page()

            # 应用 stealth 伪装
            self._apply_stealth(page)

            try:
                for keyword in self.keywords:
                    self.logger.info(f"搜索关键词: {keyword}")

                    # 使用新的交互式搜索方法
                    results = self._search_keyword(page, keyword)

                    # 限制数量
                    results = results[:self.max_items_per_keyword]

                    for idx, news in enumerate(results, 1):
                        url = news['url']

                        # 去重检查
                        if url in self.seen_urls:
                            self.logger.info(f"[{idx}/{len(results)}] 跳过重复: {news['title'][:50]}...")
                            continue
                        self.seen_urls.add(url)

                        self.logger.info(f"[{idx}/{len(results)}] 处理: {news['title'][:50]}...")

                        # 获取正文
                        content = self._fetch_content_with_playwright(page, url)

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

                    time.sleep(2)

            finally:
                context.close()
                browser.close()

        self.logger.info(f"采集完成: 共 {len(all_items)} 条")
        return all_items


def main():
    """测试运行"""
    import os

    hours_window = int(os.getenv('KR36_HOURS_WINDOW', '1000'))

    crawler = Kr36Crawler(hours_window=hours_window)
    items = crawler.run()

    print(f"\n{'='*70}")
    print(f"36氪采集结果: {len(items)} 条")
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
