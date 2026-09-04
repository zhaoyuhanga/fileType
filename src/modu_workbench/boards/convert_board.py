"""墨读转换板块：文件列表 + 动作选择 + 批量执行（后台线程/取消/进度）。"""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.convert import engine as convert_engine
from modu_workbench.core.convert.formats import TARGET_EXTENSION, format_from_extension, format_label
from modu_workbench.core.convert.registry import ConverterAction, common_actions, get_action
from modu_workbench.ui_kit.toast import Toaster

VIEWABLE_DOC_EXTENSIONS = {".txt", ".md", ".json", ".mp4"}


class _ConvertWorker(QThread):
    started = Signal(str)
    finished_file = Signal(str, str, str)  # path, status, message/output

    def __init__(self, action: ConverterAction, files: list[str], output_dir: str, parent=None):
        super().__init__(parent)
        self._action = action
        self._files = files
        self._output_dir = output_dir
        self.cancel = threading.Event()

    def request_cancel(self) -> None:
        self.cancel.set()

    def run(self) -> None:  # noqa: D102
        for file_path in self._files:
            if self.cancel.is_set():
                self.finished_file.emit(file_path, "cancelled", "已取消")
                continue
            self.started.emit(file_path)
            result = convert_engine.run_conversion(self._action, file_path, self._output_dir, cancel=self.cancel)
            detail = result.output_path if result.ok else (result.message or "")
            self.finished_file.emit(file_path, result.status, detail or "")


