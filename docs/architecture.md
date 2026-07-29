# 金山系资讯采集系统 - 项目架构设计

## 目录结构

```
kingsoft-news-collector/
├── README.md                      # 项目说明
├── requirements.txt               # Python依赖
├── main.py                        # 主入口（调度器）
├── .env.example                   # 环境变量示例
│
├── collectors/                    # 📁 采集器目录（各信息源独立）
│   ├── __init__.py
│   ├── base.py                    # 采集器基类
│   ├── cninfo/                    # 巨潮资讯
│   │   ├── __init__.py
│   │   ├── crawler.py             # 采集脚本
│   │   ├── config.py              # 配置（股票代码等）
│   │   └── README.md              # 该源说明文档
│   ├── hkex/                      # 港交所
│   │   ├── __init__.py
│   │   ├── crawler.py
│   │   ├── config.py
│   │   └── README.md
│   ├── wechat/                    # 微信公众号
│   │   ├── __init__.py
│   │   ├── crawler.py
│   │   ├── config.py              # 账号列表
│   │   └── README.md
│   ├── weibo/                     # 官方微博
│   │   ├── __init__.py
│   │   ├── crawler.py
│   │   ├── config.py
│   │   └── README.md
│   ├── media/                     # 财经媒体（财联社/36氪等）
│   │   ├── __init__.py
│   │   ├── cls_crawler.py         # 财联社
│   │   ├── kr36_crawler.py        # 36氪
│   │   ├── huxiu_crawler.py       # 虎嗅
│   │   ├── config.py
│   │   └── README.md
│   └── xiaoyou/                   # 西山居游戏
│       ├── __init__.py
│       ├── crawler.py
│       ├── config.py              # 游戏ID配置
│       └── README.md
│
├── pipeline/                      # 📁 数据处理管道
│   ├── __init__.py
│   ├── base.py                    # 处理器基类
│   ├── classifier/                # 分类器
│   │   ├── __init__.py
│   │   ├── category_classifier.py # 五分类器
│   │   ├── credibility_tagger.py  # 可信度标签
│   │   └── keywords.py            # 关键词库
│   ├── deduplicator/              # 去重器
│   │   ├── __init__.py
│   │   ├── url_dedup.py           # URL去重
│   │   ├── title_dedup.py         # 标题去重
│   │   └── similarity.py          # 相似度计算
│   └── cleaner/                   # 清洗器
│       ├── __init__.py
│       └── content_cleaner.py
│
├── models/                        # 📁 数据模型
│   ├── __init__.py
│   ├── news.py                    # NewsItem/BriefingItem
│   └── enums.py                   # 枚举定义（分类/可信度）
│
├── output/                        # 📁 输出目录
│   ├── briefings/                 # 每日简报
│   │   ├── 2026/
│   │   │   ├── 07/
│   │   │   │   ├── briefing_20260729.md
│   │   │   │   └── briefing_20260730.md
│   │   │   └── 08/
│   │   └── archive/               # 归档
│   ├── reports/                   # 统计报告
│   │   ├── weekly/                # 周报
│   │   ├── monthly/               # 月报
│   │   └── source_analysis/       # 各源分析报告
│   ├── data/                      # 原始数据（JSON/CSV）
│   │   ├── cninfo/
│   │   ├── hkex/
│   │   ├── wechat/
│   │   └── ...
│   └── logs/                      # 日志文件
│       ├── collectors/            # 采集日志
│       ├── pipeline/              # 处理日志
│       └── errors/                # 错误日志
│
├── config/                        # 📁 配置文件
│   ├── __init__.py
│   ├── settings.py                # 全局配置
│   ├── sources.yaml               # 信息源配置
│   ├── keywords.yaml              # 分类关键词
│   └── schedules.yaml             # 定时任务配置
│
├── notifier/                      # 📁 推送通道
│   ├── __init__.py
│   ├── base.py
│   ├── email.py
│   ├── feishu.py                  # 飞书
│   ├── dingtalk.py                # 钉钉
│   └── wecom.py                   # 企业微信
│
├── scheduler/                     # 📁 定时调度
│   ├── __init__.py
│   └── daily_job.py               # APScheduler任务
│
├── docs/                          # 📁 文档
│   ├── architecture.md            # 架构设计
│   ├── 信息源分析报告.md           # 各源分析
│   └── api_reference.md           # API参考
│
└── tests/                         # 📁 测试
    ├── __init__.py
    ├── test_collectors/
    ├── test_pipeline/
    └── test_notifier/
```

