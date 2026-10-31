"""墨软图库核心：图片元数据、存储、缩略图、编辑管线、增强算法与 AI 集成。

分层（与其它板块一致）：
- models.py   数据模型（图片 / 相册 / 标签 / 编辑步骤 / AI 任务）
- storage.py  SQLite（图片 / 相册 / 标签 / 编辑历史 / AI 任务 / 配置）
- hashing.py  内容哈希与感知哈希（精确去重 + 相似识别）
- exif.py     EXIF 解析（拍摄时间 / 相机 / 光圈快门 ISO / GPS）
- thumbs.py   缩略图生成与磁盘缓存（万张图流畅滚动的关键）
- edits.py    非破坏性编辑管线（裁剪/旋转/滤镜/调节/文字/贴纸/涂鸦/马赛克/边框）
- enhance.py  本地增强算法（numpy + Pillow）：一键增强/超分/降噪/去模糊/白平衡/
              去雾/人像柔化/背景移除/消除/老照片/风格化
- ai.py       DeepSeek 集成（**仅文本**：标题/标签/描述、按描述推荐修图参数）
- library.py  业务动作（导入 / 收集 / 分类 / 编辑导出 / AI 任务编排）
"""
from __future__ import annotations

from .ai import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_VISION_MODEL,
    AiConfig,
    AiConfigError,
    AiRequestError,
    DeepSeekClient,
)
from .edits import (
    ADJUST_LABELS,
    DEFAULT_ADJUST,
    FILTERS,
    FILTER_LABELS,
    apply_step,
    apply_steps,
    export_image,
    render_edited,
)
from .enhance import (
    SPECS,
    SPEC_BY_KEY,
    STYLE_PRESETS,
    EnhanceSpec,
    apply_enhancement,
    compute_quality_metrics,
    enhance_labels,
)
from .exif import ExifData, read_exif
from .hashing import dhash_file, dhash_image, hamming_distance, sha256_file
from .library import (
    ImageLibrary,
    is_image_file,
    load_ai_config,
    save_ai_config,
    scan_image_files,
    scan_screenshot_dirs,
)
from .models import (
    DEFAULT_SORT,
    EXPORT_FORMATS,
    IMAGE_EXTENSIONS,
    SORT_MODES,
    SOURCE_LABELS,
    STEP_LABELS,
    TASK_STATES,
    TASK_STATE_LABELS,
    VIEW_MODES,
    VIEW_MODE_LABELS,
    AiTask,
    Album,
    EditStep,
    ImageItem,
    ScanResult,
    Tag,
    format_size,
    format_timestamp,
    safe_filename,
    source_label,
)
from .storage import FAVORITE_ALBUM_NAME, ImageStorage
from .thumbs import (
    THUMB_LARGE,
    THUMB_MEDIUM,
    THUMB_SIZES,
    THUMB_SMALL,
    ThumbnailCache,
    make_thumbnail,
    open_oriented,
)

__all__ = [
    "ADJUST_LABELS",
    "DEFAULT_ADJUST",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DEFAULT_SORT",
    "DEFAULT_VISION_MODEL",
    "EXPORT_FORMATS",
    "FAVORITE_ALBUM_NAME",
    "FILTERS",
    "FILTER_LABELS",
    "IMAGE_EXTENSIONS",
    "SORT_MODES",
    "SOURCE_LABELS",
    "SPECS",
    "SPEC_BY_KEY",
    "STEP_LABELS",
    "STYLE_PRESETS",
    "TASK_STATES",
    "TASK_STATE_LABELS",
    "THUMB_LARGE",
    "THUMB_MEDIUM",
    "THUMB_SIZES",
    "THUMB_SMALL",
    "VIEW_MODES",
    "VIEW_MODE_LABELS",
    "AiConfig",
    "AiConfigError",
    "AiRequestError",
    "AiTask",
    "Album",
    "DeepSeekClient",
    "EditStep",
    "EnhanceSpec",
    "ExifData",
    "ImageItem",
    "ImageLibrary",
    "ImageStorage",
    "ScanResult",
    "Tag",
    "ThumbnailCache",
    "apply_enhancement",
    "apply_step",
    "apply_steps",
    "compute_quality_metrics",
    "dhash_file",
    "dhash_image",
    "enhance_labels",
    "export_image",
    "format_size",
    "format_timestamp",
    "hamming_distance",
    "is_image_file",
    "load_ai_config",
    "make_thumbnail",
    "open_oriented",
    "read_exif",
    "render_edited",
    "safe_filename",
    "save_ai_config",
    "scan_image_files",
    "scan_screenshot_dirs",
    "sha256_file",
    "source_label",
]
