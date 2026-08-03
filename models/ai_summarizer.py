# -*- coding: utf-8 -*-
"""
AI 摘要生成模块
支持 OpenAI 兼容接口
"""
import os
import logging
from datetime import datetime
from typing import Optional, Tuple

try:
    import openai
    from openai import OpenAI
except ImportError:
    OpenAI = None


class AISummarizer:
    """AI 摘要生成器"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

        # 读取配置
        self.api_base = os.getenv('AI_API_BASE', '')
        self.api_key = os.getenv('AI_API_KEY', '')
        self.model = os.getenv('AI_MODEL', 'gpt-3.5-turbo')
        self.proxy = os.getenv('AI_PROXY', '')

        # 初始化客户端
        self.client = None
        if OpenAI and self.api_base and self.api_key:
            client_kwargs = {
                'base_url': self.api_base,
                'api_key': self.api_key,
            }
            if self.proxy:
                client_kwargs['http_client'] = self._create_proxy_client()

            self.client = OpenAI(**client_kwargs)
            self.logger.info(f"AI Summarizer 初始化完成，模型: {self.model}")
        else:
            if not OpenAI:
                self.logger.warning("未安装 openai 库，AI 摘要功能不可用")
            else:
                self.logger.warning("AI API 配置不完整，摘要功能将跳过")

    def _create_proxy_client(self):
        """创建带代理的 HTTP 客户端"""
        import httpx
        return httpx.Client(proxy=self.proxy)

    def summarize(self, title: str, content: str, max_length: int = 150) -> Optional[Tuple[str, datetime]]:
        """
        生成公告摘要

        Args:
            title: 公告标题
            content: 公告正文（建议截断到 5000-8000 字符）
            max_length: 摘要最大字数

        Returns:
            (摘要文本, 生成时间)，失败返回 None
        """
        if not self.client:
            self.logger.debug("AI 客户端未初始化，跳过摘要生成")
            return None

        if not content or len(content.strip()) < 50:
            self.logger.warning(f"内容太短，跳过摘要: {title}")
            return None

        # 截断内容，防止超出 token 限制
        content_truncated = content[:6000] if len(content) > 6000 else content

        prompt = f"""请对以下上市公司公告生成一段中文摘要（{max_length}字以内）。

要求：
1. 说明公告的核心事项
2. 如有具体数字（金额、股份数量、时间），请包含
3. 语言简洁专业，适合投资者快速阅读
4. 不要包含"根据公告内容"等套话，直接陈述事实

公告标题：{title}

公告正文：
{content_truncated}

请直接输出摘要，不要有任何前缀、标题或解释："""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是专业的财经资讯分析师，擅长提炼公告核心信息。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=5000,
                timeout=30
            )

            summary = response.choices[0].message.content.strip()

            # 清理可能的引号
            summary = summary.strip('"\'')

            self.logger.info(f"摘要生成成功: {title[:30]}... ({len(summary)}字)")
            return summary, datetime.now()  # 返回摘要和生成时间

        except Exception as e:
            self.logger.error(f"摘要生成失败: {e}")
            return None

    def is_available(self) -> bool:
        """检查 AI 服务是否可用"""
        return self.client is not None


# 单例模式
_summarizer: Optional[AISummarizer] = None


def get_summarizer() -> AISummarizer:
    """获取 AI 摘要器实例"""
    global _summarizer
    if _summarizer is None:
        _summarizer = AISummarizer()
    return _summarizer
