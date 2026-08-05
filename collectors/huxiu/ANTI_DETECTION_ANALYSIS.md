# 虎嗅反爬检测分析

## 问题：为什么真实浏览器不触发验证码，但 Playwright 触发？

### 核心原因：浏览器指纹检测

虎嗅的反爬系统能够区分"真实人类浏览器"和"自动化工具"。

---

## 检测维度对比

### 1. WebDriver 标志
```javascript
// Playwright 默认会有这个标志
navigator.webdriver  // true

// 真实浏览器
navigator.webdriver  // undefined
```

**已修复**：代码中已添加隐藏脚本
```javascript
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined
});
```

---

### 2. 浏览器插件
```javascript
// 真实浏览器（有插件）
navigator.plugins.length  // 3-5个

// Playwright 默认（无插件）
navigator.plugins.length  // 0
```

**已修复**：代码中已模拟插件列表

---

### 3. 用户行为模式（最难模拟）

| 行为 | 真实用户 | Playwright |
|------|----------|------------|
| 鼠标移动 | 曲线、随机速度 | 直线、匀速 |
| 点击位置 | 随机偏差 | 精确中心 |
| 页面停留 | 数秒到数分钟 | 固定的几秒 |
| 滚动行为 | 断断续续 | 一次性到底 |

**当前状态**：代码直接访问URL，缺乏人类行为模拟

---

### 4. 会话/Cookie 历史

你的真实浏览器：
- 可能有之前的虎嗅访问记录
- 可能有登录状态
- 有正常的浏览历史

Playwright：
- 全新会话
- 无历史记录
- 无登录状态

---

### 5. IP 信誉度

虎嗅可能维护了一个"可疑IP列表"：
- 数据中心IP（阿里云、腾讯云等）→ 高风险
- 住宅IP → 低风险
- 已知爬虫IP段 → 直接拦截

---

### 6. 高级指纹技术

#### Canvas 指纹
```javascript
// 通过 Canvas 渲染测试检测
const canvas = document.createElement('canvas');
const ctx = canvas.getContext('2d');
ctx.fillText('Test', 50, 50);
const fingerprint = canvas.toDataURL();
```
不同浏览器/显卡渲染结果有细微差异。

#### WebGL 指纹
```javascript
const gl = canvas.getContext('webgl');
const debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
const vendor = gl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL);
```

#### 字体检测
```javascript
// 检测系统字体列表
```

#### 时区/语言一致性
```javascript
// 检测设置是否自然
Intl.DateTimeFormat().resolvedOptions().timeZone
navigator.language
navigator.languages
```

---

## 如何验证具体检测点

### 测试1：检查你的浏览器指纹

在真实浏览器中打开开发者工具(F12)，输入：

```javascript
// 检查 webdriver
console.log('webdriver:', navigator.webdriver);

// 检查插件
console.log('plugins:', navigator.plugins.length);

// 检查语言
console.log('language:', navigator.language);
console.log('languages:', navigator.languages);

// 检查 Chrome 对象
console.log('chrome:', typeof window.chrome);
```

### 测试2：对比 Playwright

创建一个测试脚本：

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)  # 非无头模式便于观察
    page = browser.new_page()
    
    # 访问测试指纹的网站
    page.goto('https://bot.sannysoft.com/')
    input("按回车继续...")  # 保持窗口打开查看结果
    browser.close()
```

这个网站会显示你的浏览器被检测出的自动化特征。

---

## 可能的解决方案

### 方案1：使用你的真实 Cookies

1. 在真实浏览器中登录虎嗅
2. 导出 Cookies（使用 Chrome 插件如 "EditThisCookie"）
3. 在代码中加载这些 Cookies

```python
import json

# 从文件加载导出的 cookies
with open('huxiu_cookies.json') as f:
    cookies = json.load(f)

context.add_cookies(cookies)
```

### 方案2：使用已登录的浏览器配置文件

```python
# 使用你的 Chrome 用户数据目录
browser = p.chromium.launch_persistent_context(
    user_data_dir="C:/Users/你的用户名/AppData/Local/Google/Chrome/User Data",
    headless=False
)
```

**注意**：这会使用你的真实浏览器配置，包括登录状态。

### 方案3：慢速模拟人类行为

```python
# 添加随机延迟和鼠标移动
import random

page.goto('https://www.huxiu.com')
time.sleep(random.uniform(2, 5))  # 随机等待

# 模拟鼠标移动（贝塞尔曲线）
page.mouse.move(x, y, steps=10)

# 滚动页面（分段滚动）
for i in range(5):
    page.mouse.wheel(0, random.randint(300, 500))
    time.sleep(random.uniform(0.5, 1.5))
```

### 方案4：使用 Puppeteer-Extra-Stealth

Puppeteer 有更成熟的 stealth 插件：
```javascript
const puppeteer = require('puppeteer-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
puppeteer.use(StealthPlugin());
```

对应 Python 版本：
```bash
pip install playwright-stealth
```

```python
from playwright_stealth import stealth_sync

page = context.new_page()
stealth_sync(page)  # 应用 stealth 配置
```

---

## 建议的下一步

1. **先用你的 Cookies 测试**（最简单有效）
2. **使用 bot.sannysoft.com 检测** Playwright 的指纹特征
3. **尝试 playwright-stealth 库**（更完善的反检测）
4. **考虑使用付费代理**（如果以上都不奏效）

你想尝试哪个方案？我可以帮你实现。
