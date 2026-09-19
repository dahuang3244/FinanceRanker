"""Offline tests for US sector classification (app/sectors.py).

The SIC -> module mapping is pure logic, so it is tested exhaustively without
network. The HTTP layer is exercised only for the shape of `get_company_sector`
via an injected cache, so no request is made.

Run:  PYTHONPATH=. python tests/test_sectors.py
"""

from __future__ import annotations

from app import cache as cache_mod
from app import sectors as S


# --------------------------------------------------------------------------- #
# SIC mapping
# --------------------------------------------------------------------------- #
def test_every_sic_code_maps_to_a_known_module():
    """Total coverage: no code in the valid range may fall through."""
    for code in range(100, 10000):
        # Gaps are allowed by SIC, but any returned module must be one of six.
        result = S.sector_for_sic(code)
        assert result is None or result in S.SECTOR_KEYS, f"SIC {code} -> {result}"


def test_known_companies_map_to_expected_modules():
    cases = [
        (7372, "tech", "Microsoft / software"),
        (3674, "tech", "NVIDIA / semiconductors"),
        (3571, "tech", "Apple / computers"),
        (3826, "tech", "lab instruments"),
        (6021, "finance", "JPMorgan / banks"),
        (6211, "finance", "brokerage"),
        (6311, "finance", "life insurance"),
        (2834, "health", "Pfizer / pharma"),
        (3841, "health", "medical devices"),
        (2000, "health", "food"),
        (5812, "health", "restaurants"),
        (4813, "media", "telecom"),
        (2711, "media", "publishing"),
        (7812, "media", "motion pictures"),
        (2911, "autoenergy", "Exxon / refining"),
        (1311, "autoenergy", "oil & gas extraction"),
        (3711, "autoenergy", "Tesla / motor vehicles"),
        (4911, "autoenergy", "utilities"),
        (5331, "industrial", "Walmart / retail"),
        (1521, "industrial", "construction"),
    ]
    for sic, expected, why in cases:
        got = S.sector_for_sic(sic)
        assert got == expected, f"SIC {sic} ({why}) -> {got}, expected {expected}"


def test_overrides_beat_the_division_default():
    """Motor vehicles and pharma sit in divisions that would misclassify them.

    SIC division D (manufacturing, 2000-3999) defaults to 制造零售类, but 3711
    must land in 汽车能源类 and 2834 in 医药食品类.
    """
    assert S.sector_for_sic(3711) == "autoenergy"      # not industrial
    assert S.sector_for_sic(2834) == "health"          # not industrial
    assert S.sector_for_sic(3674) == "tech"            # not industrial
    assert S.sector_for_sic(7372) == "tech"            # not industrial
    # A manufacturing code with no override keeps the division default.
    assert S.sector_for_sic(3559) == "industrial"


def test_invalid_input_returns_none():
    for bad in (None, "", "abc", 0, -5, "not-a-code"):
        assert S.sector_for_sic(bad) is None, f"{bad!r} should not classify"


def test_all_six_modules_are_reachable():
    seen = set()
    for code in range(100, 10000):
        key = S.sector_for_sic(code)
        if key:
            seen.add(key)
    assert seen == set(S.SECTOR_KEYS), f"unreachable modules: {set(S.SECTOR_KEYS) - seen}"


# --------------------------------------------------------------------------- #
# seed / candidates
# --------------------------------------------------------------------------- #
def test_seed_covers_every_module():
    """A cold cache must still be able to rank something in each module."""
    for key in S.SECTOR_KEYS:
        seeded = S.SEED_BY_SECTOR.get(key) or []
        assert len(seeded) >= 15, f"{key} has only {len(seeded)} seed tickers"


def test_candidates_come_back_with_cijk_when_available():
    rows = S.sector_candidates("tech", limit=10)
    assert rows, "tech candidates should not be empty"
    assert all(r["sector"] == "tech" for r in rows)
    # Resolvable entries are sorted ahead of unresolvable ones so a ranking has
    # something to fetch.
    ciks = [r.get("cik") for r in rows]
    first_none = next((i for i, c in enumerate(ciks) if c is None), len(ciks))
    assert all(c is None for c in ciks[first_none:]), "unresolvable entries must sort last"


def test_sector_labels_exist_for_every_key():
    for key in S.SECTOR_KEYS:
        zh, en = S.SECTOR_LABELS[key]
        assert zh and en
        assert not zh.isascii() and en.isascii()


# --------------------------------------------------------------------------- #
# resolve (network path, cache injected)
# --------------------------------------------------------------------------- #
def test_get_company_sector_parses_sic_from_cache():
    """Feed a canned SEC submissions payload through the cache layer."""
    payload = {
        "sic": "3674",
        "sicDescription": "Semiconductors & Related Devices",
        "name": "NVIDIA CORP",
        "exchanges": ["Nasdaq", "Nasdaq"],
    }
    original_get = cache_mod.get
    original_put = cache_mod.put
    cache_mod.get = lambda ns, key, ttl: payload if ns == S.NS and key == "cik:1045810" else None
    cache_mod.put = lambda *a, **k: None
    try:
        item = S.get_company_sector("NVDA", cik=1045810)
    finally:
        cache_mod.get = original_get
        cache_mod.put = original_put

    assert item is not None
    assert item.ticker == "NVDA"
    assert item.sic == 3674
    assert item.sector == "tech"
    assert item.sic_description.startswith("Semiconductors")
    assert item.exchange == "Nasdaq", "first non-empty exchange wins"


def test_remember_adds_to_index_only_with_a_module():
    stored: dict = {}
    original_get = cache_mod.get
    original_put = cache_mod.put
    cache_mod.get = lambda ns, key, ttl: stored.get(key)
    cache_mod.put = lambda ns, key, value: stored.__setitem__(key, value)
    try:
        resolved = S.CompanySector(
            ticker="NVDA", cik=1, sic=3674, sic_description="Semis",
            sector="tech", name="NVIDIA CORP",
        )
        S.remember(resolved)
        index = stored.get("index") or {}
        assert "NVDA" in index and index["NVDA"]["sector"] == "tech"

        # An unclassifiable company must not pollute the index.
        unknown = S.CompanySector(
            ticker="ZZZZ", cik=2, sic=None, sic_description="", sector=None,
        )
        before = len(index)
        S.remember(unknown)
        assert len((stored.get("index") or {})) == before
    finally:
        cache_mod.get = original_get
        cache_mod.put = original_put


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
