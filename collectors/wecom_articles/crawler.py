# -*- coding: utf-8 -*-
"""
公众号文章采集器（CLI 版本）
通过 wps365 CLI 读取 WPS 多维表格中的公众号文章链接，抓取正文内容。

工作流程：
1. 调用 wps365 CLI 读取多维表格记录
2. 按创建时间筛选前一天的记录
3. 提取"链接"字段中的文章 URL
4. 访问每个 URL 抓取公众号文章正文
5. 返回 NewsItem 列表供项目统一处理

前置依赖：
- wps365 CLI 已安装并认证（由 skill 的 setup.sh 和 auth.sh 自动处理）
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from collectors.base import BaseCrawler
from models.news import NewsItem
from config.settings import COLLECTORS

# ---------------------------------------------------------------- 常量

# 默认 User-Agent（模拟浏览器访问公众号文章）
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------- CLI 调用辅助

# wps365 CLI 路径：优先使用项目内副本，回退到系统 PATH
_WPS365_BIN = Path(__file__).parent.parent.parent / "tools" / "bin" / "wps365.exe"
if not _WPS365_BIN.exists():
    _WPS365_BIN = "wps365"  # 回退到系统 PATH


def run_wps365_cli(args: List[str], timeout: int = 60) -> dict:
    """
    调用 wps365 CLI 并返回 JSON 结果

    Args:
        args: CLI 参数列表（不含 wps365 前缀）
        timeout: 超时时间（秒）

    Returns:
        解析后的 JSON 字典

    Raises:
        RuntimeError: CLI 调用失败
    """
    cmd = [str(_WPS365_BIN)] + args + ["-o", "json"]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding='utf-8',
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"CLI 调用失败: {' '.join(cmd)}\n"
            f"stdout: {result.stdout[:500]}\n"
            f"stderr: {result.stderr[:500]}"
        )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"CLI 输出解析失败: {e}\nstdout: {result.stdout[:500]}"
        )


def read_bitable_records(file_id: str, sheet_id: int) -> List[dict]:
    """
    通过 wps365 CLI 读取多维表格全部记录

    Args:
        file_id: 多维表格 file_id
        sheet_id: 工作表 sheet_id

    Returns:
        记录列表
    """
    all_records = []
    page_token = ""

    for _ in range(100):  # 防死循环
        args = [
            "dbsheet", "record", "list",
            str(file_id),
            str(sheet_id),
            "--page-size", "100",
        ]
        if page_token:
            args.extend(["--page-token", page_token])

        data = run_wps365_cli(args)

        if data.get("code") != 0:
            raise RuntimeError(f"读取记录失败: {data.get('msg', '未知错误')}")

        records = data.get("data", {}).get("records") or []
        all_records.extend(records)

        page_token = data.get("data", {}).get("page_token") or ""
        if not page_token:
            break

    return all_records


# ---------------------------------------------------------------- 采集器

class WecomArticlesCrawler(BaseCrawler):
    """
    公众号文章采集器（CLI 版本）
    通过 wps365 CLI 读取 WPS 多维表格中的公众号文章链接，抓取正文内容

    表格字段映射：
    - 链接：文章 URL（MultiLineText）
    - 日期：创建时间（CreatedTime，自动生成）
    - 公众号：公众号名称（MultiLineText）
    - 备注：备注信息（MultiLineText）
    - 编号：自动编号（CustomAutoNumber）
    """

    source_name = "公众号文章"
    source_code = "wecom_articles"
    credibility_base = "【官方资讯】"

    def __init__(self, enable_summary: bool = None, hours_window: int = 24, test_mode: bool = False):
        config = COLLECTORS.get('wecom_articles', {})

        # 从配置读取 enable_summary
        if enable_summary is None:
            enable_summary = config.get('enable_summary', True)

        super().__init__(enable_summary=enable_summary)

        # WPS 表格配置
        self.file_id = config.get('file_id', '')
        self.sheet_id = config.get('sheet_id', 1)

        # 时间窗口
        self.hours_window = hours_window

        # 测试模式：跳过日期筛选，处理所有有链接的记录
        self.test_mode = test_mode or os.environ.get('WECOM_TEST_MODE', '0') == '1'
        if self.test_mode:
            self.logger.info("测试模式已启用：将处理所有有链接的记录（跳过日期筛选）")

    def _get_yesterday_str(self) -> str:
        """获取昨天日期字符串 YYYY/MM/DD"""
        yesterday = datetime.now() - timedelta(days=1)
        return yesterday.strftime('%Y/%m/%d')

    def _parse_created_time(self, time_str: str) -> Optional[datetime]:
        """解析 CreatedTime 字段（格式如 '2026/08/20 14:57:58'）"""
        if not time_str:
            return None

        formats = [
            '%Y/%m/%d %H:%M:%S',
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%dT%H:%M:%S',
            '%Y-%m-%dT%H:%M:%SZ',
            '%Y-%m-%dT%H:%M:%S+08:00',
        ]

        for fmt in formats:
            try:
                return datetime.strptime(time_str.strip(), fmt)
            except ValueError:
                continue

        # 尝试时间戳格式
        try:
            ts = float(time_str)
            if ts > 1e11:
                ts /= 1000  # 毫秒转秒
            return datetime.fromtimestamp(ts)
        except (ValueError, OSError):
            pass

        return None

    def _is_yesterday(self, time_str: str) -> bool:
        """判断时间是否是昨天（测试模式下总是返回 True）"""
        if self.test_mode:
            return True
        dt = self._parse_created_time(time_str)
        if not dt:
            return False
        return dt.date() == (datetime.now() - timedelta(days=1)).date()

    def _fetch_article_content(self, url: str) -> str:
        """抓取公众号文章正文内容"""
        try:
            headers = {
                'User-Agent': DEFAULT_USER_AGENT,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            }

            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or 'utf-8'

            html = resp.text
            return self._parse_wechat_article(html)

        except Exception as e:
            self.logger.error(f"抓取文章失败: {url} - {e}")
            return ""

    def _parse_wechat_article(self, html: str) -> str:
        """解析微信公众号文章 HTML，提取正文"""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')

            # 方式1：从 rich_media_content 提取（标准公众号文章）
            content_div = soup.find('div', id='js_content')
            if not content_div:
                content_div = soup.find('div', class_='rich_media_content')
            if not content_div:
                content_div = soup.find('div', id='img-content')

            if content_div:
                # 移除 script/style
                for tag in content_div.find_all(['script', 'style']):
                    tag.decompose()

                # 提取文本
                text = content_div.get_text(separator='\n', strip=True)
                # 清理多余空行
                lines = [line.strip() for line in text.split('\n') if line.strip()]
                return '\n'.join(lines)

            # 方式2：从 meta 标签提取 description
            meta_desc = soup.find('meta', attrs={'name': 'description'})
            if meta_desc and meta_desc.get('content'):
                return meta_desc['content'].strip()

        except ImportError:
            # 没有 bs4，用正则提取
            self.logger.warning("BeautifulSoup 未安装，使用正则提取")

        # 方式3：正则提取（兜底）
        match = re.search(r'var\s+content\s*=\s*["\'](.+?)["\']', html, re.DOTALL)
        if match:
            content = match.group(1)
            content = re.sub(r'<[^>]+>', '\n', content)
            lines = [line.strip() for line in content.split('\n') if line.strip()]
            return '\n'.join(lines)

        return ""

    def _extract_article_title(self, html: str) -> str:
        """从 HTML 中提取文章标题"""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')

            # 方式1：从 h1 标签提取
            h1 = soup.find('h1', class_='rich_media_title')
            if h1:
                return h1.get_text(strip=True)

            # 方式2：从 meta og:title 提取
            og_title = soup.find('meta', attrs={'property': 'og:title'})
            if og_title and og_title.get('content'):
                return og_title['content'].strip()

            # 方式3：从 title 标签提取
            title_tag = soup.find('title')
            if title_tag:
                return title_tag.get_text(strip=True)

        except ImportError:
            pass

        # 方式4：正则兜底
        match = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()

        return ""

    def _extract_article_meta(self, html: str) -> Dict[str, Any]:
        """提取文章元信息（作者、发布时间等）"""
        meta = {}

        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')

            # 作者
            author = soup.find('meta', attrs={'name': 'author'})
            if author and author.get('content'):
                meta['author'] = author['content'].strip()

            # 发布时间（从 JS 变量提取）
            time_match = re.search(r'var\s+ct\s*=\s*["\']?(\d{10})["\']?', html)
            if time_match:
                ts = int(time_match.group(1))
                meta['publish_time'] = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')

            # 封面图
            og_image = soup.find('meta', attrs={'property': 'og:image'})
            if og_image and og_image.get('content'):
                meta['cover'] = og_image['content'].strip()

        except ImportError:
            pass

        return meta

    def fetch(self) -> List[NewsItem]:
        """
        采集数据的主流程：
        1. 通过 wps365 CLI 读取多维表格记录
        2. 筛选昨天的记录
        3. 抓取每篇文章的内容
        4. 返回 NewsItem 列表
        """
        all_items: List[NewsItem] = []
        self.logger.info("开始采集公众号文章")
        self.logger.info(f"表格 file_id: {self.file_id}, sheet_id: {self.sheet_id}")

        if not self.file_id:
            self.logger.error("缺少表格 file_id，请在配置中设置 wecom_articles.file_id")
            return all_items

        try:
            # 读取全部记录
            self.logger.info("正在通过 wps365 CLI 读取记录...")
            records = read_bitable_records(self.file_id, self.sheet_id)
            self.logger.info(f"共读取 {len(records)} 条记录")

            # 筛选昨天的记录
            yesterday_items = []
            for record in records:
                fields_str = record.get('fields', '')
                if isinstance(fields_str, str):
                    fields = json.loads(fields_str)
                else:
                    fields = fields_str

                # 解析创建时间
                created_time = fields.get('日期', '')
                if not created_time or not self._is_yesterday(created_time):
                    continue

                # 提取链接
                url = fields.get('链接', '').strip()
                if not url:
                    continue

                # 提取公众号名称
                account = fields.get('公众号', '').strip()

                # 提取备注
                note = fields.get('备注', '').strip()

                yesterday_items.append({
                    'url': url,
                    'account': account,
                    'note': note,
                    'created_time': created_time,
                    'record_id': record.get('id', ''),
                })

            self.logger.info(f"筛选出昨天 {len(yesterday_items)} 条公众号文章")

            # 抓取每篇文章的内容
            for idx, item in enumerate(yesterday_items, 1):
                url = item['url']
                self.logger.info(f"[{idx}/{len(yesterday_items)}] 正在抓取: {url[:60]}...")

                # 抓取文章内容
                content = self._fetch_article_content(url)

                # 提取标题和元信息
                title = ""
                meta = {}
                if content:
                    try:
                        headers = {'User-Agent': DEFAULT_USER_AGENT}
                        resp = requests.get(url, headers=headers, timeout=30)
                        resp.raise_for_status()
                        resp.encoding = resp.apparent_encoding or 'utf-8'
                        html = resp.text
                        title = self._extract_article_title(html)
                        meta = self._extract_article_meta(html)
                    except Exception:
                        pass

                if not title:
                    title = f"{item['account'] or '公众号'}文章_{idx}"

                # 确定可信度标签
                credibility = self.credibility_base
                if item['account']:
                    official_accounts = [
                        '金山办公', 'WPS办公软件', 'WPS 365', '西山居', '剑网3',
                        '金山软件', '金山云', 'WPS',
                    ]
                    if any(acc in item['account'] for acc in official_accounts):
                        credibility = "【官方资讯】"
                    else:
                        credibility = "【媒体报道】"

                # 创建 NewsItem
                news_item = NewsItem(
                    title=title,
                    date=self._parse_time(item['created_time']),
                    url=url,
                    source=self.source_name,
                    source_code=self.source_code,
                    credibility_tag=credibility,
                    category=self._auto_classify(title),
                    summary=content[:150] + "..." if len(content) > 150 else content,
                    content=content,
                    raw_data={
                        'account': item['account'],
                        'note': item['note'],
                        'record_id': item['record_id'],
                        'created_time': item['created_time'],
                        'meta': meta,
                    },
                )

                # 生成 AI 摘要（如果启用）
                if content and len(content.strip()) > 100:
                    ai_summary, summary_time = self.generate_summary(title, content)
                    if ai_summary:
                        news_item.summary = ai_summary
                        news_item.summary_generated_at = summary_time

                all_items.append(news_item)
                time.sleep(0.5)  # 请求间隔

        except RuntimeError as e:
            self.logger.error(f"wps365 CLI 调用失败: {e}")
        except Exception as e:
            self.logger.error(f"采集异常: {e}", exc_info=True)

        self.logger.info(f"采集完成: 共 {len(all_items)} 条公众号文章")
        return all_items

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
        return "产品动态"  # 公众号文章默认分类


# ---------------------------------------------------------------- 主入口

def main():
    """测试运行"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    crawler = WecomArticlesCrawler()
    items = crawler.run()

    print(f"\n{'='*70}")
    print(f"公众号文章采集结果: {len(items)} 条")
    print('='*70)

    for i, item in enumerate(items, 1):
        print(f"\n{'─'*70}")
        print(f"{i}. [{item.category}] {item.title}")
        print(f"   时间: {item.date}")
        print(f"   链接: {item.url}")
        print(f"   来源: {item.raw_data.get('account', '未知')}")

        if item.summary:
            print(f"   摘要: {item.summary[:100]}...")


if __name__ == "__main__":
    main()
