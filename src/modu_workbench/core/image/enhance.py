"""图片增强算法（numpy + Pillow 本地实现，不依赖外部模型或网络）。

能力边界（必须如实告知用户，不要做成"点了没反应"的按钮）：
- **能真正做好的**：一键增强、超分放大、锐化、降噪、去模糊、自动白平衡/去雾、
  人像柔化（近似磨皮）、马赛克/区域模糊、文字/贴纸/涂鸦/边框、色彩风格化。
- **只能做简化近似的**：背景移除（基于边缘色的连通区域近似，非语义分割）、
  AI 消除（周围纹理填充，非生成式修补）、老照片修复（降噪+对比度+色调近似）。
- **做不到的**：真正的语义分割、生成式补全、动漫风格迁移 —— 这些需要专门的
  深度模型（onnx/torch），本项目不引入，因此界面上会明确标注为"近似"。

所有函数都以 PIL.Image 进出，内部用 numpy 运算，RGB 工作空间。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

# 单个操作允许的最大像素数（超过先等比缩小，防止内存爆掉）
MAX_PIXELS = 24_000_000
# 超分目标上限（避免 4x 把 800×600 变成 3200×2400 后又乘 4）
MAX_UPSCALE_PIXELS = 40_000_000


@dataclass(frozen=True)
class EnhanceSpec:
    """一个优化能力的元信息（界面上据此生成按钮与说明）。"""

    key: str
    label: str
    description: str
    # local: 本地算法；approximate: 简化近似；需要网络/模型的在此标注
    fidelity: str = "local"
    accepts: tuple = ()          # 需要用户输入的参数名
    cost: str = "轻"             # 轻 / 中 / 重（用于批量排队时的提示）

    @property
    def fidelity_label(self) -> str:
        return {"local": "本地算法", "approximate": "近似效果"}.get(self.fidelity, self.fidelity)


SPECS: tuple[EnhanceSpec, ...] = (
    EnhanceSpec("auto_enhance", "一键增强", "自动调色 + 提亮 + 增强细节（去雾感）"),
    EnhanceSpec("upscale", "超分辨率", "2x / 4x 放大并补细节（Lanczos + 锐化）", accepts=("scale",), cost="中"),
    EnhanceSpec("sharpen", "锐化", "提升边缘清晰度", accepts=("amount",)),
    EnhanceSpec("denoise", "降噪", "去除高 ISO 噪点（边缘保留平滑）", accepts=("strength",), cost="中"),
    EnhanceSpec("deblur", "去模糊", "修复轻微抖动/失焦（USM 反卷积近似）", accepts=("amount",), cost="中"),
    EnhanceSpec("white_balance", "自动白平衡", "按灰世界假设校正偏色"),
    EnhanceSpec("dehaze", "去雾", "提升对比与通透度"),
    EnhanceSpec("portrait", "人像柔化", "肤色区域平滑（近似磨皮）", accepts=("strength",), fidelity="approximate"),
    EnhanceSpec("background_remove", "背景移除", "按边缘色近似抠图，可替换底色", accepts=("background", "tolerance"), fidelity="approximate", cost="中"),
    EnhanceSpec("erase", "AI 消除", "涂抹区域用周围纹理填充（非生成式）", accepts=("mask",), fidelity="approximate", cost="中"),
    EnhanceSpec("old_photo", "老照片修复", "降噪 + 对比度 + 轻度上色近似", fidelity="approximate", cost="中"),
    EnhanceSpec("stylize", "风格化", "动漫/油画/水彩/黑白 等色调风格", accepts=("style",)),
)

SPEC_BY_KEY = {spec.key: spec for spec in SPECS}


def enhance_labels() -> dict[str, str]:
    return {spec.key: spec.label for spec in SPECS}


# --------------------------------------------------------------------------- 基础工具


def _as_array(image: Image.Image) -> np.ndarray:
    """PIL → float32 RGB 数组（0~255）。"""
    if image.mode != "RGB":
        image = image.convert("RGB")
    return np.asarray(image, dtype=np.float32)


def _as_image(array: np.ndarray) -> Image.Image:
    """float32 RGB 数组 → PIL.Image（截断到 0~255）。"""
    return Image.fromarray(np.clip(array, 0, 255).astype(np.uint8), "RGB")


def _limit_pixels(image: Image.Image, limit: int = MAX_PIXELS) -> tuple[Image.Image, float]:
    """超过像素上限时等比缩小，返回 (图, 缩放比)；缩放比 <1 表示缩小过。"""
    total = image.width * image.height
    if total <= limit:
        return image, 1.0
    ratio = (limit / total) ** 0.5
    new_size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
    return image.resize(new_size, Image.Resampling.LANCZOS), ratio


def _percentile_stretch(array: np.ndarray, low: float = 0.5, high: float = 99.5) -> np.ndarray:
    """按分位数拉伸对比度（比固定直方图均衡更不容易过曝）。"""
    result = np.empty_like(array)
    for channel in range(3):
        plane = array[:, :, channel]
        low_value = float(np.percentile(plane, low))
        high_value = float(np.percentile(plane, high))
        if high_value - low_value < 1e-6:
            result[:, :, channel] = plane
            continue
        result[:, :, channel] = (plane - low_value) * (255.0 / (high_value - low_value))
    return result


def _unsharp(image: Image.Image, amount: float, radius: float = 2.0, threshold: int = 3) -> Image.Image:
    return image.filter(ImageFilter.UnsharpMask(radius=radius, percent=int(amount * 100), threshold=threshold))


# --------------------------------------------------------------------------- 单项能力


def auto_enhance(image: Image.Image, *, strength: float = 1.0) -> Image.Image:
    """一键增强：分位拉伸 → 自动白平衡 → 提亮 → 饱和度 → 锐化。"""
    working, _ = _limit_pixels(image)
    array = _percentile_stretch(_as_array(working))
    enhanced = _as_image(array)
    enhanced = ImageOps.autocontrast(enhanced, cutoff=1)
    enhanced = auto_white_balance(enhanced)
    enhanced = ImageEnhance.Brightness(enhanced).enhance(1.0 + 0.06 * strength)
    enhanced = ImageEnhance.Contrast(enhanced).enhance(1.0 + 0.10 * strength)
    enhanced = ImageEnhance.Color(enhanced).enhance(1.0 + 0.12 * strength)
    return _unsharp(enhanced, amount=0.55 * strength, radius=1.8)


def upscale(image: Image.Image, *, scale: int = 2) -> Image.Image:
    """超分：Lanczos 放大 + 分步锐化补细节（非模型超分，但观感明显提升）。"""
    scale = 2 if int(scale) not in (2, 3, 4) else int(scale)
    source, _ = _limit_pixels(image, MAX_UPSCALE_PIXELS // max(1, scale * scale))
    target = (source.width * scale, source.height * scale)
    if target[0] * target[1] > MAX_UPSCALE_PIXELS:
        return image
    # 先放大到目标，再轻度锐化两次，比一次强锐化更自然
    big = source.resize(target, Image.Resampling.LANCZOS)
    big = _unsharp(big, amount=0.45, radius=1.2, threshold=2)
    big = _unsharp(big, amount=0.25, radius=0.8, threshold=2)
    return big


def sharpen(image: Image.Image, *, amount: float = 1.0) -> Image.Image:
    working, _ = _limit_pixels(image)
    return _unsharp(working, amount=max(0.2, amount), radius=2.0)


def denoise(image: Image.Image, *, strength: float = 1.0) -> Image.Image:
    """降噪：对亮度做边缘保留平滑（近似双边），色度做轻度模糊。

    实现方式：用 3×3 邻域方差区分平坦区/边缘区 —— 平坦区多平滑、边缘区少动，
    比纯高斯模糊保边好，比真双边快得多（纯 numpy 向量化）。
    """
    working, _ = _limit_pixels(image)
    array = _as_array(working)
    if array.shape[0] < 3 or array.shape[1] < 3:
        return working

    strength = max(0.0, min(2.0, float(strength)))
    radius = 1 + int(round(strength))
    # 用均值滤波与平方均值求局部方差
    padded = np.pad(array, ((radius, radius), (radius, radius), (0, 0)), mode="edge")
    window = (radius * 2 + 1) ** 2
    total = np.zeros_like(array)
    total_sq = np.zeros_like(array)
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            shifted = padded[radius + dy:radius + dy + array.shape[0],
                            radius + dx:radius + dx + array.shape[1], :]
            total += shifted
            total_sq += shifted * shifted
    mean = total / window
    variance = np.maximum(total_sq / window - mean * mean, 0.0)
    # 方差大 = 边缘 → 权重低；用 0.5 作为软阈值
    weight = 1.0 / (1.0 + variance / (18.0 * 18.0))
    blend = 0.25 + 0.65 * strength
    result = array * (1 - weight * blend) + mean * (weight * blend)

    # 色度再轻度平滑，避免彩色噪点
    chroma = _as_image(result).filter(ImageFilter.GaussianBlur(radius=0.6 + 0.4 * strength))
    return Image.blend(_as_image(result), chroma, 0.35)


def deblur(image: Image.Image, *, amount: float = 1.0) -> Image.Image:
    """去模糊：USM 反卷积近似（大半径低强度 + 小半径高强度组合）。"""
    working, _ = _limit_pixels(image)
    amount = max(0.2, min(3.0, float(amount)))
    first = _unsharp(working, amount=0.9 * amount, radius=3.0, threshold=2)
    return _unsharp(first, amount=0.5 * amount, radius=1.2, threshold=2)


def auto_white_balance(image: Image.Image) -> Image.Image:
    """灰世界假设自动白平衡。"""
    working, _ = _limit_pixels(image)
    array = _as_array(working)
    means = array.reshape(-1, 3).mean(axis=0)
    gray = float(means.mean()) or 1.0
    gains = gray / np.maximum(means, 1.0)
    # 限制增益幅度，避免偏色图被拉爆
    gains = np.clip(gains, 0.8, 1.25)
    return _as_image(array * gains)


def dehaze(image: Image.Image, *, strength: float = 1.0) -> Image.Image:
    """去雾：提高对比 + 轻微提饱和（暗通道先验的轻量替代）。"""
    working, _ = _limit_pixels(image)
    stretched = _as_image(_percentile_stretch(_as_array(working), 1.0, 99.0))
    stretched = ImageEnhance.Contrast(stretched).enhance(1.0 + 0.14 * strength)
    stretched = ImageEnhance.Color(stretched).enhance(1.0 + 0.10 * strength)
    return _unsharp(stretched, amount=0.35 * strength, radius=1.5)


def portrait_smooth(image: Image.Image, *, strength: float = 1.0) -> Image.Image:
    """人像柔化（近似磨皮）：只平滑"肤色且平坦"的区域，保留五官边缘。

    真磨皮需要人脸关键点；这里用肤色检测 + 边缘保留平滑近似，
    对逆光/浓妆/大面积非肤色场景效果有限（界面标注为近似）。
    """
    working, _ = _limit_pixels(image)
    array = _as_array(working)
    red, green, blue = array[:, :, 0], array[:, :, 1], array[:, :, 2]
    # 常见肤色判据（RGB 空间）
    skin = (
        (red > 95) & (green > 40) & (blue > 20)
        & (red > green) & (red > blue)
        & (np.abs(red - green) > 12)
        & ((red - green) / np.maximum(red, 1) > 0.08)
    )
    if not skin.any():
        return working
    smooth = denoise(working, strength=0.8)
    smooth_array = _as_array(smooth)
    # 皮肤区域按强度混合，非皮肤保持原样
    mask = skin.astype(np.float32)[:, :, None] * max(0.0, min(1.0, float(strength))) * 0.85
    return _as_image(array * (1 - mask) + smooth_array * mask)


def background_remove(image: Image.Image, *, background: str = "transparent",
                      tolerance: float = 0.18) -> Image.Image:
    """背景移除（近似）：以四角颜色为背景色，按颜色距离做容差抠图。

    这是**基于颜色的近似**，不是语义分割：纯色/简单背景效果好，
    复杂背景（人群、风景）会留残边。界面需如实标注。
    """
    working, _ = _limit_pixels(image)
    array = _as_array(working)
    height, width = array.shape[:2]
    if height < 2 or width < 2:
        return working

    corners = np.stack([
        array[0, 0], array[0, width - 1], array[height - 1, 0], array[height - 1, width - 1],
    ])
    background_color = corners.mean(axis=0)
    distance = np.sqrt(((array - background_color) ** 2).sum(axis=2))
    threshold = max(12.0, float(tolerance) * 255.0)
    alpha = np.clip((distance - threshold * 0.6) / (threshold * 0.8), 0.0, 1.0)

    rgba = np.dstack([array, alpha * 255.0]).astype(np.uint8)
    cut = Image.fromarray(rgba, "RGBA")
    # 边缘羽化，避免锯齿
    cut.putalpha(cut.getchannel("A").filter(ImageFilter.GaussianBlur(0.6)))

    choice = (background or "transparent").lower()
    if choice == "transparent":
        return cut
    if choice == "white":
        base = Image.new("RGB", cut.size, (255, 255, 255))
    elif choice == "black":
        base = Image.new("RGB", cut.size, (0, 0, 0))
    else:
        base = Image.new("RGB", cut.size, (246, 248, 252))
    base.paste(cut, mask=cut.getchannel("A"))
    return base


def erase_region(image: Image.Image, mask: Image.Image, *, feather: int = 6) -> Image.Image:
    """AI 消除（近似）：用掩码周围的内容填充被涂抹区域。

    做法是把掩码区域的像素用「上下左右已知像素的加权扩散」反复迭代填满，
    再整体加一点模糊融合。**不是生成式修补**：纹理复杂处会显得糊。
    """
    working, _ = _limit_pixels(image)
    if mask.size != working.size:
        mask = mask.resize(working.size, Image.Resampling.LANCZOS)
    array = _as_array(working)
    hole = np.asarray(mask.convert("L"), dtype=np.float32) > 40
    if not hole.any() or hole.all():
        return working

    filled = array.copy()
    unknown = hole.copy()
    height, width = hole.shape
    for _ in range(220):
        if not unknown.any():
            break
        # 用已知像素的 4 邻域均值填补未知像素（一次传播一圈）
        padded = np.pad(filled, ((1, 1), (1, 1), (0, 0)), mode="edge")
        neighbors = (
            padded[0:height, 1:width + 1] + padded[2:height + 2, 1:width + 1]
            + padded[1:height + 1, 0:width] + padded[1:height + 1, 2:width + 2]
        ) / 4.0
        filled[unknown] = neighbors[unknown]
        # 每轮结束后，把「还有未知邻居」的位置留到下一轮
        unknown_neighbors = np.zeros_like(unknown)
        inner = unknown[1:height - 1, 1:width - 1]
        if inner.any():
            grown = np.zeros_like(unknown)
            grown[1:height - 1, 1:width - 1] = inner
            unknown_neighbors = grown
        unknown = unknown & unknown_neighbors

    result = _as_image(filled)
    # 选区边缘羽化融合
    soft = mask.convert("L").filter(ImageFilter.GaussianBlur(max(1, feather)))
    return Image.composite(result, working, soft)


def old_photo_restore(image: Image.Image, *, strength: float = 1.0) -> Image.Image:
    """老照片修复（近似）：降噪 → 分位拉伸 → 轻微去黄 → 提饱和 → 锐化。"""
    working, _ = _limit_pixels(image)
    step = denoise(working, strength=0.9)
    step = _as_image(_percentile_stretch(_as_array(step), 0.6, 99.4))
    # 老照片常偏黄：轻微压低红、抬高蓝
    array = _as_array(step)
    array[:, :, 0] *= 1.0 - 0.035 * strength
    array[:, :, 2] *= 1.0 + 0.045 * strength
    step = _as_image(array)
    step = ImageEnhance.Color(step).enhance(1.0 + 0.10 * strength)
    step = ImageEnhance.Contrast(step).enhance(1.0 + 0.08 * strength)
    return _unsharp(step, amount=0.45 * strength, radius=1.6)


STYLE_PRESETS = {
    "anime": ("动漫", "高饱和 + 平滑 + 提亮，偏插画感"),
    "oil": ("油画", "柔化 + 高对比 + 暖色调"),
    "watercolor": ("水彩", "低对比 + 高亮 + 淡彩"),
    "mono": ("黑白", "去色 + 高对比"),
    "vivid": ("鲜艳", "提升饱和与对比"),
    "warm": ("暖阳", "偏暖色调"),
    "cool": ("冷调", "偏冷色调"),
}


def stylize(image: Image.Image, *, style: str = "vivid", strength: float = 1.0) -> Image.Image:
    """风格化：色调分级实现的风格近似（非风格迁移模型）。"""
    working, _ = _limit_pixels(image)
    style = (style or "vivid").lower()
    array = _as_array(working)

    if style == "anime":
        base = denoise(working, strength=0.9)
        base = ImageEnhance.Color(base).enhance(1.0 + 0.45 * strength)
        base = ImageEnhance.Contrast(base).enhance(1.0 + 0.18 * strength)
        base = ImageEnhance.Brightness(base).enhance(1.0 + 0.06 * strength)
        return _unsharp(base, amount=0.5 * strength, radius=1.2)
    if style == "oil":
        base = working.filter(ImageFilter.ModeFilter(size=5))
        base = ImageEnhance.Color(base).enhance(1.0 + 0.30 * strength)
        base = ImageEnhance.Contrast(base).enhance(1.0 + 0.22 * strength)
        return _as_image(_as_array(base) * np.array([1.04, 1.0, 0.96]))
    if style == "watercolor":
        base = working.filter(ImageFilter.GaussianBlur(1.1))
        base = ImageEnhance.Brightness(base).enhance(1.0 + 0.08 * strength)
        base = ImageEnhance.Contrast(base).enhance(1.0 - 0.10 * strength)
        return ImageEnhance.Color(base).enhance(1.0 + 0.18 * strength)
    if style == "mono":
        base = ImageOps.grayscale(working).convert("RGB")
        return ImageEnhance.Contrast(base).enhance(1.0 + 0.20 * strength)
    if style == "warm":
        return _as_image(array * np.array([1.06, 1.01, 0.94]))
    if style == "cool":
        return _as_image(array * np.array([0.95, 1.0, 1.07]))
    # vivid / 默认
    base = ImageEnhance.Color(working).enhance(1.0 + 0.28 * strength)
    return ImageEnhance.Contrast(base).enhance(1.0 + 0.14 * strength)


# --------------------------------------------------------------------------- 统一入口


def apply_enhancement(kind: str, image: Image.Image, params: dict | None = None) -> Image.Image:
    """按 key 应用一项优化（界面/批量任务统一入口）。"""
    params = dict(params or {})
    if kind == "auto_enhance":
        return auto_enhance(image, strength=float(params.get("strength", 1.0)))
    if kind == "upscale":
        return upscale(image, scale=int(params.get("scale", 2)))
    if kind == "sharpen":
        return sharpen(image, amount=float(params.get("amount", 1.0)))
    if kind == "denoise":
        return denoise(image, strength=float(params.get("strength", 1.0)))
    if kind == "deblur":
        return deblur(image, amount=float(params.get("amount", 1.0)))
    if kind == "white_balance":
        return auto_white_balance(image)
    if kind == "dehaze":
        return dehaze(image, strength=float(params.get("strength", 1.0)))
    if kind == "portrait":
        return portrait_smooth(image, strength=float(params.get("strength", 1.0)))
    if kind == "background_remove":
        return background_remove(image, background=str(params.get("background", "transparent")),
                                 tolerance=float(params.get("tolerance", 0.18)))
    if kind == "erase":
        mask = params.get("mask")
        if not isinstance(mask, Image.Image):
            raise ValueError("AI 消除需要提供涂抹区域（mask）")
        return erase_region(image, mask, feather=int(params.get("feather", 6)))
    if kind == "old_photo":
        return old_photo_restore(image, strength=float(params.get("strength", 1.0)))
    if kind == "stylize":
        return stylize(image, style=str(params.get("style", "vivid")),
                       strength=float(params.get("strength", 1.0)))
    raise ValueError(f"未知的优化类型：{kind}")


def compute_quality_metrics(image: Image.Image) -> dict:
    """粗略画质指标（用于前后对比给出客观依据）。

    - sharpness：拉普拉斯响应方差（越大越清晰）
    - noise    ：高频残差的标准差（越大噪点越多）
    - contrast ：亮度标准差
    - brightness：平均亮度
    """
    working, _ = _limit_pixels(image, 4_000_000)
    gray = np.asarray(working.convert("L"), dtype=np.float32)
    if gray.size < 16:
        return {"sharpness": 0.0, "noise": 0.0, "contrast": 0.0, "brightness": 0.0}

    # 拉普拉斯算子（4 邻域）
    laplacian = (
        -4 * gray[1:-1, 1:-1]
        + gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:]
    )
    # 高频残差 = 原图 − 3×3 均值
    mean = (
        gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:]
        + gray[1:-1, 1:-1]
    ) / 5.0
    residual = gray[1:-1, 1:-1] - mean
    return {
        "sharpness": round(float(laplacian.var()), 1),
        "noise": round(float(residual.std()), 2),
        "contrast": round(float(gray.std()), 2),
        "brightness": round(float(gray.mean()), 1),
    }


__all__ = [
    "MAX_PIXELS",
    "SPECS",
    "SPEC_BY_KEY",
    "STYLE_PRESETS",
    "EnhanceSpec",
    "apply_enhancement",
    "auto_enhance",
    "auto_white_balance",
    "background_remove",
    "compute_quality_metrics",
    "deblur",
    "dehaze",
    "denoise",
    "enhance_labels",
    "erase_region",
    "old_photo_restore",
    "portrait_smooth",
    "sharpen",
    "stylize",
    "upscale",
]
