"""Static assets must not be served stale.

The app is upgraded by copying files over the previous build and serves on a
stable localhost port. Without explicit cache rules a browser happily reuses the
*old* shell.js after an upgrade, which produced a page calling `api.options` on a
build that predates it and rendering raw i18n keys — with every file on disk
correct. These tests pin the two mechanisms that prevent it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.main as main


def test_asset_stamp_is_stable_and_non_empty():
    first = main.asset_stamp()
    assert first and len(first) >= 8
    # Must not change between calls, or every request would bust the cache.
    assert main.asset_stamp() == first


def test_stamp_changes_when_an_asset_changes(monkeypatch_tmp=None):
    """A touched asset must produce a different stamp.

    This is the property that makes a deploy visible: new files, new URLs, so the
    browser has never cached them.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "js").mkdir()
        (root / "css").mkdir()
        asset = root / "js" / "shell.js"
        asset.write_text("let a = 1;")

        original_dir = main.STATIC_DIR
        original_stamp = main._ASSET_STAMP
        try:
            main.STATIC_DIR = root
            main._ASSET_STAMP = None
            before = main.asset_stamp()

            # Same content, new mtime: a rebuild always does this.
            import os
            import time

            time.sleep(0.01)
            os.utime(asset, None)
            main._ASSET_STAMP = None
            after_touch = main.asset_stamp()
            assert after_touch != before, "touching an asset must invalidate the stamp"

            # Changing content changes size, so it must change too.
            asset.write_text("let a = 2; // longer")
            main._ASSET_STAMP = None
            assert main.asset_stamp() != after_touch
        finally:
            main.STATIC_DIR = original_dir
            main._ASSET_STAMP = original_stamp


def test_only_script_and_style_references_are_versioned():
    """The rewriter must not touch external URLs, data URIs or anchors."""
    sample = (
        '<link rel="stylesheet" href="css/options.css" />'
        '<script src="js/shell.js"></script>'
        '<a href="ranking.html">x</a>'
        '<img src="favicon.svg" />'
        '<script src="https://cdn.example.com/x.js"></script>'
        '<link rel="icon" href="favicon.svg" type="image/svg+xml" />'
    )
    out = main._ASSET_REF.sub(lambda m: f'{m.group("attr")}="{m.group("path")}?v=TEST"', sample)
    assert 'href="css/options.css?v=TEST"' in out
    assert 'src="js/shell.js?v=TEST"' in out
    # Untouched: pages, images, and anything remote.
    assert 'href="ranking.html"' in out
    assert 'src="favicon.svg"' in out
    assert 'href="favicon.svg"' in out
    assert 'src="https://cdn.example.com/x.js"' in out


def test_every_page_versions_its_own_assets():
    """Every shipped page must reference js/ and css/ files the rewriter can see.

    A page whose scripts are injected some other way would silently escape the
    versioning and could serve stale code after an upgrade.
    """
    pages = sorted(main.STATIC_DIR.glob("*.html"))
    assert pages, "no pages found in the static directory"
    for page in pages:
        text = page.read_text(encoding="utf-8")
        refs = main._ASSET_REF.findall(text)
        assert refs, f"{page.name} references no versionable js/css assets"
        # Any script or stylesheet reference must be a relative local path, so the
        # rewriter matches it.
        for match in re.finditer(r'<script[^>]*\ssrc="([^"]+)"', text):
            url = match.group(1)
            assert not url.startswith(("http://", "https://", "//")), (
                f"{page.name} loads a remote script ({url}) that cannot be versioned"
            )
        for match in re.finditer(r'<link[^>]*rel="stylesheet"[^>]*href="([^"]+)"', text):
            url = match.group(1)
            assert not url.startswith(("http://", "https://", "//")), (
                f"{page.name} loads a remote stylesheet ({url})"
            )


def test_html_is_served_no_store_and_only_versioned_assets_are_immutable():
    """The header policy itself, as served.

    The distinction that matters: a *versioned* asset URL may be cached forever,
    because the stamp changes whenever the file does. An *unversioned* one may
    not. Serving `/js/options.js` immutably pinned a stale script in the browser
    for a year, so a page from an older build kept rendering raw i18n keys long
    after the file on disk had been corrected.
    """
    import asyncio

    async def fetch(path: str, query: str = ""):
        scope = {
            "type": "http", "method": "GET", "path": path, "headers": [],
            "query_string": query.encode("latin-1"),
        }
        files = main.VersionedStaticFiles(directory=str(main.STATIC_DIR), html=True)
        return await files.get_response(path, scope)

    html = asyncio.run(fetch("options.html"))
    assert "text/html" in html.headers["content-type"]
    # Assert the directives rather than the exact string: the header grew once
    # already when `no-store` alone proved insufficient against a browser that had
    # cached an earlier build, and it must be free to grow again.
    html_cache = html.headers["cache-control"]
    for directive in ("no-store", "no-cache", "must-revalidate", "max-age=0"):
        assert directive in html_cache, (
            f"HTML must never be reused from cache (missing {directive!r}), or an "
            f"upgrade is invisible — got {html_cache!r}"
        )
    assert html.headers.get("expires") == "0", "a zero expiry covers legacy caches"
    assert html.headers.get("pragma") == "no-cache"

    versioned = asyncio.run(fetch("js/shell.js", "v=abc123"))
    assert "immutable" in versioned.headers["cache-control"], (
        "a versioned asset URL is safe to cache permanently"
    )

    unversioned = asyncio.run(fetch("js/shell.js"))
    assert "immutable" not in unversioned.headers["cache-control"], (
        "an unversioned asset URL must not be cached permanently: a stale page "
        "requests it, and the browser would then hold the wrong script for a year"
    )
    assert "no-store" in unversioned.headers["cache-control"], (
        "an unversioned asset must revalidate every time"
    )


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
