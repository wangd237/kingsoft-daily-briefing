# -*- coding: utf-8 -*-
"""
巨潮资讯采集器（PDF 版本）
下载公告 PDF -> 解析文本 -> AI 摘要
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from datetime import datetime, timedelta  # 新增 timedelta
from typing import List, Optional, Tuple
from pathlib import Path

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from collectors.base import BaseCrawler
from models.news import NewsItem
from config.settings import COLLECTORS


class CNInfoCrawler(BaseCrawler):
    """巨潮资讯采集器（PDF 版本）
    下载公告 PDF -> 解析文本 -> AI 摘要
    支持全量采集 + 时间窗口过滤（默认24小时）
    """

    source_name = "巨潮资讯网"
    source_code = "cninfo"
    credibility_base = "【官方公告】"

    def __init__(self, max_items: int = None, enable_summary: bool = None, hours_window: int = None):
        """
        初始化

        Args:
            max_items: 已废弃，保留仅用于兼容，不再使用。现采用全量采集+时间过滤
            enable_summary: 是否启用 AI 摘要（默认从配置读取）
            hours_window: 时间窗口（小时），默认24小时
        """
        config = COLLECTORS.get('cninfo', {})
        self.stock_code = config.get('stock_code', '688111')
        self.org_id = config.get('org_id', '9900035303')

        # 时间窗口（默认24小时）
        self.hours_window = hours_window or config.get('hours_window', 24)
        self.cutoff_time = datetime.now() - timedelta(hours=self.hours_window)
        self.logger_info = f"时间窗口: 过去{self.hours_window}小时 ({self.cutoff_time.strftime('%Y-%m-%d %H:%M')} 至今)"

        # 废弃警告
        if max_items is not None and max_items != 5:
            self.logger.warning(f"max_items 参数已废弃，当前使用全量采集+时间过滤，忽略 max_items={max_items}")

        # 从配置读取 enable_summary，未设置则默认为 True
        if enable_summary is None:
            enable_summary = config.get('enable_summary', True)

        # 初始化 PDF 处理器（不指定下载目录，后续动态设置）
        try:
            from models.pdf_processor import PDFProcessor
            self.pdf_processor = PDFProcessor()  # 不指定默认目录
        except Exception as e:
            self.logger.error(f"PDF 处理器初始化失败: {e}")
            self.pdf_processor = None

        super().__init__(enable_summary=enable_summary)

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

    def _parse_cninfo_time(self, time_str: str) -> datetime:
        """
        解析巨潮资讯的公告时间
        支持格式: YYYY-MM-DD, YYYY-MM-DD HH:MM:SS, YYYY/MM/DD
        解析失败返回当前时间（保留数据）
        """
        if not time_str:
            self.logger.warning(f"⚠️ 时间解析失败(空值)，已回退到今日时间")
            return datetime.now()

        time_str = time_str.strip()

        # 标准格式 YYYY-MM-DD
        try:
            return datetime.strptime(time_str, '%Y-%m-%d')
        except ValueError:
            pass

        # 带时间格式 YYYY-MM-DD HH:MM:SS
        try:
            return datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            pass

        # 带时间格式 YYYY-MM-DD HH:MM
        try:
            return datetime.strptime(time_str, '%Y-%m-%d %H:%M')
        except ValueError:
            pass

        # 斜杠格式 YYYY/MM/DD
        try:
            return datetime.strptime(time_str, '%Y/%m/%d')
        except ValueError:
            pass

        # 解析失败，返回当前时间（保留数据）
        self.logger.warning(f"⚠️ 时间解析失败('{time_str}')，已回退到今日时间")
        return datetime.now()

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

    def fetch(self) -> List[NewsItem]:
        """抓取公告数据（下载 PDF 并解析）

        采集流程：
        1. 全量提取公告列表
        2. 按时间窗口过滤（默认24小时）
        3. 逐个下载PDF并解析
        """
        from playwright.sync_api import sync_playwright
        import os

        items = []

        self.logger.info(f"开始采集巨潮资讯 - 股票代码: {self.stock_code}")
        self.logger.info(self.logger_info)  # 打印时间窗口信息

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

            # 【改造】时间窗口过滤（全量采集后、处理详情前）
            filtered = []
            for ann in announcements:
                if not ann['date']:
                    continue
                ann_date = self._parse_cninfo_time(ann['date'])
                if ann_date >= self.cutoff_time:
                    filtered.append(ann)
                else:
                    self.logger.debug(f"[时间过滤] {ann['title'][:40]}... ({ann['date']})")

            before_count = len(announcements)
            announcements = filtered
            self.logger.info(f"时间过滤: {len(announcements)}/{before_count} 条（截止时间: {self.cutoff_time.strftime('%Y-%m-%d %H:%M')}）")

            # 逐个处理（下载PDF等）
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

                        # 生成 AI 摘要（使用基类方法）
                        self.logger.info(f"  正在生成 AI 摘要...")
                        summary, summary_time = self.generate_summary(ann['title'], content)
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

        self.logger.info(f"采集完成: {len(items)} 条（全量采集+时间过滤后）")
        return items


def main():
    """测试运行"""
    import os

    hours_window = int(os.getenv('CNINFO_HOURS_WINDOW', '500'))
    enable_summary = os.getenv('CNINFO_ENABLE_SUMMARY', 'true').lower() == 'true'

    crawler = CNInfoCrawler(hours_window=hours_window, enable_summary=enable_summary)
    items = crawler.run()

    print(f"\n{'='*70}")
    print(f"巨潮资讯采集结果: {len(items)} 条")
    print(f"时间窗口: 过去{hours_window}小时")
    print(f"截止时间: {crawler.cutoff_time.strftime('%Y-%m-%d %H:%M')}")
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
