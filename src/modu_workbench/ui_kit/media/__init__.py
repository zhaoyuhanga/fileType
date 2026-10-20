"""共享媒体层：播放器 / 视频渲染面 / 弹幕 / 字幕 / 解码后端。

为什么放在 `ui_kit` 而不是 `boards/video`：架构闸门（`tests/architecture/
test_architecture.py`）规定"板块不得引用其他板块"，而媒体组件后来也要被
其它板块复用 —— 把它上移到共享层是唯一合规做法（方案 4.0）。
v2.0.0 移除了第七板块「墨软动漫」，本层现在是「墨软影视」的使用方。

    surface.py   视频渲染面（QGraphicsView + QGraphicsVideoItem / QVideoWidget 回退）
    player.py    播放器窗口（控制条/快捷键/清晰度/倍速/诊断/自动连播）
    danmaku.py   弹幕数据/XML/性能分级/轨道分配/渲染层
    danmaku_bridge.py  弹幕取数接线（弹弹play 只读 + 本地 XML 导入导出）
    subtitle.py  外挂字幕（srt/vtt）解析
    decode.py    解码后端与硬解环境变量注入

代理/缓存/ABR/下载器分别住在 `services/media_proxy/` 与 `core/video/`，
本层只负责"把画面、控制与跨板块适配做出来"。
"""
from __future__ import annotations

from .danmaku import (
    DanmakuItem,
    DanmakuLayer,
    TrackAllocator,
    machine_grade,
    max_lines_for,
    parse_danmaku_xml,
    to_danmaku_xml,
)
from .player import (
    SELF_HEAL_DOWNGRADE_MS,
    SELF_HEAL_DOWNLOAD_MS,
    SELF_HEAL_SWITCH_SOURCE_MS,
    SPEEDS,
    VideoPlayerDialog,
    format_time,
    local_file_url,
    player_available,
)
from .subtitle import SubtitleCue, SubtitleTrack, find_subtitle, load_subtitle
from .surface import (
    RENDER_AUTO,
    RENDER_GRAPHICS,
    RENDER_WIDGET,
    VideoSurface,
    WidgetSurface,
    graphics_available,
    make_surface,
    read_render_path,
)

__all__ = [
    "RENDER_AUTO", "RENDER_GRAPHICS", "RENDER_WIDGET",
    "SELF_HEAL_DOWNGRADE_MS", "SELF_HEAL_DOWNLOAD_MS", "SELF_HEAL_SWITCH_SOURCE_MS",
    "SPEEDS", "DanmakuItem", "DanmakuLayer", "SubtitleCue", "SubtitleTrack",
    "TrackAllocator", "VideoPlayerDialog", "VideoSurface", "WidgetSurface",
    "find_subtitle", "format_time", "graphics_available", "load_subtitle",
    "local_file_url", "machine_grade", "make_surface", "max_lines_for",
    "parse_danmaku_xml", "player_available", "read_render_path", "to_danmaku_xml",
]
