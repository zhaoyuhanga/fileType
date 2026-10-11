"""动漫设置页测试：新键的默认值、类型与往返保存。

设置项的"读出来对不对/存进去能不能读回来"是这类页最容易出错的点
（历史上踩过 `bool("false") is True` 的坑），所以这里逐键验证。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from modu_workbench.ui_kit.settings.anime import AnimeSettingsPage

ORG = "ModuWorkbench"
APP = "modu-workbench"

#: 新设置键 → (默认值, 类型)
EXPECTED_DEFAULTS: dict[str, tuple] = {
    "anime/auto_next": (True, bool),
    "anime/download_dir": ("", str),
    "anime/segment_concurrency": (6, int),
    "anime/download_parallel": (2, int),
    "anime/danmaku_enabled": (False, bool),
    "anime/danmaku_app_id": ("", str),
    "anime/danmaku_app_secret": ("", str),
    "anime/danmaku_opacity": (80, int),
    "anime/danmaku_speed": (8, int),
    "anime/danmaku_max_lines": (0, int),
    "anime/danmaku_block_words": ("", str),
    "anime/bangumi_enabled": (True, bool),
    "anime/update_on_start": (False, bool),
}


@pytest.fixture()
def temp_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qapp):
    """把 QSettings 指到临时目录，避免污染真实设置。

    必须先有 qapp：构造 QWidget 之前没有 QApplication 时 Qt 会**直接 abort**
    （不是抛异常），表现为 pytest 进程静默退出、一行输出都没有 —— 这个坑很贵，
    所以夹具显式依赖 qapp（session 级、offscreen）。
    """
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    for key in EXPECTED_DEFAULTS:
        QSettings(ORG, APP).remove(key)
    yield QSettings(ORG, APP)
    for key in EXPECTED_DEFAULTS:
        QSettings(ORG, APP).remove(key)


def _page(qapp=None) -> AnimeSettingsPage:  # noqa: ANN001
    page = AnimeSettingsPage()
    page.load()
    return page


def test_defaults_are_sane(temp_settings) -> None:  # noqa: ANN001
    """未设置过时，读出来的默认值必须是"保守但可用"的。"""
    page = _page()
    try:
        assert page._auto_next.isChecked() is True
        assert page._danmaku_enabled.isChecked() is False, "弹幕默认关（避免打开就刷屏）"
        assert page._bangumi_enabled.isChecked() is True
        assert page._update_on_start.isChecked() is False, "启动时不自动联网更新规则/追番"
        assert 2 <= page._segment_concurrency.value() <= 16
        assert 1 <= page._download_parallel.value() <= 4
        assert 20 <= page._danmaku_opacity.value() <= 100
        assert 4 <= page._danmaku_speed.value() <= 12
    finally:
        page.deleteLater()


def test_roundtrip_save_and_load(temp_settings) -> None:  # noqa: ANN001
    """改一遍 → 保存 → 新建页面重新载入，值必须一致（含 secret 与屏蔽词）。"""
    page = _page()
    page._auto_next.setChecked(False)
    page._danmaku_enabled.setChecked(True)
    page._bangumi_enabled.setChecked(False)
    page._update_on_start.setChecked(True)
    page._segment_concurrency.setValue(3)
    page._download_parallel.setValue(4)
    page._danmaku_app_id.setText("app-123")
    page._danmaku_app_secret.setText("secret-xyz")
    page._danmaku_opacity.setValue(55)
    page._danmaku_speed.setValue(11)
    page._danmaku_lines.setValue(40)
    page._danmaku_block.setText("广告,/^\\d+$/")
    page.save()
    page.deleteLater()

    again = _page()
    try:
        assert again._auto_next.isChecked() is False
        assert again._danmaku_enabled.isChecked() is True
        assert again._bangumi_enabled.isChecked() is False
        assert again._update_on_start.isChecked() is True
        assert again._segment_concurrency.value() == 3
        assert again._download_parallel.value() == 4
        assert again._danmaku_app_id.text() == "app-123"
        assert again._danmaku_app_secret.text() == "secret-xyz"
        assert again._danmaku_opacity.value() == 55
        assert again._danmaku_speed.value() == 11
        assert again._danmaku_lines.value() == 40
        assert "广告" in again._danmaku_block.text()
    finally:
        again.deleteLater()


def test_booleans_survive_string_representation(temp_settings) -> None:  # noqa: ANN001
    """把 bool 写成字符串再读回来必须仍是 bool（历史坑：`bool("false") is True`）。"""
    settings = temp_settings
    settings.setValue("anime/auto_next", "false")
    settings.setValue("anime/danmaku_enabled", "true")
    settings.sync()

    page = _page()
    try:
        assert page._auto_next.isChecked() is False
        assert page._danmaku_enabled.isChecked() is True
    finally:
        page.deleteLater()


def test_secret_field_is_masked(temp_settings) -> None:  # noqa: ANN001
    """AppSecret 必须是密码框（不该在屏幕上明文显示）。"""
    from PySide6.QtWidgets import QLineEdit

    page = _page()
    try:
        assert page._danmaku_app_secret.echoMode() == QLineEdit.EchoMode.Password
        assert page._danmaku_app_id.echoMode() == QLineEdit.EchoMode.Normal
    finally:
        page.deleteLater()


def test_danmaku_cache_clear_is_safe_when_empty(temp_settings, tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """清空弹幕缓存：目录为空/不存在时也不能抛错。"""
    page = _page()
    try:
        assert hasattr(page, "_clear_danmaku"), "设置页应提供「清空弹幕缓存」入口"
        page._clear_danmaku()
    finally:
        page.deleteLater()
