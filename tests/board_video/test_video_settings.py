"""影视设置页测试：v1.1.0 新增的播放/缓存/弹幕三组键。

这些键里有几个**改动会改变播放行为**（硬解、清晰度策略、缓冲档位、弹幕开关），
所以"默认值是什么"必须被测试钉住 —— 默认值跑偏就是行为回归。

注意：构造 QWidget 前必须有 `QApplication`，否则 Qt 会**直接 abort**
（进程静默退出、没有任何 traceback），因此夹具显式依赖 session 级的 `qapp`。
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings

from modu_workbench.ui_kit.settings.video import VideoSettingsPage

ORG = "ModuWorkbench"
APP = "modu-workbench"

#: v1.1.0 新增键 → 期望默认值（类型由读取方式决定）
EXPECTED_DEFAULTS: dict[str, object] = {
    "video/media_backend": "auto",
    "video/hw_decode": "auto",
    "video/hw_texture_conversion": True,
    "video/hw_profile_mismatch": False,
    "video/render_path": "auto",
    "video/quality_policy": "auto",
    "video/auto_downgrade": True,
    "video/auto_upgrade": False,
    "video/auto_next": True,
    "video/buffer_mode": "balanced",
    "video/cache_quota_mb": 1024,
    "video/prefetch_segments": 3,
    "video/proxy_concurrency": 16,
    "video/segment_concurrency": 6,
    "video/download_parallel": 2,
    "video/download_speed_limit_mbps": 0,
    "video/skip_ad_segments": False,
    "video/danmaku_enabled": False,
    "video/danmaku_opacity": 80,
    "video/danmaku_speed": 8,
    "video/danmaku_max_lines": 0,
    "video/danmaku_block_words": "",
    "video/danmaku_dedupe": False,
}


@pytest.fixture()
def temp_settings(tmp_path, monkeypatch, qapp):  # noqa: ANN001
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    for key in EXPECTED_DEFAULTS:
        QSettings(ORG, APP).remove(key)
    yield QSettings(ORG, APP)
    for key in EXPECTED_DEFAULTS:
        QSettings(ORG, APP).remove(key)


def _page() -> VideoSettingsPage:
    page = VideoSettingsPage()
    page.load()
    return page


def test_new_defaults_match_documented_values(temp_settings) -> None:  # noqa: ANN001
    page = _page()
    try:
        assert page._media_backend.currentData() == "auto"
        assert page._hw_decode.currentData() == "auto"
        assert page._texture_conversion.isChecked() is True
        assert page._profile_mismatch.isChecked() is False
        assert page._render_path.currentData() == "auto"
        assert page._quality_policy.currentData() == "auto"
        assert page._auto_downgrade.isChecked() is True, "卡顿降档默认开（这是修卡顿的关键）"
        assert page._auto_upgrade.isChecked() is False, "升档默认关（避免来回横跳）"
        assert page._auto_next.isChecked() is True
        assert page._buffer_mode.currentData() == "balanced"
        assert page._cache_quota.value() == 1024
        assert page._prefetch.value() == 3
        assert page._proxy_concurrency.value() == 16
        assert page._segment_concurrency.value() == 6
        assert page._download_parallel.value() == 2
        assert page._speed_limit.value() == 0, "默认不限速"
        assert page._skip_ads.isChecked() is False, "广告分片跳过默认关（误判会丢正片）"
        assert page._danmaku_enabled.isChecked() is False
        assert page._danmaku_opacity.value() == 80
        assert page._danmaku_speed.value() == 8
        assert page._danmaku_lines.value() == 0, "0 = 按性能自动分级"
    finally:
        page.deleteLater()


def test_roundtrip_playback_and_cache_keys(temp_settings) -> None:  # noqa: ANN001
    page = _page()
    page._media_backend.setCurrentIndex(page._media_backend.findData("ffmpeg"))
    page._hw_decode.setCurrentIndex(page._hw_decode.findData("off"))
    page._texture_conversion.setChecked(False)
    page._profile_mismatch.setChecked(True)
    page._render_path.setCurrentIndex(page._render_path.findData("widget"))
    page._quality_policy.setCurrentIndex(page._quality_policy.findData("720p"))
    page._auto_downgrade.setChecked(False)
    page._auto_upgrade.setChecked(True)
    page._auto_next.setChecked(False)
    page._buffer_mode.setCurrentIndex(page._buffer_mode.findData("high"))
    page._cache_quota.setValue(4096)
    page._prefetch.setValue(8)
    page._proxy_concurrency.setValue(24)
    page._segment_concurrency.setValue(12)
    page._download_parallel.setValue(4)
    page._speed_limit.setValue(5)
    page._skip_ads.setChecked(True)
    page._danmaku_enabled.setChecked(True)
    page._danmaku_app_id.setText("app-v")
    page._danmaku_secret.setText("secret-v")
    page._danmaku_opacity.setValue(40)
    page._danmaku_speed.setValue(12)
    page._danmaku_lines.setValue(25)
    page._danmaku_block.setText("广告,/^\\d+$/")
    page._danmaku_dedupe.setChecked(True)
    page.save()
    page.deleteLater()

    again = _page()
    try:
        assert again._media_backend.currentData() == "ffmpeg"
        assert again._hw_decode.currentData() == "off"
        assert again._texture_conversion.isChecked() is False
        assert again._profile_mismatch.isChecked() is True
        assert again._render_path.currentData() == "widget"
        assert again._quality_policy.currentData() == "720p"
        assert again._auto_downgrade.isChecked() is False
        assert again._auto_upgrade.isChecked() is True
        assert again._auto_next.isChecked() is False
        assert again._buffer_mode.currentData() == "high"
        assert again._cache_quota.value() == 4096
        assert again._prefetch.value() == 8
        assert again._proxy_concurrency.value() == 24
        assert again._segment_concurrency.value() == 12
        assert again._download_parallel.value() == 4
        assert again._speed_limit.value() == 5
        assert again._skip_ads.isChecked() is True
        assert again._danmaku_enabled.isChecked() is True
        assert again._danmaku_app_id.text() == "app-v"
        assert again._danmaku_secret.text() == "secret-v"
        assert again._danmaku_opacity.value() == 40
        assert again._danmaku_speed.value() == 12
        assert again._danmaku_lines.value() == 25
        assert "广告" in again._danmaku_block.text()
        assert again._danmaku_dedupe.isChecked() is True
    finally:
        again.deleteLater()


def test_danmaku_secret_is_masked(temp_settings) -> None:  # noqa: ANN001
    from PySide6.QtWidgets import QLineEdit

    page = _page()
    try:
        assert page._danmaku_secret.echoMode() == QLineEdit.EchoMode.Password
    finally:
        page.deleteLater()


def test_buffer_mode_choices_are_the_documented_three(temp_settings) -> None:  # noqa: ANN001
    """缓冲档位必须是省流/均衡/高质三档（方案 3.1.5 定的数值）。"""
    page = _page()
    try:
        modes = [page._buffer_mode.itemData(i) for i in range(page._buffer_mode.count())]
        assert modes == ["low", "balanced", "high"]
    finally:
        page.deleteLater()


def test_quality_policy_choices_match_abr(temp_settings) -> None:  # noqa: ANN001
    """清晰度策略必须与 `core/video/abr.py` 认得的取值一致（否则设置形同虚设）。"""
    from modu_workbench.core.video.abr import POLICY_TARGET_HEIGHT

    page = _page()
    try:
        values = {page._quality_policy.itemData(i) for i in range(page._quality_policy.count())}
        assert values <= set(POLICY_TARGET_HEIGHT), f"ABR 不认这些策略：{values}"
        assert {"auto", "highest", "1080p", "720p", "lowest"} <= values
    finally:
        page.deleteLater()


def test_clear_cache_reports_without_error(temp_settings) -> None:  # noqa: ANN001
    """清空分片缓存必须幂等且不抛错（缓存目录可能是空的）。"""
    page = _page()
    try:
        page._clear_cache()
        page._clear_cache()
        assert "MB" in page._cache_usage.text() or page._cache_usage.text() == ""
    finally:
        page.deleteLater()
