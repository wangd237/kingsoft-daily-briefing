# -*- coding: utf-8 -*-
"""
全局配置
"""
import os
from pathlib import Path

# 尝试加载 .env 文件
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv 未安装时跳过

# 项目根目录
BASE_DIR = Path(__file__).parent.parent

# 输出目录
OUTPUT_DIR = BASE_DIR / "output"
DATA_DIR = OUTPUT_DIR / "data"
BRIEFING_DIR = OUTPUT_DIR / "briefings"
LOG_DIR = OUTPUT_DIR / "logs"
REPORT_DIR = OUTPUT_DIR / "reports"

# 确保目录存在
for dir_path in [OUTPUT_DIR, DATA_DIR, BRIEFING_DIR, LOG_DIR, REPORT_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)

# 采集器配置
COLLECTORS = {
    'cninfo': {
        'enabled': True,
        'name': '巨潮资讯网',
        'stock_code': '688111',
        'org_id': '9900035303',
        'credibility': '【官方公告】',
        'enable_summary': True,  # 启用 AI 摘要
        'hours_window': 24,  # 时间窗口：24小时
    },
    'hkex': {
        'enabled': False,  # 待实现
        'name': '港交所披露易',
        'stocks': [
            {'code': '03888', 'name': '金山软件'},
            {'code': '03896', 'name': '金山云'},
        ],
        'credibility': '【官方公告】',
        'enable_summary': False,
    },
    'kingsoft_ir': {
        'enabled': True,
        'name': '金山软件IR官网',
        'credibility': '【官方公告】',
        'enable_summary': True,  # 启用 AI 摘要
    },
    'kingsoft_office_ir': {
        'enabled': True,
        'name': '金山办公IR官网',
        'credibility': '【官方公告】',
        'enable_summary': True,
        'hours_window': 24,  # 时间窗口：24小时
    },
    'wechat': {
        'enabled': False,  # 待实现
        'name': '微信公众号',
        'accounts': [
            '金山办公',
            'WPS办公软件',
            'WPS 365',
            '西山居',
            '剑网3',
        ],
        'credibility': '【官方资讯】',
        'enable_summary': False,
    },
    'weibo': {
        'enabled': True,
        'name': '微博',
        'credibility': '【官方资讯】',
        'enable_summary': True,
        'hours_window': 24,
        'accounts': [
            # 官方账号
            {'uid': '1298306070', 'name': '金山软件', 'type': '官方'},
            {'uid': '1595145397', 'name': 'WPS', 'type': '官方'},
            {'uid': '2074833864', 'name': '西山居游戏', 'type': '官方'},
            {'uid': '1761587065', 'name': '剑网3', 'type': '官方'},
            {'uid': '7573935610', 'name': '尘白禁区', 'type': '官方'},
            # 个人账号（制作人）
            {'uid': '2046281757', 'name': '余玉贤', 'type': '个人'},
            {'uid': '7056235331', 'name': '邓毅', 'type': '个人'},
        ],
    },
    'media': {
        'enabled': False,
        'name': '财经媒体',
        'sources': ['cls', '36kr', 'huxiu'],
        'credibility': '【媒体报道】',
        'enable_summary': False,
    },
    'xiaoyou': {
        'enabled': False,
        'name': '西山居游戏',
        'games': [
            {'id': 'jx3', 'name': '剑网3'},
            {'id': 'cbjq', 'name': '尘白禁区'},
        ],
        'credibility': '【官方资讯】',
        'enable_summary': False,
    },
    'stcn': {
        'enabled': True,
        'name': '证券时报e公司',
        'keywords': ['金山软件', '金山办公', '金山云', '西山居', 'WPS AI'],
        'credibility': '【媒体报道】',
        'enable_summary': True,
        'hours_window': 24,  # 时间窗口：24小时
    },
    'kr36': {
        'enabled': True,
        'name': '36氪',
        'keywords': ['金山软件', '金山办公', '金山云', '西山居', 'WPS AI'],
        'credibility': '【媒体报道】',
        'enable_summary': True,
        'hours_window': 24,  # 时间窗口：24小时
    },
    'yicai': {
        'enabled': True,
        'name': '第一财经',
        'keywords': ['金山软件', '金山办公', '金山云', '西山居', 'WPS AI'],
        'credibility': '【媒体报道】',
        'enable_summary': True,
    },
    'cls': {
        'enabled': True,
        'name': '财联社',
        'keywords': ['金山软件', '金山办公', '金山云', '西山居', 'WPS AI'],
        'credibility': '【媒体报道】',
        'enable_summary': True,
    },
    'huxiu': {
        'enabled': True,
        'name': '虎嗅',
        'keywords': ['金山软件', '金山办公', '金山云', '西山居', 'WPS AI'],
        'credibility': '【媒体报道】',
        'enable_summary': True,
    },
    'eastmoney': {
        'enabled': True,
        'name': '东方财富网',
        'keywords': ['金山软件', '金山办公', '金山云', '西山居', 'WPS AI'],
        'credibility': '【媒体报道】',
        'enable_summary': True,
    },
    'xueqiu': {
        'enabled': True,
        'name': '雪球',
        'keywords': ['金山软件', '金山办公', '金山云', '西山居', 'WPS AI'],
        'credibility': '【媒体报道】',
        'enable_summary': True,
    },
    'wind': {
        'enabled': False,  # Wind需要授权，默认禁用
        'name': 'Wind金融终端',
        'keywords': ['金山软件', '金山办公', '金山云', '西山居', 'WPS AI'],
        'credibility': '【媒体报道】',
        'enable_summary': False,
        'api_key': '',  # 需要配置API密钥
    },
    'zhidx': {
        'enabled': True,
        'name': '智东西',
        'keywords': ['金山', 'WPS'],
        'credibility': '【媒体报道】',
        'enable_summary': True,
    },
    'gamelook': {
        'enabled': True,
        'name': 'GameLook',
        'keywords': ['金山', '西山居', '金山世游', '剑网3', '尘白禁区', '解限机', '鹅鸭杀'],
        'credibility': '【媒体报道】',
        'enable_summary': True,
    },
    'gamersky': {
        'enabled': True,
        'name': '游民星空',
        'keywords': ['西山居', '金山世游', '剑网3', '尘白禁区', '解限机', '鹅鸭杀'],
        'credibility': '【媒体报道】',
        'enable_summary': True,
    },
    'ali213': {
        'enabled': True,
        'name': '游侠网',
        'keywords': ['西山居', '金山世游', '剑网3', '尘白禁区', '解限机', '鹅鸭杀'],
        'credibility': '【媒体报道】',
        'enable_summary': True,
    },
}

