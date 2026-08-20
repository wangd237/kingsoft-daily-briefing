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

# 主产物与附件归集（方案书 3.4.2 / 3.4.3）
LATEST_JSON = OUTPUT_DIR / "latest.json"
LATEST_ATTACHMENTS_DIR = OUTPUT_DIR / "latest_attachments"

# 确保目录存在
for dir_path in [OUTPUT_DIR, DATA_DIR, BRIEFING_DIR, LOG_DIR, REPORT_DIR, LATEST_ATTACHMENTS_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)

# 采集器配置
COLLECTORS = {
    'cninfo': {
        'enabled': True,
        'name': '巨潮资讯网',
        'stock_code': '688111',
        'org_id': '9900035303',
        'credibility': '【官方公告】',
        'enable_summary': True,  
        'hours_window': 100,  # 时间窗口：24小时
    },
    'hkex': {
        'enabled': True,
        'name': '港交所披露易',
        'credibility': '【官方公告】',
        'enable_summary': True,
        'hours_window': 100,
        'stocks': [
            {'code': '03888', 'name': '金山软件'},
            {'code': '03896', 'name': '金山云'},
        ],
    },
    'kingsoft_ir': {
        'enabled': True,
        'name': '金山软件IR官网',
        'credibility': '【官方公告】',
        'enable_summary': True,
        'hours_window': 100,  # IR官网公告需要更长时间窗口
    },
    'kingsoft_office_ir': {
        'enabled': True,
        'name': '金山办公IR官网',
        'credibility': '【官方公告】',
        'enable_summary': True,
        'hours_window': 100,  # 时间窗口：24小时
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
            {
                'uid': '2046281757',
                'name': '余玉贤',
                'type': '个人',
                'skip_retweets_from_tracked_accounts': True,

                # 仅供 AI 理解人物、产品和游戏术语；不应写入单条 summary。
                'intro': (
                    '余玉贤是西山居《剑网3》制作人。《剑网3》全称《剑侠情缘网络版叁》，'
                    '是西山居研发并长期运营的中国武侠题材大型多人在线角色扮演游戏（MMORPG），'
                    '以中国传统武侠世界为背景，玩家可选择不同门派、武学心法与角色体型，在开放式江湖中'
                    '体验主线剧情、团队副本、竞技对战、生活技艺、家园及社交等玩法。'
                    '游戏长期通过资料片、赛季与版本更新扩展剧情、地图、门派、装备、外观及玩法内容，'
                    '并形成了较为丰富的玩家社群文化和游戏内术语体系。'
                    '余玉贤的公开发言主要涉及《剑网3》的版本开发、角色与外观设计、玩法调整、运营活动'
                    '及玩家反馈；文中的门派、体型、心法、橙武、拓印等术语均指《剑网3》游戏内设定。'
                ),

                # 日报生成时按账号聚合，仅在“余玉贤动态”板块显示一次。
                'daily_preface': (
                    '余玉贤为西山居《剑侠情缘网络版叁》（以下简称《剑网3》）制作人。'
                    '《剑网3》是一款中国武侠题材大型多人在线角色扮演游戏，其制作人公开发言通常涉及'
                    '版本研发、角色与外观设计、玩法调整、运营活动及玩家反馈。以下为相关产品动态。'
                ),
            },
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
        'enabled': True,
        'name': '西山居游戏',
        'credibility': '【官方资讯】',
        'enable_summary': True,
        'hours_window': 24,
    },
    'stcn': {
        'enabled': True,
        'name': '证券时报e公司',
        'keywords': ['金山软件', '金山办公', '金山云', '西山居', 'WPS'],
        'credibility': '【媒体报道】',
        'enable_summary': True,
        'hours_window': 100,  # 时间窗口：24小时
    },
    'kr36': {
        'enabled': True,
        'name': '36氪',
        'keywords': ['金山软件', '金山办公', '金山云', '西山居', 'WPS'],
        'credibility': '【媒体报道】',
        'enable_summary': True,
        'hours_window': 100,  # 时间窗口：24小时
    },
    'yicai': {
        'enabled': True,
        'name': '第一财经',
        'keywords': ['金山软件', '金山办公', '金山云', '西山居', 'WPS'],
        'credibility': '【媒体报道】',
        'enable_summary': True,
        'hours_window': 100,
    },
    'cls': {
        'enabled': True,
        'name': '财联社',
        'keywords': ['金山软件', '金山办公', '金山云', '西山居', 'WPS'],
        'credibility': '【媒体报道】',
        'enable_summary': True,
        'hours_window': 100,
    },
    'huxiu': {
        'enabled': True,
        'name': '虎嗅',
        'keywords': ['金山软件', '金山办公', '金山云', '西山居', 'WPS'],
        'credibility': '【媒体报道】',
        'enable_summary': True,
        'hours_window': 100,
    },
    'eastmoney': {
        'enabled': True,
        'name': '东方财富网',
        'keywords': ['金山软件', '金山办公', '金山云', '西山居', 'WPS'],
        'credibility': '【媒体报道】',
        'enable_summary': True,
        'hours_window': 100,
    },
    'xueqiu': {
        'enabled': False,  # 雪球需要授权，默认禁用
        'name': '雪球',
        'keywords': ['金山软件', '金山办公', '金山云', '西山居', 'WPS'],
        'credibility': '【媒体报道】',
        'enable_summary': True,
        'hours_window': 24,
    },
    'wind': {
        'enabled': False,  # Wind需要授权，默认禁用
        'name': 'Wind金融终端',
        'keywords': ['金山软件', '金山办公', '金山云', '西山居', 'WPS'],
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
        'hours_window': 100,
    },
    'gamelook': {
        'enabled': True,
        'name': 'GameLook',
        'keywords': ['金山', '西山居', '金山世游', '剑网3', '尘白禁区', '解限机', '鹅鸭杀'],
        'credibility': '【媒体报道】',
        'hours_window': 100,
        'enable_summary': True,
    },
    'gamersky': {
        'enabled': True,
        'name': '游民星空',
        'keywords': ['西山居', '金山世游', '剑网3', '尘白禁区', '解限机', '鹅鸭杀'],
        'credibility': '【媒体报道】',
        'enable_summary': True,
        'hours_window': 100,
    },
    'ali213': {
        'enabled': True,
        'name': '游侠网',
        'keywords': ['西山居', '金山世游', '剑网3', '尘白禁区', '解限机', '鹅鸭杀'],
        'credibility': '【媒体报道】',
        'enable_summary': True,
        'hours_window': 100,
    },
    'youzi': {
        'enabled': True,
        'name': 'GameRes游资网',
        'keywords': ['金山', '西山居', '金山世游', '剑网3', '尘白禁区', '解限机', '鹅鸭杀'],
        'credibility': '【媒体报道】',
        'enable_summary': True,
        'hours_window': 1000,
        'request_delay_min': 1.5,
        'request_delay_max': 3.0,
        'rate_limit_retries': 3,
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
    'default_hours': 100,  # 汇总层统一 24h 兜底（方案书 5.2 决策点 #2，由 48 改为 24）
    'briefing_time': '09:30',
}

# 调度器配置（方案书 3.2.3）
SCHEDULER = {
    'timeout_seconds': 300,  # 单源采集超时上限（秒），超过则终止并标记 timeout
    'max_workers': 4,        # 默认并发采集数
}

# WPS 多维表格对接（M2，总调度器与汇总实施方案.md 模块 M2）
# 凭证来自 open.wps.cn 企业应用，可在 .env 中覆盖：
#   WPS_APP_ID / WPS_APP_KEY / WPS_FILE_ID / WPS_SHEET_ID
BITABLE = {
    # 启用开关：由 .env WPS_SYNC_ENABLED=1 控制（默认关闭），无需改代码
    'enabled': os.environ.get('WPS_SYNC_ENABLED', '0') == '1',
    'base_url': 'https://openapi.wps.cn',
    'app_id': os.environ.get('WPS_APP_ID', ''),
    'app_key': os.environ.get('WPS_APP_KEY', ''),
    'file_id': os.environ.get('WPS_FILE_ID', ''),
    'sheet_id': os.environ.get('WPS_SHEET_ID', ''),
    # OAuth 用户授权：dbsheet 是用户级接口，必须用用户 access_token（不能用应用 token）
    # 首次运行 scripts/wps_authorize.py 换取并持久化到 token_file，之后自动刷新
    'authorize_url': 'https://openapi.wps.cn/oauth2/auth',
    # kso.drive.readwrite：获取云盘列表；kso.file.readwrite：云文档三步上传（附件写入）
    'scope': 'kso.dbsheet.readwrite,kso.dbsheet.read,kso.drive.readwrite,kso.file.readwrite',
    'redirect_uri': os.environ.get('WPS_REDIRECT_URI', 'http://127.0.0.1:8765/callback'),
    'token_file': OUTPUT_DIR / 'wps_token.json',
    # 附件字段名（原"图片和附件"类型。注：开放平台服务端无素材库上传接口，
    # 附件字段无法程序化写入，见 docs/wps_bitable_attachment_upload_journey.md；
    # 方案 A：改上传云文档取链接，写入下方"附件链接"字段）
    'attach_field': '附件',
    # 附件链接字段名（表格里需建"文本"类型列，支持一行一个链接）
    'attach_url_field': '附件链接',
    # 表格自动"日期"字段名（upsert 查重用：信息源 + 创建时间落在今天；
    # 当前表格字段名是"日期"，类型 Date，default_value_type=RecordCreateTime）
    'created_at_field': '日期',
    # 列名 -> 含义（建表时字段名需与此一致；如有改动只改这里）
    # 每个采集器一行：序号/日期 由表格字段类型（自动编号/创建时间）自动生成，不写入
    'field_map': {
        'source': '信息源',
        'summary': '总结',
        'attachments': '附件',
        'attachment_url': '附件链接',
        'links': '原始链接',
        'category': '分类',
        'credibility': '可信度',
    },
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
