# -*- coding: utf-8 -*-
"""
金山软件IR官网采集器

采集金山软件IR官网三个栏目：
- 公告：港交所公告，通过HKEX-EPS文件名提取日期
- 新闻稿：PDF格式新闻稿
- 投资者活动：电话会议等活动信息

支持PDF附件下载和AI摘要生成。
"""
from .crawler import KingsoftIRCrawler

__all__ = ['KingsoftIRCrawler']