# 分类关键词
CATEGORIES = {
    '资本动态': {
        'keywords': ['公告', '财报', '年报', '半年报', '季报', '业绩', '业绩预告',
                    '营收', '净利润', '利润', '分红', '派息', '回购', '减持',
                    '增持', '股权', '融资', 'ipo', '定增', '股东大会', '调研',
                    '投资者关系', '路演', '解禁', '质押'],
    },
    '产品动态': {
        'keywords': ['wps', 'ai', '版本更新', '新功能', '上线', '发布', '迭代',
                    '剑网3', '尘白禁区', '解限机', '版本', '更新', '维护', '开服',
                    '赛季', '资料片', '新英雄', '新角色', '产品', '功能'],
    },
    '市场&政企合作': {
        'keywords': ['签约', '合作', '战略', '战略合作', '生态', '携手',
                    '联合', '伙伴关系', '政企', '中标', '采购', '合同', '框架'],
    },
    '活动IP': {
        'keywords': ['活动', '联动', '赛事', '比赛', '发布会', '展会', 'cj',
                    'chinajoy', '线下', '见面会', '周年庆', '庆典', '嘉年华'],
    },
    '人事&其他声明': {
        'keywords': ['人事', '任命', '任职', '高管', '离职', '跳槽', '加入',
                    '招聘', '诚聘', '声明', '澄清', '说明', '辞职', '聘任'],
    },
}

# 时间过滤配置
TIME_FILTER = {
    'default_hours': 48,
    'briefing_time': '09:30',
}

# 推送配置
NOTIFIERS = {
    'email': {
        'enabled': False,
        'smtp_host': '',
        'smtp_port': 587,
        'username': '',
        'password': '',
        'to': [],
    },
    'feishu': {
        'enabled': False,
        'webhook_url': '',
    },
    'dingtalk': {
        'enabled': False,
        'webhook_url': '',
    },
}
