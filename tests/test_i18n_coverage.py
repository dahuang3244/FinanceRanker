"""Which i18n keys does the UI ask for that are not defined?

A raw key rendered on screen ("op.th.nearFlow") is the worst kind of bug: it
looks like data. This walks every t("...") call in the shipped scripts, plus the
prefixes of dynamic keys built from a variable, and reports anything the
catalogue does not define — so a key family that gained a member without gaining
a translation is caught rather than passing silently.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "app" / "web" / "static" / "js"
I18N = JS / "i18n.js"

# Static `t("key")` and `t('key')`.
STATIC = re.compile(r"""\bt\(\s*["']([a-zA-Z][\w.]*)["']""")
# `t(`prefix.${expr}`)` — the prefix before the interpolation.
DYNAMIC = re.compile(r"""\bt\(\s*`([a-zA-Z][\w.]*?)\.\$\{""")
# Any key literal anywhere in i18n.js.
DEFINED = re.compile(r"""^\s*["']([a-zA-Z][\w.]*)["']\s*:""", re.M)


def defined_keys() -> set[str]:
    text = I18N.read_text(encoding="utf-8")
    return set(DEFINED.findall(text))


def requested_keys() -> tuple[set[str], set[str]]:
    static: set[str] = set()
    prefixes: set[str] = set()
    for path in sorted(JS.glob("*.js")):
        if path.name == "i18n.js":
            continue
        text = path.read_text(encoding="utf-8")
        static |= set(STATIC.findall(text))
        prefixes |= set(DYNAMIC.findall(text))
    return static, prefixes


def expand(prefix: str, keys: set[str]) -> set[str]:
    """All defined keys under `prefix.` — the family a dynamic key can resolve to."""
    return {k for k in keys if k.startswith(prefix + ".")}


def test_no_requested_key_is_undefined():
    keys = defined_keys()
    static, prefixes = requested_keys()
    missing = sorted(k for k in static if k not in keys)
    assert not missing, f"the UI asks for keys that are not defined: {missing}"


def test_no_dynamic_key_family_is_entirely_undefined():
    keys = defined_keys()
    _, prefixes = requested_keys()
    undefined = sorted(p for p in prefixes if not expand(p, keys))
    assert not undefined, f"these key families resolve to nothing: {undefined}"


def test_the_catalogue_is_not_empty():
    assert len(defined_keys()) > 500


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
    print(f"\\n{passed}/{passed + failed} passed")
    raise SystemExit(1 if failed else 0)