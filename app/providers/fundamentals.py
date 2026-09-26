"""Fundamentals providers.

SEC XBRL `companyfacts` is the primary source: it is authoritative, free, stable
and its figures were verified to match the existing workbook exactly (MSFT
revenue 331,839,000,000 / EPS 17.95 / assets 758,376,000,000).

`akshare` (Eastmoney financial statements) is the fallback for issuers that do
not file with the SEC or whose tags are unusable.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from app.config import settings
from app.http import FetchError, fetch
from app.models import FactSeries, Fundamentals

log = logging.getLogger(__name__)

SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

_ANNUAL_FORMS = ("10-K", "20-F", "40-F")

# Ordered tag preferences — first usable series wins.
FLOW_TAGS: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ],
    "gross_profit": ["GrossProfit"],
    "cost_of_revenue": [
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsAndServicesSoldIncludingDepreciationDepletionAndAmortization",
        "CostOfGoodsSold",
        # Oracle and other license/support filers use these instead.
        "CostOfServices",
        "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization",
    ],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        # Qualcomm and others use the broader "productive assets" tag instead.
        "PaymentsToAcquireProductiveAssets",
        "PaymentsToAcquireOtherProductiveAssets",
    ],
    "sbc": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
    "restructuring": ["RestructuringCharges"],
    "amortization": ["AmortizationOfIntangibleAssets"],
    "depreciation_amortization": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationDepletionAndAmortizationPropertyPlantAndEquipment",
        "Depreciation",
    ],
    "tax_provision": ["IncomeTaxExpenseBenefit"],
    "pretax_income": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ],
    # Non-GAAP reconciliation inputs. Filers do not tag a comparable "non-GAAP
    # EPS": each discloses its own adjustments, so the bridge is assembled from
    # the line items a company actually reports and the rest are shown as
    # unavailable rather than assumed to be zero.
    "equity_securities_gain": [
        "EquitySecuritiesFvNiUnrealizedGainLoss",
        "EquitySecuritiesFvNiRealizedGainLoss",
        "EquitySecuritiesFvNiGainLoss",
        "MarketableSecuritiesRealizedGainLoss",
        "GainLossOnSaleOfInvestments",
    ],
    "other_nonoperating_income": ["OtherNonoperatingIncomeExpense"],
    "legal_settlement": [
        "LitigationSettlementExpense",
        "LossContingencyLossInPeriod",
        "LegalSettlementExpense",
    ],
    "impairment": [
        "GoodwillImpairmentLoss",
        "AssetImpairmentCharges",
        "ImpairmentOfIntangibleAssetsExcludingGoodwill",
    ],
    "acquisition_costs": [
        "BusinessCombinationAcquisitionRelatedCosts",
        "AcquisitionRelatedCosts",
    ],
    "debt_extinguishment": ["GainsLossesOnExtinguishmentOfDebt"],
    "discontinued_operations": ["IncomeLossFromDiscontinuedOperationsNetOfTax"],
}

INSTANT_TAGS: dict[str, list[str]] = {
    "equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "assets": ["Assets"],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "debt_current": [
        "ShortTermBorrowings",
        "LongTermDebtCurrent",
        "LongTermDebtAndCapitalLeaseObligationsCurrent",
        "CommercialPaper",
        "NotesPayableCurrent",
        "DebtCurrent",
    ],
    "debt_long": [
        "LongTermDebtNoncurrent",
        "LongTermDebtAndCapitalLeaseObligations",
        "LongTermNotesPayable",
        "LongTermDebt",
        "ConvertibleDebtNoncurrent",
    ],
}

UNITS: dict[str, list[str]] = {
    "eps_diluted": ["USD/shares"],
    "shares_diluted": ["shares"],
}

# --------------------------------------------------------------------------- #
# IFRS taxonomy (`ifrs-full`), used by foreign private issuers such as TSM that
# file 20-F instead of 10-K. Tag names differ substantially from US-GAAP, so
# they get their own mapping rather than being forced through the GAAP lists.
# --------------------------------------------------------------------------- #
IFRS_FLOW_TAGS: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractsWithCustomers",
        "Revenue",
        "RevenueAndOperatingIncome",
    ],
    "gross_profit": ["GrossProfit"],
    "cost_of_revenue": ["CostOfSales", "CostOfRevenue"],
    "operating_income": [
        "ProfitLossFromOperatingActivities",
        "ProfitLossFromContinuingOperations",
    ],
    "net_income": ["ProfitLoss", "ProfitLossAttributableToOwnersOfParent"],
    "operating_cash_flow": [
        "CashFlowsFromUsedInOperatingActivities",
        "CashFlowsFromUsedInOperatingActivitiesBeforeIncomeTax",
    ],
    "capex": [
        "PurchaseOfPropertyPlantAndEquipment",
        "PurchaseOfPropertyPlantAndEquipmentIntangibleAssetsOtherThanGoodwill",
        "AcquisitionOfPropertyPlantAndEquipment",
        "PaymentsToAcquirePropertyPlantAndEquipment",
    ],
    "depreciation_amortization": [
        "DepreciationAndAmortisationExpense",
        "DepreciationExpense",
    ],
    "sbc": ["ShareBasedPaymentExpense", "ExpenseFromSharebasedPaymentTransactionsWithEmployees"],
    "tax_provision": [
        "IncomeTaxExpenseContinuingOperations",
        "CurrentTaxExpenseIncomeAndOtherComponentsOfIncomeTaxExpense",
        "TaxExpenseIncome",
    ],
    "pretax_income": ["ProfitLossBeforeTax", "AccountingProfit"],
    # IFRS equivalents for the non-GAAP bridge.
    "equity_securities_gain": [
        "GainsLossesOnFinancialAssetsAtFairValueThroughProfitOrLoss",
        "OtherGainsLosses",
    ],
    "restructuring": ["ExpenseOfRestructuringActivities", "RestructuringExpense"],
    "amortization": [
        "AmortisationExpense",
        "AmortisationOfIntangibleAssetsOtherThanGoodwill",
    ],
    "impairment": ["ImpairmentLoss", "ImpairmentLossRecognisedInProfitOrLoss"],
    "legal_settlement": ["LitigationSettlementExpense"],
}

IFRS_INSTANT_TAGS: dict[str, list[str]] = {
    # EquityAndLiabilities is the TOTAL balance sheet, never shareholders' equity.
    "equity": ["EquityAttributableToOwnersOfParent", "Equity"],
    "assets": ["Assets"],
    "cash": ["CashAndCashEquivalents", "CashAndCashEquivalentsAndShortTermInvestments"],
    "debt_current": [
        "CurrentPortionOfLongtermBorrowings",
        "ShorttermBorrowings",
        "CurrentBorrowings",
    ],
    "debt_long": ["LongtermBorrowings", "NoncurrentPortionOfLongtermBorrowings"],
}

IFRS_EPS_TAGS = ["DilutedEarningsLossPerShare", "BasicEarningsLossPerShare"]
IFRS_SHARES_TAGS = ["WeightedAverageNumberOfOrdinarySharesOutstandingDiluted"]


# --------------------------------------------------------------------------- #
# Ticker -> CIK map (cached for a day)
# --------------------------------------------------------------------------- #
def load_ticker_map() -> dict[str, dict]:
    try:
        raw = fetch(
            SEC_TICKERS,
            headers={
                "User-Agent": settings.sec_user_agent,
                "Accept-Encoding": "gzip, deflate",
            },
            namespace="sec_map",
            ttl=settings.cache_ttl_secmap,
            expect_json=True,
            retries=3,
        )
    except FetchError as exc:
        log.warning("SEC ticker map unavailable: %s", exc)
        return {}
    out: dict[str, dict] = {}
    for entry in (raw or {}).values():
        try:
            symbol = str(entry.get("ticker", "")).upper()
            if symbol:
                out[symbol] = {
                    "cik": int(entry["cik_str"]),
                    "title": str(entry.get("title", "")),
                }
        except (KeyError, TypeError, ValueError):
            continue
    return out


def lookup_cik(ticker: str) -> int | None:
    entry = load_ticker_map().get(ticker.upper())
    return entry["cik"] if entry else None


# --------------------------------------------------------------------------- #
# Extraction helpers
# --------------------------------------------------------------------------- #
def _dedupe_latest_filing(points: list[dict]) -> list[dict]:
    """For each period end keep the most recently filed value."""
    best: dict[str, dict] = {}
    for p in points:
        key = p["end"]
        if key not in best or str(p.get("filed", "")) > str(best[key].get("filed", "")):
            best[key] = p
    return sorted(best.values(), key=lambda x: x["end"], reverse=True)


def _flow_series(gaap: dict, tags: list[str], unit: str = "USD") -> FactSeries | None:
    """Annual income/cash-flow facts: needs a start date and ~1 year duration.

    Selection is by *period*, not by tag order. US issuers routinely migrate
    tags (Alphabet tagged FY2025 revenue as `Revenues` while earlier years used
    `RevenueFromContractWithCustomerExcludingAssessedTax`), so taking the first
    tag that yields any series silently returns a stale year. Instead every
    candidate tag is scanned and the series containing the newest period wins.
    """
    candidates: list[tuple[str, str, list[tuple[date, float]]]] = []
    by_period: dict[date, tuple[str, float]] = {}
    for tag in tags:
        node = gaap.get(tag)
        if not node:
            continue
        available_units = [unit] if unit else list(node.get("units", {}))
        for u in available_units:
            points = node.get("units", {}).get(u) or []
            rows = []
            for p in points:
                if p.get("form") not in _ANNUAL_FORMS or p.get("fp") != "FY":
                    continue
                if p.get("val") is None or not p.get("start") or not p.get("end"):
                    continue
                try:
                    span = (date.fromisoformat(p["end"]) - date.fromisoformat(p["start"])).days
                except ValueError:
                    continue
                if 330 <= span <= 380:
                    rows.append(p)
            if not rows:
                continue
            series = [(date.fromisoformat(p["end"]), float(p["val"]))
                      for p in _dedupe_latest_filing(rows)]
            if series:
                candidates.append((tag, u, series))

    if not candidates:
        return None
    # The newest tag often has only one year (e.g. a migrated revenue tag).
    # Merge equivalent tags by fiscal end, preferring the caller's tag order
    # for overlapping dates instead of discarding all older observations.
    for tag, u, series in candidates:
        for end, value in series:
            by_period.setdefault(end, (tag, value))
    newest_tag = by_period[max(by_period)][0]
    return FactSeries(tag=newest_tag, unit=candidates[0][1],
                      points=sorted(((d, v) for d, (_, v) in by_period.items()), reverse=True))


def _instant_series(gaap: dict, tags: list[str], unit: str = "USD") -> FactSeries | None:
    """Balance-sheet facts: a point in time, so no `start` is present.

    As with `_flow_series`, the series holding the newest period wins across all
    candidate tags so a stale tag cannot mask the current balance sheet.
    """
    candidates: list[tuple[str, str, list[tuple[date, float]]]] = []
    for tag in tags:
        node = gaap.get(tag)
        if not node:
            continue
        available_units = [unit] if unit else list(node.get("units", {}))
        for u in available_units:
            points = node.get("units", {}).get(u) or []
            rows = [
                p
                for p in points
                if p.get("form") in _ANNUAL_FORMS and not p.get("start") and p.get("val") is not None
            ]
            if not rows:
                continue
            series = [
                (date.fromisoformat(p["end"]), float(p["val"]))
                for p in _dedupe_latest_filing(rows)
            ]
            if series:
                candidates.append((tag, u, series))

    if not candidates:
        return None
    by_period: dict[date, tuple[str, float]] = {}
    for tag, u, series in candidates:
        for end, value in series:
            by_period.setdefault(end, (tag, value))
    newest_tag = by_period[max(by_period)][0]
    return FactSeries(tag=newest_tag, unit=candidates[0][1],
                      points=sorted(((d, v) for d, (_, v) in by_period.items()), reverse=True))


def _latest_dei_shares(facts: dict) -> float | None:
    node = (facts.get("dei") or {}).get("EntityCommonStockSharesOutstanding")
    if not node:
        return None
    best: tuple[str, float] | None = None
    for u, points in (node.get("units") or {}).items():
        for p in points:
            if p.get("form") not in _ANNUAL_FORMS + ("10-Q",) or p.get("val") is None:
                continue
            end = str(p.get("end", ""))
            if best is None or end > best[0]:
                best = (end, float(p["val"]))
    return best[1] if best else None


# --------------------------------------------------------------------------- #
# SEC provider
# --------------------------------------------------------------------------- #
def _detect_currency(node_source: dict, flow_tags: dict, instant_tags: dict) -> str:
    """Infer the reporting currency of a taxonomy.

    Only *monetary* tags are inspected. Scanning arbitrary tags is wrong: XBRL
    unit keys also include things like `Year`, `Store` and `instrument`, which
    would masquerade as a currency (this previously mislabelled Apple as
    reporting in "Year" and tripped the cross-currency guard).
    """
    monetary_flow = ("revenue", "net_income", "operating_income", "assets", "equity", "cash")
    for group in (flow_tags, instant_tags):
        for field in monetary_flow:
            for tag in group.get(field, []):
                node = node_source.get(tag)
                if not node:
                    continue
                for unit in (node.get("units") or {}):
                    if unit and unit not in ("shares", "pure") and "/" not in unit:
                        return unit
    return "USD"


def get_sec_fundamentals(ticker: str) -> Fundamentals | None:
    cik = lookup_cik(ticker)
    if cik is None:
        log.info("%s is not in the SEC issuer map", ticker)
        return None
    url = SEC_FACTS.format(cik=cik)
    facts = fetch(
        url,
        headers={
            "User-Agent": settings.sec_user_agent,
            "Accept-Encoding": "gzip, deflate",
        },
        namespace="sec_facts",
        ttl=settings.cache_ttl_fundamentals,
        expect_json=True,
        timeout=60,
        retries=4,
    )
    all_facts = facts.get("facts") or {}
    gaap = all_facts.get("us-gaap") or {}
    ifrs = all_facts.get("ifrs-full") or {}

    # Prefer US-GAAP when present; foreign private issuers file IFRS only.
    if gaap:
        taxonomy, flow_tags, instant_tags = "us-gaap", FLOW_TAGS, INSTANT_TAGS
        eps_tags, shares_tags = ["EarningsPerShareDiluted"], [
            "WeightedAverageNumberOfDilutedSharesOutstanding"
        ]
        taxonomy_label = "US-GAAP"
    elif ifrs:
        taxonomy, flow_tags, instant_tags = "ifrs-full", IFRS_FLOW_TAGS, IFRS_INSTANT_TAGS
        eps_tags, shares_tags = IFRS_EPS_TAGS, IFRS_SHARES_TAGS
        taxonomy_label = "IFRS"
    else:
        log.info("%s (CIK %s) has no us-gaap or ifrs-full facts", ticker, cik)
        return None

    node_source = gaap if taxonomy == "us-gaap" else ifrs
    currency = _detect_currency(node_source, flow_tags, instant_tags)

    fund = Fundamentals(
        ticker=ticker,
        entity_name=facts.get("entityName"),
        currency=currency,
        source=f"SEC XBRL ({taxonomy_label})",
        source_url=url,
        cik=cik,
    )
    for field, tags in flow_tags.items():
        setattr(fund, field, _flow_series(node_source, tags, unit=currency))
    for field, tags in instant_tags.items():
        setattr(fund, field, _instant_series(node_source, tags, unit=currency))

    # EPS is per ordinary share for TSM, not per US-listed depositary share.
    eps_unit = f"{currency}/shares" if taxonomy == "ifrs-full" else "USD/shares"
    fund.eps_diluted = _flow_series(node_source, eps_tags, unit=eps_unit)
    if fund.eps_diluted is None:
        fund.eps_diluted = _flow_series(node_source, eps_tags, unit=None)
    fund.shares_diluted = _flow_series(node_source, shares_tags, unit="shares")
    # DEI tags live under companyfacts["facts"]["dei"], alongside us-gaap.
    fund.shares_outstanding = _latest_dei_shares(all_facts)
    if fund.shares_outstanding:
        fund.shares_basis = "Latest common shares (SEC DEI)"

    # Anchor on the newest fiscal period present in any statement series, so a
    # stale tag on one line item cannot pull the row back a year. Revenue leads
    # because it is the primary driver; other series may legitimately end
    # slightly later (e.g. a balance sheet dated a few weeks after the P&L).
    anchor_candidates = [
        s.latest_end()
        for s in (
            fund.revenue,
            fund.net_income,
            fund.eps_diluted,
            fund.operating_income,
            fund.assets,
        )
        if s is not None and s.points
    ]
    if anchor_candidates:
        fund.fiscal_end = max(anchor_candidates)

    if not fund.is_usable:
        log.info("%s: SEC facts incomplete (no revenue or operating income)", ticker)
    return fund


# --------------------------------------------------------------------------- #
# akshare fallback (Eastmoney financial statements)
# --------------------------------------------------------------------------- #
_AK_ITEM_MAP = {
    "revenue": ["主营收入", "营业收入", "营业总收入"],
    "gross_profit": ["毛利"],
    "operating_income": ["营业利润", "经营溢利"],
    "net_income": ["净利润", "归属于母公司股东的净利润"],
    "assets": ["总资产"],
    "equity": ["股东权益合计", "归属于母公司股东权益合计", "总权益"],
    "cash": ["现金及现金等价物"],
    "operating_cash_flow": ["经营活动产生的现金流量净额", "经营业务现金净额"],
    "capex": ["购建固定资产、无形资产和其他长期资产支付的现金"],
    "eps_diluted": ["基本每股收益", "稀释每股收益"],
    "tax_provision": ["所得税费用"],
}


def _ak_frames(ticker: str) -> dict[str, list[dict]]:
    import akshare as ak

    out: dict[str, list[dict]] = {}
    for sheet, key in (
        ("综合损益表", "income"),
        ("现金流量表", "cashflow"),
        ("资产负债表", "balance"),
    ):
        try:
            df = ak.stock_financial_us_report_em(stock=ticker, symbol=sheet, indicator="年报")
        except Exception as exc:
            log.debug("akshare %s %s failed: %s", ticker, sheet, exc)
            continue
        if df is None or df.empty:
            continue
        out[key] = df.to_dict("records")
    return out


def _ak_series(records: list[dict], names: list[str]) -> FactSeries | None:
    if not records:
        return None
    for name in names:
        rows = [r for r in records if str(r.get("ITEM_NAME", "")).strip() == name]
        if not rows:
            continue
        by_end: dict[date, float] = {}
        for r in rows:
            try:
                end = datetime.fromisoformat(str(r["REPORT_DATE"])[:19]).date()
                val = float(r["AMOUNT"])
            except (KeyError, TypeError, ValueError):
                continue
            by_end[end] = val
        points = sorted(by_end.items(), key=lambda kv: kv[0], reverse=True)
        if points:
            return FactSeries(tag=f"akshare:{name}", unit="USD", points=points)
    return None


def get_akshare_fundamentals(ticker: str) -> Fundamentals | None:
    try:
        frames = _ak_frames(ticker)
    except ImportError:
        return None
    if not frames:
        return None

    fund = Fundamentals(
        ticker=ticker,
        currency="USD",
        source="akshare/Eastmoney financial statements",
        source_url="https://emweb.securities.eastmoney.com/",
    )
    all_records = [r for rows in frames.values() for r in rows]
    for field, names in _AK_ITEM_MAP.items():
        series = _ak_series(all_records, names)
        if series:
            setattr(fund, field, series)

    for series in (fund.revenue, fund.net_income, fund.eps_diluted):
        if series and series.points:
            fund.fiscal_end = series.latest_end()
            break
    return fund if fund.is_usable else None


# --------------------------------------------------------------------------- #
def get_fundamentals(ticker: str, *, allow_yahoo: bool = False) -> Fundamentals:
    """Resolve SEC facts; add aligned Yahoo gaps when reachable; then alternatives."""
    ticker = ticker.upper().strip()
    errors: list[str] = []
    sec = None

    try:
        sec = get_sec_fundamentals(ticker)
        if sec and sec.is_usable:
            if ticker == "TSM":
                from app.providers.tsm_official import fill_tsm_cash_flow
                sec = fill_tsm_cash_flow(sec)
            if allow_yahoo and sec.currency in ("USD", "TWD"):
                from app.providers.yahoo_fundamentals import fill_missing, get_yahoo_fundamentals

                # Yahoo's financial currency for foreign ADRs can differ from
                # the listing currency. Only merge verified USD filing data.
                try:
                    yahoo_symbol = "2330.TW" if ticker == "TSM" and sec.currency == "TWD" else ticker
                    yahoo = get_yahoo_fundamentals(yahoo_symbol, currency=sec.currency)
                    if yahoo:
                        if yahoo_symbol != ticker:
                            yahoo.ticker = ticker
                        sec = fill_missing(sec, yahoo)
                except Exception as exc:
                    log.info("Yahoo gap fill unavailable for %s: %s", ticker, exc)
            return sec
        errors.append("SEC: incomplete")
    except Exception as exc:
        errors.append(f"SEC: {exc}")
        log.info("SEC fundamentals failed for %s: %s", ticker, exc)

    if allow_yahoo and (ticker != "TSM" or sec is None or sec.currency == "TWD"):
        from app.providers.yahoo_fundamentals import get_yahoo_fundamentals

        try:
            yahoo_symbol = "2330.TW" if ticker == "TSM" else ticker
            yahoo = get_yahoo_fundamentals(yahoo_symbol, currency="TWD" if ticker == "TSM" else "USD")
            if yahoo and yahoo.is_usable:
                yahoo.ticker = ticker
                return yahoo
            errors.append("Yahoo: incomplete")
        except Exception as exc:
            errors.append(f"Yahoo: {exc}")

    if settings.enable_akshare:
        try:
            ak_fund = get_akshare_fundamentals(ticker)
            if ak_fund and ak_fund.is_usable:
                return ak_fund
            errors.append("akshare: incomplete")
        except Exception as exc:
            errors.append(f"akshare: {exc}")

    raise FetchError(f"no fundamentals for {ticker} ({'; '.join(errors)})")
