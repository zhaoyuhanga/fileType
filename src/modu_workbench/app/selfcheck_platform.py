"""平台与打包子自检：单 Qt 栈、ffmpeg/ffprobe 定位、品牌图标

**为什么拆到这里**：这些是「打包形态」相关的检查：单 Qt 栈是硬约束（不许再 import QtWebEngine），ffmpeg/ffprobe 必须来自包内目录，图标必须能真的加载。它们与任何业务板块无关。

本模块只放 `check_*` 函数（纯函数、无参数、返回 bool 或抛出）。它们由
`app/selfcheck.py` 的 `_CHECKS` 表按固定顺序调用 —— 拆分**不改变任何检查的内容与顺序**，
只是把原来一个 800 多行的 `run_checks` 按「验的是谁」切开，让每一条都能被单独调用与单独测试。
"""
from __future__ import annotations


def check_single_qt_stack() -> bool:
    """v1.0.0：前端统一为 Qt 单栈。

    用 AST 精确检查"有没有任何模块 import 了 QtWebEngine"（注释里提到不算），
    并确认内嵌前端产物目录已不存在。
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    needle = "PySide6.QtWebEngine"
    for path in list(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"), filename=str(path))
        except SyntaxError:
            return False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name.startswith(needle) for alias in node.names):
                    return False
            elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith(needle):
                return False
    return not (root / "webfront").exists()


def check_ffmpeg_tools() -> bool:
    """随包 ffmpeg/ffprobe 可定位且能执行（音视频转换与 HLS 合流依赖它）。

    两条判定：
    1. **打包产物里必须真的带上 ffmpeg**：冻结运行时 `_MEIPASS/tools/ffmpeg` 必须存在
       （否则 spec 的 `datas` 写错/漏了，用户会得到"下载退化为 .ts、音视频转换不可用"，
       而旧逻辑会因为 `find_ffmpeg()` 回退到 PATH 而**误报通过**）；
    2. 源码运行时缺 ffmpeg 不算失败：用户可自行安装或用 `MODU_FFMPEG` 指定。
    """
    import subprocess
    import sys
    from pathlib import Path as _Path

    from modu_workbench.core.platform.media import bundled_dir, find_ffmpeg, find_ffprobe

    frozen = bool(getattr(sys, "frozen", False))
    bundled = bundled_dir()
    if frozen:
        # 冻结时随包目录必须存在，且解析出来的 ffmpeg 必须来自包内
        meipass = _Path(getattr(sys, "_MEIPASS", "") or "")
        if bundled is None or not str(bundled).startswith(str(meipass)):
            return False

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return not frozen   # 源码运行：可选依赖；打包运行：必须随包
    if frozen and bundled is not None and not str(_Path(ffmpeg)).startswith(str(bundled)):
        return False        # 打包后却用了系统 PATH 的 ffmpeg：说明随包丢失了
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-version"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if out.returncode != 0:
            return False
    except Exception:  # noqa: BLE001
        return False
    # ffprobe 可选（仅用于探测时长），定位不到也不判失败
    ffprobe = find_ffprobe()
    if ffprobe:
        try:
            probe = subprocess.run([ffprobe, "-hide_banner", "-version"], capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=20,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if probe.returncode != 0:
                return False
        except Exception:  # noqa: BLE001
            return False
    return True


def check_branding_icon() -> bool:
    """品牌图标：随包 app.ico 可定位，且能加载出多尺寸 QIcon（窗口/任务栏图标）。"""
    try:
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication

        from modu_workbench.services.assets import app_icon_path

        QApplication.instance() or QApplication([])   # QIcon/QPixmap 需要 GUI 应用实例
        path = app_icon_path()
        if path is None or not path.is_file():
            return False
        icon = QIcon(str(path))
        return (not icon.isNull()) and bool(icon.availableSizes())
    except Exception:  # noqa: BLE001
        return False


__all__ = [
    "check_single_qt_stack",
    "check_ffmpeg_tools",
    "check_branding_icon",
]
