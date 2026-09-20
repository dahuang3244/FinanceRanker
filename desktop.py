"""Desktop launcher: run the FastAPI service and show it in a native window.

This is the packaged-app entry point. It does **not** bundle a browser: it uses
the operating system's own webview (WKWebView on macOS, WebView2 on Windows,
WebKitGTK on Linux) via pywebview. That keeps the app around the size of the
Python runtime rather than the ~150 MB a bundled Chromium would add, and it
avoids a second engine that could drift from the system one.

The server binds to 127.0.0.1 on a free port and the window points at it, so the
app is a normal local web app — the same code path as `uvicorn app.main:app`.
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import NoReturn

log = logging.getLogger("launcher")


def _free_port(preferred: int = 8848) -> int:
    """Prefer the usual port, but fall back so a second instance still starts."""
    for candidate in (preferred, 0):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", candidate))
                return sock.getsockname()[1]
        except OSError:
            continue
    raise RuntimeError("no free port available")


def _wait_for_server(port: int, timeout: float = 120.0) -> bool:
    """Wait for the server socket to accept, returning as soon as it does.

    The budget only matters when startup is genuinely slow: the first launch of
    a freshly installed bundle can spend a long time in OS/antivirus scanning
    before Python even runs, and a 30 s ceiling killed the app (exit 1) on
    exactly that -- intermittently, which is worse than reliably.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


# Filled in by `_serve` when the server thread dies before it can listen, so the
# launcher can report *why* instead of a bare "did not come up".
_serve_error: list[BaseException] = []


def _serve(port: int) -> None:
    try:
        import uvicorn

        # Imported inside the thread so PyInstaller's analysis still sees it.
        from app.main import app

        # log_config=None leaves our own root handlers in charge. uvicorn's
        # default config installs a stderr handler, and a windowed
        # (console=False) PyInstaller build has no stderr at all -- which is
        # exactly how a startup failure turns into a silent exit code 1.
        uvicorn.run(app, host="127.0.0.1", port=port, log_config=None, log_level="info")
    except Exception as exc:  # noqa: BLE001 - a startup failure must never be silent
        _serve_error.append(exc)
        log.exception("server thread failed")


def _install_file_logging() -> Path | None:
    """Send logs to a file as early as possible.

    A frozen windowed build has no stdout/stderr, so anything logged before this
    runs -- including a failure to start the server -- would otherwise vanish.
    """
    if not getattr(sys, "frozen", False):
        return None
    try:
        from app.config import DATA_DIR

        log_path = Path(DATA_DIR) / "launcher.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        return log_path
    except Exception:  # pragma: no cover - best effort only
        logging.getLogger(__name__).exception("could not open launcher.log")
        return None


def _find_bundled_browser() -> Path | None:
    """Path to the Chromium shipped inside the bundle, if there is one.

    Only Windows ships one. The bundle layout is `_internal/chromium/chrome.exe`
    (a directory in `datas` is copied by *contents*, into the destination name).
    """
    if not getattr(sys, "frozen", False):
        return None
    names = ("chrome.exe", "Chromium", "chromium")
    roots = [
        Path(getattr(sys, "_MEIPASS", "")) / "chromium",
        Path(sys.executable).parent / "chromium",
    ]
    for root in roots:
        for name in names:
            for candidate in (root / name, root / "chrome-win64" / name):
                if candidate.is_file():
                    return candidate
    return None


def _reset_browser_cache(profile: Path) -> None:
    """Drop the bundled browser's HTTP cache before opening it.

    The profile is deliberately persistent (so the window keeps its size and the
    app keeps its localStorage preferences), but a persistent Chromium profile
    also keeps a disk cache, and it answers heuristically-fresh entries without
    asking the server. That is how a *rebuilt* app kept rendering the previous
    build's front-end: `launcher.log` recorded no request at all for
    `js/company-view.js` after the rebuild, while other scripts were re-fetched,
    so a fix that was definitely in the bundle never reached the page.

    The server now sends `Cache-Control: no-store`, which stops new entries from
    being written -- but a copy cached by an older build would still be reused,
    so the cache is cleared here as well. Nothing of value lives in it: every
    asset is read from local disk in microseconds, while the profile's cookies,
    localStorage and window state are untouched.
    """
    cleared = 0
    for relative in (
        "Default/Cache",
        "Default/Code Cache",
        "Default/GPUCache",
        "Default/DawnGraphiteCache",
        "Default/DawnWebGPUCache",
    ):
        target = profile / relative
        if target.is_dir():
            try:
                shutil.rmtree(target)
                cleared += 1
            except OSError:  # pragma: no cover - best effort, never fatal
                log.debug("could not clear the browser cache at %s", target)
    if cleared:
        # Logged on purpose: "the fix is in the bundle but the page still shows
        # the old behaviour" is diagnosed fastest from this line being absent.
        log.info("cleared %d browser cache director%s", cleared, "y" if cleared == 1 else "ies")


