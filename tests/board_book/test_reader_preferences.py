"""阅读偏好接线：设置里的字号/行距/进度开关与阅读器、持久化。"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from modu_workbench.boards.book.reader import (
    PREF_FONT_SIZE,
    PREF_LINE_SPACING,
    PREF_REMEMBER,
    PREF_THEME,
    ReaderView,
)
from modu_workbench.core.book import Library, Storage
from modu_workbench.core.platform.paths import SETTINGS_APP, SETTINGS_ORG

KEYS = (PREF_FONT_SIZE, PREF_LINE_SPACING, PREF_REMEMBER, PREF_THEME, "reader/theme")


@pytest.fixture()
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def _clean_settings():
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    for key in KEYS:
        settings.remove(key)
    settings.sync()
    yield
    for key in KEYS:
        settings.remove(key)
    settings.sync()


@pytest.fixture()
def library(tmp_path: Path) -> Library:
    storage = Storage(str(tmp_path / "library.db"))
    lib = Library(storage)
    book = tmp_path / "sample.txt"
    book.write_text(
        "正文 第一章 起点\n\n这是第一章的内容。\n\n"
        "正文 第二章 旅途\n\n这是第二章的内容。\n\n"
        "正文 第三章 终点\n\n这是第三章的内容。",
        encoding="utf-8",
    )
    lib.import_path(str(book))
    return lib


def _settings() -> QSettings:
    return QSettings(SETTINGS_ORG, SETTINGS_APP)


def test_reader_uses_saved_preferences(qapp: QApplication, library: Library) -> None:
    """设置页写的字号/行距/主题以前没人读，阅读器永远是 18px + 1.9 行距。"""
    settings = _settings()
    settings.setValue(PREF_FONT_SIZE, 26)
    settings.setValue(PREF_LINE_SPACING, 220)
    settings.setValue(PREF_THEME, 3)
    settings.sync()

    record = library.list_books()[0]
    reader = ReaderView(library, record)
    try:
        assert reader._font_size == 26  # noqa: SLF001
        assert reader._line_spacing == 220  # noqa: SLF001
        assert reader._theme_index == 3  # noqa: SLF001
        html = reader._chapter_html(0)  # noqa: SLF001
        assert "font-size:26px" in html
        assert "line-height:2.20" in html
    finally:
        reader.close()


def test_font_and_theme_changes_are_persisted(qapp: QApplication, library: Library) -> None:
    record = library.list_books()[0]
    reader = ReaderView(library, record)
    try:
        before = reader._font_size  # noqa: SLF001
        reader.change_font(+2)  # noqa: SLF001
        reader._change_theme(4)  # noqa: SLF001
        assert int(_settings().value(PREF_FONT_SIZE)) == before + 2
        assert int(_settings().value(PREF_THEME)) == 4
    finally:
        reader.close()


def test_remember_progress_off_stops_saving(qapp: QApplication, library: Library) -> None:
    _settings().setValue(PREF_REMEMBER, False)
    _settings().sync()
    record = library.list_books()[0]
    reader = ReaderView(library, record)
    try:
        reader.go_chapter(+1)  # noqa: SLF001
        reader._save_now()  # noqa: SLF001
        after = library.get_book(record.id)
        assert after is not None
        assert after.last_chapter_index == 0, "关闭「记住进度」后不该再写阅读位置"
    finally:
        reader.close()


def test_settings_change_notifies_open_reader(qapp: QApplication, library: Library) -> None:
    from modu_workbench.ui_kit.settings.base import notify_settings_changed

    record = library.list_books()[0]
    reader = ReaderView(library, record)
    try:
        settings = _settings()
        settings.setValue(PREF_FONT_SIZE, 30)
        settings.setValue(PREF_LINE_SPACING, 200)
        settings.sync()
        notify_settings_changed("book")
        assert reader._font_size == 30  # noqa: SLF001
        assert reader._line_spacing == 200  # noqa: SLF001
        assert "font-size:30px" in reader._chapter_html(0)  # noqa: SLF001
    finally:
        reader.close()
