"""图片互转（Pillow）。

覆盖：
- 可写目标：jpg / png / webp / bmp / gif / tiff / ico / tga / pcx / ppm，以及 **pdf**（图片转 PDF）；
- 只读输入：psd / dds / jp2（Pillow 能读不能写，因此只作为源）；
- 动图（GIF 等）转静态格式时取第一帧；转 PDF 时**保留全部帧**为多页；
- 转 JPG / BMP / PCX / PPM 这类不支持透明通道的格式时自动合成白底。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

QUALITY_JPG = 92
# 单张图最大边长（ICO 最大 256，超出会被 Pillow 拒绝或产出异常尺寸）
ICO_MAX_SIZE = 256

# 目标格式 → Pillow 保存格式名
_PIL_FORMAT = {
    "jpg": "JPEG",
    "png": "PNG",
    "webp": "WEBP",
    "bmp": "BMP",
    "gif": "GIF",
    "tiff": "TIFF",
    "ico": "ICO",
    "tga": "TGA",
    "pcx": "PCX",
    "ppm": "PPM",
    "pdf": "PDF",
}

_SAVE_KWARGS: dict[str, dict] = {
    "jpg": {"quality": QUALITY_JPG, "optimize": True},
    "webp": {"quality": 90, "method": 6},
    "png": {"optimize": True},
}

# 这些格式没有 alpha 通道，必须先压成 RGB，否则 Pillow 直接报错
_OPAQUE_TARGETS = ("jpg", "bmp", "pcx", "ppm")


def _flatten(frame: Image.Image) -> Image.Image:
    """把带透明通道的图合成到白底上（转不支持透明的格式时用）。"""
    rgba = frame.convert("RGBA")
    background = Image.new("RGB", rgba.size, (255, 255, 255))
    background.paste(rgba, mask=rgba.split()[-1])
    return background


def _adapt(frame: Image.Image, target_format: str) -> Image.Image:
    """按目标格式调整色彩模式与尺寸（每种格式的坑都不一样，集中在这里）。"""
    if target_format in _OPAQUE_TARGETS and frame.mode in ("RGBA", "LA", "P", "PA"):
        frame = _flatten(frame)
    elif target_format in _OPAQUE_TARGETS and frame.mode not in ("RGB", "L"):
        frame = frame.convert("RGB")
    elif target_format == "ico":
        # ICO 只能是方形，且 Pillow 默认会**自动生成 16/24/32… 一整套尺寸**
        # （32×24 的图会变成 24×18 这种怪尺寸）。这里显式指定单一尺寸。
        edge = min(ICO_MAX_SIZE, max(frame.size))
        copy = frame.copy()
        if copy.size != (edge, edge):
            copy = copy.resize((edge, edge), Image.LANCZOS)
        if copy.mode not in ("RGBA", "RGB", "P", "L"):
            copy = copy.convert("RGBA")
        frame = copy
    elif target_format == "pcx" and frame.mode not in ("P", "L", "RGB", "1"):
        frame = frame.convert("RGB")
    elif target_format == "ppm" and frame.mode not in ("RGB", "L"):
        frame = frame.convert("RGB")
    return frame


def _save_pdf(img: Image.Image, output_path: str | Path) -> None:
    """图片 → PDF：动图逐帧写成多页，静态图写单页。"""
    frames: list[Image.Image] = []
    if getattr(img, "is_animated", False):
        try:
            for index in range(getattr(img, "n_frames", 1)):
                img.seek(index)
                frames.append(img.convert("RGB"))
        except EOFError:
            pass
        finally:
            img.seek(0)
    if not frames:
        frame = img if img.mode == "RGB" else img.convert("RGB")
        frames = [frame]
    first, rest = frames[0], frames[1:]
    first.save(output_path, format="PDF", resolution=150.0,
               save_all=bool(rest), append_images=rest)


def convert_image(source_path: str | Path, target_format: str, output_path: str | Path) -> None:
    pil_format = _PIL_FORMAT.get(target_format, target_format.upper())
    with Image.open(source_path) as img:
        if target_format == "pdf":
            _save_pdf(img, output_path)
            return

        frame = img
        if getattr(frame, "is_animated", False) and target_format != "gif":
            frame.seek(0)
        frame = _adapt(frame, target_format)
        kwargs = dict(_SAVE_KWARGS.get(target_format, {}))
        if target_format == "ico":
            # 不给 sizes 时 Pillow 会写出一整套 16/24/32… 尺寸，尺寸随机漂移
            kwargs["sizes"] = [frame.size]
        frame.save(output_path, format=pil_format, **kwargs)
