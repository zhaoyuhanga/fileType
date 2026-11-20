"""查看/编辑器状态机回归：未保存判定、预览切换、读取失败禁用保存、Ctrl+S。"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from modu_workbench.boards.convert import doc_viewer as viewer_module
from modu_workbench.boards.convert.doc_viewer import DocViewerDialog


@pytest.fixture()
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def _dispose_dialogs(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    """收尾时销毁所有查看器：留在 topLevelWidgets 里的脏窗口会在会话结束再弹确认框。"""
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    yield
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, DocViewerDialog):
            widget._dirty = False  # noqa: SLF001
            widget.close()
            widget.deleteLater()
    qapp.processEvents()


def _open(path: Path) -> DocViewerDialog:
    return DocViewerDialog(str(path))


def test_opening_a_file_is_not_dirty(qapp: QApplication, tmp_path: Path) -> None:
    path = tmp_path / "笔记.txt"
    path.write_text("原文", encoding="utf-8")
    dialog = _open(path)
    try:
        assert not dialog._dirty  # noqa: SLF001
        assert " *" not in dialog.windowTitle()
        assert not dialog._has_unsaved()  # noqa: SLF001
    finally:
        dialog.close()


def test_edit_then_preview_still_counts_as_unsaved(
    qapp: QApplication, tmp_path: Path
) -> None:
    """编辑 → 切预览 → 关闭：以前 `_current_text()` 返回旧内容，改动被静默丢弃。"""
    path = tmp_path / "笔记.txt"
    path.write_text("原文", encoding="utf-8")
    dialog = _open(path)
    try:
        dialog._set_mode("edit")  # noqa: SLF001
        dialog._editor.setPlainText("改过的内容")  # noqa: SLF001
        assert dialog._dirty  # noqa: SLF001

        dialog._set_mode("preview")  # noqa: SLF001
        assert dialog._current_text() == "改过的内容"  # noqa: SLF001
        assert dialog._has_unsaved()  # noqa: SLF001
        assert "改过的内容" in dialog._preview.toPlainText()  # noqa: SLF001
    finally:
        dialog.close()


def test_typing_back_to_original_clears_dirty(qapp: QApplication, tmp_path: Path) -> None:
    path = tmp_path / "笔记.txt"
    path.write_text("原文", encoding="utf-8")
    dialog = _open(path)
    try:
        dialog._set_mode("edit")  # noqa: SLF001
        dialog._editor.setPlainText("临时")  # noqa: SLF001
        assert dialog._dirty  # noqa: SLF001
        dialog._editor.setPlainText("原文")  # noqa: SLF001
        assert not dialog._dirty  # noqa: SLF001
    finally:
        dialog.close()


def test_save_writes_editor_content_when_in_preview(
    qapp: QApplication, tmp_path: Path
) -> None:
    path = tmp_path / "笔记.txt"
    path.write_text("原文", encoding="utf-8")
    dialog = _open(path)
    try:
        dialog._set_mode("edit")  # noqa: SLF001
        dialog._editor.setPlainText("新内容")  # noqa: SLF001
        dialog._set_mode("preview")  # noqa: SLF001
        dialog._save()  # noqa: SLF001
        assert path.read_text(encoding="utf-8") == "新内容"
        assert not dialog._has_unsaved()  # noqa: SLF001
    finally:
        dialog.close()


def test_failed_read_disables_save_and_never_truncates(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "坏文件.txt"
    path.write_text("原始内容", encoding="utf-8")

    def boom(_path):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(viewer_module, "read_text_smart", boom)
    warnings: list[object] = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *args, **kwargs: warnings.append(args),
    )
    dialog = _open(path)
    try:
        assert dialog._load_failed  # noqa: SLF001
        assert not dialog._save_btn.isEnabled()  # noqa: SLF001
        dialog._save()  # noqa: SLF001
        assert path.read_text(encoding="utf-8") == "原始内容", "读取失败后保存不得清空原文件"
        assert warnings
    finally:
        dialog.close()


def test_ctrl_s_shortcut_is_installed(qapp: QApplication, tmp_path: Path) -> None:
    path = tmp_path / "笔记.txt"
    path.write_text("原文", encoding="utf-8")
    dialog = _open(path)
    try:
        sequences = {shortcut.key().toString() for shortcut in dialog._shortcuts}  # noqa: SLF001
        assert "Ctrl+S" in sequences
    finally:
        dialog.close()
