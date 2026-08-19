# -*- coding: utf-8 -*-
"""
微博采集器
使用 crawl4weibo 库采集多个账号的微博动态。
转发微博的摘要会合并“转发文案”和“被转发原微博内容”。
支持通过账号配置的 intro 为 AI 提供背景上下文；
支持 daily_preface 供日报生成模块按账号聚合后展示；
支持跳过转发自已采集账号的重复内容。
"""
import io
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
sys.path.append(str(Path(__file__).parent.parent.parent))

from collectors.base import BaseCrawler
from models.news import NewsItem
from config.settings import CATEGORIES, COLLECTORS

try:
    from crawl4weibo import WeiboClient
    CRAWL4WEIBO_AVAILABLE = True
except ImportError:
    CRAWL4WEIBO_AVAILABLE = False


class WeiboCrawler(BaseCrawler):
    """微博采集器。"""

    source_name = "微博"
    source_code = "weibo"
    credibility_base = "【官方资讯】"

    def __init__(self, hours_window: int = None, enable_summary: bool = None):
        """初始化微博客户端及采集参数。"""
        if not CRAWL4WEIBO_AVAILABLE:
            raise ImportError(
                "crawl4weibo 未安装，请运行: pip install crawl4weibo && playwright install chromium"
            )

        config = COLLECTORS.get("weibo", {})
        self.accounts = config.get("accounts", [])
        self.tracked_uids = {str(account.get("uid")) for account in self.accounts}

        self.max_pages_per_account = 3
        self.hours_window = hours_window or config.get("hours_window", 24)
        self.cutoff_time = datetime.now() - timedelta(hours=self.hours_window)
        self.logger_info = (
            f"时间窗口: 过去{self.hours_window}小时 "
            f"({self.cutoff_time.strftime('%Y-%m-%d %H:%M')} 至今)"
        )

        if enable_summary is None:
            enable_summary = config.get("enable_summary", True)

        super().__init__(enable_summary=enable_summary)
        self.enable_summary = enable_summary

        if not self.accounts:
            self.logger.warning("未配置微博采集账号列表")

        self.logger.info("初始化微博客户端...")
        self.client = WeiboClient(login_cookies=False)
        self.seen_ids: set[str] = set()

    def _auto_classify(self, title: str) -> str:
        """依据关键词自动分类。"""
        title_lower = title.lower()
        scores = {}
        for category, rules in CATEGORIES.items():
            score = sum(1 for kw in rules["keywords"] if kw in title_lower)
            scores[category] = score
        if scores and max(scores.values()) > 0:
            return max(scores, key=scores.get)
        return "产品动态"

    def _parse_weibo_time(self, timestamp) -> datetime:
        """解析微博时间，统一返回无时区 datetime。"""
        if not timestamp:
            self.logger.warning("时间解析失败(空值)，已回退到当前时间")
            return datetime.now()

        if isinstance(timestamp, datetime):
            if timestamp.tzinfo is not None:
                return timestamp.replace(tzinfo=None)
            return timestamp

        try:
            return datetime.fromtimestamp(int(timestamp))
        except (ValueError, TypeError, OSError) as exc:
            self.logger.warning(f"时间解析失败('{timestamp}'): {exc}，已回退到当前时间")
            return datetime.now()

    def _is_in_time_window(self, timestamp) -> bool:
        """判断发布时间是否仍在指定时间窗口中。"""
        return self._parse_weibo_time(timestamp) >= self.cutoff_time

    def _format_weibo_text(self, post) -> str:
        """提取并格式化一条微博的正文、长文与话题。"""
        text_parts = []

        if hasattr(post, "text") and post.text:
            text_parts.append(post.text)

        if hasattr(post, "long_text") and post.long_text:
            text_parts.append(f"\n\n[长文本] {post.long_text}")

        if hasattr(post, "topics") and post.topics:
            topics_str = " ".join(f"#{topic}#" for topic in post.topics)
            text_parts.append(f"\n\n话题: {topics_str}")

        return "\n".join(text_parts).strip()

    @staticmethod
    def _read_value(obj, *keys):
        """兼容从对象或字典读取多个候选字段。"""
        for key in keys:
            value = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
            if value not in (None, ""):
                return value
        return None

    def _get_retweeted_uid(self, post) -> str | None:
        """提取被转发原微博作者 UID。"""
        retweet = getattr(post, "retweeted_status", None)
        if not retweet:
            return None

        original_user = self._read_value(retweet, "user", "user_info", "author")
        original_uid = None
        if original_user:
            original_uid = self._read_value(original_user, "id", "idstr", "uid", "user_id")

        if original_uid is None:
            original_uid = self._read_value(retweet, "uid", "user_id", "author_id")

        return str(original_uid) if original_uid not in (None, "") else None

    def _build_full_context(self, post, result: dict) -> tuple[str, dict]:
        """构建转发微博完整上下文，同时返回结构化转发信息。"""
        retweet_info = {
            "is_retweet": False,
            "retweeted_from": None,
            "retweeted_uid": None,
            "retweeted_text": None,
        }

        retweet = getattr(post, "retweeted_status", None)
        if not retweet:
            return result["text"], retweet_info

        retweet_info["is_retweet"] = True
        retweet_info["retweeted_uid"] = self._get_retweeted_uid(post)

        original_author = None
        original_user = self._read_value(retweet, "user", "user_info", "author")
        if original_user:
            original_author = self._read_value(
                original_user,
                "screen_name", "name", "nickname", "user_name", "username",
            )

        if not original_author:
            original_author = self._read_value(
                retweet,
                "screen_name", "user_name", "author_name", "nickname", "name",
            )

        retweet_info["retweeted_from"] = str(original_author or "未知用户")

        original_text = self._format_weibo_text(retweet)
        retweet_info["retweeted_text"] = original_text

        full_context_parts = [
            f"【转发人文案】{result['text']}" if result["text"] else "【转发人文案】（无附言）",
            "",
            "────────────────────────────",
            f"【被转发微博 @{retweet_info['retweeted_from']}】",
        ]

        if original_text:
            full_context_parts.append(original_text)

        original_time = getattr(retweet, "created_at", None)
        if original_time:
            original_time_str = original_time.isoformat() if isinstance(original_time, datetime) else str(original_time)
            full_context_parts.append(f"\n（原微博发布时间: {original_time_str}）")

        return "\n".join(full_context_parts).strip(), retweet_info

    def _build_merged_summary(self, post: dict, retweet_info: dict) -> str:
        """生成默认摘要；转发微博合并转发文案与原微博正文。"""
        repost_text = (post.get("text") or "").strip()

        if not retweet_info.get("is_retweet"):
            return repost_text

        original_author = retweet_info.get("retweeted_from") or "未知用户"
        original_text = (retweet_info.get("retweeted_text") or "").strip()

        summary_parts = []
        if repost_text:
            summary_parts.append(f"转发文案：{repost_text}")
        if original_text:
            summary_parts.append(f"原微博（@{original_author}）：{original_text}")

        return "\n\n".join(summary_parts) or repost_text

    @staticmethod
    def _build_ai_content(content: str, account_intro: str | None) -> str:
        """将账号背景与正文分开传给 AI；背景不写入最终摘要。"""
        intro = (account_intro or "").strip()
        body = (content or "").strip()
        if not intro:
            return body
        return f"【背景知识】\n{intro}\n\n【本条内容】\n{body}"

    @staticmethod
    def _clean_title_text(text: str) -> str:
        """清理微博标题中的话题、链接和多余空白。"""
        import re

        title = (text or "").replace("\n", " ").strip()
        title = re.sub(r"https?://\S+", "", title)
        title = re.sub(r"#[^#\s]+#", "", title)
        title = re.sub(r"\s+", " ", title).strip(" -—|，,。！!：:")
        return title

    def _build_weibo_title(self, post: dict, retweet_info: dict) -> str:
        """生成适合日报阅读的规则标题，供 AI 标题生成失败时兜底。"""
        account_name = (post.get("account_name") or "微博").strip()
        text = self._clean_title_text(post.get("text", ""))

        if retweet_info.get("is_retweet"):
            original_author = retweet_info.get("retweeted_from") or ""
            original_text = self._clean_title_text(retweet_info.get("retweeted_text", ""))
            text = original_text or text
            prefix = f"{account_name}转发"
            if original_author:
                prefix += f" @{original_author}"
        else:
            prefix = account_name

        if not text:
            return f"{prefix}微博动态"

        max_length = 38
        if len(text) > max_length:
            text = text[:max_length].rstrip("，,。！!：:；; ") + "…"
        return f"{prefix}｜{text}"

    def _generate_ai_weibo_title(self, post: dict, summary: str, fallback_title: str) -> str:
        """基于最终摘要生成简短日报标题；失败返回空串，由调用处沿用规则标题。"""
        import re

        text = (summary or "").strip()
        if not text:
            return ""

        if not self.enable_summary or not self.summarizer.client:
            return ""
       
        prompt = f"""请根据以下微博摘要生成一个中文日报标题。
        
    要求：
    1. 只输出标题正文，不要解释，不要空行。
    2. 标题控制在 12—40 个汉字以内。
    3. 不要出现微博账号名或 @用户名；人物名仅在无法准确概括事件时保留。
    4. 不要添加“标题：”“AI微博标题：”等前缀。
    5. 保留《剑网3》等作品名书名号。
    6. 必须直接给出一个标题。

    微博摘要：
    {summary}
    """

        try:
            response = self.summarizer.client.chat.completions.create(
                model=self.summarizer.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是游戏行业日报编辑。"
                            "禁止输出思考过程、分析过程、字数计数或英文内容。"
                            "只输出一个简短客观的中文标题，且只有一行。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=1200,
                timeout=30,
            )
            choice = response.choices[0]
            message = choice.message
            raw = (getattr(message, "content", None) or "").strip()
            
            if not raw:
                reasoning = (getattr(message, "reasoning_content", None) or "").strip()
                self.logger.warning(
                    f"AI 标题无正文：finish_reason={getattr(choice, 'finish_reason', None)!r}，"
                    f"模型={self.summarizer.model}，"
                    f"推理内容长度={len(reasoning)}"
                )
                return ""

            title = str(raw or "").strip().replace("\n", " ")
            title = re.sub(
            r"^(AI\s*微博标题|微博标题|日报标题|标题|Title)\s*[:：]\s*",
            "",
            title,
            flags=re.I,
        )
            title = re.sub(r"^\[[^\]]{1,20}\]\s*", "", title)
            title = re.sub(r"^@\S+\s*[｜|:：\-]*\s*", "", title)
            title = title.strip("\"'“”‘’").strip()
            title = re.sub(r"\s+", " ", title)
            title = title.strip(" 　-—|·，,。！!：:；;")
            
            if len(title) < 4 or len(title) > 40:
                self.logger.warning(f"AI 标题长度不合格，沿用规则标题: {title!r}")
                return ""
           
            self.logger.info(f"AI 微博最终标题: {title!r}，长度: {len(title)}")
            return title

        except Exception as exc:
            self.logger.warning(f"AI 标题生成失败，使用规则标题：{exc}")
            return ""
        
    def _fetch_account_posts(self, account: dict) -> list[dict]:
        """采集单一账号的微博。"""
        results = []
        uid = account["uid"]
        name = account["name"]

        self.logger.info(f"[{name}] 开始采集...")

        try:
            user = self.client.get_user_by_uid(uid)
            self.logger.info(f"[{name}] 用户验证成功: {user.screen_name}")

            for page in range(1, self.max_pages_per_account + 1):
                self.logger.info(f"[{name}] 获取第 {page} 页...")
                posts = self.client.get_user_posts(uid, page=page, expand=True)

                if not posts:
                    self.logger.info(f"[{name}] 第 {page} 页无数据，停止采集")
                    break

                self.logger.info(f"[{name}] 第 {page} 页获取到 {len(posts)} 条")
                time_filtered = 0

                for post in posts:
                    post_id = str(post.id)
                    if post_id in self.seen_ids:
                        continue
                    self.seen_ids.add(post_id)

                    if not self._is_in_time_window(post.created_at):
                        time_filtered += 1
                        continue

                    retweeted_uid = self._get_retweeted_uid(post)
                    if (
                        account.get("skip_retweets_from_tracked_accounts")
                        and retweeted_uid in self.tracked_uids
                    ):
                        self.logger.info(
                            f"[{name}] 跳过重复转发：原作者 UID {retweeted_uid} 已在采集账号列表中"
                        )
                        continue

                    result = {
                        "id": post.id,
                        "bid": post.bid,
                        "uid": uid,
                        "account_name": name,
                        "account_type": account.get("type", "未知"),
                        "account_intro": account.get("intro", ""),
                        "daily_preface": account.get("daily_preface", ""),
                        "text": self._format_weibo_text(post),
                        "created_at": post.created_at,
                        "attitudes_count": post.attitudes_count,
                        "comments_count": post.comments_count,
                        "reposts_count": post.reposts_count,
                        "pic_urls": post.pic_urls if hasattr(post, "pic_urls") else [],
                        "video_url": post.video_url if hasattr(post, "video_url") else None,
                        "source": post.source if hasattr(post, "source") else "",
                        "is_top": post.is_top if hasattr(post, "is_top") else False,
                    }

                    full_context, retweet_info = self._build_full_context(post, result)
                    result["full_context"] = full_context
                    result["retweet_info"] = retweet_info
                    results.append(result)

                if time_filtered > len(posts) * 0.5:
                    self.logger.info(f"[{name}] 大量数据超出时间窗口，停止翻页")
                    break

                time.sleep(1)

        except Exception as exc:
            self.logger.error(f"[{name}] 采集失败: {exc}", exc_info=True)

        self.logger.info(f"[{name}] 有效数据: {len(results)} 条")
        return results

    def fetch(self) -> list[NewsItem]:
        """采集配置中的全部微博账号。"""
        all_items = []

        self.logger.info(f"开始采集微博 - 账号数: {len(self.accounts)}")
        self.logger.info(self.logger_info)

        now = datetime.now()
        date_dir = now.strftime("%Y/%m/%d")
        batch_name = now.strftime(f"{self.source_code}_%Y%m%d_%H%M%S")
        self._batch_dir = f"{self.output_dir}/{self.source_code}/{date_dir}/{batch_name}"
        os.makedirs(self._batch_dir, exist_ok=True)

        for account in self.accounts:
            posts = self._fetch_account_posts(account)

            for post in posts:
                full_context = post.get("full_context", post["text"])
                retweet_info = post.get("retweet_info", {})
                fallback_title = self._build_weibo_title(post, retweet_info)
                url = f"https://weibo.com/{post['uid']}/{post['bid']}"

                summary = self._build_merged_summary(post, retweet_info)

                post_time = self._parse_weibo_time(post["created_at"])
                created_at_raw = post["created_at"]
                if isinstance(created_at_raw, datetime):
                    created_at_str = created_at_raw.isoformat()
                else:
                    created_at_str = str(created_at_raw) if created_at_raw else None

                raw_data = {
                    "account_name": post["account_name"],
                    "account_type": post["account_type"],
                    "account_intro": post.get("account_intro") or None,
                    "daily_preface": post.get("daily_preface") or None,
                    "post_id": post["id"],
                    "created_at": created_at_str,
                    "is_retweet": retweet_info.get("is_retweet", False),
                }

                if retweet_info.get("is_retweet"):
                    raw_data["retweeted_from"] = retweet_info.get("retweeted_from")
                    raw_data["retweeted_uid"] = retweet_info.get("retweeted_uid")
                    raw_data["retweeted_text_preview"] = (
                        retweet_info.get("retweeted_text", "")[:100] + "..."
                        if retweet_info.get("retweeted_text")
                        else None
                    )

                item = NewsItem(
                    title=fallback_title,
                    date=post_time.strftime("%Y-%m-%d"),
                    url=url,
                    source=self.source_name,
                    source_code=self.source_code,
                    credibility_tag="【官方资讯】",
                    category=self._auto_classify(post["text"]),
                    summary=summary,
                    content=full_context,
                    raw_data=raw_data,
                )

                # 第一次调用：沿用共用逻辑生成摘要，不修改 ai_summarizer.py。
                ai_content = self._build_ai_content(
                    full_context,
                    post.get("account_intro"),
                )
                if self.enable_summary and len(full_context) > 50:
                    ai_summary, summary_time = self.generate_summary(fallback_title, ai_content)
                    if ai_summary:
                        item.summary = ai_summary
                        item.summary_generated_at = summary_time

                    # 第二次调用：仅微博采集器根据“最终摘要”生成日报标题。
                    ai_title = ""
                    
                    for attempt in range(2):
                        ai_title = self._generate_ai_weibo_title(
                            post=post,
                            summary=item.summary,
                            fallback_title=fallback_title,
                        )
                        
                        if ai_title:
                            item.title = ai_title
                            self.logger.info(f"第 {attempt + 1} 次 AI 标题生成成功，跳过后续重试")
                            break

                        if attempt == 0:
                            self.logger.warning("第 1 次 AI 标题生成失败，使用摘要重试")
                            time.sleep(1)
                        else:
                            self.logger.warning("第 2 次 AI 标题生成失败，保留规则标题")
                        
                    if not ai_title:
                        self.logger.info(f"AI 标题未生成，保留规则标题：{fallback_title}")

                all_items.append(item)

            time.sleep(2)

        self.logger.info(f"采集完成: 共 {len(all_items)} 条")
        return all_items


def main():
    """运行微博采集器。"""
    hours_window = int(os.getenv("WEIBO_HOURS_WINDOW", "72"))

    crawler = WeiboCrawler(hours_window=hours_window)
    items = crawler.run()

    crawler.logger.info("=" * 70)
    crawler.logger.info(f"微博采集结果: {len(items)} 条")
    crawler.logger.info(f"时间窗口: 过去{hours_window}小时")
    crawler.logger.info(f"截止时间: {crawler.cutoff_time.strftime('%Y-%m-%d %H:%M')}")
    crawler.logger.info("=" * 70)

    for index, item in enumerate(items, 1):
        crawler.logger.info("─" * 70)
        crawler.logger.info(f"{index}. [{item.category}] {item.title}")
        crawler.logger.info(f"   来源: {item.credibility_tag}")
        crawler.logger.info(f"   日期: {item.date}")
        crawler.logger.info(f"   链接: {item.url}")
        if item.summary:
            crawler.logger.info(f"   摘要: {item.summary[:200]}")


if __name__ == "__main__":
    main()
