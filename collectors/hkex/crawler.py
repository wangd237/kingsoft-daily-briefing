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

from datetime import datetime
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

    def __init__(self, stock_code: str = "03888", max_items: int = 5, enable_summary: bool | None = None):
        """
        初始化

        Args:
            stock_code: 股票代码（03888 或 03896）
            max_items: 最多抓取几条公告（默认5条）
            enable_summary: 是否启用 AI 摘要（默认从配置读取）
        """
        self.stock_code = stock_code
        self.stock_name = "金山软件" if stock_code == "03888" else "金山云"
        self.max_items = max_items

        # 从配置读取 enable_summary，未设置则默认为 True
        hkex_config = COLLECTORS.get('hkex', {})
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

    def _extract_pdf_content(self, pdf_url: str, cookies: list | None = None, referer: str | None = None) -> tuple[str | None, str | None]:
        """
        从 PDF URL 下载并提取内容

        Args:
            pdf_url: PDF 文件链接
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
            pdf_path = download_dir / f"{self.stock_code}_{file_id}.pdf"

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

    def fetch(self) -> list[NewsItem]:
        """采集公告数据（下载 PDF 并解析）"""
        from playwright.sync_api import sync_playwright
        import os

        items = []

        self.logger.info(f"开始采集港交所 - 股票: {self.stock_code} ({self.stock_name}), 计划采集: {self.max_items}条")

        # 创建批次目录
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
                headless=True,
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
                page.screenshot(path=f"output/logs/hkex_{self.stock_code}_step1_initial.png")

                # 等待搜索框加载并使用 Playwright 原生方法填充
                stock_input = page.locator("input#searchStockCode")
                stock_input.wait_for(state="visible", timeout=15000)
                stock_input.fill(self.stock_code)
                self.logger.info(f"已填充股票代码: {self.stock_code}")

                # 随机延时
                self._random_delay(1, 2)

                # 截图查看填充后
                page.screenshot(path=f"output/logs/hkex_{self.stock_code}_step2_filled.png")

                # 点击搜索按钮（纯 Class 定位，删除文本匹配）
                search_btn = page.locator('a.filter__btn-applyFilters-js').first
                search_btn.wait_for(state="visible", timeout=10000)
                search_btn.click()
                self.logger.info("已点击搜索按钮")

                # 等待搜索结果加载 - 等待表格出现（使用更通用的选择器）
                self.logger.info("等待搜索结果加载...")
                try:
                    # 尝试多种表格选择器
                    page.wait_for_selector("table tbody tr, .el-table__row, .search-result tr, [class*='table'] tr", timeout=30000)
                except Exception as e:
                    self.logger.warning(f"等待表格超时: {e}")

                # 随机延时让数据完全加载
                self._random_delay(2, 3)

                # 截图查看搜索结果
                page.screenshot(path=f"output/logs/hkex_{self.stock_code}_step3_results.png")

                # 获取 cookies 用于 PDF 下载
                cookies = context.cookies()
                current_url = page.url

                # 提取公告列表 - 使用更通用的表格选择器
                results = page.evaluate("""() => {
                    const data = [];

                    // 尝试多种表格选择器
                    let rows = document.querySelectorAll('.el-table__body tr.el-table__row');
                    if (rows.length === 0) {
                        rows = document.querySelectorAll('table tbody tr');
                    }
                    if (rows.length === 0) {
                        rows = document.querySelectorAll('.search-result tbody tr, .result-table tbody tr');
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

                # 限制数量，逐个处理
                raw_items = raw_items[:self.max_items]

                # 去重处理
                seen_titles = set()
                invalid_words = ['Search', 'Guide', 'Help', 'Disclaimer']

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

                    self.logger.info(f"[{idx}/{len(raw_items)}] 处理: {title[:50]}...")

                    # 解析日期（兼容 DD/MM/YYYY 港交所格式）
                    parsed_date = self._parse_hkex_date(date)

                    # 创建基础 NewsItem
                    item = NewsItem(
                        title=f"{self.stock_name}: {title}",
                        date=parsed_date,
                        url=url,
                        source=f"港交所-{self.stock_name}",
                        source_code=f"hkex_{self.stock_code}",
                        credibility_tag=self.credibility_base,
                        category=self._auto_classify(title),
                    )

                    # 下载并解析 PDF（带上 cookies 和 referer）
                    if url and self.pdf_processor:
                        self.logger.info(f"  正在下载 PDF...")
                        pdf_path, content = self._extract_pdf_content(url, cookies=cookies, referer=current_url)

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
                page.screenshot(path=f"output/logs/hkex_{self.stock_code}_final.png")
                self.logger.info("截图已保存")

            except Exception as e:
                self.logger.error(f"采集失败: {e}", exc_info=True)
            finally:
                context.close()
                browser.close()

        self.logger.info(f"采集完成: {len(items)} 条（含 PDF 和摘要）")
        return items

    def _random_delay(self, min_sec: float = 1.0, max_sec: float = 3.0):
        """随机延时防 IP 封禁"""
        import time
        delay = random.uniform(min_sec, max_sec)
        time.sleep(delay)

    def _parse_hkex_date(self, date_str: str) -> str:
        """
        解析港交所日期格式
        兼容 DD/MM/YYYY 格式
        """
        if not date_str:
            return ''

        date_str = date_str.strip()

        # 尝试 DD/MM/YYYY 格式（港交所常用）
        try:
            dt = datetime.strptime(date_str, '%d/%m/%Y')
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            pass

        # 尝试 DD/MM/YYYY HH:MM 格式
        try:
            dt = datetime.strptime(date_str, '%d/%m/%Y %H:%M')
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            pass

        # 回退到基类解析
        return self._parse_time(date_str)


def main():
    """测试运行 - 合并两个股票的采集结果到一个 JSON"""
    import json
    import os

    max_items = int(os.getenv('HKEX_MAX_ITEMS', '5'))
    enable_summary = os.getenv('HKEX_ENABLE_SUMMARY', 'true').lower() == 'true'

    all_items = []
    batch_dir = None

    for code in ["03888", "03896"]:
        print(f"\n{'='*70}")
        print(f"采集股票代码: {code}")
        print('='*70)

        # 创建爬虫实例
        crawler = HKEXCrawler(stock_code=code, max_items=max_items, enable_summary=enable_summary)

        try:
            # 只采集，不保存（通过直接调用 fetch 而不是 run）
            crawler.logger.info(f"开始采集: {crawler.source_name}")
            items = crawler.fetch()
            crawler.logger.info(f"采集完成: {len(items)} 条")

            # 保存第一个股票的批次目录，用于后续统一保存
            if batch_dir is None and crawler._batch_dir:
                batch_dir = crawler._batch_dir

            # 添加到总列表
            all_items.extend(items)

            # 打印本次采集结果
            for i, item in enumerate(items, 1):
                print(f"\n{'─'*70}")
                print(f"{i}. [{item.category}] {item.title}")
                print(f"   日期: {item.date}")
                print(f"   链接: {item.url}")

                if item.raw_data and item.raw_data.get('pdf_path'):
                    print(f"   PDF: {item.raw_data['pdf_path']}")
                else:
                    print(f"   PDF: [未下载]")

                if item.summary:
                    print(f"   AI摘要: {item.summary}")

        except Exception as e:
            crawler.logger.error(f"采集失败: {e}", exc_info=True)

    # ========== 合并保存所有数据到一个 JSON ==========
    if all_items and batch_dir:
        print(f"\n{'='*70}")
        print(f"合并保存: 共 {len(all_items)} 条数据")
        print('='*70)

        # 使用第一个股票的批次目录作为统一保存目录
        # 修改批次名称为合并版本
        merged_json_path = os.path.join(batch_dir, "hkex_merged_all_stocks.json")

        # 构建保存数据
        merged_data = {
            'source': '港交所披露易',
            'source_code': 'hkex',
            'fetch_time': datetime.now().isoformat(),
            'count': len(all_items),
            'stocks': ['03888', '03896'],
            'stock_names': ['金山软件', '金山云'],
            'items': [item.to_dict() for item in all_items]
        }

        # 保存合并的 JSON
        with open(merged_json_path, 'w', encoding='utf-8') as f:
            json.dump(merged_data, f, ensure_ascii=False, indent=2)

        print(f"✅ 合并数据已保存: {merged_json_path}")
        print(f"   包含股票: 03888 (金山软件) + 03896 (金山云)")
        print(f"   总条数: {len(all_items)}")

    print(f"\n{'='*70}")
    print(f"港交所总采集结果: {len(all_items)} 条")
    print("="*70)


if __name__ == "__main__":
    main()
