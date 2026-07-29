# -*- coding: utf-8 -*-
"""
证券时报e公司采集器
使用 requests + 从window.__INITIAL_STATE__提取数据
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import requests
import re
import json
from datetime import datetime
from typing import List
import time

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))

from collectors.base import BaseCrawler, NewsItem
from config.settings import CATEGORIES


class StcnCrawler(BaseCrawler):
    """证券时报e公司采集器"""

    source_name = "证券时报e公司"
    source_code = "stcn"
    credibility_base = "【媒体报道】"

    def __init__(self):
        super().__init__()
        self.base_url = "https://egs.stcn.com"
        self.search_keyword = "金山办公"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        })

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

    def _parse_time(self, time_str: str) -> str:
        """解析时间"""
        if not time_str:
            return datetime.now().strftime('%Y-%m-%d')
        # 如果是时间戳
        if isinstance(time_str, int):
            return datetime.fromtimestamp(time_str).strftime('%Y-%m-%d %H:%M')
        return str(time_str)

    def _fetch_page(self, page: int = 1) -> List[NewsItem]:
        """获取单页"""
        items = []

        import urllib.parse
        encoded = urllib.parse.quote(self.search_keyword)
        url = f"{self.base_url}/news/search.html?keyword={encoded}"
        if page > 1:
            url += f"&page={page}"

        self.logger.info(f"请求: {url}")

        try:
            resp = self.session.get(url, timeout=30)
            resp.encoding = 'utf-8'

            if resp.status_code != 200:
                self.logger.error(f"HTTP {resp.status_code}")
                return items

            html = resp.text

            # 方法1: 从 window.__INITIAL_STATE__ 提取JSON数据
            match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.+?});', html, re.DOTALL)
            if match:
                data = json.loads(match.group(1))
                # 根据实际结构解析
                if 'search' in data and 'list' in data['search']:
                    news_list = data['search']['list']
                else:
                    # 尝试其他路径
                    news_list = data.get('list', [])
            else:
                # 方法2: 正则提取新闻条目
                news_list = []
                # 查找新闻链接和标题
                pattern = r'<a[^>]*href="([^"]+)"[^>]*>([^<]{10,100})</a>'
                matches = re.findall(pattern, html)
                for href, title in matches:
                    if '/news/' in href and title.strip():
                        news_list.append({
                            'title': title.strip(),
                            'url': href if href.startswith('http') else self.base_url + href,
                            'time': ''
                        })

            self.logger.info(f"找到 {len(news_list)} 条")

            # 过滤和转换
            nav_words = ['首页', '推荐', '快讯', '解读', '股市', '港股通', '视听', 'VIP', '更多', '下一页', '上一页']

            for news in news_list:
                if isinstance(news, dict):
                    title = news.get('title', '')
                    url = news.get('url', '')
                    time_str = news.get('time', news.get('ctime', ''))
                else:
                    continue

                # 过滤
                if not title or len(title) < 10:
                    continue
                if any(w in title for w in nav_words):
                    continue
                if not url.startswith('http'):
                    url = self.base_url + url

                item = NewsItem(
                    title=title,
                    date=self._parse_time(time_str),
                    url=url,
                    source=self.source_name,
                    source_code=self.source_code,
                    credibility_tag=self.credibility_base,
                    category=self._auto_classify(title),
                    summary=''
                )
                items.append(item)

        except Exception as e:
            self.logger.error(f"请求失败: {e}")

        return items

    def fetch(self, max_pages: int = 3) -> List[NewsItem]:
        """采集数据"""
        all_items = []

        self.logger.info(f"开始采集 - 关键词: {self.search_keyword}")

        for page in range(1, max_pages + 1):
            items = self._fetch_page(page)
            if not items:
                break
            all_items.extend(items)
            time.sleep(2)

        self.logger.info(f"采集完成: {len(all_items)} 条")
        return all_items


def main():
    crawler = StcnCrawler()
    items = crawler.run()

    print(f"\n{'='*60}")
    print(f"证券时报e公司采集结果: {len(items)} 条")
    print('='*60)

    for i, item in enumerate(items[:10], 1):
        print(f"\n{i}. [{item.category}] {item.title}")
        print(f"   时间: {item.date}")
        print(f"   链接: {item.url}")


if __name__ == "__main__":
    main()
