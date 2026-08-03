# -*- coding: utf-8 -*-
"""
巨潮资讯采集器（PDF 版本）
下载公告 PDF -> 解析文本 -> AI 摘要
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from datetime import datetime
from typing import List, Optional, Tuple
from pathlib import Path

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from collectors.base import BaseCrawler
from models.news import NewsItem
from config.settings import COLLECTORS


class CNInfoCrawler(BaseCrawler):
    """巨潮资讯采集器"""

    source_name = "巨潮资讯网"
    source_code = "cninfo"
    credibility_base = "【官方公告】"

    def __init__(self, max_items: int = 5, enable_summary: bool = True):
        """
        初始化

        Args:
            max_items: 最多抓取几条公告（默认5条）
            enable_summary: 是否启用 AI 摘要（默认开启）
        """
        config = COLLECTORS.get('cninfo', {})
        self.stock_code = config.get('stock_code', '688111')
        self.org_id = config.get('org_id', '9900035303')
        self.max_items = max_items
        self.enable_summary = enable_summary

        # 初始化 PDF 处理器（不指定下载目录，后续动态设置）
        try:
            from models.pdf_processor import PDFProcessor
            self.pdf_processor = PDFProcessor()  # 不指定默认目录
        except Exception as e:
            self.logger.error(f"PDF 处理器初始化失败: {e}")
            self.pdf_processor = None

        # 延迟导入 AI 摘要器
        self.summarizer = None
        if enable_summary:
            try:
                from models.ai_summarizer import get_summarizer
                self.summarizer = get_summarizer()
                if not self.summarizer.is_available():
                    self.logger.warning("AI 摘要服务不可用，将只下载 PDF 不生成摘要")
            except Exception as e:
                self.logger.warning(f"AI 摘要模块加载失败: {e}")

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

    def _extract_pdf_content(self, detail_url: str) -> Tuple[Optional[str], Optional[str]]:
        """
        从详情页提取 PDF 内容

        Returns:
            (PDF本地路径, 提取的文本内容)
        """
        if not self.pdf_processor:
            self.logger.warning("PDF 处理器不可用")
            return None, None

        result = self.pdf_processor.process(detail_url, self.stock_code)
        if result:
            return result

        return None, None

    def _generate_summary(self, title: str, content: str) -> Tuple[Optional[str], Optional[datetime]]:
        """生成 AI 摘要，返回 (摘要, 生成时间)"""
        if not self.summarizer or not self.summarizer.is_available():
            return None, None

        if not content or len(content.strip()) < 50:
            return None, None

        result = self.summarizer.summarize(title, content, max_length=150)
        if result:
            return result  # 已经是 (summary, datetime) 元组
        return None, None

    def fetch(self) -> List[NewsItem]:
        """抓取公告数据（下载 PDF 并解析）"""
        from playwright.sync_api import sync_playwright
        import os

        items = []

        self.logger.info(f"开始采集巨潮资讯 - 股票代码: {self.stock_code}, 计划采集: {self.max_items}条")

        # 创建批次目录
        from datetime import datetime
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
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            # 访问列表页
            url = f"http://www.cninfo.com.cn/new/disclosure/stock?stockCode={self.stock_code}&orgId={self.org_id}"
            self.logger.info(f"访问URL: {url}")

            page.goto(url)
            page.wait_for_timeout(3000)

            # 提取公告列表数据
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

                        const dateMatch = texts.find(t => /\\d{4}-\\d{2}-\\d{2}/.test(t));
                        const title = texts.filter(t => t && !/\\d{4}-\\d{2}-\\d{2}/.test(t))
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

            self.logger.info(f"列表页提取到 {len(announcements)} 条公告")

            # 限制数量，逐个处理
            announcements = announcements[:self.max_items]

            for idx, ann in enumerate(announcements, 1):
                if not ann['date'] or not ann['title']:
                    continue

                self.logger.info(f"[{idx}/{len(announcements)}] 处理: {ann['title'][:50]}...")

                # 创建基础 NewsItem
                item = NewsItem(
                    title=ann['title'],
                    date=ann['date'],
                    url=ann['url'],
                    source=self.source_name,
                    source_code=self.source_code,
                    credibility_tag=self.credibility_base,
                    category=self._auto_classify(ann['title']),
                )

                # 下载并解析 PDF
                if ann['url'] and self.pdf_processor:
                    self.logger.info(f"  正在下载 PDF...")
                    pdf_path, content = self._extract_pdf_content(ann['url'])

                    if pdf_path and content:
                        item.content = content
                        # 存储相对路径（相对于批次目录）
                        rel_pdf_path = os.path.relpath(pdf_path, self._batch_dir)
                        item.raw_data = {'pdf_path': rel_pdf_path}
                        self.logger.info(f"  ✓ PDF 解析成功: {len(content)} 字符")

                        # 生成 AI 摘要
                        if self.enable_summary and self.summarizer:
                            self.logger.info(f"  正在生成 AI 摘要...")
                            summary, summary_time = self._generate_summary(ann['title'], content)
                            if summary:
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

            browser.close()

        self.logger.info(f"采集完成: {len(items)} 条（含 PDF 和摘要）")
        return items


def main():
    """测试运行"""
    import os
    max_items = int(os.getenv('CNINFO_MAX_ITEMS', '5'))
    enable_summary = os.getenv('CNINFO_ENABLE_SUMMARY', 'true').lower() == 'true'

    crawler = CNInfoCrawler(max_items=max_items, enable_summary=enable_summary)
    items = crawler.run()

    print(f"\n{'='*70}")
    print(f"采集结果: {len(items)} 条")
    print('='*70)

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
            if item.summary_generated_at:
                print(f"   摘要时间: {item.summary_generated_at.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            print(f"   AI摘要: [未生成]")

        if item.content:
            preview = item.content[:200].replace('\n', ' ') if len(item.content) > 200 else item.content
            print(f"   内容预览: {preview}...")

    print(f"\n{'='*70}")
    print("数据结构：")
    print("  - JSON文件：包含标题、日期、摘要、PDF路径")
    print("  - output/data/pdfs/：下载的 PDF 文件")
    print("="*70)


if __name__ == "__main__":
    main()
