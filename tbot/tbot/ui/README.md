# tbot UI 目录

前端页面目录，提供市场研究仪表盘的 5 个标签页。纯静态 HTML + JavaScript，通过 `fetch` 访问后端 API 或加载静态 JSON 数据文件。

## 目录结构

```
ui/
├── index.html           # 入口页面，注册所有标签页和模块加载器
├── static/
│   ├── shared.js        # 共享工具库（颜色、格式化、数据加载、Tab路由）
│   └── style.css        # 暗色主题样式表
└── pages/
    ├── overview.js      # 概览条（全局 KPI 卡片 + 迷你流入时序图）
    ├── industry.js      # [行业资金流] 和 [行业时序] 两个标签页
    ├── limitup.js       # 涨停池标签页
    └── simulator.js     # 模拟盘标签页
```

## 页面结构（5 个标签页）

| Tab | Hash | 页面文件 | 内容 |
|-----|------|----------|------|
| 行业资金流 | `#industry` | `industry.js` | 行业 × 日期热力图、最新截面排名、IC 时序、五分组超额收益 |
| 涨停池 | `#limitup` | `limitup.js` | 日期导航 + 涨停概况/梯队表、概念聚合力导向图、连板高度时序 |
| 行业时序 | `#fundflow` | `industry.js`（复用） | 行业资金流时序图、异常行业 Z-score 检测 |
| 模拟盘 | `#simulator` | `simulator.js` | 策略列表（卡片）、运行/结果展示（权益曲线、持仓、交易记录） |
| 更新管理 | `#updater` | 尚未实现 | 数据更新管理面板（已注册但无页面文件） |

### 特殊说明：`#fundflow` 与 `#industry` 共享

`#fundflow` 没有独立页面文件，而是复用 `industry.js`。当用户切换到 `#fundflow` 时，模块加载器（`index.html` 中的 `__onTabActivate`）会加载 `industry.js`，然后滚动到页面底部的行业时序控件和图表区域，而非重新渲染。行业时序的 UI（下拉选择框、异常检测横幅）由 `industry.js` 的 `buildHTML()` 追加到 `#industry` section 的末尾，并用 `<hr>` 分隔。

## 各页面文件详解

### overview.js — 概览条

- **加载时机**：页面启动时立即执行，不依赖 hash 路由
- **入口**：`window.initOverview('overview-bar')`
- **数据依赖**：
  - `data/limitup_main.json` → `.overview.kpi`（KPI 卡片）
  - `data/industry.json` → `.series.market_flow_mini`（迷你流入时序图，近 20 日）
- **渲染**：KPI 卡片（净流入/涨停数等）+ ECharts 折线图（净流入/主力净流入双线）

### industry.js — 行业资金流 + 行业时序

- **入口**：`window.initIndustry(containerId)`
- **数据依赖**：
  - `data/industry.json` → 热力图矩阵、排名、IC、五分组
  - `GET /api/industry/{ts_code}/timeseries?mode=raw|share|zscore` → 单行业时序数据
  - `GET /api/industry/anomalies?threshold=2.0&limit=10` → 异常行业 Z-score 列表
- **功能**：
  - 行业 × 日期 `md_share` 热力图（ECharts heatmap）
  - 最新交易日截面排名（横向条形图）
  - 前瞻 IC 时序（k=1/k=3 双线）
  - 五分组前瞻超额收益（分组柱状图）
  - 行业资金流时序图（可选原始净额/归一化/share/Z-score，含行业指数叠加）
  - 异常行业检测横幅（交互式阈值切换 1.5/2.0/2.5/3.0）
- **特点**：内部使用 `buildHTML()` 动态注入 DOM，而不是依赖预先写好的 HTML 结构

### limitup.js — 涨停池

- **入口**：`window.initLimitup(containerId)`
- **数据依赖**：
  - `data/limitup_main.json` → `.dates`/`.by_date` 涨停主数据、`.overview`
  - `data/calendar.json` → 交易日历（日期选择器）
  - `data/limitup/{YYYYMMDD}.json` → 具体某日的涨停明细（按需加载）
  - `data/concept_graph.json` → 概念力导向图数据
  - `POST /api/concept/generate` → AI 生成概念图（按钮触发）
