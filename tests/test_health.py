"""Offline tests for the provider health probe and the tri-state Yahoo policy.

The probes themselves do network I/O, so they are tested by substitution: what
matters for correctness is the *policy* and the aggregation, plus that a probe
never raises into the caller. Live reachability is verified separately by
`tests/verify_providers.py`.

Run:  PYTHONPATH=. python tests/test_health.py
"""

from __future__ import annotations

from app import cache as cache_mod
from app import health as H
from app.config import settings


def _with_probe(stub):
    """Run `stub` with the cache and settings restored afterwards."""
    saved_get, saved_put = cache_mod.get, cache_mod.put
    saved_mode, saved_flag = settings.yahoo_mode, settings.enable_yahoo
    cache_mod.get = lambda ns, key, ttl: None
    cache_mod.put = lambda *a, **k: None
    try:
        return stub()
    finally:
        cache_mod.get, cache_mod.put = saved_get, saved_put
        settings.yahoo_mode, settings.enable_yahoo = saved_mode, saved_flag


# --------------------------------------------------------------------------- #
# policy
# --------------------------------------------------------------------------- #
def test_yahoo_mode_off_never_uses_yahoo():
    def run():
        settings.enable_yahoo = False
        settings.yahoo_mode = "off"
        H.reachable = lambda key, force=False: True    # even if reachable
        assert H.yahoo_usable() is False

    _with_probe(run)


def test_yahoo_mode_on_always_uses_yahoo():
    def run():
        settings.enable_yahoo = False
        settings.yahoo_mode = "on"
        H.reachable = lambda key, force=False: False   # even if unreachable
        assert H.yahoo_usable() is True

    _with_probe(run)


def test_yahoo_mode_auto_follows_the_probe():
    def run():
        settings.enable_yahoo = False
        settings.yahoo_mode = "auto"
        H.reachable = lambda key, force=False: True
        assert H.yahoo_usable() is True
        H.reachable = lambda key, force=False: False
        assert H.yahoo_usable() is False

    _with_probe(run)


def test_legacy_bool_overrides_mode():
    def run():
        settings.enable_yahoo = True
        settings.yahoo_mode = "off"
        assert H.yahoo_usable() is True, "the explicit bool must win"

    _with_probe(run)


def test_mode_parsing_is_forgiving():
    def run():
        settings.enable_yahoo = False
        H.reachable = lambda key, force=False: False
        for mode, expected in [
            ("OFF", False), ("off", False), ("no", False), ("0", False), ("false", False),
            ("ON", True), ("on", True), ("yes", True), ("1", True), ("true", True),
        ]:
            settings.yahoo_mode = mode
            assert H.yahoo_usable() is expected, f"mode {mode!r}"


def test_unknown_mode_falls_back_to_auto():
    def run():
        settings.enable_yahoo = False
        settings.yahoo_mode = "banana"
        H.reachable = lambda key, force=False: True
        assert H.yahoo_usable() is True   # auto semantics

    _with_probe(run)


# --------------------------------------------------------------------------- #
# aggregation
# --------------------------------------------------------------------------- #
def test_results_shape_and_never_raises():
    def run():
        # Every probe fails; the aggregate must still be well formed.
        original = dict(H.PROBES)
        H.PROBES.clear()
        H.PROBES["boom"] = ("explodes", lambda: (_ for _ in ()).throw(RuntimeError("nope")))
        H.PROBES["fine"] = ("works", lambda: (True, "ok"))
        try:
            out = H.results(force=True)
        finally:
            H.PROBES.clear()
            H.PROBES.update(original)

        assert "providers" in out and "reachable" in out
        assert out["providers"]["fine"]["ok"] is True
        assert out["providers"]["boom"]["ok"] is False, "a raising probe counts as down"
        assert "reachable" in out and out["reachable"] == ["fine"]
        assert "checked_at" in out and "elapsed_s" in out

    _with_probe(run)


def test_reachable_defaults_to_optimistic_on_failure():
    """A broken probe must not be read as 'source is down'."""
    saved = H.results
    H.results = lambda force=False: (_ for _ in ()).throw(RuntimeError("probe failed"))
    try:
        assert H.reachable("sec") is True
    finally:
        H.results = saved


def test_sec_probe_validates_user_agent_configuration():
    """SEC rejects a generic UA, so a bad sec_user_agent must show as down."""
    saved_fetch = H.fetch

    def fake_fetch(url, **kwargs):
        ua = (kwargs.get("headers") or {}).get("User-Agent", "")
        if not ua or ua.lower().startswith("python") or "mozilla" in ua.lower():
            raise H.FetchError("HTTP 403")
        return {"units": {"USD": []}}

    H.fetch = fake_fetch
    try:
        ok, _ = H._probe_sec()
        assert ok is True, "declared UA should pass"

        from app.config import settings as cfg
        original = cfg.sec_user_agent
        cfg.sec_user_agent = "Mozilla/5.0"
        try:
            ok, detail = H._probe_sec()
            assert ok is False, "a browser UA is rejected by SEC fair-access"
            assert "403" in detail
        finally:
            cfg.sec_user_agent = original
    finally:
        H.fetch = saved_fetch


def test_sina_probe_accepts_jsonp_payload():
    """Sina returns a JSONP array without echoing the method name."""
    saved_fetch = H.fetch
    payload = '/*<script>location.href=\'//sina.com\';</script>*/\nx([{"d":"2026-09-18","c":"336.13"}]' + "0" * 600 + ")"
    H.fetch = lambda *a, **k: payload
    try:
        ok, _ = H._probe_sina()
        assert ok is True
    finally:
        H.fetch = saved_fetch

    # A truncated or blocked response must not pass.
    H.fetch = lambda *a, **k: "<html>blocked</html>"
    try:
        ok, detail = H._probe_sina()
        assert ok is False and detail == "unexpected payload"
    finally:
        H.fetch = saved_fetch


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
