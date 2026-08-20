# -*- coding: utf-8 -*-
"""
西山居游戏官网新闻采集器（剑网3 + 剑网3缘起）

"""
import sys
# 强制 UTF-8 输出（Windows 兼容）
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

import json
import time
import re
from datetime import datetime, timedelta
from typing import List
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

# sys.path hack，兼容从模块运行
sys.path.append(str(Path(__file__).parent.parent.parent))

from collectors.base import BaseCrawler
from models.news import NewsItem


class XiaoYouCrawler(BaseCrawler):
    """西山居游戏官网新闻采集器。"""

    source_name = "西山居游戏官网"
    source_code = "xiaoyou"
    credibility_base = "【官方资讯】"

    BASE_URL = "https://games.xoyo.com"

    # 列表 JSON-RPC 接口（同一入口，不同 game_id）
    YXFX_API_URL = "https://api-games.xoyo.com/api.php?op=yxfx_api"

    # game_id:
    # 1: 剑网3
    # 20: 剑网3缘起
    GAME_CHANNELS = [(1, "剑网3"), (20, "剑网3缘起")]

    def __init__(self, enable_summary: bool = True, hours_window: int = 168):
        super().__init__(enable_summary=enable_summary)

        try:
            self.hours_window = int(
                # 兼容你之前项目用的环境变量名
                __import__("os").getenv("XOYO_HOURS_WINDOW", str(hours_window))
            )
            if self.hours_window <= 0:
                raise ValueError
        except Exception:
            self.hours_window = hours_window

        self.cutoff_time = datetime.now() - timedelta(hours=self.hours_window)

    @staticmethod
    def _clean_text(text: str) -> str:
        if not text:
            return ""
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _ts_to_datetime(ts) -> datetime | None:
        try:
            # 列表里 inputtime 一般是秒时间戳（或可转为 int 的数字）
            return datetime.fromtimestamp(int(ts))
        except Exception:
            return None

    def _get_detail_content(self, page, url: str) -> str:
        """进入详情页提取正文。使用 JS 端选择器数组，类似 gamersky/gamelook/ali213 模式。"""
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

            content = page.evaluate("""() => {
                const selectors = [
                    'div[class*="content"] div[class*="article"][class*="root"]',
                    'div[class*="article"][class*="root"]',
                    'article[class*="content"]',
                    'div[class*="article"]',
                    'div[class*="content"]',
                    "article",
                    "main",
                ];

                for (const selector of selectors) {
                    const elements = document.querySelectorAll(selector);
                    for (const el of elements) {
                        const text = el.innerText?.trim();
                        if (text && text.length >= 30) {
                            return text;
                        }
                    }
                }

                // 备用：段落拼接
                const paragraphs = document.querySelectorAll(
                    'div[class*="content"] p, main p, article p'
                );
                const texts = [];
                paragraphs.forEach(p => {
                    const text = p.innerText?.trim();
                    if (text) texts.push(text);
                });
                const content = texts.join("\\n");
                return content.length >= 30 ? content : "";
            }""")

            if content and len(content.strip()) >= 30:
                return self._clean_text(content)

            self.logger.warning("未提取到正文: %s", url)
            return ""

        except Exception as exc:
            self.logger.warning("获取详情页失败: %s，原因: %s", url, exc)
            return ""

    def _build_summary(self, title: str, content: str) -> tuple[str, datetime | None]:
        """优先使用 BaseCrawler 的 generate_summary，失败时回退至正文或标题。"""
        summary = ""
        summary_generated_at = None

        if content:
            try:
                summary, summary_generated_at = self.generate_summary(title, content)
            except Exception as exc:
                self.logger.warning("生成摘要失败: %s", exc)

        summary = self._clean_text(summary)
        if summary:
            # 约束长度（避免摘要过长撑爆后处理）
            return summary[:150], summary_generated_at

        fallback = self._clean_text(content)
        if fallback:
            return fallback[:150], None

        return title, None

    def _yxfx_fetch_list(
        self,
        game_id: int,
        page: int = 1,
        num: int = 10,
        article_type: str = "",
        timeout: int = 30,
    ) -> List[dict]:
        """通过 yxfx_api 拉取新闻列表。"""
        headers = {
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }

        payload = {
            "jsonrpc": "2.0",
            "method": "get_game_news_list",
            "id": f"get_game_news_list_{game_id}_{page}_{num}",
            "params": {
                "page": page,
                "num": num,
                "game_id": str(game_id),
                "article_type": article_type,
            },
        }

        r = requests.post(
            self.YXFX_API_URL,
            headers=headers,
            data=json.dumps(payload, ensure_ascii=False),
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("result", {}).get("list", [])

    def fetch(self) -> List[NewsItem]:
        """采集新闻列表及详情正文（支持 剑网3 + 剑网3缘起）。"""
        items: List[NewsItem] = []
        seen_urls = set()

        self.logger.info(
            "开始采集西山居游戏官网新闻，时间窗口：过去 %s 小时（%s 至今）",
            self.hours_window,
            self.cutoff_time.strftime("%Y-%m-%d %H:%M"),
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(
                viewport={"width": 1440, "height": 1200},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            detail_page = context.new_page()

            try:
                for game_id, channel_name in self.GAME_CHANNELS:
                    news_list = []
                    try:
                        news_list = self._yxfx_fetch_list(game_id=game_id, page=1, num=20)
                    except Exception as exc:
                        self.logger.warning(
                            "拉取列表失败: game_id=%s err=%s",
                            game_id,
                            exc,
                        )
                        news_list = []

                    if not news_list:
                        self.logger.info("game_id=%s 无新数据", game_id)
                        continue

                    for index_in_page, item in enumerate(news_list):
                        dynamic_id = item.get("dynamic_id")
                        title = self._clean_text(item.get("title") or "")
                        inputtime = item.get("inputtime")

                        publish_time = self._ts_to_datetime(inputtime)
                        if not publish_time:
                            continue

                        if publish_time < self.cutoff_time:
                            # 列表通常按时间倒序，这里继续看同页是否有未超出窗口的
                            continue

                        if not dynamic_id or not title:
                            continue

                        url = f"{self.BASE_URL}/content/news/?id={dynamic_id}&gameId={game_id}"
                        if url in seen_urls:
                            continue
                        seen_urls.add(url)

                        self.logger.info(
                            "采集详情（%s | %s）：%s - %s",
                            len(items) + 1,
                            channel_name,
                            publish_time.strftime("%Y-%m-%d"),
                            title,
                        )

                        content = self._get_detail_content(detail_page, url)
                        summary, summary_generated_at = self._build_summary(title, content)

                        items.append(
                            NewsItem(
                                title=title,
                                date=publish_time.strftime("%Y-%m-%d"),
                                url=url,
                                source=self.source_name,
                                source_code=self.source_code,
                                credibility_tag=self.credibility_base,
                                category="产品动态",
                                publish_time=publish_time,
                                summary=summary,
                                summary_generated_at=summary_generated_at,
                                content=content,
                                raw_data={
                                    "game_id": game_id,
                                    "channel_name": channel_name,
                                    "dynamic_id": dynamic_id,
                                    "inputtime": inputtime,
                                    "index_in_page": index_in_page,
                                },
                            )
                        )

                        time.sleep(0.5)

            finally:
                context.close()
                browser.close()

        self.logger.info("西山居游戏官网新闻采集完成，共 %s 条", len(items))
        return items


def main():
    crawler = XiaoYouCrawler(enable_summary=True)
    crawler.run()


if __name__ == "__main__":
    main()
