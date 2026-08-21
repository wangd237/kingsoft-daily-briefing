# -*- coding: utf-8 -*-
"""
游侠网采集器
使用 Playwright 模拟浏览器获取搜索结果
支持多关键词搜索、详情页正文抓取、AI 摘要
"""
import sys
# 强制 UTF-8 输出：用 reconfigure 改编码，不替换 sys.stdout/stderr 对象
# （替换后原对象被 GC 会关闭共享 buffer，导致 print 抛 "I/O operation on closed file"）
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

from datetime import datetime, timedelta
from typing import List, Dict, Set, Optional
from pathlib import Path
import time
import os
import re

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from collectors.base import BaseCrawler
from models.news import NewsItem
from config.settings import CATEGORIES, COLLECTORS


class Ali213Crawler(BaseCrawler):
    """游侠网采集器

    页面结构：
    - 首页：https://www.ali213.net/
    - 搜索框：首页顶部 form#cse-search-box
    - 搜索流程：
      1. 首页点击搜索框输入关键词
      2. 按Enter或点击搜索按钮
      3. 在新标签页打开搜索结果
    - 搜索结果页：https://so.ali213.net/s/s?sub=97&keyword=关键词
    - 新闻列表容器：<div class="newsModual"><ul class="newsList">
    - 列表项：<li>
    - 标题：<a class="glTitle"><span class="titleFront dj">单机</span>标题文字</a>
    - 摘要：<p class="newsContent">摘要文字</p>
    - 时间：<span class="authorTime"><i></i>2026-06-26</span>
    - 链接：<a class="glTitle" href="...">
    """

    source_name = "游侠网"
    source_code = "ali213"
    credibility_base = "【媒体报道】"

    def __init__(self, enable_summary: bool = None, hours_window: int = 168):
        # 从配置读取参数
        config = COLLECTORS.get('ali213', {})
        self.keywords = config.get('keywords', ['西山居', '金山世游'])
        self.max_items_per_keyword = 30  # 只爬当前页

        # 时间窗口（默认7天，游戏媒体更新频率较低）
        self.hours_window = hours_window
        self.cutoff_time = datetime.now() - timedelta(hours=hours_window)
        self.logger_info = f"时间窗口: 过去{hours_window}小时 ({self.cutoff_time.strftime('%Y-%m-%d %H:%M')} 至今)"

        # 从配置读取 enable_summary
        if enable_summary is None:
            enable_summary = config.get('enable_summary', True)

        super().__init__(enable_summary=enable_summary)

        self.base_url = "https://www.ali213.net"
        self.search_base_url = "https://so.ali213.net"

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
        支持格式：YYYY-MM-DD（如 2026-06-26）
        """
        if not time_str:
            return datetime.now().strftime('%Y-%m-%d')

        time_str = time_str.strip()

        # 匹配 YYYY-MM-DD
        match = re.search(r'(\d{4})-(\d{2})-(\d{2})', time_str)
        if match:
            return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"

        # 匹配 YYYY/MM/DD
        match = re.search(r'(\d{4})/(\d{2})/(\d{2})', time_str)
        if match:
            return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"

        # 匹配 YYYY年MM月DD日
        match = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', time_str)
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"

        return datetime.now().strftime('%Y-%m-%d')

    def _parse_ali213_time(self, time_str: str) -> datetime:
        """
        解析游侠网的时间格式为 datetime 对象
        支持格式：YYYY-MM-DD
        """
        if not time_str:
            return datetime.now()

        time_str = time_str.strip()

        # 匹配 YYYY-MM-DD
        match = re.search(r'(\d{4})-(\d{2})-(\d{2})', time_str)
        if match:
            year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            return datetime(year, month, day)

        # 匹配 YYYY/MM/DD
        match = re.search(r'(\d{4})/(\d{2})/(\d{2})', time_str)
        if match:
            year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            return datetime(year, month, day)

        return datetime.now()

    def _is_in_time_window(self, time_str: str) -> bool:
        """判断时间是否在过去指定小时内"""
        try:
            parsed_time = self._parse_ali213_time(time_str)
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
        从搜索结果页提取新闻内容
        """
        results = []

        try:
            # 等待新闻列表加载
            page.wait_for_selector('div.newsModual ul.newsList li', timeout=10000)
        except Exception:
            self.logger.debug("未找到新闻列表")
            return results

        # 获取所有新闻项
        items = page.locator('div.newsModual ul.newsList li').all()
        self.logger.info(f"找到 {len(items)} 条新闻")

        for item in items:
            try:
                # 提取标题
                title_el = item.locator('a.glTitle').first
                if title_el.count() == 0:
                    continue

                # 获取原始HTML来清理标签
                title_html = title_el.inner_html() or ''
                title_text = title_el.text_content() or ''
                title = title_text.strip()

                # 提取链接
                href = title_el.get_attribute('href') or ''
                if href and not href.startswith('http'):
                    href = f"{self.base_url}{href}" if href.startswith('/') else f"{self.base_url}/{href}"

                # 提取摘要
                summary = ''
                content_el = item.locator('p.newsContent').first
                if content_el.count() > 0:
                    summary = content_el.text_content() or ''
                    summary = summary.strip()

                # 提取时间
                time_str = ''
                time_el = item.locator('span.authorTime').first
                if time_el.count() > 0:
                    time_text = time_el.text_content() or ''
                    # 提取日期部分
                    match = re.search(r'(\d{4}-\d{2}-\d{2})', time_text)
                    if match:
                        time_str = match.group(1)

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
        流程：首页 → 搜索框输入 → 点击搜索 → 新标签页 → 提取结果
        """
        results = []

        try:
            # 步骤1：访问首页
            self.logger.info(f"[{keyword}] 访问首页: {self.base_url}/")
            page.goto(f"{self.base_url}/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

            # 步骤2：查找搜索框
            self.logger.info(f"[{keyword}] 查找搜索框")
            search_input = page.locator('input#soinput.soinput').first

            if search_input.count() == 0:
                self.logger.warning(f"[{keyword}] 未找到搜索框，尝试直接访问搜索页")
                # 直接构造搜索URL
                import urllib.parse
                encoded_keyword = urllib.parse.quote(keyword)
                search_url = f"{self.search_base_url}/s/s?sub=97&keyword={encoded_keyword}"
                page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)
            else:
                # 输入关键词
                self.logger.info(f"[{keyword}] 输入关键词")
                search_input.fill(keyword)
                page.wait_for_timeout(500)

                # 步骤3：点击搜索按钮，等待新标签页
                self.logger.info(f"[{keyword}] 点击搜索，等待新标签页")

                # 监听新页面
                with page.expect_popup() as popup_info:
                    # 尝试点击搜索按钮
                    search_button = page.locator('input#msobutton.msobutton').first
                    if search_button.count() > 0:
                        search_button.click()
                    else:
                        # 回车提交
                        search_input.press('Enter')

                # 切换到新标签页
                new_page = popup_info.value
                new_page.wait_for_load_state("domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)

                self.logger.info(f"[{keyword}] 新标签页已打开: {new_page.url}")

                # 关闭广告弹窗（Steam激活码等）
                try:
                    # 方法1: 尝试点击关闭按钮
                    close_selectors = [
                        'text=关闭',
                        '.close',
                        '.dialog-close',
                        '.popup-close',
                        '[class*="close"]',
                        '[class*="Close"]',
                    ]
                    for selector in close_selectors:
                        close_btn = new_page.locator(selector).first
                        if close_btn.count() > 0 and close_btn.is_visible():
                            close_btn.click()
                            new_page.wait_for_timeout(500)
                            self.logger.debug(f"[{keyword}] 已关闭广告弹窗")
                            break

                    # 方法2: 按 ESC 键兜底
                    new_page.keyboard.press('Escape')
                    new_page.wait_for_timeout(300)
                except Exception:
                    pass  # 没有弹窗时静默处理

                # 点击"资讯"标签（新闻内容在资讯标签页）
                try:
                    self.logger.info(f"[{keyword}] 尝试切换到'资讯'标签")

                    # 方法1: 尝试点击"资讯"链接（精确匹配 yxsosub="97"）
                    zixun_clicked = False
                    zixun_selectors = [
                        'a[yxsosub="97"]',               # 最精确：属性选择器
                        'a[href*="sub=97"]',              # href 包含 sub=97
                        '.toggleBtn a:has-text("资讯")',  # toggleBtn 内的资讯链接
                        'a:has-text("资讯")',              # 包含资讯文字
                    ]
                    for selector in zixun_selectors:
                        try:
                            zixun_btn = new_page.locator(selector).first
                            if zixun_btn.count() > 0 and zixun_btn.is_visible():
                                zixun_btn.click()
                                new_page.wait_for_timeout(2000)
                                self.logger.debug(f"[{keyword}] 已点击'资讯'标签: {selector}")
                                zixun_clicked = True
                                break
                        except Exception:
                            continue

                    # 方法2: 如果点击失败，通过URL参数切换（sub=97 是资讯分类）
                    if not zixun_clicked:
                        current_url = new_page.url
                        if 'sub=97' not in current_url:
                            import urllib.parse
                            parsed = urllib.parse.urlparse(current_url)
                            params = urllib.parse.parse_qs(parsed.query)
                            params['sub'] = ['97']  # 资讯分类
                            new_query = urllib.parse.urlencode(params, doseq=True)
                            new_url = urllib.parse.urlunparse((
                                parsed.scheme, parsed.netloc, parsed.path,
                                parsed.params, new_query, parsed.fragment
                            ))
                            new_page.goto(new_url, wait_until="domcontentloaded", timeout=30000)
                            new_page.wait_for_timeout(2000)
                            self.logger.info(f"[{keyword}] 已通过URL切换到资讯分类: {new_url}")
                        else:
                            self.logger.info(f"[{keyword}] 已在资讯分类页面")
                except Exception as e:
                    self.logger.debug(f"[{keyword}] 切换资讯标签失败: {e}")

                # 步骤4：提取新闻列表（在新页面）
                results = self._extract_news_results(new_page)

                # 关闭新标签页，回到主页
                new_page.close()
                self.logger.info(f"[{keyword}] 已关闭搜索结果页，返回首页")

            # 如果直接访问搜索页的情况
            if not results:
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
            page.wait_for_timeout(500)

            # [调试代码已注释] 截图和页面信息调试
            # screenshot_path = f"{self._batch_dir}/debug_screenshot.png"
            # page.screenshot(path=screenshot_path, full_page=False)
            # self.logger.info(f"[DEBUG] 截图已保存: {screenshot_path}")
            #
            # debug_info = page.evaluate("""() => {
            #     const info = {};
            #     info.url = window.location.href;
            #     info.title = document.title;
            #     info.contentExists = !!document.querySelector('#Content');
            #     info.nShowExists = !!document.querySelector('.n_show');
            #     info.bodyLength = document.body ? document.body.innerText.length : -1;
            #     info.bodyHTML = document.body ? document.body.innerHTML.substring(0, 1000) : 'NO BODY';
            #     info.htmlLength = document.documentElement.innerHTML.length;
            #     return JSON.stringify(info);
            # }""")
            # self.logger.info(f"[DEBUG] 页面信息: {debug_info}")

            content = page.evaluate("""() => {
                // 优先使用 #Content（游侠网正文区域）
                const el = document.querySelector('#Content') || document.querySelector('.n_show') || document.querySelector('.ol_detail_left_news') || document.querySelector('.detail_content') || document.querySelector('.TContL');
                if (!el) {
                    return '';
                }

                // 移除脚本、样式和UI元素
                const removeSelectors = ['script', 'style', '.news_ding', '.news_media', '.news_app', '.news_appshow'];
                removeSelectors.forEach(sel => {
                    el.querySelectorAll(sel).forEach(e => e.remove());
                });

                // 提取所有段落文本
                const paragraphs = el.querySelectorAll('p');
                const texts = [];
                paragraphs.forEach(p => {
                    const text = p.innerText?.trim();
                    if (text && text.length > 5) {
                        texts.push(text);
                    }
                });

                if (texts.length > 0) {
                    return texts.join('\\n');
                }

                // 备用：直接获取 innerText
                const text = el.innerText?.trim();
                return text || '';
            }""")

            self.logger.info(f"[DEBUG] 提取内容长度: {len(content) if content else 0}")
            return content or ""

        except Exception as e:
            self.logger.error(f"获取详情页失败: {url} - {e}")
            return ""

    def fetch(self) -> List[NewsItem]:
        """
        采集数据
        """
        all_items = []

        self.logger.info(f"开始采集游侠网 - 关键词: {self.keywords}")
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

    # 支持环境变量配置时间窗口（默认7天）
    hours_window = int(os.getenv('ALI213_HOURS_WINDOW', '250'))

    crawler = Ali213Crawler(hours_window=hours_window)
    items = crawler.run()

    print(f"\n{'='*70}")
    print(f"游侠网采集结果: {len(items)} 条")
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
