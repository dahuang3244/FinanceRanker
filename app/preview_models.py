"""Data models for the akshare-backed US-stock parameter preview."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class ParamSpec(BaseModel):
    """One Excel parameter and how it is derived."""

    key: str
    label: str
    excel_ref: str = ""          # e.g. "Data Cache!L" or "Data Cache!N"
    category: str = "other"
    unit: str = ""
    formula: str = ""
    note: str = ""


class ParamValue(BaseModel):
    """A parameter resolved for one ticker."""

    key: str
    label: str
    excel_ref: str = ""
    category: str = "other"
    unit: str = ""
    formula: str = ""
    value: float | str | None = None
    display: str = ""
    source: str = ""             # which akshare interface provided the inputs
    status: str = "ok"           # ok | missing | not_in_akshare | manual
    note: str = ""


class ParamGroup(BaseModel):
    category: str
    title: str
    items: list[ParamValue] = Field(default_factory=list)


class FinancialLine(BaseModel):
    """A raw statement line item, kept verbatim for transparency."""

    item: str
    values: dict[str, float | None] = Field(default_factory=dict)  # fiscal-year label -> amount


class RawStatements(BaseModel):
    income: list[FinancialLine] = Field(default_factory=list)
    cashflow: list[FinancialLine] = Field(default_factory=list)
    balance: list[FinancialLine] = Field(default_factory=list)


class RatioLine(BaseModel):
    """Pre-computed ratio straight from akshare's analysis-indicator interface."""

    key: str
    label: str
    values: dict[str, float | None] = Field(default_factory=dict)
    unit: str = ""


class PriceBar(BaseModel):
    d: date
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None


class TickerInfo(BaseModel):
    ticker: str
    name: str = ""
    exchange: str = ""
    cik: int | None = None
    source: str = ""


class PreviewPayload(BaseModel):
    """Everything the preview page renders for one ticker."""

    ticker: str
    company: str = ""
    generated_at: datetime
    fiscal_years: list[str] = Field(default_factory=list)
    currency: str = "USD"

    groups: list[ParamGroup] = Field(default_factory=list)
    raw: RawStatements = Field(default_factory=RawStatements)
    ratios: list[RatioLine] = Field(default_factory=list)
    prices: list[PriceBar] = Field(default_factory=list)

    sources: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @property
    def all_items(self) -> list[ParamValue]:
        return [item for group in self.groups for item in group.items]
