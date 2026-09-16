"""图片编辑管线：把「非破坏性历史栈」的步骤按顺序应用到位图。

设计：编辑不直接改图，而是记录一串步骤（EditStep）。任何时刻都能：
- 重新执行整个栈（重做/换参数）；
- 只回退最后一步（撤销）；
- 导出时再把最终结果写盘。

步骤类型与参数：
- crop    : {"box": [x, y, w, h]}
- rotate  : {"angle": 90, "expand": true}
- flip    : {"axis": "horizontal" | "vertical"}
- filter  : {"name": "grayscale", "amount": 1.0}
- adjust  : {"brightness":.., "contrast":.., "saturation":.., "sharpness":.., "temperature":..}
- text    : {"text":.., "x":.., "y":.., "size":.., "color":[r,g,b], "shadow":bool}
- sticker : {"path":.., "x":.., "y":.., "scale":.., "rotation":..}
- draw    : {"points": [[x,y],..], "color":[r,g,b], "width":..}（涂鸦/橡皮）
- mosaic  : {"box":[x,y,w,h], "block":12, "mode":"mosaic"|"blur"}
- border  : {"width":.., "color":[r,g,b], "radius":..}
- ai      : {"kind": "auto_enhance", "params": {...}}（复用 enhance 模块）
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from . import enhance as enhance_mod
from .models import EditStep

# 滤镜预设：名称 → (显示名, 处理函数)
FILTERS: dict[str, tuple[str, object]] = {}


def _register_filters() -> None:
    def grayscale(image: Image.Image) -> Image.Image:
        return ImageOps.grayscale(image).convert("RGB")

    def sepia(image: Image.Image) -> Image.Image:
        gray = ImageOps.grayscale(image).convert("RGB")
        return enhance_mod._as_image(  # noqa: SLF001  复用内部转换
            enhance_mod._as_array(gray) * (1.12, 1.0, 0.82)  # noqa: SLF001
        )

    def warm(image: Image.Image) -> Image.Image:
        return enhance_mod._as_image(  # noqa: SLF001
            enhance_mod._as_array(image) * (1.08, 1.01, 0.93)  # noqa: SLF001
        )

    def cool(image: Image.Image) -> Image.Image:
        return enhance_mod._as_image(  # noqa: SLF001
            enhance_mod._as_array(image) * (0.93, 1.0, 1.09)  # noqa: SLF001
        )

    def film(image: Image.Image) -> Image.Image:
        base = ImageEnhance.Contrast(image).enhance(1.12)
        base = ImageEnhance.Color(base).enhance(0.92)
        return enhance_mod._as_image(  # noqa: SLF001
            enhance_mod._as_array(base) * (1.04, 1.0, 0.96)  # noqa: SLF001
        )

    def fade(image: Image.Image) -> Image.Image:
        washed = Image.blend(image, Image.new("RGB", image.size, (235, 235, 238)), 0.22)
        return ImageEnhance.Contrast(washed).enhance(0.94)

    def vivid(image: Image.Image) -> Image.Image:
        return ImageEnhance.Color(ImageEnhance.Contrast(image).enhance(1.10)).enhance(1.35)

    def mono_high(image: Image.Image) -> Image.Image:
        gray = ImageOps.grayscale(image).convert("RGB")
        return ImageEnhance.Contrast(gray).enhance(1.35)

    FILTERS.update({
        "none": ("原图", lambda image: image),
        "grayscale": ("黑白", grayscale),
        "sepia": ("复古", sepia),
        "warm": ("暖调", warm),
        "cool": ("冷调", cool),
        "film": ("胶片", film),
        "fade": ("褪色", fade),
        "vivid": ("鲜艳", vivid),
        "mono_high": ("硬黑白", mono_high),
    })


_register_filters()

FILTER_LABELS = {key: value[0] for key, value in FILTERS.items()}

DEFAULT_ADJUST = {
    "brightness": 1.0,
    "contrast": 1.0,
    "saturation": 1.0,
    "sharpness": 1.0,
    "temperature": 0.0,     # -1 ~ +1，负冷正暖
}

ADJUST_LABELS = {
    "brightness": "亮度",
    "contrast": "对比度",
    "saturation": "饱和度",
    "sharpness": "锐化",
    "temperature": "色温",
}


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """找一个可用的中文字体；找不到退回 Pillow 内置位图字体。"""
    candidates = (
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    )
    for path in candidates:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size)
            except Exception:  # noqa: BLE001
                continue
    try:
        return ImageFont.load_default(size)
    except TypeError:      # 旧版 Pillow 无 size 参数
        return ImageFont.load_default()


def _apply_temperature(image: Image.Image, temperature: float) -> Image.Image:
    if abs(temperature) < 1e-3:
        return image
    factors = {
        "r": 1.0 + 0.10 * temperature,
        "b": 1.0 - 0.10 * temperature,
    }
    array = enhance_mod._as_array(image)      # noqa: SLF001
    array[:, :, 0] *= factors["r"]
    array[:, :, 2] *= factors["b"]
    return enhance_mod._as_image(array)       # noqa: SLF001


def apply_steps(image: Image.Image, steps: list[EditStep]) -> Image.Image:
    """把步骤栈依次作用到图上，返回新图（不修改入参）。"""
    current = image
    for step in steps:
        try:
            current = apply_step(current, step)
        except Exception:  # noqa: BLE001
            # 单步失败（例如贴纸文件丢了）不应让整个编辑丢失
            continue
    return current


def apply_step(image: Image.Image, step: EditStep) -> Image.Image:
    """应用单个编辑步骤。"""
    params = step.params or {}
    kind = step.step

    if kind == "crop":
        box = params.get("box") or []
        if len(box) == 4:
            left, top, width, height = (int(v) for v in box)
            right, bottom = left + width, top + height
            left, top = max(0, left), max(0, top)
            right, bottom = min(image.width, right), min(image.height, bottom)
            if right - left >= 1 and bottom - top >= 1:
                return image.crop((left, top, right, bottom))
        return image

    if kind == "rotate":
        angle = float(params.get("angle", 0))
        expand = bool(params.get("expand", True))
        if abs(angle) < 0.01:
            return image
        # 90 的整数倍用 transpose，避免重采样损失
        if abs(angle % 90) < 0.01:
            turns = int(angle // 90) % 4
            result = image
            for _ in range(turns):
                result = result.transpose(Image.Transpose.ROTATE_270)
            return result
        return image.rotate(angle, expand=expand, resample=Image.Resampling.BICUBIC,
                            fillcolor=(255, 255, 255))

    if kind == "flip":
        axis = str(params.get("axis", "horizontal"))
        if axis == "vertical":
            return image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        return image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)

    if kind == "filter":
        name = str(params.get("name", "none"))
        amount = float(params.get("amount", 1.0))
        handler = FILTERS.get(name, FILTERS["none"])[1]
        filtered = handler(image)
        if amount >= 0.999:
            return filtered
        return Image.blend(image, filtered, max(0.0, min(1.0, amount)))

    if kind == "adjust":
        result = image
        brightness = float(params.get("brightness", 1.0))
        contrast = float(params.get("contrast", 1.0))
        saturation = float(params.get("saturation", 1.0))
        sharpness = float(params.get("sharpness", 1.0))
        temperature = float(params.get("temperature", 0.0))
        if abs(brightness - 1.0) > 1e-3:
            result = ImageEnhance.Brightness(result).enhance(brightness)
        if abs(contrast - 1.0) > 1e-3:
            result = ImageEnhance.Contrast(result).enhance(contrast)
        if abs(saturation - 1.0) > 1e-3:
            result = ImageEnhance.Color(result).enhance(saturation)
        if abs(sharpness - 1.0) > 1e-3:
            result = ImageEnhance.Sharpness(result).enhance(sharpness)
        result = _apply_temperature(result, temperature)
        return result

    if kind == "text":
        text = str(params.get("text", ""))
        if not text:
            return image
        result = image.convert("RGB").copy()
        draw = ImageDraw.Draw(result)
        size = int(params.get("size", max(16, image.height // 16)))
        font = _load_font(size)
        color = tuple(int(v) for v in (params.get("color") or [255, 255, 255]))[:3]
        position = (int(params.get("x", 12)), int(params.get("y", 12)))
        if params.get("shadow", True):
            draw.text((position[0] + 2, position[1] + 2), text, font=font, fill=(0, 0, 0))
        draw.text(position, text, font=font, fill=color)
        return result

    if kind == "sticker":
        path = str(params.get("path", ""))
        if not path or not Path(path).is_file():
            return image
        try:
            sticker = Image.open(path).convert("RGBA")
        except Exception:  # noqa: BLE001
            return image
        scale = max(0.02, float(params.get("scale", 0.25)))
        width = max(8, int(image.width * scale))
        height = max(8, int(sticker.height * width / max(1, sticker.width)))
        sticker = sticker.resize((width, height), Image.Resampling.LANCZOS)
        rotation = float(params.get("rotation", 0))
        if abs(rotation) > 0.01:
            sticker = sticker.rotate(rotation, expand=True, resample=Image.Resampling.BICUBIC)
        base = image.convert("RGBA")
        position = (int(params.get("x", 0)), int(params.get("y", 0)))
        base.alpha_composite(sticker, dest=(max(0, position[0]), max(0, position[1])))
        return base.convert("RGB")

    if kind == "draw":
        points = params.get("points") or []
        if len(points) < 2:
            return image
        result = image.convert("RGB").copy()
        draw = ImageDraw.Draw(result)
        color = tuple(int(v) for v in (params.get("color") or [255, 0, 0]))[:3]
        width = max(1, int(params.get("width", 6)))
        erase = bool(params.get("erase", False))
        coords = [(int(p[0]), int(p[1])) for p in points if isinstance(p, (list, tuple)) and len(p) >= 2]
        if erase:
            # 橡皮：用白色覆盖（简化处理，非真实透明擦除）
            draw.line(coords, fill=(255, 255, 255), width=width, joint="curve")
        else:
            draw.line(coords, fill=color, width=width, joint="curve")
        return result

    if kind == "mosaic":
        box = params.get("box") or []
        if len(box) != 4:
            return image
        left, top, width, height = (int(v) for v in box)
        left, top = max(0, left), max(0, top)
        right, bottom = min(image.width, left + width), min(image.height, top + height)
        if right - left < 2 or bottom - top < 2:
            return image
        region = image.crop((left, top, right, bottom))
        mode = str(params.get("mode", "mosaic"))
        if mode == "blur":
            processed = region.filter(ImageFilter.GaussianBlur(max(2, int(params.get("block", 12)) / 2)))
        else:
            block = max(3, int(params.get("block", 12)))
            small = region.resize(
                (max(1, region.width // block), max(1, region.height // block)),
                Image.Resampling.BILINEAR,
            )
            processed = small.resize(region.size, Image.Resampling.NEAREST)
        result = image.copy()
        result.paste(processed, (left, top))
        return result

    if kind == "border":
        width = max(1, int(params.get("width", 12)))
        color = tuple(int(v) for v in (params.get("color") or [255, 255, 255]))[:3]
        radius = max(0, int(params.get("radius", 0)))
        result = image.convert("RGB").copy()
        draw = ImageDraw.Draw(result)
        if radius > 0:
            # 圆角：先把四角涂成边框色（简单近似）
            draw.rounded_rectangle(
                [0, 0, result.width - 1, result.height - 1],
                radius=radius, outline=color, width=width,
            )
        else:
            for offset in range(width):
                draw.rectangle(
                    [offset, offset, result.width - 1 - offset, result.height - 1 - offset],
                    outline=color,
                )
        return result

    if kind == "ai":
        ai_kind = str(params.get("kind", ""))
        if not ai_kind:
            return image
        return enhance_mod.apply_enhancement(ai_kind, image, params.get("params") or {})

    return image


def render_edited(source: str | Path, steps: list[EditStep]) -> Image.Image:
    """读原图 → 应用步骤栈 → 返回结果图（已加载，可脱离 with 使用）。"""
    from .thumbs import open_oriented

    base = open_oriented(source)
    try:
        return apply_steps(base, steps)
    finally:
        try:
            base.close()
        except Exception:  # noqa: BLE001
            pass


# 扩展名 → Pillow 格式名（Pillow 用 JPEG 而不是 JPG，TIFF 而不是 TIF）
_PIL_FORMAT = {
    "jpg": "JPEG",
    "png": "PNG",
    "webp": "WEBP",
    "bmp": "BMP",
    "tif": "TIFF",
    "tiff": "TIFF",
}


def export_image(image: Image.Image, target: str | Path, fmt: str = "jpg", quality: int = 92) -> Path:
    """把结果写出到目标文件（按格式选择编码参数）。

    注意：临时文件必须带正确扩展名 —— Pillow 会按扩展名推断格式，
    用 `.tmp` 结尾会直接抛 "unknown file extension"。
    """
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = (fmt or "jpg").lower()
    if fmt == "jpeg":
        fmt = "jpg"
    suffix = f".{fmt}"

    # 临时文件放在同目录、带正确扩展名，写成功后原子替换
    tmp = path.with_name(f"{path.stem}.__writing__{suffix}")

    save_kwargs: dict = {}
    if fmt == "jpg":
        save_kwargs = {"quality": quality, "optimize": True, "progressive": True}
        if image.mode in ("RGBA", "LA", "P"):
            base = Image.new("RGB", image.size, (255, 255, 255))
            rgba = image.convert("RGBA")
            base.paste(rgba, mask=rgba.split()[-1])
            image = base
        elif image.mode != "RGB":
            image = image.convert("RGB")
    elif fmt == "webp":
        save_kwargs = {"quality": quality, "method": 5}
    elif fmt == "png":
        save_kwargs = {"optimize": True}
    elif fmt in ("tif", "tiff"):
        save_kwargs = {"compression": "tiff_lzw"}
    elif fmt == "bmp":
        save_kwargs = {}
    else:
        raise ValueError(f"不支持的导出格式：{fmt}")

    try:
        image.save(tmp, format=_PIL_FORMAT.get(fmt, fmt.upper()), **save_kwargs)
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return path


__all__ = [
    "ADJUST_LABELS",
    "DEFAULT_ADJUST",
    "FILTERS",
    "FILTER_LABELS",
    "apply_step",
    "apply_steps",
    "export_image",
    "render_edited",
]
