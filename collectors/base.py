# -*- coding: utf-8 -*-
"""
采集器基类
所有具体采集器都应继承此类
"""
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List
from dataclasses import dataclass
import logging
import json
import os


@dataclass
class NewsItem:
    """资讯条目数据模型"""
    title: str
    date: str
    url: str
    source: str = ""                    # 信息源名称
    source_code: str = ""               # 信息源代码
    credibility_tag: str = "【官方公告】" # 【官方公告】/【官方资讯】/【媒体报道】
    category: str = ""                  # ①资本动态 ②产品动态 ③市场合作 ④活动IP ⑤人事其他
    publish_time: datetime = None
    summary: str = ""
    content: str = ""
    raw_data: dict = None               # 原始数据

    def __post_init__(self):
        if self.publish_time is None and self.date:
            try:
                self.publish_time = datetime.strptime(self.date, '%Y-%m-%d')
            except:
                try:
                    self.publish_time = datetime.strptime(self.date, '%Y-%m-%d %H:%M:%S')
                except:
                    pass

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            'title': self.title,
            'date': self.date,
            'url': self.url,
            'source': self.source,
            'source_code': self.source_code,
            'credibility_tag': self.credibility_tag,
            'category': self.category,
            'publish_time': self.publish_time.isoformat() if self.publish_time else None,
            'summary': self.summary,
            'content': self.content,
            'raw_data': self.raw_data
        }


class BaseCrawler(ABC):
    """采集器基类"""

    # 子类必须定义的属性
    source_name: str = ""           # 信息源名称
    source_code: str = ""           # 信息源代码（短标识）
    credibility_base: str = ""      # 基础可信度标签

    def __init__(self, output_dir: str = "output/data", log_dir: str = "output/logs"):
        self.output_dir = output_dir
        self.log_dir = log_dir
        self.logger = self._setup_logger()
        self.items: List[NewsItem] = []

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

    def _get_data_path(self) -> str:
        """获取数据文件路径"""
        now = datetime.now()
        date_dir = now.strftime('%Y/%m/%d')
        filename = now.strftime(f'{self.source_code}_%Y%m%d_%H%M%S.json')
        return f"{self.output_dir}/{self.source_code}/{date_dir}/{filename}"

    @abstractmethod
    def fetch(self) -> List[NewsItem]:
        """
        采集数据的主方法
        子类必须实现
        """
        pass

    def save(self) -> str:
        """
        保存采集的数据到JSON文件
        返回保存的文件路径
        """
        if not self.items:
            self.logger.warning("没有数据可保存")
            return ""

        file_path = self._get_data_path()
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        data = {
            'source': self.source_name,
            'source_code': self.source_code,
            'fetch_time': datetime.now().isoformat(),
            'count': len(self.items),
            'items': [item.to_dict() for item in self.items]
        }

        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        self.logger.info(f"数据已保存: {file_path}")
        return file_path

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

    def __enter__(self):
        return self

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
