"""缩略图生成与磁盘缓存。

为什么单独做缓存：PRD 要求「1 万张图片流畅滚动、缩略图 ≤300ms」。
如果每次都从原图解码（尤其是单反的 4000×3000 JPEG 或 HEIC），滚动必然卡。
这里按「内容指纹 + 尺寸」生成一次性缩略图并落盘，之后只读几十 KB 的小文件。

缓存键 = sha1(绝对路径 + 修改时间 + 文件大小 + 目标尺寸)：
文件被改动后自动失效，不需要额外的失效逻辑。
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from PIL import Image, ImageOps

# 常用缩略图尺寸（网格小图 / 网格大图 / 详情预览）
THUMB_SMALL = 160
THUMB_MEDIUM = 320
THUMB_LARGE = 1024
THUMB_SIZES = (THUMB_SMALL, THUMB_MEDIUM, THUMB_LARGE)
# 格子背景：把透明图（PNG）合成到浅灰，避免网格里一片黑
_MATTE = (246, 248, 252)


def _fingerprint(path: Path, size: int) -> str:
    """内容指纹：路径 + mtime + 文件大小 + 目标尺寸。

    故意不用文件内容哈希（大图读取太慢）；文件一变指纹就变，缓存自然失效。
    """
    try:
        stat = path.stat()
        stamp = f"{path}|{int(stat.st_mtime)}|{stat.st_size}|{size}"
    except OSError:
        stamp = f"{path}|0|0|{size}"
    return hashlib.sha1(stamp.encode("utf-8", "replace")).hexdigest()


def thumb_path_for(cache_dir: str | Path, source: str | Path, size: int) -> Path:
    """计算某张图在指定尺寸下的缩略图缓存路径（分级目录，避免单目录文件过多）。"""
    source_path = Path(source)
    digest = _fingerprint(source_path, size)
    return Path(cache_dir) / digest[:2] / f"{digest}_{size}.webp"


def open_oriented(path: str | Path) -> Image.Image:
    """打开图片并按 EXIF 方向摆正、按需合成底色。

    返回的 Image 已 `load()`，可安全脱离 with 使用。
    """
    image = Image.open(path)
    image.load()
    try:
        image = ImageOps.exif_transpose(image)
    except Exception:  # noqa: BLE001  方向信息损坏时按原样处理
        pass
    return _flatten(image)


def _flatten(image: Image.Image) -> Image.Image:
    """把带透明通道的图合成到浅色底（保持其余模式不变）。"""
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        base = Image.new("RGB", image.size, _MATTE)
        rgba = image.convert("RGBA")
        base.paste(rgba, mask=rgba.split()[-1])
        return base
    if image.mode in ("CMYK", "I;16", "I", "F"):
        return image.convert("RGB")
    return image


def make_thumbnail(source: str | Path, cache_dir: str | Path, size: int = THUMB_MEDIUM,
                   *, force: bool = False) -> Path | None:
    """生成（或复用）缩略图，返回缓存文件路径；失败返回 None。"""
    source_path = Path(source)
    if not source_path.is_file():
        return None
    target = thumb_path_for(cache_dir, source_path, size)
    if target.is_file() and not force and target.stat().st_size > 0:
        return target
    try:
        image = open_oriented(source_path)
    except Exception:  # noqa: BLE001  解码失败（损坏/不支持）→ 交给界面显示占位
        return None
    try:
        image.thumbnail((size, size), Image.Resampling.LANCZOS)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        image.save(tmp, format="WEBP", quality=82, method=4)
        tmp.replace(target)
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            image.close()
        except Exception:  # noqa: BLE001
            pass
    return target


class ThumbnailCache:
    """线程安全的缩略图缓存（带内存 LRU，避免重复解码磁盘缩略图）。

    界面（网格视图）在多线程里调用 `get()`：
    - 命中内存 → 直接返回路径；
    - 未命中 → 生成/读取磁盘缩略图，并把最近用到的路径放进 LRU。
    Qt 侧再把路径转成 QPixmap（QPixmap 只能在主线程创建，因此这里只返回路径）。
    """

    def __init__(self, cache_dir: str | Path, *, memory_limit: int = 600):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._limit = max(64, int(memory_limit))
        self._order: list[tuple[str, int]] = []
        self._known: dict[tuple[str, int], str] = {}
        self._lock = threading.RLock()
        self._stats = {"hit": 0, "miss": 0, "fail": 0}

    def get(self, source: str | Path, size: int = THUMB_MEDIUM) -> str | None:
        """返回缩略图路径（失败返回 None）。"""
        key = (str(source), int(size))
        with self._lock:
            cached = self._known.get(key)
            if cached and Path(cached).is_file():
                self._stats["hit"] += 1
                self._touch(key)
                return cached

        produced = make_thumbnail(source, self.cache_dir, size)
        with self._lock:
            if produced is None:
                self._stats["fail"] += 1
                return None
            self._stats["miss"] += 1
            self._known[key] = str(produced)
            self._touch(key)
            self._evict()
            return str(produced)

    def prefetch(self, sources, size: int = THUMB_MEDIUM) -> None:
        """批量预热（导入完成后后台调用，滚动时就不用等）。"""
        for source in sources:
            self.get(source, size)

    def invalidate(self, source: str | Path) -> None:
        """图片被编辑覆盖后调用，丢掉该图的缓存并删除磁盘缩略图。"""
        with self._lock:
            for key in [k for k in self._known if k[0] == str(source)]:
                cached = self._known.pop(key, None)
                if cached:
                    Path(cached).unlink(missing_ok=True)
                if key in self._order:
                    self._order.remove(key)

    def _touch(self, key: tuple[str, int]) -> None:
        if key in self._order:
            self._order.remove(key)
        self._order.append(key)

    def _evict(self) -> None:
        while len(self._order) > self._limit:
            oldest = self._order.pop(0)
            self._known.pop(oldest, None)      # 只丢内存索引，磁盘缓存保留

    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)

    def disk_usage(self) -> int:
        total = 0
        for path in self.cache_dir.rglob("*.webp"):
            try:
                total += path.stat().st_size
            except OSError:
                continue
        return total

    def clear_disk(self) -> int:
        """清空磁盘缓存，返回删除的文件数。"""
        removed = 0
        for path in list(self.cache_dir.rglob("*")):
            if path.is_file():
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    continue
        with self._lock:
            self._known.clear()
            self._order.clear()
        return removed


__all__ = [
    "THUMB_LARGE",
    "THUMB_MEDIUM",
    "THUMB_SIZES",
    "THUMB_SMALL",
    "ThumbnailCache",
    "make_thumbnail",
    "open_oriented",
    "thumb_path_for",
]


def _self_check() -> bool:
    """供打包自检调用：能生成缩略图即视为缓存链路可用。"""
    import tempfile

    try:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "probe.png"
            Image.new("RGB", (400, 300), (120, 160, 200)).save(source)
            produced = make_thumbnail(source, root / "cache", THUMB_SMALL)
            return produced is not None and produced.is_file() and produced.stat().st_size > 0
    except Exception:  # noqa: BLE001
        return False
