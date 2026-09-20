# FinanceRanker

免费公开数据源驱动的**科技股同行排名服务**。输入一组股票代码，自动抓取行情与财报、
计算 40+ 项指标、做 1–10 分位打分排名，并**一键导出 CSV / Excel**。

后端基于 FastAPI，爬取层以 [akshare](https://akshare.akfamily.xyz/) 生态的公开数据源为主，
财报以 SEC XBRL 官方数据为准，输出格式兼容你原有的 `Free_Public_Data_Tech_Ranker.xlsx`。

---

## 1. 关于雅虎财经（重要）

你最初要求用雅虎财经。实测结论：**当前网络无法访问雅虎财经**。

| 端点 | 结果 |
|---|---|
| `query1/2.finance.yahoo.com/v8/finance/chart` | `429 / 403` |
| `/v7/finance/quote` | `403` |
| `/v10/finance/quoteSummary` | `403` |
| `/ws/fundamentals-timeseries` | `403` |

返回体是雅虎的“不再服务中国大陆”提示页。已加 `curl_cffi` 的 Chrome TLS 指纹伪装、
`fc.yahoo.com` cookie 预热、双主机重试，仍被拦截——说明是**出口 IP / 数据中心 IP 级别**的限制，
不是请求写法问题。

因此本服务把数据源抽象成 provider，**默认走实测可用的免费源**，雅虎作为可选适配器保留：

| 用途 | 主源 | 备源 | 说明 |
|---|---|---|---|
| 年度财报 | **SEC XBRL `companyfacts`** | akshare（东方财富财报） | 官方权威，数值与原工作簿**完全一致** |
| 日线行情 | **新浪财经 US 日 K** | akshare/东财、雅虎 | 回溯到上市首日，含复权 |
| 实时报价 | **腾讯 `qt.gtimg.cn`** | 东财、雅虎 | 一次返回价格/市值/股本/PE 等 73 字段 |

> 想启用雅虎：部署到海外主机或配好代理，然后设 `FR_ENABLE_YAHOO=true`。
> 代码无需改动，会自动作为候选源参与容错。

---

## 2. 快速开始

```bash
cd FinanceRanker

# 0) 只想直接用桌面版？见 §7.55（Windows 安装包）与 §7.5（macOS .app）

# 1) 建虚拟环境（需 Python 3.10+，推荐 3.12）
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2) 启动服务
./run.sh
#    -> http://127.0.0.1:8848
```

打开网页后有两个工作区：

- **排名**（`/`）：输入股票代码 → 抓取并打分 → 看排名表 → 一键导出 CSV / Excel。
- **数据预览**（`/preview.html`）：选一只**美股**，用 akshare 实时取数，
  逐项展示工作簿里每个参数**怎么算出来的**——对应哪一列、用什么公式、取自哪个接口。
  只读，不写快照。

### 命令行

```bash
# 抓取并同时导出两种格式
PYTHONPATH=. .venv/bin/python -m app.cli refresh MSFT AAPL NVDA \
    --csv data/exports/ranked.csv --xlsx data/exports/ranked.xlsx

# 用默认股票池
PYTHONPATH=. .venv/bin/python -m app.cli refresh --default --xlsx report.xlsx

# 查看历史快照
PYTHONPATH=. .venv/bin/python -m app.cli runs
PYTHONPATH=. .venv/bin/python -m app.cli show
```

---

## 3. 配置

复制 `.env.example` 为 `.env` 按需修改，常用项：

```ini
FR_PROXY=                 # 空=跟随系统代理；"direct"=绕过代理；或填 http://127.0.0.1:7897
FR_MAX_CONCURRENCY=6      # 并发股票数
FR_SCHEDULE_HOURS=6       # 每 6 小时自动刷新（定时+历史快照）
FR_SCHEDULE_CRON=0 22 * * 1-5   # 或工作日 22:00（本地时区）
FR_ENABLE_YAHOO=false     # 旧开关；设 true 则强制启用雅虎
FR_YAHOO_MODE=auto        # auto=探测通就用 / on / off
FR_ADS_RATIOS=TSM=5       # 每份 ADS 对应多少普通股（见 §8.16），可追加其它 ADR
```

> ⚠️ **macOS 系统代理坑**：如果系统里配了代理但没启动，`requests` 会读到它并抛
> `ProxyError`（akshare 内部自建 Session 尤其明显）。此时设 `FR_PROXY=direct`。

---

## 4. API

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/refresh` | 启动抓取任务，body: `{"tickers":["MSFT","AAPL"]}` |
| `GET` | `/api/jobs/{id}/stream` | **SSE 实时进度** |
| `GET` | `/api/jobs/{id}` | 任务状态（轮询兜底） |
| `GET` | `/api/ranking` | 排名结果（可带 `job_id` / `run_id` / `eligible_only`） |
| `GET` | `/api/ticker/{ticker}` | 单只明细 |
| `GET` | `/api/export/csv` | **一键下载 CSV**（按 Identity / Market / Growth / … / Scoring 分节） |
| `GET` | `/api/export/detail?ticker=MSFT` | **单只标的明细 CSV**（Meta / 维度得分 / 逐项同行中位与名次 / 计算输入） |
| `GET` | `/api/export/xlsx` | 下载 Excel（8 个 sheet，兼容原模板） |
| `POST` | `/api/export/both` | 同时落盘两种格式 |
| `GET` | `/api/runs` `/api/runs/{id}` | 历史快照列表 / 详情 |
| `GET` | `/api/history/{ticker}` | 单只股票的历史快照序列 |
| `GET` | `/api/health` | 健康检查、数据源、探测结果与缓存状态 |
| `GET` | `/api/providers` | 各数据源在本机的可达性（读缓存） |
| `POST` | `/api/providers/probe` | 强制重新探测数据源 |
| `GET` | `/api/us/tickers` | 美股股票池搜索（`?q=NV&limit=40`） |
| `GET` | `/api/us/params` | 参数目录：能算哪些参数、各自公式 |
| `GET` | `/api/us/preview/{ticker}` | **预览**：该美股的全部参数与原始报表 |
| `GET` | `/api/sectors` | 六大板块及各自可解析的候选数 |
| `GET` | `/api/sectors/resolve/{ticker}` | 该股的 SEC SIC 码与所属板块 |
| `GET` | `/api/sectors/{sector}/ranking` | **板块内前十**（`?focus=NVDA` 高亮该股） |
| `POST` | `/api/sectors/build` | 批量预热板块索引（可选，较慢） |
| `GET` | `/docs` | OpenAPI 文档 |

---

## 4.5 数据预览页（仅美股）

`/preview.html` 用 akshare 直接把一只美股拆开给你看，回答“这些参数到底怎么来的”。

**页面结构**

1. **选股票** —— 输入代码或公司名搜索（如 `NVDA`、`nvid`、`google`、`tsmc`），
   也可点常用代码。股票池来自 **SEC 官方名录**（10,439 只美股，自带公司名与 CIK）。
   搜索支持品牌别名（`google` → GOOGL、`tsmc` → TSM、`facebook` → META）。
2. **参数计算明细** —— 按 8 个分类列出 **51 个参数**，每行给出：
   参数名 · **Excel 列**（如 `Data Cache!L`）· 值 · **算自**（公式）· 状态 · 备注。
   状态分三种：`已算出` / `该源无此项` / `手工录入`（akshare 不提供，需在原表手工填）。
3. **akshare 额外可得** —— **14 项**原工作簿没有的指标（ROA、流动比率、速动比率、
   存货周转率、应收周转率、总资产周转率、净利润同比…），可作为补充视角。
4. **原始报表** —— 损益表 / 现金流量表 / 资产负债表的原值（未加工），用于核对口径。
5. **akshare 接口可用性** —— 明确列出哪些接口可用、哪些不可用及原因。

**实测可用的 akshare 美股接口（本服务实际使用）**

| 接口 | 状态 | 用途 |
|---|---|---|
| `stock_us_daily` | ✅ | 日线行情，新浪财经，含完整历史、支持前复权 |
| `stock_financial_us_report_em` | ✅ | 三大报表，东方财富（年度） |
| `stock_financial_us_analysis_indicator_em` | ✅ | 49 项现成比率，可与自算值交叉验证 |
| `stock_us_spot_em` | ❌ | 东财 push2 域名不通，全市场行情拉不到 |
| `stock_us_hist` / `stock_us_hist_min_em` | ❌ | 同样走 push2，已用新浪日线替代 |
| `stock_individual_basic_info_us_xq` | ❌ | 雪球需要 `xq_a_token` 登录态 |
| `stock_us_valuation_baidu` | ❌ | 返回非 JSON 内容 |

**口径差异（与「排名」页对照时请注意）**

- **股本**：akshare 美股接口**不提供股本**，预览页用「净利润 ÷ 摊薄EPS」反推，
  因此市值与估值倍数与「排名」页（用 SEC DEI 股本）会有细微差异。
- **折旧与摊销**：akshare 把 D&A 合并为一行、不单列无形资产摊销，
  所以「摊销调整/股」在预览页**为空**（原表是从单独的 XBRL 标签取的）。
- **NTM P/E**：用 FY0→FY1 的 EPS 增速外推，**不是**分析师一致预期。
- **非GAAP EPS / 分析师目标价**：akshare 不提供，页面明确标为「手工录入」。

## 4.6 抓取任务的两种模式

「抓取任务」页顶部有模式切换，**原来的用法完整保留**：

| 模式 | 用法 | 说明 |
|---|---|---|
| **自定义股票池**（默认） | 手填代码 → 抓取 → 打分 | 原有行为，未做改动。快捷预设、去重、SSE 进度、产物导出照旧 |
| **板块排名** | 选板块，或输入一只股票定位 | 按其 **SEC SIC 行业分类**归入板块，与其他同板块公司一起打分，取前十名 |

板块为六大模块：科技类 / 金融类 / 医药食品类 / 媒体类 / 汽车能源类 / 制造零售类。

**分类来源是 SEC 官方 SIC 码**（`data.sec.gov/submissions/CIK##########.json`），
不是第三方行业标签——官方、免费、无需 key，而且每个 SEC 注册主体都有。
`company_tickers.json`（股票池用的那份）**不含** SIC，所以要按 CIK 逐个取；
取到的结果会永久缓存，并可用 `POST /api/sectors/build` 批量预热。

**SIC → 模块**的映射分两层，因为 SIC 自己的门类对我们的六个模块太粗：

- 先查**精确区间覆盖**：汽车整车 3711 在 SIC 里属于制造业，但归入**汽车能源类**；
  制药 2834 属于化工，归入**医药食品类**；半导体 3674、软件 7372 归入**科技类**。
- 再落到**两大位门类默认**，保证 100–9999 内每个码都有归属。

每个模块还带一份**精选种子**（各 22–39 只知名公司），所以冷缓存下也能立刻出结果；
每次查询都会把结果并入索引，索引随使用增长。

> 排名口径：分位打分只在**同一组内**才有意义，所以板块模式是一次性抓取整池
> （默认 40 只）后**统一打分**，而不是逐只取分再排。

## 5. 指标与打分

**指标**（对齐原 `Data Cache` L..AT）：营收及其同比/5年CAGR、毛利率、营业利润率、净利率、
ROE、ROIC、自由现金流及其利润率/收益率、现金转化、资产负债率、GAAP EPS、
非GAAP 调整桥（SBC/重组/摊销 税后每股）、调整后 EPS、Forward P/E、P/S、EV/EBITDA、
P/B、P/FCF、1年回报、52周回撤、Beta。

**打分**（对齐 `Scoring` sheet）：每项指标在股票池内做 1–10 分位打分，按 5 个维度
（成长/盈利/现金/估值/市场）平均，再按权重 `25/25/20/20/10` 合成总分。
覆盖度不足（<12/21 项或维度缺失）则标记为不可排名。

**Beta** 用与 SPY 对齐的日收益率协方差/方差计算，最少 30 个观测。

---

## 6. 项目结构

```
app/
├── main.py                 FastAPI 入口：refresh / SSE / ranking / export
├── cli.py                  命令行入口
├── pipeline.py             编排：抓取 → 计算 → 打分
├── jobs.py                 后台任务管理 + SSE 事件缓冲
├── scheduler.py            APScheduler 定时刷新
├── store.py                SQLite 历史快照
├── cache.py                本地 TTL 缓存
├── http.py                 统一 HTTP：重试/退避/缓存/TLS 伪装
├── models.py               领域模型
├── preview.py             预览页：参数目录 + 从 akshare 推导每个参数
├── preview_models.py      预览页数据模型
├── universe.py            美股股票池（SEC 名录 + 品牌别名 + 搜索排序）
├── sectors.py             板块分类（SEC SIC → 六大模块）+ 板块内排名
├── providers/
│   ├── fundamentals.py     SEC XBRL（US-GAAP + IFRS）→ akshare 兜底
│   ├── akshare_us.py       akshare 美股接口封装（日线 / 三大报表 / 分析指标）
│   ├── prices.py           新浪 → akshare/东财 → 雅虎
│   └── quotes.py           腾讯 → 东财 → 雅虎
├── engine/
│   ├── metrics.py          指标引擎（对应 Excel 公式）
│   ├── market.py           收益率 / Beta / 回撤
│   └── scoring.py          分位打分与加权合成
├── exporters/
│   ├── csv_export.py       全池 CSV（按维度分节，84 列）
│   ├── detail_export.py    单只明细 CSV（对齐 Stock Detail sheet）
│   └── excel_export.py     Excel（8 sheet，兼容原模板）
└── web/static/             前端（原生 HTML/CSS/JS，无构建步骤、无第三方依赖）
    ├── index.html          总览：数据源、快照概览、分维度矩阵
    ├── refresh.html        抓取任务：股票池编辑 + SSE 实时进度
    ├── ranking.html        排名结果：筛选、排序、列控制、一键导出
    ├── company.html        公司明细：五个加权维度 + 逐项同行对比 + 计算输入
    ├── history.html        历史快照：时间轴、分数走势、快照对比
    ├── sources.html        数据源：provider 链路、缓存、指标口径、API 速查
    ├── preview.html        参数预览：逐个指标的数据来源与公式（辅助页）
    ├── css/shell.css       设计系统：暖色色板、排版、侧边导航、玻璃面板
    ├── css/components.css  页面组件：记分板、排名表、时间轴、指标词典
    ├── js/scale.js        分数配色单一真源（红↔绿发散色阶）
    └── js/                 shell（导航+API+格式化）、drawer（标的明细）、各页脚本

desktop.py                 桌面入口：起本地服务 + 窗口层（macOS 系统 webview / Windows 内嵌 Chromium）
finance_ranker.spec        PyInstaller 配置（含 py_mini_racer 原生库与静态资源、应用图标）
tools/make_app_icons.py    由 favicon.svg 生成 assets/ 下的 .ico / .icns（纯 numpy，无图像库依赖）
assets/                    生成好的应用图标（.ico / .icns / .png），随仓库提交
windows/installer.iss      Inno Setup 安装包脚本（中文向导 + 应用图标）
windows/build.ps1          Windows 本地一键构建（测试 → 打包 → 冒烟 → 安装包）
windows/stage_chromium.ps1 下载官方 Chromium 快照并解压到 vendor/chromium（内嵌浏览器）
.github/workflows/build.yml  CI：在 Windows / macOS 上各自出包
tests/                     离线回归测试（引擎 / 数据源 / 板块 / 健康检查 / 页面渲染）
```

---

## 6.5 分维度矩阵的配色口径

矩阵（成长/盈利/现金/估值/市场/总分）采用**红绿发散色阶**：

- **5.5 为中性轴**，`红 = 弱`，`绿 = 强`，**颜色越深表示越偏离中性**。
- 色阶实现是**单一真源** `app/web/static/js/scale.js`：总览页色块用 `FRScale.swatch`，
  排名/历史/抽屉的 meter 条用 `FRScale.fill`；页面上的 0–10 图例也由同一函数生成，
  所以图例与被解释的格子不可能不一致。
- **数字始终保留**，颜色只是扫视引导。所有有分格子在真实浏览器里实测
  对比度 ≥ 4.88:1，满足 WCAG AA——红绿色盲用户靠数字读数。
- 缺数据的格子仍渲染成 `—`（`.swatch.na`），不参与色阶。

> 取舍说明：维度分位本身是 1–10 的**单调**量，单色深浅就足以表达；
> 采用红绿发散色阶是为了贴合美股「红弱绿强」的直觉。
> 代价是颜色与深浅存在部分信息冗余，这是有意接受的。

不变量由 `tests/test_scale.py` 守着（色阶单调性、中性轴对称、AA 对比度、
meter 条必须拉伸、不允许任何页面再引入私有色表）：

```bash
PYTHONPATH=. python tests/test_scale.py
```

## 7. 前端

原生 HTML / CSS / ES2020，**没有构建步骤**：改完刷新即可，`app/web/static/` 就是产物。
多子页结构由左侧玻璃导航栏串联，窄屏折叠为抽屉，每页都自带加载骨架、空状态与错误态。
**中英双语可切换**（侧栏底部 / 移动端顶栏），见 §7.3。

| 页面 | 路径 | 能做什么 |
|---|---|---|
| 总览 | `/index.html` | 池内统计、前三名分维度条、全员分维度矩阵、最近运行柱状条 |
| 抓取任务 | `/refresh.html` | 编辑股票池（快捷预设 / 标签删除 / 去重提示）、SSE 实时进度与事件日志、任务列表、产物导出 |
| 排名结果 | `/ranking.html` | 六种维度视图、显示列控制、表头排序、搜索与画像筛选、CSV / Excel / 复制表格 |
| 公司明细 | `/company.html?ticker=NVDA` | 五维加权得分轨、逐维度指标表（本股 / 同行中位 / 差值 / 池内名次 / 分位条 / 得分）、计算输入、单只明细 CSV |
| 历史快照 | `/history.html` | 运行时间轴、单只标的分数走势、跨快照对比（Δ 列）、快照级导出与删除 |
| 数据源 | `/sources.html` | provider 容错链状态、缓存占用、指标口径词典、API 速查、清空缓存 |

设计约束（写在 CSS 里，不靠口头约定）：

- **浅暖色玻璃**：暖米色底 + 半透明玻璃面板（`backdrop-filter` + 1px 内高光边），
  阴影统一带暖色偏移；**没有纯黑、没有霓虹外发光、没有紫蓝渐变**。
- **单一强调色**：陶土红 `#bd5c3c`；界面色只用在强调与状态上。
- **分数色阶是红↔绿发散**：5.5 为中性轴，红=弱、绿=强，越深越偏离中性；
  由 `js/scale.js` 单一真源提供，总览色块与排名/历史/抽屉的 meter 条共用，
  实测所有有分格子对比度 ≥ 4.88:1（WCAG AA）。详见 §6.5。
- **极简密度**：能用间距和 1px 分隔线表达层级的地方不放卡片；
  数值一律等宽字体 + `tabular-nums`，表格列宽固定不抖动。
- **交互完整**：按钮有 hover 抬起与 `:active` 按压反馈；导出条是实体玻璃底座，
  不会让滚动中的表格行透出来。
- **零外部资源**：不引用 CDN、字体或图床，图标全部是内联 SVG。
- **无障碍与性能**：尊重 `prefers-reduced-motion`；背景光斑是 `pointer-events:none`
  的固定层且只动 `transform`；页面用 `overflow-x: clip` 收口，移动端不会横向滚动。

本地校对页面（开发辅助脚本，不属于服务运行时）：

```bash
.venv/bin/pip install playwright          # 仅开发用
.venv/bin/python tests/ui_screenshot.py   # 逐页全页截图到 /tmp/frshots 并回报控制台错误
```

### 7.3 中英双语

侧栏底部（移动端顶栏）的「中文 / English」开关即时切换，选择存 `localStorage['fr.lang']`，
首次访问按浏览器语言判断。切换后所有静态文案、动态表格、数字格式、日期与
`<title>` 一起更新，`document.documentElement.lang` 同步改成 `zh-CN` / `en`。

实现约定：

- `js/i18n.js` 是唯一的文案表（`DICT[key] = [中文, English]`），导出
  `FRI18n.t(key, vars)` / `both()` / `set()`，并挂一个全局 `t()` 方便各页调用。
- 静态标记用 `data-i18n` / `data-i18n-title` / `data-i18n-ph` / `data-i18n-aria`，
  `FRI18n.applyI18n(root)` 可在动态渲染后局部重刷。
- 动态表格不重刷 DOM 结构，而是在 `fr:lang` 事件里重新渲染自己那一块数据。
- **数字随语言变**：`FR.fmt()` 走 `toLocaleString(locale)`，英文下 `13,310`、中文下 `13310`。
- **指标名共用一套 key**：`m.gross_margin` 等 key 同时供排名表、公司明细、数据源词典、
  单只明细 CSV 与复制功能使用，改文案只改一处。
- 双语文案不重复定义：公司明细的指标名主行取当前语言，副行取另一种语言。

数据预览页（§4.5）的目录文案由后端提供，所以 `/api/us/params` 与
`/api/us/preview/{ticker}` 都接受 `?lang=zh|en`，切换语言时该页会重新取数一次
（公式、备注、来源说明都在后端目录里）。

### 7.1 单只标的明细页

点排名表里的代码（或「展开」）会离开列表，进入 `company.html`，页面结构对齐工作簿的
`Stock Detail` sheet，而不是把一长串字段塞进侧边抽屉：

| 区块 | 内容 |
|---|---|
| 头部 | 代码 / 公司 / 画像 / 排名 / 覆盖度 + 总分，五维得分横排 |
| 得分轨 | 五维得分 + 总分，各自带权重（25/25/20/20/10） |
| 维度块 ×5 | 每个维度一张表：指标、本股、同行中位、差值（按好坏着色）、池内名次、得分条、1–10 得分 |
| 计算输入 | 规模与资本 / 利润与现金流 / 每股与调整桥三组原始科目 |
| 来源 | 财报源、行情源、非 GAAP 源、股本口径、抓取时间 |

同行中位数与池内名次都在**当前股票池**上实时计算（优先使用可排名标的），
与打分口径一致；窄屏会收敛为「指标 / 本股（含名次）/ 得分」三列，不做横向滚动。

### 7.2 导出 CSV 的结构

两种粒度，都按维度分节，节标题写成 `## 节名` 行，既能直接读也能被表格软件切开。

```text
全池（/api/export/csv）—— 文件自身也用 `## 节名` 行分节：

    ## FinanceRanker · 13 tickers · 2026-09-19 14:45
    ## Identity / ## Market / ## Growth / ## Profitability
    ## Cash quality / ## Per-share bridge / ## Valuation
    ## Filing inputs / ## Scoring / ## Provenance
    Identity · Ticker, Identity · Company, …, Scoring · Score overall, …

单只（/api/export/detail?ticker=NVDA）：

    ## Meta
    Ticker,NVDA
    Overall score (1-10),7.14
    ## Dimension scores
    Dimension,Score (1-10),Weight group
    Growth,8.84,Growth
    ## Metrics
    Metric,Stock,Peer median,Within-group rank,Score,Better direction,Weight group
    Revenue growth YoY,65.5%,17.6%,1,10.00,Higher,Growth
    ## Calculation inputs
```

---

## 7.5 打包成桌面 App

可以把整个服务打成一个**独立桌面应用**，不需要用户装 Python。

```bash
.venv/bin/pip install pyinstaller pywebview
PYINSTALLER_CONFIG_DIR=/tmp/pyi_cache pyinstaller --clean --noconfirm finance_ranker.spec
open dist/FinanceRanker.app          # macOS；Windows/Linux 用 dist/FinanceRanker/ 里的可执行文件
```

**窗口层按平台分流，因为只有 Windows 有这个坑：**

| 平台 | 窗口 | 是否内嵌浏览器 |
|---|---|---|
| macOS | pywebview → 系统 WKWebView | 否，产物约 150 MB |
| Windows | **内嵌 Chromium**（官方 Chromium 连续快照 x64），以 `--app=` 打开 | 是，产物约 400 MB |

Windows 之所以不再走 pywebview：它的 WinForms 后端必须经 **pythonnet** 去调 .NET，
而打包成 exe 之后 pythonnet 加载不了 `Python.Runtime.dll`：

```
RuntimeError: Failed to resolve Python.Runtime.Loader.Initialize
             from ...\_internal\pythonnet\runtime\Python.Runtime.dll
```

这个故障**只在冻结包里出现**——源码运行、CI 的 `--no-window` 冒烟测试都完全正常，
所以第一版发了出去，用户双击才炸。与其去赌一套 .NET 互操作桥，不如自己带浏览器：
Windows 版现在不依赖 WebView2，也不依赖用户装过 Edge/Chrome。

代价是体积。相比之下，抓取全在 Python 侧（requests / curl_cffi），
整个 Python 运行时也才 60 MB——多出来的 300 多 MB 全是浏览器引擎。

**冻结后与源码运行的关键差异**（都已处理）：

| 问题 | 处理 |
|---|---|
| `data/` 在解包临时目录里，退出即丢 | 冻结时数据落到用户目录：macOS `~/Library/Application Support/FinanceRanker`、Windows `%LOCALAPPDATA%\FinanceRanker`、Linux `$XDG_DATA_HOME/FinanceRanker`，可用 `FR_DATA_DIR` 覆盖 |
| `app/web/static` 是**数据**不是代码 | spec 里 `--add-data` 打进包，`STATIC_DIR` 冻结时回退到 bundle 根 |
| `py_mini_racer` 的原生库 | 必须 `collect_dynamic_libs`，否则 akshare 取价全部报 "Native library or dependency not available" |
| 无控制台看不到日志 | 冻结时写 `launcher.log` 到数据目录 |
| 端口被占用 | 优先 8848，占用则退到随机空闲端口，窗口指向实际端口 |
| Windows 的窗口层 | 内嵌 Chromium 用 `--app=` 拉起；找不到或拉起失败则回退系统默认浏览器，**窗口层永远不会弄崩应用** |

**应用图标来自网页端那个 SVG，不是 PyInstaller 的默认图标。**
`app/web/static/favicon.svg`（也就是侧栏左上角那个品牌标记）是唯一的事实来源，
`tools/make_app_icons.py` 把它栅格化成两个平台容器：

```bash
python tools/make_app_icons.py       # 重新生成 assets/（改过 SVG 后跑一次并提交）
```

| 文件 | 用途 |
|---|---|
| `assets/finance_ranker.ico` | Windows exe（16/24/32/48/64/128/256 七档，任务栏/Alt-Tab/资源管理器/安装包） |
| `assets/finance_ranker.icns` | macOS `.app`（16–1024，PNG 压缩） |
| `assets/finance_ranker-256.png` | 备用位图 |

脚本不依赖任何图像库：`favicon.svg` 用到的构造（圆角矩形 + 线性渐变 + 折线描边 + 圆点）
用 numpy 直接解析并超采样渲染，几何写死在 SVG 里、图标跟着 SVG 走。
如果 SVG 以后用了这里不认识的构造，脚本会**报错退出**而不是悄悄产出一个旧图标。
spec 里 `EXE(icon=...)` / `BUNDLE(icon=...)` 指向它，文件缺失时会自动重新生成，
所以忘记提交 `assets/` 的干净检出也能打出正确图标。

调试用参数：

| 参数 | 作用 |
|---|---|
| `--no-window` | 只起服务，供无人值守验证（不碰窗口层） |
| `--selftest` | 校验窗口层真的在包里（Windows 上缺 Chromium 就以退出码 2 失败），构建时用它把关 |
| `--browser` | 强制用系统默认浏览器打开，跳过内嵌 Chromium |

## 7.55 Windows 安装包（.exe）

**可以，但有一条硬约束：PyInstaller 不能交叉编译。**
在 macOS 上产不出 Windows `.exe`，反之亦然——每个平台的产物必须在那个平台上构建。
所以有两条路，都已配好：

### 路线 A：GitHub Actions 自动出包（推荐，不需要 Windows 机器）

仓库已带 `.github/workflows/build.yml`：

```bash
git init && git add -A && git commit -m "FinanceRanker"
git remote add origin <你的仓库>
git push -u origin main
```

推上去后自动执行并在 **Actions → 对应 run → Artifacts** 下载：

| 产物 | 内容 |
|---|---|
| `FinanceRanker-setup` | **`FinanceRanker-0.1.0-setup.exe`** ← 双击安装的安装包 |
| `FinanceRanker-windows` | 免安装的绿色版目录（整个 `dist/FinanceRanker/`） |
| `FinanceRanker-macos` | macOS 的 `.app` |

**Artifacts 需要登录才能下载，而且 90 天就过期**，所以安装包另外走 Release 发布：

```bash
git tag v0.1.0 && git push origin v0.1.0
```

打 `v*` tag 后，`release` job 会把该 tag 那次构建出的安装包发布到
**Releases**（[github.com/dahuang3244/FinanceRanker/releases](https://github.com/dahuang3244/FinanceRanker/releases)），
得到不需要登录、不过期的公开直链。发布用的是**被 tag 的那个 commit** 构建的产物，
不会出现"源码和二进制对不上"。

工作流在打包**之前**会跑全部测试、并把官方 **Chromium 快照**（win64）下载到
`vendor/chromium/` 供打包内嵌；打包**之后**会先跑一次 `--selftest` 确认窗口层真的进了包，
再做冒烟测试（启动冻结的应用 → 等它报出端口 → 校验 `/api/health` 与首页 200），
所以"能下载"等于"能跑"。

内嵌浏览器**不能用 Chrome for Testing**：那是 Google 的**带品牌**便携版 Chrome，每次启动都会
挂一条 info bar（`IDS_CHROME_FOR_TESTING_INFOBAR`）：

> Chrome 测试版 v`153.0.8010.52` 仅适用于自动测试。若要进行常规浏览，请使用可自动更新的标准版 Chrome。

这是给最终用户看的"你用的浏览器不对"警告，而用户根本不该看到它。它**没有任何命令行开关能关掉**：
`chrome.dll` 里只有一条机器级企业策略（`SOFTWARE\Policies\Google\Chrome for Testing` →
`ChromeForTestingAllowed`），对便携应用来说既要求管理员又要求用户机器上写 HKLM。
改用**无品牌的 Chromium 快照**，这条提示在构建里根本不存在，不需要提权、不需要策略，
应用依然自包含。下载与校验见 `windows/stage_chromium.ps1`。

### 路线 B：在 Windows 上本地构建

```powershell
powershell -ExecutionPolicy Bypass -File windows\build.ps1
```

脚本会依次：校验 Python 是 64 位 → 装依赖 → 跑测试 → PyInstaller 打包 →
冒烟测试 → 若有 Inno Setup 则顺带编译安装包。缺 Inno Setup 时会明确提示：

```powershell
winget install -e --id JRSoftware.InnoSetup
```

### 安装包（Inno Setup）做了什么

`windows/installer.iss`：

- **中文优先**的安装向导（`ChineseSimplified.isl`），同时提供英文。
  该翻译文件随仓库放在 `windows/` 下，不再依赖编译器自带——
  Inno Setup 的 `Languages\` 目录并不保证包含它（CI 用的 chocolatey 6.7.1 就没有），
  而引用一个不存在的文件会让整个编译中止。脚本用预处理器做了三级兜底：
  仓库自带 → 编译器自带 → 仅英文。
- **默认按用户安装**（`PrivilegesRequired=lowest`），不弹 UAC；也可在对话框切到全机器安装
- 开始菜单 + 可选桌面快捷方式，卸载项齐全
- **安装时不联网**：应用自带 Chromium，所以既不需要 WebView2 运行时，
  也不再需要 Inno 的 `download` + `external` 在安装时去拉 bootstrapper
  （那套逻辑已随内嵌浏览器一起删掉）
- **卸载不删用户数据**：快照、缓存与浏览器 profile 留在 `%LOCALAPPDATA%\FinanceRanker`，重装不丢

### 为什么 Windows 内嵌 Chromium，而 macOS 不内嵌

不是"想不想"的问题，是 pywebview 在 Windows 上必须过 pythonnet 这道桥，
而这道桥在冻结包里是坏的（见 §7.5 的报错）。所以 Windows 的选项只有两个：
**自己带浏览器**，或者**赌用户机器上的 WebView2**。选了前者，代价是 ~300 MB。

macOS 的 WKWebView 走 Objective-C 桥，没有这层 .NET 互操作，一直很稳，就继续保持轻量。
若内嵌 Chromium 因故起不来（被杀软拦、被策略禁），
`FinanceRanker.exe --browser` 会用系统默认浏览器打开同一个本地服务作为兜底——
代码路径上它会**自动**兜底，不需要用户加参数。

## 7.6 数据源探测（"任何环境都能用"的真相）

先说结论：**无法保证任何环境下 akshare 都能用**，这是数据源本身的属性，不是代码问题。
实测证据（同一台机器、同一时刻，Python 与真实 Chrome 内核对比）：

| 端点 | Python | 真实 Chrome 内核 |
|---|---|---|
| 东财 push2（clist / kline） | 不通 | **ERR_EMPTY_RESPONSE**（同样不通） |
| 东财 datacenter-web（财报） | 200 | 200 |
| 雅虎 v8/chart | 200 | 200 |
| **SEC submissions** | **200** | **403** ← 浏览器反而被拒 |

两个结论：

1. **换浏览器内核解决不了连通性**。push2 是 TCP/HTTP 层直接空响应，
   与 JS 渲染、UA、TLS 指纹都无关；查代码假说被实测否定。
2. **浏览器甚至更差**：SEC 按 fair-access 要求带联系方式的 User-Agent，
   Chrome 的 UA 会吃 403。所以这里刻意用 requests/curl_cffi 而不是浏览器抓取。

代码能保证的是**环境自适应**：启动时并发探测各数据源（约 2.5 秒），
把探测结果缓存 1 小时，并据此决定容错链。当前机器的探测输出示例：

```
sec OK · sina OK · tencent OK · akshare OK · eastmoney 不通 · yahoo 不通
```

雅虎因此改成**三态**策略而非硬开关：

- `FR_YAHOO_MODE=auto`（默认）：探测通就用，不通就跳过——同代码在能连雅虎的地区自动变强
- `on` / `off`：强制启用 / 强制禁用
- `FR_ENABLE_YAHOO=true` 仍然可用，且优先级最高

真正要"任何环境都能用"，只有两条路：**可配置代理**（`FR_PROXY`）或
**部署到能连通这些源的主机**（见打包与 Docker）。探测的价值是把"为什么这次没数据"
从一句泛泛的报错，变成一张明确的可用性表。

API：`GET /api/providers`（读缓存）、`POST /api/providers/probe`（强制重测）。

## 8. 工程上踩到的坑（已修）

这些是实测发现并已修复的真实问题，记录备查：

1. **XBRL 标签迁移导致取到旧年份**。Alphabet 把 FY2025 营收标成 `Revenues`，而
   早期年份用 `RevenueFromContractWithCustomerExcludingAssessedTax`。
   按“第一个有数据的标签”取值会静默返回 **FY2024**；NVDA 更严重（26.9B vs 215.9B）。
   → 改为**跨标签按期间取最新**。
2. **资产负债表科目没有 `start`**，用利润表的“年度区间”规则过滤会把
   Assets / Equity 全部过滤掉。→ 拆成 flow / instant 两套抽取规则。
3. **外币申报主体**。TSM 报 IFRS 且以 **TWD** 申报，却是 USD ADR。
   直接算 `市值(USD)/营收(TWD)`、`价格(USD)/EPS(TWD)` 是错的
   （ADR 还有 1:5 比例）。→ 增加 `ifrs-full` 支持 + **跨币种守卫**：
   保留原始申报值，剔除所有价格类倍数并明确标注原因。
   **守卫是有意为之的取舍，不是缺陷**：没有引入汇率源之前不做换算，
   代价是该标的的估值维度为空、进不了排名（详见本节第 15 条）。
   要做完整的话需要两件事：TWD→USD 汇率，以及每份 ADR 对应的普通股数
   （东财同时发 `摊薄每股收益-普通股` 与 `摊薄每股收益-ADS`，两者相除即可反推比例）。
4. **币种探测误判**。早期实现扫描全部标签找非 USD 单位，结果把
   `Year` / `Store` 这类**单位键**当成币种，导致 Apple 被判定为“以 Year 申报”，
   所有估值指标被误清空。→ 只扫描货币类标签。
5. **SSE 阻塞整个事件循环**。在 async 生成器里调用带 `threading.Lock` 的
   `snapshot()`，会卡死 uvicorn（连 `/api/health` 都不响应）。→ 改为无锁读取
   （`deque` 的 append/len 在 CPython 下原子），并加心跳与断连检测。
6. **预览页搜索排序**。`NV` 搜不到 NVDA：早期实现在攒够若干匹配后就 `break`，
   而股票池按字母序排列，NVDA 还没被扫到；短查询还会把一堆仅名称含 `NV` 的公司顶到前面。
   另外 SEC 名录里只有 `Alphabet Inc.`，搜 `google` 一无所获。
   → 改为全量扫描 + 「精选代码优先 / 代码越短越前 / 匹配位置越前越前」排序，并加品牌别名表。
7. **红绿色阶暴露出的两个真实缺陷**（都是先落码、后被测出来的）：
   - **中性轴不对称，绿色永远到不了饱和**。中性轴取 5.5，但两个方向合用一个
     `SPAN = 4.5`：低端 `-5.5/5.5 = -1` 正常，高端 `+4.5/5.5 = 0.82` 到不了 1，
     于是 9 分和 10 分渲染成几乎相同的颜色。修法：两端各用自己的分母
     （`SPAN_LOW = 5.5`、`SPAN_HIGH = 4.5`），并把中性色压暗到 `rgb(120,112,101)`，
     使白字在全色阶都 ≥ 4.88:1。
   - **meter 条宽度为 0，整条进度条其实不可见**。`.meter .fill` 用
     `inset: 0 auto 0 0` 只固定左边界，`width:auto` 的绝对定位盒子在行内包含块里
     解析成 `width: 0`，`transform: scaleX()` 于是缩放了一个零宽盒子。
     修法：显式固定 `left/right/top/bottom` 四边使其拉伸。
     该缺陷在红绿改造前就存在，只是空条在改造后更容易被注意到。
8. **板块分类不能靠第三方标签**。理想的 `stock_us_spot_em` 带行业字段，但它走
   东财 push2 域名，在本环境不通；`stock_us_famous_spot_em` 的六个分类
   （科技/金融/医药食品/媒体/汽车能源/制造零售）非常贴合需求，但**同样走 push2**，
   全部失败。改用 **SEC 官方 SIC 码**：官方、免费、全覆盖，且是可推导的门类层级——
   代价是 `company_tickers.json` 不含 SIC，要按 CIK 逐个取，所以做了永久缓存 + 精选种子
   + 可选的批量预热，避免首次使用就要爬一万次。
9. **PyInstaller 不能交叉编译**。想在 macOS 上顺手产个 Windows `.exe` 是行不通的，
   必须走 Windows 构建机或 CI。此外 Inno Setup 的"安装时下载"不是想当然的
   `[Files]` 加个 URL 就行：按官方文档，`download` 必须与
   `external + ignoreversion + DestName + ExternalSize` 同时使用，且 `Source`
   要写 URL——第一版写成了本地路径 + `destsize`，构建机上根本不存在该文件必然失败。
10. **定时器时区**。`BackgroundScheduler(timezone="UTC")` 配
   `next_run_time=datetime.now()`（本地壁钟时间）会被当成 UTC，
   在 UTC+8 机器上首次执行被推到 **8 小时后**；`str(tzinfo)` 得到 `CST`
   又被 `ZoneInfo` 拒绝。→ 统一用 APScheduler 解析出的真实 IANA 时区。
11. **在 CI 上真的出一次 `.exe` 才暴露出来的三个打包缺陷**（都只在 Windows 上发作）：
    - **`EXE(version=...)` 不再接受 dict**。PyInstaller 6.x 抛
      `TypeError: Unsupported type for version info argument: <class 'dict'>`。
      因为那段只在 `sys.platform == "win32"` 下构造，macOS 拿到 `None` 一切正常，
      只有 Windows 构建挂——属于"平台分支里的死代码"，本地永远测不到。
      → 改用 `PyInstaller.utils.win32.versioninfo` 构造真正的 `VSVersionInfo`；
      顺带把 macOS 专属的 `argv_emulation` 也限定到 darwin。
    - **窗口化（`console=False`）的包没有 stderr**，而 uvicorn 默认又自装一个 stderr
      handler，于是"启动失败"只剩一个退出码 1，任何地方都没有原因。首次运行被杀软扫描
      拖慢时，启动器 30 秒上限还会直接 `return 1`——间歇性失败最难查。
      → 启动器在最前面挂上文件日志、用 `log_config=None` 让 uvicorn 走根 handler、
      记录子线程异常，并把等待上限放宽到 120 秒。
    - **`compiler:Languages\ChineseSimplified.isl` 并不保证存在**。它自 6.5.0 起才是官方
      翻译，而且 CI 用的 chocolatey Inno Setup 6.7.1 里就没有；引用不存在的文件会让
      ISCC 直接中止（`Couldn't open include file`），连英文安装包都产不出来。
      → 语言文件随仓库走，预处理器按「仓库自带 → 编译器自带 → 仅英文」兜底。
12. **Windows 打包后窗口层炸，而 CI 全绿**。`pywebview` 在 Windows 上必须经 pythonnet 调 .NET，
    冻结后解析不出 `Python.Runtime.Loader.Initialize`（源码运行正常、CI 也正常——
    因为 `--no-window` 冒烟测试压根没走到 `webview.start()`，这个洞是漏给用户去踩的）。
    → Windows 改为内嵌 Chromium 并用 `--app=` 拉起，**彻底移除 pythonnet**；
    同时新增 `--selftest`，在构建期就把「窗口层到底有没有真的进包」卡住，
    而不是只测一个永远不碰窗口的 `--no-window`。教训：冒烟测试必须覆盖用户真正会走的那条路。
13. **单只标的明细页的「指标」列整列是空的**。五个维度表每一行都从
    `metric.label` / `metric.en` 取名，而 `DIMENSIONS` 里的指标对象只声明了
    `attr`（名字统一放在 i18n 词典的 `m.<attr>`）。`escapeHtml(undefined)` 返回空串，
    于是**每只股票**的指标名都是空白，而旁边的本股值、同行中位数、名次、得分全部正常，
    所以看起来像"只缺一列数据"。
    → 改用同一文件里本就写好的 `labelFor` / `subLabelFor`；`subLabelFor`
    此前是死代码，现在真正接上了双语副标题。
    新增 `tests/test_company_render.py`：在 Node 里加载真实的 i18n/shell/company-view
    三个脚本渲染一行，断言 21 个指标在中英文下都落在自己的单元格里，并断言
    「值缺失的行仍显示 —」——防止把列填满的同时把缺失也填成假数据。
14. **打包出来是 PyInstaller 默认图标**。spec 里 `EXE(...)` 没有 `icon=`，
    macOS 的 `BUNDLE` 还写着 `icon=None`，安装包也没有 `SetupIconFile`。
    → 见 §7.5：图标由 `favicon.svg` 生成并接入 spec 与 Inno Setup。
15. **TSM（以及所有以本币申报的外国私人发行人）大量指标为空**，几个独立原因叠加：
    - **单位选择与币种标签不一致**：`_flow_series` / `_instant_series` 默认
      `unit="USD"`，而币种探测返回 TWD。TSM 的 IFRS 事实里 20-F 同时带了
      **TWD 正表和 USD 便利换算**，于是金额全部取到了 USD 换算值，EPS 却因为
      `unit=None` 取到了 `TWD/shares`——同一个 `Fundamentals` 里混了两种币种；
      而且某个标签只要没有 USD 单位就会被**跳过**，等于币种反过来决定了标签选择。
      → 先定币种，再把同一个单位贯穿所有金额字段（EPS 用 `CUR/shares`），
      并在同时存在多种货币单位时写明用了哪一个。
    - **`dei` 股本数读错了层级**：`_latest_dei_shares(facts)` 找的是 `facts["dei"]`，
      真实结构是 `facts["facts"]["dei"]`，所以它**永远返回 None**，
      `shares_basis` 也从未真正标出过 SEC DEI 口径。修好之后 multi-class 申报主体
      同一期末的多个类别会相加（该接口不带维度成员），并与年度摊薄股数做
      ±10% 一致性校验，避免只拿到一个类别就把市值砍半。
    - **IFRS 标签表缺名**：TSM 的资本开支标成
      `PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities`（不在表里），
      折旧、摊销分开标（`DepreciationExpense` / `AmortisationExpense`），
      SBC 用现金流量表的 `AdjustmentsForSharebasedPayments`，股数用
      `WeightedAverageShares`。缺一个就少一片指标：没有 capex 就没有 FCF/P·FCF，
      没有股数就没有市值。→ 补齐别名，并让「只标折旧」的标签补加上摊销。
    - **akshare 回退路径几乎是空的**：它按精确名匹配 `ITEM_NAME`，但表里写的是
      自己臆想的名字（`基本每股收益`、`稀释每股收益`、
      `购建固定资产、无形资产和其他长期资产支付的现金`），东财实际发的是
      `摊薄每股收益-普通股`、`营业成本`、`购买固定资产`、`短期债务` 等；还把三张表
      合并成一个「名字 → 值」映射，而 `净利润` 在现金流量表里是**税前**口径
      （TSM FY2025：2041.7B vs 利润表的 1695.1B），于是现金流量表的数覆盖了利润表的数，
      净利率、ROE、现金转化全被污染。另外币种被硬编码成 `USD`。
      → 每张表各取各的字段、按东财真实科目名匹配、币种取 `CURRENCY_ABBR`、
      空单元格不再变成 NaN（NaN 不是合法 JSON，会直接打崩前端解析）。
      顺带把「哪个 sheet 没取到」写进 `row.notes`，而不是静默少半张表。
    修完 TSM 在 SEC 路径上从 11/21 覆盖升到 14/21；剩余空项是**跨币种守卫按设计剔除**的
    （TWD 申报 vs USD ADR，且 ADR 还有 1:5 比例）。
    另外 `MetricRow.filing_currency` 现在单独记录申报币种：明细页「以 TWD 申报」的徽章
    过去判的是 `row.currency !== "USD"`，而 ADR 的交易币种**就是** USD，
    所以这个徽章对自己要提醒的那一类标的一次都没亮过；现在改为比较
    「申报币种 vs 交易币种」，并在「计算输入」区块上直接标出金额单位。
16. **TSM 的台币财报 → 美元 + ADS 换算**（把第 15 条剩下的缺口补齐）。
    换算需要两样东西，都不引入新的外部数据源：
    - **汇率**：20-F 自带 US$ 便利换算，同一笔金额在申报里同时以 TWD 和 USD 出现，
      两者相除**就是这家公司自己用的汇率**，逐年取多个科目的中位数即可
      （实测 15 个大额科目互差在 1e-5 以内；TSM FY2024 = 32.79，正是其 20-F 声明的
      「NT$32.79 = US$1.00」）。用的是申报里的口径，不需要维护任何汇率表。
    - **ADS 比例**：这是存托协议的条款，不是 XBRL 事实，因此按发行人配置
      （`FR_ADS_RATIOS`，默认 `TSM=5`，依据台积电 20-F：每份 ADS 代表 5 股普通股）。
    两者齐备时，整个 `Fundamentals` 会被重述成交易币种 + ADS 口径（市值用的是
    ADS 等价股数 51.9 亿，而不是 259.3 亿普通股），跨币种守卫自然不再触发。
    缺失任一项时**不做部分换算**，仍按第 15 条留空并说明原因——宁可空着，不要错 5 倍。
    换算说明与所用汇率（含逐年明细）都写进 `Fundamentals.fx_rates` 与 `row.notes`。

    效果（同一网络、同一份缓存数据）：

    | | 换算前 | 换算后 |
    |---|---|---|
    | 覆盖 | 11/21，`Partial filing data` | **20/21**，`Refreshed`（缺的那个是 beta，需要 SPY 基准） |
    | 市值 | 空 | 2.2544e12（腾讯报价 2.2544e12，**相差 0.001%**，两条独立路径互证） |
    | P/S · P/B · P/FCF · EV/EBITDA | 全空 | 25.5 · 17.4 · 85.0 · 36.2 |
    | EPS | 44.67 TWD/普通股 | 6.81 USD/ADS（与券商口径一致） |

    注意 akshare 兜底路径**没有**美元列，因此那里仍无法换算，`row.notes` 会直说
    「需要 SEC 申报的美元数字」。这也是为什么 SEC 不通时该标的仍然进不了排名。
    另外东财的币种来自另一个接口，该接口失败时**不会**再假定成 USD：
    对已配置 ADS 比例的标的（即已知是 ADR）改为标记币种未确认，让守卫继续留空——
    标错成 USD 会让守卫失效，反而把「美元价格 ÷ 台币每股」当成正常值发布出去。
17. **「指标列还是空的」——修好了却看不见：浏览器缓存把上一版前端顶了回来。**
    明细页指标名的修复确实在包里（服务端字节里能查到 `labelFor(metric.attr)`，渲染测试也过），
    但用户的 `launcher.log` 给出了真凭实据：23:42 重新打包后，23:47 打开 `company.html` 时
    **完全没有对 `/js/company-view.js` 发过任何请求**（同一时间其他脚本都重新取了 200），
    而它在 23:31 曾以 `304` 取过一次——也就是说 Chromium 直接从磁盘缓存里拿了旧文件。
    机制：`StaticFiles` 只发 `ETag`/`Last-Modified`、**不发 `Cache-Control`**（已核对
    starlette 0.45.3 源码），浏览器于是按「启发式新鲜度 ≈ 文件年龄的 10%」自行判断，
    而内嵌浏览器的 profile 是**持久化**的，于是近期取过的旧脚本会被直接复用。
    → 双保险：① 静态资源改为 `Cache-Control: no-store, must-revalidate`
    （+ `Pragma` / `Expires` 照顾 HTTP/1.0；本地磁盘读取成本可忽略，换来「重新打包必定生效」）；
    ② 启动内嵌浏览器前清掉 profile 的 HTTP / Code / GPU 缓存目录——已经写进磁盘的旧副本，
    光靠响应头是赶不走的。localStorage（语言、列显示偏好）不受影响，只清缓存。
    日志里会写 `cleared N browser cache directories`，便于判断这一层到底有没有生效。
18. **关了窗口进程却不退**（正是这次「文件被占用、无法覆盖构建」的原因）。
    `SystemExit` 本身不够：`pipeline.build_rows` 用 `ThreadPoolExecutor` 并发抓取，
    而 Python 在解释器退出时会 **join 这些 worker 线程**，于是在刷新进行中关掉窗口，
    进程会继续活着——此时 uvicorn 的 daemon 线程已经停了，端口还在 accept 却永远不回应
    （外部表现就是「服务死了但进程还在」），同时锁着包内文件，只能去任务管理器结束。
    → 窗口层结束后用 `desktop._exit_now()` 确定性退出：先 `logging.shutdown()` 刷日志，
    再 `os._exit()`。代价是**丢弃正在进行的那次抓取**——这正是「关窗口」的语义，
    而且快照只在最后一步于 SQLite 事务内写入，不会留下半条记录。
19. **Chromium 快照里带着 346 MB 的测试程序**。官方 `chrome-win.zip` 含
    `interactive_ui_tests.exe`（Chrome 自己的交互测试宿主），`stage_chromium.ps1`
    原本整包复制，会让安装体积凭空翻倍（Chrome for Testing 里没有它）。
    → staging 后剪掉 `*_tests.exe` / `*unittests*`：809 MB → 463 MB。

---

## 9. 数据口径与免责声明

- 价格来自**日收盘价**（有复权用复权），52 周高低点可能与盘中极值略有差异。
- FY1 EPS 一致预期在免费源上**不可得**，因此不做预测打分；`Guidance` sheet 保留
  供手工填入口径。
- 非GAAP EPS 未自动抓取，桥表是**模型估算**，不代表公司口径。
- 这是**研究筛选工具**，不构成投资建议；任何决策前请核对原始财报。
