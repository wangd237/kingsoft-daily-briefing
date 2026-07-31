# -*- coding: utf-8 -*-
"""
巨潮资讯采集器
支持详情页内容抓取和 AI 摘要生成
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from datetime import datetime
from typing import List, Optional
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

        # 延迟导入 AI 摘要器
        self.summarizer = None
        if enable_summary:
            try:
                from models.ai_summarizer import get_summarizer
                self.summarizer = get_summarizer()
                if not self.summarizer.is_available():
                    self.logger.warning("AI 摘要服务不可用，将只抓取正文不生成摘要")
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

    def _extract_content_from_detail(self, page, url: str) -> Optional[str]:
        """
        访问详情页并提取公告正文

        Args:
            page: Playwright page 对象
            url: 详情页 URL

        Returns:
            正文纯文本，失败返回 None
        """
        try:
            self.logger.debug(f"访问详情页: {url}")
            page.goto(url, wait_until='networkidle', timeout=15000)
            page.wait_for_timeout(2000)  # 等待内容渲染

            # 尝试多种选择器提取正文
            content_selectors = [
                '.detail-content',      # 常见正文容器
                '.main-content',
                '.content',
                '#content',
                '.announcement-detail',
                'article',
                '.page-content',
            ]

            content = None

            # 方法1：查找专门的正文容器
            for selector in content_selectors:
                try:
                    element = page.locator(selector).first
                    if element.count() > 0:
                        content = element.inner_text()
                        if content and len(content.strip()) > 100:
                            self.logger.debug(f"使用选择器 {selector} 提取到 {len(content)} 字符")
                            break
                except:
                    continue

            # 方法2：如果没找到，尝试提取所有段落
            if not content:
                paragraphs = page.locator('p, div').all_inner_texts()
                # 过滤太短的段落，合并长段落
                valid_paras = [p.strip() for p in paragraphs if len(p.strip()) > 30]
                if valid_paras:
                    content = '\n'.join(valid_paras[:50])  # 最多取50段
                    self.logger.debug(f"通过段落提取到 {len(content)} 字符")

            # 方法3：最后手段，提取 body 文本
            if not content:
                content = page.locator('body').inner_text()
                self.logger.debug(f"通过 body 提取到 {len(content)} 字符")

            if content:
                # 清理内容
                content = self._clean_content(content)
                return content

            return None

        except Exception as e:
            self.logger.error(f"详情页提取失败 {url}: {e}")
            return None

    def _clean_content(self, content: str) -> str:
        """清理公告正文"""
        if not content:
            return ""

        # 移除常见无用文本
        useless_patterns = [
            '证券代码：',
            '证券简称：',
            '公告编号：',
            '本公司董事会及全体董事保证本公告内容不存在任何虚假记载',
            '误导性陈述或者重大遗漏',
            '并对其内容的真实性、准确性和完整性承担法律责任',
            '↑↑↑',
            '返回顶部',
            '打印',
            '分享到',
        ]

        lines = content.split('\n')
        cleaned_lines = []

        for line in lines:
            line = line.strip()
            # 跳过空行
            if not line:
                continue
            # 跳过无用内容
            if any(pattern in line for pattern in useless_patterns):
                continue
            # 跳过过短的行（可能是导航等）
            if len(line) < 5:
                continue
            cleaned_lines.append(line)

        # 重新组合，限制长度
        result = '\n'.join(cleaned_lines)

        # 如果还是太长，取前8000字符
        if len(result) > 8000:
            result = result[:8000] + "\n...（内容已截断）"

        return result

    def _generate_summary(self, title: str, content: str) -> Optional[str]:
        """生成 AI 摘要"""
        if not self.summarizer or not self.summarizer.is_available():
            return None

        if not content or len(content.strip()) < 50:
            return None

        return self.summarizer.summarize(title, content, max_length=150)

    def fetch(self) -> List[NewsItem]:
        """抓取公告数据（包含详情页内容）"""
        items = []

        self.logger.info(f"开始采集巨潮资讯 - 股票代码: {self.stock_code}, 计划采集: {self.max_items}条")

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

            self.logger.info(f"列表页提取到 {len(announcements)} 条公告")

            # 限制数量，逐个抓取详情
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

                # 抓取详情页内容
                if ann['url']:
                    content = self._extract_content_from_detail(page, ann['url'])
                    if content:
                        item.content = content
                        self.logger.info(f"  ✓ 正文提取成功: {len(content)} 字符")

                        # 生成 AI 摘要
                        if self.enable_summary and self.summarizer:
                            summary = self._generate_summary(ann['title'], content)
                            if summary:
                                item.summary = summary
                                self.logger.info(f"  ✓ AI摘要生成成功: {len(summary)} 字")
                            else:
                                self.logger.warning(f"  ✗ AI摘要生成失败")
                    else:
                        self.logger.warning(f"  ✗ 正文提取失败")

                items.append(item)

            browser.close()

        self.logger.info(f"采集完成: {len(items)} 条（含详情内容和摘要）")
        return items


def main():
    """测试运行"""
    # 从环境变量读取配置，默认抓5条
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

        if item.content:
            content_preview = item.content[:200].replace('\n', ' ')
            print(f"   正文预览: {content_preview}...")
        else:
            print(f"   正文: [未获取]")

        if item.summary:
            print(f"   AI摘要: {item.summary}")
        else:
            print(f"   AI摘要: [未生成]")

    print(f"\n{'='*70}")
    print("数据已保存到 output/data/cninfo/ 目录")
    print('='*70)


if __name__ == "__main__":
    main()
