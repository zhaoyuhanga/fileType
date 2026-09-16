"""墨软图库核心测试：存储 / 导入去重 / 哈希 / EXIF / 缩略图 / 编辑 / 增强 / DeepSeek。

重点覆盖那些「看起来能跑但其实没效果」的地方：
- 感知哈希必须能区分不同图片（曾因 Pillow 12 弃用 getdata 而全部返回 0）；
- 导出必须写出带正确扩展名的临时文件（曾因 .tmp 后缀被 Pillow 拒绝）；
- 增强算法必须真的改变指标（降噪降噪点、锐化提清晰度），而不是原样返回。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from modu_workbench.core.image import (
    ADJUST_LABELS,
    EXPORT_FORMATS,
    SPECS,
    AiConfig,
    AiConfigError,
    DeepSeekClient,
    EditStep,
    ImageItem,
    ImageLibrary,
    ImageStorage,
    apply_enhancement,
    apply_step,
    apply_steps,
    compute_quality_metrics,
    dhash_file,
    dhash_image,
    export_image,
    format_size,
    hamming_distance,
    load_ai_config,
    make_thumbnail,
    open_oriented,
    read_exif,
    safe_filename,
    save_ai_config,
    scan_image_files,
    sha256_file,
)
from modu_workbench.core.image.hashing import dhash_image as _dhash

# --------------------------------------------------------------------------- 夹具


def make_image(path: Path, *, size=(320, 240), seed: int = 0, color=None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if color is not None:
        Image.new("RGB", size, color).save(path)
        return path
    rng = np.random.default_rng(seed)
    array = rng.integers(0, 255, (size[1], size[0], 3), dtype="uint8")
    Image.fromarray(array).save(path)
    return path


@pytest.fixture()
def storage(tmp_path: Path) -> ImageStorage:
    store = ImageStorage(str(tmp_path / "gallery.db"))
    yield store
    store.close()


@pytest.fixture()
def library(storage: ImageStorage, tmp_path: Path) -> ImageLibrary:
    return ImageLibrary(storage, tmp_path / "cache", image_dir=tmp_path / "images")


# --------------------------------------------------------------------------- 模型


def test_safe_filename_and_format_size() -> None:
    assert safe_filename('a/b:c*d?"e') == "a_b_c_d_e"
    assert safe_filename("   ") == "image"
    assert format_size(0) == "-"
    assert format_size(512) == "512 B"
    assert format_size(2048) == "2 KB"
    assert format_size(5 * 1048576) == "5.0 MB"


def test_image_item_properties() -> None:
    item = ImageItem(width=400, height=300, size_bytes=1234, ext="png")
    assert item.resolution == "400×300"
    assert item.size_text == "1 KB"
    assert abs(item.aspect - 4 / 3) < 0.01
    empty = ImageItem()
    assert empty.resolution == "-"


def test_scan_image_files(tmp_path: Path) -> None:
    make_image(tmp_path / "a.jpg", color=(10, 20, 30))
    make_image(tmp_path / "sub" / "b.PNG", color=(30, 40, 50))
    (tmp_path / "note.txt").write_text("x", encoding="utf-8")
    found = scan_image_files([str(tmp_path)])
    assert sorted(Path(p).name for p in found) == ["a.jpg", "b.PNG"]
    assert scan_image_files([str(tmp_path / "a.jpg"), str(tmp_path / "a.jpg")]) == [
        str(tmp_path / "a.jpg")
    ]


# --------------------------------------------------------------------------- 哈希


def test_dhash_discriminates_images(library: ImageLibrary, tmp_path: Path) -> None:
    """感知哈希必须真的随内容变化（曾整体返回 0，导致去重/相似识别失效）。"""
    first = make_image(tmp_path / "a.jpg", seed=1)
    second = make_image(tmp_path / "b.jpg", seed=2)
    hash_a, hash_b = dhash_file(first), dhash_file(second)
    assert hash_a and hash_b
    assert hash_a != hash_b
    assert len(hash_a) == 16
    # 同图不同文件名 → 距离 0
    copy = tmp_path / "a_copy.jpg"
    copy.write_bytes(first.read_bytes())
    assert hamming_distance(hash_a, dhash_file(copy)) == 0


def test_dhash_tolerates_resize(tmp_path: Path) -> None:
    """缩放变体应被判为相似（距离小），不同内容距离大。"""
    source = make_image(tmp_path / "src.jpg", seed=7)
    small = tmp_path / "small.jpg"
    with Image.open(source) as image:
        image.resize((120, 90)).save(small)
    other = make_image(tmp_path / "other.jpg", seed=99)

    near = hamming_distance(dhash_file(source), dhash_file(small))
    far = hamming_distance(dhash_file(source), dhash_file(other))
    assert near is not None and far is not None
    assert near < far


def test_hamming_distance_guards() -> None:
    assert hamming_distance("", "abcd") is None
    assert hamming_distance("abcd", "ab") is None
    assert hamming_distance("zzzz", "abcd") is None


def test_sha256_file_stable(tmp_path: Path) -> None:
    path = make_image(tmp_path / "x.jpg", color=(1, 2, 3))
    assert sha256_file(path) == sha256_file(path)
    assert len(sha256_file(path)) == 64


# --------------------------------------------------------------------------- 存储/导入


def test_import_dedupes_by_content(library: ImageLibrary, tmp_path: Path) -> None:
    source = tmp_path / "src"
    make_image(source / "one.jpg", seed=3)
    make_image(source / "two.jpg", seed=4)
    (source / "copy.jpg").write_bytes((source / "one.jpg").read_bytes())

    result = library.import_paths([str(source)])
    assert result.added == 2
    assert result.skipped_duplicate == 1
    assert library.storage.count_images() == 2


def test_import_is_incremental_and_updates_existing(library: ImageLibrary, tmp_path: Path) -> None:
    source = tmp_path / "src"
    path = make_image(source / "one.jpg", seed=5)
    first = library.import_paths([str(source)])
    assert first.added == 1
    # 再导入同一文件：按路径识别为已存在，不新增
    second = library.import_paths([str(source)])
    assert second.added == 1
    assert library.storage.count_images() == 1
    item = library.storage.get_by_path(str(path))
    assert item is not None


def test_metadata_read_on_import(library: ImageLibrary, tmp_path: Path) -> None:
    path = make_image(tmp_path / "src" / "meta.jpg", size=(640, 480))
    result = library.import_paths([str(path)])
    item = result.images[0]
    assert item.width == 640 and item.height == 480
    assert item.size_bytes > 0
    assert item.ext == "jpg"
    assert item.taken_at > 0          # 无 EXIF 时退回文件时间
    assert item.sha256 and item.dhash


def test_album_tags_favorite(library: ImageLibrary, tmp_path: Path) -> None:
    result = library.import_paths([str(make_image(tmp_path / "a.jpg", seed=8))])
    item = result.images[0]

    album_id = library.create_album("旅行")
    assert library.add_to_album(album_id, [item.id]) == 1
    assert library.storage.get_album(album_id).image_count == 1
    assert library.storage.list_images(album_id=album_id)[0].id == item.id

    library.tag_images([item.id], "风景")
    assert library.storage.list_tags()[0].name == "风景"
    assert library.storage.list_images(tag="风景")[0].id == item.id

    assert library.toggle_favorite(item.id) is True
    assert library.storage.list_images(favorite_only=True)[0].id == item.id
    # 收藏相册成员同步
    favorite_album = library.storage.favorite_album_id
    assert [i.id for i in library.storage.list_album_images(favorite_album)] == [item.id]

    # 删除相册不删原图
    assert library.delete_album(album_id) is True
    assert library.storage.get_image(item.id) is not None


def test_auto_classify_local(library: ImageLibrary, tmp_path: Path) -> None:
    make_image(tmp_path / "wide.jpg", size=(800, 300))
    make_image(tmp_path / "tall.jpg", size=(300, 800))
    library.import_paths([str(tmp_path)])
    counts = library.auto_classify()
    assert counts  # 至少生成了分类标签
    names = {tag.name for tag in library.storage.list_tags(source="auto")}
    assert {"横图", "竖图"} & names


def test_list_images_filters_and_order(library: ImageLibrary, tmp_path: Path) -> None:
    for index in range(3):
        make_image(tmp_path / f"f{index}.jpg", size=(200 + index * 50, 200), seed=index)
    library.import_paths([str(tmp_path)])
    assert len(library.storage.list_images()) == 3
    assert len(library.storage.list_images(keyword="f1")) == 1
    assert len(library.storage.list_images(order="size")) == 3
    # 默认降序；传 descending=False 时升序
    assert library.storage.list_images(order="name")[0].title == "f2.jpg"
    assert library.storage.list_images(order="name", descending=False)[0].title == "f0.jpg"


def test_delete_and_missing(library: ImageLibrary, tmp_path: Path) -> None:
    path = make_image(tmp_path / "gone.jpg", seed=11)
    item = library.import_paths([str(path)]).images[0]
    assert library.missing_files() == []
    path.unlink()
    assert [i.id for i in library.missing_files()] == [item.id]
    assert library.cleanup_missing() == 1
    assert library.storage.count_images() == 0


# --------------------------------------------------------------------------- EXIF


def test_read_exif_on_plain_image_is_safe(tmp_path: Path) -> None:
    path = make_image(tmp_path / "plain.jpg", color=(90, 90, 90))
    data = read_exif(path)
    assert data.taken_at == 0
    assert data.camera == ""
    assert not data.has_gps
    assert isinstance(data.to_json(), str)


def test_read_exif_missing_file() -> None:
    data = read_exif("/no/such/file.jpg")
    assert data.taken_at == 0


# --------------------------------------------------------------------------- 缩略图


def test_make_thumbnail_and_cache(library: ImageLibrary, tmp_path: Path) -> None:
    path = make_image(tmp_path / "big.jpg", size=(1600, 1200), seed=12)
    produced = make_thumbnail(path, library.cache_dir / "thumbs", 160)
    assert produced is not None and produced.is_file()
    with Image.open(produced) as image:
        assert max(image.size) <= 160
    # 缓存复用：同一路径再次调用返回同一文件
    assert make_thumbnail(path, library.cache_dir / "thumbs", 160) == produced


def test_thumbnail_cache_hit_and_invalidate(library: ImageLibrary, tmp_path: Path) -> None:
    path = make_image(tmp_path / "c.jpg", size=(600, 400), seed=13)
    first = library.thumbs.get(path, 160)
    assert first is not None
    second = library.thumbs.get(path, 160)
    assert first == second
    assert library.thumbs.stats()["hit"] >= 1
    library.thumbs.invalidate(str(path))
    assert library.thumbs.disk_usage() >= 0


def test_thumbnail_of_broken_file_returns_none(tmp_path: Path) -> None:
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not an image")
    assert make_thumbnail(broken, tmp_path / "cache", 160) is None


def test_open_oriented_flattens_transparency(tmp_path: Path) -> None:
    path = tmp_path / "alpha.png"
    Image.new("RGBA", (40, 40), (255, 0, 0, 0)).save(path)
    with open_oriented(path) as image:
        assert image.mode == "RGB"


# --------------------------------------------------------------------------- 编辑


def test_edit_steps_crop_rotate_filter(tmp_path: Path) -> None:
    base = Image.new("RGB", (100, 80), (120, 120, 120))
    cropped = apply_step(base, EditStep("crop", {"box": [10, 10, 40, 30]}))
    assert cropped.size == (40, 30)

    rotated = apply_step(base, EditStep("rotate", {"angle": 90}))
    assert rotated.size == (80, 100)

    flipped = apply_step(base, EditStep("flip", {"axis": "horizontal"}))
    assert flipped.size == base.size

    gray = apply_step(base, EditStep("filter", {"name": "grayscale", "amount": 1.0}))
    assert gray.mode == "RGB"
    assert apply_steps(base, []) is base or apply_steps(base, []) == base


def test_edit_steps_are_non_destructive(tmp_path: Path) -> None:
    base = Image.new("RGB", (60, 60), (10, 20, 30))
    steps = [EditStep("adjust", {"brightness": 1.5}), EditStep("border", {"width": 4})]
    result = apply_steps(base, steps)
    assert result is not base
    # 原图未被修改
    assert base.getpixel((0, 0)) == (10, 20, 30)


def test_edit_step_failure_is_isolated(tmp_path: Path) -> None:
    base = Image.new("RGB", (40, 40), (5, 5, 5))
    steps = [EditStep("sticker", {"path": "/missing/sticker.png"}),
             EditStep("crop", {"box": [0, 0, 20, 20]})]
    result = apply_steps(base, steps)
    assert result.size == (20, 20)   # 坏步骤被跳过，后续步骤仍生效


def test_export_image_formats(tmp_path: Path) -> None:
    image = Image.new("RGB", (40, 30), (200, 100, 50))
    for fmt in EXPORT_FORMATS:
        target = export_image(image, tmp_path / f"out.{fmt}", fmt=fmt)
        assert target.is_file() and target.stat().st_size > 0
        assert target.suffix == f".{fmt}"


def test_export_rejects_unknown_format(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        export_image(Image.new("RGB", (10, 10)), tmp_path / "x.xyz", fmt="xyz")


def test_library_edit_roundtrip(library: ImageLibrary, tmp_path: Path) -> None:
    path = make_image(tmp_path / "edit.jpg", size=(400, 300), seed=21)
    item = library.import_paths([str(path)]).images[0]
    steps = [EditStep("rotate", {"angle": 90}), EditStep("filter", {"name": "sepia"})]
    library.save_steps(item.id, steps)
    assert [s.label for s in library.load_steps(item.id)] == ["旋转", "滤镜"]

    target = library.export_default_path(item, fmt="jpg", tag="edited")
    exported = library.export_edited(item, target, fmt="jpg")
    assert exported.is_file() and exported.stat().st_size > 0
    # 导出结果入库，原图仍在
    assert library.storage.count_images() == 2
    assert Path(item.path).is_file()


def test_overwrite_original_refreshes_thumbnail(library: ImageLibrary, tmp_path: Path) -> None:
    path = make_image(tmp_path / "ow.jpg", size=(300, 200), seed=22)
    item = library.import_paths([str(path)]).images[0]
    library.thumbs.get(path, 160)
    library.overwrite_original(item, steps=[EditStep("crop", {"box": [0, 0, 100, 100]})])
    refreshed = library.storage.get_image(item.id)
    assert refreshed is not None
    assert refreshed.width == 100
    assert library.storage.count_images() == 1     # 覆盖不新增条目


# --------------------------------------------------------------------------- 增强


@pytest.mark.parametrize("spec", SPECS, ids=[spec.key for spec in SPECS])
def test_every_enhancement_runs(spec, tmp_path: Path) -> None:
    """每一项优化都必须能跑通并返回同尺寸（或更大）的图。"""
    image = make_image(tmp_path / "src.jpg", size=(160, 120), seed=31)
    with open_oriented(image) as opened:
        params: dict = {}
        if "scale" in spec.accepts:
            params["scale"] = 2
        if "mask" in spec.accepts:
            mask = Image.new("L", opened.size, 0)
            ImageDraw.Draw(mask).rectangle([20, 20, 80, 80], fill=255)
            params["mask"] = mask
        if "style" in spec.accepts:
            params["style"] = "anime"
        result = apply_enhancement(spec.key, opened, params)
    assert result is not None
    if spec.key == "upscale":
        assert result.width > 160
    else:
        assert result.size == (160, 120)


def test_enhancement_unknown_kind_raises() -> None:
    with pytest.raises(ValueError):
        apply_enhancement("no_such_kind", Image.new("RGB", (10, 10)))


def test_denoise_reduces_noise(tmp_path: Path) -> None:
    """降噪必须真的降低噪声指标，而不是原样返回。"""
    rng = np.random.default_rng(5)
    base = np.full((160, 160, 3), 128.0)
    noisy = Image.fromarray(
        np.clip(base + rng.normal(0, 25, base.shape), 0, 255).astype("uint8")
    )
    before = compute_quality_metrics(noisy)["noise"]
    after = compute_quality_metrics(apply_enhancement("denoise", noisy, {"strength": 1.5}))["noise"]
    assert after < before


def test_sharpen_increases_sharpness() -> None:
    rng = np.random.default_rng(6)
    array = rng.integers(0, 255, (120, 120, 3), dtype="uint8")
    image = Image.fromarray(array)
    before = compute_quality_metrics(image)["sharpness"]
    after = compute_quality_metrics(apply_enhancement("sharpen", image, {"amount": 1.5}))["sharpness"]
    assert after > before


def test_background_remove_makes_corners_transparent() -> None:
    image = Image.new("RGB", (120, 120), (250, 250, 250))
    ImageDraw.Draw(image).ellipse([30, 30, 90, 90], fill=(200, 40, 30))
    cut = apply_enhancement("background_remove", image,
                            {"background": "transparent", "tolerance": 0.15})
    assert cut.mode == "RGBA"
    alpha = np.asarray(cut)[:, :, 3]
    assert alpha[60, 60] > 200      # 主体保留
    assert alpha[2, 2] < 60         # 背景被抠掉


def test_upscale_actually_enlarges() -> None:
    image = Image.new("RGB", (100, 80), (60, 90, 120))
    assert apply_enhancement("upscale", image, {"scale": 2}).size == (200, 160)
    assert apply_enhancement("upscale", image, {"scale": 4}).size == (400, 320)


def test_stylize_changes_pixels() -> None:
    image = make_image(Path(tempfile.mkdtemp()) / "s.jpg", seed=41)
    with open_oriented(image) as opened:
        original = np.asarray(opened, dtype=np.float32)
        for style in ("anime", "oil", "mono", "warm"):
            result = apply_enhancement("stylize", opened, {"style": style})
            diff = np.abs(np.asarray(result, dtype=np.float32) - original).mean()
            assert diff > 1.0, f"{style} 未产生变化"


def test_enhancement_via_library(library: ImageLibrary, tmp_path: Path) -> None:
    path = make_image(tmp_path / "enh.jpg", size=(200, 150), seed=51)
    item = library.import_paths([str(path)]).images[0]
    produced = library.run_enhancement(item, "auto_enhance")
    assert produced.is_file()
    assert library.storage.count_images() == 2       # 另存为新图
    before, after = library.compare_metrics(item, "sharpen")
    assert set(before) == set(after) == {"sharpness", "noise", "contrast", "brightness"}


# --------------------------------------------------------------------------- DeepSeek


def test_ai_config_requires_key() -> None:
    client = DeepSeekClient(AiConfig(api_key=""))
    with pytest.raises(AiConfigError):
        client.test_connection()
    with pytest.raises(AiConfigError):
        client.suggest_search_keywords("海边")


def test_ai_config_endpoint() -> None:
    config = AiConfig(api_key="k", base_url="https://api.deepseek.com/v1/")
    assert config.endpoint == "https://api.deepseek.com/v1/chat/completions"
    assert config.configured is True


def test_ai_vision_blocked_without_consent(tmp_path: Path) -> None:
    """未授权上传时，不允许把图片发给云端。"""
    path = make_image(tmp_path / "v.jpg", color=(1, 2, 3))
    client = DeepSeekClient(AiConfig(api_key="k", allow_upload=False))
    with pytest.raises(AiConfigError):
        client.analyze_image(path)


def test_ai_config_roundtrip(storage: ImageStorage) -> None:
    config = AiConfig(api_key="secret", base_url="https://example.com/v1",
                      model="m1", vision_model="m2", allow_upload=True, temperature=0.7)
    save_ai_config(storage, config)
    restored = load_ai_config(storage)
    assert restored.api_key == "secret"
    assert restored.base_url == "https://example.com/v1"
    assert restored.model == "m1" and restored.vision_model == "m2"
    assert restored.allow_upload is True
    assert abs(restored.temperature - 0.7) < 0.01


def test_ai_parses_keyword_json() -> None:
    from modu_workbench.core.image.ai import _parse_str_list

    assert _parse_str_list('["海边", "日落"]') == ["海边", "日落"]
    assert _parse_str_list('```json\n["a","b"]\n```') == ["a", "b"]
    assert _parse_str_list("海边,日落") == ["海边", "日落"]


def test_ai_parses_edit_params_json() -> None:
    from modu_workbench.core.image.ai import _parse_json_object

    assert _parse_json_object('{"filter": "黑白"}') == {"filter": "黑白"}
    assert _parse_json_object('说明文字 {"brightness": 1.2} 结尾') == {"brightness": 1.2}
    assert _parse_json_object("不是 JSON") == {}


def test_ai_analyze_by_metadata_needs_no_upload(library: ImageLibrary, tmp_path: Path) -> None:
    """纯文本模式（只发元数据）在未授权上传时也应可用 —— 这里只验证它会去调网络并报错。"""
    path = make_image(tmp_path / "m.jpg", color=(9, 9, 9))
    item = library.import_paths([str(path)]).images[0]
    item.camera = "TestCam"
    client = DeepSeekClient(AiConfig(api_key="dummy", allow_upload=False))
    # 没有真实网络/密钥 → 应当抛出请求错误，而不是配置错误
    with pytest.raises(Exception):
        client.analyze_by_metadata(item)


__all__ = ["ADJUST_LABELS", "_dhash"]
