# -*- coding: utf-8 -*-
"""
36氪采集器
使用 requests + API/页面解析
支持多关键词搜索、详情页正文抓取、AI 摘要
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


class Kr36Crawler(BaseCrawler):
    """36氪采集器"""

    source_name = "36氪"
    source_code = "kr36"
    credibility_base = "【媒体报道】"

    def __init__(self, enable_summary: bool = None):
        # 从配置读取参数
        config = COLLECTORS.get('kr36', {})
        self.keywords = config.get('keywords', ['金山办公'])
        self.max_items_per_keyword = 5  # 每个关键词最多采集条数

        # 从配置读取 enable_summary
        if enable_summary is None:
            enable_summary = config.get('enable_summary', True)

        super().__init__(enable_summary=enable_summary)

        self.base_url = "https://36kr.com"
        self.api_base = "https://gateway.36kr.com"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Origin': 'https://36kr.com',
            'Referer': 'https://36kr.com/',
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
        return "产品动态"

    def _fetch_search_api(self, keyword: str) -> List[Dict]:
        """使用搜索API获取结果"""
        results = []

        try:
            # 36氪搜索API
            url = f"{self.api_base}/api/mis/open/search/articles"

            payload = {
                "keyword": keyword,
                "page": 1,
                "pageSize": self.max_items_per_keyword * 2,  # 多取一些用于过滤
                "sort": "published_at",  # 按发布时间排序
                "order": "desc"
            }

            self.logger.info(f"[{keyword}] 请求搜索API...")

            resp = self.session.post(url, json=payload, timeout=30)

            if resp.status_code != 200:
                self.logger.error(f"[{keyword}] API请求失败: HTTP {resp.status_code}")
                return results

            data = resp.json()

            if data.get('code') != 0:
                self.logger.error(f"[{keyword}] API返回错误: {data.get('msg', 'Unknown')}")
                return results

            items = data.get('data', {}).get('items', [])
            self.logger.info(f"[{keyword}] API返回 {len(items)} 条结果")

            for item in items:
                title = item.get('title', '').strip()
                summary = item.get('summary', '').strip()
                url = item.get('url', '')
                publish_time = item.get('published_at', '')

                if not title or not url:
                    continue

                # 补全URL
                if not url.startswith('http'):
                    url = f"{self.base_url}/p/{url}" if url.isdigit() else f"{self.base_url}{url}"

                results.append({
                    'title': title,
                    'url': url,
                    'summary': summary,
                    'time': publish_time,
                    'item_id': item.get('id', ''),
                })

        except Exception as e:
            self.logger.error(f"[{keyword}] API请求异常: {e}")

        return results

    def _fetch_content(self, url: str) -> str:
        """获取详情页正文内容"""
        try:
            resp = self.session.get(url, timeout=30)
            resp.encoding = 'utf-8'

            if resp.status_code != 200:
                self.logger.warning(f"详情页请求失败: {url} (HTTP {resp.status_code})")
                return ""

            html = resp.text

            # 尝试从 window.initialState 提取
            match = re.search(r'window\.initialState\s*=\s*({.+?});', html, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                    article = data.get('article', {}).get('articleDetail', {}).get('articleDetailData', {}).get('data', {})
                    content = article.get('content', '')
                    if content:
                        return self._clean_html_content(content)
                except:
                    pass

            # 备用：使用 BeautifulSoup 解析
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')

            content_selectors = [
                '.article-content',
                '.article-detail-content',
                '.content-detail',
                'article',
            ]

            for selector in content_selectors:
                element = soup.select_one(selector)
                if element:
                    content = element.get_text(separator='\n', strip=True)
                    if len(content) > 100:
                        return content

            return ""

        except Exception as e:
            self.logger.error(f"获取详情页失败: {url} - {e}")
            return ""

    def _clean_html_content(self, html: str) -> str:
        """清理HTML内容"""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')

        # 移除script和style
        for script in soup(['script', 'style']):
            script.decompose()

        # 获取文本
        text = soup.get_text(separator='\n', strip=True)

        # 清理多余空行
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        return '\n'.join(lines)

    def _parse_time(self, time_str: str) -> str:
        """解析时间字符串"""
        if not time_str:
            return datetime.now().strftime('%Y-%m-%d')

        # 时间戳（毫秒）
        if time_str.isdigit():
            try:
                timestamp = int(time_str) / 1000  # 毫秒转秒
                dt = datetime.fromtimestamp(timestamp)
                return dt.strftime('%Y-%m-%d')
            except:
                pass

        # ISO格式
        try:
            dt = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
            return dt.strftime('%Y-%m-%d')
        except:
            pass

        return datetime.now().strftime('%Y-%m-%d')

    def _search_keyword(self, keyword: str) -> List[NewsItem]:
        """搜索单个关键词"""
        items = []

        self.logger.info(f"开始搜索关键词: {keyword}")

        results = self._fetch_search_api(keyword)

        for news in results[:self.max_items_per_keyword]:
            url = news['url']

            # 去重检查
            if url in self.seen_urls:
                continue
            self.seen_urls.add(url)

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
                summary=news['summary'] or news['title'][:150],
                content=content,
                raw_data={'keyword': keyword},
            )

            # 生成 AI 摘要（如果有正文）
            if content and len(content.strip()) > 50:
                ai_summary, summary_time = self.generate_summary(news['title'], content)
                if ai_summary:
                    item.summary = ai_summary
                    item.summary_generated_at = summary_time

            items.append(item)
            time.sleep(1)

        self.logger.info(f"[{keyword}] 搜索完成: {len(items)} 条")
        return items

    def fetch(self) -> List[NewsItem]:
        """采集数据"""
        all_items = []

        self.logger.info(f"开始采集36氪 - 关键词: {self.keywords}")

        # 创建批次目录
        now = datetime.now()
        date_dir = now.strftime('%Y/%m/%d')
        batch_name = now.strftime(f'{self.source_code}_%Y%m%d_%H%M%S')
        self._batch_dir = f"{self.output_dir}/{self.source_code}/{date_dir}/{batch_name}"
        os.makedirs(self._batch_dir, exist_ok=True)

        for keyword in self.keywords:
            items = self._search_keyword(keyword)
            all_items.extend(items)
            time.sleep(2)

        self.logger.info(f"采集完成: 共 {len(all_items)} 条")
        return all_items


def main():
    """测试运行"""
    crawler = Kr36Crawler()
    items = crawler.run()

    print(f"\n{'='*70}")
    print(f"36氪采集结果: {len(items)} 条")
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
