# -*- coding: utf-8 -*-
"""港交所披露易采集器
使用 Playwright 浏览器自动化
支持多股票：03888.HK（金山软件）、03896.HK（金山云）
"""
import sys
from pathlib import Path
from datetime import datetime

sys.path.append(str(Path(__file__).parent.parent.parent))

from collectors.base import BaseCrawler, NewsItem
from config.settings import CATEGORIES


class HKEXCrawler(BaseCrawler):
    """港交所披露易采集器"""

    source_name = "港交所披露易"
    source_code = "hkex"
    credibility_base = "【官方公告】"

    def __init__(self, stock_code: str = "03888"):
        super().__init__()
        self.stock_code = stock_code
        self.stock_name = "金山软件" if stock_code == "03888" else "金山云"
        # 直接使用已完成的搜索URL（预先执行过搜索）
        self.search_url = f"https://www.hkexnews.hk/search/titlesearch.xhtml?lang=zh-HK"

    def _auto_classify(self, title: str) -> str:
        """自动分类"""
        title_lower = title.lower()
        scores = {
            cat: sum(1 for kw in rules['keywords'] if kw in title_lower)
            for cat, rules in CATEGORIES.items()
        }
        if scores and max(scores.values()) > 0:
            return max(scores, key=lambda k: scores[k])
        return "①资本动态"

    def _parse_time(self, time_str: str) -> str:
        """解析时间"""
        if not time_str:
            return datetime.now().strftime('%Y-%m-%d')

        time_str = time_str.strip()
        formats = ['%d/%m/%Y', '%Y-%m-%d', '%Y/%m/%d', '%d-%m-%Y']

        for fmt in formats:
            try:
                return datetime.strptime(time_str, fmt).strftime('%Y-%m-%d')
            except ValueError:
                continue

        return time_str

    def fetch(self, max_pages: int = 1) -> list[NewsItem]:
        """采集公告数据"""
        items = []
        self.logger.info(f"开始采集港交所 - 股票: {self.stock_code} ({self.stock_name})")

        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
            )

            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0.36'
            )

            page = context.new_page()

            try:
                # 访问搜索页面
                self.logger.info(f"访问: {self.search_url}")
                page.goto(self.search_url, wait_until="load", timeout=60000)
                page.wait_for_timeout(5000)

                # 等待搜索框加载
                try:
                    page.wait_for_selector("input#searchStockCode", timeout=15000)
                except:
                    self.logger.warning("搜索框未加载，尝试其他方式")

                # 执行搜索 - 使用JavaScript直接填充和提交
                page.evaluate(f"""() => {{
                    // 查找并填充股票代码
                    const stockInput = document.querySelector('input#searchStockCode');
                    if (stockInput) {{
                        stockInput.value = '{self.stock_code}';
                        stockInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        stockInput.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    }}

                    // 触发搜索 - 查找搜索按钮或提交表单
                    const searchBtn = document.querySelector('button.search-btn, .search-btn, button[type="submit"]');
                    if (searchBtn) {{
                        searchBtn.click();
                    }} else {{
                        // 尝试提交表单
                        const form = document.querySelector('form');
                        if (form) form.submit();
                    }}
                }}""")

                # 等待搜索结果加载
                page.wait_for_timeout(8000)

                # 提取公告列表
                results = page.evaluate("""() => {
                    const data = [];

                    // 查找表格行
                    const rows = document.querySelectorAll('table tbody tr');

                    rows.forEach(row => {
                        const cells = row.querySelectorAll('td');
                        if (cells.length >= 4) {
                            const date = cells[0]?.textContent?.trim() || '';
                            const stockCode = cells[1]?.textContent?.trim() || '';
                            const stockName = cells[2]?.textContent?.trim() || '';
                            const docCell = cells[3];
                            const linkEl = docCell?.querySelector('a');
                            const title = linkEl?.textContent?.trim() || '';
                            const href = linkEl?.href || '';

                            // 过滤：只保留目标股票代码的公告
                            if (title && title.length > 3 && stockCode.includes('{self.stock_code}')) {
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

                    return {
                        items: data,
                        pageTitle: document.title,
                        url: window.location.href,
                        rowCount: rows.length,
                        recordCount: document.querySelector('.total-records')?.textContent || ''
                    };
                }""")

                self.logger.info(f"页面: {results.get('pageTitle')}")
                self.logger.info(f"记录数: {results.get('recordCount')}")
                self.logger.info(f"表格行数: {results.get('rowCount', 0)}")

                raw_items = results.get('items', [])
                self.logger.info(f"找到 {len(raw_items)} 条数据")

                # 过滤和转换
                seen_titles = set()
                invalid_words = ['Search', 'Guide', 'Help', 'Disclaimer']

                for news in raw_items:
                    title = news.get('title', '')
                    url = news.get('url', '')

                    if not title or len(title) < 5:
                        continue
                    if title in seen_titles:
                        continue
                    if any(w in title for w in invalid_words):
                        continue

                    seen_titles.add(title)

                    item = NewsItem(
                        title=f"{self.stock_name}: {title}",
                        date=self._parse_time(news.get('date', '')),
                        url=url,
                        source=f"港交所-{self.stock_name}",
                        source_code=f"hkex_{self.stock_code}",
                        credibility_tag=self.credibility_base,
                        category=self._auto_classify(title),
                        summary=''
                    )
                    items.append(item)

                # 截图调试
                Path("output/logs").mkdir(parents=True, exist_ok=True)
                page.screenshot(path=f"output/logs/hkex_{self.stock_code}_check.png")
                self.logger.info("截图已保存")

            except Exception as e:
                self.logger.error(f"采集失败: {e}", exc_info=True)
            finally:
                context.close()
                browser.close()

        self.logger.info(f"采集完成: {len(items)} 条有效数据")
        return items


def main():
    """测试运行"""
    all_items = []

    for code in ["03888", "03896"]:
        print(f"\n{'='*60}")
        print(f"采集股票代码: {code}")
        print('='*60)

        crawler = HKEXCrawler(stock_code=code)
        items = crawler.run()
        all_items.extend(items)

        for i, item in enumerate(items[:5], 1):
            print(f"\n{i}. [{item.category}] {item.title}")
            print(f"   时间: {item.date}")
            print(f"   链接: {item.url}")

    print(f"\n{'='*60}")
    print(f"港交所总采集结果: {len(all_items)} 条")
    print('='*60)


if __name__ == "__main__":
    main()
