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
    "webview",
]
# pywebview picks its backend by platform; only the local one is importable, so
# the others are added defensively (harmless if absent on this OS).
import sys as _sys

if _sys.platform == "darwin":
    hiddenimports += ["webview.platforms.cocoa"]
elif _sys.platform == "win32":
    hiddenimports += [
        "webview.platforms.winforms",
        "clr",              # pythonnet, required by the WinForms backend
        "clr_loader",
        "pythonnet",
    ]
else:
    hiddenimports += ["webview.platforms.gtk"]
# akshare resolves many providers by name at call time.
hiddenimports += collect_submodules("akshare")

excludes = [
    # Nothing here needs a GUI toolkit; dropping these avoids bundling Tk/Qt.
    "tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "matplotlib",
    "IPython", "jupyter", "notebook", "pytest", "PyInstaller",
]

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
        icon=None,
        bundle_identifier="io.github.financeranker.app",
        info_plist={
            "CFBundleShortVersionString": "0.1.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
