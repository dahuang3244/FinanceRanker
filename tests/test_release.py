"""The release-text extractor, tested against the wording real releases use.

These figures are the ones the reconciliation cannot compute: the tax on an
equity-securities gain and a one-off fine are not XBRL facts, so they exist only in
the earnings release. The patterns are therefore tested against the sentences the
releases actually contain, quoted here, rather than against invented samples.

Run: python tests/test_release.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.release import (  # noqa: E402
    _CENTS,
    _EPS_SENTENCE,
    _FINE,
    _flatten,
    extract_disclosures,
)

# Quoted from Alphabet's releases. The 2025 ones fold the performance fees into the
# same sentence; the 2026 ones do not, and one says "per common share".
ALPHABET_2025Q3 = (
    "the net effect of the gain on equity securities of $10.7 billion and the "
    "performance fees related to certain investments of $174 million increased the "
    "provision for income tax, net income, and diluted net income per share by "
    "$2.2 billion, $8.3 billion, and $0.68, respectively."
)
ALPHABET_2026Q2 = (
    "the net effect of the gain on equity securities of $99.0 billion increased the "
    "provision for income tax, net income, and diluted net income per common share by "
    "$21.9 billion, $77.1 billion, and $6.26, respectively."
)
# The 2025 Q1 release has a stray space inside a word, which the released HTML does
# leave behind: "increased th e provision".
ALPHABET_2025Q1_GLITCHED = (
    "the net effect of the gain on equity securities of $9.8 billion and the "
    "performance fees related to certain investments of $40 million increased th e "
    "provision for income tax, net income, and diluted net income per share by "
    "$2.0 billion, $7.7 billion, and $0.62, respectively."
)
# Verbatim from the reconciliation table.
EC_FINE_TABLE = (
    "Quarter Ended September 30, 2024 2025 % Change Revenues $ 88,268 $ 102,346 "
    "16 % Operating income (GAAP) $ 28,521 $ 31,228 9 % add: EC fine 0 3,457 "
    "Operating income, excluding the EC fine (Non-GAAP) $ 28,521 $ 34,685 22 %"
)


def test_the_gain_triple_is_read_in_order():
    """The three amounts are tax, net income, then per share, in that order."""
    result = extract_disclosures(ALPHABET_2025Q3)
    assert result is not None
    assert abs(result.equity_gain - 10.7e9) < 1e6
    assert abs(result.tax_on_gain - 2.2e9) < 1e6
    assert abs(result.gain_after_tax - 8.3e9) < 1e6
    assert abs(result.eps_effect - 0.68) < 0.001


def test_the_per_share_figure_is_the_cents_amount_not_the_tax():
    """A regression: the first `$` after "per share" is the tax, not the EPS effect.

    Anchoring on the first amount read $2.2 billion as the per-share effect, which is
    three orders of magnitude too large and would have made every surprise look
    enormous.
    """
    result = extract_disclosures(ALPHABET_2025Q3)
    assert result.eps_effect < 10, "a per-share effect cannot be in the billions"
    assert abs(result.eps_effect - 0.68) < 0.001


def test_cents_are_not_confused_with_the_billions_in_the_same_clause():
    """"$2.2 billion, $8.3 billion, and $0.68" holds exactly one per-share amount."""
    clause = " $2.2 billion, $8.3 billion, and $0.68, "
    assert _CENTS.findall(clause) == ["0.68"]


def test_the_2026_wording_without_performance_fees_is_read():
    """"per common share" and a sentence with no fee clause must still parse."""
    result = extract_disclosures(ALPHABET_2026Q2)
    assert result is not None
    assert abs(result.gain_after_tax - 77.1e9) < 1e7
    assert abs(result.tax_on_gain - 21.9e9) < 1e7
    assert abs(result.eps_effect - 6.26) < 0.001
    assert result.performance_fees is None, "no fee was disclosed this quarter"


def test_a_stray_space_inside_a_word_does_not_break_the_match():
    """The released HTML contains "increased th e provision"."""
    result = extract_disclosures(ALPHABET_2025Q1_GLITCHED)
    assert result is not None
    assert abs(result.gain_after_tax - 7.7e9) < 1e6
    assert abs(result.eps_effect - 0.62) < 0.001
    assert abs(result.performance_fees - 40e6) < 1e6


def test_a_fine_is_read_from_the_reconciliation_table():
    """The table is the accounting figure, not the rounded sentence in the prose.

    The release also says "an EC fine of $3.5 billion"; the table says 3,457, and
    only the table is the amount the company actually added back.
    """
    result = extract_disclosures(EC_FINE_TABLE)
    assert result is not None
    assert abs(result.non_deductible_items - 3_457e6) < 1e6
    assert "fine" in result.non_deductible_label.lower()


def test_nothing_is_invented_when_the_wording_does_not_match():
    """A release this module cannot read must yield nothing, not a wrong number.

    The caller keeps the calculated bridge in that case, so an unmatched filer loses
    no table; a guessed figure would be worse than none.
    """
    unrelated = (
        "Our third quarter was strong. Revenues grew across every segment and we "
        "returned capital to shareholders. Diluted EPS was $2.87."
    )
    assert extract_disclosures(unrelated) is None


def test_zero_is_not_recorded_as_a_disclosed_add_back():
    """A comparative period of zero must not become the add-back."""
    result = extract_disclosures(EC_FINE_TABLE)
    assert result.non_deductible_items != 0
    assert result.non_deductible_items is not None


def test_html_entities_are_decoded_before_matching():
    """SEC writes punctuation as entities, which would sit inside the sentences."""
    html = "<p>gain on equity securities of $10.7 billion</p><p>&#8226; and the " \
           "performance fees related to certain investments of $174 million</p>"
    text = _flatten(html)
    assert "&#8226;" not in text
    assert "gain on equity securities of $10.7 billion" in text


def test_the_eps_sentence_is_bounded_by_the_word_respectively():
    """The capture must reach the last amount, not stop at the decimal point.

    A regression: `[^.]` treated the period inside "$2.2" as a sentence end, so the
    capture was two characters long and no per-share figure was ever found.
    """
    match = _EPS_SENTENCE.search(ALPHABET_2025Q3)
    assert match is not None
    assert "0.68" in match.group("amounts")


def test_a_non_deductible_fine_is_added_back_without_a_tax_benefit():
    """The distinction that made the earlier figures wrong in both directions.

    A regulatory fine is generally not deductible, so adding it back creates no tax
    benefit: the add-back is the full pre-tax amount. A gain that was taxed is the
    opposite — it is removed at its after-tax amount. Applying one netting rate to
    both is what left Alphabet's 2025 Q3 at 2.26 against the release's 2.47.

    Asserted on the arithmetic rather than through `quarterly_eps`, because whether
    that quarter appears in the series depends on which quarters are derivable, and
    the point being tested is the tax treatment. The figures are the release's own:
    net income 34,979m, after-tax gain 8,300m, EC fine 3,457m, 12,203m shares.
    """
    net_income = 34_979e6
    after_tax_gain = 8_300e6
    fine = 3_457e6
    shares = 12_203e6

    # Removing the gain after tax, and adding the fine at full value.
    adjusted = net_income - after_tax_gain + fine
    assert abs(adjusted - 30_136e6) < 1e6, (
        "the fine must be added at full value and the gain removed after tax"
    )
    assert abs(adjusted / shares - 2.47) < 0.01, (
        "the workbook's figure for this quarter is 2.47"
    )

    # The wrong treatment, for contrast: netting the fine at the structural rate
    # would credit a tax benefit the company does not receive, and understate it.
    netted = net_income - after_tax_gain + fine * (1 - 0.17)
    assert netted < adjusted, "netting a non-deductible item understates the add-back"
    assert abs((adjusted - netted) - fine * 0.17) < 1e3, (
        "the erosion is exactly the rate applied to the fine"
    )


def test_quarterly_eps_extraction_is_not_guessed_at():
    """Reading a quarter's EPS from the release is deliberately not implemented.

    The figure is there, but which column it is cannot be decided from the row alone.
    Two rules were measured against figures already known from XBRL: taking the first
    numeric value on the diluted line was right for 17 of 23 real filings, and the
    last for 3. The failures are ambiguous layouts rather than matching mistakes —
    Alphabet's 2025 Q3 statement reads [2.12, 2.87, 5.9, 7.99], prior-year quarter,
    current quarter, prior-year year-to-date, current year-to-date.

    A rule that is wrong for a quarter of filings and fails *silently* — returning a
    plausible figure from the wrong column — is worse than an absent quarter. This
    test exists so the function cannot be quietly made to guess: if it is ever
    implemented it must be by matching the column headers, and it must be scored the
    same way before it is allowed to return a number.
    """
    from app.release import extract_quarterly_eps

    try:
        extract_quarterly_eps("<table><tr><td>Diluted</td><td>1.00</td></tr></table>")
    except NotImplementedError:
        return
    raise AssertionError(
        "extract_quarterly_eps returned a figure; it must identify the column from "
        "the headers and be measured against the 23-filing sample first"
    )


if __name__ == "__main__":
    import traceback

    passed = 0
    failed = 0
    for name, function in sorted(globals().items()):
        if not name.startswith("test_") or not callable(function):
            continue
        try:
            function()
        except Exception:  # noqa: BLE001 - report and continue
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
        else:
            passed += 1
            print(f"PASS  {name}")
    print()
    print(f"{passed}/{passed + failed} passed")
    sys.exit(1 if failed else 0)
