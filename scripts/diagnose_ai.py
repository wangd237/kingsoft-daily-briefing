# -*- coding: utf-8 -*-
"""
AI 摘要模块诊断脚本
用于排查 AI 摘要生成失败的原因
"""
import os
import sys
from pathlib import Path

# 添加项目路径
sys.path.append(str(Path(__file__).parent.parent))

def check_env():
    """检查环境变量"""
    print("=" * 60)
    print("1. 环境变量检查")
    print("=" * 60)

    # 尝试加载 .env
    try:
        from dotenv import load_dotenv
        load_dotenv()
        print("✓ python-dotenv 已加载")
    except ImportError:
        print("✗ python-dotenv 未安装 (pip install python-dotenv)")
    except Exception as e:
        print(f"✗ 加载 .env 失败: {e}")

    # 检查关键变量
    api_base = os.getenv('AI_API_BASE', '')
    api_key = os.getenv('AI_API_KEY', '')
    model = os.getenv('AI_MODEL', '')

    print(f"\nAI_API_BASE: {'✓ 已设置' if api_base else '✗ 未设置'}")
    if api_base:
        print(f"  值: {api_base}")

    print(f"\nAI_API_KEY: {'✓ 已设置' if api_key else '✗ 未设置'}")
    if api_key:
        # 只显示前8位和后4位
        masked = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "***"
        print(f"  值: {masked} (长度: {len(api_key)})")

    print(f"\nAI_MODEL: {'✓ 已设置' if model else '✗ 未设置 (将使用默认 gpt-3.5-turbo)'}")
    if model:
        print(f"  值: {model}")

    return api_base, api_key, model


def check_dependencies():
    """检查依赖库"""
    print("\n" + "=" * 60)
    print("2. 依赖库检查")
    print("=" * 60)

    # 检查 openai
    try:
        import openai
        print(f"✓ openai 已安装 (版本: {openai.__version__})")
    except ImportError:
        print("✗ openai 未安装 (pip install openai)")
        return False

    # 检查 requests/httpx
    try:
        import requests
        print(f"✓ requests 已安装")
    except ImportError:
        print("✗ requests 未安装")

    try:
        import httpx
        print(f"✓ httpx 已安装")
    except ImportError:
        print("✗ httpx 未安装 (如需代理建议安装: pip install httpx)")

    return True


def test_api_connection(api_base, api_key, model):
    """测试 API 连接"""
    print("\n" + "=" * 60)
    print("3. API 连接测试")
    print("=" * 60)

    if not api_base or not api_key:
        print("✗ 环境变量未配置，跳过 API 测试")
        return False

    try:
        from openai import OpenAI

        print(f"正在连接: {api_base}")
        print(f"使用模型: {model or 'gpt-3.5-turbo'}")

        # 初始化客户端
        client = OpenAI(
            base_url=api_base,
            api_key=api_key,
        )

        # 测试调用（使用最简单的请求）
        print("\n发送测试请求...")
        response = client.chat.completions.create(
            model=model or "gpt-3.5-turbo",
            messages=[
                {"role": "user", "content": "你好，这是一个测试。请回复'API测试成功'。"}
            ],
            max_tokens=50,
            timeout=30
        )

        result = response.choices[0].message.content
        print(f"✓ API 连接成功!")
        print(f"  响应: {result}")
        return True

    except Exception as e:
        print(f"✗ API 调用失败")
        print(f"  错误类型: {type(e).__name__}")
        print(f"  错误信息: {e}")

        # 常见错误分析
        error_str = str(e).lower()
        if "authentication" in error_str or "api key" in error_str:
            print("\n💡 可能原因: API Key 无效或已过期")
        elif "model" in error_str and ("not found" in error_str or "does not exist" in error_str):
            print(f"\n💡 可能原因: 模型 '{model}' 不存在或无权访问")
            print("   建议: 检查 AI_MODEL 设置，或尝试使用 'gpt-3.5-turbo'")
        elif "connection" in error_str or "timeout" in error_str:
            print("\n💡 可能原因: 网络连接问题")
            print("   建议: 检查网络，或配置代理 (AI_PROXY)")
        elif "rate limit" in error_str:
            print("\n💡 可能原因: API 调用频率限制")
        elif "insufficient_quota" in error_str or "quota" in error_str:
            print("\n💡 可能原因: API 余额不足")

        return False


def test_summarizer():
    """测试完整摘要流程"""
    print("\n" + "=" * 60)
    print("4. 完整摘要流程测试")
    print("=" * 60)

    try:
        from models.ai_summarizer import get_summarizer

        summarizer = get_summarizer()

        if not summarizer.is_available():
            print("✗ AISummarizer 未初始化 (环境变量可能未配置)")
            return False

        print("✓ AISummarizer 初始化成功")
        print(f"  模型: {summarizer.model}")
        print(f"  API地址: {summarizer.api_base}")

        # 测试生成摘要
        print("\n测试生成摘要...")
        test_title = "金山办公：关于2026年半年度业绩预告的自愿性披露公告"
        test_content = """
        北京金山办公软件股份有限公司（以下简称"公司"）预计2026年半年度
        实现营业收入25.5亿元到27.8亿元，与上年同期相比，将增加5.2亿元到7.5亿元，
        同比增长25%到37%。预计实现归属于母公司所有者的净利润为7.8亿元到8.8亿元，
        同比增长28%到45%。本期业绩增长主要系公司持续提升产品体验，
        深化AI技术应用，推动订阅业务快速增长所致。
        """

        result = summarizer.summarize(test_title, test_content, max_length=100)

        if result:
            summary, generated_at = result
            print(f"✓ 摘要生成成功!")
            print(f"  摘要: {summary}")
            print(f"  生成时间: {generated_at}")
            return True
        else:
            print("✗ 摘要生成失败 (返回 None)")
            return False

    except Exception as e:
        print(f"✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主诊断流程"""
    print("=" * 60)
    print("AI 摘要模块诊断工具")
    print("=" * 60)

    # 1. 检查环境变量
    api_base, api_key, model = check_env()

    # 2. 检查依赖
    deps_ok = check_dependencies()
    if not deps_ok:
        print("\n✗ 依赖库未安装，请先安装: pip install openai")
        return

    # 3. 测试 API 连接
    api_ok = test_api_connection(api_base, api_key, model)

    # 4. 测试完整流程
    if api_ok:
        test_summarizer()

    # 总结
    print("\n" + "=" * 60)
    print("诊断总结")
    print("=" * 60)

    if not api_base or not api_key:
        print("❌ 环境变量未配置")
        print("   请创建 .env 文件并设置 AI_API_BASE 和 AI_API_KEY")
    elif not api_ok:
        print("❌ API 连接失败")
        print("   请检查 API 地址、Key 是否正确，网络是否通畅")
    else:
        print("✅ 基础配置正常")
        print("   如果摘要仍失败，请检查日志中的具体错误信息")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
