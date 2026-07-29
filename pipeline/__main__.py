# -*- coding: utf-8 -*-
"""
数据处理管道 - 可执行入口
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from pipeline import DataPipeline
from datetime import datetime


def main():
    """测试运行"""
    pipeline = DataPipeline(hours=24)

    # 加载今日所有数据
    pipeline.load_from_files(date=datetime.now().strftime('%Y%m%d'))

    # 处理
    pipeline.process()

    # 生成简报
    if pipeline.items:
        md = pipeline.generate_briefing()
        print("\n" + "="*60)
        print("简报预览（前1000字符）:")
        print("="*60)
        print(md[:1000])
        print("\n... [内容截断] ...")
    else:
        print("\n没有数据可生成简报")


if __name__ == "__main__":
    main()
