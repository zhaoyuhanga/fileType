"""墨软图库业务层：导入扫描、缩略图、去重、编辑导出、AI 任务编排。

与其它板块一致的职责划分：
- `ImageStorage` 只管 SQLite；
- `ImageLibrary` 负责"业务动作"（导入/收集/打标签/编辑/导出/AI），
  并且是唯一知道缩略图缓存与 AI 客户端的地方。
"""
from __future__ import annotations

import hashlib
import shutil
import threading
from pathlib import Path
from typing import Callable, Iterable, List

from . import ai as ai_mod
from . import edits as edits_mod
from . import enhance as enhance_mod
from .hashing import dhash_file, hamming_distance, sha256_file
from .models import (
    IMAGE_EXTENSIONS,
    AiTask,
    Album,
    EditStep,
    ImageItem,
    ScanResult,
    Tag,
    safe_filename,
)
from .storage import ImageStorage
from .thumbs import THUMB_LARGE, THUMB_MEDIUM, THUMB_SMALL, ThumbnailCache, open_oriented

ProgressFn = Callable[[int, int, str], None]


def is_image_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def scan_image_files(paths: Iterable[str | Path]) -> List[str]:
    """展开文件/文件夹为图片文件列表（递归、去重、保持顺序）。"""
    found: list[str] = []
    for raw in paths:
        target = Path(raw)
        if target.is_file() and is_image_file(target):
            found.append(str(target))
        elif target.is_dir():
            for child in sorted(target.rglob("*")):
                if child.is_file() and is_image_file(child):
                    found.append(str(child))
    return list(dict.fromkeys(found))


def scan_screenshot_dirs() -> List[str]:
    """常见截图目录（用于「截图收集」一键扫描）。"""
    candidates = []
    pictures = Path.home() / "Pictures"
    for name in ("Screenshots", "截图", "屏幕截图", "Screenshot"):
        candidates.append(pictures / name)
    candidates.append(pictures)
    return [str(path) for path in candidates if path.is_dir()]


