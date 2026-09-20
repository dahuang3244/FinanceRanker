# PyInstaller spec for FinanceRanker.
#
# Build:  pyinstaller --clean --noconfirm finance_ranker.spec
#
# Notes that matter:
#  * The web UI is data, not code: `app/web/static` must be shipped with
#    --add-data or the packaged app serves 404s.
#  * akshare, pandas and curl_cffi pull in optional/native pieces that
#    PyInstaller's static analysis misses; the hiddenimports below cover them.
#  * No browser is bundled. The desktop window uses the OS webview (WKWebView /
#    WebView2 / WebKitGTK), which keeps the app near the size of the Python
#    runtime instead of adding a ~150 MB Chromium.

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).resolve()

# ---- application icon ---------------------------------------------------- #
# The window/executable icon is the app's own SVG mark (the same one the web UI
# serves as favicon.svg and shows top-left), rasterised into the containers the
# platforms need: .ico for Windows, .icns for macOS. Committing an icon the spec
# only *reads* is not enough on its own -- a fresh checkout without the generated
# assets would silently ship PyInstaller's default icon -- so it is regenerated
# here when missing. `tools/make_app_icons.py` derives everything from the SVG,
# so the packaged app can never drift from the in-app mark.
ICON = None
if sys.platform == "win32":
    ICON = ROOT / "assets" / "finance_ranker.ico"
elif sys.platform == "darwin":
    ICON = ROOT / "assets" / "finance_ranker.icns"
if ICON is not None and not ICON.is_file():
    sys.path.insert(0, str(ROOT / "tools"))
    from make_app_icons import generate_all

    generate_all()
    if not ICON.is_file():
        raise SystemExit(f"ERROR: the app icon could not be generated at {ICON}")

datas = [
    (str(ROOT / "app" / "web" / "static"), "app/web/static"),
]
# akshare ships JSON/CSV lookup tables that its functions read at runtime.
datas += collect_data_files("akshare")
datas += collect_data_files("curl_cffi")
# py_mini_racer is a V8 binding: the .dylib/.so/.dll is a *binary*, and it also
# needs icudtl.dat at runtime. Missing this made every akshare price call fail
# with "Native library or dependency not available" inside the frozen bundle,
# while working fine from source — the classic frozen-build-only failure.
binaries = collect_dynamic_libs("py_mini_racer")
datas += collect_data_files("py_mini_racer")

# Windows ships its own Chromium, so the packaged app is self-contained: it
# needs neither the machine's WebView2/Edge nor pythonnet. The build stages the
# browser into vendor/chromium (see windows/stage_chromium.ps1, called by
# windows/build.ps1 and .github/workflows/build.yml); building without it is not
# a supported path on Windows any more -- the CI selftest fails such a build with
# exit code 2 rather than shipping a window layer that is not there.
CHROMIUM_DIR = ROOT / "vendor" / "chromium"
if sys.platform == "win32" and CHROMIUM_DIR.is_dir():
    datas.append((str(CHROMIUM_DIR), "chromium"))

hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "apscheduler.triggers.cron",
    "apscheduler.triggers.interval",
    "apscheduler.schedulers.background",
    "apscheduler.executors.pool",
    "apscheduler.jobstores.memory",
    "pydantic_settings",
    "py_mini_racer",
]
# pywebview picks its backend by platform; Windows does not use it at all (see
# below), so only the backends for the platforms that do are pulled in.
import sys as _sys

if _sys.platform == "darwin":
    hiddenimports += ["webview", "webview.platforms.cocoa"]
elif _sys.platform == "win32":
    # No pywebview on Windows: desktop.py runs the bundled Chromium as a
    # subprocess instead. The WinForms backend needs pythonnet, whose
    # Python.Runtime.dll cannot be resolved from a frozen bundle
    # ("Failed to resolve Python.Runtime.Loader.Initialize"), so the entire
    # .NET bridge is left out rather than shipped broken.
    pass
else:
    hiddenimports += ["webview", "webview.platforms.gtk"]
# akshare resolves many providers by name at call time.
hiddenimports += collect_submodules("akshare")

excludes = [
    # Nothing here needs a GUI toolkit; dropping these avoids bundling Tk/Qt.
    "tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "matplotlib",
    "IPython", "jupyter", "notebook", "pytest", "PyInstaller",
]
if sys.platform == "win32":
    # Belt and braces: desktop.py still contains the (unreachable on Windows)
    # pywebview import, so without this PyInstaller's static analysis would pull
    # pythonnet back in.
    excludes += ["webview", "clr", "clr_loader", "pythonnet"]

block_cipher = None

# Windows file properties (shown in Explorer's Details tab and used by the
# uninstaller entry). Harmless on other platforms: `version` is Windows-only and
# PyInstaller warns and ignores it elsewhere, so it stays None off Windows.
#
# It must be a `VSVersionInfo` object (or a path to a version-resource file).
# A plain dict -- which older PyInstaller accepted -- now raises
# "TypeError: Unsupported type for version info argument: <class 'dict'>",
# and because the dict was built under `if sys.platform == "win32"` the failure
# only ever appeared on the Windows runner.
APP_VERSION = "0.1.0"

version_info = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    _v = tuple(int(p) for p in APP_VERSION.split(".")) + (0,)  # -> (0, 1, 0, 0)
    version_info = VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=_v,
            prodvers=_v,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,      # VOS_NT_WINDOWS32
            fileType=0x1,    # VFT_APP
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",  # US English, Unicode (codepage 1200)
                        [
                            StringStruct("CompanyName", "FinanceRanker"),
                            StringStruct(
                                "FileDescription",
                                "FinanceRanker — 免费公开数据科技股同行排名",
                            ),
                            StringStruct("FileVersion", f"{APP_VERSION}.0"),
                            StringStruct("InternalName", "FinanceRanker"),
                            StringStruct(
                                "LegalCopyright",
                                "Research screen; not investment advice.",
                            ),
                            StringStruct("OriginalFilename", "FinanceRanker.exe"),
                            StringStruct("ProductName", "FinanceRanker"),
                            StringStruct("ProductVersion", f"{APP_VERSION}.0"),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )

a = Analysis(
    ["desktop.py"],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FinanceRanker",
    version=version_info,
    # The brand mark, not PyInstaller's stock icon. `None` off Windows/macOS,
    # where the icon argument is not used and would only warn.
    icon=str(ICON) if ICON else None,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Windowed: no console window for end users. desktop.py logs to a file when
    # frozen. Build with --console via CONSOLE=1 for a diagnostic variant.
    console=bool(os.environ.get("CONSOLE")),
    disable_windowed_traceback=False,
    # macOS-only option; a Windows build would otherwise carry a meaningless
    # `pyi-macos-argv-emulation` TOC entry.
    argv_emulation=(sys.platform == "darwin"),
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="FinanceRanker",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="FinanceRanker.app",
        # macOS reads the .icns rather than the executable's resources, so the
        # Dock/Finder icon comes from here.
        icon=str(ICON) if ICON else None,
        bundle_identifier="io.github.financeranker.app",
        info_plist={
            "CFBundleShortVersionString": "0.1.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
