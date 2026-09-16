"""墨软影视的应用级单例（板块内共享；其他板块不直接引用）。"""
from __future__ import annotations

from modu_workbench.core.platform.paths import app_db_path, video_dir
from modu_workbench.core.video import VideoLibrary, VideoRegistry, VideoStorage
from modu_workbench.core.video.sources import registry as video_source_registry

_video_storage: VideoStorage | None = None
_video_library: VideoLibrary | None = None
_video_registry: VideoRegistry | None = None


def video_storage() -> VideoStorage:
    global _video_storage
    if _video_storage is None:
        _video_storage = VideoStorage(app_db_path())
    return _video_storage


def video_registry() -> VideoRegistry:
    """应用级视频源注册表（读取用户启用/优先级/接口地址配置）。"""
    global _video_registry
    if _video_registry is None:
        _video_registry = video_source_registry()
        try:
            _video_registry.load_settings(video_storage())
        except Exception:  # noqa: BLE001  配置损坏不应阻断板块加载
            pass
    return _video_registry


def video_library() -> VideoLibrary:
    global _video_library
    if _video_library is None:
        _video_library = VideoLibrary(video_storage(), video_dir(), video_registry())
    return _video_library