class ImageLibrary:
    """本地图库（数据库位于应用数据目录 gallery.db）。"""

    def __init__(self, storage: ImageStorage, cache_dir: Path, *, image_dir: Path | None = None):
        self.storage = storage
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.image_dir = Path(image_dir) if image_dir else self.cache_dir.parent / "images"
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self.thumbs = ThumbnailCache(self.cache_dir / "thumbs")

    # ------------------------------------------------------------------ 导入

    def build_item(self, path: str | Path, *, source: str = "local",
                   with_hash: bool = True, with_thumb: bool = True) -> ImageItem:
        """读取一张图片的元数据（尺寸、EXIF、哈希、缩略图）。"""
        file_path = Path(path)
        stat = file_path.stat()
        from .exif import read_exif

        meta = read_exif(file_path)
        width = height = 0
        try:
            with open_oriented(file_path) as image:
                width, height = image.width, image.height
        except Exception:  # noqa: BLE001  读不出尺寸也要入库（界面显示占位）
            pass

        thumb = ""
        if with_thumb:
            produced = self.thumbs.get(file_path, THUMB_MEDIUM)
            thumb = produced or ""

        item = ImageItem(
            path=str(file_path),
            thumb_path=thumb,
            title=file_path.name,
            ext=file_path.suffix.lstrip(".").lower(),
            size_bytes=stat.st_size,
            width=width,
            height=height,
            # 没有 EXIF 拍摄时间时退回文件修改时间，保证时间轴不空
            taken_at=meta.taken_at or int(stat.st_mtime),
            exif_json=meta.to_json(),
            gps_lat=meta.gps_lat,
            gps_lon=meta.gps_lon,
            camera=meta.camera,
            source=source,
            sha256=sha256_file(file_path) if with_hash else "",
            dhash=dhash_file(file_path) if with_hash else "",
        )
        return item

    def import_paths(self, paths: Iterable[str | Path], *, source: str = "local",
                     album_id: int | None = None, skip_duplicates: bool = True,
                     on_progress: ProgressFn | None = None,
                     cancel: threading.Event | None = None,
                     copy_into_library: bool = False) -> ScanResult:
        """批量导入（文件或文件夹），自动去重并统计结果。"""
        files = scan_image_files(paths)
        result = ScanResult()
        total = len(files)
        for index, file_path in enumerate(files, start=1):
            if cancel is not None and cancel.is_set():
                break
            if on_progress:
                on_progress(index - 1, total, f"导入中 {index}/{total}：{Path(file_path).name}")
            try:
                item = self._import_one(file_path, source=source,
                                        skip_duplicates=skip_duplicates,
                                        copy_into_library=copy_into_library)
            except Exception:  # noqa: BLE001  单张失败不影响其余
                result.failed += 1
                continue
            if item is None:
                result.skipped_duplicate += 1
                continue
            result.added += 1
            result.images.append(item)
            if album_id:
                self.storage.add_to_album(album_id, [item.id])
        if on_progress:
            on_progress(len(files), total, result.summary())
        return result

    def _import_one(self, path: str | Path, *, source: str, skip_duplicates: bool,
                    copy_into_library: bool = False) -> ImageItem | None:
        file_path = Path(path)
        if not is_image_file(file_path):
            raise ValueError(f"不支持的图片格式：{file_path.suffix}")

        # 已按路径入库 → 只刷新元数据
        existing = self.storage.get_by_path(str(file_path))
        if existing is not None:
            item = self.build_item(file_path, source=existing.source)
            item.id = existing.id
            item.favorited = existing.favorited
            item.rating = existing.rating
            item.ai_tags = existing.ai_tags
            item.ai_caption = existing.ai_caption
            item.added_at = existing.added_at
            self.storage.upsert_image(item)
            return item

        if copy_into_library and self.image_dir not in file_path.parents:
            target = self._unique_target(self.image_dir / file_path.name)
            shutil.copy2(file_path, target)
            file_path = target

        item = self.build_item(file_path, source=source)

        # 内容级去重：同一份文件换个名字也不重复入库
        if skip_duplicates and item.sha256:
            duplicate = self.storage.find_by_sha256(item.sha256)
            if duplicate is not None:
                return None

        item.id = self.storage.upsert_image(item)
        return item

    @staticmethod
    def _unique_target(target: Path) -> Path:
        if not target.exists():
            return target
        for index in range(2, 1000):
            candidate = target.with_name(f"{target.stem} ({index}){target.suffix}")
            if not candidate.exists():
                return candidate
        return target.with_name(f"{target.stem}_{hashlib.sha1(str(target).encode()).hexdigest()[:6]}"
                                f"{target.suffix}")

    # ------------------------------------------------------------------ 收集

    def collect_from_url(self, url: str, *, timeout: float = 30.0,
                         album_id: int | None = None) -> ImageItem:
        """从网址收集图片：下载到图库目录后入库（来源标记 url）。"""
        import requests

        url = (url or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("请输入以 http(s):// 开头的图片地址")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Referer": url,
        }
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        content_type = str(response.headers.get("Content-Type", "")).lower()
        if content_type and not content_type.startswith("image/"):
            raise ValueError(f"该地址不是图片（返回 {content_type}）")

        name = Path(url.split("?", 1)[0]).name or "collected"
        if "." not in name:
            suffix = "." + (content_type.split("/")[-1].split(";")[0] or "jpg")
            name += suffix if suffix != ".jpeg" else ".jpg"
        target = self._unique_target(self.image_dir / safe_filename(name, "collected"))
        target.write_bytes(response.content)

        item = self.build_item(target, source="url")
        item.url = url
        item.id = self.storage.upsert_image(item)
        if album_id:
            self.storage.add_to_album(album_id, [item.id])
        return item

    def collect_from_clipboard(self, album_id: int | None = None) -> ImageItem | None:
        """从剪贴板收集图片（截图后直接粘贴入库）。返回 None 表示剪贴板没有图片。"""
        from PySide6.QtGui import QGuiApplication

        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return None
        image = clipboard.image()
        if image is None or image.isNull():
            return None

        stamp = __import__("time").strftime("%Y%m%d-%H%M%S")
        target = self._unique_target(self.image_dir / f"clipboard-{stamp}.png")
        if not image.save(str(target), "PNG"):
            raise ValueError("剪贴板图片保存失败")
        item = self.build_item(target, source="clipboard")
        item.id = self.storage.upsert_image(item)
        if album_id:
            self.storage.add_to_album(album_id, [item.id])
        return item

    def copy_into_library(self, path: str | Path) -> Path:
        """把外部图片复制进图库目录。"""
        source = Path(path)
        if not source.is_file():
            raise FileNotFoundError(f"文件不存在：{path}")
        target = self._unique_target(self.image_dir / source.name)
        shutil.copy2(source, target)
        return target

    # ------------------------------------------------------------------ 查询

    def list_images(self, **kwargs) -> List[ImageItem]:
        return self.storage.list_images(**kwargs)

    def get(self, image_id: int) -> ImageItem | None:
        return self.storage.get_image(image_id)

    def thumbnail(self, item: ImageItem, size: int = THUMB_MEDIUM) -> str | None:
        """取缩略图路径（在线条目或文件缺失返回 None）。"""
        if not item.path or not Path(item.path).is_file():
            return None
        return self.thumbs.get(item.path, size)

    def preview(self, item: ImageItem, size: int = THUMB_LARGE) -> str | None:
        return self.thumbnail(item, size)

    def find_duplicates(self, *, max_distance: int = 4) -> List[List[ImageItem]]:
        """按感知哈希聚类出相似/重复图片分组（每组至少 2 张）。"""
        items = [item for item in self.storage.list_images(limit=50000) if item.dhash]
        used: set[int] = set()
        groups: list[list[ImageItem]] = []
        for index, base in enumerate(items):
            if base.id in used:
                continue
            group = [base]
            for other in items[index + 1:]:
                if other.id in used:
                    continue
                distance = hamming_distance(base.dhash, other.dhash)
                if distance is not None and distance <= max_distance:
                    group.append(other)
                    used.add(other.id)
            if len(group) > 1:
                used.add(base.id)
                groups.append(group)
        groups.sort(key=len, reverse=True)
        return groups

    # ------------------------------------------------------------------ 分类

    def create_album(self, name: str) -> int:
        return self.storage.create_album(name)

    def list_albums(self, include_favorite: bool = True) -> List[Album]:
        return self.storage.list_albums(include_favorite)

    def add_to_album(self, album_id: int, image_ids: Iterable[int]) -> int:
        return self.storage.add_to_album(album_id, image_ids)

    def remove_from_album(self, album_id: int, image_ids: Iterable[int]) -> int:
        return self.storage.remove_from_album(album_id, image_ids)

    def delete_album(self, album_id: int) -> bool:
        return self.storage.delete_album(album_id)

    def rename_album(self, album_id: int, new_name: str) -> bool:
        return self.storage.rename_album(album_id, new_name)

    def toggle_favorite(self, image_id: int) -> bool:
        return self.storage.toggle_favorite(image_id)

    def list_tags(self, source: str = "") -> List[Tag]:
        return self.storage.list_tags(source)

    def tag_images(self, image_ids: Iterable[int], tag_name: str, *, source: str = "user") -> int:
        return self.storage.tag_images(image_ids, tag_name, source=source)

    def untag_images(self, image_ids: Iterable[int], tag_name: str) -> int:
        return self.storage.untag_images(image_ids, tag_name)

    def auto_classify(self, image_ids: Iterable[int] | None = None) -> dict[str, int]:
        """本地自动分类：按尺寸/朝向/时间给出基础标签（不联网、不依赖模型）。

        规则化标签便于用户再手动修正；真正的语义分类交给 DeepSeek（analyze_image /
        analyze_by_metadata），两者都在 tags 表里以 user / auto 区分。
        """
        import time

        ids = list(image_ids) if image_ids is not None else [
            item.id for item in self.storage.list_images(limit=50000)
        ]
        counts: dict[str, int] = {}
        for image_id in ids:
            item = self.storage.get_image(image_id)
            if item is None:
                continue
            labels: list[str] = []
            if item.width and item.height:
                ratio = item.aspect
                if ratio > 1.35:
                    labels.append("横图")
                elif ratio < 0.75:
                    labels.append("竖图")
                else:
                    labels.append("方图")
                if item.width >= 3000 or item.height >= 3000:
                    labels.append("高分辨率")
                if item.width <= 400 and item.height <= 400:
                    labels.append("小图")
            if item.camera:
                labels.append("相机拍摄")
            else:
                labels.append("无 EXIF")
            if item.taken_at:
                hour = time.localtime(item.taken_at).tm_hour
                if hour >= 20 or hour < 5:
                    labels.append("夜景时段")
                elif 6 <= hour < 10:
                    labels.append("清晨")
                elif 16 <= hour < 19:
                    labels.append("傍晚")
            if item.source == "clipboard":
                labels.append("截图")
            for label in labels:
                self.storage.tag_images([image_id], label, source="auto")
                counts[label] = counts.get(label, 0) + 1
        return counts

    # ------------------------------------------------------------------ 编辑

    def load_steps(self, image_id: int) -> List[EditStep]:
        return self.storage.load_edit_steps(image_id)

    def save_steps(self, image_id: int, steps: Iterable[EditStep]) -> None:
        self.storage.save_edit_steps(image_id, steps)

    def clear_steps(self, image_id: int) -> None:
        self.storage.clear_edit_steps(image_id)

    def render(self, item: ImageItem, steps: list[EditStep] | None = None):
        """按当前编辑栈渲染出 PIL.Image（调用方负责 close）。"""
        steps = self.load_steps(item.id) if steps is None else steps
        if steps and item.path:
            return edits_mod.render_edited(item.path, steps)
        return open_oriented(item.path)

    def export_edited(self, item: ImageItem, target: str | Path, *, fmt: str = "jpg",
                      steps: list[EditStep] | None = None, quality: int = 92,
                      register: bool = True, source: str = "edit") -> Path:
        """导出编辑结果；默认另存为新图并入库（保留原图）。"""
        image = self.render(item, steps)
        try:
            path = edits_mod.export_image(image, target, fmt=fmt, quality=quality)
        finally:
            try:
                image.close()
            except Exception:  # noqa: BLE001
                pass
        if register:
            produced = self.build_item(path, source=source)
            produced.id = self.storage.upsert_image(produced)
        return path

    def export_default_path(self, item: ImageItem, *, fmt: str = "jpg",
                            tag: str = "edited") -> Path:
        """生成一个不覆盖原图的默认输出路径。"""
        stem = safe_filename(Path(item.path).stem if item.path else item.title, "image")
        return self._unique_target(self.image_dir / f"{stem}_{tag}.{fmt}")

    def overwrite_original(self, item: ImageItem, *, fmt: str = "", steps: list[EditStep] | None = None,
                           quality: int = 95) -> Path:
        """覆盖原图（界面必须二次确认）。"""
        if not item.path:
            raise ValueError("在线条目没有本地文件，无法覆盖")
        target = Path(item.path)
        image = self.render(item, steps)
        try:
            edits_mod.export_image(image, target, fmt=fmt or item.ext or "jpg", quality=quality)
        finally:
            try:
                image.close()
            except Exception:  # noqa: BLE001
                pass
        self.thumbs.invalidate(target)                 # 原图变了，缩略图必须重算
        refreshed = self.build_item(target, source=item.source or "edit", with_hash=False)
        refreshed.id = item.id
        refreshed.favorited = item.favorited
        refreshed.rating = item.rating
        refreshed.ai_tags = item.ai_tags
        refreshed.ai_caption = item.ai_caption
        refreshed.added_at = item.added_at
        self.storage.upsert_image(refreshed)
        return target

    # ------------------------------------------------------------------ AI 优化

    def run_enhancement(self, item: ImageItem, kind: str, params: dict | None = None, *,
                        target: str | Path | None = None, register: bool = True) -> Path:
        """对一张图执行本地增强并落盘入库（不覆盖原图）。"""
        if not item.path:
            raise ValueError("该条目没有本地文件")
        image = open_oriented(item.path)
        try:
            processed = enhance_mod.apply_enhancement(kind, image, params or {})
        finally:
            try:
                image.close()
            except Exception:  # noqa: BLE001
                pass
        fmt = "png" if kind == "background_remove" and processed.mode == "RGBA" else "jpg"
        out = Path(target) if target else self.export_default_path(item, fmt=fmt,
                                                                   tag=f"ai_{kind}")
        try:
            edits_mod.export_image(processed, out, fmt=fmt, quality=94)
        finally:
            try:
                processed.close()
            except Exception:  # noqa: BLE001
                pass
        if register:
            produced = self.build_item(out, source="ai")
            produced.id = self.storage.upsert_image(produced)
        return out

    def compare_metrics(self, item: ImageItem, kind: str, params: dict | None = None) -> tuple[dict, dict]:
        """返回 (处理前, 处理后) 的画质指标，用于前后对比给客观依据。"""
        if not item.path:
            raise ValueError("该条目没有本地文件")
        image = open_oriented(item.path)
        try:
            before = enhance_mod.compute_quality_metrics(image)
            processed = enhance_mod.apply_enhancement(kind, image, params or {})
        finally:
            try:
                image.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            after = enhance_mod.compute_quality_metrics(processed)
        finally:
            try:
                processed.close()
            except Exception:  # noqa: BLE001
                pass
        return before, after

    # ---------- AI 文本（大模型） ----------

    def ai_client(self, config: ai_mod.AiConfig | None = None) -> ai_mod.DeepSeekClient:
        if config is None:
            config = load_ai_config(self.storage)
        return ai_mod.DeepSeekClient(config)

    @staticmethod
    def _llm_router():  # noqa: ANN205
        """应用级大模型路由器（多类型 / 多配置 / 优先级降级）。"""
        from modu_workbench.app import context as app_context

        return app_context.llm_router()

    def ai_analyze(self, item: ImageItem, *, use_vision: bool = True,
                   config: ai_mod.AiConfig | None = None, prefer_vision: bool = True) -> dict:
        """用大模型生成描述与标签，并写回库。

        调用顺序：**图片大模型**（看图，需允许上传且已配置）→ **文字大模型**（只用元数据）。
        同一类型内部按「设置 → 大模型」里的优先级依次尝试，失败自动降级。
        显式传入 `config` 时走单配置老路径（测试/自定义场景）。
        """
        allow_upload = (config.allow_upload if config is not None
                        else load_ai_config(self.storage).allow_upload)
        result: dict
        if config is not None:
            result = self._analyze_one(item, config, use_vision=use_vision)
        else:
            result = self._analyze_routed(item, use_vision=use_vision and prefer_vision,
                                          allow_upload=allow_upload)

        tags = [str(t) for t in (result.get("tags") or [])]
        for tag in tags:
            self.storage.tag_images([item.id], tag, source="auto")
        self.storage.update_ai_fields(
            item.id,
            tags=",".join(tags),
            caption=str(result.get("caption") or ""),
        )
        return result

    def _analyze_one(self, item: ImageItem, config: ai_mod.AiConfig, *,
                     use_vision: bool) -> dict:
        """单配置分析：看清图是否可用，不可用退回元数据模式。"""
        client = ai_mod.DeepSeekClient(config)
        if use_vision and config.allow_upload and item.path:
            try:
                return client.analyze_image(item.path)
            except Exception:  # noqa: BLE001  退回文本模式
                pass
        return client.analyze_by_metadata(item)

    def _analyze_routed(self, item: ImageItem, *, use_vision: bool,
                        allow_upload: bool) -> dict:
        """按「图片 → 文字」的顺序路由，同一类型内自动降级。"""
        from modu_workbench.core.llm import KIND_IMAGE, KIND_TEXT

        router = self._llm_router()
        kinds: list[str] = []
        if use_vision and allow_upload and item.path and router.has_any(KIND_IMAGE):
            kinds.append(KIND_IMAGE)
        kinds.append(KIND_TEXT)

        problems: list[str] = []
        for kind in kinds:
            try:
                result, _profile = router.run(
                    kind,
                    lambda profile, k=kind: self._analyze_one(
                        item,
                        ai_mod.config_from_profile(profile, kind=k, allow_upload=allow_upload),
                        use_vision=(k == KIND_IMAGE),
                    ),
                )
            except Exception as error:  # noqa: BLE001  换下一个类型
                problems.append(str(error))
                continue
            if isinstance(result, dict) and (result.get("tags") or result.get("caption")):
                return result
            problems.append(f"{kind}：没有解析出有效内容")
        raise ai_mod.AiRequestError(
            "AI 分析失败：\n" + "\n".join(problems) if problems else "AI 分析失败")

    def ai_suggest_keywords(self, query: str) -> list[str]:
        """自然语言检索 → 关键词（文字大模型，多配置降级）。"""
        text, _profile = self._llm_router().run(
            "text", lambda profile: ai_mod.DeepSeekClient(
                ai_mod.config_from_profile(profile, kind="text")).suggest_search_keywords(query))
        return list(text or [])

    def ai_suggest_edit_params(self, description: str) -> dict:
        """自然语言修图需求 → 本地可执行参数（文字大模型，多配置降级）。"""
        params, _profile = self._llm_router().run(
            "text", lambda profile: ai_mod.DeepSeekClient(
                ai_mod.config_from_profile(profile, kind="text")).suggest_edit_params(description))
        return dict(params or {})

    # ------------------------------------------------------------------ 维护

    def delete_images(self, image_ids: Iterable[int], *, remove_file: bool = False) -> int:
        ids = list(image_ids)
        for image_id in ids:
            item = self.storage.get_image(image_id)
            if item is None or not remove_file or not item.path:
                continue
            try:
                Path(item.path).unlink(missing_ok=True)
                self.thumbs.invalidate(item.path)
            except OSError:
                pass
        return self.storage.delete_images(ids)

    def missing_files(self) -> List[ImageItem]:
        return [item for item in self.storage.list_images(limit=50000)
                if item.path and not Path(item.path).is_file()]

    def disk_usage(self) -> int:
        total = 0
        for item in self.storage.list_images(limit=50000):
            if item.path:
                try:
                    total += Path(item.path).stat().st_size
                except OSError:
                    continue
        return total

    def cleanup_missing(self) -> int:
        """清理文件已丢失的条目。"""
        ids = [item.id for item in self.missing_files()]
        return self.storage.delete_images(ids) if ids else 0