## 核心设计原则

### 1. 隔离性
- **采集器隔离**：每个信息源独立目录，互不影响
- **数据隔离**：各源原始数据分开存储
- **日志隔离**：每个采集器独立日志文件
- **配置隔离**：每个采集器独立配置文件

### 2. 扩展性
- **基类设计**：所有采集器继承BaseCrawler
- **插件化**：新增信息源只需添加目录
- **管道化**：数据处理可插拔

### 3. 可追溯
```
output/
├── briefings/       # 最终简报（按年/月组织）
├── data/            # 原始数据（按源/日期组织）
└── logs/            # 日志（按组件/日期组织）
```

### 4. 命名规范

**文件命名：**
```
# 简报
briefing_YYYYMMDD.md
briefing_20260729.md

# 数据
cninfo_YYYYMMDD_HHMMSS.json
wechat_20260729_093000.json

# 日志
cninfo_YYYYMMDD.log
error_YYYYMMDD.log
```

**目录组织：**
```
output/data/cninfo/2026/07/29/cninfo_20260729_103000.json
output/logs/collectors/cninfo_20260729.log
```

## 数据流向

```
┌─────────────────────────────────────────────────────────────┐
│                        Collectors                            │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐           │
│  │ 巨潮资讯 │ │ 港交所  │ │ 微信公众号│ │  其他   │ ...       │
│  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘           │
│       │           │           │           │                 │
│       └───────────┴───────────┴───────────┘                 │
│                   │                                          │
│            output/data/{source}/YYYY/MM/DD/                 │
│                   │                                          │
└───────────────────┼──────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────┐
│                        Pipeline                              │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐           │
│  │  清洗   │ → │  去重   │ → │  分类   │ → │  标签   │           │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘           │
│       ↑              ↑             ↑            ↑            │
│  output/logs/pipeline/YYYYMMDD.log                           │
└───────────────────┼──────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────┐
│                    Briefing Generator                        │
│                                                              │
│  output/briefings/YYYY/MM/briefing_YYYYMMDD.md              │
│                                                              │
└───────────────────┬──────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────┐
│                      Notifiers                               │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐                       │
│  │  邮件   │ │  飞书   │ │  钉钉   │ ...                   │
│  └─────────┘ └─────────┘ └─────────┘                       │
└─────────────────────────────────────────────────────────────┘
```

## 使用示例

### 单个采集器运行
```bash
python -m collectors.cninfo.crawler
```

### 完整流程
```bash
python main.py --date 20260729 --push feishu
```

### 生成周报
```bash
python main.py --report weekly --start 20260701 --end 20260707
```

## 配置文件示例

### config/sources.yaml
```yaml
collectors:
  cninfo:
    enabled: true
    stock_code: "688111"
    org_id: "9900035303"
    schedule: "09:00"
    
  hkex:
    enabled: true
    stocks:
      - code: "03888"
        name: "金山软件"
      - code: "03896"
        name: "金山云"
    schedule: "09:05"
    
  wechat:
    enabled: true
    api_provider: "qingbo"  # 清博/新榜
    accounts:
      - "金山办公"
      - "WPS办公软件"
      - "西山居"
    schedule: "09:10"
```

## 下一步行动

- [ ] 迁移现有脚本到对应目录
- [ ] 创建基类（BaseCrawler）
- [ ] 实现配置加载模块
- [ ] 编写各采集器配置文件
- [ ] 设置日志系统
