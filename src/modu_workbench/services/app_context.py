"""应用级共享上下文：唯一书库实例（跨板块复用）。

初始化只发生在主线程（板块构造期）；Python GIL 保证单次赋值安全，
无需额外锁（曾观察到 Qt offscreen 下 threading.Lock 被外部因素阻塞的现象）。
"""
from __future__ import annotations

from modu_workbench.core.gallery import ImageLibrary, ImageStorage
from modu_workbench.core.llm import LlmStorage, ModelRouter
from modu_workbench.core.music import MusicLibrary, MusicRegistry, MusicPlayer, MusicStorage, registry
from modu_workbench.core.book import Library, Storage
from modu_workbench.core.video import VideoLibrary, VideoRegistry, VideoStorage
from modu_workbench.core.video.sources import registry as video_source_registry

from .config import (
    ensure_legacy_migration,
    gallery_cache_dir,
    gallery_db_path,
    gallery_dir,
    llm_db_path,
    music_db_path,
    music_dir,
    video_db_path,
    video_dir,
)

_storage: Storage | None = None
_library: Library | None = None
_music_storage: MusicStorage | None = None
_music_library: MusicLibrary | None = None
_music_player: MusicPlayer | None = None
_music_registry: MusicRegistry | None = None
_video_storage: VideoStorage | None = None
_video_library: VideoLibrary | None = None
_video_registry: VideoRegistry | None = None
_image_storage: ImageStorage | None = None
_image_library: ImageLibrary | None = None
_llm_storage: LlmStorage | None = None
_llm_router: ModelRouter | None = None


def storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = Storage(ensure_legacy_migration())
    return _storage


def library() -> Library:
    global _library
    if _library is None:
        _library = Library(storage())
    return _library


def music_storage() -> MusicStorage:
    global _music_storage
    if _music_storage is None:
        _music_storage = MusicStorage(music_db_path())
    return _music_storage


def music_library() -> MusicLibrary:
    global _music_library
    if _music_library is None:
        _music_library = MusicLibrary(music_storage(), music_dir())
    return _music_library


def music_registry() -> MusicRegistry:
    """应用级音源注册表（读取用户启用/优先级配置）。"""
    global _music_registry
    if _music_registry is None:
        _music_registry = registry()
    return _music_registry


def music_player() -> MusicPlayer:
    """应用级单例播放器：切换板块/页面时音乐不中断。"""
    global _music_player
    if _music_player is None:
        _music_player = MusicPlayer()
    return _music_player


# ---------- 墨软影视 ----------

def video_storage() -> VideoStorage:
    global _video_storage
    if _video_storage is None:
        _video_storage = VideoStorage(video_db_path())
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


# ---------- 墨软图库 ----------

def image_storage() -> ImageStorage:
    global _image_storage
    if _image_storage is None:
        _image_storage = ImageStorage(gallery_db_path())
    return _image_storage


def image_library() -> ImageLibrary:
    global _image_library
    if _image_library is None:
        _image_library = ImageLibrary(image_storage(), gallery_cache_dir(),
                                      image_dir=gallery_dir())
    return _image_library


# ---------- 大模型（跨板块共用） ----------

def llm_storage() -> LlmStorage:
    global _llm_storage
    if _llm_storage is None:
        _llm_storage = LlmStorage(llm_db_path())
    return _llm_storage


def llm_router() -> ModelRouter:
    """应用级大模型路由器（多类型 / 多配置 / 优先级降级）。"""
    global _llm_router
    if _llm_router is None:
        _llm_router = ModelRouter(llm_storage())
    return _llm_router
