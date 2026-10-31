"""文档板块 · 安全/脱敏面板（`boards/document/security_page.py`）测试。

## 为什么这个页面必须有测试

421 行、此前**零覆盖**。它是"把文档脱敏后再用"的操作入口 ——
涉及身份证、手机号、银行卡这些**真实隐私**。这里出错的两个方向都很糟：

- **漏脱**：用户以为脱敏了，实际把原件内容发了出去；
- **误脱**：把正文里的普通数字也打了码，文档变得不可用。

本文件测的是面板这一层的行为：默认勾选哪些规则、没选规则时是否拦住、
是否改动原对象（能不能撤销）、非法输入是否兜住。

脱敏算法本身在 `test_document_security.py` 里已单测；这里只管"面板有没有正确使用它"。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from modu_workbench.core.document import security as S
from modu_workbench.core.document.models import BLOCK_PARAGRAPH, Block, DocumentIR


class FakeSettings:
    """内存版 QSettings 替身（测试不能碰真实注册表）。"""

    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def setValue(self, key: str, value: object) -> None:  # noqa: N802
        self.values[key] = value

    def value(self, key: str, default: object = None, **_kwargs: object) -> object:
        return self.values.get(key, default)

    def sync(self) -> None:
        return None


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
    from modu_workbench.boards.document.security_page import SecurityPanel

    widget = SecurityPanel(library)
    widget._settings = FakeSettings()      # 隔离真实注册表
    yield widget
    widget.deleteLater()


def ir_with_text(text: str) -> DocumentIR:
    ir = DocumentIR(title="测试")
    ir.blocks = [Block(kind=BLOCK_PARAGRAPH, text=text)]
    return ir


def collect_info(widget) -> list[str]:  # noqa: ANN001
    """把 toaster.info 换成记录器（面板用 info 提示"请先打开文档"）。"""
    messages: list[str] = []
    widget._toaster.info = lambda text, *a, **k: messages.append(text)  # type: ignore[method-assign]
    return messages


def collect_errors(widget) -> list[str]:  # noqa: ANN001
    messages: list[str] = []
    widget._toaster.error = lambda text, *a, **k: messages.append(text)  # type: ignore[method-assign]
    return messages


# --------------------------------------------------------------------------- 默认规则


def test_default_rules_are_the_common_pii(panel) -> None:  # noqa: ANN001
    """默认勾选必须覆盖最常见的五类个人信息。

    "默认不勾"会让用户在不知情的情况下把身份证号发出去 ——
    默认值在这里是一种安全承诺。
    """
    selected = set(panel._selected_rules())
    assert selected == set(S.DEFAULT_MASK_RULES), (
        f"默认勾选与约定的默认规则不一致：{selected}"
    )


def test_all_rules_are_offered_as_checkboxes(panel) -> None:  # noqa: ANN001
    """每个可选规则都要有勾选框（界面上不能漏掉某一类）。"""
    keys = {key for key, _label in S.mask_rule_choices()}
    assert set(panel._mask_boxes) == keys


def test_selected_rules_follow_checkboxes(panel) -> None:  # noqa: ANN001
    """取消勾选后 `_selected_rules()` 要如实反映。"""
    panel._mask_boxes["phone"].setChecked(False)
    assert "phone" not in panel._selected_rules()
    panel._mask_boxes["phone"].setChecked(True)
    assert "phone" in panel._selected_rules()


# --------------------------------------------------------------------------- 前置校验


def test_apply_without_document_is_refused(panel) -> None:  # noqa: ANN001
    """没有打开文档时点"应用脱敏"要提示，不能崩。"""
    messages = collect_info(panel)
    panel._apply_mask()
    assert messages, "没打开文档却没有提示"


def test_apply_without_rules_is_refused(panel) -> None:  # noqa: ANN001
    """一条规则都没勾时要拦住 —— 否则等于"脱敏"了一个空操作，
    用户会以为已经脱敏了。"""
    for box in panel._mask_boxes.values():
        box.setChecked(False)
    panel.set_ir(ir_with_text("电话 13812345678"))

    messages = collect_info(panel)
    panel._apply_mask()
    assert messages, "没勾选任何规则却没有提示"
    assert "脱敏" in messages[0]


def test_preview_without_document_is_refused(panel) -> None:  # noqa: ANN001
    """没打开文档时点"预览命中"要提示。"""
    messages = collect_info(panel)
    panel._preview_mask()
    assert messages


# --------------------------------------------------------------------------- 应用脱敏


def test_apply_mask_removes_pii(panel) -> None:  # noqa: ANN001
    """应用后文档里不能再出现完整手机号 / 身份证号。"""
    panel.set_ir(ir_with_text("电话 13812345678，身份证 110101199003071234"))
    panel._mask_boxes["phone"].setChecked(True)
    panel._mask_boxes["id_card"].setChecked(True)

    emitted: list[DocumentIR] = []
    panel.irChanged.connect(emitted.append)
    panel._apply_mask()

    text = emitted[0].text() if emitted else ""
    assert "13812345678" not in text, "手机号没有被脱掉"
    assert "110101199003071234" not in text, "身份证号没有被脱掉"
    assert "138****5678" in text, "脱敏保留了不该保留的位数"


def test_apply_mask_does_not_mutate_original(panel) -> None:  # noqa: ANN001
    """脱敏必须产出一份**新**文档，不能就地改（否则无法撤销/对比）。"""
    original = ir_with_text("电话 13812345678")
    panel.set_ir(original)
    panel._toaster.success = lambda *a, **k: None  # type: ignore[method-assign]

    panel._apply_mask()

    assert "13812345678" in original.text(), "原文档被就地改写了"


def test_apply_mask_only_uses_selected_rules(panel) -> None:  # noqa: ANN001
    """只勾手机号时，邮箱必须原样保留。"""
    panel.set_ir(ir_with_text("电话 13812345678 邮箱 zhangsan@example.com"))
    for box in panel._mask_boxes.values():
        box.setChecked(False)
    panel._mask_boxes["phone"].setChecked(True)
    panel._toaster.success = lambda *a, **k: None  # type: ignore[method-assign]

    emitted: list[DocumentIR] = []
    panel.irChanged.connect(emitted.append)
    panel._apply_mask()

    text = emitted[0].text()
    assert "zhangsan@example.com" in text, "未勾选的邮箱规则被误用了"
    assert "13812345678" not in text


def test_apply_mask_ignores_plain_numbers(panel) -> None:  # noqa: ANN001
    """普通数字（年份）不能被金额规则误伤 —— 面板层也要回归一次。

    之前这里有过真实缺陷：金额规则是裸的三位以上数字匹配，
    把「2024 年」打成「**** 年」。
    """
    panel.set_ir(ir_with_text("2024 年我们做了三件事。"))
    for box in panel._mask_boxes.values():
        box.setChecked(False)
    panel._mask_boxes["amount"].setChecked(True)
    panel._toaster.success = lambda *a, **k: None  # type: ignore[method-assign]

    emitted: list[DocumentIR] = []
    panel.irChanged.connect(emitted.append)
    panel._apply_mask()

    assert "2024" in emitted[0].text(), "年份被当金额脱掉了"


# --------------------------------------------------------------------------- 权限保存


def test_save_permissions_writes_all_keys(panel) -> None:  # noqa: ANN001
    """保存权限要把界面上的值写进设置（否则改了不生效）。"""
    panel._toaster.success = lambda *a, **k: None  # type: ignore[method-assign]
    panel._save_permissions()

    for key in (
        "document/allow_cloud",
        "document/allow_local",
        "document/mask_before_upload",
        "document/max_chars",
    ):
        assert key in panel._settings.values, f"{key} 没有被写入"


def test_invalid_price_falls_back_to_zero(panel) -> None:  # noqa: ANN001
    """价格填了非数字时退回 0.0，而不是崩或写进脏值。"""
    panel._toaster.success = lambda *a, **k: None  # type: ignore[method-assign]
    panel._price.setText("不是数字")
    panel._save_permissions()
    assert panel._settings.values["document/price_per_1k"] == 0.0


def test_save_permissions_emits_change(panel) -> None:  # noqa: ANN001
    """保存后要发出 `settingsChanged`（其它板块据此刷新）。"""
    panel._toaster.success = lambda *a, **k: None  # type: ignore[method-assign]
    fired: list[bool] = []
    panel.settingsChanged.connect(lambda: fired.append(True))
    panel._save_permissions()
    assert fired, "保存权限后没有发出 settingsChanged"