class ConvertBoardPage(QWidget):
    """转换板块页（占位被替换为真实实现）。"""

    def __init__(self, output_dir: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._toaster = Toaster(self)
        self._output_dir = output_dir or str(default_output_dir())
        self._worker: _ConvertWorker | None = None
        self._rows: list[dict] = []  # path/name/ext/format/checked/status

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        toolbar = QHBoxLayout()
        add_files = QPushButton("添加文件")
        add_files.clicked.connect(self._pick_files)
        add_folder = QPushButton("添加文件夹")
        add_folder.clicked.connect(self._pick_folder)
        clear = QPushButton("清空列表")
        clear.clicked.connect(self._clear_rows)
        self._out_input = QLineEdit(self._output_dir)
        self._out_input.editingFinished.connect(self._commit_output_dir)
        browse_out = QPushButton("输出目录")
        browse_out.clicked.connect(self._pick_output_dir)
        toolbar.addWidget(add_files)
        toolbar.addWidget(add_folder)
        toolbar.addWidget(clear)
        toolbar.addSpacing(8)
        toolbar.addWidget(QLabel("输出："))
        toolbar.addWidget(self._out_input, 1)
        toolbar.addWidget(browse_out)
        layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(["", "文件名", "格式", "状态", "详情/输出", "查看"])
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.setColumnWidth(0, 36)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(2, 90)
        self._table.setColumnWidth(3, 130)
        self._table.setColumnWidth(4, 240)
        splitter.addWidget(self._table)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        self._actions_box = QVBoxLayout()
        right_layout.addWidget(QLabel("可用转换动作（按列表文件自动匹配）"))
        right_layout.addLayout(self._actions_box)
        right_layout.addStretch(1)

        run_row = QHBoxLayout()
        self._run_button = QPushButton("开始转换")
        self._run_button.setEnabled(False)
        self._run_button.clicked.connect(self._start)
        self._cancel_button = QPushButton("取消")
        self._cancel_button.setEnabled(False)
        self._cancel_button.clicked.connect(self._cancel)
        run_row.addWidget(self._run_button)
        run_row.addWidget(self._cancel_button)
        right_layout.addLayout(run_row)
        self._hint = QLabel("提示：双击文件行可本地查看/编辑 txt、md、json、mp4")
        self._hint.setObjectName("readerStatus")
        right_layout.addWidget(self._hint)
        splitter.addWidget(right)
        splitter.setSizes([720, 300])
        layout.addWidget(splitter, 1)

        self._table.itemChanged.connect(self._on_item_changed)
        self._table.cellDoubleClicked.connect(self._on_double_click)
        self._rebuild_action_list()

    # ---------- 导入 ----------

    def _pick_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择要转换的文件", "", "支持文件 (*.*)"
        )
        self._append_paths(paths)

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹（递归收集支持的文件）")
        if folder:
            files = [str(p) for p in Path(folder).rglob("*") if p.is_file()]
            self._append_paths(files)

    def _append_paths(self, paths: list[str]) -> None:
        existing = {row["path"] for row in self._rows}
        added = 0
        for raw in paths:
            path = Path(raw)
            if not path.is_file() or str(path) in existing:
                continue
            ext = path.suffix.lower()
            if not ext:
                continue
            fmt = format_from_extension(ext)
            self._rows.append({
                "path": str(path),
                "name": path.name,
                "ext": ext,
                "format": fmt,
                "checked": True,
                "status": "待转换",
            })
            added += 1
        if added:
            self._rebuild_table()
            self._rebuild_action_list()
            self._toaster.success(f"已添加 {added} 个文件")
        else:
            self._toaster.info("没有新增文件")

    def _clear_rows(self) -> None:
        if self._worker is not None:
            return
        self._rows.clear()
        self._rebuild_table()
        self._rebuild_action_list()

    # ---------- 输出目录 ----------

    def _pick_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录", self._output_dir)
        if folder:
            self._output_dir = folder
            self._out_input.setText(folder)

    def _commit_output_dir(self) -> None:
        text = self._out_input.text().strip()
        if text:
            self._output_dir = text

    # ---------- 表格 ----------

    def _rebuild_table(self) -> None:
        self._table.blockSignals(True)
        self._table.setRowCount(len(self._rows))
        for row_index, row in enumerate(self._rows):
            box = QTableWidgetItem()
            box.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            box.setCheckState(Qt.CheckState.Checked if row["checked"] else Qt.CheckState.Unchecked)
            self._table.setItem(row_index, 0, box)
            self._table.setItem(row_index, 1, QTableWidgetItem(row["name"]))
            self._table.setItem(row_index, 2, QTableWidgetItem(format_label(row["format"]) if row["format"] != "unknown" else "未知"))
            self._table.setItem(row_index, 3, QTableWidgetItem(row["status"]))
            self._table.setItem(row_index, 4, QTableWidgetItem(row.get("detail", "")))
            viewable = row["ext"] in VIEWABLE_DOC_EXTENSIONS
            action = QTableWidgetItem("查看/编辑" if viewable else "—")
            action.setForeground(Qt.GlobalColor.darkBlue if viewable else Qt.GlobalColor.gray)
            self._table.setItem(row_index, 5, action)
        self._table.blockSignals(False)
        # 动作列表仅在格式/勾选变化时由调用方显式刷新（状态变化不刷新，避免重置选择）

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0 or item.row() >= len(self._rows):
            return
        self._rows[item.row()]["checked"] = item.checkState() == Qt.CheckState.Checked
        self._rebuild_action_list()

    def _on_double_click(self, row: int, _column: int) -> None:
        if row >= len(self._rows):
            return
        entry = self._rows[row]
        if entry["ext"] not in VIEWABLE_DOC_EXTENSIONS:
            return
        from .doc_viewer import DocViewerDialog

        dialog = DocViewerDialog(entry["path"], self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()

    # ---------- 动作与执行 ----------

    def _checked_files(self) -> list[str]:
        return [row["path"] for row in self._rows if row["checked"] and row["format"] != "unknown"]

    def _rebuild_action_list(self) -> None:
        previous_id = self._selected_action.id if getattr(self, "_selected_action", None) else None
        while self._actions_box.count():
            item = self._actions_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        formats = [row["format"] for row in self._rows if row["checked"] and row["format"] != "unknown"]
        actions = common_actions(formats) if formats else []
        self._current_actions: list[ConverterAction] = actions
        self._action_buttons: list[QPushButton] = []
        for action in actions:
            button = QPushButton(action.label)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, a=action: self._select_action(a))
            self._actions_box.addWidget(button)
            self._action_buttons.append(button)
        self._selected_action = next((a for a in actions if a.id == previous_id), actions[0] if actions else None)
        if self._selected_action is not None and self._action_buttons:
            selected = self._selected_action
            for button, candidate in zip(self._action_buttons, self._current_actions):
                button.setChecked(candidate.id == selected.id)
        self._run_button.setEnabled(bool(actions) and self._worker is None)

    def _select_action(self, action: ConverterAction) -> None:
        self._selected_action = action
        for button, candidate in zip(self._action_buttons, self._current_actions):
            button.setChecked(candidate.id == action.id)

    def _start(self) -> None:
        if self._worker is not None or self._selected_action is None:
            return
        files = self._checked_files()
        if not files:
            self._toaster.info("请先勾选要转换的文件")
            return
        Path(self._output_dir).mkdir(parents=True, exist_ok=True)
        for row in self._rows:
            row["status"] = "排队中"
            row["detail"] = ""
        self._rebuild_table()

        worker = _ConvertWorker(self._selected_action, files, self._output_dir, self)
        worker.started.connect(self._on_started)
        worker.finished_file.connect(self._on_finished_file)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        self._run_button.setEnabled(False)
        self._cancel_button.setEnabled(True)
        worker.start()

    def _on_started(self, path: str) -> None:
        for row in self._rows:
            if row["path"] == path:
                row["status"] = "转换中…"
        self._update_row_ui(path)

    def _on_finished_file(self, path: str, status: str, message: str) -> None:
        for row in self._rows:
            if row["path"] != path:
                continue
            if status == "succeeded":
                row["status"] = "成功"
            elif status == "cancelled":
                row["status"] = "已取消"
            else:
                row["status"] = "失败"
            row["detail"] = message
        self._update_row_ui(path)

    def _update_row_ui(self, path: str) -> None:
        for index, row in enumerate(self._rows):
            if row["path"] == path:
                self._table.item(index, 3).setText(row["status"])
                self._table.item(index, 4).setText(row.get("detail", ""))
                return

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()
            self._toaster.info("正在取消剩余转换…")

    def _on_worker_finished(self) -> None:
        self._worker = None
        self._run_button.setEnabled(bool(self._current_actions))
        self._cancel_button.setEnabled(False)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker is not None:
            self._worker.request_cancel()
            self._worker.wait(3000)
        super().closeEvent(event)


def default_output_dir() -> Path:
    base = Path.home() / "Documents" / "墨读转换输出"
    base.mkdir(parents=True, exist_ok=True)
    return base
