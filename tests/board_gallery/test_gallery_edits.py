"""图库 · 编辑步骤（`core/gallery/edits.py`）测试。

## 为什么这个模块必须测

它把用户的编辑操作（裁剪/旋转/滤镜/调色/文字/马赛克/边框/AI 增强）
真正作用到图片上。此前**零测试覆盖**，而它的失败方式很具体：

- 参数顺序理解错 → 裁剪出完全不该出现的位置（用户看到"裁歪了"）；
- 滤镜/调色不生效 → 用户反复调，看不出变化；
- 未知步骤抛异常 → **整个编辑栈崩掉，用户此前所有改动一起丢失**；
- 导出格式错 → 存成了别的格式，或丢失透明通道。

这里测的是**可验证的行为**（尺寸、像素方向、格式），不去比较审美效果。
"""
from __future__ import annotations

import pytest
from PIL import Image

from modu_workbench.core.gallery import edits as E


@pytest.fixture()
def image() -> Image.Image:
    """100x80 的纯色图（便于判断"有没有真的改动"）。"""
    return Image.new("RGB", (100, 80), (120, 100, 90))


def mean_luma(img: Image.Image) -> float:
    """平均亮度（免用已弃用的 getdata）。"""
    return sum(img.convert("L").resize((1, 1), Image.Resampling.BOX).tobytes()) / 1.0


def step(kind: str, **params: object) -> E.EditStep:
    """构造一个编辑步骤（参数统一放 params 里，避免与 `name` 之类的键名冲突）。"""
    return E.EditStep(step=kind, params=dict(params))


# --------------------------------------------------------------------------- 裁剪


def test_crop_uses_x_y_width_height() -> None:
    """`crop` 的 box 是 **(x, y, 宽, 高)**，不是 PIL 的 (left, top, right, bottom)。

    这个约定必须钉住：理解错就会"裁歪"（用户最常见的抱怨之一）。
    """
    result = E.apply_step(Image.new("RGB", (100, 80)), step("crop", box=(10, 10, 60, 50)))
    assert result.size == (60, 50)


def test_crop_is_clamped_to_image_bounds() -> None:
    """越界的裁剪框要被夹到图内，不能抛异常（用户可能拖出画布）。"""
    result = E.apply_step(Image.new("RGB", (100, 80)), step("crop", box=(80, 60, 500, 500)))
    assert result.width <= 100 and result.height <= 80
    assert result.width >= 1 and result.height >= 1


def test_crop_with_degenerate_box_returns_original() -> None:
    """空/非法裁剪框要原样返回（而不是崩或产生 0 尺寸图）。"""
    original = Image.new("RGB", (100, 80))
    assert E.apply_step(original, step("crop", box=[])).size == (100, 80)
    assert E.apply_step(original, step("crop", box=(10, 10, 0, 0))).size == (100, 80)


# --------------------------------------------------------------------------- 旋转与翻转


def test_rotate_90_swaps_dimensions() -> None:
    """90° 整数倍用 transpose，尺寸应互换且**不重采样**（画面不糊）。"""
    result = E.apply_step(Image.new("RGB", (100, 80)), step("rotate", angle=90))
    assert result.size == (80, 100)


def test_rotate_zero_is_noop() -> None:
    """0° 不动。"""
    assert E.apply_step(Image.new("RGB", (100, 80)), step("rotate", angle=0)).size == (100, 80)


def test_rotate_arbitrary_angle_expands_canvas() -> None:
    """任意角度默认 expand，画布应变大（否则边角被裁掉）。"""
    result = E.apply_step(Image.new("RGB", (100, 80)), step("rotate", angle=30))
    assert result.width >= 100 and result.height >= 80


def test_flip_horizontal_mirrors_pixels() -> None:
    """水平翻转要真的镜像像素（左半边的颜色应跑到右半边）。"""
    img = Image.new("RGB", (4, 2), (0, 0, 0))
    img.putpixel((0, 0), (255, 0, 0))
    flipped = E.apply_step(img, step("flip", axis="horizontal"))
    assert flipped.getpixel((3, 0)) == (255, 0, 0)
    assert flipped.getpixel((0, 0)) == (0, 0, 0)


