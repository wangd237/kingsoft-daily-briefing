# -*- coding: utf-8 -*-
"""
巨潮资讯采集器
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from datetime import datetime
from typing import List
from playwright.sync_api import sync_playwright

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))

from collectors.base import BaseCrawler, NewsItem
from config.settings import COLLECTORS


class CNInfoCrawler(BaseCrawler):
    """巨潮资讯采集器"""

    source_name = "巨潮资讯网"
    source_code = "cninfo"
    credibility_base = "【官方公告】"

    def __init__(self):
        config = COLLECTORS.get('cninfo', {})
        self.stock_code = config.get('stock_code', '688111')
        self.org_id = config.get('org_id', '9900035303')
        super().__init__()

    def _auto_classify(self, title: str) -> str:
        """自动分类"""
        from config.settings import CATEGORIES

        title_lower = title.lower()
        scores = {}

        for category, rules in CATEGORIES.items():
            score = sum(1 for kw in rules['keywords'] if kw in title_lower)
            scores[category] = score

        if scores and max(scores.values()) > 0:
            return max(scores, key=scores.get)

        return "资本动态"

    def fetch(self) -> List[NewsItem]:
        """抓取公告数据"""
        items = []

        self.logger.info(f"开始采集巨潮资讯 - 股票代码: {self.stock_code}")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            url = f"http://www.cninfo.com.cn/new/disclosure/stock?stockCode={self.stock_code}&orgId={self.org_id}"
            self.logger.info(f"访问URL: {url}")

            page.goto(url)
            page.wait_for_timeout(3000)

            # 提取公告数据
            announcements = page.evaluate('''() => {
                let rows = document.querySelectorAll('.el-table__body-wrapper .el-table__row');
                if (rows.length === 0) {
                    rows = document.querySelectorAll('.el-table tbody tr');
                }
                if (rows.length === 0) {
                    rows = document.querySelectorAll('table tr');
                }

                const data = [];
                rows.forEach(row => {
                    const cells = row.querySelectorAll('td');
                    if (cells.length >= 2) {
                        const texts = [];
                        cells.forEach(c => texts.push(c.textContent?.trim() || ''));

                        const dateMatch = texts.find(t => /\d{4}-\d{2}-\d{2}/.test(t));
                        const title = texts.filter(t => t && !/\d{4}-\d{2}-\d{2}/.test(t))
                                          .sort((a,b) => b.length - a.length)[0] || '';
                        const link = row.querySelector('a')?.href || '';

                        data.push({
                            title: title,
                            date: dateMatch || '',
                            url: link
                        });
                    }
                });
                return data;
            }''')

            browser.close()

            # 转换为NewsItem
            for ann in announcements:
                if ann['date'] and ann['title']:
                    item = NewsItem(
                        title=ann['title'],
                        date=ann['date'],
                        url=ann['url'],
                        source=self.source_name,
                        source_code=self.source_code,
                        credibility_tag=self.credibility_base,
                        category=self._auto_classify(ann['title']),
                    )
                    items.append(item)

        self.logger.info(f"采集完成: {len(items)} 条")
        return items


def main():
    """测试运行"""
    crawler = CNInfoCrawler()
    items = crawler.run()

    print(f"\n{'='*60}")
    print(f"采集结果: {len(items)} 条")
    print('='*60)

    for i, item in enumerate(items[:5], 1):
        print(f"\n{i}. [{item.category}] {item.title}")
        print(f"   日期: {item.date}")
        print(f"   链接: {item.url}")


if __name__ == "__main__":
    main()
