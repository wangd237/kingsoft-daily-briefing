# -*- coding: utf-8 -*-
"""
采集器基类
所有具体采集器都应继承此类
"""
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional, Tuple
from pathlib import Path
import logging
import json
import os

from models.news import NewsItem


def load_content_from_ref(content_ref: str, base_dir: str = "output/data") -> str:
    """
    根据 content_ref 加载正文内容

    Args:
        content_ref: 内容文件引用路径
        base_dir: 数据根目录

    Returns:
        正文内容，失败返回空字符串
    """
    if not content_ref:
        return ""

    try:
        content_path = Path(base_dir) / content_ref
        if content_path.exists():
            with open(content_path, 'r', encoding='utf-8') as f:
                return f.read()
        else:
            logging.getLogger(__name__).warning(f"内容文件不存在: {content_path}")
    except OSError as e:
        logging.getLogger(__name__).error(f"加载内容文件失败: {e}")

    return ""


class BaseCrawler(ABC):
    """采集器基类"""

    # 子类必须定义的属性
    source_name: str = ""           # 信息源名称
    source_code: str = ""           # 信息源代码（短标识）
    credibility_base: str = ""      # 基础可信度标签

    def __init__(self, output_dir: str = "output/data", log_dir: str = "output/logs", enable_summary: bool = False):
        self.output_dir = output_dir
        self.log_dir = log_dir
        self.logger = self._setup_logger()
        self.items: List[NewsItem] = []
        self._batch_dir: Optional[str] = None
        self.enable_summary = enable_summary  # 保存 enable_summary 属性

        # 初始化 AI 摘要器（如果启用）
        self.summarizer = None
        if enable_summary:
            try:
                from models.ai_summarizer import get_summarizer
                self.summarizer = get_summarizer()
                if not self.summarizer.is_available():
                    self.logger.warning("AI 摘要服务不可用")
            except Exception as e:
                self.logger.warning(f"AI 摘要模块加载失败: {e}")

    def _setup_logger(self) -> logging.Logger:
        """设置日志"""
        logger = logging.getLogger(self.source_code or 'crawler')
        logger.setLevel(logging.INFO)

        if not logger.handlers:
            # 控制台输出
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)

            # 文件输出
            log_path = self._get_log_path()
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            file_handler = logging.FileHandler(log_path, encoding='utf-8')
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

        return logger

    def _get_log_path(self) -> str:
        """获取日志文件路径"""
        today = datetime.now().strftime('%Y/%m/%d')
        return f"{self.log_dir}/collectors/{self.source_code}/{today}.log"

    def _get_data_path(self) -> tuple:
        """
        获取数据文件路径
        返回 (批次目录路径, json文件名)
        批次目录结构：output/data/cninfo/2026/07/31/cninfo_20260731_155933/
        """
        # 如果子类已经设置了批次目录，直接使用
        if self._batch_dir:
            batch_dir = self._batch_dir
            json_filename = f"{Path(batch_dir).name}.json"
            return batch_dir, json_filename

        # 否则创建新的批次目录
        now = datetime.now()
        date_dir = now.strftime('%Y/%m/%d')
        batch_name = now.strftime(f'{self.source_code}_%Y%m%d_%H%M%S')
        batch_dir = f"{self.output_dir}/{self.source_code}/{date_dir}/{batch_name}"
        json_filename = f"{batch_name}.json"
        return batch_dir, json_filename

    def save(self) -> str:
        """
        保存采集的数据到JSON文件（分级存储）
        - content 存到单独文本文件
        - JSON 只存 content_ref 引用
        返回保存的文件路径
        """
        if not self.items:
            self.logger.warning("没有数据可保存")
            return ""

        batch_dir, json_filename = self._get_data_path()
        os.makedirs(batch_dir, exist_ok=True)

        # 构建完整的JSON路径
        json_path = os.path.join(batch_dir, json_filename)

        # 保存正文内容到批次目录下的 contents 子目录
        content_dir = os.path.join(batch_dir, "contents")
        os.makedirs(content_dir, exist_ok=True)

        for idx, item in enumerate(self.items):
            if item.content and len(item.content.strip()) > 0:
                # 生成内容文件名（使用索引保证唯一）
                content_filename = f"ann_{idx:03d}.txt"
                content_path = os.path.join(content_dir, content_filename)

                try:
                    with open(content_path, 'w', encoding='utf-8') as f:
                        f.write(item.content)

                    # 设置相对引用路径（相对于批次目录）
                    item.content_ref = f"contents/{content_filename}"
                    self.logger.debug(f"正文已保存: {content_path}")
                except OSError as e:
                    self.logger.error(f"保存正文失败: {e}")
                    item.content_ref = ""

        # 保存 JSON（不包含 content，只存引用）
        data = {
            'source': self.source_name,
            'source_code': self.source_code,
            'fetch_time': datetime.now().isoformat(),
            'count': len(self.items),
            'items': [item.to_dict() for item in self.items]
        }

        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        self.logger.info(f"数据已保存: {json_path} (批次目录: {batch_dir})")
        return json_path

    @abstractmethod
    def fetch(self) -> List[NewsItem]:
        """
        采集数据的主方法
        子类必须实现
        """
        pass

    def run(self) -> List[NewsItem]:
        """
        运行完整流程：采集 + 保存
        返回采集的数据
        """
        self.logger.info(f"开始采集: {self.source_name}")

        try:
            self.items = self.fetch()
            self.logger.info(f"采集完成: {len(self.items)} 条")

            if self.items:
                self.save()

            return self.items

        except Exception as e:
            self.logger.error(f"采集失败: {e}", exc_info=True)
            raise

    def generate_summary(self, title: str, content: str, max_length: int = 150) -> Tuple[str, datetime]:
        """
        生成摘要，始终返回有效文本（AI生成或fallback截取）

        Args:
            title: 标题
            content: 正文内容
            max_length: 摘要最大字数

        Returns:
            (摘要文本, 生成时间)
            摘要文本永远不会为None，失败时使用fallback策略
        """
        # 1. 尝试AI摘要
        if self.summarizer and self.summarizer.is_available() and len(content) >= 50:
            try:
                result = self.summarizer.summarize(title, content, max_length)
                if result:
                    summary, gen_time = result
                    return summary, gen_time  # AI成功，静默返回
            except Exception as e:
                self.logger.warning(f"AI摘要失败，使用fallback: {e}")

        # 2. Fallback策略（AI失败或不可用时，记录日志）
        if not content or len(content.strip()) == 0:
            # 无内容，使用标题
            self.logger.info(f"内容为空，使用标题作为摘要: {title[:30]}...")
            return title[:max_length], datetime.now()

        elif len(content) <= max_length:
            # 内容短，直接用全文
            self.logger.info(f"内容较短({len(content)}字)，直接使用全文作为摘要: {title[:30]}...")
            return content, datetime.now()

        else:
            # 内容长，截取前max_length字
            truncated = content[:max_length] + "..."
            self.logger.info(f"使用截取摘要({max_length}字): {title[:30]}...")
            return truncated, datetime.now()

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器退出时自动保存"""
        if self.items:
            self.save()

    def _parse_time(self, time_str: str) -> str:
        """
        解析时间字符串，返回 YYYY-MM-DD 格式
        子类可覆盖以支持特定格式
        """
        if not time_str or time_str.strip() == '':
            return ''

        time_str = time_str.strip()

        # 标准格式（优先）
        formats = [
            '%Y-%m-%d',      # 2025-12-31
            '%Y/%m/%d',      # 2025/12/31
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d %H:%M',
        ]

        for fmt in formats:
            try:
                from datetime import datetime
                dt = datetime.strptime(time_str, fmt)
                return dt.strftime('%Y-%m-%d')
            except:
                continue

        # 其他格式需要额外验证
        other_formats = [
            ('%d/%m/%Y', 'DMY'),      # 31/12/2025 港式
            ('%d-%m-%Y', 'DMY'),      # 31-12-2025
            ('%B %d, %Y', 'MDY'),     # December 31, 2025
            ('%b %d, %Y', 'MDY'),     # Dec 31, 2025
            ('%Y年%m月%d日', 'YMD'),   # 2025年12月31日
            ('%Y年%m月%d', 'YMD'),     # 2025年12月31
        ]

        for fmt, fmt_type in other_formats:
            try:
                from datetime import datetime
                dt = datetime.strptime(time_str, fmt)

                # 验证日期合理性
                if dt.month > 12 or dt.day > 31:
                    continue

                return dt.strftime('%Y-%m-%d')
            except:
                continue

        # 正则提取日期（安全模式）
        import re
        # 优先匹配 YYYY-MM-DD 或 YYYY/MM/DD
        match = re.match(r'^(\d{4})[\-/](\d{1,2})[\-/](\d{1,2})$', time_str)
        if match:
            year, month, day = match.groups()
            try:
                month_int = int(month)
                day_int = int(day)
                # 验证月份和日期范围
                if 1 <= month_int <= 12 and 1 <= day_int <= 31:
                    from datetime import datetime
                    dt = datetime(int(year), month_int, day_int)
                    return dt.strftime('%Y-%m-%d')
            except:
                pass

        return ''
