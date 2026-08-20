# -*- coding: utf-8 -*-
"""
金山办公投资者关系官网采集器
使用 Playwright 浏览器自动化
采集临时公告和定期报告，支持PDF下载和AI摘要
"""
import re
import sys
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

# 强制 UTF-8 输出：用 reconfigure 改编码，不替换 sys.stdout 对象
# （替换后原对象被 GC 会关闭共享 buffer，导致 print 抛 "I/O operation on closed file"）
sys.stdout.reconfigure(encoding='utf-8')
sys.path.append(str(Path(__file__).parent.parent.parent))

from collectors.base import BaseCrawler
from models.news import NewsItem
from config.settings import CATEGORIES, COLLECTORS


class KingsoftOfficeIRCrawler(BaseCrawler):
    """
    金山办公投资者关系官网采集器
    采集临时公告和定期报告两个栏目
    支持PDF下载、文本提取和AI摘要生成
    支持时间窗口过滤（默认24小时）
    """

    source_name = "金山办公IR官网"
    source_code = "kingsoft_office_ir"
    credibility_base = "【官方公告】"

    def __init__(self, enable_summary: bool = None, hours_window: int = None):
        """
        初始化

        Args:
            enable_summary: 是否启用AI摘要
            hours_window: 时间窗口（小时），默认24小时
        """
        config = COLLECTORS.get('kingsoft_office_ir', {})

        # 时间窗口（默认24小时）
        self.hours_window = hours_window or config.get('hours_window', 24)
        self.cutoff_time = datetime.now() - timedelta(hours=self.hours_window)
        self.logger_info = f"时间窗口: 过去{self.hours_window}小时 ({self.cutoff_time.strftime('%Y-%m-%d %H:%M')} 至今)"

        # 从配置读取 enable_summary
        if enable_summary is None:
            enable_summary = config.get('enable_summary', False)

        # 初始化 PDF 处理器
        try:
            from models.pdf_processor import PDFProcessor
            self.pdf_processor = PDFProcessor()
        except Exception as e:
            self.logger.error(f"PDF 处理器初始化失败: {e}")
            self.pdf_processor = None

        super().__init__(enable_summary=enable_summary)
        self.base_url = "https://ir.wps.cn/financial.html"

    def _auto_classify(self, title: str) -> str:
        """自动分类"""
        title_lower = title.lower()
        scores: dict[str, int] = {}
        for category, rules in CATEGORIES.items():
            score = sum(1 for kw in rules['keywords'] if kw in title_lower)
            scores[category] = score
        if scores and max(scores.values()) > 0:
            best_category = max(scores.items(), key=lambda x: x[1])[0]
            return best_category
        return '资本动态'

    def _parse_time(self, time_str: str) -> datetime:
        """
        解析时间字符串（YYYY-MM-DD格式）
        解析失败返回当前时间（保留数据）
        """
        if not time_str:
            return datetime.now()

        time_str = time_str.strip()

        try:
            return datetime.strptime(time_str, '%Y-%m-%d')
        except ValueError:
            pass

        # 正则提取日期
        match = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', time_str)
        if match:
            year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            return datetime(year, month, day)

        return datetime.now()

    def _click_tab(self, page, tab_id: str):
        """点击Tab切换（临时公告/定期报告）"""
        try:
            page.click(f'#{tab_id}')
            page.wait_for_timeout(1500)
            self.logger.info(f"  已切换到: {tab_id}")
        except Exception as e:
            self.logger.warning(f"  切换Tab失败: {e}")

    def _extract_from_page(self, page, tab_type: str) -> List[dict]:
        """从当前页面提取公告列表"""
        return page.evaluate('''(tabType) => {
            const data = [];
            const items = document.querySelectorAll('#new-cotice-content li, .news-content-list li');

            items.forEach(li => {
                // 从 onclick 提取链接
                const onclick = li.getAttribute('onclick') || '';
                const urlMatch = onclick.match(/tonotice\\(['"](.+?)['"]\\)/);
                const url = urlMatch ? urlMatch[1] : '';

                // 判断是否为PDF
                const isPdf = url.toLowerCase().endsWith('.pdf');

                // 标题
                const titleEl = li.querySelector('.text-left span');
                const title = titleEl ? titleEl.textContent.trim() : '';

                // 日期
                const dateEl = li.querySelector('.text-right');
                const date = dateEl ? dateEl.textContent.trim() : '';

                if (title && url) {
                    data.push({
                        title,
                        url,
                        date,
                        type: tabType,
                        is_pdf: isPdf
                    });
                }
            });

            return {
                items: data,
                count: items.length
            };
        }''', tab_type)

    def _has_next_page(self, page) -> bool:
        """检查是否有下一页"""
        return page.evaluate('''() => {
            const active = document.querySelector('.pagination .active');
            if (!active) return false;
            const next = active.nextElementSibling;
            return next && !next.classList.contains('disabled') && next.querySelector('a');
        }''')

    def _go_to_page(self, page, page_num: int):
        """跳转到指定页"""
        try:
            page.evaluate(f'toPage({page_num})')
            page.wait_for_timeout(2000)
        except Exception as e:
            self.logger.warning(f"  翻页失败: {e}")

    def _download_pdf_by_click(self, page, li_element_handle, pdf_url: str) -> tuple:
        """
        通过点击列表项下载PDF（绕过反爬虫验证）

        Args:
            page: Playwright页面对象
            li_element_handle: 列表项元素句柄
            pdf_url: PDF下载链接（用于验证）

        Returns:
            (PDF本地路径, PDF文本内容) 失败返回 (None, None)
        """
        if not self.pdf_processor:
            self.logger.warning("PDF处理器未初始化")
            return None, None

        pdf_path = None

        try:
            self.logger.debug(f"  通过点击下载PDF...")

            # 设置下载路径
            pdf_dir = os.path.join(self._batch_dir, "pdfs")
            os.makedirs(pdf_dir, exist_ok=True)

            # 生成文件ID
            import hashlib
            file_id = hashlib.md5(pdf_url.encode()).hexdigest()[:12]
            pdf_filename = f"kingsoft_office_{file_id}.pdf"
            pdf_path = os.path.join(pdf_dir, pdf_filename)

            # 方法：监听新页面打开，然后获取PDF内容
            new_page = None

            # 设置新页面监听
            with page.context.expect_page() as new_page_info:
                # 点击列表项（触发 tonotice() 打开新标签）
                li_element_handle.click()

                # 等待新页面打开
                try:
                    new_page = new_page_info.value
                    new_page.wait_for_load_state("networkidle", timeout=10000)
                except Exception as e:
                    self.logger.debug(f"  等待新页面超时: {e}")

            if not new_page:
                self.logger.warning("  点击后未检测到新页面")
                return None, None

            try:
                # 获取新页面的URL
                current_url = new_page.url
                self.logger.debug(f"  新页面URL: {current_url}")

                # 如果URL是PDF，使用CDP获取内容
                if current_url.endswith('.pdf') or 'pdf' in current_url.lower():
                    # 使用Chrome DevTools Protocol 获取PDF内容
                    client = new_page.context.new_cdp_session(new_page)

                    # 方法：使用 fetch 从浏览器缓存中获取PDF
                    pdf_data = new_page.evaluate('''async () => {
                        try {
                            const response = await fetch(window.location.href, {
                                credentials: 'include'
                            });
                            const buffer = await response.arrayBuffer();
                            return Array.from(new Uint8Array(buffer));
                        } catch (e) {
                            return null;
                        }
                    }''')

                    if pdf_data:
                        pdf_bytes = bytes(pdf_data)
                        with open(pdf_path, 'wb') as f:
                            f.write(pdf_bytes)
                        self.logger.debug(f"  通过新页面获取PDF成功，大小: {len(pdf_bytes)} bytes")
                    else:
                        # 备选：截图OCR
                        self.logger.warning("  无法从页面获取PDF数据，尝试截图...")
                        return None, None
                else:
                    # 如果不是PDF页面，可能是验证页面，等待一下再尝试
                    new_page.wait_for_timeout(3000)
                    # 再次检查URL
                    current_url = new_page.url
                    self.logger.debug(f"  重定向后URL: {current_url}")

                    # 尝试获取页面内容
                    pdf_data = new_page.evaluate('''async () => {
                        try {
                            const response = await fetch(window.location.href, {
                                credentials: 'include'
                            });
                            const buffer = await response.arrayBuffer();
                            return Array.from(new Uint8Array(buffer));
                        } catch (e) {
                            return null;
                        }
                    }''')

                    if pdf_data:
                        pdf_bytes = bytes(pdf_data)
                        with open(pdf_path, 'wb') as f:
                            f.write(pdf_bytes)
                        self.logger.debug(f"  获取内容成功，大小: {len(pdf_bytes)} bytes")

            finally:
                # 关闭新页面
                if new_page:
                    new_page.close()

            # 验证PDF文件
            if not os.path.exists(pdf_path) or os.path.getsize(pdf_path) < 100:
                return pdf_path, None

            with open(pdf_path, 'rb') as f:
                header = f.read(10)
                self.logger.debug(f"  文件头: {header}")
                if not header.startswith(b'%PDF'):
                    with open(pdf_path, 'rb') as f:
                        content_preview = f.read(500).decode('utf-8', errors='ignore')
                    self.logger.error(f"  下载的不是有效PDF文件，内容预览: {content_preview[:200]}")
                    return pdf_path, None

            # 提取文本
            self.logger.debug(f"  开始提取PDF文本...")
            text = self.pdf_processor.extract_text(pdf_path)

            if text:
                text = text.strip()
                self.logger.debug(f"  PDF文本提取成功，长度: {len(text)}")
            else:
                self.logger.warning(f"  PDF文本提取返回空，可能是扫描件/图片PDF")

            return pdf_path, text

        except Exception as e:
            self.logger.warning(f"点击下载PDF失败: {e}", exc_info=True)
            return pdf_path, None

    def _download_wpscdn_pdf(self, pdf_url: str) -> tuple:
        """
        下载金山CDN的PDF（无反爬虫，直接requests下载）

        Args:
            pdf_url: PDF下载链接

        Returns:
            (PDF本地路径, PDF文本内容) 失败返回 (None, None)
        """
        if not self.pdf_processor or not pdf_url:
            return None, None

        try:
            import requests

            # 设置下载路径
            pdf_dir = os.path.join(self._batch_dir, "pdfs")
            os.makedirs(pdf_dir, exist_ok=True)

            # 生成文件ID
            import hashlib
            file_id = hashlib.md5(pdf_url.encode()).hexdigest()[:12]
            pdf_path = os.path.join(pdf_dir, f"kingsoft_office_{file_id}.pdf")

            # 直接下载（金山CDN无防盗链）
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'application/pdf,*/*',
            }

            response = requests.get(pdf_url, headers=headers, timeout=30)
            response.raise_for_status()

            with open(pdf_path, 'wb') as f:
                f.write(response.content)

            # 验证PDF
            with open(pdf_path, 'rb') as f:
                header = f.read(10)
                if not header.startswith(b'%PDF'):
                    return pdf_path, None

            # 提取文本
            text = self.pdf_processor.extract_text(pdf_path)
            return pdf_path, text.strip() if text else None

        except Exception as e:
            self.logger.warning(f"金山CDN PDF下载失败: {e}")
            return None, None

    def _download_and_process_pdf(self, page, pdf_url: str, li_element_handle=None) -> tuple:
        """
        下载PDF并提取文本

        Args:
            page: Playwright页面对象
            pdf_url: PDF下载链接
            li_element_handle: 可选，列表项元素句柄（用于点击下载）

        Returns:
            (PDF本地路径, PDF文本内容) 失败返回 (None, None)
        """
        if not self.pdf_processor or not pdf_url:
            self.logger.warning("PDF处理器未初始化或URL为空")
            return None, None

        # 如果有元素句柄且是上交所PDF，尝试点击下载
        if li_element_handle and 'sse.com.cn' in pdf_url:
            self.logger.debug(f"  尝试点击下载上交所PDF...")
            return self._download_pdf_by_click(page, li_element_handle, pdf_url)

        # 否则使用常规下载（非上交所PDF）
        pdf_path = None
        try:
            self.logger.debug(f"  直接下载PDF: {pdf_url[:80]}...")

            # 设置下载路径
            pdf_dir = os.path.join(self._batch_dir, "pdfs")
            os.makedirs(pdf_dir, exist_ok=True)

            # 生成文件ID
            import hashlib
            file_id = hashlib.md5(pdf_url.encode()).hexdigest()[:12]
            pdf_filename = f"kingsoft_office_{file_id}.pdf"
            pdf_path = os.path.join(pdf_dir, pdf_filename)

            # 针对不同域名设置headers
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'application/pdf,*/*',
            }

            if 'wpscdn.cn' in pdf_url:
                headers['Referer'] = 'https://ir.wps.cn/'

            import requests
            response = requests.get(pdf_url, headers=headers, timeout=30)
            response.raise_for_status()

            with open(pdf_path, 'wb') as f:
                f.write(response.content)

            # 验证PDF
            with open(pdf_path, 'rb') as f:
                header = f.read(10)
                if not header.startswith(b'%PDF'):
                    return pdf_path, None

            # 提取文本
            text = self.pdf_processor.extract_text(pdf_path)
            return pdf_path, text.strip() if text else None

        except Exception as e:
            self.logger.warning(f"PDF下载失败: {e}")
            return pdf_path, None

    def _fetch_tab(self, page, tab_id: str, tab_name: str, max_pages: int) -> List[dict]:
        """
        采集单个Tab的数据
        实时时间过滤，翻页到max_pages为止
        同时处理上交所PDF的点击下载
        """
        all_items = []

        self.logger.info("=" * 60)
        self.logger.info(f"开始采集【{tab_name}】")
        self.logger.info("=" * 60)

        # 点击Tab
        self._click_tab(page, tab_id)

        # 翻页采集
        for page_num in range(1, max_pages + 1):
            self.logger.info(f"  第{page_num}页...")

            result = self._extract_from_page(page, tab_name)
            items = result.get('items', [])

            if not items:
                self.logger.info(f"    本页无数据，停止翻页")
                break

            # 【时间过滤】只保留时间窗口内的数据
            filtered_items = []
            for item in items:
                time_str = item.get('date', '')
                news_time = self._parse_time(time_str)

                if news_time >= self.cutoff_time:
                    filtered_items.append(item)
                else:
                    self.logger.debug(f"  [时间过滤] {item.get('title', '')[:40]}... ({time_str})")

            self.logger.info(f"    找到 {len(filtered_items)}/{len(items)} 条新数据")

            # 【处理PDF下载】分类处理：上交所需要点击，金山CDN直接下载
            if self.pdf_processor:
                for idx, item in enumerate(filtered_items):
                    url = item.get('url', '')
                    is_pdf = item.get('is_pdf', False)
                    title = item.get('title', '')

                    if not is_pdf:
                        continue

                    # 判断PDF来源
                    is_sse = 'sse.com.cn' in url
                    is_wpscdn = 'wpscdn.cn' in url

                    if is_wpscdn:
                        # 金山CDN：直接下载，无反爬虫
                        self.logger.info(f"    下载金山CDN PDF [{idx+1}/{len(filtered_items)}]: {title[:40]}...")
                        try:
                            pdf_path, pdf_content = self._download_wpscdn_pdf(url)
                            if pdf_path:
                                item['pdf_path'] = pdf_path
                                item['pdf_content'] = pdf_content
                                content_len = len(pdf_content) if pdf_content else 0
                                self.logger.info(f"      ✓ 直接下载成功，文本长度: {content_len}")
                            else:
                                self.logger.warning(f"      ✗ 下载失败")
                        except Exception as e:
                            self.logger.warning(f"      下载失败: {e}")

                    elif is_sse:
                        # 上交所：需要点击绕过验证
                        self.logger.info(f"    处理上交所PDF [{idx+1}/{len(filtered_items)}]: {title[:40]}...")
                        try:
                            # 通过onclick属性定位元素
                            li_locators = page.locator('#new-cotice-content li, .news-content-list li').all()
                            target_element = None

                            for li in li_locators:
                                onclick_attr = li.get_attribute('onclick') or ''
                                if url in onclick_attr:
                                    target_element = li
                                    break

                            if target_element:
                                # 尝试下载（带重试）
                                pdf_path, pdf_content = None, None
                                for attempt in range(2):
                                    if attempt > 0:
                                        self.logger.info(f"      第{attempt+1}次尝试...")
                                        time.sleep(2)

                                    pdf_path, pdf_content = self._download_pdf_by_click(page, target_element, url)
                                    if pdf_path and pdf_content:
                                        break
                                    time.sleep(1)

                                if pdf_path:
                                    item['pdf_path'] = pdf_path
                                    item['pdf_content'] = pdf_content
                                    content_len = len(pdf_content) if pdf_content else 0
                                    self.logger.info(f"      ✓ PDF下载成功，文本长度: {content_len}")
                                else:
                                    self.logger.warning(f"      ✗ PDF下载失败")
                            else:
                                self.logger.warning(f"      未找到对应元素")
                        except Exception as e:
                            self.logger.warning(f"      点击下载失败: {e}")

                        # 每次点击后等待，避免触发反爬虫
                        if idx < len(filtered_items) - 1:
                            time.sleep(2)

            all_items.extend(filtered_items)

            # 翻页判断
            if page_num < max_pages and self._has_next_page(page):
                self._go_to_page(page, page_num + 1)
            else:
                break

        self.logger.info(f"【{tab_name}】总计: {len(all_items)} 条")
        return all_items

    def fetch(self, max_pages: int = 3) -> List[NewsItem]:
        """
        采集临时公告和定期报告数据
        支持PDF下载、文本提取和AI摘要生成
        """
        items = []

        self.logger.info(f"开始采集 {self.source_name}")
        self.logger.info(f"页面: {self.base_url}")
        self.logger.info(self.logger_info)

        # 创建批次目录
        now = datetime.now()
        date_dir = now.strftime('%Y/%m/%d')
        batch_name = now.strftime(f'{self.source_code}_%Y%m%d_%H%M%S')
        if not self._batch_dir:
            self._batch_dir = f"{self.output_dir}/{self.source_code}/{date_dir}/{batch_name}"
        os.makedirs(self._batch_dir, exist_ok=True)
        self.logger.info(f"批次目录: {self._batch_dir}")

        # 设置 PDF 下载目录
        if self.pdf_processor:
            pdf_dir = os.path.join(self._batch_dir, "pdfs")
            self.pdf_processor.set_download_dir(pdf_dir)

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-web-security',
                    '--disable-features=IsolateOrigins,site-per-process',
                    '--ignore-certificate-errors',
                    '--ignore-ssl-errors',
                ]
            )

            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                ignore_https_errors=True,
            )

            page = context.new_page()

            try:
                # 访问页面
                page.goto(self.base_url, wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(3000)

                self.logger.info(f"页面标题: {page.title()}")

                # 采集两个Tab
                all_raw_items = []

                # 1. 临时公告
                temp_items = self._fetch_tab(page, 'temporary-notice', '临时公告', max_pages)
                all_raw_items.extend(temp_items)

                # 2. 定期报告（需要切换Tab）
                regular_items = self._fetch_tab(page, 'regular-notice', '定期报告', max_pages)
                all_raw_items.extend(regular_items)

                self.logger.info(f"\n原始数据总计: {len(all_raw_items)} 条")

                # 去重和转换
                seen_titles = set()

                for idx, raw in enumerate(all_raw_items, 1):
                    title = raw.get('title', '')
                    url = raw.get('url', '')
                    tab_type = raw.get('type', '')
                    time_str = raw.get('date', '')
                    is_pdf = raw.get('is_pdf', False)

                    if not title or len(title) < 5:
                        continue

                    # 去重
                    if title in seen_titles:
                        continue
                    seen_titles.add(title)

                    self.logger.info(f"\n[{idx}/{len(all_raw_items)}] 处理: {title[:50]}...")

                    # 确保URL完整
                    if url.startswith('http'):
                        full_url = url
                    else:
                        full_url = f"https://ir.wps.cn{url}" if url.startswith('/') else f"https://ir.wps.cn/{url}"

                    # 下载PDF并提取内容（如果是PDF链接且尚未下载）
                    pdf_path = None
                    pdf_content = None
                    if is_pdf and self.pdf_processor:
                        # 检查是否已在_fetch_tab中下载
                        if raw.get('pdf_path'):
                            pdf_path = raw.get('pdf_path')
                            pdf_content = raw.get('pdf_content')
                            content_len = len(pdf_content) if pdf_content else 0
                            self.logger.info(f"  PDF已下载（在列表页）: {os.path.basename(pdf_path)}, 文本长度: {content_len}")
                        else:
                            # 需要下载（非上交所PDF）
                            self.logger.info(f"  下载PDF...")
                            pdf_path, pdf_content = self._download_and_process_pdf(page, full_url)
                            if pdf_path:
                                content_len = len(pdf_content) if pdf_content else 0
                                self.logger.info(f"  ✓ PDF下载成功: {os.path.basename(pdf_path)}, 文本长度: {content_len}")
                                if not pdf_content:
                                    self.logger.warning(f"  ⚠️ PDF文本提取为空")
                            else:
                                self.logger.warning(f"  ✗ PDF下载失败")

                    # 生成 AI 摘要（如果启用且内容足够）
                    summary = ""
                    summary_generated_at = None
                    final_content = pdf_content if pdf_content else ""

                    if final_content and len(final_content.strip()) >= 50:
                        self.logger.info(f"  正在生成 AI 摘要...")
                        ai_summary, gen_time = self.generate_summary(title, final_content)
                        if ai_summary:
                            summary = ai_summary
                            summary_generated_at = gen_time
                            self.logger.info(f"  ✓ AI 摘要生成成功: {len(ai_summary)} 字")
                        else:
                            self.logger.warning(f"  ✗ AI 摘要生成失败")

                    # 如果没有摘要，使用标题作为备选
                    if not summary:
                        summary = title

                    # 解析日期
                    news_time = self._parse_time(time_str)

                    # 构建 raw_data
                    raw_data = {
                        'tab_type': tab_type,
                    }
                    if pdf_path:
                        rel_pdf_path = os.path.relpath(pdf_path, self._batch_dir)
                        raw_data['pdf_path'] = rel_pdf_path

                    item = NewsItem(
                        title=title,
                        date=news_time.strftime('%Y-%m-%d'),
                        url=full_url,
                        source=self.source_name,
                        source_code=self.source_code,
                        credibility_tag=self.credibility_base,
                        category=self._auto_classify(title),
                        summary=summary,
                        summary_generated_at=summary_generated_at,
                        content=final_content,
                        raw_data=raw_data
                    )
                    items.append(item)
                    self.logger.info(f"  ✓ 已采集: {title[:40]}...")

                # 截图用于调试
                try:
                    page.goto(self.base_url, wait_until="networkidle", timeout=30000)
                    page.wait_for_timeout(2000)
                    screenshot_path = f"output/logs/kingsoft_office_ir.png"
                    os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
                    page.screenshot(path=screenshot_path)
                    self.logger.info(f"截图已保存: {screenshot_path}")
                except Exception as e:
                    self.logger.warning(f"截图失败: {e}")

            except Exception as e:
                self.logger.error(f"采集失败: {e}", exc_info=True)
            finally:
                context.close()
                browser.close()

        self.logger.info("")
        self.logger.info('='*60)
        self.logger.info(f"采集完成: {len(items)} 条")
        self.logger.info('='*60)
        return items


def main():
    """测试运行"""
    import os

    hours_window = int(os.getenv('KINGSOFT_OFFICE_IR_HOURS_WINDOW', '720'))  # 默认30天

    crawler = KingsoftOfficeIRCrawler(hours_window=hours_window)
    items = crawler.run()

    print(f"\n{'='*70}")
    print(f"金山办公IR官网采集结果: {len(items)} 条")
    print(f"时间窗口: 过去{hours_window}小时")
    print(f"截止时间: {crawler.cutoff_time.strftime('%Y-%m-%d %H:%M')}")
    print('='*70)

    for i, item in enumerate(items[:20], 1):
        print(f"\n{i}. [{item.category}] {item.title}")
        print(f"   时间: {item.date}")
        print(f"   链接: {item.url}")
        if item.summary:
            summary_display = item.summary[:200] + '...' if len(item.summary) > 200 else item.summary
            print(f"   摘要: {summary_display}")


if __name__ == "__main__":
    main()
