"""Builds the parameter preview for one US ticker from akshare data.

Every number the page shows is derived from an akshare interface, and each row
is annotated with the Excel column it corresponds to, the formula used, and the
interface that supplied the inputs — so the mapping from raw data to the
workbook's parameters is auditable rather than asserted.

Anchor rule: all figures are taken at the newest fiscal year that has revenue,
which keeps a stale or partially-tagged year from mixing periods.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from app.engine.metrics import DEFAULT_TAX_RATE, MAX_TAX_RATE, cagr, growth, safe_div
from app.preview_models import (
    FinancialLine,
    ParamGroup,
    ParamSpec,
    ParamValue,
    PreviewPayload,
    PriceBar,
    RatioLine,
    RawStatements,
)
from app.providers import akshare_us as akus
from app.universe import resolve

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# parameter catalog
# --------------------------------------------------------------------------- #
# English names for the raw statement line items. The preview page serves both
# languages from this one catalogue, so the frontend never has to map strings.
ITEM_EN: dict[str, str] = {
    "主营收入": "Revenue",
    "营业成本": "Cost of revenue",
    "毛利": "Gross profit",
    "研发费用": "R&D expense",
    "营业利润": "Operating income",
    "所得税": "Income tax",
    "净利润": "Net income",
    "税前利润其他项目": "Pre-tax income, other",
    "持续经营税前利润": "Pre-tax income, continuing operations",
    "摊薄每股收益-普通股": "Diluted EPS, common",
    "摊薄加权平均股数-普通股": "Diluted weighted average shares",
    "经营活动产生的现金流量净额": "Operating cash flow",
    "购买固定资产": "Purchases of fixed assets",
    "折旧及摊销": "Depreciation & amortisation",
    "基于股票的补偿费": "Share-based compensation",
    "股东权益合计": "Total shareholders' equity",
    "归属于母公司股东权益": "Equity attributable to parent",
    "总资产": "Total assets",
    "现金及现金等价物": "Cash & cash equivalents",
    "短期债务": "Short-term debt",
    "长期债务": "Long-term debt",
    "资本租赁债务(非流动)": "Capital lease obligations (non-current)",
    # lines that appear in the raw statements akshare returns
    "营销费用": "Marketing expense",
    "一般及行政费用": "General & administrative",
    "营业费用": "Operating expense",
    "基本每股收益-普通股": "Basic EPS, common",
    "所得税费用": "Income tax expense",
    "利息费用": "Interest expense",
    "营业总成本": "Total operating cost",
}

# English catalogue copy (labels, formulas, notes) keyed by the stable keys.
CATEGORY_EN = {
    "market": "Market & cap",
    "growth": "Growth",
    "profitability": "Profitability",
    "cash": "Cash flow & leverage",
    "pershare": "Per-share bridge (non-GAAP)",
    "valuation": "Valuation",
    "market_risk": "Market & risk",
    "akshare_extra": "Extra from akshare"
}

PARAM_EN = {
    "price": [
        "Price",
        "latest daily close",
        "last akshare daily bar; not the workbook's live quote"
    ],
    "high_52w": [
        "52W high",
        "highest close in the last 365 days",
        "the workbook uses intraday extremes; this is close-based"
    ],
    "low_52w": [
        "52W low",
        "lowest close in the last 365 days",
        "same as above"
    ],
    "market_cap": [
        "Market cap",
        "price x share count",
        "akshare has no share count; derived from net income / diluted EPS (see Shares)"
    ],
    "shares": [
        "Shares (derived)",
        "net income / diluted EPS",
        "akshare's US endpoints do not report share count; this is an estimate"
    ],
    "revenue_fy0": [
        "Revenue FY0",
        "latest fiscal year of income-statement revenue",
        ""
    ],
    "revenue_growth_yoy": [
        "Revenue growth YoY",
        "FY0 / FY-1 - 1",
        ""
    ],
    "revenue_cagr_5y": [
        "Revenue CAGR 5Y",
        "(FY0 / FY-5)^(1/5) - 1",
        ""
    ],
    "eps_growth_yoy": [
        "EPS growth YoY",
        "EPS FY0 / EPS FY-1 - 1",
        "not a workbook column; share-count changes push it away from net-income growth"
    ],
    "eps_cagr_5y": [
        "EPS CAGR 5Y",
        "(EPS FY0 / EPS FY-5)^(1/5) - 1",
        ""
    ],
    "gross_profit_fy0": [
        "Gross profit FY0",
        "income statement, gross profit",
        ""
    ],
    "gross_margin": [
        "Gross margin",
        "gross profit / revenue",
        ""
    ],
    "operating_income_fy0": [
        "Operating income FY0",
        "income statement, operating income",
        ""
    ],
    "operating_margin": [
        "Operating margin",
        "operating income / revenue",
        ""
    ],
    "net_income_fy0": [
        "Net income FY0",
        "income statement, net income",
        "not a workbook column, but the denominator of every margin"
    ],
    "net_margin": [
        "Net margin",
        "net income / revenue",
        ""
    ],
    "equity_fy0": [
        "Shareholders' equity FY0",
        "balance sheet, total equity",
        ""
    ],
    "roe": [
        "ROE",
        "net income / equity",
        ""
    ],
    "roic": [
        "ROIC",
        "operating income x (1 - tax rate) / (equity + debt - cash)",
        ""
    ],
    "rd_expense": [
        "R&D expense",
        "income statement, R&D",
        ""
    ],
    "ocf_fy0": [
        "Operating cash flow FY0",
        "cash-flow statement, net operating cash",
        ""
    ],
    "capex_fy0": [
        "Capital expenditure FY0",
        "absolute value of purchases of fixed assets",
        ""
    ],
    "fcf_fy0": [
        "Free cash flow FY0",
        "operating cash flow - capex",
        ""
    ],
    "fcf_margin": [
        "FCF margin",
        "free cash flow / revenue",
        ""
    ],
    "fcf_yield": [
        "FCF yield",
        "free cash flow / market cap",
        ""
    ],
    "ocf_to_net_income": [
        "Cash conversion",
        "operating cash flow / net income",
        ""
    ],
    "cash_fy0": [
        "Cash FY0",
        "balance sheet, cash & equivalents",
        ""
    ],
    "debt_fy0": [
        "Debt FY0",
        "short-term + long-term debt",
        ""
    ],
    "total_assets_fy0": [
        "Total assets FY0",
        "balance sheet, total assets",
        ""
    ],
    "debt_to_assets": [
        "Debt / assets",
        "debt / total assets",
        ""
    ],
    "da_fy0": [
        "D&A FY0",
        "cash-flow statement, depreciation & amortisation",
        ""
    ],
    "sbc_fy0": [
        "Share-based comp FY0",
        "cash-flow statement, stock-based compensation",
        ""
    ],
    "effective_tax_rate": [
        "Effective tax rate",
        "income tax / pre-tax income, clamped to 0-40%",
        "falls back to 21% when pre-tax income is missing"
    ],
    "gaap_eps": [
        "GAAP diluted EPS",
        "income statement, diluted EPS",
        ""
    ],
    "diluted_shares": [
        "Diluted shares",
        "income statement, diluted weighted average shares",
        ""
    ],
    "sbc_adj_share": [
        "SBC adj/share",
        "SBC x (1 - tax rate) / diluted shares",
        ""
    ],
    "amortization_adj_share": [
        "Amortization adj/share",
        "intangible amortisation x (1 - tax rate) / diluted shares",
        ""
    ],
    "restructuring_adj_share": [
        "Restructuring adj/share",
        "restructuring x (1 - tax rate) / diluted shares",
        "not reported separately by MSFT and others, so blank is normal"
    ],
    "model_adjusted_eps": [
        "Model adjusted EPS",
        "GAAP EPS + the after-tax per-share adjustments",
        ""
    ],
    "reported_non_gaap_eps": [
        "Reported non-GAAP EPS",
        "-",
        "akshare does not provide it; enter it by hand"
    ],
    "selected_adjusted_eps": [
        "Selected adjusted EPS",
        "company figure when available, otherwise the model figure",
        ""
    ],
    "price_to_sales": [
        "P/S",
        "market cap / revenue",
        ""
    ],
    "price_to_book": [
        "P/B",
        "market cap / equity",
        ""
    ],
    "price_to_fcf": [
        "P/FCF",
        "market cap / free cash flow (when FCF > 0)",
        ""
    ],
    "ev_to_ebitda": [
        "EV/EBITDA",
        "(market cap + debt - cash) / (operating income + D&A)",
        ""
    ],
    "ntm_pe": [
        "NTM P/E",
        "price / next-year EPS",
        "next-year EPS is extrapolated from the FY0 to FY1 growth rate, not analyst consensus"
    ],
    "ev_fy0": [
        "Enterprise value",
        "market cap + debt - cash",
        ""
    ],
    "analyst_target": [
        "Analyst target",
        "-",
        "akshare does not provide it; enter it by hand"
    ],
    "forward_pe_quote": [
        "Forward P/E (quote source)",
        "-",
        "the workbook takes it from Yahoo, unavailable here, so it stays blank"
    ],
    "return_1y": [
        "1Y return",
        "close-to-close over the last 365 days",
        ""
    ],
    "drawdown_52w": [
        "52W drawdown",
        "price / 52-week high - 1",
        ""
    ]
}

EXTRA_EN = {
    "ROA": [
        "Return on assets",
        "asset efficiency; absent from the workbook"
    ],
    "ROE_AVG": [
        "Average ROE",
        "akshare's own figure, for cross-checking the derived ROE"
    ],
    "CURRENT_RATIO": [
        "Current ratio",
        "short-term solvency; absent from the workbook"
    ],
    "SPEED_RATIO": [
        "Quick ratio",
        "liquidity excluding inventory; absent from the workbook"
    ],
    "DEBT_ASSET_RATIO": [
        "Debt / assets (akshare)",
        "cross-check against the derived value"
    ],
    "GROSS_PROFIT_RATIO": [
        "Gross margin (akshare)",
        "cross-check against the derived value"
    ],
    "NET_PROFIT_RATIO": [
        "Net margin (akshare)",
        "cross-check against the derived value"
    ],
    "ACCOUNTS_RECE_TR": [
        "Receivables turnover",
        "absent from the workbook"
    ],
    "INVENTORY_TR": [
        "Inventory turnover",
        "absent from the workbook"
    ],
    "TOTAL_ASSETS_TR": [
        "Total asset turnover",
        "absent from the workbook"
    ],
    "BASIC_EPS": [
        "Basic EPS",
        "compare with diluted EPS to see the dilution"
    ],
    "BASIC_EPS_YOY": [
        "Basic EPS YoY",
        "absent from the workbook"
    ],
    "OPERATE_INCOME_YOY": [
        "Revenue YoY (akshare)",
        "cross-check against the derived value"
    ],
    "PARENT_HOLDER_NETPROFIT_YOY": [
        "Net profit YoY",
        "absent from the workbook"
    ]
}


# The provider records its provenance under Chinese section names; map them for
# the English page so the legend matches the rest of the UI.
SOURCE_EN: dict[str, str] = {
    "日线行情": "Daily prices",
    "综合损益表": "Income statement",
    "现金流量表": "Cash flow statement",
    "资产负债表": "Balance sheet",
    "分析指标": "Analysis indicators",
}


# Provider strings the akshare layer records (Chinese) -> English.
SOURCE_VALUE_EN: dict[str, str] = {
    "akshare.stock_us_daily（新浪财经）": "akshare.stock_us_daily (Sina)",
    "akshare.stock_financial_us_report_em（东方财富）": "akshare.stock_financial_us_report_em (Eastmoney)",
    "akshare.stock_financial_us_analysis_indicator_em（东方财富）":
        "akshare.stock_financial_us_analysis_indicator_em (Eastmoney)",
}


def item_label(item: str, lang: str = "zh") -> str:
    """Statement line label in the requested language (falls back to the source)."""
    if lang == "en":
        return ITEM_EN.get(item, item)
    return item


CATEGORY_TITLES = {
    "market": "行情与市值",
    "growth": "成长性",
    "profitability": "盈利能力",
    "cash": "现金流与杠杆",
    "pershare": "每股调整（Non-GAAP 桥）",
    "valuation": "估值倍数",
    "market_risk": "市场与风险",
    "akshare_extra": "akshare 额外可得（Excel 中没有）",
}

# key, label, excel ref, category, unit, formula, note
CATALOG: list[ParamSpec] = [
    # ---------------- market ----------------
    ParamSpec(key="price", label="股价", excel_ref="Data Cache!F", category="market",
              unit="USD", formula="最新日线收盘价",
              note="取 akshare 日线最后一根；区别于原表的实时报价"),
    ParamSpec(key="high_52w", label="52周最高", excel_ref="Data Cache!G", category="market",
              unit="USD", formula="近365天最高收盘价", note="原表为盘中极值，此处口径为收盘价"),
    ParamSpec(key="low_52w", label="52周最低", excel_ref="Data Cache!H", category="market",
              unit="USD", formula="近365天最低收盘价", note="同上"),
    ParamSpec(key="market_cap", label="市值", excel_ref="Data Cache!I", category="market",
              unit="USD", formula="股价 × 股本",
              note="akshare 无股本字段，用「净利润 ÷ 摊薄EPS」反推，见「股本(反推)」"),
    ParamSpec(key="shares", label="股本(反推)", excel_ref="Data Cache!BD", category="market",
              unit="shares", formula="净利润 ÷ 摊薄EPS",
              note="akshare 美股接口不提供股本；此为推算值"),

    # ---------------- growth ----------------
    ParamSpec(key="revenue_fy0", label="营收 FY0", excel_ref="Data Cache!L", category="growth",
              unit="USD", formula="损益表「主营收入」最新财年"),
    ParamSpec(key="revenue_growth_yoy", label="营收同比", excel_ref="Data Cache!M", category="growth",
              unit="%", formula="FY0 ÷ FY-1 − 1"),
    ParamSpec(key="revenue_cagr_5y", label="营收5年CAGR", excel_ref="Data Cache!N", category="growth",
              unit="%", formula="(FY0 ÷ FY-5)^(1/5) − 1"),
    ParamSpec(key="eps_growth_yoy", label="EPS同比", excel_ref="(新增)", category="growth",
              unit="%", formula="EPS FY0 ÷ EPS FY-1 − 1",
              note="原表无此列；股本变动会使它偏离净利润增速"),
    ParamSpec(key="eps_cagr_5y", label="EPS 5年CAGR", excel_ref="Data Cache!AL", category="growth",
              unit="%", formula="(EPS FY0 ÷ EPS FY-5)^(1/5) − 1"),

    # ---------------- profitability ----------------
    ParamSpec(key="gross_profit_fy0", label="毛利 FY0", excel_ref="Data Cache!BT", category="profitability",
              unit="USD", formula="损益表「毛利」"),
    ParamSpec(key="gross_margin", label="毛利率", excel_ref="Data Cache!O", category="profitability",
              unit="%", formula="毛利 ÷ 营收"),
    ParamSpec(key="operating_income_fy0", label="营业利润 FY0", excel_ref="Data Cache!BG", category="profitability",
              unit="USD", formula="损益表「营业利润」"),
    ParamSpec(key="operating_margin", label="营业利润率", excel_ref="Data Cache!P", category="profitability",
              unit="%", formula="营业利润 ÷ 营收"),
    ParamSpec(key="net_income_fy0", label="净利润 FY0", excel_ref="(N/A)", category="profitability",
              unit="USD", formula="损益表「净利润」", note="原表未单列，但为利润率分母"),
    ParamSpec(key="net_margin", label="净利率", excel_ref="Data Cache!Q", category="profitability",
              unit="%", formula="净利润 ÷ 营收"),
    ParamSpec(key="equity_fy0", label="股东权益 FY0", excel_ref="Data Cache!BO", category="profitability",
              unit="USD", formula="资产负债表「股东权益合计」"),
    ParamSpec(key="roe", label="ROE", excel_ref="Data Cache!R", category="profitability",
              unit="%", formula="净利润 ÷ 股东权益"),
    ParamSpec(key="roic", label="ROIC", excel_ref="Data Cache!S", category="profitability",
              unit="%", formula="营业利润×(1−实际税率) ÷ (权益+债务−现金)"),
    ParamSpec(key="rd_expense", label="研发费用", excel_ref="(新增)", category="profitability",
              unit="USD", formula="损益表「研发费用」"),

    # ---------------- cash ----------------
    ParamSpec(key="ocf_fy0", label="经营现金流 FY0", excel_ref="Data Cache!BP", category="cash",
              unit="USD", formula="现金流量表「经营活动产生的现金流量净额」"),
    ParamSpec(key="capex_fy0", label="资本开支 FY0", excel_ref="Data Cache!BQ", category="cash",
              unit="USD", formula="现金流量表「购买固定资产」取绝对值"),
    ParamSpec(key="fcf_fy0", label="自由现金流 FY0", excel_ref="Data Cache!T", category="cash",
              unit="USD", formula="经营现金流 − 资本开支"),
    ParamSpec(key="fcf_margin", label="FCF利润率", excel_ref="Data Cache!U", category="cash",
              unit="%", formula="自由现金流 ÷ 营收"),
    ParamSpec(key="fcf_yield", label="FCF收益率", excel_ref="Data Cache!V", category="cash",
              unit="%", formula="自由现金流 ÷ 市值"),
    ParamSpec(key="ocf_to_net_income", label="现金转化", excel_ref="Data Cache!W", category="cash",
              unit="x", formula="经营现金流 ÷ 净利润"),
    ParamSpec(key="cash_fy0", label="现金 FY0", excel_ref="Data Cache!BE", category="cash",
              unit="USD", formula="资产负债表「现金及现金等价物」"),
    ParamSpec(key="debt_fy0", label="债务 FY0", excel_ref="Data Cache!BF", category="cash",
              unit="USD", formula="资产负债表「短期债务」+「长期债务」"),
    ParamSpec(key="total_assets_fy0", label="总资产 FY0", excel_ref="Data Cache!BS", category="cash",
              unit="USD", formula="资产负债表「总资产」"),
    ParamSpec(key="debt_to_assets", label="资产负债率", excel_ref="Data Cache!X", category="cash",
              unit="%", formula="债务 ÷ 总资产"),
    ParamSpec(key="da_fy0", label="折旧及摊销 FY0", excel_ref="Data Cache!BH", category="cash",
              unit="USD", formula="现金流量表「折旧及摊销」"),
    ParamSpec(key="sbc_fy0", label="股权激励费 FY0", excel_ref="Data Cache!BJ", category="cash",
              unit="USD", formula="现金流量表「基于股票的补偿费」"),
    ParamSpec(key="effective_tax_rate", label="实际税率", excel_ref="Data Cache!BM", category="cash",
              unit="%", formula="所得税 ÷ 税前利润，钳制在 0–40%",
              note="税前利润缺失时回退 21%"),

    # ---------------- per share ----------------
    ParamSpec(key="gaap_eps", label="GAAP摊薄EPS", excel_ref="Data Cache!Y", category="pershare",
              unit="USD/share", formula="损益表「摊薄每股收益-普通股」"),
    ParamSpec(key="diluted_shares", label="摊薄股数", excel_ref="Data Cache!BI", category="pershare",
              unit="shares", formula="损益表「摊薄加权平均股数-普通股」"),
    ParamSpec(key="sbc_adj_share", label="股权激励调整/股", excel_ref="Data Cache!Z", category="pershare",
              unit="USD/share", formula="股权激励费 × (1−税率) ÷ 摊薄股数"),
    ParamSpec(key="amortization_adj_share", label="摊销调整/股", excel_ref="Data Cache!AB", category="pershare",
              unit="USD/share", formula="无形资产摊销 × (1−税率) ÷ 摊薄股数"),
    ParamSpec(key="restructuring_adj_share", label="重组调整/股", excel_ref="Data Cache!AA", category="pershare",
              unit="USD/share", formula="重组费用 × (1−税率) ÷ 摊薄股数",
              note="MSFT 等公司未单列该科目，缺失属正常"),
    ParamSpec(key="model_adjusted_eps", label="模型调整后EPS", excel_ref="Data Cache!AE", category="pershare",
              unit="USD/share", formula="GAAP EPS + 各项税后每股调整之和"),
    ParamSpec(key="reported_non_gaap_eps", label="公司披露Non-GAAP EPS", excel_ref="Data Cache!AF", category="pershare",
              unit="USD/share", formula="—", note="akshare 不提供，需手工录入原表"),
    ParamSpec(key="selected_adjusted_eps", label="选定调整后EPS", excel_ref="Data Cache!AG", category="pershare",
              unit="USD/share", formula="有公司口径则用公司口径，否则用模型口径"),

    # ---------------- valuation ----------------
    ParamSpec(key="price_to_sales", label="市销率 P/S", excel_ref="Data Cache!AN", category="valuation",
              unit="x", formula="市值 ÷ 营收"),
    ParamSpec(key="price_to_book", label="市净率 P/B", excel_ref="Data Cache!AP", category="valuation",
              unit="x", formula="市值 ÷ 股东权益"),
    ParamSpec(key="price_to_fcf", label="P/FCF", excel_ref="Data Cache!AQ", category="valuation",
              unit="x", formula="市值 ÷ 自由现金流（FCF>0 时）"),
    ParamSpec(key="ev_to_ebitda", label="EV/EBITDA", excel_ref="Data Cache!AO", category="valuation",
              unit="x", formula="(市值+债务−现金) ÷ (营业利润+折旧摊销)"),
    ParamSpec(key="ntm_pe", label="NTM P/E", excel_ref="Data Cache!AM", category="valuation",
              unit="x", formula="当前股价 ÷ 下一年度EPS",
              note="用 FY0→FY1 增速外推下一财年EPS，非分析师一致预期"),
    ParamSpec(key="ev_fy0", label="企业价值 EV", excel_ref="(N/A)", category="valuation",
              unit="USD", formula="市值 + 债务 − 现金"),
    ParamSpec(key="analyst_target", label="分析师目标价", excel_ref="Data Cache!AS", category="valuation",
              unit="USD", formula="—", note="akshare 不提供，需手工录入"),
    ParamSpec(key="forward_pe_quote", label="Forward P/E(报价源)", excel_ref="Data Cache!AM/BV", category="valuation",
              unit="x", formula="—", note="原表取自雅虎报价；本环境不可用，故留空"),

    # ---------------- market risk ----------------
    ParamSpec(key="return_1y", label="1年回报", excel_ref="Data Cache!J", category="market_risk",
              unit="%", formula="近365天收盘价变化"),
    ParamSpec(key="drawdown_52w", label="52周回撤", excel_ref="Data Cache!AR", category="market_risk",
              unit="%", formula="当前价 ÷ 52周最高 − 1"),
]

# Ratios akshare ships directly that the workbook does not compute at all.
EXTRA_RATIOS: list[tuple[str, str, str, str]] = [
    # (analysis column, label, unit, why it is interesting)
    ("ROA", "总资产收益率 ROA", "%", "衡量资产使用效率，原表缺失"),
    ("ROE_AVG", "平均ROE", "%", "akshare 口径，可与自算 ROE 交叉验证"),
    ("CURRENT_RATIO", "流动比率", "x", "短期偿债能力，原表缺失"),
    ("SPEED_RATIO", "速动比率", "x", "剔除存货后的流动性，原表缺失"),
    ("DEBT_ASSET_RATIO", "资产负债率(akshare)", "%", "与自算值交叉验证"),
    ("GROSS_PROFIT_RATIO", "毛利率(akshare)", "%", "与自算值交叉验证"),
    ("NET_PROFIT_RATIO", "净利率(akshare)", "%", "与自算值交叉验证"),
    ("ACCOUNTS_RECE_TR", "应收账款周转率", "x", "原表缺失"),
    ("INVENTORY_TR", "存货周转率", "x", "原表缺失"),
    ("TOTAL_ASSETS_TR", "总资产周转率", "x", "原表缺失"),
    ("BASIC_EPS", "基本EPS", "USD/share", "与摊薄EPS对照，观察摊薄幅度"),
    ("BASIC_EPS_YOY", "基本EPS同比", "%", "原表缺失"),
    ("OPERATE_INCOME_YOY", "营收同比(akshare)", "%", "与自算值交叉验证"),
    ("PARENT_HOLDER_NETPROFIT_YOY", "净利润同比", "%", "原表缺失"),
]

# Raw statement items surfaced for transparency, grouped by statement.
INCOME_ITEMS = [
    "主营收入", "营业成本", "毛利", "研发费用", "营销费用", "一般及行政费用",
    "营业费用", "营业利润", "利息收入", "所得税", "净利润", "摊薄每股收益-普通股",
    "基本每股收益-普通股", "摊薄加权平均股数-普通股", "重组费用",
]
CASHFLOW_ITEMS = [
    "经营活动产生的现金流量净额", "购买固定资产", "折旧及摊销", "基于股票的补偿费",
    "投资活动产生的现金流量净额", "筹资活动产生的现金流量净额",
    "回购股份", "股息支付", "现金及现金等价物期末余额",
]
BALANCE_ITEMS = [
    "总资产", "流动资产合计", "现金及现金等价物", "短期投资", "总负债",
    "流动负债合计", "短期债务", "资本租赁债务(非流动)", "长期债务",
    "股东权益合计", "归属于母公司股东权益", "商誉", "无形资产", "存货",
]


# --------------------------------------------------------------------------- #
# builder
# --------------------------------------------------------------------------- #
class PreviewBuilder:
    """Derives the workbook parameters for one ticker from akshare data."""

    def __init__(self, ticker: str, lang: str = "zh") -> None:
        self.ticker = ticker.upper().strip()
        self.lang = lang if lang in ("zh", "en") else "zh"
        self.data = akus.TickerData(self.ticker)
        self.warnings: list[str] = []
        self._series_cache: dict[str, dict[date, float | None]] = {}

    # -- infrastructure ---------------------------------------------------- #
    def _frame(self, statement: str):
        return (
            self.data.income if statement == "income"
            else self.data.cashflow if statement == "cashflow"
            else self.data.balance
        )

    def _series(self, statement: str, item: str) -> dict[date, float | None]:
        key = f"{statement}:{item}"
        if key not in self._series_cache:
            self._series_cache[key] = akus.statement_series(self._frame(statement), item)
        return self._series_cache[key]

    def _value(self, statement: str, item: str, periods: list[date], offset: int = 0):
        series = self._series(statement, item)
        if not series or len(periods) <= offset:
            return None
        return series.get(periods[offset])

    # -- main -------------------------------------------------------------- #
    def build(self, price_points: int = 260) -> PreviewPayload:
        periods = self.data.fiscal_years(limit=6)
        if not periods:
            raise akus.AkshareUnavailable(
                f"akshare 未返回 {self.ticker} 的年度财报，无法构建参数"
            )

        years = [akus._fiscal_year(p) for p in periods]
        info = resolve(self.ticker)

        computed = self._compute(periods)

        # Assemble parameter rows in catalog order.
        by_category: dict[str, list[ParamValue]] = {}
        for spec in CATALOG:
            value = computed.get(spec.key)
            status = "ok"
            if spec.key in ("reported_non_gaap_eps", "analyst_target", "forward_pe_quote"):
                status = "manual"
            elif value is None:
                status = "missing"
            # English copy lives in PARAM_EN, so the 51 catalog definitions keep
            # a single authoring language and the API serves either.
            en = PARAM_EN.get(spec.key) if self.lang == "en" else None
            label, formula, note = en if en else (spec.label, spec.formula, spec.note)
            row = ParamValue(
                key=spec.key, label=label, excel_ref=spec.excel_ref,
                category=spec.category, unit=spec.unit, formula=formula,
                value=value, display=_display(value, spec.unit), status=status,
                source=self.data.sources.get(
                    "income statement" if spec.category in ("growth", "profitability", "pershare")
                    and self.lang == "en"
                    else "综合损益表" if spec.category in ("growth", "profitability", "pershare")
                    else "cash-flow statement" if spec.category == "cash" and self.lang == "en"
                    else "现金流量表" if spec.category == "cash"
                    else "daily prices" if self.lang == "en"
                    else "日线行情", ""
                ),
                note=note,
            )
            by_category.setdefault(spec.category, []).append(row)

        groups = [
            ParamGroup(
                category=cat,
                title=(CATEGORY_EN.get(cat) if self.lang == "en" else None)
                or CATEGORY_TITLES.get(cat, cat),
                items=items,
            )
            for cat, items in by_category.items()
        ]

        return PreviewPayload(
            ticker=self.ticker,
            company=(info.name if info else "") or self.ticker,
            generated_at=datetime.now(),
            fiscal_years=years,
            currency=self.data.currency(),
            groups=groups,
            raw=self._raw_statements(periods),
            ratios=self._extra_ratios(periods),
            prices=self._price_bars(price_points),
            sources={
                key: (SOURCE_VALUE_EN.get(value, value) if self.lang == "en" else value)
                for key, value in self.data.sources.items()
            },
            warnings=self.data.warnings + self.warnings,
        )

    # -- computation ------------------------------------------------------- #
    def _compute(self, periods: list[date]) -> dict[str, float | None]:
        v = lambda stmt, item, off=0: self._value(stmt, item, periods, off)  # noqa: E731

        revenue = v("income", "主营收入")
        revenue_prev = v("income", "主营收入", 1)
        revenue_5y = v("income", "主营收入", 5)
        gross = v("income", "毛利")
        cost = v("income", "营业成本")
        operating = v("income", "营业利润")
        net = v("income", "净利润")
        tax = v("income", "所得税")
        pretax = v("income", "税前利润其他项目") or v("income", "持续经营税前利润")
        eps = v("income", "摊薄每股收益-普通股")
        eps_prev = v("income", "摊薄每股收益-普通股", 1)
        eps_5y = v("income", "摊薄每股收益-普通股", 5)
        diluted_shares = v("income", "摊薄加权平均股数-普通股")

        if gross is None and revenue is not None and cost is not None:
            gross = revenue - cost

        equity = v("balance", "股东权益合计") or v("balance", "归属于母公司股东权益")
        assets = v("balance", "总资产")
        cash = v("balance", "现金及现金等价物")
        # Debt is a sum of parts; keep None when neither part is reported so the
        # row reads "missing" instead of a misleading 0.
        debt_short = v("balance", "短期债务")
        debt_long = v("balance", "长期债务") or v("balance", "资本租赁债务(非流动)")
        if debt_short is None and debt_long is None:
            debt = None
        else:
            debt = (debt_short or 0.0) + (debt_long or 0.0)

        ocf = v("cashflow", "经营活动产生的现金流量净额")
        capex_raw = v("cashflow", "购买固定资产")
        capex = abs(capex_raw) if capex_raw is not None else None
        da = v("cashflow", "折旧及摊销")
        sbc = v("cashflow", "基于股票的补偿费")

        # ---- tax rate (mirror the engine's clamping) ----
        if tax is not None and pretax is not None and abs(pretax) > 1:
            tax_rate = min(MAX_TAX_RATE, max(0.0, tax / pretax))
        else:
            tax_rate = DEFAULT_TAX_RATE

        # ---- market ----
        closes = self._closes()
        price = closes[-1] if closes else None
        window = self._closes(365)
        high = max(window) if window else None
        low = min(window) if window else None

        # ---- shares: akshare has no share count, so reverse it out of EPS ----
        shares = None
        if net and eps and eps > 0:
            shares = net / eps
        elif diluted_shares:
            shares = diluted_shares

        market_cap = price * shares if (price and shares) else None
        fcf = (ocf - capex) if (ocf is not None and capex is not None) else None

        # ---- per-share bridge ----
        def per_share(amount: float | None) -> float | None:
            if amount is None or not diluted_shares:
                return None
            return amount * (1 - tax_rate) / diluted_shares

        sbc_ps = per_share(sbc)
        # akshare lumps depreciation and amortization into a single line and
        # does not split out intangible amortization, so this add-back cannot
        # be derived from it (the workbook gets it from a separate XBRL tag).
        amort_ps = None
        restruct_ps = per_share(v("income", "重组费用"))
        addbacks = [x for x in (sbc_ps, amort_ps, restruct_ps) if x is not None]
        model_eps = (eps + sum(addbacks)) if (eps is not None and addbacks) else None

        invested = None
        if equity is not None and debt is not None and cash is not None:
            invested = equity + debt - cash
        roic = None
        if operating is not None and invested and invested > 0:
            roic = operating * (1 - tax_rate) / invested

        ebitda = None
        if operating is not None and da is not None:
            ebitda = operating + da
        ev = None
        if market_cap is not None and debt is not None and cash is not None:
            ev = market_cap + debt - cash

        # ---- naive next-year EPS projection for NTM P/E ----
        ntm_eps = None
        if eps is not None and eps_prev not in (None, 0) and eps_prev > 0 and eps > 0:
            g = eps / eps_prev - 1
            if -0.9 < g < 5.0:  # guard against nonsense extrapolation
                ntm_eps = eps * (1 + g)
        ntm_pe = safe_div(price, ntm_eps)

        return {
            "price": price,
            "high_52w": high,
            "low_52w": low,
            "shares": shares,
            "market_cap": market_cap,

            "revenue_fy0": revenue,
            "revenue_growth_yoy": growth(revenue, revenue_prev),
            "revenue_cagr_5y": cagr(revenue, revenue_5y, 5),
            "eps_growth_yoy": growth(eps, eps_prev),
            "eps_cagr_5y": cagr(eps, eps_5y, 5),

            "gross_profit_fy0": gross,
            "gross_margin": safe_div(gross, revenue),
            "operating_income_fy0": operating,
            "operating_margin": safe_div(operating, revenue),
            "net_income_fy0": net,
            "net_margin": safe_div(net, revenue),
            "equity_fy0": equity,
            "roe": safe_div(net, equity),
            "roic": roic,
            "rd_expense": v("income", "研发费用"),

            "ocf_fy0": ocf,
            "capex_fy0": capex,
            "fcf_fy0": fcf,
            "fcf_margin": safe_div(fcf, revenue),
            "fcf_yield": safe_div(fcf, market_cap),
            "ocf_to_net_income": safe_div(ocf, net),
            "cash_fy0": cash,
            "debt_fy0": debt,
            "total_assets_fy0": assets,
            "debt_to_assets": safe_div(debt, assets),
            "da_fy0": da,
            "sbc_fy0": sbc,
            "effective_tax_rate": tax_rate,

            "gaap_eps": eps,
            "diluted_shares": diluted_shares,
            "sbc_adj_share": sbc_ps,
            "amortization_adj_share": amort_ps,
            "restructuring_adj_share": restruct_ps,
            "model_adjusted_eps": model_eps,
            "reported_non_gaap_eps": None,
            "selected_adjusted_eps": model_eps,

            "price_to_sales": safe_div(market_cap, revenue),
            "price_to_book": safe_div(market_cap, equity),
            "price_to_fcf": safe_div(market_cap, fcf) if (fcf and fcf > 0) else None,
            "ev_to_ebitda": safe_div(ev, ebitda) if (ebitda and ebitda > 0) else None,
            "ntm_pe": ntm_pe,
            "ev_fy0": ev,
            "analyst_target": None,
            "forward_pe_quote": None,

            "return_1y": self._return_1y(),
            "drawdown_52w": (price / high - 1) if (price and high) else None,
        }

    # -- helpers ----------------------------------------------------------- #
    def _closes(self, days: int | None = None) -> list[float]:
        frame = self.data.daily
        if frame is None or frame.empty:
            return []
        closes = [float(x) for x in frame["close"].tolist() if x == x]
        if days is None or not closes:
            return closes
        # Daily bars are trading days; ~252 per calendar year.
        count = min(len(closes), max(2, int(days / 365 * 252)))
        return closes[-count:]

    def _return_1y(self) -> float | None:
        window = self._closes(365)
        if len(window) < 2 or not window[0]:
            return None
        return window[-1] / window[0] - 1

    def _raw_statements(self, periods: list[date], limit: int = 5) -> RawStatements:
        def lines(frame, items: list[str]) -> list[FinancialLine]:
            out: list[FinancialLine] = []
            for item in items:
                series = akus.statement_series(frame, item)
                if not series:
                    continue
                values = {
                    akus._fiscal_year(period): series.get(period)
                    for period in periods[:limit]
                }
                if any(x is not None for x in values.values()):
                    out.append(FinancialLine(item=item_label(item, self.lang), values=values))
            return out

        return RawStatements(
            income=lines(self.data.income, INCOME_ITEMS),
            cashflow=lines(self.data.cashflow, CASHFLOW_ITEMS),
            balance=lines(self.data.balance, BALANCE_ITEMS),
        )

    def _extra_ratios(self, periods: list[date], limit: int = 5) -> list[RatioLine]:
        frame = self.data.analysis
        out: list[RatioLine] = []
        if frame is None or frame.empty:
            return out
        for column, label, unit, _why in EXTRA_RATIOS:
            series = akus.analysis_series(frame, column)
            if not series:
                continue
            values = {
                akus._fiscal_year(period): series.get(period)
                for period in periods[:limit]
            }
            if any(x is not None for x in values.values()):
                shown = EXTRA_EN.get(column, (label, ""))[0] if self.lang == "en" else label
                out.append(RatioLine(key=column, label=shown, values=values, unit=unit))
        return out

    def _price_bars(self, points: int) -> list[PriceBar]:
        frame = self.data.daily
        if frame is None or frame.empty:
            return []
        tail = frame.tail(points)
        bars: list[PriceBar] = []
        for _, row in tail.iterrows():
            try:
                bars.append(
                    PriceBar(
                        d=row["date"].date() if hasattr(row["date"], "date") else row["date"],
                        open=_f(row.get("open")), high=_f(row.get("high")),
                        low=_f(row.get("low")), close=_f(row.get("close")),
                        volume=_f(row.get("volume")),
                    )
                )
            except Exception:  # pragma: no cover - defensive
                continue
        return bars


def _f(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _display(value, unit: str) -> str:
    if value is None:
        return "—"
    if unit == "%":
        return f"{value * 100:.2f}%"
    if unit == "x":
        return f"{value:.2f}x"
    if unit in ("USD",) :
        return _money(value)
    if unit == "USD/share":
        return f"${value:,.2f}"
    if unit == "shares":
        return f"{value / 1e9:,.2f}B"
    return f"{value:,.4f}"


def _money(value: float) -> str:
    for suffix, scale in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(value) >= scale:
            return f"${value / scale:,.2f}{suffix}"
    return f"${value:,.2f}"


def build_preview(ticker: str, price_points: int = 260, lang: str = "zh") -> PreviewPayload:
    """Public entry point used by the API."""
    builder = PreviewBuilder(ticker, lang=lang)
    try:
        return builder.build(price_points=price_points)
    finally:
        builder.data.close()
