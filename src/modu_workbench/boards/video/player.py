"""墨软影视播放器（**兼容壳**）。

v1.1.0 起播放器实现上移到共享层 `ui_kit/media/player.py`，原因见方案 4.0：
第七大板块「墨软动漫」要复用同一个播放器，而架构闸门禁止板块之间互相引用，
所以播放器必须住在 `ui_kit`。

本模块只做**同名转发**，因此：

- `boards/video/*` 的既有调用完全不用改；
- 既有测试（`tests/board_video/test_video_ui.py` 等）继续有效；
- 想用新能力（诊断/弹幕/字幕/自愈信号）时，`VideoPlayerDialog` 已经是新的那个。

渲染路径的变化（V1-f）：`QVideoWidget` → `QGraphicsView + QGraphicsVideoItem`，
从而可以在画面上叠加字幕/弹幕/诊断浮层；异常时用 `video/render_path=widget` 回退。
"""
from __future__ import annotations

from modu_workbench.ui_kit.media.player import (  # noqa: F401
    AUTO_NEXT_COUNTDOWN_S,
    NATIVE_GRACE_HLS_MS,
    NATIVE_GRACE_MS,
    SELF_HEAL_DOWNGRADE_MS,
    SELF_HEAL_DOWNLOAD_MS,
    SELF_HEAL_SWITCH_SOURCE_MS,
    SPEEDS,
    STALL_DOWNGRADE_MS,
    STALL_SWITCH_SOURCE_MS,
    VideoPlayerDialog,
    format_time,
    local_file_url,
    player_available,
)

__all__ = [
    "AUTO_NEXT_COUNTDOWN_S", "NATIVE_GRACE_HLS_MS", "NATIVE_GRACE_MS",
    "SELF_HEAL_DOWNGRADE_MS", "SELF_HEAL_DOWNLOAD_MS", "SELF_HEAL_SWITCH_SOURCE_MS",
    "SPEEDS", "STALL_DOWNGRADE_MS", "STALL_SWITCH_SOURCE_MS", "VideoPlayerDialog",
    "format_time", "local_file_url", "player_available",
]
