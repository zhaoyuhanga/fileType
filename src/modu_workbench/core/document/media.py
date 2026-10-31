"""墨软文档：文档里的二进制素材（目前是 Word 里的图片）。

**为什么不让图片直接进 IR**：`DocumentIR` 会被逐字写进版本快照（`doc_versions.ir_json`），
把 base64 图片塞进去，一份带图文档的 40 个快照就能吃掉几十 MB。所以约定：

- 解析时把图片落到数据目录的 `document/media/`（按内容 sha1 命名，天然去重）；
- 块上只留一句 JSON 级引用：`block.meta["image"] = {"file": "…png", "name": "原始文件名", …}`；
- 写出（Word 重新嵌入）与预览/HTML（转 data URI）时按引用把字节读回来。

素材跟着"数据目录"走，因此 `MODU_DATA_DIR` 一改就整体挪走（测试与便携模式都靠它）。
"""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any, Optional

from modu_workbench.core.platform.paths import document_dir

#: 单张图片的上限（超过就不落缓存，改为在文档里留一行提示）
MAX_IMAGE_BYTES = 20 * 1024 * 1024

#: python-docx / 浏览器认得的常见图片类型；EMF/WMF 只在 Word 里能显示，写出时留占位
_EXT_BY_TYPE = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/x-ms-bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/x-emf": ".emf",
    "image/emf": ".emf",
    "image/x-wmf": ".wmf",
    "image/wmf": ".wmf",
}
_MIME_BY_EXT = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".bmp": "image/bmp", ".tiff": "image/tiff", ".tif": "image/tiff", ".webp": "image/webp",
    ".svg": "image/svg+xml",
}
#: python-docx 的 add_picture 只支持这几种；其它（emf/wmf/svg）写出时给占位
DOCX_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif")


class MediaError(RuntimeError):
    """素材无法缓存（过大 / 无内容）。"""


def media_dir() -> Path:
    """素材目录（不存在时创建）。"""
    path = document_dir() / "media"
    path.mkdir(parents=True, exist_ok=True)
    return path


def extension_for(content_type: str = "", name: str = "") -> str:
    """按 MIME 或原文件名推断扩展名（认不出时按 .png）。"""
    lowered = (content_type or "").split(";")[0].strip().lower()
    if lowered in _EXT_BY_TYPE:
        return _EXT_BY_TYPE[lowered]
    suffix = Path(name or "").suffix.lower()
    if suffix in _MIME_BY_EXT or suffix in (".emf", ".wmf"):
        return suffix
    return ".png"


def store_image(data: bytes, *, content_type: str = "", name: str = "",
                width_pt: float = 0.0, height_pt: float = 0.0) -> dict[str, Any]:
    """把图片写进素材目录，返回可直接放进 `block.meta["image"]` 的引用。

    同名（同内容）图片只会存一份：文件名就是内容的 sha1。
    """
    if not data:
        raise MediaError("图片内容为空")
    if len(data) > MAX_IMAGE_BYTES:
        raise MediaError(f"图片过大（{len(data) / 1024 / 1024:.1f} MB > "
                         f"{MAX_IMAGE_BYTES / 1024 / 1024:.0f} MB）")
    extension = extension_for(content_type, name)
    digest = hashlib.sha1(data).hexdigest()[:16]
    file_name = f"{digest}{extension}"
    target = media_dir() / file_name
    if not target.is_file():
        try:
            target.write_bytes(data)
        except OSError as error:
            raise MediaError(f"图片写入失败：{error}") from error
    return {
        "file": file_name,
        "name": Path(name or file_name).name,
        "ext": extension,
        "bytes": len(data),
        "width_pt": round(float(width_pt or 0.0), 2),
        "height_pt": round(float(height_pt or 0.0), 2),
    }


def _safe_name(file_name: str) -> str:
    """只接受裸文件名（防止快照里的引用被改写成路径穿越）。"""
    text = str(file_name or "").strip()
    if not text or Path(text).name != text:
        return ""
    return text


def image_bytes(block: Any) -> Optional[bytes]:
    """按块的 `meta["image"]` 读回字节（没有引用 / 文件没了 → None）。"""
    meta = getattr(block, "meta", None) or {}
    info = meta.get("image")
    if not isinstance(info, dict):
        return None
    name = _safe_name(info.get("file", ""))
    if not name:
        return None
    path = media_dir() / name
    if not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def data_uri(block: Any) -> str:
    """把图片转成 data URI（HTML/PDF 预览自包含用；读不到时返回空串）。"""
    data = image_bytes(block)
    if not data:
        return ""
    meta = getattr(block, "meta", None) or {}
    info = meta.get("image") if isinstance(meta.get("image"), dict) else {}
    extension = str((info or {}).get("ext") or "").lower()
    mime = _MIME_BY_EXT.get(extension, "image/png")
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def image_label(block: Any) -> str:
    """图片的展示名（原文件名优先，其次块文字）。"""
    meta = getattr(block, "meta", None) or {}
    info = meta.get("image") if isinstance(meta.get("image"), dict) else {}
    return str((info or {}).get("name") or getattr(block, "text", "") or "图片")


def cache_stats() -> dict[str, int]:
    """素材缓存概况（设置页/文档用）。"""
    folder = media_dir()
    files = [path for path in folder.glob("*") if path.is_file()]
    return {"files": len(files), "bytes": sum(path.stat().st_size for path in files)}


def clear_cache() -> dict[str, int]:
    """清空素材缓存，返回清掉的（文件数, 字节数）。

    清掉之后，已打开文档里的图片会退化成 `[图片] 名字` 占位并给出提示；
    重新打开源文件（或重新解析带图的 Word/HTML/MD）会自动补回来。
    """
    stats = cache_stats()
    folder = media_dir()
    for path in folder.glob("*"):
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                continue
    return stats


__all__ = [
    "DOCX_IMAGE_EXTS",
    "MAX_IMAGE_BYTES",
    "MediaError",
    "cache_stats",
    "clear_cache",
    "data_uri",
    "extension_for",
    "image_bytes",
    "image_label",
    "media_dir",
    "store_image",
]
