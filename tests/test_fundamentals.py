"""Regression tests for the fundamentals providers.

Everything here runs offline against synthetic payloads that mirror the shapes
these providers actually see. The cases come from a real investigation of TSM,
whose figures were mostly missing because:

* its SEC IFRS facts carry TWD statements **and** a US$ convenience translation,
  and every series was read with a hard-coded `unit="USD"` while the currency
  label was detected as TWD -- so money and per-share figures disagreed;
* the dei (cover-page) share count was read from the wrong path and therefore
  never returned anything;
* the akshare fallback matched line-item names Eastmoney never emits (`基本每股收益`
  rather than `摊薄每股收益-普通股`), and merged the three statements into one
  name -> value map, where the cash-flow `净利润` (pre-tax) overwrote the income
  statement's after-tax one.

Run:  PYTHONPATH=. python tests/test_fundamentals.py
"""

from __future__ import annotations

from datetime import date

from app.engine import metrics
from app.models import FactSeries, Fundamentals
from app.providers import fundamentals as fp


# --------------------------------------------------------------------------- #
# synthetic payloads
# --------------------------------------------------------------------------- #
def _flow_points(values: dict[str, float], form: str = "20-F") -> list[dict]:
    return [
        {
            "start": f"{year}-01-01",
            "end": f"{year}-12-31",
            "val": value,
            "form": form,
            "fp": "FY",
            "fy": int(year),
            "filed": f"{int(year) + 1}-04-01",
        }
        for year, value in values.items()
    ]


def _instant_points(values: dict[str, float], form: str = "20-F") -> list[dict]:
    return [
        {
            "end": f"{year}-12-31",
            "val": value,
            "form": form,
            "fp": "FY",
            "fy": int(year),
            "filed": f"{int(year) + 1}-04-01",
        }
        for year, value in values.items()
    ]


def _tsm_like_payload() -> dict:
    """A dual-unit IFRS payload shaped like TSMC's companyfacts response."""
    # The year-end TWD/USD rates TSMC's own 20-F translations were made with.
    rates = {"2020": 28.08, "2021": 27.74, "2022": 30.73, "2023": 30.62, "2024": 32.79}
    twd = {str(y): v * 1e9 for y, v in zip(range(2020, 2025), (1339, 1587, 2264, 2162, 2894))}
    # The 20-F's US$ convenience translation is exactly twd / rate.
    usd = {year: value / rates[year] for year, value in twd.items()}

    def scaled(source: dict, factor: float) -> dict:
        return {year: value * factor for year, value in source.items()}

    def tag(twd_values: dict, usd_values: dict | None, instant: bool = False) -> dict:
        points = _instant_points if instant else _flow_points
        units = {"TWD": points(twd_values)}
        if usd_values:
            units["USD"] = points(usd_values)
        return {"units": units}

    def per_share(twd_values: dict, usd_values: dict) -> dict:
        return {
            "units": {
                "TWD/shares": _flow_points(twd_values),
                "USD/shares": _flow_points(usd_values),
            }
        }

    return {
        "cik": 1046179,
        "entityName": "Taiwan Semiconductor Manufacturing Company Limited",
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {
                                "end": "2024-12-31",
                                "val": 25932733242,
                                "form": "20-F",
                                "filed": "2025-04-17",
                            }
                        ]
                    }
                }
            },
            "ifrs-full": {
                # TWD only: this is what makes TWD the reporting currency.
                "RevenueFromContractsWithCustomers": {"units": {"TWD": _flow_points(twd)}},
                # Derived concepts scale *both* currencies identically, exactly as
                # a real dual-currency filing does -- scaling only one side would
                # not be a filing, it would be a broken rate.
                "ProfitLoss": tag(scaled(twd, 0.4), scaled(usd, 0.4)),
                "Assets": tag(scaled(twd, 2.3), scaled(usd, 2.3), instant=True),
                "Equity": tag(scaled(twd, 1.5), scaled(usd, 1.5), instant=True),
                "CashAndCashEquivalents": tag(scaled(twd, 0.7), scaled(usd, 0.7), instant=True),
                "ProfitLossFromOperatingActivities": tag(scaled(twd, 0.45), scaled(usd, 0.45)),
                # The variant name that the plain IFRS tag list used to miss.
                "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities": {
                    "units": {"TWD": _flow_points(scaled(twd, 0.3))}
                },
                "DepreciationExpense": {"units": {"TWD": _flow_points(scaled(twd, 0.22))}},
                "AmortisationExpense": {"units": {"TWD": _flow_points({k: "9" for k in twd})}},
                "AdjustmentsForSharebasedPayments": {
                    "units": {"TWD": _flow_points({k: "1.2" for k in twd})}
                },
                "DilutedEarningsLossPerShare": per_share(
                    {k: "44.67" for k in twd}, {k: "1.36" for k in twd}
                ),
                "BasicEarningsLossPerShare": per_share(
                    {k: "44.68" for k in twd}, {k: "1.37" for k in twd}
                ),
                "WeightedAverageShares": {
                    "units": {"shares": _flow_points({k: "25927600000" for k in twd})}
                },
            },
        },
    }


