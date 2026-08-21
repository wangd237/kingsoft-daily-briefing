# -*- coding: utf-8 -*-
"""
公共工具函数
"""
import re


def sanitize_filename(text: str, max_len: int = 20) -> str:
    """清洗文件名：去除非法字符，截断到指定长度。

    >>> sanitize_filename("金山办公：上半年净利润同比增长", 20)
    '金山办公上半年净利润同比增长'
    >>> sanitize_filename('a/b:c*d?"e<f>g|h', 50)
    'abcdfgh'
    """
    return re.sub(r'[\\/:*?"<>|\r\n\t]', '', text)[:max_len]
