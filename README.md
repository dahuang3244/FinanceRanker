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
| 年度财报 | **SEC XBRL `companyfacts`** | Yahoo 年度财报（同财年缺项）→ akshare | 保留字段来源；缺失年份不臆造 |
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
| `GET` | `/api/insights/{ticker}` | 趋势、可追溯总结、财报日期提示与新闻标题温度；支持 `job_id` / `run_id` |

公司明细新增数据提示：3 个月回报使用足够跨度的日线，52 周回撤按同一价格口径的收盘价与收盘高点计算；20/50/200 日均线与 RSI(14) 均从标注日期的价格序列计算。TSM 优先使用 SEC 台币财报，Yahoo `2330.TW` 台币年报只补同财年缺项，之后按原有 5:1 ADS 比例和汇率换算。缺失的资本支出不按 0 处理：先尝试发行人披露，仍取不到时用折旧摊销的一半作保守替代，并在 `capex_basis` 中写明依据（参见 §5）。

TSM 现金流进一步补缺：2025 财年采用[台积电合并财务报告](https://investor.tsmc.com/sites/ir/financial-report/2025/TSMC%202025Q4%20Consolidated%20Financial%20Statements_E.pdf)中经营现金流 NT$2,274,975,625 千元和购置不动产、厂房及设备支出 NT$1,272,410,529 千元，计算 FCF 为 NT$1,002,565,096 千元、FCF 利润率约 26.32%。同口径 FCF 换算为美元后再计算 FCF 收益率和市值/FCF。仅当财报日期为 2025-12-31 且报表币种为台币时使用这些已核对的数值；之后财年尝试自动读取台积电官网该年度合并财报 PDF，不能获取时保留空值而不会沿用 2025 年数据。3 个月回报优先调用完整的 akshare/Sina 调整后美国 ADR 日线以补足短历史记录。**更新旧快照请重新执行抓取任务**。

财报提示只显示 Yahoo 日历中可取得的未来**预估日期**，需以公司公告核对；取不到则显示日期未核实。新闻温度来自 Google News RSS 近 90 天标题，至少两条含方向词才计算，不能当作全文情感分析或交易信号。公司回报／风险面板的 1–7 分是现有同行分位的可解释映射，`n/N` 标明数据覆盖，新闻不参与排序。更新已有快照里的 3 个月回报或 FCF 时，请重新运行抓取并排名。
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
P/B、P/FCF。

**行情与风险指标**分为三段，目的是让**每只股票在加入后都拿到同一套数字**，可以直接横向比较：

- **回报**：1 个月、3 个月、6 个月、1 年、年初至今、3 年（复权总回报）。
- **相对 SPY**：3 个月 / 6 个月 / 1 年的超额回报，以及 SPY 自身的同期回报（用于对账）。
- **风险**：年化波动率、下行波动率、夏普比率、索提诺比率、Beta、
  近一年最大回撤、距 52 周高点回撤。
- **卖方一致预期**：目标均价 / 中位数 / 最高 / 最低、覆盖分析师数、评级、上行空间。

**为四个基本面维度补充的指标**（`roa`、`capex_intensity` 计入评分，其余标为「参考」）：

| 维度 | 新增指标 | 回答什么问题 |
|---|---|---|
| 成长 | 营收 3Y CAGR、EPS 同比、净利润同比、毛利同比、FCF 同比 | 增速是新出现的还是持续的？利润与现金流有没有跟上营收？ |
| 盈利 | ROA、资产周转率、经营杠杆 | 赚钱靠的是资产效率还是杠杆？毛利率在扩张吗？ |
| 现金 | 资本开支强度、现金/总资产、股权激励/营收 | FCF 低是因为在重投入，还是因为收不回现金？摊薄成本有多高？ |
| 估值 | PEG、EV/营收、市值/经营现金流、净负债/EBITDA | 高倍数有没有增速支撑？EBITDA 失真时的替代口径是什么？ |

**「参考」不等于不重要**：这些指标展示、导出、参与同行排名，但**不进入加权总分**，
每一条都在 `/api/metrics` 里写明「为什么不计分」（例如 PEG 在增速为负时无定义）。
界面上它们带 `参考` 标签、得分列写「不计分」而不是留空——留空与「得分很低」看起来一模一样。

**指标目录是唯一事实来源**：`GET /api/metrics` 返回 52 项指标的定义（维度、方向、
格式、是否计分、不计分的原因），公司明细页的五个维度块完全由它构建。
此前前端自带一份手写指标清单，后端改了评分范围而前端没跟上，
结果就是 Beta 和 1 年超额回报的得分列一片空白而且没有任何解释。现在由
`tests/test_metric_catalog.py` 锁死这个不变量：目录、评分范围、z 字段、
双语标签、前端渲染必须一致。

### 5.1 市场分析师（`GET /api/analyst/{ticker}`）

公司明细页新增一节，用**一次** Yahoo `quoteSummary` 调用（五个 module）取回：

- **评级分布**：强烈买入 / 买入 / 持有 / 卖出 / 强烈卖出，并对比约三个月前的分布。
- **目标价与评级历史**：每条已公布的评级调整画一个点（近 24 个月），叠加现价虚线。
  点越散说明分歧越大——单个「平均目标价」看不出这一点。
- **实际 EPS vs 一致预期**：Yahoo 公布的每个季度，实际（绿/红）与预期（灰）并列，
  下方标注超预期幅度。
- **前瞻一致预期**：本季 / 下季 / 本财年 / 下财年的 EPS 均值、区间、机构数与同比。
- **近期评级与目标价调整**：逐条机构、评级变化、目标价及调整方向。

**这一节不参与打分**：卖方观点是第三方看法，不是公司事实，混进分位排名会把它变成
另一种东西。图表用内联手写 SVG，不引入图表库——整个前端保持零依赖。

**数据边界要说清楚**：Yahoo 只公布**四个季度**的「实际 vs 一致预期」，
参考产品里的 12 个季度来自付费数据源；这一点写在 `notes` 里并显示在面板底部，
不会让人以为历史更长。

**打分**（对齐 `Scoring` sheet）：每项指标在股票池内做 1–10 分位打分，再按 5 个维度
（成长/盈利/现金/估值/市场）加权合成总分。

**权重是可切换的口径，不是写死的常数**。排名页顶部有四个预设，切换后**同一份分位得分**
按新权重重新排名，不重新抓取：

| 预设 | 成长 | 盈利 | 现金 | 估值 | 市场 | 回答什么问题 |
|---|---|---|---|---|---|---|
| 均衡 | 20% | 15% | 20% | 25% | 20% | 默认口径 |
| 动量 | 20% | 10% | 5% | 20% | **45%** | 现在哪只更强（相对 SPY 与风险调整后） |
| 质量 | 10% | **30%** | **30%** | 20% | 10% | 生意质量，最不看重短期涨跌 |
| 估值 | 15% | 10% | 15% | **40%** | 20% | 买入价格，代价是价值陷阱 |
| 自定义 | — | — | — | — | — | 来自 `FR_W_*`，改了 `.env` 即成为默认 |

**市场维度由两个子分合成**，避免「涨得多」压过「涨得稳」：表现（3M/6M/1Y 回报及
相对 SPY 超额，占 60%）+ 风险（夏普/索提诺/波动率/Beta/回撤，占 40%）。
子分各自显示在公司明细页的市场区块标题上，所以一个高排名能看出是来自表现还是来自稳健。
只奖励回报而不惩罚风险，会让波动最大的标的仅因为涨得多而排前面。

**遇到无法计算的指标时，先替代、再说明，绝不静默留空**：

- **符号翻转**（亏损公司）：`+$26m` 净利润变成 `-$6.9bn` 亏损时，百分比增长在数学上不存在。
  此时改报**变化量**（相对营收的百分比），并在行内标注「替代口径」，同时**不参与该项打分**
  —— 因为它是绝对变化，与同行的百分比不是同一个单位，混在一起算分位会污染排名。
- **历史不足**：5 年 CAGR 需要 6 个年度点；上市 3 年的公司改用可用的最长窗口，
  并在 `cagr_basis` 写明实际用的是 3 年，不会把 3 年数字当作 5 年呈现。
- **负基数 FCF**：上一年 FCF 为负时同样改报变化量，而不是留空——这恰恰是最该看方向的时候。

权重是探索性的比较口径，不是对未来收益的预测；若用于投资判断，应先做样本外回测。
覆盖度不足（<12 项或维度缺失）则标记为不可排名；
`data_coverage` / `coverage_pct` 会显示到底缺了哪几项。

**口径必须说清楚**，否则数字之间不可比：

- **价格口径**：开盘先确定本次运行的价格基准——若雅虎可达，个股与 SPY 都取
  `adjclose`（含分红、拆股，即总回报）；否则两者都退到新浪口径（仅拆股调整）。
  两侧必须同口径，否则超额回报会凭空多出或少掉一个股息率。
  实际口径写在 `market_price_basis` 与行备注里。
- **Beta**：`cov(个股, SPY) / var(SPY)`，最少 30 个观测。`beta` 用全部可得历史
  （与供应商口径一致，仅展示）；参与打分的是 `beta_1y`，与夏普/波动率同期。
  注意：早期版本误用了个股方差作分母，等价于相关系数，已修正。
- **夏普 / 索提诺**：用近一年日收益率，无风险利率由 `FR_RISK_FREE` 统一设定
  （默认 4%）。必须全池同一数值，否则比率不可比。实际取值写在 `risk_free_rate`。
- **窗口长度**：3M/6M/1Y 是自然日窗口，且要求实际覆盖足够长（3M≥70 天、6M≥150 天、
  1Y≥300 天），避免把两周的行情说成一个季度。

**数据缺口**按“先补齐、补不上就说明”的原则处理，绝不静默留空：

- 未打标的 capex（常见于 IFRS 外国私人发行人）用**折旧摊销的一半**作保守替代，
  依据写入 `capex_basis`。
- 未打标的借款用**总负债（资产 − 权益）**作上限替代，依据写入 `debt_basis`。
- 未打标的成本行使毛利率无法自下而上计算时，用**营收 − 营业利润**作为上限
  （会高估，因为未扣研发与销售管理费用），依据写入 `margin_basis`。
- 外国私人发行人（如 TSM）的 TWD 报表按即期汇率折算为 USD、普通股 ÷5 换算为 ADS，
  权益类比率（毛利/营业/净利率、FCF 利润率、资产周转、资产负债率）不受汇率影响，
  在跨币种保护后重新计算，因此不会因为“报表币种不同”而整列留空。

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
│   ├── prices.py           新浪 → akshare/东财 → 雅虎（按价格口径筛选 provider）
│   ├── quotes.py           腾讯 → 东财 → 雅虎
│   └── yahoo_analyst.py    雅虎分析师一致预期（crumb 鉴权，失败即静默跳过）
├── engine/
│   ├── metrics.py          指标引擎（对应 Excel 公式）
│   ├── market.py           回报窗口 / 波动率 / 夏普 / 索提诺 / 最大回撤 / Beta
│   └── scoring.py          分位打分与加权合成
├── exporters/
│   ├── csv_export.py       全池 CSV（按维度分节，116 列 / 13 节）
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

desktop.py                 桌面入口：起本地服务 + 原生窗口（系统 webview）
finance_ranker.spec        PyInstaller 配置（含 py_mini_racer 原生库与静态资源）
windows/installer.iss      Inno Setup 安装包脚本（中英文向导 + 随包备用浏览器）
windows/build.ps1          Windows 本地构建（测试 → 打包 → 冒烟 → 安装包）
Build-Windows.cmd          Windows 双击构建入口
.github/workflows/build.yml  CI：在 Windows / macOS 上各自出包
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
| 得分轨 | 五维得分 + 总分，各自带权重（20/15/20/25/20） |
| 维度块 ×5 | 每个维度一张表：指标、本股、同行中位、差值（按好坏着色）、池内名次、得分条、1–10 得分。市场／风险维度含 12 项：1M/3M/6M/1Y 回报、3M/6M/1Y 相对 SPY 超额、波动率、夏普、索提诺、Beta、最大回撤、52 周回撤 |
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
| Windows | 默认用系统浏览器；内嵌 Chrome for Testing 备用 | 是，产物约 400 MB |

Windows 之所以不再走 pywebview：它的 WinForms 后端必须经 **pythonnet** 去调 .NET，
而打包成 exe 之后 pythonnet 加载不了 `Python.Runtime.dll`：

```
RuntimeError: Failed to resolve Python.Runtime.Loader.Initialize
             from ...\_internal\pythonnet\runtime\Python.Runtime.dll
```

这个故障**只在冻结包里出现**——源码运行、CI 的 `--no-window` 冒烟测试都完全正常，
所以第一版发了出去，用户双击才炸。与其去赌一套 .NET 互操作桥，不如自己带浏览器：
Windows 版默认使用系统浏览器；没有可用的系统浏览器时使用随包提供的 Chrome for Testing。

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
| Windows 的窗口层 | 默认系统浏览器；无法启动时尝试随包 Chrome for Testing；不再因测试版浏览器提示误以为 Chrome 需要升级 |

调试用参数：

| 参数 | 作用 |
|---|---|
| `--no-window` | 只起服务，供无人值守验证（不碰窗口层） |
| `--selftest` | 校验窗口层真的在包里（Windows 上缺 Chromium 就以退出码 2 失败），构建时用它把关 |
| `--browser` | 使用系统默认浏览器（现在也是 Windows 默认行为） |
| `--bundled-browser` | Windows 上明确要求用随包浏览器打开 |

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
| `FinanceRanker-setup` | **`FinanceRanker-0.1.1-setup.exe`** ← 双击安装的安装包 |
| `FinanceRanker-windows` | 免安装的绿色版目录（整个 `dist/FinanceRanker/`） |
| `FinanceRanker-macos` | macOS 的 `.app` |

**Artifacts 需要登录才能下载，而且 90 天就过期**，所以安装包另外走 Release 发布：

```bash
git tag v0.1.1 && git push origin v0.1.1
```

打 `v*` tag 后，`release` job 会把该 tag 那次构建出的安装包发布到
**Releases**（[github.com/dahuang3244/FinanceRanker/releases](https://github.com/dahuang3244/FinanceRanker/releases)），
得到不需要登录、不过期的公开直链。发布用的是**被 tag 的那个 commit** 构建的产物，
不会出现"源码和二进制对不上"。

工作流在打包**之前**会跑全部测试、并把 Chrome for Testing（win64）下载到
`vendor/chromium/` 供打包内嵌；打包**之后**会先跑一次 `--selftest` 确认窗口层真的进了包，
再做冒烟测试（启动冻结的应用 → 等它报出端口 → 校验 `/api/health` 与首页 200），
所以"能下载"等于"能跑"。

### 路线 B：在 Windows 上本地构建

将本仓库的完整源码包解压到一个文件夹，安装 Python 3.12 x64 后，在该文件夹中**双击 `Build-Windows.cmd`**。
它会使用 `.venv-build` 独立安装依赖，并在结束时保留窗口供查看结果。
若看到 `Python was not found`，那可能只是 Windows 的 Microsoft Store 执行别名；
请安装真正的 Python 3.12 x64（安装完成后重新双击脚本）。在 PowerShell 中可运行：

```powershell
winget install --id Python.Python.3.12 --exact --source winget
```

若无法使用 winget，请从 Python 官网下载 3.12 的 Windows 64-bit installer。
也可从项目根目录的 PowerShell 手动运行：

```powershell
powershell -ExecutionPolicy Bypass -File windows\build.ps1
```

脚本会依次：校验 Python 3.12 x64 → 装依赖 → 跑测试 → PyInstaller 打包 →
冒烟测试 → 若有 Inno Setup 则顺带编译安装包。

可直接运行的文件位于 `dist\FinanceRanker\FinanceRanker.exe`，运行时要保留同目录的 `_internal`；
若已安装 Inno Setup，则另有 `dist\FinanceRanker-0.1.1-setup.exe`，便于单文件分发安装。
缺 Inno Setup 时会明确提示，若需要安装包，可先安装：

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
   → 改为**跨标签按期间取最新，并合并旧标签里的历史财年**，供同比与 CAGR 使用。
2. **资产负债表科目没有 `start`**，用利润表的“年度区间”规则过滤会把
   Assets / Equity 全部过滤掉。→ 拆成 flow / instant 两套抽取规则。
3. **外币申报主体**。TSM 报 IFRS 且以 **TWD** 申报，却是 USD ADR。
   直接算 `市值(USD)/营收(TWD)`、`价格(USD)/EPS(TWD)` 是错的
   （ADR 还有 1:5 比例）。→ 增加 `ifrs-full` 支持 + **跨币种守卫**：
   台币财务金额通过公开 TWD/USD 汇率换算、普通股股数除以五、每普通股 EPS
   乘以五。Yahoo 汇率不可用时再尝试开放的 ExchangeRate-API 日度参考汇率；
   两者都失效才剔除跨币种估值倍数并明确标注原因。换算仅供同业估值比较，
   财报利润表历史金额按现汇换算并非公司正式报告的美元数。
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

---

## 9. 数据口径与免责声明

- 价格来自**日收盘价**（有复权用复权），52 周高低点可能与盘中极值略有差异。
- FY1 EPS 一致预期在免费源上**不可得**，因此不做预测打分；`Guidance` sheet 保留
  供手工填入口径。
- 非GAAP EPS 未自动抓取，桥表是**模型估算**，不代表公司口径。
- 这是**研究筛选工具**，不构成投资建议；任何决策前请核对原始财报。
