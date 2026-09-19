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
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

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


def _wait_for_server(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def _serve(port: int) -> None:
    import uvicorn

    # Imported inside the thread so PyInstaller's analysis still sees it.
    from app.main import app

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    # A frozen build has no console; keep logs next to the user's data.
    if getattr(sys, "frozen", False):
        from app.config import DATA_DIR

        log_path = Path(DATA_DIR) / "launcher.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(handler)
        log.info("frozen launch, logging to %s", log_path)

    port = _free_port()
    url = f"http://127.0.0.1:{port}/"

    thread = threading.Thread(target=_serve, args=(port,), name="uvicorn", daemon=True)
    thread.start()

    if not _wait_for_server(port):
        log.error("server did not come up on port %s", port)
        return 1
    log.info("serving at %s", url)

    # --no-window is for headless smoke tests of the packaged bundle.
    if "--no-window" in sys.argv:
        log.info("--no-window given; serving until interrupted")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0

    if "--browser" in sys.argv or not _webview_available():
        log.info("opening in the default browser instead of a native window")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0

    import webview

    webview.create_window("FinanceRanker", url, width=1440, height=960, min_size=(980, 700))
    webview.start()
    return 0


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
