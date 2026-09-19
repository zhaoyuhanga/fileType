"""墨软转换板块：文件列表 + 动作选择 + 批量执行（后台线程/取消/进度）。"""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.convert import engine as convert_engine
from modu_workbench.core.convert.capabilities import blocked_reason
from modu_workbench.core.convert.formats import TARGET_EXTENSION, format_from_extension, format_label
from modu_workbench.core.convert.registry import (
    ConverterAction,
    common_actions,
    get_action,
    union_actions,
)
from modu_workbench.ui_kit.components import EmptyState, PageHeader, TaskBar
from modu_workbench.ui_kit.settings import app_settings
from modu_workbench.ui_kit.tokens import PAGE_MARGIN, ROW_GAP, SECTION_GAP, SPACE
from modu_workbench.ui_kit.toast import Toaster

VIEWABLE_DOC_EXTENSIONS = {".txt", ".md", ".json", ".mp4"}

# 动作面板的排序与分组：按"用户想干什么"排（文档 → 图片 → 音频 → 视频 → 数据 → 字幕 → 电子书 → 归档）
_CATEGORY_ORDER = ("document", "image", "audio", "video", "data", "subtitle", "ebook", "archive")
_CATEGORY_LABELS = {
    "document": "文档", "image": "图片", "audio": "音频", "video": "视频",
    "data": "数据", "subtitle": "字幕", "ebook": "电子书", "archive": "归档",
}


