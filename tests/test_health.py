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


def test_static_assets_forbid_stale_caching():
    """A rebuilt app must not be shadowed by a previously cached script.

    Starlette sends ETag/Last-Modified but no Cache-Control, so Chromium fell back
    to heuristic freshness and served the *previous* build's `company-view.js`
    without revalidating -- that is why the company page's 指标 column stayed blank
    after a rebuild although the fix was in the bundle (the server log showed no
    request for that file at all, while other scripts were re-fetched with 200).
    The static mount must therefore tell the browser not to store.
    """
    import inspect

    from app.main import RevalidatedStaticFiles, app as fastapi_app

    static_route = next(
        r for r in fastapi_app.routes if getattr(r, "name", "") == "static"
    )
    assert isinstance(static_route.app, RevalidatedStaticFiles), (
        "the static mount must be the revalidating subclass"
    )
    source = inspect.getsource(RevalidatedStaticFiles.file_response)
    assert "no-store" in source, "no-store is what stops heuristic reuse"
    assert "Pragma" in source and "Expires" in source, "cover HTTP/1.0 clients"


def test_browser_cache_is_cleared_but_preferences_survive():
    """The persistent profile's cache is cleared before Chromium is launched.

    `no-store` stops *new* cache entries, but a copy written by an older build
    would still be reused, so the launcher clears the profile's caches as well.
    localStorage (language + ranking column preferences) must survive.
    """
    import shutil
    from pathlib import Path

    import desktop

    # Built inside the repo's ignored scratch dir rather than the OS temp dir,
    # because hardened environments can refuse writes there (this sandbox does).
    profile = Path(__file__).resolve().parent.parent / "build" / "_cache_probe"
    shutil.rmtree(profile, ignore_errors=True)
    try:
        for relative in ("Default/Cache", "Default/Code Cache", "Default/GPUCache"):
            (profile / relative).mkdir(parents=True, exist_ok=True)
        (profile / "Default/Local Storage").mkdir(parents=True, exist_ok=True)

        desktop._reset_browser_cache(profile)

        assert not (profile / "Default/Cache").exists(), "the HTTP cache must be cleared"
        assert not (profile / "Default/Code Cache").exists(), "the code cache too"
        assert (profile / "Default/Local Storage").is_dir(), (
            "localStorage (language + column preferences) must survive"
        )
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def test_closing_the_window_does_not_wait_for_a_running_refresh():
    """Closing the window must quit even mid-refresh.

    `SystemExit` is not enough: `pipeline.build_rows` fans out over a
    `ThreadPoolExecutor`, and Python joins those worker threads at interpreter
    shutdown. Closing the window during a refresh therefore left the process
    running with no window, a server that accepted connections but never answered
    them, and the bundle's files locked -- which is what blocked rebuilding into
    the same directory. `desktop._exit_now` makes the quit immediate; prove it in
    a child process that has a sleeping pool worker alive.
    """
    import subprocess
    import sys
    import time
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    program = (
        "import concurrent.futures as cf, sys, time\n"
        f"sys.path.insert(0, {str(root)!r})\n"
        "import desktop\n"
        "pool = cf.ThreadPoolExecutor(max_workers=1)\n"
        "pool.submit(time.sleep, 30)   # a refresh still in flight\n"
        "time.sleep(0.3)               # let the worker actually start\n"
        "desktop._exit_now(0)\n"
        "print('NOT REACHED')\n"
    )
    started = time.time()
    proc = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=25,
    )
    elapsed = time.time() - started
    assert proc.returncode == 0, proc.stderr[-300:]
    assert "NOT REACHED" not in proc.stdout, "execution continued past _exit_now"
    assert elapsed < 10, (
        f"took {elapsed:.1f}s to exit -- it waited for the pool worker instead"
    )


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
