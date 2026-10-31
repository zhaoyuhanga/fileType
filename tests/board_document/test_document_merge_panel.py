"""文档板块 · 合并面板（`boards/document/merge_page.py`）测试。

## 为什么这个页面必须有测试

321 行、此前**零覆盖**。它把用户在界面上的十几个开关**翻译成 `MergeOptions`**，
这一层翻译错了，用户会得到一份"参数不对"的合并结果，而界面显示一切正常。

最值得测的是 `options()` 里的**术语替换解析**：用户在输入框里手写
`旧=新,旧2=新2`，解析规则有大量边界（漏写等号、等号在值里、空键、空格）。
解析错了就是"合并了但术语没换"，用户很难定位。

`_preview()` 里还有一条真实校验：主文档和副文档不能是同一个文件。
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def library(tmp_path: Path):  # noqa: ANN201
    from modu_workbench.core.document.library import DocumentLibrary
    from modu_workbench.core.document.storage import DocumentStorage

    storage = DocumentStorage(str(tmp_path / "doc.db"))
    lib = DocumentLibrary(storage, output_dir=str(tmp_path / "out"))
    yield lib
    storage.close()


@pytest.fixture()
def panel(qapp, library):  # noqa: ANN001, ANN201
    from modu_workbench.boards.document.merge_page import MergePanel

    widget = MergePanel(library)
    yield widget
    widget.deleteLater()


# --------------------------------------------------------------------------- 术语替换解析


def test_term_map_parses_pairs(panel) -> None:  # noqa: ANN001
    """`旧=新,旧2=新2` 解析成字典。"""
    panel._term_map.setText("公司=集团,部门=事业部")
    panel._unify_terms.setChecked(True)
    options = panel.options()
    assert options.term_map == {"公司": "集团", "部门": "事业部"}
    assert options.unify_terms is True


def test_term_map_skips_chunks_without_equals(panel) -> None:  # noqa: ANN001
    """没有等号的片段跳过（用户可能顺手打了逗号多一项）。"""
    panel._term_map.setText("公司=集团,随便写的,部门=事业部")
    panel._unify_terms.setChecked(True)
    options = panel.options()
    assert options.term_map == {"公司": "集团", "部门": "事业部"}


def test_term_map_skips_empty_key(panel) -> None:  # noqa: ANN001
    """`=新词`（键为空）必须跳过，否则会产生一个空字符串替换规则 ——
    那会把文档里每个位置都"替换"一次，等于毁掉格式。"""
    panel._term_map.setText("=坏规则,公司=集团")
    panel._unify_terms.setChecked(True)
    options = panel.options()
    assert "" not in options.term_map
    assert options.term_map == {"公司": "集团"}


def test_term_map_allows_empty_value(panel) -> None:  # noqa: ANN001
    """`旧=` 表示"删除这个词"——值是空串是合法意图，不能被丢掉。"""
    panel._term_map.setText("请删除=")
    panel._unify_terms.setChecked(True)
    options = panel.options()
    assert options.term_map == {"请删除": ""}


def test_term_map_keeps_equals_in_value(panel) -> None:  # noqa: ANN001
    """值里带等号时只按**第一个**等号切分（`a=b=c` → `{a: 'b=c'}`）。"""
    panel._term_map.setText("公式=SUM(B2:B3)=说明")
    panel._unify_terms.setChecked(True)
    options = panel.options()
    assert options.term_map == {"公式": "SUM(B2:B3)=说明"}


def test_term_map_strips_whitespace(panel) -> None:  # noqa: ANN001
    """键值两侧空格要去掉（用户手打很容易带空格）。"""
    panel._term_map.setText("  公司 = 集团  , 部门 = 事业部 ")
    panel._unify_terms.setChecked(True)
    options = panel.options()
    assert options.term_map == {"公司": "集团", "部门": "事业部"}


def test_unify_terms_disabled_when_no_terms(panel) -> None:  # noqa: ANN001
    """勾了"统一术语"但一个有效规则都没写时，`unify_terms` 必须是 False。

    否则引擎会带着空规则表跑一遍替换（无用功，还可能触发额外分支）。
    """
    panel._term_map.setText("")
    panel._unify_terms.setChecked(True)
    options = panel.options()
    assert options.unify_terms is False
    assert options.term_map == {}


def test_unify_terms_disabled_when_unchecked(panel) -> None:  # noqa: ANN001
    """没勾选时即便填了规则也不生效（用户的勾选优先于输入框内容）。"""
    panel._term_map.setText("公司=集团")
    panel._unify_terms.setChecked(False)
    options = panel.options()
    assert options.unify_terms is False


# --------------------------------------------------------------------------- 其它选项


def test_options_defaults_are_sane(panel) -> None:  # noqa: ANN001
    """默认参数必须安全：模式有值、阈值在范围内、模板有值。"""
    options = panel.options()
    assert options.mode, "合并模式不能为空"
    assert 0 <= options.dedupe_threshold <= 100, (
        f"去重阈值越界：{options.dedupe_threshold}"
    )
    assert options.template, "模板不能为空"


def test_options_reflects_mode_selection(panel) -> None:  # noqa: ANN001
    """切换合并模式后 `options().mode` 要跟着变。"""
    count = panel._mode.count()
    assert count >= 2, "合并模式至少要有两个选项"
    seen: set[str] = set()
    for index in range(count):
        panel._mode.setCurrentIndex(index)
        seen.add(panel.options().mode)
    assert len(seen) == count, f"不同模式解析出了重复的 mode 值：{seen}"


def test_options_section_title_trimmed(panel) -> None:  # noqa: ANN001
    """分节标题要去掉首尾空格。"""
    panel._section_title.setText("  附件说明  ")
    assert panel.options().section_title == "附件说明"


def test_capability_hint_is_non_empty(panel) -> None:  # noqa: ANN001
    """能力提示不能是空串（界面要靠它告诉用户"当前能做什么"）。"""
    hint = panel.capability_hint()
    assert isinstance(hint, str)
    assert hint.strip(), "合并页的能力提示为空"


# --------------------------------------------------------------------------- 预览校验


def test_preview_rejects_same_file(panel, tmp_path: Path) -> None:  # noqa: ANN001
    """主文档与副文档是同一个文件时必须拦下。

    不拦的话会拿一份文档和自己合并 —— 要么得到重复内容，要么在去重逻辑里
    死循环，而用户完全看不出原因。
    """
    same = tmp_path / "same.md"
    same.write_text("# 标题\n\n正文。", encoding="utf-8")
    panel._main_path.setText(str(same))
    panel._other_path.setText(str(same))

    messages: list[str] = []
    panel._toaster.info = lambda text, *a, **k: messages.append(text)  # type: ignore[method-assign]
    panel._preview()

    assert messages, "同一个文件却没有给出提示"
    assert "同一个文件" in messages[0] or "不同" in messages[0]
    # 且没有真的产生合并结果
    assert panel._result is None


def test_preview_rejects_missing_input(panel) -> None:  # noqa: ANN001
    """未选择文件时给出提示而不是崩。"""
    panel._main_path.setText("")
    panel._other_path.setText("")
    messages: list[str] = []
    panel._toaster.info = lambda text, *a, **k: messages.append(text)  # type: ignore[method-assign]
    panel._preview()
    assert messages, "空路径应当有提示"


def test_apply_disabled_before_preview(panel) -> None:  # noqa: ANN001
    """没预览过就不能点"应用"（避免基于空结果写库）。"""
    assert panel._apply_button.isEnabled() is False
    assert panel._export_button.isEnabled() is False
