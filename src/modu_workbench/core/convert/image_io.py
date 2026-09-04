"""图片互转（Pillow）：jpg / png / webp / bmp / gif。

- 动图（GIF）转其它格式时取第一帧（与旧版行为一致）；
- 转 JPG 自动合成白底去透明。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

QUALITY_JPG = 92

_SAVE_KWARGS: dict[str, dict] = {
    "jpg": {"quality": QUALITY_JPG, "optimize": True},
    "webp": {"quality": 90, "method": 6},
    "png": {"optimize": True},
}


def convert_image(source_path: str | Path, target_format: str, output_path: str | Path) -> None:
    from PIL import Image

    pil_format = {"jpg": "JPEG"}.get(target_format, target_format.upper())
    with Image.open(source_path) as img:
        frame = img
        if getattr(frame, "is_animated", False) and target_format != "gif":
            frame.seek(0)

        if target_format == "jpg" and frame.mode in ("RGBA", "LA", "P"):
            rgba = frame.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[-1])
            frame = background
        elif frame.mode not in ("RGB", "L") and target_format in ("jpg", "bmp"):
            frame = frame.convert("RGB")

        kwargs = dict(_SAVE_KWARGS.get(target_format, {}))
        frame.save(output_path, format=pil_format, **kwargs)