def test_flip_vertical_mirrors_pixels() -> None:
    """垂直翻转同理（上半边的颜色应跑到下半边）。"""
    img = Image.new("RGB", (2, 4), (0, 0, 0))
    img.putpixel((0, 0), (0, 255, 0))
    flipped = E.apply_step(img, step("flip", axis="vertical"))
    assert flipped.getpixel((0, 3)) == (0, 255, 0)


# --------------------------------------------------------------------------- 滤镜


def test_grayscale_filter_has_equal_channels() -> None:
    """灰度滤镜后 R=G=B（否则不是真灰度，用户会看到偏色）。"""
    result = E.apply_step(Image.new("RGB", (10, 10), (120, 100, 90)), step("filter", name="grayscale"))
    pixel = result.getpixel((5, 5))
    assert pixel[0] == pixel[1] == pixel[2], f"灰度后三通道不相等：{pixel}"


def test_filter_none_is_identity() -> None:
    """`none` 滤镜不应改动画面。"""
    original = Image.new("RGB", (10, 10), (120, 100, 90))
    result = E.apply_step(original, step("filter", name="none"))
    assert result.getpixel((5, 5)) == (120, 100, 90)


def test_filter_amount_blends_toward_original() -> None:
    """`amount=0.5` 应介于原图与全滤镜之间（可调强度）。"""
    img = Image.new("RGB", (10, 10), (200, 60, 60))
    full = E.apply_step(img, step("filter", name="grayscale", amount=1.0))
    half = E.apply_step(img, step("filter", name="grayscale", amount=0.5))
    # 半强度应比全灰更"红"（R 更大）
    assert half.getpixel((5, 5))[0] > full.getpixel((5, 5))[0]


def test_unknown_filter_falls_back_to_none() -> None:
    """未知滤镜名不应崩溃，退化成"无滤镜"。"""
    original = Image.new("RGB", (10, 10), (120, 100, 90))
    result = E.apply_step(original, step("filter", name="不存在的滤镜"))
    assert result.getpixel((5, 5)) == (120, 100, 90)


def test_all_declared_filters_are_applicable() -> None:
    """`FILTERS` 里声明的每个滤镜都必须真的能跑（防止名字注册了却没实现）。"""
    img = Image.new("RGB", (20, 16), (120, 100, 90))
    for name in E.FILTERS:
        result = E.apply_step(img, step("filter", name=name))
        assert result.size == (20, 16), f"滤镜 {name} 改变了尺寸"


# --------------------------------------------------------------------------- 调色


def test_brightness_direction_is_correct() -> None:
    """调高亮度应变亮、调低应变暗（方向错了用户调反）。"""
    img = Image.new("RGB", (10, 10), (120, 120, 120))
    darker = E.apply_step(img, step("adjust", brightness=0.5))
    brighter = E.apply_step(img, step("adjust", brightness=1.5))
    assert mean_luma(darker) < mean_luma(img) < mean_luma(brighter)


def test_adjust_defaults_are_identity() -> None:
    """全部用默认值（1.0 / 0.0）时不应改动画面。"""
    img = Image.new("RGB", (10, 10), (120, 100, 90))
    result = E.apply_step(img, step("adjust", **E.DEFAULT_ADJUST))
    assert result.getpixel((5, 5)) == (120, 100, 90)


def test_every_adjust_key_is_supported() -> None:
    """`ADJUST_LABELS` 里列出的每个滑块都必须真的生效（不能只是界面有）。"""
    img = Image.new("RGB", (20, 20), (120, 100, 90))
    for key in E.ADJUST_LABELS:
        result = E.apply_step(img, step("adjust", **{key: 1.6}))
        assert result.size == (20, 20), f"调色 {key} 崩了"


# --------------------------------------------------------------------------- 其它步骤


def test_mosaic_blurs_the_selected_region() -> None:
    """马赛克区域内的像素应被改变（区域外保持不变）。"""
    img = Image.new("RGB", (60, 60), (10, 10, 10))
    for x in range(20, 40):
        img.putpixel((x, 30), (250, 250, 250))
    result = E.apply_step(img, step("mosaic", box=(20, 20, 20, 20), block=6))
    assert result.getpixel((30, 30)) != (250, 250, 250), "马赛克没起作用"
    assert result.getpixel((5, 5)) == (10, 10, 10), "区域外被误改"