def _patched_sec(monkeypatch_payload: dict):
    """Point the provider at a synthetic payload instead of the network."""
    fp.lookup_cik = lambda ticker: 1046179  # type: ignore[assignment]
    fp.fetch = lambda *a, **k: monkeypatch_payload  # type: ignore[assignment]


# The year-end TWD/USD rates the synthetic filing's US$ column was built with,
# matching the rates TSMC's real 20-F translations use.
EXPECTED_RATES = {"2020": 28.08, "2021": 27.74, "2022": 30.73, "2023": 30.62, "2024": 32.79}


# --------------------------------------------------------------------------- #
# SEC: one currency per Fundamentals
# --------------------------------------------------------------------------- #
def test_monetary_units_lists_the_primary_statement_currency_first():
    payload = _tsm_like_payload()
    node_source = payload["facts"]["ifrs-full"]
    units = fp._monetary_units(node_source, fp.IFRS_FLOW_TAGS, fp.IFRS_INSTANT_TAGS)
    assert units[0] == "TWD", units
    assert "USD" in units, units


def test_sec_series_share_one_currency_and_report_the_translation():
    payload = _tsm_like_payload()
    _patched_sec(payload)
    fund = fp.get_sec_fundamentals("TSM")
    assert fund is not None
    assert fund.currency == "TWD"
    # Money in TWD (billions), not the US$ convenience translation.
    assert fund.revenue.latest() == 2_894_000_000_000
    for name in ("revenue", "net_income", "assets", "equity", "cash"):
        assert getattr(fund, name).unit == "TWD", name
    # Per-share figures follow the same currency and the diluted tag wins.
    assert fund.eps_diluted.unit == "TWD/shares"
    assert fund.eps_diluted.tag == "DilutedEarningsLossPerShare"
    assert any("convenience translation" in note for note in fund.notes), fund.notes


def test_sec_picks_up_the_ifrs_capex_variant_and_separate_amortisation():
    payload = _tsm_like_payload()
    _patched_sec(payload)
    fund = fp.get_sec_fundamentals("TSM")
    assert fund.capex is not None and fund.capex.latest() > 0
    assert fund.amortization is not None and fund.amortization.latest() == 9
    # Depreciation is tagged separately from amortisation, so D&A is the sum.
    row = metrics.compute_row(
        "TSM",
        history=_flat_history(),
        quote=_quote(),
        fund=fund,
    )
    depreciation = fund.depreciation_amortization.latest()
    assert row.da_fy0 == depreciation + fund.amortization.latest()


# --------------------------------------------------------------------------- #
# SEC: cover-page share count
# --------------------------------------------------------------------------- #
def test_dei_share_count_is_read_from_the_nested_facts_block():
    payload = _tsm_like_payload()
    shares = fp._latest_dei_shares(payload)
    assert shares == 25_932_733_242, shares


def test_dei_share_count_sums_share_classes_at_the_newest_period():
    payload = {
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {"end": "2024-12-31", "val": 5_800_000_000, "form": "10-K", "filed": "2025-02-01"},
                            {"end": "2024-12-31", "val": 800_000_000, "form": "10-K", "filed": "2025-02-01"},
                            {"end": "2023-12-31", "val": 5_700_000_000, "form": "10-K", "filed": "2024-02-01"},
                        ]
                    }
                }
            }
        }
    }
    assert fp._latest_dei_shares(payload) == 6_600_000_000


def test_partial_cover_page_count_is_rejected():
    """A single class on the cover page must not halve the issuer's market cap."""
    payload = _tsm_like_payload()
    # Simulate the API exposing only one class: half the annual diluted count.
    payload["facts"]["dei"]["EntityCommonStockSharesOutstanding"]["units"]["shares"][0][
        "val"
    ] = 12_963_800_000
    _patched_sec(payload)
    fund = fp.get_sec_fundamentals("TSM")
    assert fund.shares_outstanding is None
    assert any("cover-page share count" in note for note in fund.notes), fund.notes


