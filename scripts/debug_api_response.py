# -*- coding: utf-8 -*-
"""
增强诊断：查看 API 完整响应
"""
import os
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).parent.parent))

# 加载环境变量
try:
    from dotenv import load_dotenv
    load_dotenv()
except:
    pass

from openai import OpenAI

client = OpenAI(
    base_url=os.getenv('AI_API_BASE'),
    api_key=os.getenv('AI_API_KEY'),
)

model = os.getenv('AI_MODEL', 'gpt-3.5-turbo')
print(f"模型: {model}")
print("=" * 60)

# 测试1：使用 system + user 角色（当前方式）
print("\n测试1: system + user 角色")
print("-" * 60)
try:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是专业的财经资讯分析师。"},
            {"role": "user", "content": "请用一句话总结：金山办公2026年上半年净利润预计增长28%到45%。"}
        ],
        temperature=0.3,
        max_tokens=300,
        timeout=30
    )
    print(f"响应对象: {response}")
    print(f"choices数量: {len(response.choices)}")
    if response.choices:
        print(f"finish_reason: {response.choices[0].finish_reason}")
        print(f"content: '{response.choices[0].message.content}'")
except Exception as e:
    print(f"错误: {e}")

# 测试2：只使用 user 角色
print("\n测试2: 只使用 user 角色")
print("-" * 60)
try:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": "你是专业的财经资讯分析师。请用一句话总结：金山办公2026年上半年净利润预计增长28%到45%。"}
        ],
        temperature=0.3,
        max_tokens=300,
        timeout=30
    )
    print(f"content: '{response.choices[0].message.content}'")
except Exception as e:
    print(f"错误: {e}")

# 测试3：使用 OpenAI 格式（明确的 role: assistant）
print("\n测试3: 使用不同的 max_tokens")
print("-" * 60)
try:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": "请用一句话总结：金山办公2026年上半年净利润预计增长28%到45%。"}
        ],
        temperature=0.3,
        max_tokens=100,  # 减少 token 数
        timeout=30
    )
    print(f"content: '{response.choices[0].message.content}'")
except Exception as e:
    print(f"错误: {e}")

# 测试5：增加 max_tokens
print("\n测试5: 增加 max_tokens 到 500")
print("-" * 60)
try:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": "请用一句话总结：金山办公2026年上半年净利润预计增长28%到45%。"}
        ],
        temperature=0.3,
        max_tokens=500,  # 增加 token 数
        timeout=30
    )
    print(f"finish_reason: {response.choices[0].finish_reason}")
    print(f"content: '{response.choices[0].message.content}'")
    print(f"reasoning_content: '{getattr(response.choices[0].message, 'reasoning_content', 'N/A')[:100]}...'")
except Exception as e:
    print(f"错误: {e}")