def _open_browser_window(url: str) -> bool:
    """Show `url` in the bundled Chromium as a chromeless app window.

    Returns False when there is no bundled browser, so the caller can fall back
    to the system browser. Blocks until the window is closed.
    """
    browser = _find_bundled_browser()
    if browser is None:
        return False

    # Chrome refuses to start if its profile directory is not writable, and the
    # install directory may be read-only (or under Program Files).
    from app.config import DATA_DIR

    profile = Path(DATA_DIR) / "browser-profile"
    profile.mkdir(parents=True, exist_ok=True)
    _reset_browser_cache(profile)

    args = [
        str(browser),
        f"--app={url}",
        f"--user-data-dir={profile}",
        # A dedicated profile normally keeps this a private instance, but if one
        # is already running Chrome hands the URL over and exits immediately.
        # `--no-startup-window` is deliberately NOT passed: we want the window.
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,MediaRouter",
        "--window-size=1440,960",
    ]
    log.info("opening the bundled browser: %s", browser)
    proc = subprocess.Popen(args)

    # Closing the window ends the browser process, which ends the app: the
    # server has no reason to outlive its only window.
    code = proc.wait()
    log.info("browser window closed (exit %s)", code)
    return True


def _serve_forever() -> int:
    """Keep the server alive until interrupted (browser fallback path)."""
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        return 0


def _exit_now(code: int) -> NoReturn:
    """Quit for real, without waiting for a refresh that is still running.

    `SystemExit` on its own is *not* enough, and this cost a user a locked build
    directory: `pipeline.build_rows` fans out over a `ThreadPoolExecutor`, and
    Python joins those worker threads at interpreter shutdown. So closing the
    window mid-refresh left the process alive with no window at all -- and with
    the server's event loop already stopped, its port still accepted connections
    that were never answered, while the workers kept fetching. From the outside
    the app looked closed but kept running, kept its files locked (so rebuilding
    into the same directory failed with "the file is in use") and could only be
    killed from Task Manager.

    A run is only written at the end, inside SQLite's transaction, so an abrupt
    exit cannot leave a half-written snapshot behind -- the in-flight fetch is
    simply dropped, which is what closing the window means.
    """
    log.info("exiting (code %s)", code)
    logging.shutdown()
    os._exit(code)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    log_path = _install_file_logging()
    if log_path is not None:
        log.info("frozen launch, logging to %s", log_path)

    # A crash inside the server thread is reported through threading's hook,
    # which writes to stderr -- absent in a windowed build, so route it to ours.
    def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
        log.error(
            "unhandled exception in thread %s",
            args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = _thread_excepthook

    # Exercises the pieces a headless --no-window check never touches, so a
    # missing window layer fails the build instead of the user's double-click.
    if "--selftest" in sys.argv:
        browser = _find_bundled_browser()
        if browser is not None:
            log.info("selftest: bundled browser at %s", browser)
            return 0
        if sys.platform == "win32":
            # Windows promises a self-contained window layer, so its absence is
            # a build failure. Elsewhere the native webview is used instead.
            log.error("selftest: this Windows build has no bundled browser")
            return 2
        log.info("selftest: no bundled browser, which is expected on this platform")
        return 0

    port = _free_port()
    url = f"http://127.0.0.1:{port}/"

    thread = threading.Thread(target=_serve, args=(port,), name="uvicorn", daemon=True)
    thread.start()

    if not _wait_for_server(port):
        if _serve_error:
            log.error(
                "server did not come up on port %s (%s: %s)",
                port,
                type(_serve_error[0]).__name__,
                _serve_error[0],
            )
        else:
            log.error("server did not come up on port %s", port)
        return 1
    log.info("serving at %s", url)

    # --no-window is for headless smoke tests of the packaged bundle.
    if "--no-window" in sys.argv:
        log.info("--no-window given; serving until interrupted")
        return _serve_forever()

    # ---- window layer -----------------------------------------------------
    # On Windows the bundled Chromium is driven directly as a subprocess.
    # pywebview is deliberately not used there: its WinForms backend goes
    # through pythonnet, whose Python.Runtime.dll cannot be loaded from a frozen
    # bundle ("Failed to resolve Python.Runtime.Loader.Initialize"). Native
    # window on macOS, where WKWebView has no such dependency.
    if sys.platform == "win32" and "--browser" not in sys.argv:
        try:
            if _open_browser_window(url):
                # The window *is* the app; see `_exit_now` for why returning here
                # is not enough.
                _exit_now(0)
            log.warning("this build has no bundled browser; using the system one")
        except Exception:  # noqa: BLE001 - the window must never kill the app
            log.exception("the bundled browser failed; using the system one")
        webbrowser.open(url)
        return _serve_forever()

    if "--browser" in sys.argv or not _webview_available():
        log.info("opening in the default browser instead of a native window")
        webbrowser.open(url)
        return _serve_forever()

    try:
        import webview

        webview.create_window(
            "FinanceRanker", url, width=1440, height=960, min_size=(980, 700)
        )
        webview.start()
    except Exception:  # noqa: BLE001 - degrade to a browser tab, never crash
        log.exception("native window failed; opening in the default browser")
        webbrowser.open(url)
        return _serve_forever()
    _exit_now(0)


def _webview_available() -> bool:
    """Whether a native window backend can be used at all.

    `find_spec` avoids importing the package here; the real import happens in
    `main` so that a missing backend degrades to the browser instead of failing
    at module load (headless Linux CI has no GTK/Qt).
    """
    import importlib.util

    try:
        found = importlib.util.find_spec("webview") is not None
    except (ImportError, ValueError) as exc:
        log.warning("pywebview not usable (%s)", exc)
        return False
    if not found:
        log.warning("pywebview not installed; will open in the default browser")
    return found


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    raise SystemExit(main())