# --------------------------------------------------------------------------- #
# akshare fallback
# --------------------------------------------------------------------------- #
def test_akshare_fields_are_read_from_their_own_statement():
    """The cash-flow `净利润` (pre-tax) must not overwrite the income statement's."""
    frames = {
        "income": [
            {"ITEM_NAME": "净利润", "REPORT_DATE": "2024-12-31 00:00:00", "AMOUNT": "1695124900000"},
            {"ITEM_NAME": "所得税", "REPORT_DATE": "2024-12-31 00:00:00", "AMOUNT": "346529800000"},
        ],
        "cashflow": [
            # Same label, different meaning: the cash-flow statement starts pre-tax.
            {"ITEM_NAME": "净利润", "REPORT_DATE": "2024-12-31 00:00:00", "AMOUNT": "2041654700000"},
            {"ITEM_NAME": "折旧及摊销", "REPORT_DATE": "2024-12-31 00:00:00", "AMOUNT": "688096400000"},
        ],
        "balance": [
            {"ITEM_NAME": "总资产", "REPORT_DATE": "2024-12-31 00:00:00", "AMOUNT": "7933023878000"},
        ],
    }
    income_net = fp._ak_series(frames["income"], fp._AK_FIELDS["net_income"][1], "TWD")
    cash_net = fp._ak_series(frames["cashflow"], fp._AK_FIELDS["net_income"][1], "TWD")
    assert fp._AK_FIELDS["net_income"][0] == "income"
    assert income_net.latest() == 1_695_124_900_000
    # The label does exist on the cash-flow sheet too, which is exactly the trap.
    assert cash_net.latest() == 2_041_654_700_000


def test_akshare_diluted_eps_matches_the_emitted_item_name():
    records = [
        {"ITEM_NAME": "基本每股收益-普通股", "REPORT_DATE": "2024-12-31 00:00:00", "AMOUNT": "44.68"},
        {"ITEM_NAME": "摊薄每股收益-普通股", "REPORT_DATE": "2024-12-31 00:00:00", "AMOUNT": "44.67"},
        {"ITEM_NAME": "摊薄每股收益-ADS", "REPORT_DATE": "2024-12-31 00:00:00", "AMOUNT": "327.34"},
    ]
    series = fp._ak_series(records, fp._AK_FIELDS["eps_diluted"][1], "TWD")
    # Per ordinary share (not per ADS), and diluted (not basic).
    assert series.latest() == 44.67


def test_akshare_blank_cells_never_yield_nan():
    records = [
        {"ITEM_NAME": "总资产", "REPORT_DATE": "2024-12-31 00:00:00", "AMOUNT": "nan"},
        {"ITEM_NAME": "总资产", "REPORT_DATE": "2023-12-31 00:00:00", "AMOUNT": "7933023878000"},
    ]
    series = fp._ak_series(records, fp._AK_FIELDS["assets"][1], "TWD")
    assert series.latest() == 7_933_023_878_000
    assert all(value == value for _, value in series.points)  # no NaN survived


# --------------------------------------------------------------------------- #
# ADR conversion (FX from the filing, ADS ratio from the deposit agreement)
# --------------------------------------------------------------------------- #
def test_implied_fx_rates_recover_the_filings_own_translation():
    payload = _tsm_like_payload()
    rates = fp._implied_fx_rates(payload["facts"]["ifrs-full"], "TWD", "USD")
    assert rates, "no rate could be derived from a dual-currency filing"
    for end, rate in rates.items():
        assert abs(rate - EXPECTED_RATES[str(end.year)]) < 0.01, (end, rate)


def test_conversion_restates_the_filing_onto_the_ads_basis():
    payload = _tsm_like_payload()
    _patched_sec(payload)
    fund = fp.get_sec_fundamentals("TSM", trading_currency="USD")

    assert fund.currency == "USD"
    assert fund.filing_currency == "TWD"
    assert fund.ads_ratio == 5
    # Money moved to USD at the FY2024 rate the filing itself used.
    assert abs(fund.revenue.latest() - 2_894_000_000_000 / EXPECTED_RATES["2024"]) < 1
    assert fund.equity.unit == "USD"
    # Per ordinary share -> per ADS, i.e. x5 then converted.
    expected_eps = 44.67 / EXPECTED_RATES["2024"] * 5
    assert abs(fund.eps_diluted.latest() - expected_eps) < 1e-9
    assert fund.eps_diluted.unit == "USD/ADS"
    # Share counts are ADS-equivalent, otherwise price x shares is 5x too big.
    assert abs(fund.shares_outstanding - 25_932_733_242 / 5) < 1
    assert abs(fund.shares_diluted.latest() - 25_927_600_000 / 5) < 1
    assert "ADS" in fund.shares_basis
    assert abs(fund.fx_rates["2024-12-31"] - EXPECTED_RATES["2024"]) < 1e-6
    assert any("converted TWD -> USD" in note for note in fund.notes), fund.notes


