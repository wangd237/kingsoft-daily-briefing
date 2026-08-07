# -*- coding: utf-8 -*-
"""
东方财富网采集器
使用 Playwright 模拟浏览器获取搜索结果
支持多关键词搜索、详情页正文抓取、AI 摘要
参考第一财经采集器模式
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from datetime import datetime, timedelta
from pathlib import Path
import time
import os
import re

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from collectors.base import BaseCrawler
from models.news import NewsItem
from config.settings import CATEGORIES, COLLECTORS


class EastMoneyCrawler(BaseCrawler):
    """东方财富网采集器

    使用 so.eastmoney.com 的三个专业频道：
    - news: 资讯 (finance.eastmoney.com)
    - report: 研报 (data.eastmoney.com)
    - article: 文章/财富号 (caifuhao.eastmoney.com)
    """

    source_name = "东方财富网"
    source_code = "eastmoney"
    credibility_base = "【媒体报道】"

    # 三个搜索路由
    ROUTES = {
        'news': {
            'name': '资讯',
            'search_url': 'https://so.eastmoney.com/news/s?keyword={keyword}',
        },
        'report': {
            'name': '研报',
            'search_url': 'https://so.eastmoney.com/report/s?keyword={keyword}',
        },
        'article': {
            'name': '文章',
            'search_url': 'https://so.eastmoney.com/article/s?keyword={keyword}',
        },
    }

    def __init__(self, enable_summary: bool = None, hours_window: int = 24):
        # 从配置读取参数
        config = COLLECTORS.get('eastmoney', {})
        self.keywords = config.get('keywords', ['金山办公'])
        self.max_items_per_keyword = 50  # 提高上限，过滤后保留足够数据

        # 时间窗口（默认24小时）
        self.hours_window = hours_window
        self.cutoff_time = datetime.now() - timedelta(hours=hours_window)
        self.logger_info = f"时间窗口: 过去{hours_window}小时 ({self.cutoff_time.strftime('%Y-%m-%d %H:%M')} 至今)"

        # 从配置读取 enable_summary
        if enable_summary is None:
            enable_summary = config.get('enable_summary', True)

        super().__init__(enable_summary=enable_summary)

        self.base_url = "https://search.eastmoney.com"

        # 已抓取的URL集合
        self.seen_urls: set[str] = set()

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

        # 匹配 MM-DD HH:MM（如 "07-29 09:38"，假设是今年）
        match = re.match(r'(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})', time_str)
        if match:
            month, day = int(match.group(1)), int(match.group(2))
            current_year = datetime.now().year
            return f"{current_year}-{month:02d}-{day:02d}"

        return datetime.now().strftime('%Y-%m-%d')

    def _parse_eastmoney_time(self, time_str: str) -> datetime | None:
        """
        解析东方财富网的时间格式
        支持: "刚刚", "X分钟前", "X小时前", "昨天", "昨天 15:30",
              "2025-08-04 17:19:24", "2025-08-04", "08-04 15:30", "今天 15:30"

        Returns:
            解析成功的 datetime，失败返回 None
        """
        if not time_str:
            return None

        time_str = time_str.strip()
        now = datetime.now()

        # 处理 "刚刚"
        if time_str == "刚刚":
            return now

        # 处理 "X分钟前"
        match = re.search(r'(\d+)分钟前', time_str)
        if match:
            minutes = int(match.group(1))
            return now - timedelta(minutes=minutes)

        # 处理 "X小时前"
        match = re.search(r'(\d+)小时前', time_str)
        if match:
            hours = int(match.group(1))
            return now - timedelta(hours=hours)

        # 处理 "今天 HH:MM"
        match = re.search(r'今天\s*(\d{1,2}):(\d{2})', time_str)
        if match:
            hour, minute = int(match.group(1)), int(match.group(2))
            return now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # 处理 "昨天" 或 "昨天 15:30"
        if '昨天' in time_str:
            time_part = re.search(r'(\d{1,2}):(\d{2})', time_str)
            if time_part:
                hour, minute = int(time_part.group(1)), int(time_part.group(2))
                yesterday = now - timedelta(days=1)
                return yesterday.replace(hour=hour, minute=minute, second=0, microsecond=0)
            else:
                return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

        # 优先处理完整格式 "YYYY-MM-DD HH:MM:SS" 或 "YYYY-MM-DD HH:MM"
        match = re.search(r'(\d{4})[-/](\d{2})[-/](\d{2})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?', time_str)
        if match:
            year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            hour, minute = int(match.group(4)), int(match.group(5))
            return datetime(year, month, day, hour, minute)

        # 处理 "YYYY-MM-DD" 或 "YYYY/MM/DD"（纯日期，假设时间为 00:00）
        match = re.search(r'(\d{4})[-/](\d{2})[-/](\d{2})', time_str)
        if match:
            year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            return datetime(year, month, day)

        # 处理 "MM-DD HH:MM"（假设是今年）
        match = re.match(r'(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})', time_str)
        if match:
            month, day, hour, minute = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
            return datetime(now.year, month, day, hour, minute)

        # 解析失败，返回 None（不保留数据）
        return None

    def _is_in_time_window(self, time_str: str) -> bool:
        """判断时间是否在过去指定小时内"""
        try:
            parsed_time = self._parse_eastmoney_time(time_str)
            if parsed_time is None:
                return False  # 解析失败视为不符合条件
            return parsed_time >= self.cutoff_time
        except:
            return False  # 异常视为不符合条件

    def _matches_keywords(self, title: str) -> bool:
        """检查标题是否包含任一关键词"""
        if not title:
            return False
        title_lower = title.lower()
        return any(kw.lower() in title_lower for kw in self.keywords)

    # ... 前面的代码 ...

    def _search_single_route(self, page, keyword: str, route_type: str) -> list[dict]:
        """搜索单个路由（资讯/研报/文章）"""
        results = []
        route = self.ROUTES.get(route_type)
        if not route:
            return results

        search_url = route['search_url'].format(keyword=keyword)
        self.logger.info(f"[{route['name']}] 搜索: {keyword}")

        try:
            page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2000)

            # 截图调试 - 保存到日志目录
            try:
                import os
                os.makedirs("output/logs", exist_ok=True)
                page.screenshot(path=f"output/logs/eastmoney_{route_type}_{keyword}.png")
                self.logger.info(f"[{route['name']}] 已保存截图: output/logs/eastmoney_{route_type}_{keyword}.png")
            except Exception as e:
                self.logger.debug(f"截图失败: {e}")

            # 等待结果加载 - 使用更通用的选择器
            try:
                page.wait_for_selector('.news-list, .result-list, [class*="list"]', timeout=10000)
            except:
                pass  # 继续尝试提取

            # 提取列表项 - 针对不同路由使用精确选择器
            list_items = []
            route_selectors = {
                'news': ['div.news_item'],           # 资讯
                'report': ['div.notice_item'],       # 研报
                'article': ['div.cfh_item'],         # 文章
            }

            selectors = route_selectors.get(route_type, ['div.news_item', 'div.notice_item', 'div.cfh_item'])

            for selector in selectors:
                try:
                    items = page.locator(selector).all()
                    if items and len(items) > 0:
                        self.logger.info(f"[{route['name']}] 使用选择器 '{selector}' 找到 {len(items)} 个列表项")
                        list_items = items
                        break
                except Exception:
                    continue

            if not list_items:
                # 最后的尝试：找包含时间格式的 div
                try:
                    all_divs = page.locator('div').all()
                    for div in all_divs:
                        text = div.text_content(timeout=100) or ''
                        if re.search(r'\d{4}-\d{2}-\d{2}', text) and 'http' in text:
                            list_items.append(div)
                    self.logger.info(f"[{route['name']}] 通过时间格式匹配找到 {len(list_items)} 个可能项")
                except:
                    pass

            self.logger.info(f"[{route['name']}] 找到 {len(list_items)} 条原始结果")

            # 根据路由类型定义提取选择器
            extract_config = {
                'news': {
                    'title': ['.news_item_t a'],
                    'time': '.news_item_time',
                    'summary': ['.news_item_c span'],
                },
                'report': {
                    'title': ['.notice_item_t a'],
                    'time': '.notice_item_time',
                    'summary': ['.notice_item_c span'],
                },
                'article': {
                    'title': ['.cfh_item_t a'],
                    'time': '.cfh_item_time',
                    'summary': ['.cfh_item_cc span'],
                },
            }
            config = extract_config.get(route_type, extract_config['news'])

            for item in list_items[:self.max_items_per_keyword]:
                try:
                    # 提取标题
                    title = ''
                    for title_sel in config['title']:
                        try:
                            title = item.locator(title_sel).first.text_content(timeout=100) or ''
                            if title.strip():
                                break
                        except Exception:
                            continue

                    # 提取链接
                    url = ''
                    try:
                        url = item.locator('a').first.get_attribute('href', timeout=100) or ''
                    except Exception:
                        pass

                    # 补全 URL
                    if url and not url.startswith('http'):
                        if url.startswith('//'):
                            url = f"https:{url}"
                        elif url.startswith('/'):
                            url = f"https://so.eastmoney.com{url}"
                        else:
                            url = f"https://so.eastmoney.com/{url}"

                    # 提取时间
                    pub_time = ''
                    try:
                        pub_time = item.locator(config['time']).first.text_content(timeout=100) or ''
                    except Exception:
                        pass

                    # 提取摘要
                    summary = ''
                    for sum_sel in config['summary']:
                        try:
                            summary = item.locator(sum_sel).first.text_content(timeout=100) or ''
                            if summary.strip() and summary != title:
                                break
                        except Exception:
                            continue

                    if title.strip() and url:
                        results.append({
                            'title': title.strip(),
                            'url': url,
                            'time': pub_time.strip(),
                            'summary': summary.strip(),
                            'route_type': route_type,
                            'route_name': route['name'],
                        })
                except Exception as e:
                    self.logger.debug(f"解析列表项失败: {e}")
                    continue

            self.logger.info(f"[{route['name']}] 提取 {len(results)} 条")

        except Exception as e:
            self.logger.error(f"[{route['name']}] 搜索失败: {e}")

        return results

    def _fetch_content_by_url(self, page, url: str) -> str:
        """根据 URL 类型选择对应的内容提取策略"""
        if 'data.eastmoney.com/report' in url:
            return self._fetch_report_content(page, url)
        elif 'caifuhao.eastmoney.com' in url:
            return self._fetch_caifuhao_content(page, url)
        else:
            return self._fetch_news_content(page, url)

    def _fetch_news_content(self, page, url: str) -> str:
        """获取普通资讯详情页 - finance.eastmoney.com"""
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(1500)

            content = page.evaluate("""() => {
                const selectors = [
                    '.article-content',
                    '.content-detail',
                    '.news-content',
                    '.main-content',
                    'article',
                    '#ContentBody',
                    '.txt-content',
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el) {
                        const text = el.innerText?.trim();
                        if (text && text.length > 100) return text;
                    }
                }
                // 备用：提取段落
                const pars = document.querySelectorAll('p');
                const texts = [];
                pars.forEach(p => {
                    const t = p.innerText?.trim();
                    if (t && t.length > 20) texts.push(t);
                });
                return texts.join('\\n');
            }""")
            return content or ""
        except Exception as e:
            self.logger.error(f"获取资讯页失败: {url} - {e}")
            return ""

    def _fetch_report_content(self, page, url: str) -> str:
        """获取研报详情页 - data.eastmoney.com/report"""
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(1500)

            content = page.evaluate("""() => {
                const selectors = [
                    '.report-content',
                    '.content-detail',
                    '#ContentBody',
                    '.research-report',
                    '.article-content',
                    '.main-content',
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el) {
                        const text = el.innerText?.trim();
                        if (text && text.length > 100) return text;
                    }
                }
                return document.body.innerText?.trim() || '';
            }""")
            return content or ""
        except Exception as e:
            self.logger.error(f"获取研报页失败: {url} - {e}")
            return ""

    def _fetch_caifuhao_content(self, page, url: str) -> str:
        """获取财富号文章详情页 - caifuhao.eastmoney.com"""
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(1500)

            content = page.evaluate("""() => {
                const selectors = [
                    '.article-content',
                    '.post-content',
                    '.content-detail',
                    '#article-content',
                    'article',
                    '.main-content',
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el) {
                        const text = el.innerText?.trim();
                        if (text && text.length > 100) return text;
                    }
                }
                // 备用
                const pars = document.querySelectorAll('p');
                const texts = [];
                pars.forEach(p => {
                    const t = p.innerText?.trim();
                    if (t && t.length > 20) texts.push(t);
                });
                return texts.join('\\n');
            }""")
            return content or ""
        except Exception as e:
            self.logger.error(f"获取财富号页失败: {url} - {e}")
            return ""

    def fetch(self) -> list[NewsItem]:
        """采集数据 - 遍历三个路由（资讯/研报/文章）"""
        all_items = []

        self.logger.info(f"开始采集东方财富网 - 关键词: {self.keywords}")
        self.logger.info(f"遍历路由: {[r['name'] for r in self.ROUTES.values()]}")
        self.logger.info(self.logger_info)

        # 创建批次目录
        now = datetime.now()
        date_dir = now.strftime('%Y/%m/%d')
        batch_name = now.strftime(f'{self.source_code}_%Y%m%d_%H%M%S')
        self._batch_dir = f"{self.output_dir}/{self.source_code}/{date_dir}/{batch_name}"
        os.makedirs(self._batch_dir, exist_ok=True)

        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            page = context.new_page()

            try:
                for keyword in self.keywords:
                    for route_type in ['news', 'report', 'article']:
                        route_name = self.ROUTES[route_type]['name']
                        self.logger.info(f"[{route_name}] 搜索关键词: {keyword}")

                        results = self._search_single_route(page, keyword, route_type)

                        # 过滤：关键词匹配 + 时间窗口
                        filtered = []
                        for item in results:
                            if not self._matches_keywords(item['title']):
                                self.logger.debug(f"[{route_name}] 关键词过滤: {item['title'][:50]}...")
                                continue
                            if not self._is_in_time_window(item['time']):
                                self.logger.debug(f"[{route_name}] 时间过滤: {item['title'][:50]}... ({item['time']})")
                                continue
                            filtered.append(item)

                        self.logger.info(f"[{route_name}] 过滤后: {len(filtered)}/{len(results)} 条")

                        for idx, news in enumerate(filtered):
                            url = news['url']

                            # 去重
                            if url in self.seen_urls:
                                continue
                            self.seen_urls.add(url)

                            self.logger.info(f"[{route_name}] [{idx+1}/{len(filtered)}] 获取正文: {news['title'][:50]}...")
                            content = self._fetch_content_by_url(page, url)

                            # 创建 NewsItem
                            item = NewsItem(
                                title=news['title'],
                                date=self._parse_time(news['time']),
                                url=url,
                                source=self.source_name,
                                source_code=self.source_code,
                                credibility_tag=f"{self.credibility_base}【{route_name}】",
                                category=self._auto_classify(news['title']),
                                summary=news['summary'] or news['title'][:150],
                                content=content,
                                raw_data={
                                    'keyword': keyword,
                                    'route_type': route_type,
                                    'route_name': route_name,
                                },
                            )

                            # AI 摘要
                            if content and len(content.strip()) > 50:
                                ai_summary, summary_time = self.generate_summary(news['title'], content)
                                if ai_summary:
                                    item.summary = ai_summary
                                    item.summary_generated_at = summary_time

                            all_items.append(item)
                            time.sleep(1)

                        time.sleep(2)

            finally:
                context.close()
                browser.close()

        self.logger.info(f"采集完成: 共 {len(all_items)} 条")
        return all_items


def main():
    """测试运行"""
    import os

    # 支持环境变量配置时间窗口（默认24小时）
    hours_window = int(os.getenv('EASTMONEY_HOURS_WINDOW', '56'))

    crawler = EastMoneyCrawler(hours_window=hours_window)
    items = crawler.run()

    print(f"\n{'='*70}")
    print(f"东方财富网采集结果: {len(items)} 条")
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
