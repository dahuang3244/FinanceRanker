"""The metric catalogue must stay consistent with what is scored and displayed.

The bug this prevents: the company detail view carried its own list of metrics
keyed by name. When the scoring universe changed, the UI kept rendering metrics
the backend had stopped scoring and showed an **empty score column** next to
them — indistinguishable from a metric that scored badly. These tests pin the
invariants that make that impossible.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engine import scoring
from app.models import MetricRow

JS_DIR = Path(__file__).resolve().parent.parent / "app" / "web" / "static" / "js"


def _catalog() -> list[dict]:
    return scoring.metric_catalog()


def test_every_catalogue_entry_has_a_label_and_a_render_kind():
    for entry in _catalog():
        assert entry["label"] and entry["label"] != entry["attr"], entry
        assert entry["kind"] in ("ratio", "multiple", "money", "num"), entry
        assert entry["component"] in ("growth", "profitability", "cash", "valuation", "market"), entry
        if not entry["scored"]:
            # An unscored metric must explain itself, because that explanation is
            # what the UI shows instead of a blank score.
            assert entry["note"], f"reference metric {entry['attr']} has no reason"


def test_scored_entries_match_the_scoring_universe_exactly():
    catalog = _catalog()
    scored = {e["attr"] for e in catalog if e["scored"]}
    assert scored == {attr for attr, _, _ in scoring.METRICS}
    reference = {e["attr"] for e in catalog if not e["scored"]}
    assert reference == scoring.reference_attrs()
    # No metric may be both, and none may be missing from both.
    assert not (scored & reference)
    assert len(catalog) == len(scored) + len(reference)


def test_every_metric_a_row_can_display_is_in_the_catalogue():
    """A field the engine populates but the catalogue omits is invisible drift."""
    # Market/risk and the added fundamental indicators are the ones that have
    # historically drifted, so they are asserted explicitly.
    must_be_catalogued = [
        "return_1m", "return_3m", "return_6m", "return_1y", "return_ytd", "return_3y",
        "excess_return_1m", "excess_return_3m", "excess_return_6m", "excess_return_1y",
        "excess_return_ytd", "volatility", "downside_deviation", "sharpe_ratio",
        "sortino_ratio", "beta", "beta_1y", "max_drawdown_1y", "drawdown_52w",
        "revenue_cagr_3y", "eps_growth_yoy", "net_income_growth_yoy",
        "gross_profit_growth_yoy", "fcf_growth_yoy", "roa", "asset_turnover",
        "operating_leverage", "capex_intensity", "cash_to_assets", "sbc_pct_revenue",
        "price_to_ocf", "ev_to_sales", "peg_ratio", "net_debt_to_ebitda",
    ]
    catalogued = {e["attr"] for e in _catalog()}
    missing = [a for a in must_be_catalogued if a not in catalogued]
    assert not missing, f"populated but not catalogued: {missing}"
    # And each one exists as a real field on the row model.
    fields = set(MetricRow.model_fields)
    assert not [a for a in must_be_catalogued if a not in fields]


def test_reference_metrics_have_no_z_field_and_scored_ones_do():
    for entry in _catalog():
        attr = entry["attr"]
        if entry["scored"]:
            assert attr in scoring.Z_FIELDS, f"{attr} is scored but has no z-field"
        else:
            # beta is the historical exception: a legacy z_beta column survives
            # for old snapshots, but it is deliberately not written any more.
            if attr != "beta":
                assert attr not in scoring.Z_FIELDS, f"{attr} is unscored but has a z-field"


def test_scoring_only_writes_z_fields_for_catalogued_scored_metrics():
    rows = []
    for i, ticker in enumerate(("AAA", "BBB", "CCC")):
        row = MetricRow(ticker=ticker)
        for attr, _, _ in scoring.METRICS:
            setattr(row, attr, float(i + 1) * (1 if attr != "capex_intensity" else -1))
        rows.append(row)
    scoring.score_peers(rows)
    scored_z = {scoring.Z_FIELDS[a] for a, _, _ in scoring.METRICS}
    for row in rows:
        for field in MetricRow.model_fields:
            if not field.startswith("z_"):
                continue
            value = getattr(row, field)
            if value is not None:
                assert field in scored_z, f"{field} written but not a scored metric"


def test_frontend_does_not_carry_its_own_metric_list():
    """The UI must build its blocks from the catalogue, not a local list."""
    src = (JS_DIR / "company-view.js").read_text(encoding="utf-8")
    # No literal metric attribute may be listed as an object key any more.
    offenders = re.findall(r"\{\s*attr:\s*\"([a-z_0-9]+)\"", src)
    assert not offenders, f"company-view.js still lists metrics locally: {offenders}"
    assert "api.metrics()" in src, "company-view.js must fetch the metric catalogue"
    assert "higher_is_better" in src, "company-view.js must read the catalogue's direction flag"
    # The substitution rule must be read from the catalogue, not reimplemented.
    assert "basis_fields" in src, "company-view.js must read basis_fields from the catalogue"
    assert "cagr_basis" not in src, "company-view.js must not hardcode provenance field names"


def test_catalogue_publishes_basis_fields_for_gated_metrics():
    """Every metric the engine may substitute must say which field flags it."""
    catalog = {e["attr"]: e for e in _catalog()}
    for attr, rule in scoring.BASIS_GATED_METRICS.items():
        assert attr in catalog, f"{attr} is basis-gated but not catalogued"
        assert catalog[attr]["basis_fields"] == [rule["field"]], attr
    assert catalog["gross_margin"]["basis_fields"] == []


def test_substituted_values_are_withheld_from_scoring_but_kept_on_the_row():
    """A substitute is displayed and skipped, never ranked against real values.

    A loss-making company has no EPS CAGR, so the engine reports the annualised
    absolute change instead. That is a different unit from every peer's
    percentage, so including it would corrupt the percentile — but dropping the
    value entirely would hide the most important fact about the company.
    """
    rows = []
    for i, ticker in enumerate(("LOSS", "GROW1", "GROW2")):
        row = MetricRow(ticker=ticker)
        for attr, _, _ in scoring.METRICS:
            setattr(row, attr, float(i + 1) * 0.05)
        rows.append(row)
    rows[0].eps_cagr_5y = -19.6
    rows[0].cagr_basis = "EPS CAGR: annualised absolute change over 5y (sign flip)"
    rows[1].eps_cagr_5y = 0.18
    rows[2].eps_cagr_5y = 0.22
    scoring.score_peers(rows)

    assert scoring.is_substituted(rows[0], "eps_cagr_5y") is True
    assert scoring.is_substituted(rows[1], "eps_cagr_5y") is False
    # Displayed, but not scored.
    z_field = scoring.Z_FIELDS["eps_cagr_5y"]
    assert rows[0].eps_cagr_5y == -19.6
    assert getattr(rows[0], z_field) is None
    assert getattr(rows[1], z_field) is not None


def test_strategy_presets_are_complete_and_reweight_without_rescoring():
    """Every preset covers all components, sums to 1, and switching it re-ranks
    the same percentile scores rather than recomputing the evidence."""
    from app.engine.scoring import COMPONENT_FIELDS, STRATEGY_PRESETS, preset_weights

    for key, preset in STRATEGY_PRESETS.items():
        weights = preset["weights"]
        assert set(weights) == set(COMPONENT_FIELDS), key
        assert abs(sum(weights.values()) - 1.0) < 1e-9, key
        assert preset["label"]["zh"] and preset["label"]["en"], key
        assert preset["blurb"]["zh"] and preset["blurb"]["en"], key
    assert preset_weights("nonsense") == preset_weights(None), "unknown strategy must fall back"

    def build():
        rows = []
        for i, ticker in enumerate(("AAA", "BBB", "CCC")):
            row = MetricRow(ticker=ticker)
            for attr, _, _ in scoring.METRICS:
                setattr(row, attr, float(i + 1))
            rows.append(row)
        return rows

    balanced = build()
    scoring.score_peers(balanced, strategy="balanced")
    momentum = build()
    scoring.score_peers(momentum, strategy="momentum")
    for a, b in zip(balanced, momentum):
        assert a.z_growth_yoy == b.z_growth_yoy
        assert a.z_sharpe == b.z_sharpe
    assert balanced[0].score_overall != momentum[0].score_overall


def test_market_component_is_a_performance_risk_blend():
    from app.engine.scoring import MARKET_BLEND, MARKET_SUBWEIGHTS

    scored = {attr for attr, comp, _ in scoring.METRICS if comp == "market"}
    covered = set(MARKET_SUBWEIGHTS["performance"]) | set(MARKET_SUBWEIGHTS["risk"])
    assert covered == scored, f"market sub-weights miss: {scored - covered}"
    assert abs(sum(MARKET_BLEND.values()) - 1.0) < 1e-9
    for sub, weights in MARKET_SUBWEIGHTS.items():
        assert abs(sum(weights.values()) - 1.0) < 1e-9, sub
    # Performance must lead, or the component is a risk screen in disguise.
    assert MARKET_BLEND["performance"] > MARKET_BLEND["risk"]


def test_frontend_labels_exist_for_every_catalogued_metric():
    i18n = (JS_DIR / "i18n.js").read_text(encoding="utf-8")
    missing = []
    for entry in _catalog():
        key = f'"m.{entry["attr"]}":'
        if key not in i18n:
            missing.append(entry["attr"])
            continue
        at = i18n.index(key)
        block = i18n[at:i18n.index("]", at)]
        if not re.search(r'"[^"]+?"\s*,\s*"[^"]+?"', block):
            missing.append(f"{entry['attr']} (not bilingual)")
    assert not missing, f"missing zh+en labels: {missing}"


def test_detail_export_never_leaves_a_score_cell_blank_and_silent():
    """The CSV/Excel detail export must say 'not scored', not leave a gap."""
    from app.exporters.detail_export import detail_rows_to_csv
    from app.engine.scoring import METRICS

    row = MetricRow(ticker="AAA")
    for i, (attr, _, _) in enumerate(METRICS):
        setattr(row, attr, float(i + 1))
    # A populated reference metric: the case that used to render as a blank.
    row.beta = 1.4
    row.return_1m = 0.03
    text = detail_rows_to_csv([row], row)
    assert "not scored" in text, "reference-only metrics must be marked, not left blank"
    assert "reference only" in text, "the Scored column must state the treatment"
    # A scored metric with a value must carry a number, never the marker.
    for line in text.splitlines():
        if line.startswith("ROA,") or line.startswith("Gross margin,"):
            assert "not scored" not in line, line


def test_configured_env_weights_become_a_selectable_preset():
    """A blend set in `.env` must stay selectable, not be silently ignored."""
    from unittest.mock import patch

    from app.config import settings
    from app.engine.scoring import all_presets, default_strategy, preset_weights

    # `.env` at its defaults matches the balanced preset, so nothing is overridden.
    assert default_strategy() == "balanced"
    assert all_presets()["custom"]["differs_from_default"] is False
    assert preset_weights("custom") == preset_weights("balanced")

    # A genuinely different configured blend becomes the default, so a reader
    # sees the ranking they configured rather than one that quietly ignored it.
    with patch.object(settings, "w_market", 0.60), \
         patch.object(settings, "w_valuation", 0.05):
        assert default_strategy() == "custom"
        assert all_presets()["custom"]["differs_from_default"] is True
        assert preset_weights("custom")["market"] == 0.60
        # And the named presets are unaffected.
        assert preset_weights("balanced")["market"] == 0.20

    # An unknown strategy resolves to whatever the default is.
    assert preset_weights("nope") == preset_weights(default_strategy())


if __name__ == "__main__":
    import traceback

    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception:
                failed += 1
                print(f"FAIL  {name}")
                traceback.print_exc()
            else:
                passed += 1
                print(f"PASS  {name}")
    print(f"\n{passed}/{passed + failed} passed")
    raise SystemExit(1 if failed else 0)
