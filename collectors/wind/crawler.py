# -*- coding: utf-8 -*-
"""
Wind金融终端采集器
使用 Wind API 获取金融数据和研报信息
注意：Wind 需要商业授权和 API 密钥

参考第一财经采集器模式
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from datetime import datetime, timedelta
from typing import List, Dict, Set, Optional
from pathlib import Path
import time
import os
import re

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from collectors.base import BaseCrawler
from models.news import NewsItem
from config.settings import CATEGORIES, COLLECTORS


class WindCrawler(BaseCrawler):
    """
    Wind金融终端采集器

    Wind 是付费金融数据终端，需要以下条件才能使用：
    1. Wind 商业账号和授权
    2. Wind API 接口权限
    3. API 密钥配置

    如果无法满足以上条件，建议禁用此采集器或寻找替代数据源
    """

    source_name = "Wind金融终端"
    source_code = "wind"
    credibility_base = "【金融数据】"

    def __init__(self, enable_summary: bool = None, hours_window: int = 24):
        # 从配置读取参数
        config = COLLECTORS.get('wind', {})
        self.keywords = config.get('keywords', ['金山办公'])
        self.max_items_per_keyword = 50
        self.api_key = config.get('api_key', '')

        # 时间窗口（默认24小时）
        self.hours_window = hours_window
        self.cutoff_time = datetime.now() - timedelta(hours=hours_window)
        self.logger_info = f"时间窗口: 过去{hours_window}小时 ({self.cutoff_time.strftime('%Y-%m-%d %H:%M')} 至今)"

        # 从配置读取 enable_summary
        if enable_summary is None:
            enable_summary = config.get('enable_summary', False)

        super().__init__(enable_summary=enable_summary)

        # 检查 API 密钥
        if not self.api_key:
            self.api_key = os.getenv('WIND_API_KEY', '')

        if not self.api_key:
            self.logger.warning("Wind API 密钥未配置，采集器可能无法正常工作")
            self.logger.warning("请在 config/settings.py 中配置 api_key 或设置 WIND_API_KEY 环境变量")

        # 已抓取的URL集合
        self.seen_urls: Set[str] = set()

        # Wind API 基础URL（示例，实际需要根据Wind API文档调整）
        self.base_url = "https://api.wind.com.cn"

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
        """解析时间字符串，返回 YYYY-MM-DD 格式"""
        if not time_str:
            return datetime.now().strftime('%Y-%m-%d')

        time_str = time_str.strip()

        # 匹配 YYYY-MM-DD HH:MM
        match = re.search(r'(\d{4})-(\d{2})-(\d{2})', time_str)
        if match:
            return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"

        # 匹配 YYYY年MM月DD日
        match = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', time_str)
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"

        return datetime.now().strftime('%Y-%m-%d')

    def _parse_wind_time(self, time_str: str) -> datetime:
        """解析 Wind 的时间格式"""
        if not time_str:
            return datetime.now()

        time_str = time_str.strip()
        now = datetime.now()

        # Wind 通常使用标准格式
        # 尝试多种格式
        formats = [
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d %H:%M',
            '%Y-%m-%d',
            '%Y/%m/%d %H:%M:%S',
            '%Y/%m/%d',
        ]

        for fmt in formats:
            try:
                return datetime.strptime(time_str, fmt)
            except:
                continue

        # 解析失败，返回当前时间
        return now

    def _is_in_time_window(self, time_str: str) -> bool:
        """判断时间是否在过去指定小时内"""
        try:
            parsed_time = self._parse_wind_time(time_str)
            return parsed_time >= self.cutoff_time
        except:
            # 解析失败默认保留
            return True

    def _matches_keywords(self, title: str) -> bool:
        """检查标题是否包含任一关键词"""
        if not title:
            return False
        title_lower = title.lower()
        return any(kw.lower() in title_lower for kw in self.keywords)

    def _check_api_available(self) -> bool:
        """检查 Wind API 是否可用"""
        if not self.api_key:
            self.logger.error("Wind API 密钥未配置")
            return False

        # TODO: 实现实际的 API 连接测试
        # 这里只是一个示例，实际需要根据 Wind API 文档实现
        try:
            import requests
            headers = {'Authorization': f'Bearer {self.api_key}'}
            # 示例 API 端点，实际需要替换为真实的 Wind API
            response = requests.get(
                f"{self.base_url}/api/v1/health",
                headers=headers,
                timeout=10
            )
            return response.status_code == 200
        except Exception as e:
            self.logger.error(f"Wind API 连接测试失败: {e}")
            return False

    def _search_keyword(self, keyword: str) -> List[Dict]:
        """
        通过 Wind API 搜索关键词

        TODO: 需要根据实际的 Wind API 文档实现
        以下为示例实现框架
        """
        results = []

        if not self.api_key:
            self.logger.error("无法搜索：Wind API 密钥未配置")
            return results

        try:
            import requests

            self.logger.info(f"[{keyword}] 调用 Wind API 搜索")

            # TODO: 替换为实际的 Wind API 端点和参数
            # 以下为示例代码
            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json'
            }

            # 示例 API 调用 - 实际需要根据 Wind API 文档调整
            # Wind API 通常提供以下数据：
            # - 公司新闻 (WSI_News)
            # - 研究报告 (WSI_Report)
            # - 公告信息 (WSI_Notice)

            params = {
                'keyword': keyword,
                'start_date': self.cutoff_time.strftime('%Y%m%d'),
                'end_date': datetime.now().strftime('%Y%m%d'),
                'limit': self.max_items_per_keyword
            }

            # 示例：获取新闻数据
            # 实际 API 端点需要参考 Wind API 文档
            response = requests.get(
                f"{self.base_url}/api/v1/news/search",
                headers=headers,
                params=params,
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                # TODO: 根据实际的 API 响应格式解析数据
                # 以下为示例解析
                items = data.get('data', [])
                for item in items:
                    title = item.get('title', '')
                    if not title:
                        continue

                    results.append({
                        'title': title,
                        'url': item.get('url', ''),
                        'time': item.get('publish_time', ''),
                        'summary': item.get('summary', ''),
                        'source_type': item.get('source_type', 'news'),
                    })
            else:
                self.logger.error(f"Wind API 返回错误: {response.status_code}")

        except ImportError:
            self.logger.error("requests 库未安装，无法调用 Wind API")
        except Exception as e:
            self.logger.error(f"[{keyword}] Wind API 调用失败: {e}")

        return results

    def _fetch_content(self, url: str) -> str:
        """
        获取详情页内容

        Wind API 通常直接返回完整内容，不需要额外抓取
        """
        # TODO: 如果需要，实现从 URL 抓取详情页内容
        # Wind 的数据通常通过 API 直接返回，不需要网页抓取
        return ""

    def fetch(self) -> List[NewsItem]:
        """
        采集数据

        注意：Wind API 需要商业授权
        """
        all_items = []

        # 检查 API 密钥
        if not self.api_key:
            self.logger.error("Wind API 密钥未配置，跳过采集")
            self.logger.error("请完成以下步骤之一：")
            self.logger.error("1. 在 config/settings.py 中配置 wind.api_key")
            self.logger.error("2. 设置 WIND_API_KEY 环境变量")
            return all_items

        self.logger.info(f"开始采集 Wind - 关键词: {self.keywords}")
        self.logger.info(self.logger_info)

        # 创建批次目录
        now = datetime.now()
        date_dir = now.strftime('%Y/%m/%d')
        batch_name = now.strftime(f'{self.source_code}_%Y%m%d_%H%M%S')
        self._batch_dir = f"{self.output_dir}/{self.source_code}/{date_dir}/{batch_name}"
        os.makedirs(self._batch_dir, exist_ok=True)

        try:
            for keyword in self.keywords:
                self.logger.info(f"搜索关键词: {keyword}")

                results = self._search_keyword(keyword)

                for news in results:
                    url = news.get('url', '')

                    # 去重
                    if url in self.seen_urls:
                        continue
                    if url:
                        self.seen_urls.add(url)

                    # 检查关键词匹配
                    if not self._matches_keywords(news['title']):
                        self.logger.debug(f"关键词过滤: {news['title'][:50]}...")
                        continue

                    # 检查时间窗口
                    if not self._is_in_time_window(news.get('time', '')):
                        self.logger.debug(f"时间过滤: {news['title'][:50]}...")
                        continue

                    # 创建 NewsItem
                    item = NewsItem(
                        title=news['title'],
                        date=self._parse_time(news.get('time', '')),
                        url=url or f"wind://search/{keyword}",
                        source=self.source_name,
                        source_code=self.source_code,
                        credibility_tag=self.credibility_base,
                        category=self._auto_classify(news['title']),
                        summary=news.get('summary', news['title'][:150]),
                        content=news.get('content', ''),
                        raw_data={
                            'keyword': keyword,
                            'source_type': news.get('source_type', 'news'),
                        },
                    )

                    # AI 摘要（如果内容足够）
                    content = news.get('content', '')
                    if content and len(content.strip()) > 50:
                        ai_summary, summary_time = self.generate_summary(news['title'], content)
                        if ai_summary:
                            item.summary = ai_summary
                            item.summary_generated_at = summary_time

                    all_items.append(item)
                    time.sleep(0.5)

                time.sleep(1)

        except Exception as e:
            self.logger.error(f"采集失败: {e}", exc_info=True)

        self.logger.info(f"采集完成: 共 {len(all_items)} 条")
        return all_items


def main():
    """测试运行"""
    import os

    # 检查 API 密钥
    api_key = os.getenv('WIND_API_KEY', '')
    if not api_key:
        print("\n" + "="*70)
        print("警告: Wind API 密钥未配置")
        print("="*70)
        print("Wind 是付费金融数据终端，需要商业授权才能使用。")
        print("\n使用方法:")
        print("1. 获取 Wind 账号和 API 授权")
        print("2. 在 config/settings.py 中配置 api_key")
        print("3. 或设置环境变量: export WIND_API_KEY=your_key")
        print("="*70 + "\n")

        # 如果没有密钥，仍然可以运行框架测试
        response = input("是否继续运行测试? (y/N): ")
        if response.lower() != 'y':
            return

    # 支持环境变量配置时间窗口（默认24小时）
    hours_window = int(os.getenv('WIND_HOURS_WINDOW', '24'))

    crawler = WindCrawler(hours_window=hours_window)
    items = crawler.run()

    print(f"\n{'='*70}")
    print(f"Wind金融终端采集结果: {len(items)} 条")
    print(f"时间窗口: 过去{hours_window}小时")
    print(f"截止时间: {crawler.cutoff_time.strftime('%Y-%m-%d %H:%M')}")
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
