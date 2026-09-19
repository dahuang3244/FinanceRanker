"""Offline tests for the akshare US-stock preview layer.

These exercise the parameter catalogue, the derivation maths and the ticker
search without touching the network: akshare frames are built by hand and fed
straight into the builder, so the assertions are deterministic.

Run:  PYTHONPATH=. python tests/test_preview.py
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from app import universe
from app.preview import CATALOG, CATEGORY_TITLES, EXTRA_RATIOS, PreviewBuilder
from app.preview_models import ParamSpec


# --------------------------------------------------------------------------- #
# synthetic akshare frames (same shape as stock_financial_us_report_em)
# --------------------------------------------------------------------------- #
PERIODS = [
    date(2026, 6, 30), date(2025, 6, 30), date(2024, 6, 30),
    date(2023, 6, 30), date(2022, 6, 30), date(2021, 6, 30),
]

# Amounts are stated in full dollars, exactly as the workbook reports them, so
# no hidden scaling factor can mask a unit mistake.
INCOME = {
    "主营收入": [331_839_000_000, 281_724_000_000, 245_122_000_000,
                 211_915_000_000, 198_270_000_000, 168_088_000_000],
    "营业成本": [106_374_000_000, 87_831_000_000, 74_114_000_000,
                 65_863_000_000, 62_650_000_000, 52_232_000_000],
    "毛利": [225_465_000_000, 193_893_000_000, 171_008_000_000,
             146_052_000_000, 135_620_000_000, 115_856_000_000],
    "营业利润": [155_237_000_000, 128_528_000_000, 109_433_000_000,
                 88_523_000_000, 83_383_000_000, 69_916_000_000],
    "净利润": [133_749_000_000, 101_832_000_000, 88_136_000_000,
               72_361_000_000, 72_738_000_000, 61_271_000_000],
    "所得税": [32_185_000_000, 21_795_000_000, 19_651_000_000,
               16_950_000_000, 10_978_000_000, 9_831_000_000],
    "研发费用": [35_562_000_000, 32_488_000_000, 29_510_000_000,
                 27_195_000_000, 24_512_000_000, 20_716_000_000],
    "摊薄每股收益-普通股": [17.95, 13.64, 11.80, 9.68, 9.65, 8.05],
    "摊薄加权平均股数-普通股": [7_453_000_000, 7_468_000_000, 7_469_000_000,
                               7_479_000_000, 7_540_000_000, 7_616_000_000],
}
CASHFLOW = {
    "经营活动产生的现金流量净额": [182_935_000_000, 136_162_000_000, 118_548_000_000,
                                   87_582_000_000, 89_035_000_000, 76_740_000_000],
    "购买固定资产": [-115_948_000_000, -64_551_000_000, -44_477_000_000,
                     -28_107_000_000, -23_886_000_000, -20_622_000_000],
    "折旧及摊销": [38_534_000_000, 29_433_000_000, 20_958_000_000,
                   17_069_000_000, 16_182_000_000, 14_681_000_000],
    "基于股票的补偿费": [12_405_000_000, 11_974_000_000, 10_734_000_000,
                         9_611_000_000, 7_502_000_000, 6_118_000_000],
}
BALANCE = {
    "总资产": [758_376_000_000, 619_003_000_000, 512_163_000_000,
               411_976_000_000, 364_840_000_000, 333_779_000_000],
    "现金及现金等价物": [20_935_000_000, 30_242_000_000, 18_315_000_000,
                         34_704_000_000, 13_931_000_000, 14_224_000_000],
    "短期债务": [6_000_000_000] * 6,
    "长期债务": [40_294_000_000, 43_151_000_000, 44_937_000_000,
                 47_237_000_000, 49_781_000_000, 58_146_000_000],
    "股东权益合计": [442_387_000_000, 343_479_000_000, 268_477_000_000,
                     206_223_000_000, 166_542_000_000, 141_988_000_000],
}


def _frame(items: dict[str, list[float]]) -> pd.DataFrame:
    """Build a frame shaped like stock_financial_us_report_em."""
    rows = []
    for item, values in items.items():
        for period, value in zip(PERIODS, values):
            rows.append(
                {
                    "REPORT_DATE": pd.Timestamp(period),
                    "AMOUNT": str(value),
                    "ITEM_NAME": item,
                }
            )
    return pd.DataFrame(rows)


ANALYSIS = pd.DataFrame(
    [
        {
            "REPORT_DATE": pd.Timestamp(p),
            "CURRENCY": "美元",
            "ROA": 19.4, "ROE_AVG": 34.0, "CURRENT_RATIO": 1.23, "SPEED_RATIO": 1.22,
            "DEBT_ASSET_RATIO": 41.6, "GROSS_PROFIT_RATIO": 67.9, "NET_PROFIT_RATIO": 40.3,
            "BASIC_EPS": 18.0, "DILUTED_EPS": 17.95,
        }
        for p in PERIODS
    ]
)


class FakeTickerData:
    """Stands in for akshare_us.TickerData with pre-built frames."""

    def __init__(self, *_a, **_k):
        self.sources = {"综合损益表": "test", "日线行情": "test"}
        self.warnings = []
        self._frames = {
            "income": _frame(INCOME),
            "cashflow": _frame(CASHFLOW),
            "balance": _frame(BALANCE),
        }

    income = property(lambda self: self._frames["income"])
    cashflow = property(lambda self: self._frames["cashflow"])
    balance = property(lambda self: self._frames["balance"])
    analysis = property(lambda self: ANALYSIS)

    def fiscal_years(self, limit=6):
        return PERIODS[:limit]

    def currency(self):
        return "USD"

    def close(self):
        return None

    @property
    def daily(self):
        return pd.DataFrame(
            {
                "date": pd.to_datetime(["2025-09-18", "2026-03-18", "2026-09-18"]),
                "open": [100.0, 200.0, 300.0], "high": [110.0, 210.0, 310.0],
                "low": [90.0, 190.0, 290.0], "close": [493.78, 480.0, 493.78],
                "volume": [1e6, 1e6, 1e6],
            }
        )


def _builder() -> PreviewBuilder:
    b = PreviewBuilder("MSFT")
    b.data = FakeTickerData()          # type: ignore[assignment]
    b._series_cache.clear()
    return b


def _values(b: PreviewBuilder) -> dict:
    payload = b.build()
    return {item.key: item.value for group in payload.groups for item in group.items}


# --------------------------------------------------------------------------- #
# catalogue integrity
# --------------------------------------------------------------------------- #
def test_catalogue_is_well_formed():
    keys = [spec.key for spec in CATALOG]
    assert len(keys) == len(set(keys)), "duplicate parameter keys"
    for spec in CATALOG:
        assert isinstance(spec, ParamSpec)
        assert spec.key and spec.label and spec.category
        assert spec.category in CATEGORY_TITLES, f"unknown category {spec.category}"
    assert len(CATALOG) >= 40
    assert len(EXTRA_RATIOS) >= 10


def test_every_catalogue_key_is_produced():
    """Each catalogued parameter must have a computed slot, even if None."""
    computed = _values(_builder())
    missing = [spec.key for spec in CATALOG if spec.key not in computed]
    assert not missing, f"catalogue keys never computed: {missing}"


# --------------------------------------------------------------------------- #
# derivation maths
# --------------------------------------------------------------------------- #
def test_core_ratios_match_workbook():
    v = _values(_builder())
    assert v["revenue_fy0"] == 331_839_000_000
    assert v["gross_profit_fy0"] == 225_465_000_000
    assert v["operating_income_fy0"] == 155_237_000_000
    assert v["ocf_fy0"] == 182_935_000_000
    assert v["capex_fy0"] == 115_948_000_000          # stored negative, abs() applied
    assert v["fcf_fy0"] == 182_935_000_000 - 115_948_000_000
    assert v["total_assets_fy0"] == 758_376_000_000
    assert v["gaap_eps"] == 17.95, "EPS must keep its decimals"
    # Ratios the workbook computes with the same inputs.
    assert round(v["operating_margin"], 12) == 0.467808184089
    assert round(v["net_margin"], 12) == 0.403053890592
    assert round(v["roe"], 12) == 0.302334833528


def test_debt_is_sum_of_parts_and_none_when_absent():
    v = _values(_builder())
    assert v["debt_fy0"] == 6_000_000_000 + 40_294_000_000
    assert round(v["debt_to_assets"], 12) == round(46_294_000_000 / 758_376_000_000, 12)


def test_growth_and_cagr_use_matching_periods():
    v = _values(_builder())
    assert round(v["revenue_growth_yoy"], 10) == round(331_839 / 281_724 - 1, 10)
    assert round(v["revenue_cagr_5y"], 10) == round((331_839 / 168_088) ** 0.2 - 1, 10)
    assert round(v["eps_growth_yoy"], 10) == round(17.95 / 13.64 - 1, 10)


def test_shares_reverse_engineered_from_eps():
    v = _values(_builder())
    # akshare has no share count, so it is net income / diluted EPS.
    assert v["shares"] == 133_749_000_000 / 17.95
    assert v["diluted_shares"] == 7_453_000_000
    assert v["market_cap"] == v["price"] * v["shares"]


def test_pershare_bridge_adds_sbc_only():
    v = _values(_builder())
    # akshare exposes no restructuring line and does not split intangible
    # amortization, so only SBC can be added back.
    assert v["restructuring_adj_share"] is None
    assert v["amortization_adj_share"] is None
    tax_rate = v["effective_tax_rate"]
    sbc_ps = 12_405_000_000 * (1 - tax_rate) / 7_453_000_000
    assert round(v["sbc_adj_share"], 9) == round(sbc_ps, 9)
    assert round(v["model_adjusted_eps"], 9) == round(17.95 + sbc_ps, 9)
    assert v["selected_adjusted_eps"] == v["model_adjusted_eps"]


def test_manual_parameters_are_never_fabricated():
    v = _values(_builder())
    for key in ("reported_non_gaap_eps", "analyst_target", "forward_pe_quote"):
        assert v[key] is None, f"{key} must stay empty — akshare does not provide it"


def test_valuation_multiples():
    v = _values(_builder())
    assert round(v["price_to_sales"], 9) == round(v["market_cap"] / 331_839_000_000, 9)
    assert round(v["price_to_book"], 9) == round(v["market_cap"] / 442_387_000_000, 9)
    ebitda = 155_237_000_000 + 38_534_000_000
    ev = v["market_cap"] + v["debt_fy0"] - 20_935_000_000
    assert round(v["ev_to_ebitda"], 9) == round(ev / ebitda, 9)


def test_missing_period_does_not_raise():
    """A short history must degrade to None, not crash the builder."""
    b = _builder()
    for name in ("income", "cashflow", "balance"):
        b.data._frames[name] = b.data._frames[name][
            b.data._frames[name]["REPORT_DATE"] >= pd.Timestamp("2025-01-01")
        ]
    v = _values(b)
    assert v["revenue_fy0"] == 331_839_000_000
    assert v["revenue_cagr_5y"] is None      # needs FY-5
    assert v["eps_cagr_5y"] is None


# --------------------------------------------------------------------------- #
# payload shape for the UI
# --------------------------------------------------------------------------- #
def test_payload_shape():
    payload = _builder().build()
    assert payload.ticker == "MSFT"
    assert payload.currency == "USD"
    assert payload.fiscal_years[0] == "FY2026"
    assert len(payload.groups) >= 6
    assert payload.raw.income and payload.raw.cashflow and payload.raw.balance
    assert payload.ratios, "extra akshare ratios should be surfaced"
    assert payload.prices, "price bars feed the sparkline"
    # statuses must be one of the three the UI renders
    for item in payload.all_items:
        assert item.status in ("ok", "missing", "manual")
        if item.status == "ok":
            assert item.display and item.display != "—"


def test_raw_values_are_keyed_by_fiscal_year():
    payload = _builder().build()
    revenue = next(l for l in payload.raw.income if l.item == "主营收入")
    assert revenue.values["FY2026"] == 331_839_000_000
    assert revenue.values["FY2025"] == 281_724_000_000


# --------------------------------------------------------------------------- #
# universe search (pure logic, universe injected via cache monkeypatch)
# --------------------------------------------------------------------------- #
def test_search_ranks_exact_then_prefix_then_name():
    """Search ranking is pure, so pass the pool directly instead of the cache."""
    from app.preview_models import TickerInfo

    pool = [
        TickerInfo(ticker="NVDA", name="NVIDIA CORP", exchange="US", source="test"),
        TickerInfo(ticker="NVR", name="NVR INC", exchange="US", source="test"),
        TickerInfo(ticker="ANVS", name="Annovis Bio", exchange="US", source="test"),
        TickerInfo(ticker="GOOGL", name="Alphabet Inc.", exchange="US", source="test"),
    ]

    # Exact match wins.
    assert universe.search("NVDA", 3, pool)[0].ticker == "NVDA"
    # Nvidia is curated, so it leads the "NV" prefix bucket over NVR.
    assert universe.search("NV", 3, pool)[0].ticker == "NVDA"
    # Ticker matches come before name-only matches.
    assert [t.ticker for t in universe.search("NV", 5, pool)] == ["NVDA", "NVR", "ANVS"]
    # A brand alias resolves to the issuer's listed symbol.
    assert universe.search("google", 3, pool)[0].ticker == "GOOGL"
    # Case-insensitive.
    assert universe.search("nvidia", 1, pool)[0].ticker == "NVDA"
    # No match yields an empty list rather than raising.
    assert universe.search("ZZZZ", 5, pool) == []


def test_search_empty_query_returns_curated_first():
    from app.preview_models import TickerInfo

    pool = [
        TickerInfo(ticker="ZZZZ", name="Zeta", exchange="US", source="test"),
        TickerInfo(ticker="MSFT", name="Microsoft", exchange="US", source="test"),
    ]
    assert universe.search("", 2, pool)[0].ticker == "MSFT"


# --------------------------------------------------------------------------- #
def _main() -> int:
    tests = [
        (name, obj) for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
