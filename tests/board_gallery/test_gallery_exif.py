"""图库 · EXIF 解析（`core/gallery/exif.py`）测试。

## 为什么这个模块必须有测试

它决定三件用户能直接感受到的事：

1. **时间轴顺序** —— 没有 EXIF 拍摄时间时退回文件修改时间；解析错了照片会乱序；
2. **相机筛选** —— 厂商 + 型号拼错，筛选就永远筛不出东西；
3. **GPS** —— 经纬度解析错了，地图定位会跑到海里。

这三个都不报错、只是"结果不对"，用户很难描述，也最难排查。
此前这个模块**零测试覆盖**。

## 它的失败哲学是"降级不崩"

EXIF 是用户文件，格式千奇百怪。读不出来必须返回默认值让导入继续，
而不是抛异常打断整批导入 —— 下面有用例专门守这一点。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from modu_workbench.core.gallery import exif as E


def write_photo(path: Path, exif_tags: dict[int, object] | None = None) -> Path:
    """造一张带指定 EXIF 标签的 JPEG。"""
    image = Image.new("RGB", (120, 90), (120, 100, 90))
    if exif_tags:
        exif = image.getexif()
        for tag, value in exif_tags.items():
            exif[tag] = value
        image.save(path, exif=exif)
    else:
        image.save(path)
    return path


# --------------------------------------------------------------------------- 默认值


def test_photo_without_exif_returns_defaults(tmp_path: Path) -> None:
    """没有 EXIF 时返回安全的默认值（**不能是 None 或缺字段**）。"""
    data = E.read_exif(write_photo(tmp_path / "plain.jpg"))
    assert isinstance(data, E.ExifData)
    assert data.taken_at == 0, "无拍摄时间应为 0（调用方据此退回文件 mtime）"
    assert data.camera == ""
    assert data.gps_lat is None and data.gps_lon is None
    assert data.orientation == 1, "默认方向应为 1（不旋转）"


def test_missing_file_does_not_raise(tmp_path: Path) -> None:
    """文件不存在时返回默认值 —— 导入过程中文件被移走也不该崩。"""
    data = E.read_exif(tmp_path / "nope.jpg")
    assert data.taken_at == 0


def test_corrupt_file_does_not_raise(tmp_path: Path) -> None:
    """损坏文件（不是图片）同样返回默认值。

    这是"降级不崩"的核心：一张坏图不能让整批导入中断。
    """
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not an image at all")
    data = E.read_exif(broken)
    assert data.taken_at == 0
    assert data.camera == ""


# --------------------------------------------------------------------------- 拍摄时间


def test_taken_at_parsed_from_datetime_tag(tmp_path: Path) -> None:
    """`DateTime(306)` 要能解析成 Unix 时间戳。

    时间轴排序直接依赖它 —— 解析失败会让整批照片退回文件时间。
    """
    path = write_photo(tmp_path / "dated.jpg", {306: "2024:05:01 12:34:56"})
    data = E.read_exif(path)
    assert data.taken_at > 0, "有拍摄时间却解析成 0"
    # 2024-05-01 12:34:56 UTC
    assert 1_714_500_000 < data.taken_at < 1_714_600_000


def test_malformed_datetime_does_not_raise(tmp_path: Path) -> None:
    """时间格式乱写（相机固件 bug 常见）不能让解析崩。"""
    path = write_photo(tmp_path / "weird.jpg", {306: "0000:00:00 00:00:00"})
    data = E.read_exif(path)
    assert data.taken_at >= 0


# --------------------------------------------------------------------------- 相机


def test_camera_combines_make_and_model(tmp_path: Path) -> None:
    """厂商 + 型号要拼在一起（筛选列表里显示的是组合值）。"""
    path = write_photo(tmp_path / "cam.jpg", {271: "TestMake", 272: "TestModel"})
    data = E.read_exif(path)
    assert "TestMake" in data.camera
    assert "TestModel" in data.camera


def test_camera_with_only_make(tmp_path: Path) -> None:
    """只有厂商没有型号时也能给出可用值（不能是"None None"）。"""
    path = write_photo(tmp_path / "make.jpg", {271: "OnlyMake"})
    data = E.read_exif(path)
    assert "OnlyMake" in data.camera
    assert "None" not in data.camera


# --------------------------------------------------------------------------- GPS


def test_no_gps_returns_none_pair(tmp_path: Path) -> None:
    """没有 GPS 时两个坐标都必须是 None（而不是 0.0 —— 0,0 是几内亚湾）。"""
    data = E.read_exif(write_photo(tmp_path / "nogps.jpg"))
    assert data.gps_lat is None
    assert data.gps_lon is None


# --------------------------------------------------------------------------- 展示字段


def test_display_is_empty_for_plain_photo(tmp_path: Path) -> None:
    """没有可展示信息时空字典（界面据此决定要不要显示"拍摄信息"区块）。"""
    data = E.read_exif(write_photo(tmp_path / "plain2.jpg"))
    assert data.display == {}


def test_display_contains_readable_values(tmp_path: Path) -> None:
    """有信息时 `display` 要给出**人能读**的键值（界面直接展示）。"""
    path = write_photo(
        tmp_path / "full.jpg",
        {306: "2024:05:01 12:34:56", 271: "TestMake", 272: "TestModel"},
    )
    data = E.read_exif(path)
    assert data.display, "有 EXIF 却没有任何可展示字段"
    for key, value in data.display.items():
        assert isinstance(key, str) and key, "展示字段的键不能为空"
        assert isinstance(value, str), f"展示字段 {key} 的值应为字符串"


def test_raw_keeps_original_tags(tmp_path: Path) -> None:
    """`raw` 保留原始标签（排查"为什么读不出 GPS"时要看）。"""
    path = write_photo(tmp_path / "raw.jpg", {271: "TestMake"})
    data = E.read_exif(path)
    assert isinstance(data.raw, dict)


# --------------------------------------------------------------------------- 幂等


def test_read_is_idempotent(tmp_path: Path) -> None:
    """同一文件读两次结果一致（导出/刷新会重复调用）。"""
    path = write_photo(tmp_path / "same.jpg", {271: "A", 272: "B"})
    first = E.read_exif(path)
    second = E.read_exif(path)
    assert first.taken_at == second.taken_at
    assert first.camera == second.camera
    assert first.display == second.display
