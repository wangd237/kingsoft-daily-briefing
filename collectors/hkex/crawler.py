# -*- coding: utf-8 -*-
"""港交所披露易采集器（PDF 版本）
使用 Playwright 浏览器自动化
支持多股票：03888.HK（金山软件）、03896.HK（金山云）
下载公告 PDF -> 解析文本 -> AI 摘要
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from datetime import datetime, timedelta
from pathlib import Path
import random

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from collectors.base import BaseCrawler
from models.news import NewsItem
from config.settings import CATEGORIES, COLLECTORS


class HKEXCrawler(BaseCrawler):
    """港交所披露易采集器"""

    source_name = "港交所披露易"
    source_code = "hkex"
    credibility_base = "【官方公告】"

    def __init__(self, stock_codes: list[str] | None = None, hours_window: int | None = None, enable_summary: bool | None = None):
        """
        初始化

        Args:
            stock_codes: 股票代码列表（默认从配置读取）
            hours_window: 只保留最近 N 小时的数据（默认从配置读取）
            enable_summary: 是否启用 AI 摘要（默认从配置读取）
        """
        # 从配置读取
        hkex_config = COLLECTORS.get('hkex', {})

        # 股票配置
        stocks_config = hkex_config.get('stocks', [
            {'code': '03888', 'name': '金山软件'},
            {'code': '03896', 'name': '金山云'},
        ])
        self.stock_codes = stock_codes or [s['code'] for s in stocks_config]
        self.stock_names = {s['code']: s['name'] for s in stocks_config}

        # 时间窗口（优先参数，其次配置，默认24）
        self.hours_window = hours_window or hkex_config.get('hours_window', 24)

        # 计算截止时间
        self.cutoff_time = datetime.now() - timedelta(hours=self.hours_window)
        self.logger_info = f"时间窗口: 过去{self.hours_window}小时 ({self.cutoff_time.strftime('%Y-%m-%d %H:%M')} 至今)"

        # 是否启用 AI 摘要（优先参数，其次配置，默认True）
        _enable_summary = enable_summary if enable_summary is not None else hkex_config.get('enable_summary', True)

        # 初始化 PDF 处理器（不指定下载目录，后续动态设置）
        try:
            from models.pdf_processor import PDFProcessor
            self.pdf_processor = PDFProcessor()
        except Exception as e:
            self.logger.error(f"PDF 处理器初始化失败: {e}")
            self.pdf_processor = None

        super().__init__(enable_summary=_enable_summary)

    def _auto_classify(self, title: str) -> str:
        """自动分类"""
        title_lower = title.lower()
        scores = {
            cat: sum(1 for kw in rules['keywords'] if kw in title_lower)
            for cat, rules in CATEGORIES.items()
        }
        if scores and max(scores.values()) > 0:
            return max(scores, key=lambda k: scores[k])
        return "资本动态"

    def _extract_pdf_content(self, pdf_url: str, stock_code: str, cookies: list | None = None, referer: str | None = None) -> tuple[str | None, str | None]:
        """
        从 PDF URL 下载并提取内容

        Args:
            pdf_url: PDF 文件链接
            stock_code: 股票代码
            cookies: 浏览器 cookies
            referer: Referer 头

        Returns:
            (PDF本地路径, 提取的文本内容)
        """
        if not self.pdf_processor:
            self.logger.warning("PDF 处理器不可用")
            return None, None

        try:
            import requests
            import hashlib

            # 生成文件ID（基于URL）
            file_id = hashlib.md5(pdf_url.encode()).hexdigest()[:12]

            # 获取下载目录
            download_dir = self.pdf_processor._get_download_dir()

            # 构建保存路径
            pdf_path = download_dir / f"{stock_code}_{file_id}.pdf"

            # 如果已存在，直接提取文本
            if pdf_path.exists():
                self.logger.info(f"PDF 已存在: {pdf_path}")
                text = self.pdf_processor.extract_text(str(pdf_path))
                return str(pdf_path), text

            # 下载 PDF（带上 Cookie 和 Referer）
            self.logger.info(f"下载 PDF: {pdf_url}")
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': referer or 'https://www.hkexnews.hk/',
            }

            # 转换 cookies 为 requests 格式
            cookies_dict = {}
            if cookies:
                for cookie in cookies:
                    if 'name' in cookie and 'value' in cookie:
                        cookies_dict[cookie['name']] = cookie['value']

            response = requests.get(pdf_url, headers=headers, cookies=cookies_dict, timeout=30)
            response.raise_for_status()

            # 保存
            with open(pdf_path, 'wb') as f:
                f.write(response.content)

            self.logger.info(f"PDF 下载成功: {pdf_path} ({len(response.content)} bytes)")

            # 提取文本
            text = self.pdf_processor.extract_text(str(pdf_path))
            return str(pdf_path), text

        except Exception as e:
            self.logger.error(f"PDF 处理失败: {e}")
            return None, None

    def _fetch_single_stock(self, stock_code: str) -> list[NewsItem]:
        """
        采集单个股票的公告数据（下载 PDF 并解析）

        Args:
            stock_code: 股票代码（03888 或 03896）

        Returns:
            该股票的 NewsItem 列表
        """
        from playwright.sync_api import sync_playwright
        import os

        items = []
        stock_name = self.stock_names.get(stock_code, '未知')

        self.logger.info(f"开始采集港交所 - 股票: {stock_code} ({stock_name})")

        # 创建批次目录（只在第一次调用时创建）
        if not self._batch_dir:
            now = datetime.now()
            date_dir = now.strftime('%Y/%m/%d')
            batch_name = now.strftime(f'{self.source_code}_%Y%m%d_%H%M%S')
            self._batch_dir = f"{self.output_dir}/{self.source_code}/{date_dir}/{batch_name}"
            os.makedirs(self._batch_dir, exist_ok=True)
            self.logger.info(f"批次目录: {self._batch_dir}")

            # 设置 PDF 下载目录为批次目录下的 pdfs 子目录
            if self.pdf_processor:
                pdf_dir = os.path.join(self._batch_dir, "pdfs")
                self.pdf_processor.set_download_dir(pdf_dir)

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless= True,  
                args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
            )

            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )

            page = context.new_page()

            try:
                # 访问搜索页面
                search_url = "https://www.hkexnews.hk/search/titlesearch.xhtml?lang=zh"
                self.logger.info(f"访问: {search_url}")
                page.goto(search_url, wait_until="load", timeout=60000)

                # 随机延时防封
                self._random_delay(2, 4)

                # 截图查看初始页面
                page.screenshot(path=f"output/logs/hkex_{stock_code}_step1_initial.png")

                # 等待搜索框加载并选择股票
                stock_input = page.locator("input#searchStockCode")
                stock_input.wait_for(state="visible", timeout=15000)

                # 清空并填充股票代码
                stock_input.clear()
                stock_input.fill(stock_code)
                self.logger.info(f"已填充股票代码: {stock_code}")

                # 等待下拉选项出现并选择（自动完成组件）
                try:
                    # 等待下拉框出现，选择第一个有效选项（排除"更多"）
                    dropdown_option = page.locator(".autocomplete-suggestion:not(.suggestion-viewall)").first
                    dropdown_option.wait_for(state="visible", timeout=5000)
                    dropdown_option.click()
                    self.logger.info(f"已选择股票: {stock_code} {stock_name}")
                except Exception:
                    # 如果下拉框没出现，尝试通过 JavaScript 直接设置并触发搜索
                    self.logger.warning("下拉框未出现，尝试通过 JS 触发搜索")
                    page.evaluate("""(stockCode) => {
                        const input = document.querySelector('input#searchStockCode');
                        if (input) {
                            input.value = stockCode;
                            input.dispatchEvent(new Event('change', { bubbles: true }));
                            input.dispatchEvent(new Event('input', { bubbles: true }));
                            // 触发搜索按钮点击
                            const searchBtn = document.querySelector('a.filter__btn-applyFilters-js');
                            if (searchBtn) searchBtn.click();
                        }
                    }""", stock_code)

                # 随机延时
                self._random_delay(1, 2)

                # 截图查看填充后
                page.screenshot(path=f"output/logs/hkex_{stock_code}_step2_filled.png")

                # 设置开始日期为10天前
                self._set_date_range(page, days_back=10)

                # 点击搜索按钮（纯 Class 定位）
                search_btn = page.locator('a.filter__btn-applyFilters-js').first
                search_btn.wait_for(state="visible", timeout=10000)
                search_btn.click()
                self.logger.info("已点击搜索按钮")

                # 等待搜索结果加载 - 等待表格出现（使用更精确的选择器）
                self.logger.info("等待搜索结果加载...")
                try:
                    # 等待特定容器内的表格行出现
                    page.wait_for_selector(".title-search-content table tbody tr[role='row']", timeout=30000)
                except Exception as e:
                    self.logger.warning(f"等待表格超时: {e}")

                # 随机延时让数据完全加载
                self._random_delay(2, 3)

                # 截图查看搜索结果
                page.screenshot(path=f"output/logs/hkex_{stock_code}_step3_results.png")

                # 获取 cookies 用于 PDF 下载
                cookies = context.cookies()
                current_url = page.url

                # 提取公告列表 - 使用精确的选择器
                results = page.evaluate("""() => {
                    const data = [];

                    // 使用精确的表格行选择器
                    let rows = document.querySelectorAll('.title-search-content table tbody tr[role="row"]');
                    if (rows.length === 0) {
                        // 回退到通用选择器
                        rows = document.querySelectorAll('table tbody tr');
                    }

                    console.log('Found rows:', rows.length);

                    rows.forEach((row, idx) => {
                        const cells = row.querySelectorAll('td');
                        console.log(`Row ${idx}: ${cells.length} cells`);

                        if (cells.length >= 4) {
                            // 港交所格式：日期 | 股票代码 | 股票名称 | 文档标题
                            const date = cells[0]?.textContent?.trim() || '';
                            const stockCode = cells[1]?.textContent?.trim() || '';
                            const stockName = cells[2]?.textContent?.trim() || '';
                            const linkEl = cells[3]?.querySelector('a');
                            const title = linkEl?.textContent?.trim() || cells[3]?.textContent?.trim() || '';
                            const href = linkEl?.href || '';

                            // 过滤：只保留目标股票代码的公告
                            if (title && title.length > 3) {
                                data.push({
                                    title,
                                    url: href,
                                    date,
                                    stockCode,
                                    stockName
                                });
                            }
                        }
                    });

                    // 获取调试信息
                    const debugInfo = {
                        url: window.location.href,
                        pageTitle: document.title,
                        rowCount: rows.length,
                        recordCount: document.querySelector('.total-records')?.textContent?.trim() || '',
                        searchInputValue: document.querySelector('input#searchStockCode')?.value || 'empty',
                        allTables: document.querySelectorAll('table').length,
                        bodyText: document.body.innerText.substring(0, 500)
                    };

                    return {
                        items: data,
                        debug: debugInfo
                    };
                }""")

                # 解析结果
                debug_info = results.get('debug', {})
                self.logger.info(f"页面: {debug_info.get('pageTitle', 'N/A')}")
                self.logger.info(f"当前URL: {debug_info.get('url', 'N/A')}")
                self.logger.info(f"搜索框值: {debug_info.get('searchInputValue', 'N/A')}")
                self.logger.info(f"记录数: {debug_info.get('recordCount', 'N/A')}")
                self.logger.info(f"表格行数: {debug_info.get('rowCount', 0)}")

                raw_items = results.get('items', [])
                self.logger.info(f"找到 {len(raw_items)} 条数据")

                # 去重处理
                seen_titles = set()
                invalid_words = ['Search', 'Guide', 'Help', 'Disclaimer']

                # 去重处理 + 时间过滤
                seen_titles = set()
                invalid_words = ['Search', 'Guide', 'Help', 'Disclaimer']
                filtered_results = []

                for idx, news in enumerate(raw_items, 1):
                    title = news.get('title', '')
                    url = news.get('url', '')
                    date = news.get('date', '')

                    # 过滤无效数据
                    if not title or len(title) < 5:
                        continue
                    if title in seen_titles:
                        continue
                    if any(w in title for w in invalid_words):
                        continue

                    seen_titles.add(title)

                    # 解析日期时间（用于时间过滤）
                    parsed_datetime = self._parse_hkex_datetime(date)

                    # 时间过滤：检查是否在时间窗口内（采集阶段过滤，避免浪费资源）
                    if parsed_datetime and parsed_datetime < self.cutoff_time:
                        self.logger.debug(f"[{stock_code}] 时间过滤: {title[:50]}... (时间: {date})")
                        continue

                    filtered_results.append(news)

                self.logger.info(f"[{stock_code}] 过滤后: {len(filtered_results)}/{len(raw_items)} 条")

                for idx, news in enumerate(filtered_results, 1):
                    title = news.get('title', '')
                    url = news.get('url', '')
                    date = news.get('date', '')

                    self.logger.info(f"[{idx}/{len(filtered_results)}] 处理: {title[:50]}...")

                    # 解析日期为 YYYY-MM-DD 格式
                    parsed_date = self._parse_hkex_date(date)
                    parsed_datetime = self._parse_hkex_datetime(date)

                    # 创建基础 NewsItem
                    item = NewsItem(
                        title=f"{stock_name}: {title}",
                        date=parsed_date,
                        url=url,
                        source=f"港交所-{stock_name}",
                        source_code=f"hkex_{stock_code}",
                        credibility_tag=self.credibility_base,
                        category=self._auto_classify(title),
                        publish_time=parsed_datetime,
                    )

                    # 下载并解析 PDF（带上 cookies 和 referer）
                    if url and self.pdf_processor:
                        self.logger.info(f"  正在下载 PDF...")
                        pdf_path, content = self._extract_pdf_content(url, stock_code, cookies=cookies, referer=current_url)

                        if pdf_path and content:
                            item.content = content
                            # 存储相对路径（相对于批次目录）
                            rel_pdf_path = os.path.relpath(pdf_path, self._batch_dir)
                            item.raw_data = {'pdf_path': rel_pdf_path}
                            self.logger.info(f"  ✓ PDF 解析成功: {len(content)} 字符")

                            # 生成 AI 摘要（使用基类方法）
                            self.logger.info(f"  正在生成 AI 摘要...")
                            summary, summary_time = self.generate_summary(title, content)
                            if summary and summary_time:
                                item.summary = summary
                                item.summary_generated_at = summary_time
                                self.logger.info(f"  ✓ AI 摘要生成成功: {len(summary)} 字")
                            else:
                                self.logger.warning(f"  ✗ AI 摘要生成失败")
                        elif pdf_path:
                            # 存储相对路径
                            rel_pdf_path = os.path.relpath(pdf_path, self._batch_dir)
                            item.raw_data = {'pdf_path': rel_pdf_path}
                            self.logger.warning(f"  ⚠ PDF 下载成功但文本提取失败")
                        else:
                            self.logger.warning(f"  ✗ PDF 下载失败")

                    items.append(item)

                    # 随机延时防封
                    self._random_delay(1, 2)

                # 最终截图
                page.screenshot(path=f"output/logs/hkex_{stock_code}_final.png")
                self.logger.info("截图已保存")

            except Exception as e:
                self.logger.error(f"采集失败: {e}", exc_info=True)
            finally:
                context.close()
                browser.close()

        self.logger.info(f"股票 {stock_code} 采集完成: {len(items)} 条")
        return items

    def fetch(self) -> list[NewsItem]:
        """
        采集所有股票的公告数据
        循环采集 self.stock_codes 中定义的所有股票
        """
        import os

        all_items = []

        self.logger.info(f"开始采集港交所 - 股票列表: {self.stock_codes}")

        # 创建批次目录（只创建一次）
        now = datetime.now()
        date_dir = now.strftime('%Y/%m/%d')
        batch_name = now.strftime(f'{self.source_code}_%Y%m%d_%H%M%S')
        if not self._batch_dir:
            self._batch_dir = f"{self.output_dir}/{self.source_code}/{date_dir}/{batch_name}"
        os.makedirs(self._batch_dir, exist_ok=True)
        self.logger.info(f"批次目录: {self._batch_dir}")

        # 设置 PDF 下载目录为批次目录下的 pdfs 子目录
        if self.pdf_processor:
            pdf_dir = os.path.join(self._batch_dir, "pdfs")
            self.pdf_processor.set_download_dir(pdf_dir)

        # 循环采集每个股票
        for stock_code in self.stock_codes:
            self.logger.info(f"\n{'='*60}")
            self.logger.info(f"开始采集股票: {stock_code}")
            self.logger.info('='*60)

            items = self._fetch_single_stock(stock_code)
            all_items.extend(items)

            self.logger.info(f"股票 {stock_code} 采集完成，当前总计: {len(all_items)} 条")

        # 时间过滤（二次检查，采集阶段已过滤大部分）
        if self.hours_window > 0:
            before_count = len(all_items)
            all_items = self._filter_by_time(all_items)
            if len(all_items) < before_count:
                self.logger.info(f"时间过滤: {len(all_items)}/{before_count} 条")

        self.logger.info(f"\n{'='*60}")
        self.logger.info(f"港交所所有股票采集完成: 共 {len(all_items)} 条")
        self.logger.info('='*60)

        return all_items

    def _filter_by_time(self, items: list[NewsItem]) -> list[NewsItem]:
        """
        过滤保留时间窗口内的数据
        使用 publish_time（精确到分钟）进行过滤，而不是仅比较日期

        Args:
            items: 采集的新闻列表

        Returns:
            过滤后的列表
        """
        filtered = []

        for item in items:
            # 优先使用 publish_time（精确时间），其次使用 date（仅日期）
            item_datetime = item.publish_time

            if item_datetime is None and item.date:
                # 如果没有 publish_time，尝试从 date 解析
                try:
                    item_datetime = datetime.strptime(item.date, '%Y-%m-%d')
                except ValueError:
                    item_datetime = None

            if item_datetime is None:
                # 无时间信息，保留数据（宁可多采）
                filtered.append(item)
                continue

            # 精确时间比较
            if item_datetime >= self.cutoff_time:
                filtered.append(item)
            else:
                self.logger.debug(f"时间过滤跳过: {item.title[:30]}... (时间: {item_datetime})")

        return filtered

    def _set_date_range(self, page, days_back: int = 10):
        """
        设置搜索日期范围 - 移除 readonly 后直接填充日期

        Args:
            page: Playwright page 对象
            days_back: 提前多少天（默认10天）
        """
        try:
            # 计算目标日期
            from datetime import timedelta
            target_date = datetime.now() - timedelta(days=days_back)
            date_str = target_date.strftime('%Y/%m/%d')

            self.logger.info(f"设置开始日期为 {days_back} 天前: {date_str}")

            # 等待输入框可见
            date_from_input = page.locator("input#searchDate-From")
            date_from_input.wait_for(state="visible", timeout=10000)

            # 移除 readonly 后填充日期
            result = page.evaluate("""(dateStr) => {
                const input = document.querySelector('input#searchDate-From');
                if (!input) return { success: false, error: 'Input not found' };

                // 保存原始状态
                const wasReadonly = input.hasAttribute('readonly');

                // 移除 readonly，设置值，恢复 readonly
                input.removeAttribute('readonly');
                input.value = dateStr;
                input.dispatchEvent(new Event('change', { bubbles: true }));
                input.dispatchEvent(new Event('blur', { bubbles: true }));

                if (wasReadonly) input.setAttribute('readonly', 'readonly');

                return { success: true, value: input.value };
            }""", date_str)

            if result.get('success'):
                self.logger.info(f"开始日期已设置: {result.get('value')}")
            else:
                self.logger.warning(f"设置失败: {result.get('error')}")

            self._random_delay(0.5, 1)

        except Exception as e:
            self.logger.warning(f"设置日期范围失败: {e}")

    def _random_delay(self, min_sec: float = 1.0, max_sec: float = 3.0):
        """随机延时防 IP 封禁"""
        import time
        delay = random.uniform(min_sec, max_sec)
        time.sleep(delay)

    def _parse_hkex_date(self, date_str: str) -> str:
        """
        解析港交所日期格式
        兼容 DD/MM/YYYY HH:MM 格式（如：06/08/2026 18:30）
        返回 YYYY-MM-DD 格式（用于 date 字段）
        """
        if not date_str:
            return ''

        date_str = date_str.strip()

        # 去除中文前缀 "發放時間:"（发放时间）
        if '發放時間:' in date_str:
            date_str = date_str.split('發放時間:')[-1].strip()

        # 尝试 DD/MM/YYYY HH:MM 格式（港交所实际格式：06/08/2026 18:30）
        try:
            dt = datetime.strptime(date_str, '%d/%m/%Y %H:%M')
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            pass

        # 尝试 DD/MM/YYYY 格式
        try:
            dt = datetime.strptime(date_str, '%d/%m/%Y')
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            pass

        # 回退到基类解析
        return self._parse_time(date_str)

    def _parse_hkex_datetime(self, date_str: str) -> datetime | None:
        """
        解析港交所日期时间为 datetime 对象（用于精确时间过滤）
        支持 DD/MM/YYYY HH:MM 格式（如：06/08/2026 18:30）
        返回 datetime 或 None
        """
        if not date_str:
            return None

        date_str = date_str.strip()

        # 去除中文前缀 "發放時間:"（发放时间）
        if '發放時間:' in date_str:
            date_str = date_str.split('發放時間:')[-1].strip()

        # 尝试 DD/MM/YYYY HH:MM 格式
        try:
            return datetime.strptime(date_str, '%d/%m/%Y %H:%M')
        except ValueError:
            pass

        # 尝试 DD/MM/YYYY 格式（没时间则设为当天00:00）
        try:
            return datetime.strptime(date_str, '%d/%m/%Y')
        except ValueError:
            pass

        # 尝试标准格式 YYYY-MM-DD
        try:
            return datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            pass

        return None


def main():
    """测试运行 - 使用标准 run() 方法采集双股票数据"""
    import os

    hours_window = int(os.getenv('HKEX_HOURS_WINDOW', '24'))  # 默认24小时
    enable_summary = os.getenv('HKEX_ENABLE_SUMMARY', 'true').lower() == 'true'

    # 创建爬虫实例（自动采集双股票）
    crawler = HKEXCrawler(hours_window=hours_window, enable_summary=enable_summary)

    # 使用标准 run() 方法：采集 + 保存
    items = crawler.run()

    print(f"\n{'='*70}")
    print(f"港交所采集结果: {len(items)} 条")
    print(f"时间窗口: 过去{hours_window}小时")
    print(f"截止时间: {crawler.cutoff_time.strftime('%Y-%m-%d %H:%M')}")
    # if hours_window == 24:
    #     print("提示: 如需扩大时间窗口，设置环境变量 HKEX_HOURS_WINDOW=48")
    print('='*70)

    for i, item in enumerate(items, 1):
        print(f"\n{'─'*70}")
        print(f"{i}. [{item.category}] {item.title}")
        print(f"   日期: {item.date}")
        print(f"   链接: {item.url}")
        print(f"   来源代码: {item.source_code}")

        if item.raw_data and item.raw_data.get('pdf_path'):
            print(f"   PDF: {item.raw_data['pdf_path']}")

        if item.summary:
            print(f"   AI摘要: {item.summary[:150]}...")


if __name__ == "__main__":
    main()
