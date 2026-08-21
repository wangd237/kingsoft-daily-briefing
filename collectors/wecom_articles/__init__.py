# -*- coding: utf-8 -*-
"""
公众号文章采集器
从 WPS 多维表格读取前一天的公众号文章链接，抓取正文内容
"""
from .crawler import WecomArticlesCrawler

__all__ = ['WecomArticlesCrawler']
