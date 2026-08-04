# -*- coding: utf-8 -*-
"""
证券时报e公司采集器
使用 requests + 从window.__INITIAL_STATE__提取数据
支持多关键词搜索、当天过滤、详情页正文抓取、AI 摘要
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import requests
import re
import json
from datetime import datetime
from typing import List, Dict, Set
from pathlib import Path
import time
import os

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from collectors.base import BaseCrawler
from models.news import NewsItem
from config.settings import CATEGORIES, COLLECTORS


class StcnCrawler(BaseCrawler):
    """证券时报e公司采集器"""

    source_name = "证券时报e公司"
    source_code = "stcn"
    credibility_base = "【媒体报道】"

    def __init__(self, enable_summary: bool = True, test_date: str | None = None, skip_date_filter: bool = False):
        # 从配置读取参数
        config = COLLECTORS.get('stcn', {})
        self.keywords = config.get('keywords', ['金山办公'])
        self.max_pages_per_keyword = 3  # 每个关键词最多爬取页数

        # 从配置读取 enable_summary（如果配置为 false 则覆盖传入值）
        config_enable_summary = config.get('enable_summary', True)
        final_enable_summary = enable_summary and config_enable_summary

        # 日期过滤开关
        self.skip_date_filter = skip_date_filter

        # 日期过滤（支持测试日期）
        # 优先使用传入的 test_date，其次从环境变量读取，最后使用当前日期
        import os
        env_date = os.getenv('STCN_TEST_DATE')
        target_date = test_date or env_date

        if self.skip_date_filter:
            # 跳过日期过滤模式
            self.today = target_date or datetime.now().strftime('%Y-%m-%d')
            self.today_slash = self.today.replace('-', '/')
            self.logger_info = f"⚠️ 日期过滤已禁用 - 参考日期: {self.today}"
        elif target_date:
            # 测试模式：使用指定日期
            self.today = target_date
            # 将 YYYY-MM-DD 转换为 YYYY/MM/DD
            self.today_slash = target_date.replace('-', '/')
            self.logger_info = f"测试模式 - 目标日期: {self.today}"
        else:
            # 正常模式：使用当天
            self.today = datetime.now().strftime('%Y-%m-%d')
            self.today_slash = datetime.now().strftime('%Y/%m/%d')
            self.logger_info = f"正常模式 - 当天日期: {self.today}"

        super().__init__(enable_summary=final_enable_summary)

        self.base_url = "https://egs.stcn.com"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        })

        # 已抓取的URL集合（用于去重）
        self.seen_urls: Set[str] = set()

    def _auto_classify(self, title: str) -> str:
        """自动分类"""
        title_lower = title.lower()
        scores = {}
        for category, rules in CATEGORIES.items():
            score = sum(1 for kw in rules['keywords'] if kw in title_lower)
            scores[category] = score
        if scores and max(scores.values()) > 0:
            return max(scores, key=scores.get)
        return "资本动态"

    def _is_today(self, time_str: str) -> bool:
        """判断时间是否为当天"""
        # 如果禁用日期过滤，总是返回 True
        if self.skip_date_filter:
            return True

        if not time_str:
            return False

        time_str = time_str.strip()

        # 直接匹配当天日期格式
        if self.today in time_str or self.today_slash in time_str:
            return True

        # 尝试解析时间字符串
        formats = [
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d %H:%M',
            '%Y-%m-%d',
            '%Y/%m/%d %H:%M:%S',
            '%Y/%m/%d %H:%M',
            '%Y/%m/%d',
            '%m-%d %H:%M',
            '%H:%M',  # 只有时间，假设是当天
        ]

        for fmt in formats:
            try:
                parsed = datetime.strptime(time_str, fmt)
                # 只有时间的格式，假设是当天
                if fmt == '%H:%M':
                    return True
                # 对比日期部分
                return parsed.strftime('%Y-%m-%d') == self.today
            except ValueError:
                continue

        # 如果无法解析，默认包含（避免漏掉）
        return True

    def _fetch_page(self, keyword: str, page: int = 1) -> List[Dict]:
        """获取单页搜索结果"""
        results = []

        import urllib.parse
        from bs4 import BeautifulSoup

        encoded = urllib.parse.quote(keyword)
        url = f"{self.base_url}/news/search.html?keyword={encoded}"
        if page > 1:
            url += f"&page={page}"

        self.logger.info(f"[{keyword}] 请求第{page}页: {url}")

        try:
            resp = self.session.get(url, timeout=30)
            resp.encoding = 'utf-8'

            if resp.status_code != 200:
                self.logger.error(f"[{keyword}] HTTP {resp.status_code}")
                return results

            html = resp.text

            # 调试：保存 HTML 查看结构
            debug_file = f"output/logs/stcn_debug_{keyword}_{page}.html"
            os.makedirs(os.path.dirname(debug_file), exist_ok=True)
            with open(debug_file, 'w', encoding='utf-8') as f:
                f.write(html[:50000])  # 只保存前 50000 字符
            self.logger.info(f"[{keyword}] 调试 HTML 已保存: {debug_file}")

            # 使用 BeautifulSoup 解析 HTML
            soup = BeautifulSoup(html, 'html.parser')

            # 查找资讯列表 - 在 data-type="news" 的 div 中
            news_container = soup.find('div', attrs={'data-type': 'news'})
            if not news_container:
                self.logger.warning(f"[{keyword}] 未找到资讯容器")
                return results

            # 查找所有列表项
            news_items = news_container.find_all('li')
            self.logger.info(f"[{keyword}] 找到 {len(news_items)} 个 li 元素")

            news_list = []
            for item in news_items:
                # 提取标题和链接
                title_elem = item.select_one('.title a')
                if not title_elem:
                    continue

                title = title_elem.get_text(strip=True)
                href = title_elem.get('href', '')

                # 提取时间
                time_elem = item.select_one('.info span:last-child')
                time_str = time_elem.get_text(strip=True) if time_elem else ''

                if title and href:
                    news_list.append({
                        'title': title,
                        'url': href if href.startswith('http') else self.base_url + href,
                        'time': time_str
                    })

            self.logger.info(f"[{keyword}] 第{page}页找到 {len(news_list)} 条有效资讯")

            # 过滤导航词
            nav_words = ['首页', '推荐', '快讯', '解读', '股市', '港股通', '视听', 'VIP', '更多', '下一页', '上一页']

            for news in news_list:
                if isinstance(news, dict):
                    title = news.get('title', '')
                    url = news.get('url', '')
                    time_str = news.get('time', news.get('ctime', news.get('publishTime', '')))
                else:
                    continue

                # 过滤无效数据
                if not title or len(title) < 10:
                    continue
                if any(w in title for w in nav_words):
                    continue
                if not url.startswith('http'):
                    url = self.base_url + url

                # 时间过滤 - 只保留当天
                if not self._is_today(time_str):
                    self.logger.debug(f"[{keyword}] 跳过非当天: {time_str} - {title[:30]}...")
                    continue

                results.append({
                    'title': title,
                    'url': url,
                    'time': time_str,
                    'keyword': keyword,
                })

        except Exception as e:
            self.logger.error(f"[{keyword}] 请求失败: {e}")

        return results

    def _fetch_content(self, url: str) -> str:
        """获取详情页正文内容"""
        try:
            from bs4 import BeautifulSoup

            resp = self.session.get(url, timeout=30)
            resp.encoding = 'utf-8'

            if resp.status_code != 200:
                self.logger.warning(f"详情页请求失败: {url} (HTTP {resp.status_code})")
                return ""

            soup = BeautifulSoup(resp.text, 'html.parser')

            # 尝试多种正文选择器
            content_selectors = [
                'article',  # 标准文章标签
                '.article-content',  # 常见文章容器
                '.content-detail',  # 详情内容
                '.news-content',  # 新闻内容
                '.text-content',  # 文本内容
                '#content',  # ID 选择器
                '.main-content',  # 主内容区
                '.detail-content',  # 详情内容
            ]

            content = ""
            for selector in content_selectors:
                element = soup.select_one(selector)
                if element:
                    # 提取纯文本
                    content = element.get_text(separator='\n', strip=True)
                    if len(content) > 100:  # 确保内容足够长
                        break

            # 如果上面的选择器都没找到，尝试提取 body 中的段落
            if not content or len(content) < 100:
                paragraphs = soup.find_all('p')
                content = '\n'.join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 20)

            # 清理内容
            content = self._clean_content(content)

            return content

        except Exception as e:
            self.logger.error(f"获取详情页失败: {url} - {e}")
            return ""

    def _clean_content(self, content: str) -> str:
        """清理正文内容"""
        if not content:
            return ""

        # 移除多余空白行
        lines = [line.strip() for line in content.split('\n') if line.strip()]
        content = '\n'.join(lines)

        # 移除常见的无关文本
        noise_patterns = [
            r'分享到.*',
            r'相关新闻.*',
            r'推荐阅读.*',
            r'热门文章.*',
            r'版权声明.*',
            r'免责声明.*',
            r'\(编辑.*\)',
            r'\(责任编辑.*\)',
            r'返回首页.*',
            r'返回顶部.*',
        ]

        for pattern in noise_patterns:
            content = re.sub(pattern, '', content, flags=re.IGNORECASE)

        return content.strip()

    def _parse_time(self, time_str: str) -> str:
        """解析时间字符串，返回 YYYY-MM-DD 格式"""
        if not time_str or time_str.strip() == '':
            return self.today

        time_str = time_str.strip()

        # 标准格式
        formats = [
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d %H:%M',
            '%Y-%m-%d',
            '%Y/%m/%d %H:%M:%S',
            '%Y/%m/%d %H:%M',
            '%Y/%m/%d',
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(time_str, fmt)
                return dt.strftime('%Y-%m-%d')
            except ValueError:
                continue

        # 只有时间的格式，返回当天
        if re.match(r'^\d{1,2}:\d{2}$', time_str):
            return self.today

        # 回退到基类解析
        return super()._parse_time(time_str) or self.today

    def _search_keyword(self, keyword: str) -> List[NewsItem]:
        """搜索单个关键词"""
        items = []

        self.logger.info(f"开始搜索关键词: {keyword}")

        for page in range(1, self.max_pages_per_keyword + 1):
            results = self._fetch_page(keyword, page)

            if not results:
                self.logger.info(f"[{keyword}] 第{page}页无结果，停止翻页")
                break

            for news in results:
                url = news['url']

                # 去重检查
                if url in self.seen_urls:
                    self.logger.debug(f"跳过重复: {news['title'][:30]}...")
                    continue
                self.seen_urls.add(url)

                # 获取详情页正文
                self.logger.info(f"获取正文: {news['title'][:50]}...")
                content = self._fetch_content(url)

                # 创建 NewsItem
                item = NewsItem(
                    title=news['title'],
                    date=self._parse_time(news['time']),
                    url=url,
                    source=self.source_name,
                    source_code=self.source_code,
                    credibility_tag=self.credibility_base,
                    category=self._auto_classify(news['title']),
                    content=content,
                    raw_data={'keyword': keyword, 'search_time': news['time']},
                )

                # 生成 AI 摘要（如果有正文）
                if content and len(content.strip()) > 50:
                    self.logger.info(f"生成 AI 摘要: {news['title'][:40]}...")
                    summary, summary_time = self.generate_summary(news['title'], content)
                    if summary:
                        item.summary = summary
                        item.summary_generated_at = summary_time
                        self.logger.info(f"✓ AI 摘要生成成功: {len(summary)} 字")
                    else:
                        self.logger.warning(f"✗ AI 摘要生成失败")
                        # 使用标题作为摘要
                        item.summary = news['title'][:150] + "..." if len(news['title']) > 150 else news['title']
                else:
                    # 无正文，使用标题作为摘要
                    item.summary = news['title'][:150] + "..." if len(news['title']) > 150 else news['title']

                items.append(item)

                # 礼貌延时
                time.sleep(1)

            # 翻页延时
            time.sleep(2)

        self.logger.info(f"[{keyword}] 搜索完成: {len(items)} 条")
        return items

    def fetch(self) -> List[NewsItem]:
        """采集数据（多关键词搜索合并）"""
        all_items = []

        self.logger.info(f"开始采集 - 关键词: {self.keywords}, 当天: {self.today}")

        # 创建批次目录（用于后续统一保存）
        now = datetime.now()
        date_dir = now.strftime('%Y/%m/%d')
        batch_name = now.strftime(f'{self.source_code}_%Y%m%d_%H%M%S')
        self._batch_dir = f"{self.output_dir}/{self.source_code}/{date_dir}/{batch_name}"
        os.makedirs(self._batch_dir, exist_ok=True)
        self.logger.info(f"批次目录: {self._batch_dir}")

        # 逐个关键词搜索
        for keyword in self.keywords:
            items = self._search_keyword(keyword)
            all_items.extend(items)
            self.logger.info(f"当前总计: {len(all_items)} 条")
            time.sleep(3)  # 关键词之间延时

        self.logger.info(f"采集完成: 共 {len(all_items)} 条（去重后）")
        return all_items


def main():
    """测试运行"""
    import os

    enable_summary = os.getenv('STCN_ENABLE_SUMMARY', 'true').lower() == 'true'
    test_date = os.getenv('STCN_TEST_DATE')  # 测试日期，如 2025-06-08
    skip_date_filter = os.getenv('STCN_SKIP_DATE_FILTER', 'false').lower() == 'true'  # 禁用日期过滤

    crawler = StcnCrawler(
        enable_summary=enable_summary,
        test_date=test_date,
        skip_date_filter=skip_date_filter
    )
    items = crawler.run()

    print(f"\n{'='*70}")
    print(f"证券时报e公司采集结果: {len(items)} 条")
    if skip_date_filter:
        print("⚠️  日期过滤已禁用 - 采集所有日期")
    else:
        print(f"过滤日期: {crawler.today}")
    if test_date:
        print(f"📅 测试日期: {test_date}")
    print('='*70)

    for i, item in enumerate(items, 1):
        print(f"\n{'─'*70}")
        print(f"{i}. [{item.category}] {item.title}")
        print(f"   时间: {item.date}")
        print(f"   链接: {item.url}")
        print(f"   来源关键词: {item.raw_data.get('keyword', 'N/A')}")

        if item.summary:
            print(f"   AI摘要: {item.summary}")

        if item.content:
            preview = item.content[:200].replace('\n', ' ') if len(item.content) > 200 else item.content
            print(f"   内容预览: {preview}...")

    print(f"\n{'='*70}")
    print("数据已保存到批次目录")
    print("="*70)


if __name__ == "__main__":
    main()