def _action_sort_key(action) -> tuple[int, str]:  # noqa: ANN001
    index = _CATEGORY_ORDER.index(action.category) if action.category in _CATEGORY_ORDER else 99
    return (index, action.label)


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
        # v1.0.0：前端统一为 Qt 单栈（原先的 React 内嵌前端与 QtWebEngine 已移除）
        self._toaster = Toaster(self)
        self._output_dir = output_dir or str(default_output_dir())
        self._worker: _ConvertWorker | None = None
        self._rows: list[dict] = []  # path/name/ext/format/checked/status

        layout = QVBoxLayout(self)
        layout.setContentsMargins(PAGE_MARGIN, SPACE["md"], PAGE_MARGIN, SPACE["md"])
        layout.setSpacing(SECTION_GAP)

        # 统一页头（与其他板块一致）
        self._header = PageHeader(
            "墨软转换",
            "本地离线转换：文本 / 文档 / 表格 / 图片 / 媒体 / 归档；双击文件可在本地查看或编辑。",
        )
        layout.addWidget(self._header)

        # 统一任务条：转换进度与完成汇总（空闲自动收起），解决"点了没反应"的反馈缺失
        self._task_bar = TaskBar("就绪。添加文件后选择动作，点「开始转换」。")
        self._task_bar.cancelled.connect(self._cancel)

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
        open_out = QPushButton("打开输出目录")
        open_out.setToolTip("在资源管理器里打开当前输出目录（转换完找不到文件时先点这里）")
        open_out.clicked.connect(lambda: self._reveal_output(""))
        toolbar.addWidget(add_files)
        toolbar.addWidget(add_folder)
        toolbar.addWidget(clear)
        toolbar.addSpacing(8)
        toolbar.addWidget(QLabel("输出："))
        toolbar.addWidget(self._out_input, 1)
        toolbar.addWidget(browse_out)
        toolbar.addWidget(open_out)

        board_settings = QPushButton("⚙ 板块设置")
        board_settings.setToolTip("打开「设置 → 转换」：默认输出目录、输出防覆盖、外部工具状态")
        board_settings.clicked.connect(self._open_board_settings)
        toolbar.addWidget(board_settings)
        layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(["选中", "文件名", "格式", "状态", "输出", "查看"])
        # 列宽策略：文件名吃掉剩余宽度（反馈里"文件名和详情挤在一起看不清"就是第 0/1 列
        # 都按默认宽度画、而富余宽度给了别处导致的），固定列按内容自适应，
        # 「输出」只显示文件名，完整路径进 tooltip。
        header = self._table.horizontalHeader()
        header.setMinimumSectionSize(56)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 52)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(4, 220)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._table.setWordWrap(False)
        self._table.verticalHeader().setDefaultSectionSize(30)
        self._table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

        left = QWidget()
        left.setObjectName("filePanel")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(14, 12, 14, 14)
        left_layout.setSpacing(6)
        left_header = QHBoxLayout()
        table_title = QLabel("待处理文件")
        table_title.setObjectName("sectionTitle")
        self._count_label = QLabel("0 项")
        self._count_label.setObjectName("viewerMetaLabel")
        left_header.addWidget(table_title)
        left_header.addStretch(1)
        left_header.addWidget(self._count_label)
        left_layout.addLayout(left_header)
        self._empty = EmptyState(
            "📄", "还没有待转换的文件",
            "用「添加文件 / 添加文件夹」把要处理的内容加进来，右侧会自动列出可用转换动作",
        )
        self._empty.setMaximumHeight(240)
        left_layout.addWidget(self._empty)
        left_layout.addWidget(self._table, 1)
        self._table.setVisible(False)          # 无文件时显示引导，有文件时切回表格
        splitter.addWidget(left)

        right = QWidget()
        right.setObjectName("actionPanel")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(8)
        title_label = QLabel("可用转换动作")
        title_label.setObjectName("sectionTitle")
        # 动作区提示：动态说明"为什么没有动作"（多格式勾选/未知格式）
        self._action_hint = QLabel("按列表中的文件自动匹配")
        self._action_hint.setObjectName("readerStatus")
        self._action_hint.setWordWrap(True)
        actions_host = QWidget()
        self._actions_box = QVBoxLayout(actions_host)
        self._actions_box.setContentsMargins(0, 2, 2, 2)
        self._actions_box.setSpacing(7)
        actions_scroll = QScrollArea()
        actions_scroll.setWidgetResizable(True)
        actions_scroll.setWidget(actions_host)
        right_layout.addWidget(title_label)
        right_layout.addWidget(self._action_hint)
        right_layout.addWidget(actions_scroll, 1)

        run_row = QHBoxLayout()
        self._run_button = QPushButton("开始转换")
        self._run_button.setObjectName("primaryButton")
        self._run_button.setEnabled(False)
        self._run_button.clicked.connect(self._start)
        self._cancel_button = QPushButton("取消")
        self._cancel_button.setEnabled(False)
        self._cancel_button.clicked.connect(self._cancel)
        run_row.addWidget(self._run_button)
        run_row.addWidget(self._cancel_button)
        right_layout.addLayout(run_row)

        self._book_btn = QPushButton("📚 将 TXT/EPUB 结果加入书架")
        self._book_btn.setEnabled(False)
        self._book_btn.clicked.connect(self._import_results_to_library)
        right_layout.addWidget(self._book_btn)
        self._book_candidates: list[str] = []

        self._hint = QLabel("提示：双击文件行可本地查看/编辑 txt、md、json、mp4")
        self._hint.setObjectName("readerStatus")
        right_layout.addWidget(self._hint)
        splitter.addWidget(right)
        layout.addWidget(self._task_bar)
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

    def _open_board_settings(self) -> None:
        """打开统一设置对话框的「转换」页（设置按板块区分）。"""
        from modu_workbench.ui_kit.settings import SettingsDialog

        SettingsDialog(self, initial="convert").exec()
        # 设置里可能改了默认输出目录，回到本页后同步一次
        stored = app_settings().value("convert/output_dir", "", type=str)
        if stored:
            self._output_dir = str(stored)
            self._out_input.setText(self._output_dir)

    # ---------- 表格 ----------

    def _rebuild_table(self) -> None:
        has_rows = bool(self._rows)
        self._empty.setVisible(not has_rows)
        self._table.setVisible(has_rows)
        self._table.blockSignals(True)
        self._table.setRowCount(len(self._rows))
        for row_index, row in enumerate(self._rows):
            box = QTableWidgetItem()
            box.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            box.setCheckState(Qt.CheckState.Checked if row["checked"] else Qt.CheckState.Unchecked)
            self._table.setItem(row_index, 0, box)
            name_item = QTableWidgetItem(row["name"])
            name_item.setToolTip(row["path"])
            self._table.setItem(row_index, 1, name_item)
            self._table.setItem(row_index, 2, QTableWidgetItem(format_label(row["format"]) if row["format"] != "unknown" else "未知"))
            self._table.setItem(row_index, 3, QTableWidgetItem(row["status"]))
            detail_item = QTableWidgetItem(self._short_detail(row.get("detail", "")))
            detail_item.setToolTip(row.get("detail", ""))
            self._table.setItem(row_index, 4, detail_item)
            viewable = row["ext"] in VIEWABLE_DOC_EXTENSIONS
            action = QTableWidgetItem("查看/编辑" if viewable else "—")
            action.setForeground(Qt.GlobalColor.darkBlue if viewable else Qt.GlobalColor.gray)
            self._table.setItem(row_index, 5, action)
        self._table.blockSignals(False)
        if hasattr(self, "_count_label"):
            self._count_label.setText(f"{len(self._rows)} 项")

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0 or item.row() >= len(self._rows):
            return
        self._rows[item.row()]["checked"] = item.checkState() == Qt.CheckState.Checked
        self._rebuild_action_list()

    def _on_double_click(self, row: int, column: int) -> None:
        if row >= len(self._rows):
            return
        entry = self._rows[row]
        if column == 4:
            # 双击「输出」= 在资源管理器里定位产物/打开输出目录
            self._reveal_output(entry.get("detail", ""))
            return
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
        # 始终列出「各自可用动作的并集」：
        # 以前是"先取共同动作，交集为空才退化为并集"，但归档压缩对**所有**格式都适用，
        # 于是混选 mp4+png+csv 时交集=8 个压缩动作，各格式自己的转换全被藏起来
        # （截图验收时发现）。运行时不适用的文件会被跳过并写明原因，所以并集更符合直觉。
        actions = union_actions(formats) if formats else []
        actions = sorted(actions, key=_action_sort_key)
        self._mixed_mode = len({fmt for fmt in formats}) > 1
        self._current_actions: list[ConverterAction] = actions
        self._action_buttons: list[QPushButton] = []
        blocked: list[str] = []
        for action in actions:
            reason = blocked_reason(action)
            button = QPushButton(action.label)
            button.setObjectName("actionOption")
            button.setProperty("active", False)
            tip = f"{_CATEGORY_LABELS.get(action.category, action.category)} · 输出 .{action.target_format}"
            if reason:
                # 缺依赖的动作**置灰 + 写明原因**，而不是让用户点了才拿到报错
                button.setEnabled(False)
                button.setToolTip(f"{tip}\n⚠ {reason}")
                blocked.append(reason)
            else:
                button.setToolTip(tip)
            button.clicked.connect(lambda _=False, a=action: self._select_action(a))
            self._actions_box.addWidget(button)
            self._action_buttons.append(button)
        self._blocked_reasons = blocked
        available = [a for a in actions if blocked_reason(a) is None]
        # 默认选中第一个**可用**动作：否则用户一进来点「开始转换」就是注定失败的
        self._selected_action = next(
            (a for a in actions if a.id == previous_id and blocked_reason(a) is None),
            available[0] if available else None,
        )
        if not actions:
            self._action_hint.setText(
                "勾选的文件没有可用动作（可能是未知格式）；先右侧「添加文件」选择受支持的类型"
                if not formats else "已选格式暂无可用动作"
            )
        elif not available:
            self._action_hint.setText(
                f"⚠ 这些动作在当前机器上都缺少依赖：{blocked[0]}"
            )
        else:
            scope = (f"已勾选 {len(set(formats))} 种格式：下面列出各自可用的动作，"
                     "运行时不适用的文件会自动跳过（并在状态列写明原因）"
                     if self._mixed_mode else f"「{format_label(formats[0])}」可用动作（共 {len(actions)} 个）")
            if blocked:
                scope += f"；其中 {len(blocked)} 个因缺少依赖已置灰（悬停看原因）"
            self._action_hint.setText(scope)
        if self._selected_action is not None and self._action_buttons:
            for button, candidate in zip(self._action_buttons, self._current_actions):
                active = candidate.id == self._selected_action.id
                button.setProperty("active", active)
                style = button.style()
                style.unpolish(button)
                style.polish(button)
        self._run_button.setEnabled(bool(actions) and self._worker is None)

    def _select_action(self, action: ConverterAction) -> None:
        self._selected_action = action
        for button, candidate in zip(self._action_buttons, self._current_actions):
            active = candidate.id == action.id
            button.setProperty("active", active)
            style = button.style()
            style.unpolish(button)
            style.polish(button)

    def _start(self) -> None:
        if self._worker is not None or self._selected_action is None:
            return
        action = self._selected_action
        checked = self._checked_files()
        applicable = [p for p in checked
                      if action is not None and action.matches(
                          next((row["format"] for row in self._rows if row["path"] == p), ""))]
        skipped = [p for p in checked if p not in applicable]
        for path in skipped:
            for row in self._rows:
                if row["path"] == path:
                    row["status"] = "跳过"
                    row["detail"] = f"「{action.label if action else '该动作'}」不适用于此格式"
        if skipped:
            self._rebuild_table()
            self._toaster.info(f"已跳过 {len(skipped)} 个不适用该动作的文件")
        files = applicable
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
        name = Path(path).name
        index = next((i for i, row in enumerate(self._rows) if row["path"] == path), 0)
        total = max(1, len([row for row in self._rows if row["status"] in ("排队中", "转换中…")]))
        self._task_bar.report(f"正在转换（{index + 1}/{total}）：{name}", index, total)
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
                suffix = Path(message).suffix.lower()
                if suffix in (".txt", ".epub"):
                    self._book_candidates.append(message)
                    self._book_btn.setEnabled(True)
            elif status == "cancelled":
                row["status"] = "已取消"
            else:
                row["status"] = "失败"
            row["detail"] = message
        self._update_row_ui(path)

    def _short_detail(self, value: str) -> str:
        """「输出」列只显示文件名，完整路径进 tooltip（长路径挤在窄列里看不清）。"""
        text = (value or "").strip()
        if not text:
            return ""
        if "\\" in text or "/" in text:
            return Path(text).name or text
        return text

    def _reveal_output(self, value: str = "") -> None:
        """在资源管理器里定位输出文件；传空或文件不存在时打开输出目录。"""
        import subprocess

        text = (value or "").strip()
        candidate = Path(text) if text else None
        if candidate is not None and candidate.exists():
            subprocess.Popen(["explorer", "/select,", str(candidate)])
            return
        folder = Path(self._output_dir)
        if folder.is_dir():
            subprocess.Popen(["explorer", str(folder)])
        else:
            self._toaster.info(f"输出目录还不存在：{folder}")

    def _update_row_ui(self, path: str) -> None:
        for index, row in enumerate(self._rows):
            if row["path"] == path:
                self._table.item(index, 3).setText(row["status"])
                detail = row.get("detail", "")
                item = self._table.item(index, 4)
                item.setText(self._short_detail(detail))
                item.setToolTip(detail)
                return

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()
            self._toaster.info("正在取消剩余转换…")

    def _on_worker_finished(self) -> None:
        ok = len([row for row in self._rows if row["status"] == "成功"])
        failed = len([row for row in self._rows if row["status"] == "失败"])
        skipped = len([row for row in self._rows if row["status"] == "跳过"])
        summary = f"转换完成：成功 {ok}，失败 {failed}" + (f"，跳过 {skipped}" if skipped else "")
        # 明确写出输出目录：反馈里有"显示成功但输出目录没数据"，
        # 一大原因是默认输出目录在系统「文档」下（可能被 OneDrive 重定向），用户没找到
        self._task_bar.idle(f"{summary} · 输出目录：{self._output_dir}")
        if ok:
            self._toaster.success(summary)
        elif failed:
            self._toaster.error(summary + "（可查看状态列的失败原因）")
        self._worker = None
        self._run_button.setEnabled(bool(self._current_actions))
        self._cancel_button.setEnabled(False)

    def _import_results_to_library(self) -> None:
        candidates = list(dict.fromkeys(self._book_candidates))
        if not candidates:
            return
        from modu_workbench.services.app_context import library

        imported = 0
        for file_path in candidates:
            try:
                library().import_path(file_path)
                imported += 1
            except Exception as error:  # noqa: BLE001
                self._toaster.info(f"导入失败：{file_path}（{error}）")
        if imported:
            self._toaster.success(f"已将 {imported} 本加入书架")
        self._book_candidates.clear()
        self._book_btn.setEnabled(False)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker is not None:
            self._worker.request_cancel()
            self._worker.wait(3000)
        super().closeEvent(event)


def default_output_dir() -> Path:
    """默认输出目录：优先系统「文档」位置，退回 ~/Documents。

    必须走 `QStandardPaths`：中文 Windows 上「文档」常被 OneDrive 重定向到
    `%USERPROFILE%\\OneDrive\\文档`，而 `Path.home()/"Documents"` 会新建一个
    用户根本不会去看的目录 —— 这正是「转换成功但输出目录没数据」的头号原因。
    """
    from PySide6.QtCore import QStandardPaths

    location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    base = Path(location) / "墨软转换输出" if location else Path.home() / "Documents" / "墨软转换输出"
    base.mkdir(parents=True, exist_ok=True)
    return base
