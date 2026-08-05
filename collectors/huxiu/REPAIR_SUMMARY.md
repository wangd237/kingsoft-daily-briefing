# 虎嗅采集器修复总结

## 问题分析

虎嗅网（huxiu.com）实施了严格的反爬机制：

1. **验证码保护**：访问任何页面（首页、搜索页、文章详情页）都会触发验证码/滑块验证
2. **浏览器指纹检测**：能够检测 Playwright/Selenium 等自动化工具
3. **IP 限制**：即使是普通浏览器，频繁访问也会触发限制

## 已实施的修复措施

### 1. 添加反检测配置（Stealth 脚本）
- 隐藏 `navigator.webdriver` 属性
- 模拟真实浏览器的插件列表
- 模拟真实浏览器的语言设置
- 修改 Chrome 运行时属性
- 覆盖权限查询 API

### 2. 简化搜索流程
- 直接使用 URL 参数访问搜索结果页，减少页面交互
- 避免在搜索框输入关键词的复杂操作
- 添加验证码检测函数 `_check_captcha()`

### 3. 增加智能重试机制
- 检测到验证码时自动等待10秒后重试
- 最多重试2次，避免无限循环

### 4. 添加备选采集方案
- 当搜索被拦截时，可启用直接访问文章ID的方式
- 通过环境变量 `HUXIU_USE_DIRECT_IDS=true` 启用

## 当前状态

**测试结果**：即使添加了以上所有反检测措施，虎嗅网仍然能够识别并拦截自动化访问。

**原因**：虎嗅可能使用了以下高级检测手段：
- WebGL/Canvas 指纹检测
- 行为分析（鼠标移动、点击模式等）
- TLS/JA3 指纹检测
- IP 信誉评分

## 可行的解决方案

### 方案1：使用代理IP（推荐）
```python
# 在创建浏览器上下文时添加代理
context = browser.new_context(
    proxy={"server": "http://proxy.example.com:8080"}
)
```

### 方案2：使用已登录的 Cookies
1. 用户手动登录虎嗅网
2. 导出浏览器 Cookies
3. 在采集器中加载 Cookies

```python
# 加载 cookies
context.add_cookies([
    {'name': 'session_id', 'value': 'xxx', 'domain': '.huxiu.com', 'path': '/'},
    # 其他 cookies...
])
```

### 方案3：使用付费反爬服务
- 使用如 ScrapingBee、ScrapingAnt 等专业服务
- 这些服务使用真实浏览器农场和住宅IP

### 方案4：降低采集频率
- 增加请求间隔到 30-60 秒
- 仅在必要时采集（如每天一次）
- 避免在高峰时段采集

### 方案5：使用 RSS/ API
- 查找虎嗅是否提供 RSS 订阅源
- 查找是否有官方/非官方 API 接口

## 代码改进总结

修改的文件：`collectors/huxiu/crawler.py`

主要改动：
1. ✅ 添加 `_check_captcha()` 方法检测验证码
2. ✅ 添加 `_fetch_recent_articles()` 备选采集方案
3. ✅ 简化 `_search_keyword()` 方法流程
4. ✅ 在浏览器初始化时注入 stealth 脚本
5. ✅ 添加验证码重试机制
6. ✅ 添加更详细的日志输出

## 使用建议

1. **短期**：暂时停用虎嗅采集器，专注于其他数据源（财联社、第一财经、36氪等）
2. **中期**：考虑使用代理IP服务
3. **长期**：评估是否值得为虎嗅投入额外的反爬成本

## 环境变量配置

```bash
# 设置时间窗口（默认24小时）
export HUXIU_HOURS_WINDOW=24

# 启用备选采集方案（直接访问文章ID）
export HUXIU_USE_DIRECT_IDS=true
```
