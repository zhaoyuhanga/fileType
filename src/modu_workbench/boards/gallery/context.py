"""墨软图库的应用级单例（板块内共享；其他板块不直接引用）。"""
from __future__ import annotations

from modu_workbench.core.gallery import ImageLibrary, ImageStorage
from modu_workbench.core.llm.context import llm_router, llm_storage  # noqa: F401  图库 AI 复用大模型配置
from modu_workbench.core.platform.paths import app_db_path, gallery_cache_dir, gallery_dir

_image_storage: ImageStorage | None = None
_image_library: ImageLibrary | None = None


def image_storage() -> ImageStorage:
    global _image_storage
    if _image_storage is None:
        _image_storage = ImageStorage(app_db_path())
    return _image_storage


def image_library() -> ImageLibrary:
    global _image_library
    if _image_library is None:
        _image_library = ImageLibrary(image_storage(), gallery_cache_dir(), image_dir=gallery_dir())
    return _image_library
