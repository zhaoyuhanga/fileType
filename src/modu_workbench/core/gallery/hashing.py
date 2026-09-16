"""图片哈希：内容哈希（精确去重）与感知哈希（视觉相近/重复识别）。

- `sha256`：文件字节级完全相同 → 直接判重、跳过重复导入；
- `dhash`  ：64 位差值哈希，对缩放/轻微压缩/微调不敏感 → 找「相似图片」。
  汉明距离 ≤ 6 基本可判定为同一张图的不同版本。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

# dhash 采样尺寸：9×8 得到 8×8=64 个差值位
_HASH_SIZE = 8


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    """分块计算文件 sha256（大图不占内存）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def dhash_image(image: Image.Image, size: int = _HASH_SIZE) -> str:
    """计算差值哈希，返回 16 位十六进制字符串（64 位）。

    做法：灰度 → 缩到 (size+1)×size → 逐行比较相邻像素亮度，
    得到 size×size 个比特；对整体亮度变化鲁棒，适合找重复/相似图。

    注意：用 numpy 取像素而非 `Image.getdata()` —— 后者在新版 Pillow 已弃用，
    在部分版本上会返回空序列，导致所有图片哈希都变成 0（去重与相似识别失效）。
    """
    try:
        import numpy as np

        small = image.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
        pixels = np.asarray(small, dtype=np.int16)
    except Exception:  # noqa: BLE001  损坏图不阻塞导入
        return ""
    if pixels.shape != (size, size + 1):
        return ""
    # 水平相邻像素比较：左 > 右 记 1
    bits = (pixels[:, :-1] > pixels[:, 1:]).flatten()
    value = 0
    for index, flag in enumerate(bits):
        if flag:
            value |= 1 << index
    return f"{value:016x}"


def dhash_file(path: str | Path, size: int = _HASH_SIZE) -> str:
    try:
        with Image.open(path) as image:
            image.load()
            return dhash_image(image, size)
    except Exception:  # noqa: BLE001
        return ""


def hamming_distance(left: str, right: str) -> int | None:
    """两个十六进制哈希的汉明距离；长度不一致或非法时返回 None。"""
    if not left or not right or len(left) != len(right):
        return None
    try:
        return bin(int(left, 16) ^ int(right, 16)).count("1")
    except ValueError:
        return None


__all__ = ["dhash_file", "dhash_image", "hamming_distance", "sha256_file"]