def test_converted_row_computes_the_price_based_multiples():
    """The whole point: TSM gets a real market cap and P/S instead of blanks."""
    payload = _tsm_like_payload()
    _patched_sec(payload)
    fund = fp.get_sec_fundamentals("TSM", trading_currency="USD")
    quote = _quote()  # USD, price 180
    row = metrics.compute_row("TSM", history=_flat_history(), quote=quote, fund=fund)

    ads_shares = 25_932_733_242 / 5
    assert abs(row.market_cap - quote.price * ads_shares) < 1
    assert abs(row.price_to_sales - row.market_cap / fund.revenue.latest()) < 1e-9
    # Forward P/E and FY1 EPS growth both need a per-ADS basis on both sides:
    # the quote's estimate is USD per ADS, the filing's EPS now is too.
    assert row.forward_pe is not None
    assert row.eps_growth_fy1 is not None and 0 < row.eps_growth_fy1 < 3
    assert row.currency == row.filing_currency == "USD"
    assert "withheld" not in row.notes
    assert "converted TWD -> USD" in row.notes


def test_without_an_ads_ratio_the_guard_still_withholds():
    """A conversion needs both halves; with no ratio, be honest, not 5x wrong."""
    payload = _tsm_like_payload()
    _patched_sec(payload)
    original = fp.settings.ads_ratios
    fp.settings.ads_ratios = ""
    try:
        fund = fp.get_sec_fundamentals("TSM", trading_currency="USD")
    finally:
        fp.settings.ads_ratios = original

    assert fund.currency == "TWD", "the filing must stay in its own currency"
    assert any("FR_ADS_RATIOS" in note for note in fund.notes), fund.notes
    row = metrics.compute_row("TSM", history=_flat_history(), quote=_quote(), fund=fund)
    assert row.market_cap is None and row.price_to_sales is None
    assert row.filing_currency == "TWD"


def test_same_currency_filings_are_left_alone():
    payload = _tsm_like_payload()
    _patched_sec(payload)
    fund = fp.get_sec_fundamentals("TSM", trading_currency="TWD")
    assert fund.currency == "TWD"
    assert fund.filing_currency is None
    assert fund.fx_rates == {}
    assert fund.ads_ratio is None


def test_unconfirmed_currency_for_an_adr_withholds_instead_of_assuming_usd():
    """Assuming USD for an ADR disables the guard; withhold instead.

    Eastmoney's currency label lives in a separate endpoint, and when that call
    fails an ADR's home-currency figures must not be labelled USD -- the guard
    compares currencies, so a wrong "USD" would let a USD price be divided into
    TWD per-share figures and published as if it were fine.
    """
    frames = {
        "income": [
            {"ITEM_NAME": "营业收入", "REPORT_DATE": "2024-12-31 00:00:00", "AMOUNT": "2894307700000"},
            {"ITEM_NAME": "营业利润", "REPORT_DATE": "2024-12-31 00:00:00", "AMOUNT": "1322053000000"},
        ],
        "cashflow": [],
        "balance": [],
    }
    original_frames, original_meta = fp._ak_frames, fp._ak_meta
    fp._ak_frames = lambda ticker: (frames, [])  # type: ignore[assignment]
    fp._ak_meta = lambda ticker: (None, None)  # type: ignore[assignment]
    try:
        adr = fp.get_akshare_fundamentals("TSM", trading_currency="USD")
        assert adr.currency == fp.UNKNOWN_CURRENCY
        assert any("could not be confirmed" in note for note in adr.notes), adr.notes
        row = metrics.compute_row("TSM", history=_flat_history(), quote=_quote(), fund=adr)
        assert row.market_cap is None and row.price_to_sales is None
        assert row.filing_currency == fp.UNKNOWN_CURRENCY

        # A ticker with no ADS ratio configured keeps the old, clearly-labelled
        # assumption: it is not known to be an ADR.
        domestic = fp.get_akshare_fundamentals("MSFT", trading_currency="USD")
        assert domestic.currency == "USD"
        assert any("assuming USD" in note for note in domestic.notes), domestic.notes
    finally:
        fp._ak_frames, fp._ak_meta = original_frames, original_meta


# --------------------------------------------------------------------------- #
# engine helpers used above
# --------------------------------------------------------------------------- #
def _flat_history():
    from datetime import timedelta

    from app.models import PriceHistory, PricePoint

    start = date(2024, 1, 1)
    return PriceHistory(
        ticker="TSM",
        points=[
            PricePoint(d=start + timedelta(days=i), close=100 + i * 0.1) for i in range(400)
        ],
        source="test",
    )


def _quote():
    from app.models import Quote

    # The estimate is USD per ADS, exactly as the providers quote it.
    return Quote(
        ticker="TSM", currency="USD", price=180.0, eps_forward=13.43, source="test"
    )


# --------------------------------------------------------------------------- #
def _main() -> int:
    """Run every test_* function without needing pytest."""
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