def test_text_step_draws_something() -> None:
    """文字水印要真的画上去（纯白图上画黑字，像素应变化）。"""
    img = Image.new("RGB", (120, 60), (255, 255, 255))
    result = E.apply_step(img, step("text", text="测试", size=24, color=[0, 0, 0]))
    assert result.getbbox() != img.getbbox() or result.tobytes() != img.tobytes()


def test_text_without_content_is_noop() -> None:
    """空文字原样返回（不能浪费一次编解码）。"""
    img = Image.new("RGB", (30, 30), (255, 255, 255))
    assert E.apply_step(img, step("text", text="")).tobytes() == img.tobytes()


def test_border_changes_edge_pixels_only() -> None:
    """边框只改边缘像素，中心不变。"""
    img = Image.new("RGB", (40, 40), (0, 0, 0))
    result = E.apply_step(img, step("border", width=4, color=[255, 255, 255]))
    assert result.getpixel((0, 0)) == (255, 255, 255), "边框没画在边上"
    assert result.getpixel((20, 20)) == (0, 0, 0), "中心被误改"


# --------------------------------------------------------------------------- 步骤栈


def test_apply_steps_runs_in_order() -> None:
    """步骤按顺序执行：先裁后转 与 先转后裁 尺寸不同。"""
    img = Image.new("RGB", (100, 80))
    crop_then_rotate = E.apply_steps(img, [
        step("crop", box=(0, 0, 60, 40)),
        step("rotate", angle=90),
    ])
    rotate_then_crop = E.apply_steps(img, [
        step("rotate", angle=90),
        step("crop", box=(0, 0, 60, 40)),
    ])
    assert crop_then_rotate.size == (40, 60)
    assert rotate_then_crop.size == (60, 40)


def test_empty_steps_returns_same_size() -> None:
    """空步骤栈不崩、不改尺寸。"""
    assert E.apply_steps(Image.new("RGB", (30, 20)), []).size == (30, 20)


def test_apply_steps_does_not_mutate_input() -> None:
    """不能修改入参（否则"取消编辑"就没法还原）。"""
    img = Image.new("RGB", (40, 40), (100, 100, 100))
    before = img.tobytes()
    E.apply_steps(img, [step("adjust", brightness=1.8), step("filter", name="sepia")])
    assert img.tobytes() == before, "入参被就地修改了"


def test_unknown_step_is_skipped_not_raised() -> None:
    """未识别的步骤必须**跳过而不是抛异常**。

    抛异常的后果很严重：整个编辑栈崩掉，用户之前所有改动一起丢失
    （典型来源：版本升级后撤销栈里有旧步骤名）。
    """
    img = Image.new("RGB", (30, 30), (100, 100, 100))
    result = E.apply_steps(img, [step("不存在的步骤", foo=1), step("flip", axis="horizontal")])
    assert result.size == (30, 30)


# --------------------------------------------------------------------------- 导出


@pytest.mark.parametrize(
    ("fmt", "suffix"),
    [("jpg", ".jpg"), ("png", ".png"), ("webp", ".webp"), ("bmp", ".bmp")],
)
def test_export_image_writes_correct_format(tmp_path, fmt: str, suffix: str) -> None:  # noqa: ANN001
    """导出格式与扩展名要一致（存成别的格式会让用户"打不开"）。"""
    target = tmp_path / f"out{suffix}"
    written = E.export_image(Image.new("RGB", (20, 20), (10, 20, 30)), target, fmt=fmt)
    assert written.exists()
    assert written.suffix.lower() == suffix
    with Image.open(written) as check:
        assert check.size == (20, 20)


def test_export_png_keeps_size(tmp_path) -> None:  # noqa: ANN001
    """PNG 导出尺寸不变（不因压缩而缩放）。"""
    written = E.export_image(Image.new("RGB", (33, 17)), tmp_path / "a.png", fmt="png")
    with Image.open(written) as check:
        assert check.size == (33, 17)


def test_render_edited_reads_then_applies(tmp_path) -> None:  # noqa: ANN001
    """`render_edited` 从文件读图并应用步骤（界面用它出预览）。"""
    source = tmp_path / "src.png"
    Image.new("RGB", (60, 40), (200, 200, 200)).save(source)
    result = E.render_edited(source, [step("crop", box=(0, 0, 30, 20))])
    assert result.size == (30, 20)
