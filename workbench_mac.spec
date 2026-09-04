# -*- mode: python ; coding: utf-8 -*-
# 墨读·工作台 macOS 打包配置（在 Mac 上运行，onedir → .app）
# 构建：pyinstaller workbench_mac.spec --noconfirm --clean
# 产物：dist/ModuWorkbench.app

EXCLUDES = [
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DExtras",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQml",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "tkinter",
]

a = Analysis(
    ["src/modu_workbench/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=[("src/modu_workbench/webfront", "modu_workbench/webfront")],
    hiddenimports=[
        "modu_workbench.boards.book_online",
        "modu_workbench.boards.book_reader",
        "modu_workbench.boards.book_shelf",
        "modu_workbench.boards.convert_board",
        "modu_workbench.boards.convert_web",
        "modu_workbench.boards.doc_viewer",
        "modu_workbench.core.convert.media_io",
        "modu_workbench.core.convert.pdf_out",
        "modu_workbench.services.web_bridge",
        "modu_workbench.ui_kit.settings_dialog",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebChannel",
        "markdown.extensions.extra",
        "markdown.extensions.fenced_code",
        "markdown.extensions.sane_lists",
        "markdown.extensions.tables",
        "pygments.lexers.data",
        "pygments.lexers.python",
        "pygments.lexers.javascript",
        "pygments.lexers.html",
        "pygments.lexers.css",
        "pygments.lexers.shell",
        "pygments.lexers.sql",
        "pygments.lexers.text",
        "pygments.formatters.html",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ModuWorkbench",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    argv_emulation=True,
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
    name="ModuWorkbench",
)
app = BUNDLE(
    coll,
    name="ModuWorkbench.app",
    icon=None,
    bundle_identifier="com.internal.moduworkbench",
    info_plist={
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "CFBundleDisplayName": "墨读·工作台",
        "CFBundleShortVersionString": "0.3.0",
    },
)
