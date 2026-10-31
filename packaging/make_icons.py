"""生成「墨软·工作台」应用图标与安装包图形（可重复执行）。

设计（参考墨软标识）：白色圆角底 + 蓝色双箭头「S」形标记，
上下两支箭头互相咬合并留白色分隔，形成 S / 闪电观感；附「墨软·工作台 / Morwork」字标。

产物（写入 src/modu_workbench/assets/，随包分发并被安装脚本引用）：
- app.ico                     多尺寸应用/安装包图标（16~256）
- app.png                     512×512 PNG（图标源图）
- installer_header.bmp        NSIS 页面右上/左上小图 150×57
- installer_welcome.bmp       NSIS 欢迎页左侧图 164×314

用法：python packaging/make_icons.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS = REPO_ROOT / "src" / "modu_workbench" / "assets"

BLUE_LIGHT = (77, 163, 255)     # #4DA3FF
BLUE = (35, 116, 240)           # #2374F0
BLUE_DEEP = (20, 82, 196)       # #1452C4
INK = (27, 36, 48)              # #1B2430
GRAY = (110, 122, 140)
WHITE = (255, 255, 255, 255)

FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\Dengb.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
)


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in FONT_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size)
            except Exception:  # noqa: BLE001
                continue
    print("警告：未找到中文字体，字标可能显示为方框", file=sys.stderr)
    return ImageFont.load_default()


def arrow_polygon(length: float, thickness: float, head_width: float, head_length: float):
    """尾部在原点、指向 +x 的箭头多边形（中心线在 y=0）。"""
    half_t = thickness / 2
    half_h = head_width / 2
    body_end = length - head_length
    return [
        (0, -half_t), (body_end, -half_t), (body_end, -half_h),
        (length, 0), (body_end, half_h), (body_end, half_t), (0, half_t),
    ]


def transform(points, *, angle_deg: float, dx: float, dy: float):  # noqa: ANN001, ANN201
    import math

    rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    return [
        (dx + x * cos_a - y * sin_a, dy + x * sin_a + y * cos_a)
        for x, y in points
    ]


def draw_mark(canvas: Image.Image, *, origin: tuple[int, int], size: int) -> None:
    """在 canvas 的 origin 处绘制标记：两支反向咬合的箭头（白缝分隔）。"""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    unit = size / 512.0
    length = 340 * unit
    thickness = 80 * unit
    head_width = 118 * unit
    head_length = 104 * unit
    gap = thickness * 1.14               # 两支箭头中心线的垂直间距（留出白色分隔）

    center = size / 2
    # 共用「右上方向」的法线，保证两支箭头分别落在对角线的两侧（上左下右），互不遮挡
    base_cos, base_sin = math_cos_sin(-45.0)
    normal = (-base_sin, base_cos)          # 指向右下
    specs = (
        # (方向角度, 侧向偏移（法线倍数）, 上色, 下色)
        (135.0, +gap, BLUE_DEEP, BLUE_DEEP),     # 指向左下（下层）
        (-45.0, -gap, BLUE_LIGHT, BLUE),         # 指向右上（上层）
    )
    for angle, offset, top_color, bottom_color in specs:
        cos_a, sin_a = math_cos_sin(angle)
        cx = center + normal[0] * offset
        cy = center + normal[1] * offset
        tail = (cx - cos_a * length / 2, cy - sin_a * length / 2)
        points = transform(arrow_polygon(length, thickness, head_width, head_length),
                           angle_deg=angle, dx=tail[0], dy=tail[1])

        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).polygon(points, fill=255)
        outline = mask.filter(_outline_filter(max(3, int(size / 34))))
        layer.paste(Image.new("RGBA", (size, size), WHITE), (0, 0), outline)
        layer.paste(gradient_fill(size, top_color, bottom_color), (0, 0), mask)

    canvas.paste(layer, origin, layer)


def math_cos_sin(angle_deg: float) -> tuple[float, float]:
    import math

    rad = math.radians(angle_deg)
    return math.cos(rad), math.sin(rad)


def gradient_fill(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    """竖直渐变图（供多边形遮罩使用）。"""
    gradient = Image.new("RGBA", (1, size))
    for y in range(size):
        ratio = y / max(1, size - 1)
        gradient.putpixel((0, y), (
            int(top[0] + (bottom[0] - top[0]) * ratio),
            int(top[1] + (bottom[1] - top[1]) * ratio),
            int(top[2] + (bottom[2] - top[2]) * ratio),
            255,
        ))
    return gradient.resize((size, size), Image.Resampling.NEAREST)


def _outline_filter(width: int):  # noqa: ANN201
    from PIL import ImageFilter

    return ImageFilter.MaxFilter(width if width % 2 == 1 else width + 1)


def app_icon(size: int = 512, *, rounded: bool = True) -> Image.Image:
    """应用图标：白色圆角底 + 蓝色标记。"""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    radius = int(size * 0.22) if rounded else 0
    backdrop = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(backdrop)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=WHITE,
                           outline=(226, 232, 242, 255), width=max(1, size // 128))
    canvas.alpha_composite(backdrop)
    mark = int(size * 0.68)
    draw_mark(canvas, origin=((size - mark) // 2, (size - mark) // 2), size=mark)
    return canvas


def installer_header(width: int = 150, height: int = 57) -> Image.Image:
    """NSIS 页面小图：标记 + 「墨软·工作台 / Morwork」。"""
    image = Image.new("RGB", (width, height), WHITE[:3])
    mark = 34
    mark_layer = Image.new("RGBA", (mark, mark), (0, 0, 0, 0))
    draw_mark(mark_layer, origin=(0, 0), size=mark)
    image.paste(mark_layer, (6, (height - mark) // 2), mark_layer)

    draw = ImageDraw.Draw(image)
    title_font = load_font(15)
    sub_font = load_font(10)
    draw.text((mark + 12, 12), "墨软·工作台", font=title_font, fill=INK)
    draw.text((mark + 13, 31), "Morwork", font=sub_font, fill=BLUE)
    return image


def installer_welcome(width: int = 164, height: int = 314) -> Image.Image:
    """NSIS 欢迎页左侧图：大字标 + 标记 + 一句话说明。"""
    image = Image.new("RGB", (width, height), WHITE[:3])
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width - 1, 4], fill=BLUE)
    draw.rectangle([0, height - 5, width - 1, height - 1], fill=BLUE_LIGHT)

    mark = 96
    mark_layer = Image.new("RGBA", (mark, mark), (0, 0, 0, 0))
    draw_mark(mark_layer, origin=(0, 0), size=mark)
    image.paste(mark_layer, ((width - mark) // 2, 48), mark_layer)

    title_font = load_font(20)
    name_font = load_font(12)
    tag_font = load_font(11)

    def centered(text: str, font, y: int, fill) -> None:  # noqa: ANN001
        box = draw.textbbox((0, 0), text, font=font)
        draw.text(((width - (box[2] - box[0])) / 2, y), text, font=font, fill=fill)

    centered("墨软·工作台", title_font, 168, INK)
    centered("Morwork", name_font, 198, BLUE)
    centered("本地离线", tag_font, 224, GRAY)
    centered("阅读 · 转换 · 音乐", tag_font, 240, GRAY)
    return image


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)

    icon = app_icon(512)
    icon.save(ASSETS / "app.png")
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]
    icon.save(ASSETS / "app.ico", sizes=sizes)
    installer_header().save(ASSETS / "installer_header.bmp")
    installer_welcome().save(ASSETS / "installer_welcome.bmp")

    for name in ("app.png", "app.ico", "installer_header.bmp", "installer_welcome.bmp"):
        path = ASSETS / name
        print(f"生成 {path.relative_to(REPO_ROOT)}（{path.stat().st_size} 字节）")


if __name__ == "__main__":
    main()
