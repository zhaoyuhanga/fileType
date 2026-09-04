# -*- mode: python ; coding: utf-8 -*-
# 墨读·工作台 PyInstaller 打包配置（Windows onedir，随包依赖）
# 构建：pyinstaller workbench.spec --noconfirm --clean
# 产物：dist/ModuWorkbench/ModuWorkbench.exe

EXCLUDES = [
    # 用不到的 Qt 大模块
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQml",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtPdf",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtStateMachine",
    "PySide6.QtTextToSpeech",
    "PySide6.QtWebSockets",
    "tkinter",
]

a = Analysis(
    ["src/modu_workbench/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
    hiddenimports=[
        "modu_workbench.boards.book_online",
        "modu_workbench.boards.book_reader",
        "modu_workbench.boards.book_shelf",
        "modu_workbench.boards.convert_board",
        "modu_workbench.boards.doc_viewer",
        "modu_workbench.core.convert.media_io",
        "modu_workbench.core.convert.pdf_out",
        "modu_workbench.ui_kit.settings_dialog",
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
    disable_windowed_traceback=False,
    argv_emulation=False,
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
    name="ModuWorkbench",
)
