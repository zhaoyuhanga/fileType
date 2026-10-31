"""图库 · 评分与相机筛选（把已有能力接到界面上）测试。

## 背景

这两个功能的后端**早就实现好了**，但界面上一直没有入口：

- `ImageStorage.set_rating()` —— 详情面板会显示「评分 ★★★」，
  用户却**没法打分**（星级永远是空的）；
- `ImageStorage.list_cameras()` —— 有 EXIF 机型数据，但筛选栏没有相机选项。

这属于"能力做好了但用户点不到"，比缺功能更浪费。本文件守住：
**入口真的存在、点了真的生效、边界真的被钳住**。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from modu_workbench.core.gallery.models import ImageItem


@pytest.fixture()
def storage(tmp_path: Path):  # noqa: ANN201
    from modu_workbench.core.gallery.storage import ImageStorage

    store = ImageStorage(str(tmp_path / "gallery.db"))
    yield store
    store.close()


def make_item(index: int, *, camera: str = "", rating: int = 0) -> ImageItem:
    return ImageItem(
        path=f"/photos/{index:04d}.jpg",
        sha256=f"{index:064x}",
        thumb_path="",
        title=f"照片{index}",
        ext="jpg",
        mime="image/jpeg",
        size_bytes=1000,
        width=100,
        height=100,
        added_at=1_700_000_000 + index,
        source="local",
        camera=camera,
        rating=rating,
    )


# --------------------------------------------------------------------------- 评分


def test_set_rating_writes_and_reads_back(storage) -> None:  # noqa: ANN001
    """打分后能读回来（这是"评分"功能的最小闭环）。"""
    image_id = storage.upsert_image(make_item(1))
    assert storage.set_rating([image_id], 4) == 1
    assert storage.get_image(image_id).rating == 4


def test_set_rating_batches(storage) -> None:  # noqa: ANN001
    """一次给多张打分（右键菜单支持多选）。"""
    ids = [storage.upsert_image(make_item(i)) for i in range(5)]
    assert storage.set_rating(ids, 3) == 5
    assert len(storage.list_images(min_rating=3)) == 5


def test_set_rating_clamps_out_of_range(storage) -> None:  # noqa: ANN001
    """越界值要被钳到 0~5，不能写进脏数据。

    界面只有 0~5 的按钮，但导入/脚本可能传任意值 —— 数据库里出现 99 星
    会让筛选和显示都变形。
    """
    image_id = storage.upsert_image(make_item(1))
    storage.set_rating([image_id], 99)
    assert storage.get_image(image_id).rating == 5
    storage.set_rating([image_id], -3)
    assert storage.get_image(image_id).rating == 0


def test_set_rating_empty_ids_is_noop(storage) -> None:  # noqa: ANN001
    """空列表不报错（用户没选中任何图时）。"""
    assert storage.set_rating([], 5) == 0


def test_clear_rating(storage) -> None:  # noqa: ANN001
    """0 星等于清除评分 —— 用户要能撤销自己的打分。"""
    image_id = storage.upsert_image(make_item(1))
    storage.set_rating([image_id], 5)
    storage.set_rating([image_id], 0)
    assert storage.get_image(image_id).rating == 0


def test_min_rating_filter(storage) -> None:  # noqa: ANN001
    """`min_rating` 是"不低于"语义：筛 4 星要同时命中 4 星和 5 星。"""
    low = storage.upsert_image(make_item(1, rating=2))
    high = storage.upsert_image(make_item(2, rating=5))
    storage.set_rating([low], 2)
    storage.set_rating([high], 5)

    assert len(storage.list_images(min_rating=0)) == 2
    assert len(storage.list_images(min_rating=4)) == 1
    assert storage.list_images(min_rating=4)[0].id == high


# --------------------------------------------------------------------------- 相机筛选


def test_list_cameras_aggregates_by_model(storage) -> None:  # noqa: ANN001
    """相机列表按机型聚合计数，按数量降序。"""
    for index in range(3):
        storage.upsert_image(make_item(index, camera="Canon R5"))
    storage.upsert_image(make_item(9, camera="Sony A7"))

    cameras = storage.list_cameras()
    assert cameras[0] == ("Canon R5", 3)
    assert ("Sony A7", 1) in cameras


def test_list_cameras_skips_empty(storage) -> None:  # noqa: ANN001
    """没有机型信息的照片不能出现在筛选列表里（否则会出现一个空白的"相机"项）。"""
    storage.upsert_image(make_item(1, camera=""))
    storage.upsert_image(make_item(2, camera="Canon R5"))
    cameras = storage.list_cameras()
    assert all(name for name, _count in cameras), f"出现了空机型：{cameras}"
    assert ("", 1) not in cameras


def test_camera_filter_returns_only_that_model(storage) -> None:  # noqa: ANN001
    """按相机筛选只返回该机型。"""
    canon = storage.upsert_image(make_item(1, camera="Canon R5"))
    storage.upsert_image(make_item(2, camera="Sony A7"))

    result = storage.list_images(camera="Canon R5")
    assert [item.id for item in result] == [canon]


def test_camera_filter_unknown_is_empty(storage) -> None:  # noqa: ANN001
    """不存在的机型返回空（而不是忽略条件返回全部）。"""
    storage.upsert_image(make_item(1, camera="Canon R5"))
    assert storage.list_images(camera="不存在的相机") == []


# --------------------------------------------------------------------------- 组合


def test_rating_and_camera_combine(storage) -> None:  # noqa: ANN001
    """评分与相机筛选可叠加（真实使用是组合条件）。"""
    a = storage.upsert_image(make_item(1, camera="Canon R5"))
    storage.upsert_image(make_item(2, camera="Canon R5"))
    storage.upsert_image(make_item(3, camera="Sony A7"))
    storage.set_rating([a], 5)

    result = storage.list_images(min_rating=5, camera="Canon R5")
    assert [item.id for item in result] == [a]
