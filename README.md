# 金山系资讯采集系统

自动采集金山系（金山办公、金山软件、金山云、西山居）相关资讯，生成每日简报。

## 📋 功能特性

- **多源采集**：巨潮资讯、港交所、IR官网、财经媒体等
- **智能分类**：五大类别自动归类
- **可信度标签**：官方公告/官方资讯/媒体报道
- **每日简报**：Markdown格式自动输出

## 🏗️ 项目结构

```
.
├── collectors/          # 信息采集器
│   ├── cninfo/         # 巨潮资讯网
│   ├── hkex/           # 港交所披露易
│   ├── kingsoft_ir/    # 金山软件IR官网
│   └── stcn/           # 证券时报
├── pipeline/           # 数据处理管道
├── models/             # 数据模型
├── config/             # 配置文件
└── output/             # 输出目录
    ├── data/           # 原始数据
    └── briefings/      # 每日简报
```

## 🚀 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
playwright install chromium
```

### 运行采集

```bash
# 运行单个采集器
python collectors/cninfo/crawler.py

# 运行完整流程
python main.py
```

### 生成简报

```bash
python pipeline/__init__.py
```

## 📊 五大分类

1. **资本动态** - 公告、财报、融资、股权
2. **产品动态** - WPS更新、游戏版本、AI功能
3. **市场&政企合作** - 签约、战略合作
4. **活动IP** - 发布会、赛事、联动
5. **人事&其他声明** - 人事变动、澄清公告

## 📝 输出示例

简报输出路径：`output/briefings/YYYY/MM/briefing_YYYYMMDD.md`

```markdown
# 金山系资讯日报 - 2026年07月29日

## 📊 概览
- 资讯总数：**12** 条
- 官方公告：5 条

## ①资本动态 (3条)
### 1. 【官方公告】 金山软件: 2026年第一季度业绩公告
...
```

## ⚙️ 配置

编辑 `config/settings.py`：

- `COLLECTORS` - 启用/禁用采集器
- `CATEGORIES` - 分类关键词
- `TIME_FILTER` - 时间过滤设置

## 📄 License

MIT
