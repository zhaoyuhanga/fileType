"""M4 联动/设置测试：共享书库上下文与设置对话框。"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from modu_workbench.boards.book.online import OnlineDownloadPage
from modu_workbench.core.book import Library
from modu_workbench.app import context as app_context
from modu_workbench.ui_kit.settings import PAGE_ROLE, SettingsDialog
from modu_workbench.ui_kit.toast import Toaster


@pytest.fixture()
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_shared_library_singleton() -> None:
    assert app_context.library() is app_context.library()
    assert isinstance(app_context.library(), Library)


def test_settings_dialog_builds(qapp: QApplication) -> None:
    dialog = SettingsDialog()
    dialog.show()
    try:
        # 默认停在「通用」页；书库页默认开启合规声明
        dialog._select("book")                                   # noqa: SLF001
        book_page = dialog._ensure_page("book")                  # noqa: SLF001
        assert book_page is not None
        assert book_page._compliance.isChecked()                 # noqa: SLF001
    finally:
        dialog.close()


def test_settings_dialog_has_all_board_pages(qapp: QApplication) -> None:
    """设置必须按板块分区，且能直接定位到指定板块。"""
    from modu_workbench.ui_kit.settings import PAGE_FACTORIES

    keys = [key for key, _title, _icon, _factory in PAGE_FACTORIES]
    assert keys == ["general", "llm", "book", "convert", "document", "music", "video",
                    "gallery"]

    dialog = SettingsDialog(initial="gallery")
    try:
        entry = dialog._nav.currentItem()                        # noqa: SLF001
        assert entry is not None and entry.data(PAGE_ROLE) == "gallery"
    finally:
        dialog.close()


def test_settings_pages_scroll_and_keep_buttons_visible(qapp: QApplication) -> None:
    """设置项多的板块必须能滚动，且「保存/关闭」永远在可视区域内。"""
    from PySide6.QtWidgets import QScrollArea

    dialog = SettingsDialog(initial="llm")
    dialog.resize(820, 560)          # 刻意用较小的窗口
    dialog.show()
    qapp.processEvents()
    try:
        # 每一页都套在滚动区里
        for key in ("general", "llm", "gallery"):
            dialog._select(key)                                  # noqa: SLF001
            qapp.processEvents()
            page = dialog._ensure_page(key)                      # noqa: SLF001
            assert page is not None
            assert isinstance(dialog._scrolls[key], QScrollArea)  # noqa: SLF001
            assert dialog._stack.currentWidget() is dialog._scrolls[key]  # noqa: SLF001

        # 操作条上的两个按钮必须落在对话框可视矩形内（曾经被内容顶出窗口）
        from PySide6.QtCore import QPoint, QRect

        for label in ("保存", "关闭"):
            button = next(btn for btn in dialog.findChildren(QPushButton)
                          if btn.text() == label and btn.isVisible())
            rect = QRect(button.mapTo(dialog, QPoint(0, 0)), button.size())
            assert dialog.rect().contains(rect.center()), f"{label} 被内容挤出可视区"
            assert rect.bottom() <= dialog.rect().bottom(), f"{label} 溢出对话框底部"
    finally:
        dialog.close()


def test_settings_pages_fit_default_width(qapp: QApplication) -> None:
    """每个设置页在默认窗口宽度下都不该出现横向滚动条。

    之前 `music` / `video` / `llm` 三页的最小宽度被内容顶到 1000px 以上
    （长文件路径的 QLabel、写满状态的长复选框），打开设置就横着滚 —— 观感很差。
    """
    dialog = SettingsDialog()
    dialog.resize(980, 700)          # 默认尺寸
    dialog.show()
    qapp.processEvents()
    try:
        from modu_workbench.ui_kit.settings import PAGE_FACTORIES

        for key, _title, _icon, _factory in PAGE_FACTORIES:
            dialog._select(key)                                  # noqa: SLF001
            qapp.processEvents()
            scroll = dialog._scrolls[key]                        # noqa: SLF001
            bar = scroll.horizontalScrollBar()
            assert bar.maximum() == 0, (
                f"{key} 页需要横向滚动（最大 {bar.maximum()}px）："
                "把长文本换成只读输入框 / 缩短标签 / 收窄固定宽度")
    finally:
        dialog.close()


def test_settings_dialog_survives_broken_page(qapp: QApplication, monkeypatch) -> None:
    """某个板块设置页构造失败时，不应把整个设置窗口带崩。"""
    import modu_workbench.ui_kit.settings as settings_pkg

    class Boom(settings_pkg.SettingsPage):
        def __init__(self, parent=None):  # noqa: ANN001
            raise RuntimeError("模拟板块初始化失败")

    patched = tuple(
        (key, title, icon, Boom if key == "music" else factory)
        for key, title, icon, factory in settings_pkg.PAGE_FACTORIES
    )
    monkeypatch.setattr(settings_pkg, "PAGE_FACTORIES", patched)

    dialog = settings_pkg.SettingsDialog(initial="music")
    try:
        page = dialog._ensure_page("music")                      # noqa: SLF001
        assert page is not None and "失败" in page.hint()
    finally:
        dialog.close()


def test_online_page_respects_settings_default(qapp: QApplication) -> None:
    host = QApplication.instance()
    assert host is not None
    page = OnlineDownloadPage(app_context.library(), Toaster(host))
    try:
        assert page._compliance.isChecked()  # noqa: SLF001  # 默认合规开启
    finally:
        page.close()


def test_general_page_survives_very_long_tool_paths(qapp: QApplication, monkeypatch) -> None:
    """**超长外部工具路径**也不能把「通用」页顶出横向滚动。

    ## 这条用例是一次 CI 失败换来的

    原有的 `test_settings_pages_fit_default_width` 只在本机能过：它依赖
    "当前机器上 ffmpeg 路径有多长"。开发机装了 WinGet 版 ffmpeg 后，路径是

        C:\\Users\\me\\AppData\\Local\\Microsoft\\WinGet\\Packages\\
        Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\\...\\ffmpeg.exe

    单个标签实测 **1452px**，`general` 页在 980px 窗口里横向滚动 768px ——
    而 GitHub Actions 上没装 ffmpeg，路径不同，于是**本机能过、CI 挂**。

    根本原因是 `_pickable()` 给数据库路径插了零宽断点，但**外部工具那一段漏了**。
    这条用例把"长路径"直接注入，不再依赖环境，任何机器上都可复现。
    """
    from modu_workbench.ui_kit.settings import general as general_module

    # 一个没有任何自然断点、比真实 ffmpeg 路径还长的路径
    long_path = (
        "C:\\Users\\someone\\AppData\\Local\\Microsoft\\WinGet\\Packages\\"
        "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\\"
        "ffmpeg-9.0.2-essentials_build\\bin\\ffmpeg.exe"
    )
    assert len(long_path) > 120
    monkeypatch.setattr(general_module, "find_ffmpeg", lambda: long_path)
    monkeypatch.setattr(general_module, "find_ffprobe", lambda: long_path.replace("ffmpeg.exe", "ffprobe.exe"))
    monkeypatch.setattr(general_module, "find_soffice", lambda: long_path.replace("ffmpeg.exe", "soffice.exe"))

    dialog = SettingsDialog()
    dialog.resize(980, 700)
    dialog.show()
    qapp.processEvents()
    try:
        dialog._select("general")            # noqa: SLF001
        qapp.processEvents()
        scroll = dialog._scrolls["general"]  # noqa: SLF001
        bar = scroll.horizontalScrollBar()
        assert bar.maximum() == 0, (
            f"超长工具路径让「通用」页横向滚动了 {bar.maximum()}px —— "
            "外部工具那一段也要用零宽断点（见 general.py 的 _breakable）"
        )
    finally:
        dialog.close()


def test_breakable_inserts_zero_width_breaks() -> None:
    """`_breakable()` 必须给出可断点，且不改变可见文字。

    零宽空格（U+200B）不可见，但会出现在复制的文本里 —— 这是"路径可复制
    又要能折行"的既有取舍，完整原文保留在 tooltip 中。
    """
    from modu_workbench.ui_kit.settings.base import breakable_text

    path = "C:\\a\\" + "x" * 80 + "\\file.db"
    broken = breakable_text(path)
    assert "\u200b" in broken, "没有插入任何零宽断点"
    assert broken.replace("\u200b", "") == path, "插入断点后可见文字被改变了"
    # 最长不可断片段应被压到阈值以内
    longest = max(len(part) for part in broken.split("\u200b"))
    assert longest <= 30, f"仍有 {longest} 字符无法断行"

