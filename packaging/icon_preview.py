"""生成图标尺寸预览图（临时工具）：把 16~256 各尺寸放大平铺，便于肉眼检查小尺寸可读性。"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_icons import app_icon  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)
SCALE = 3
PADDING = 12

tiles = []
for size in SIZES:
    icon = app_icon(size)
    tiles.append(icon.resize((size * SCALE, size * SCALE), Image.Resampling.NEAREST))

width = sum(tile.width + PADDING for tile in tiles) + PADDING
height = max(tile.height for tile in tiles) + PADDING * 2
sheet = Image.new("RGBA", (width, height), (245, 247, 250, 255))
x = PADDING
for tile in tiles:
    sheet.paste(tile, (x, PADDING), tile)
    x += tile.width + PADDING
out = Path(__file__).resolve().parent.parent / ".icon_preview.png"
sheet.save(out)
print("preview:", out, sheet.size)
