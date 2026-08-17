# -*- coding: utf-8 -*-
"""
微博采集器
使用 crawl4weibo 库采集多个账号的微博动态
支持24小时时间窗口过滤
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from datetime import datetime, timedelta
from pathlib import Path
import time
import os

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))

from collectors.base import BaseCrawler
from models.news import NewsItem
from config.settings import CATEGORIES, COLLECTORS

# 导入 crawl4weibo
try:
    from crawl4weibo import WeiboClient
    CRAWL4WEIBO_AVAILABLE = True
except ImportError:
    CRAWL4WEIBO_AVAILABLE = False


class WeiboCrawler(BaseCrawler):
    """
    微博采集器
    采集多个官方微博和个人账号的动态
    使用 crawl4weibo 库实现
    """

    source_name = "微博"
    source_code = "weibo"
    credibility_base = "【官方资讯】"

    def __init__(self, hours_window: int = None, enable_summary: bool = None):
        """
        初始化

        Args:
            hours_window: 时间窗口（小时），默认24小时
            enable_summary: 是否启用 AI 摘要
        """
        # 检查依赖
        if not CRAWL4WEIBO_AVAILABLE:
            raise ImportError(
                "crawl4weibo 未安装，请运行: pip install crawl4weibo && playwright install chromium"
            )

        # 从配置读取参数
        config = COLLECTORS.get('weibo', {})
        self.accounts = config.get('accounts', [])

        if not self.accounts:
            self.logger.warning("未配置微博采集账号列表")

        # 每个账号最多采集页数
        self.max_pages_per_account = 3

        # 时间窗口（默认24小时）
        self.hours_window = hours_window or config.get('hours_window', 24)
        self.cutoff_time = datetime.now() - timedelta(hours=self.hours_window)
        self.logger_info = f"时间窗口: 过去{self.hours_window}小时 ({self.cutoff_time.strftime('%Y-%m-%d %H:%M')} 至今)"

        # 从配置读取 enable_summary
        if enable_summary is None:
            enable_summary = config.get('enable_summary', True)

        super().__init__(enable_summary=enable_summary)
        self.enable_summary = enable_summary  # 保存实例属性供后续使用

        # 初始化 crawl4weibo 客户端
        self.logger.info("初始化微博客户端...")
        self.client = WeiboClient(
            login_cookies=False,  # 先尝试无需登录模式
        )

        # 已抓取的微博ID集合（去重用）
        self.seen_ids: set[str] = set()

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

    def _parse_weibo_time(self, timestamp) -> datetime:
        """
        解析微博时间，返回 offset-naive datetime（无时区）
        便于与 cutoff_time 比较
        """
        if not timestamp:
            self.logger.warning(f"⚠️ 时间解析失败(空值)，已回退到今日时间")
            return datetime.now()

        # 如果已经是 datetime 对象
        if isinstance(timestamp, datetime):
            # 如果是 offset-aware，转换为 offset-naive
            if timestamp.tzinfo is not None:
                return timestamp.replace(tzinfo=None)
            return timestamp

        # 如果是时间戳（整数/浮点数）
        try:
            return datetime.fromtimestamp(int(timestamp))
        except (ValueError, TypeError, OSError) as e:
            self.logger.warning(f"⚠️ 时间解析失败('{timestamp}'): {e}，已回退到今日时间")
            return datetime.now()

    def _is_in_time_window(self, timestamp: int) -> bool:
        """判断微博时间是否在时间窗口内"""
        post_time = self._parse_weibo_time(timestamp)
        return post_time >= self.cutoff_time

    def _format_weibo_text(self, post) -> str:
        """
        格式化微博文本
        """
        text_parts = []

        # 主内容
        if hasattr(post, 'text') and post.text:
            text_parts.append(post.text)

        # 长文本展开
        if hasattr(post, 'long_text') and post.long_text:
            text_parts.append(f"\n\n[长文本] {post.long_text}")

        # 话题
        if hasattr(post, 'topics') and post.topics:
            topics_str = ' '.join([f"#{t}#" for t in post.topics])
            text_parts.append(f"\n\n话题: {topics_str}")

        return '\n'.join(text_parts)

    def _build_full_context(self, post, result: dict) -> tuple[str, dict]:
        """
        构建包含转发上下文的完整文本

        Args:
            post: crawl4weibo 返回的 Post 对象
            result: 已构建的基础结果字典

        Returns:
            (完整上下文文本, 转发信息字典)
        """
        retweet_info = {
            'is_retweet': False,
            'retweeted_from': None,
            'retweeted_text': None,
        }

        # 检查是否有转发内容
        retweet = None
        if hasattr(post, 'retweeted_status') and post.retweeted_status:
            retweet = post.retweeted_status

        if not retweet:
            # 非转发微博，直接返回原文
            return result['text'], retweet_info

        # 是转发微博，构建完整上下文
        retweet_info['is_retweet'] = True

        # 获取原微博作者
        original_author = "未知用户"
        if hasattr(retweet, 'user') and retweet.user:
            if hasattr(retweet.user, 'screen_name'):
                original_author = retweet.user.screen_name
            elif hasattr(retweet.user, 'id'):
                original_author = f"用户{retweet.user.id}"

        retweet_info['retweeted_from'] = original_author

        # 获取原微博内容
        original_text = ""
        if hasattr(retweet, 'text') and retweet.text:
            original_text = retweet.text
            retweet_info['retweeted_text'] = original_text

        # 构建完整上下文
        full_context_parts = [
            f"【评论】{result['text']}",
            "",
            "────────────────────────────",
            f"【引用 @{original_author}】",
        ]

        if original_text:
            full_context_parts.append(original_text)

            # 如果原微博有长文本
            if hasattr(retweet, 'long_text') and retweet.long_text:
                full_context_parts.append(f"\n[长文本] {retweet.long_text}")

        # 获取原微博发布时间
        if hasattr(retweet, 'created_at') and retweet.created_at:
            original_time = retweet.created_at
            if isinstance(original_time, datetime):
                original_time_str = original_time.strftime('%Y-%m-%d %H:%M')
            else:
                original_time_str = str(original_time)
            full_context_parts.append(f"\n（原微博发布时间: {original_time_str}）")

        full_context = '\n'.join(full_context_parts)

        return full_context, retweet_info

    def _fetch_account_posts(self, account: dict) -> list[dict]:
        """
        采集单个账号的微博

        Args:
            account: {'uid': str, 'name': str, 'type': str}

        Returns:
            微博列表
        """
        results = []
        uid = account['uid']
        name = account['name']

        self.logger.info(f"[{name}] 开始采集...")

        try:
            # 获取用户信息
            user = self.client.get_user_by_uid(uid)
            self.logger.info(f"[{name}] 用户验证成功: {user.screen_name}")
            #self.logger.info(f"[{name}] 用户验证成功: {user.screen_name}, 粉丝: {user.followers_count}")

            # 分页获取微博
            for page in range(1, self.max_pages_per_account + 1):
                self.logger.info(f"[{name}] 获取第 {page} 页...")

                posts = self.client.get_user_posts(uid, page=page, expand=True)

                if not posts:
                    self.logger.info(f"[{name}] 第 {page} 页无数据，停止采集")
                    break

                self.logger.info(f"[{name}] 第 {page} 页获取到 {len(posts)} 条")

                # 时间窗口检查
                time_filtered = 0
                for post in posts:
                    # 去重检查
                    if post.id in self.seen_ids:
                        continue
                    self.seen_ids.add(post.id)

                    # 时间过滤
                    if not self._is_in_time_window(post.created_at):
                        time_filtered += 1
                        continue

                    # 构建基础结果
                    result = {
                        'id': post.id,
                        'bid': post.bid,
                        'uid': uid,
                        'account_name': name,
                        'account_type': account.get('type', '未知'),
                        'text': self._format_weibo_text(post),
                        'created_at': post.created_at,
                        'attitudes_count': post.attitudes_count,
                        'comments_count': post.comments_count,
                        'reposts_count': post.reposts_count,
                        'pic_urls': post.pic_urls if hasattr(post, 'pic_urls') else [],
                        'video_url': post.video_url if hasattr(post, 'video_url') else None,
                        'source': post.source if hasattr(post, 'source') else '',
                        'is_top': post.is_top if hasattr(post, 'is_top') else False,
                    }

                    # 构建完整上下文（包含转发内容）
                    full_context, retweet_info = self._build_full_context(post, result)
                    result['full_context'] = full_context
                    result['retweet_info'] = retweet_info

                    results.append(result)

                # 如果大量数据超时，停止翻页
                if time_filtered > len(posts) * 0.5:
                    self.logger.info(f"[{name}] 大量数据超出时间窗口，停止翻页")
                    break

                time.sleep(1)

        except Exception as e:
            self.logger.error(f"[{name}] 采集失败: {e}", exc_info=True)

        self.logger.info(f"[{name}] 有效数据: {len(results)} 条")
        return results

    def fetch(self) -> list[NewsItem]:
        """
        采集所有配置账号的微博
        """
        all_items = []

        self.logger.info(f"开始采集微博 - 账号数: {len(self.accounts)}")
        self.logger.info(self.logger_info)

        # 创建批次目录
        now = datetime.now()
        date_dir = now.strftime('%Y/%m/%d')
        batch_name = now.strftime(f'{self.source_code}_%Y%m%d_%H%M%S')
        if not self._batch_dir:
            self._batch_dir = f"{self.output_dir}/{self.source_code}/{date_dir}/{batch_name}"
        os.makedirs(self._batch_dir, exist_ok=True)

        # 采集每个账号
        for account in self.accounts:
            posts = self._fetch_account_posts(account)

            for post in posts:
                # 获取完整上下文（包含转发内容）
                full_context = post.get('full_context', post['text'])
                retweet_info = post.get('retweet_info', {})

                # 构建标题（使用原文本，保持简洁）
                title_text = post['text'].replace('\n', ' ')[:50]
                title = title_text + "..." if len(post['text']) > 50 else title_text

                # 构建URL
                url = f"https://weibo.com/{post['uid']}/{post['bid']}"

                # 构建摘要（使用原文本）
                summary_text = post['text'][:200]
                summary = summary_text + "..." if len(post['text']) > 200 else summary_text

                # 发布时间
                post_time = self._parse_weibo_time(post['created_at'])

                # 将 created_at 转换为字符串（如果是 datetime 对象）
                created_at_raw = post['created_at']
                if isinstance(created_at_raw, datetime):
                    created_at_str = created_at_raw.isoformat()
                else:
                    created_at_str = str(created_at_raw) if created_at_raw else None

                # 构建 raw_data（包含转发信息）
                raw_data = {
                    'account_name': post['account_name'],
                    'account_type': post['account_type'],
                    'post_id': post['id'],
                    'created_at': created_at_str,
                    'is_retweet': retweet_info.get('is_retweet', False),
                }

                # 如果是转发，添加原微博信息
                if retweet_info.get('is_retweet'):
                    raw_data['retweeted_from'] = retweet_info.get('retweeted_from')
                    raw_data['retweeted_text_preview'] = retweet_info.get('retweeted_text', '')[:100] + '...' if retweet_info.get('retweeted_text') else None

                item = NewsItem(
                    title=title,
                    date=post_time.strftime('%Y-%m-%d'),
                    url=url,
                    source=self.source_name,
                    source_code=self.source_code,
                    credibility_tag='【官方资讯】',  # 统一使用官方资讯标签
                    category=self._auto_classify(post['text']),
                    summary=summary,
                    content=full_context,  # 保存完整上下文
                    raw_data=raw_data,
                )

                # AI 摘要（使用完整上下文生成）
                if self.enable_summary and len(full_context) > 50:
                    ai_summary, summary_time = self.generate_summary(title, full_context)
                    if ai_summary:
                        item.summary = ai_summary
                        item.summary_generated_at = summary_time

                all_items.append(item)

            time.sleep(2)

        self.logger.info(f"采集完成: 共 {len(all_items)} 条")
        return all_items


def main():
    """测试运行"""
    import os

    hours_window = int(os.getenv('WEIBO_HOURS_WINDOW', '72'))

    crawler = WeiboCrawler(hours_window=hours_window)
    items = crawler.run()

    print(f"\n{'='*70}")
    print(f"微博采集结果: {len(items)} 条")
    print(f"时间窗口: 过去{hours_window}小时")
    print(f"截止时间: {crawler.cutoff_time.strftime('%Y-%m-%d %H:%M')}")
    print('='*70)

    for i, item in enumerate(items, 1):
        print(f"\n{'─'*70}")
        print(f"{i}. [{item.category}] {item.title}")
        print(f"   来源: {item.credibility_tag}")
        print(f"   日期: {item.date}")
        print(f"   链接: {item.url}")

        if item.summary:
            print(f"   摘要: {item.summary[:200]}...")


if __name__ == "__main__":
    main()
