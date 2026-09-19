"""转换能力探测：**当前这台机器上**哪些动作真的能跑。

为什么需要它：部分格式依赖外部能力 ——
- `ods`、Word/Excel → PDF 的"高保真"路径需要 LibreOffice（soffice）；
- 所有音视频转换需要 ffmpeg；其中 `amr` 还需要 ffmpeg 带 `libopencore_amrnb` 编码器
  （很多精简构建没有）；
- `rar` 解压需要系统提供 unrar / 7z；
- `yaml` 需要 PyYAML。

没有探测时，界面照样把动作列出来，用户点了才拿到报错（历史反馈里"点了没反应/报错"
大多属于这类）。这里的约定：**界面置灰 + 写明缺什么**，运行时再用同样的理由兜底。
"""
from __future__ import annotations

import importlib.util
import subprocess
from dataclasses import dataclass
from functools import lru_cache

from .registry import ConverterAction

# 动作 id / 目标格式 → 依赖的能力键
_MEDIA_FORMATS = {
    "mp4", "mov", "avi", "mkv", "webm", "flv", "wmv", "m4v", "mpg", "mpeg", "ts", "3gp", "ogv",
    "m4a", "mp3", "wav", "flac", "aac", "ogg", "opus", "wma", "m4b", "aiff", "amr", "ac3",
}
_SOFFICE_FORMATS = {"ods", "doc"}


@dataclass(frozen=True)
class Capability:
    """一项外部能力的状态（界面提示用）。"""

    key: str
    label: str
    available: bool
    hint: str


@lru_cache(maxsize=1)
def has_soffice() -> bool:
    from .office_io import find_soffice

    return bool(find_soffice())


@lru_cache(maxsize=1)
def has_ffmpeg() -> bool:
    from modu_workbench.core.platform.media import find_ffmpeg

    return bool(find_ffmpeg())


@lru_cache(maxsize=1)
def has_unrar() -> bool:
    """rarfile 能不能找到可用的解压后端（unrar / 7z / bsdtar）。"""
    try:
        import rarfile
    except ImportError:
        return False
    try:
        rarfile.tool_setup()
        return True
    except Exception:  # noqa: BLE001  rarfile 找不到工具时抛 RarExecError
        return False


@lru_cache(maxsize=1)
def has_pyyaml() -> bool:
    return importlib.util.find_spec("yaml") is not None


@lru_cache(maxsize=1)
def ffmpeg_encoders() -> frozenset[str]:
    """ffmpeg 支持的编码器名集合（拿不到就返回空集合，调用方按"未知"处理）。"""
    from modu_workbench.core.platform.media import find_ffmpeg

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return frozenset()
    try:
        proc = subprocess.run([ffmpeg, "-hide_banner", "-encoders"],
                              capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    names: set[str] = set()
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith(("A", "V", "S")):
            names.add(parts[1])
    return frozenset(names)


def capability_summary() -> list[Capability]:
    """给界面用的一句话能力清单。"""
    return [
        Capability("soffice", "LibreOffice", has_soffice(),
                   "未检测到 LibreOffice：ODS 与 Word/Excel 转 PDF 的「高保真」路径不可用"
                   "（装好 LibreOffice 后重启应用即可）"),
        Capability("ffmpeg", "ffmpeg", has_ffmpeg(),
                   "未检测到 ffmpeg：所有音视频转换不可用（可用 MODU_FFMPEG 指定路径）"),
        Capability("unrar", "unrar/7z", has_unrar(),
                   "未检测到 unrar / 7z：RAR 解压不可用（zip / tar / gz / bz2 / xz 不受影响）"),
        Capability("yaml", "PyYAML", has_pyyaml(),
                   "缺少 PyYAML：json/xml/ini ↔ yaml 的转换不可用（pip install PyYAML）"),
    ]


def blocked_reason(action: ConverterAction | None) -> str | None:
    """这个动作在当前机器上能不能跑？不能跑就返回中文原因，能跑返回 None。"""
    if action is None:
        return None
    target = action.target_format
    if action.kind == "media" or target in _MEDIA_FORMATS:
        if not has_ffmpeg():
            return "未检测到 ffmpeg：音视频转换不可用（可用 MODU_FFMPEG 指定路径）"
        if target == "amr" and ffmpeg_encoders() and "libopencore_amrnb" not in ffmpeg_encoders():
            return "当前 ffmpeg 构建不带 AMR 编码器（libopencore_amrnb），无法输出 .amr"
    if action.kind in ("word", "sheet"):
        source = action.source_formats[0] if action.source_formats else ""
        if (source in _SOFFICE_FORMATS or source == "doc") and not has_soffice():
            return "需要 LibreOffice（soffice）才能读取 ODS / 旧版 DOC：装好后重启应用即可"
    if action.id == "rar-extract" and not has_unrar():
        return "需要系统提供 unrar / 7z 才能解压 RAR"
    if action.kind == "data" and target == "yaml" and not has_pyyaml():
        return "缺少 PyYAML：pip install PyYAML"
    return None