- **功能**：
  - 日期选择器（自定义弹出日历，标记交易日）
  - 涨停概况：KPI 行 + 梯队表格（连板/数量/股票/行业/封板时间/封单）
  - 概念力导向图（ECharts graph, force layout, 支持主题层级）
  - "生成概念图" 按钮调用后端 AI 接口
  - 连板高度时序图（全区间折线）

### simulator.js — 模拟盘

- **入口**：`window.initSimulator(containerId)`
- **数据依赖**：
  - `POST /api/sim/discover` → 触发后端扫描策略目录
  - `GET /api/sim/strategies` → 策略列表
  - `GET /api/sim/strategies/{id}` → 单策略详情
  - `POST /api/sim/strategies/{id}/run` → 运行回测
  - `GET /api/sim/strategies/{id}/equity` → 权益曲线数据
  - `GET /api/sim/strategies/{id}/positions` → 持仓
  - `GET /api/sim/strategies/{id}/trades` → 交易记录
- **功能**：
  - 策略卡片列表（显示名称、作者、参数配置入口、最近运行状态）
  - 运行策略（通过设置参数）
  - 结果展示：权益曲线图、持仓表、交易记录表
- **特点**：支持多实例，每个 container 有独立状态（`instances` 对象）

## 共享工具库（static/shared.js）

`window.__shared` 暴露以下工具函数：

| 函数 | 说明 |
|------|------|
| `cssVar(name)` | 读取 CSS 自定义属性值 |
| `parseHex(h)` | 十六进制颜色 → RGB 元组 |
| `interpColor(hexA, hexB, t)` | 线性插值颜色 |
| `divColor(v)` | 分歧色映射（蓝-灰-红），v ∈ [-1, 1] |
| `fmtDate(d)` | `YYYYMMDD` → `YYYY-MM-DD` |
| `loadJson(path)` | 带缓存破坏的 JSON fetch |
| `fmtMoney(v)` | 金额格式化（亿/万） |
| `signClass(v)` | 符号 CSS 类名（`up`/`down`/`''`） |
| `activateTab(hash)` | Tab 激活/切换 |
| `resizeCharts()` | 统一缩放所有 ECharts 实例 |
| `knownChartKeys()` | 已知的 ECharts 实例 key 列表 |
| `getWsUrl(path)` | 构建 WebSocket URL |

此外，`shared.js` 自动设置：

- **Hash 路由**：监听 `hashchange` 事件，初始无 hash 时默认跳转到 `#industry`；每次切换调用 `__onTabActivate` 钩子
- **全局 resize**：`window.resize` 时统一触发 `resizeCharts()`

### 模块加载机制（index.html）

`index.html` 的 inline `<script>` 定义模块注册表：

```js
var REGISTRY = {
  industry: { script: 'industry', initFn: 'initIndustry', container: 'industry' },
  limitup:  { script: 'limitup',  initFn: 'initLimitup',  container: 'limitup'  },
  fundflow: { script: 'industry', initFn: 'initIndustry', container: 'industry' },
  simulator:{ script: 'simulator',initFn: 'initSimulator',container: 'simulator' },
  updater:  { script: 'updater',  initFn: 'initUpdater',  container: 'updater'  },
};
```

每个 Tab 首次激活时动态加载对应的 `pages/{name}.js`，调用 `window[initFn](containerId)` 初始化，避免一次性加载所有脚本。

## 如何添加新标签页

1. **在 `index.html` 中添加 Tab DOM**：
   - 在 `<aside id="tab-sidebar">` 中添加一个 `<a class="tab-link">`，`href="#newtab"`
   - 在 `<main id="main-content">` 中添加 `<section id="newtab" class="tab-content"></section>`

2. **在 `static/style.css` 中添加样式**（可选）

3. **创建 `pages/newtab.js`**：
   - 自执行函数模式，暴露 `window.initNewTab(containerId)`
   - 可通过 `window.__shared` 访问共享工具函数
   - 如需 ECharts，依赖全局 `echarts`

4. **在 `index.html` 的 REGISTRY 注册**：
   ```js
   newtab: { script: 'newtab', initFn: 'initNewTab', container: 'newtab' }
   ```

5. **如需后端 API**：在 tbot 后端应用中添加对应的 API 路由，页面通过 `fetch` 调用
