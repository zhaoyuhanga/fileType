"""墨软图库：数据模型（图片 / 相册 / 标签 / 编辑记录 / AI 任务）。

设计要点：
- `ImageItem` 既是「已入库的本地图片」也是「在线收集的图片」（url 非空、path 为空）；
- 缩略图不存进数据库，而是按 `thumb_key` 落到缓存目录，数据库只记路径（省体积）；
- 去重靠两级哈希：`sha256`（完全相同）+ `dhash`（视觉相近，汉明距离判定）；
- 面部/场景等 AI 标签与用户标签共用 tags 表，靠 `source` 区分，便于「自动分类结果可手动修正」。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# 支持的图片扩展名（本地导入/扫描共用）
IMAGE_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff",
    ".avif", ".ico", ".heic", ".heif", ".jfif", ".ppm", ".pgm",
)

# 保存/导出可选的格式
EXPORT_FORMATS = ("jpg", "png", "webp", "bmp", "tiff")

# 支持的收集来源（用于来源标记与筛选）
SOURCE_LABELS = {
    "local": "本地导入",
    "folder": "文件夹扫描",
    "url": "网址收集",
    "clipboard": "剪贴板",
    "camera": "拍照",
    "screenshot": "截图",
    "album": "系统相册",
    "edit": "编辑产出",
    "ai": "AI 优化产出",
    "unknown": "未知来源",
}

# 排序方式
SORT_MODES = {
    "taken": "按拍摄时间",
    "added": "按导入时间",
    "name": "按文件名",
    "size": "按文件大小",
    "rating": "按评分",
}
DEFAULT_SORT = "added"

# 视图模式
VIEW_MODES = ("grid", "waterfall", "timeline")
VIEW_MODE_LABELS = {"grid": "网格", "waterfall": "瀑布流", "timeline": "时间轴"}

# 编辑记录的步骤类型
STEP_LABELS = {
    "crop": "裁剪",
    "rotate": "旋转",
    "flip": "翻转",
    "filter": "滤镜",
    "adjust": "调节",
    "text": "文字",
    "sticker": "贴纸",
    "draw": "涂鸦",
    "mosaic": "马赛克",
    "border": "边框",
    "ai": "AI 优化",
}

# AI 任务状态
TASK_STATES = ("pending", "running", "done", "failed", "canceled")
TASK_STATE_LABELS = {
    "pending": "排队中",
    "running": "处理中",
    "done": "已完成",
    "failed": "失败",
    "canceled": "已取消",
}

_ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]')


def safe_filename(name: str, fallback: str = "image") -> str:
    """把名称转成合法文件名。"""
    cleaned = _ILLEGAL.sub("_", (name or "").strip())
    cleaned = re.sub(r"_{2,}", "_", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    return (cleaned or fallback)[:110]


def format_size(size_bytes: int) -> str:
    if not size_bytes or size_bytes < 0:
        return "-"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1048576:
        return f"{size_bytes / 1024:.0f} KB"
    if size_bytes < 1073741824:
        return f"{size_bytes / 1048576:.1f} MB"
    return f"{size_bytes / 1073741824:.2f} GB"


def format_timestamp(seconds: int) -> str:
    """Unix 秒 → 本地可读时间。"""
    import time

    if not seconds:
        return "-"
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(seconds)))
    except (ValueError, OSError):
        return "-"


def source_label(source: str) -> str:
    return SOURCE_LABELS.get(source or "unknown", source or "未知来源")


@dataclass
class ImageItem:
    """一张图片（本地文件或在线收集）。"""

    id: int = 0
    path: str = ""
    url: str = ""              # 在线收集的来源地址（本地图库为收藏来源信息）
    thumb_path: str = ""
    title: str = ""
    ext: str = ""
    mime: str = ""
    size_bytes: int = 0
    width: int = 0
    height: int = 0
    taken_at: int = 0          # EXIF 拍摄时间（Unix 秒），取不到用文件时间
    added_at: int = 0
    exif_json: str = ""
    gps_lat: float | None = None
    gps_lon: float | None = None
    camera: str = ""
    source: str = "local"
    sha256: str = ""
    dhash: str = ""            # 64 位感知哈希（16 位十六进制）
    album_id: int = 0
    favorited: bool = False
    rating: int = 0
    ai_tags: str = ""          # 逗号分隔的自动/AI 标签
    ai_caption: str = ""       # AI 生成的描述
    last_opened_at: int = 0

    # ---------- 便捷属性 ----------

    @property
    def exists(self) -> bool:
        return bool(self.path) and Path(self.path).is_file()

    @property
    def online_only(self) -> bool:
        """还没落地成文件（在线收集后未下载）。"""
        return not self.path

    @property
    def playable(self) -> bool:
        return self.exists or bool(self.url)

    @property
    def resolution(self) -> str:
        return f"{self.width}×{self.height}" if self.width and self.height else "-"

    @property
    def size_text(self) -> str:
        return format_size(self.size_bytes)

    @property
    def taken_text(self) -> str:
        return format_timestamp(self.taken_at or self.added_at)

    @property
    def added_text(self) -> str:
        return format_timestamp(self.added_at)

    @property
    def aspect(self) -> float:
        """宽高比，瀑布流排版用（拿不到尺寸时按 4:3 估）。"""
        if self.width and self.height:
            return self.width / self.height
        return 4 / 3

    @property
    def tag_list(self) -> list[str]:
        return [t for t in (self.ai_tags or "").split(",") if t]

    @property
    def source_text(self) -> str:
        return source_label(self.source)

    def display(self) -> str:
        return self.title or (Path(self.path).name if self.path else (self.url or "未命名图片"))

    def to_remote(self) -> "ImageItem":
        return self


@dataclass
class Album:
    id: int = 0
    name: str = ""
    cover_path: str = ""
    kind: str = "album"        # album / favorite
    created_at: int = 0
    image_count: int = 0

    @property
    def is_favorite(self) -> bool:
        return self.kind == "favorite"


@dataclass
class Tag:
    id: int = 0
    name: str = ""
    color: str = "#4f5bd5"
    source: str = "user"       # user / auto
    use_count: int = 0

    @property
    def is_auto(self) -> bool:
        return self.source == "auto"


@dataclass
class EditStep:
    """一次编辑操作（非破坏性历史栈的一步）。"""

    step: str
    params: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return STEP_LABELS.get(self.step, self.step)

    def to_json(self) -> dict:
        return {"step": self.step, "params": self.params}

    @classmethod
    def from_json(cls, data: dict) -> "EditStep":
        return cls(step=str(data.get("step", "")), params=dict(data.get("params") or {}))


@dataclass
class AiTask:
    id: int = 0
    image_id: int = 0
    kind: str = ""
    state: str = "pending"
    progress: int = 0
    result_path: str = ""
    message: str = ""
    created_at: int = 0
    finished_at: int = 0

    @property
    def state_text(self) -> str:
        return TASK_STATE_LABELS.get(self.state, self.state)


@dataclass
class ScanResult:
    """一次导入/扫描的结果统计。"""

    added: int = 0
    skipped_duplicate: int = 0
    skipped_unsupported: int = 0
    failed: int = 0
    images: list[ImageItem] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.added + self.skipped_duplicate + self.skipped_unsupported + self.failed

    def summary(self) -> str:
        parts = [f"新增 {self.added}"]
        if self.skipped_duplicate:
            parts.append(f"重复 {self.skipped_duplicate}")
        if self.skipped_unsupported:
            parts.append(f"不支持 {self.skipped_unsupported}")
        if self.failed:
            parts.append(f"失败 {self.failed}")
        return "，".join(parts)


__all__ = [
    "DEFAULT_SORT",
    "EXPORT_FORMATS",
    "IMAGE_EXTENSIONS",
    "SORT_MODES",
    "SOURCE_LABELS",
    "STEP_LABELS",
    "TASK_STATES",
    "TASK_STATE_LABELS",
    "VIEW_MODES",
    "VIEW_MODE_LABELS",
    "AiTask",
    "Album",
    "EditStep",
    "ImageItem",
    "ScanResult",
    "Tag",
    "format_size",
    "format_timestamp",
    "safe_filename",
    "source_label",
]