# --------------------------------------------------------------------------- 配置读写


def load_ai_config(storage: ImageStorage) -> ai_mod.AiConfig:
    """从图库设置 + 环境变量读取 DeepSeek 配置（环境变量优先）。"""
    import os

    def value(key: str, default: str = "") -> str:
        return storage.get_setting(f"{ai_mod.SETTING_PREFIX}/{key}", default)

    def flag(key: str, default: bool = False) -> bool:
        raw = value(key, "1" if default else "0")
        return str(raw).lower() in ("1", "true", "yes", "on")

    return ai_mod.AiConfig(
        api_key=os.environ.get("MODU_DEEPSEEK_KEY") or value("api_key"),
        base_url=os.environ.get("MODU_DEEPSEEK_BASE") or value("base_url", ai_mod.DEFAULT_BASE_URL),
        model=value("model", ai_mod.DEFAULT_MODEL),
        vision_model=value("vision_model", ai_mod.DEFAULT_VISION_MODEL),
        timeout=float(value("timeout", "60") or 60),
        max_tokens=int(value("max_tokens", "1024") or 1024),
        temperature=float(value("temperature", "0.3") or 0.3),
        allow_upload=flag("allow_upload", False),
    )


def save_ai_config(storage: ImageStorage, config: ai_mod.AiConfig) -> None:
    storage.set_setting(f"{ai_mod.SETTING_PREFIX}/api_key", config.api_key)
    storage.set_setting(f"{ai_mod.SETTING_PREFIX}/base_url", config.base_url)
    storage.set_setting(f"{ai_mod.SETTING_PREFIX}/model", config.model)
    storage.set_setting(f"{ai_mod.SETTING_PREFIX}/vision_model", config.vision_model)
    storage.set_setting(f"{ai_mod.SETTING_PREFIX}/timeout", str(config.timeout))
    storage.set_setting(f"{ai_mod.SETTING_PREFIX}/max_tokens", str(config.max_tokens))
    storage.set_setting(f"{ai_mod.SETTING_PREFIX}/temperature", str(config.temperature))
    storage.set_setting(f"{ai_mod.SETTING_PREFIX}/allow_upload", "1" if config.allow_upload else "0")


__all__ = [
    "ImageLibrary",
    "ScanResult",
    "THUMB_LARGE",
    "THUMB_MEDIUM",
    "THUMB_SMALL",
    "is_image_file",
    "load_ai_config",
    "save_ai_config",
    "scan_image_files",
    "scan_screenshot_dirs",
]
