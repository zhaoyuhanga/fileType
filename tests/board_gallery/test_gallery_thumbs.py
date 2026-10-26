"""图库缩略图：生成必须快、滚动不能卡。

## 为什么要这道闸门

两件事都是"用起来才知道"的性能问题，没有测试就会悄悄回退：

### 1. 解码阶段降采样（`draft`）

生成缩略图时如果先完整解码 4000x3000 再缩到 320，单张要 **40.9 ms**；
让 JPEG 在解码阶段就降采样只要 **4.3 ms**（9.4 倍）。
这不是降质量：draft 只选不小于目标尺寸的 DCT 档，最终仍用 LANCZOS 缩。

### 2. 滚动时绝不能触发生成

`_load_row()` 原来同步调用 `thumbnail()`，缓存未命中就**当场解码**。
`fill_thumbnails_lazily` 每批 40 张 → 主线程一次卡 2.7 秒
（用户感受：导入一批照片后界面持续卡顿几十秒）。

现在滚动只取**已有**缓存，未命中的交给后台线程。
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from PIL import Image

from modu_workbench.core.gallery import thumbs as T


@pytest.fixture()
def big_photo(tmp_path: Path) -> Path:
    """造一张 4000x3000 的 JPEG（模拟手机照片）。"""
    path = tmp_path / "big.jpg"
    Image.new("RGB", (4000, 3000), (120, 100, 90)).save(path, quality=85)
    return path


# --------------------------------------------------------------------------- 缓存查询


def test_get_cached_never_generates(tmp_path: Path, big_photo: Path) -> None:
    """`get_cached()` 只查不算：没有就返回 None，绝不能当场解码。

    这是"滚动不卡"的前提 —— 一旦它偷偷生成，界面就会被冻住。
    """
    cache = T.ThumbnailCache(tmp_path / "cache")
    start = time.perf_counter()
    assert cache.get_cached(big_photo, 320) is None
    elapsed = time.perf_counter() - start
    assert elapsed < 0.05, f"`get_cached` 花了 {elapsed * 1000:.0f} ms，说明它在生成缩略图"
    # 而且确实没生成任何文件
    assert list((tmp_path / "cache").rglob("*.webp")) == []


def test_get_cached_returns_existing(tmp_path: Path, big_photo: Path) -> None:
    """已生成过的缩略图能被 `get_cached` 取到。"""
    cache = T.ThumbnailCache(tmp_path / "cache")
    produced = T.make_thumbnail(big_photo, tmp_path / "cache", 320)
    assert produced is not None
    assert cache.get_cached(big_photo, 320) == str(produced)


def test_needs_generation_flags_missing(tmp_path: Path, big_photo: Path) -> None:
    """`needs_generation` 要如实反映"还没生成"。"""
    cache = T.ThumbnailCache(tmp_path / "cache")
    assert cache.needs_generation(big_photo, 320) is True
    T.make_thumbnail(big_photo, tmp_path / "cache", 320)
    cache2 = T.ThumbnailCache(tmp_path / "cache")
    assert cache2.needs_generation(big_photo, 320) is False


# --------------------------------------------------------------------------- 降采样


def test_draft_makes_thumbnail_fast(big_photo: Path, tmp_path: Path) -> None:
    """缩略图生成必须用 draft 降采样（否则大图会慢 5 倍以上）。

    这里不比绝对时间（机器差异大），而是比"带 draft"与"完整解码"的相对差距。
    """
    size = 320

    # 完整解码再缩（旧做法）
    start = time.perf_counter()
    for _ in range(3):
        with Image.open(big_photo) as image:
            image.load()
            image = image.convert("RGB")
            image.thumbnail((size, size), Image.Resampling.LANCZOS)
    slow = (time.perf_counter() - start) / 3

    # 我们的实现（应已带 draft）
    start = time.perf_counter()
    for index in range(3):
        T.make_thumbnail(big_photo, tmp_path / f"c{index}", size)
    fast = (time.perf_counter() - start) / 3

    # make_thumbnail 还包含 WEBP 编码，所以只要求它不比"纯解码"慢太多；
    # 关键是若有人移除了 draft，slow/fast 会掉到 1 附近。
    assert fast < slow, (
        f"缩略图生成（{fast * 1000:.0f} ms）不比完整解码（{slow * 1000:.0f} ms）快，"
        "`open_oriented` 的 draft_size 可能被去掉了"
    )


def test_draft_preserves_output_size(big_photo: Path, tmp_path: Path) -> None:
    """降采样不能改变输出尺寸（否则界面格子会空一块）。"""
    produced = T.make_thumbnail(big_photo, tmp_path / "cache", 320)
    assert produced is not None
    with Image.open(produced) as check:
        assert max(check.size) == 320, f"缩略图应为 320 边长，实际 {check.size}"


def test_draft_keeps_image_usable(tmp_path: Path) -> None:
    """降采样后图像仍有内容（不是全黑/全透明的坏图）。"""
    source = tmp_path / "solid.jpg"
    Image.new("RGB", (4000, 3000), (200, 60, 60)).save(source, quality=85)
    produced = T.make_thumbnail(source, tmp_path / "cache", 320)
    assert produced is not None
    with Image.open(produced) as check:
        pixel = check.convert("RGB").getpixel((10, 10))
    assert pixel[0] > 120, f"缩略图内容异常（像素 {pixel}）"


def test_edit_path_does_not_use_draft(big_photo: Path) -> None:
    """**编辑/预览**路径必须拿原始分辨率（不能被 draft 降到 500 宽）。

    否则用户裁剪时会基于一张模糊的小图操作。
    """
    image = T.open_oriented(big_photo)          # 不传 draft_size
    try:
        assert image.size == (4000, 3000), f"编辑路径拿到了降采样图：{image.size}"
    finally:
        image.close()
