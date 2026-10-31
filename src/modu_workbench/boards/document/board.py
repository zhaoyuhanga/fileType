"""墨软文档板块：一站式文档工作台（第六大板块）。

界面分区：
- 顶栏：添加文件/文件夹、输出目录、打开输出目录、搜索、板块设置；
- 左栏：文档库（名称/格式/分类/大小/状态），双击即在右侧打开；
- 右栏六个页签：**查看编辑 / 格式美化 / 填充计算 / 文档合并 / AI 循环美化 / 权限安全**；
- 底栏：统一任务条（解析、美化、合并、循环美化的进度与取消）。

所有重活（解析、合并、循环美化）都走后台线程；右侧各页通过信号与主视图同步文档内容，
因此"美化后立刻能看、合并后能确认、循环每轮能回滚"。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.document import (
    DocumentIR,
    DocumentLibrary,
    format_label,
    format_key_for_path,
    get_spec,
    summary_text,
)
from modu_workbench.core.document import formats as document_formats
from modu_workbench.ui_kit.components import EmptyState, PageHeader, TaskBar
from modu_workbench.ui_kit.settings import SettingsDialog, app_settings
from modu_workbench.ui_kit.tokens import PAGE_MARGIN, SPACE
from modu_workbench.ui_kit.toast import Toaster

from . import context as app_context
from . import widgets as W
from .beautify_page import BeautifyPanel
from .loop_page import LoopPanel
from .merge_page import MergePanel
from .security_page import SecurityPanel
from .sheet_page import SheetPanel
from .viewer import DocViewer

STATUS_NEW = "未打开"
STATUS_OPEN = "已解析"
STATUS_MISSING = "文件不存在"


def _format_size(size: int) -> str:
    value = float(size or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


class DocumentBoardPage(QWidget):
    """墨软文档板块页。"""

    def __init__(self, parent: Optional[QWidget] = None, *,
                 library: Optional[DocumentLibrary] = None):
        super().__init__(parent)
        self._toaster = Toaster(self)
        self._library: DocumentLibrary = library or app_context.document_library()
        self._settings = app_settings()
        self._rows: list[dict] = []
        self._current_ir: Optional[DocumentIR] = None
        self._open_docs: dict[str, DocumentIR] = {}
        self._worker: Optional[W.DocumentWorker] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(PAGE_MARGIN, SPACE["md"], PAGE_MARGIN, SPACE["md"])
        layout.setSpacing(SPACE["md"])

        shortcuts_button = QPushButton("⌨ 快捷键")
        shortcuts_button.setToolTip("查看本板块的键盘快捷键（键盘全流程可操作）")
        shortcuts_button.clicked.connect(self._show_shortcuts)
        self._header = PageHeader(
            "墨软文档",
            "查看与编辑、格式美化、自动填充与计算、AI 模型与工具调用、"
            "两个文档合并、AI 循环美化。" + summary_text(),
            [shortcuts_button],
        )
        layout.addWidget(self._header)
        layout.addLayout(self._build_toolbar())

        self._task_bar = TaskBar("就绪。添加文件后即可查看、美化、计算或合并。")
        self._task_bar.cancelled.connect(self._cancel_worker)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_left())
        splitter.addWidget(self._build_right())
        splitter.setSizes([380, 900])
        layout.addWidget(splitter, 1)
        layout.addWidget(self._task_bar)

        self._install_shortcuts()
        self._apply_accessible_names()
        self._reload_library()

    # ---------------------------------------------------------------- 快捷键与可访问性

    def _install_shortcuts(self) -> None:
        """键盘快捷键（需求 6.9「易用/可访问：键盘操作」）。

        绑定表集中在这里，界面按钮的 tooltip 会带上对应快捷键，用户不必去翻文档。
        """
        from PySide6.QtGui import QKeySequence, QShortcut

        bindings: tuple[tuple[str, str, object], ...] = (
            ("add_files", "Ctrl+O", self._pick_files),
            ("add_folder", "Ctrl+Shift+O", self._pick_folder),
            ("open_selected", "Ctrl+Return", self._open_selected),
            ("save", "Ctrl+S", self._save_current),
            ("save_as", "Ctrl+Shift+S", self._save_as_current),
            ("print", "Ctrl+P", self._print_current),
            ("close_tab", "Ctrl+W", lambda: self._on_doc_tab_closed(self._doc_tabs.currentIndex())),
            ("focus_search", "Ctrl+L", lambda: (self._search.setFocus(), self._search.selectAll())),
            ("find", "Ctrl+F", lambda: self._viewer._find_input.setFocus()),
            ("refresh", "F5", self.on_shown),
            ("pdf_tools", "Ctrl+Shift+P", self._open_pdf_tools),
            ("ocr", "Ctrl+Shift+R", self._run_ocr),
            ("remove", "Ctrl+Delete", self._remove_selected),
        )
        self._shortcuts: dict[str, QShortcut] = {}
        for name, sequence, slot in bindings:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(slot)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            self._shortcuts[name] = shortcut
        # 面板切换：Alt+1..6 对应右侧六个页签
        for index in range(6):
            shortcut = QShortcut(QKeySequence(f"Alt+{index + 1}"), self)
            shortcut.activated.connect(lambda position=index: self._tabs.setCurrentIndex(position))
            self._shortcuts[f"panel_{index + 1}"] = shortcut

    def shortcut_keys(self) -> dict[str, str]:
        """快捷键表（界面提示与测试共用）。"""
        return {name: shortcut.key().toString() for name, shortcut in self._shortcuts.items()}

    def _apply_accessible_names(self) -> None:
        """给关键控件补可访问名（屏幕阅读器/自动化测试用）。"""
        self.setAccessibleName("墨软文档")
        self._table.setAccessibleName("文档库列表")
        self._table.setAccessibleDescription("双击或按 Ctrl+回车打开选中文档")
        self._search.setAccessibleName("文档库搜索")
        self._search.setPlaceholderText("搜索标题/路径…（Ctrl+L 聚焦）")
        self._doc_tabs.setAccessibleName("已打开的文档标签")
        self._tabs.setAccessibleName("文档功能页签")
        self._task_bar.setAccessibleName("任务状态栏")
        self._pdf_button.setAccessibleName("PDF 工具箱")
        self._ocr_button.setAccessibleName("OCR 文字识别")
        self._viewer.setAccessibleName("文档查看编辑")

    # ---------------------------------------------------------------- 快捷键动作

    def _show_shortcuts(self) -> None:
        """快捷键说明（需求 6.9「易用」：键盘操作可发现）。"""
        from PySide6.QtWidgets import QMessageBox

        labels = {
            "add_files": "添加文件", "add_folder": "添加文件夹",
            "open_selected": "打开选中文档", "save": "保存当前文档",
            "save_as": "另存为", "print": "打印", "close_tab": "关闭当前标签",
            "focus_search": "聚焦搜索", "find": "查找（查看编辑页）", "refresh": "刷新文档库",
            "pdf_tools": "PDF 工具箱", "ocr": "OCR 识别", "remove": "从库中移除",
        }
        lines = ["键盘快捷键（全部为窗口级，焦点在本板块即可用）：", ""]
        for name, shortcut in self._shortcuts.items():
            if name.startswith("panel_"):
                continue
            lines.append(f"  {shortcut.key().toString():<16}{labels.get(name, name)}")
        lines.append("")
        lines.append("  Alt+1 … Alt+6      切换到「查看编辑 / 格式美化 / 填充计算 / 文档合并 / "
                     "AI 循环美化 / 权限安全」")
        lines.append("  ↑ / ↓              在文档库里移动选择")
        dialog = QMessageBox(self)
        dialog.setWindowTitle("墨软文档 · 快捷键")
        dialog.setText("\n".join(lines))
        dialog.exec()

    def _save_current(self) -> None:
        if self._current_ir is None:
            self._toaster.info("先打开一个文档（Ctrl+回车）")
            return
        self._viewer.save()

    def _save_as_current(self) -> None:
        if self._current_ir is None:
            self._toaster.info("先打开一个文档（Ctrl+回车）")
            return
        self._viewer.save_as()

    def _print_current(self) -> None:
        if self._current_ir is None:
            self._toaster.info("先打开一个文档（Ctrl+回车）")
            return
        self._viewer.print_document()

    # ---------------------------------------------------------------- 构建

    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        add_files = QPushButton("添加文件")
        add_files.setToolTip("添加文件（Ctrl+O）")
        add_files.clicked.connect(self._pick_files)
        row.addWidget(add_files)
        add_folder = QPushButton("添加文件夹")
        add_folder.setToolTip("递归收集文件夹里的文档（Ctrl+Shift+O）")
        add_folder.clicked.connect(self._pick_folder)
        row.addWidget(add_folder)
        remove = QPushButton("从库中移除")
        remove.setToolTip("只从文档库移除记录，不删除磁盘文件（Ctrl+Delete）")
        remove.clicked.connect(self._remove_selected)
        row.addWidget(remove)

        open_doc = QPushButton("打开")
        open_doc.setToolTip("解析并打开选中文档（Ctrl+回车）")
        open_doc.clicked.connect(self._open_selected)
        row.addWidget(open_doc)

        row.addWidget(QLabel("输出目录："))
        self._output_dir = QLineEdit(str(app_context.document_output_dir()))
        self._output_dir.editingFinished.connect(self._commit_output_dir)
        row.addWidget(self._output_dir, 1)
        browse = QPushButton("选择…")
        browse.clicked.connect(self._pick_output_dir)
        row.addWidget(browse)
        open_out = QPushButton("打开输出目录")
        open_out.clicked.connect(self._open_output_dir)
        row.addWidget(open_out)

        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索标题/路径…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(lambda _text: self._reload_library())
        row.addWidget(self._search, 1)

        self._pdf_button = QPushButton("PDF 工具")
        self._pdf_button.setToolTip("拆分 / 合并 / 压缩 / 加密 / 水印 / 表单 / 批注 / 转图片（6.2 的 PDF 能力）")
        self._pdf_button.clicked.connect(self._open_pdf_tools)
        row.addWidget(self._pdf_button)

        self._ocr_button = QPushButton("OCR 识别")
        self._ocr_button.setToolTip("对图片或扫描件 PDF 做 OCR，识别结果作为可编辑文本打开"
                                    "（需要本机 tesseract，扫描 PDF 另需 PyMuPDF）")
        self._ocr_button.clicked.connect(self._run_ocr)
        row.addWidget(self._ocr_button)

        board_settings = QPushButton("⚙ 板块设置")
        board_settings.setToolTip("设置 → 文档：输出目录、默认模板、AI 权限与脱敏")
        board_settings.clicked.connect(lambda: SettingsDialog(self, initial="document").exec())
        row.addWidget(board_settings)
        return row

    def _build_left(self) -> QWidget:
        holder = QWidget()
        holder.setObjectName("filePanel")
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)
        head = QHBoxLayout()
        title = QLabel("文档库")
        title.setObjectName("sectionTitle")
        head.addWidget(title)
        head.addStretch(1)
        self._count_label = QLabel("0 篇")
        self._count_label.setObjectName("viewerMetaLabel")
        head.addWidget(self._count_label)
        layout.addLayout(head)

        self._empty = EmptyState(
            "📄", "还没有文档",
            "用「添加文件 / 添加文件夹」加入 Word、PDF、Excel、Markdown、TXT 等，"
            "双击即可查看、编辑、美化、计算或与另一份文档合并",
        )
        layout.addWidget(self._empty)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["名称", "格式", "分类", "大小", "状态"])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.cellDoubleClicked.connect(lambda *_args: self._open_selected())
        self._table.setVisible(False)
        layout.addWidget(self._table, 1)
        return holder

    def _build_right(self) -> QWidget:
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        # 多标签：同时打开多个文档，标签上带未保存标记
        self._doc_tabs = QTabBar()
        self._doc_tabs.setDocumentMode(True)
        self._doc_tabs.setMovable(True)
        self._doc_tabs.setExpanding(False)
        self._doc_tabs.setTabsClosable(True)
        self._doc_tabs.setVisible(False)
        self._doc_tabs.setToolTip("已打开的文档（点标签切换；• 表示有未保存的编辑）")
        self._doc_tabs.currentChanged.connect(self._on_doc_tab_changed)
        self._doc_tabs.tabCloseRequested.connect(self._on_doc_tab_closed)
        outer.addWidget(self._doc_tabs)

        self._tabs = QTabWidget()
        self._viewer = DocViewer(self._library, task_bar=self._task_bar)
        self._beautify = BeautifyPanel(self._library, task_bar=self._task_bar)
        self._sheet = SheetPanel(self._library, task_bar=self._task_bar)
        self._merge = MergePanel(self._library, task_bar=self._task_bar)
        self._loop = LoopPanel(self._library, task_bar=self._task_bar)
        self._security = SecurityPanel(self._library, task_bar=self._task_bar)

        for widget, label in (
            (self._viewer, "查看编辑"),
            (self._beautify, "格式美化"),
            (self._sheet, "填充计算"),
            (self._merge, "文档合并"),
            (self._loop, "AI 循环美化"),
            (self._security, "权限安全"),
        ):
            self._tabs.addTab(widget, label)

        self._viewer.irChanged.connect(self._on_viewer_changed)
        self._viewer.statusMessage.connect(self._on_panel_status)
        self._viewer.dirtyChanged.connect(self._on_dirty)
        self._viewer.saved.connect(lambda path: self._reload_library())
        for panel in (self._beautify, self._sheet, self._merge, self._loop, self._security):
            panel.irChanged.connect(self._apply_ir)
            panel.statusMessage.connect(self._on_panel_status)
        self._security.settingsChanged.connect(self._on_settings_changed)
        outer.addWidget(self._tabs, 1)
        return container

    # ---------------------------------------------------------------- 文档库

    def _reload_library(self) -> None:
        keyword = self._search.text().strip()
        records = self._library.documents(limit=1000, keyword=keyword)
        self._rows = [
            {
                "id": record.id,
                "path": record.path,
                "name": Path(record.path).name or record.title,
                "title": record.title,
                "format": record.format,
                "category": get_spec(record.format).category_label if get_spec(record.format) else "",
                "size": record.size_bytes,
                "status": STATUS_NEW,
            }
            for record in records
        ]
        self._rebuild_table()
        stats = self._library.stats()
        self._count_label.setText(
            f"{stats['documents']} 篇 · 版本 {stats['versions']} 个 · "
            f"{_format_size(stats['size_bytes'])}")

    def _rebuild_table(self) -> None:
        has_rows = bool(self._rows)
        self._empty.setVisible(not has_rows)
        self._table.setVisible(has_rows)
        self._table.blockSignals(True)
        self._table.setRowCount(len(self._rows))
        for index, row in enumerate(self._rows):
            name = QTableWidgetItem(row["name"])
            name.setToolTip(row["path"])
            self._table.setItem(index, 0, name)
            self._table.setItem(index, 1, QTableWidgetItem(
                format_label(row["format"]) if row["format"] else "未知"))
            self._table.setItem(index, 2, QTableWidgetItem(row["category"]))
            self._table.setItem(index, 3, QTableWidgetItem(_format_size(row["size"])))
            status = QTableWidgetItem(row["status"])
            if row["status"] == STATUS_MISSING:
                status.setForeground(Qt.GlobalColor.red)
            self._table.setItem(index, 4, status)
        self._table.blockSignals(False)

    def _selected_row(self) -> Optional[dict]:
        indexes = self._table.selectionModel().selectedRows() if self._table.selectionModel() else []
        if not indexes:
            return None
        row = indexes[0].row()
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def _update_status(self, path: str, status: str) -> None:
        for index, row in enumerate(self._rows):
            if row["path"] == path:
                row["status"] = status
                item = self._table.item(index, 4)
                if item is not None:
                    item.setText(status)
                    if status == STATUS_MISSING:
                        item.setForeground(Qt.GlobalColor.red)
                return

    def _on_selection_changed(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        self._merge.set_ir(DocumentIR(path=row["path"], title=row["title"]))
        opened = (self._current_ir is not None
                  and (self._current_ir.path or self._current_ir.title) == row["path"])
        hint = "已在标签里打开" if opened else "双击或点「打开」解析内容"
        self._task_bar.note(f"已选中：{row['name']}（{hint}）")

    def _open_selected(self) -> None:
        row = self._selected_row()
        if row is None:
            self._toaster.info("先在上面的列表里选一个文档")
            return
        self._open_path(row["path"])

    # ---------------------------------------------------------------- 打开

    def _open_path(self, path: str, *, ocr: bool = False) -> None:
        key = str(path)
        if key in self._open_docs and not ocr:
            index = self._doc_tab_index(key)
            if index >= 0:
                self._stash_current()
                self._doc_tabs.setCurrentIndex(index)
                self._on_doc_tab_changed(index)
            self._update_status(key, STATUS_OPEN)
            W.note(self._task_bar, f"已切换到已打开的标签：《{self._open_docs[key].title}》")
            return
        if self._worker is not None:
            self._toaster.info("有任务正在进行，稍后再试")
            return
        if not Path(path).is_file():
            self._update_status(path, STATUS_MISSING)
            W.note(self._task_bar, f"文件不存在或已被移动：{path}")
            self._toaster.error(f"文件不存在：{path}")
            return
        self._update_status(path, "解析中…")
        worker = W.DocumentWorker(
            lambda worker_: self._library.open_path(path, ocr=ocr), label="解析文档", parent=self)
        worker.message.connect(lambda text: W.note(self._task_bar, text))
        worker.done.connect(self._on_opened)
        worker.failed.connect(lambda message: self._on_open_failed(path, message))
        self._worker = worker
        W.busy(self._task_bar, f"正在解析：{Path(path).name}")
        worker.start()

    def _on_opened(self, result: object) -> None:
        self._worker = None
        if not isinstance(result, DocumentIR):
            W.idle(self._task_bar, "解析结束")
            return
        self._open_document(result)
        self._update_status(result.path, STATUS_OPEN)
        stats = result.stats()
        message = (f"已打开《{result.title}》：{format_label(result.format_key)} · "
                   f"{stats['blocks']} 块 / {stats['headings']} 标题 / {stats['tables']} 表")
        if result.warnings:
            message += "；提示：" + result.warnings[0]
        self._serialize_ocr_hint(result)
        W.idle(self._task_bar, message)
        self._toaster.success(message)

    # ---------------------------------------------------------------- 多标签

    def _open_document(self, ir: DocumentIR) -> None:
        """把解析结果放进（或复用）一个标签页并切过去。"""
        key = ir.path or ir.title
        self._stash_current()
        self._open_docs[key] = ir
        index = self._doc_tab_index(key)
        if index < 0:
            index = self._doc_tabs.addTab(self._doc_tab_label(ir))
            self._doc_tabs.setTabData(index, key)
            self._doc_tabs.setTabToolTip(index, ir.path)
        else:
            self._doc_tabs.setTabText(index, self._doc_tab_label(ir))
        self._doc_tabs.setVisible(self._doc_tabs.count() > 0)
        self._doc_tabs.blockSignals(True)
        self._doc_tabs.setCurrentIndex(index)
        self._doc_tabs.blockSignals(False)
        self._apply_ir(ir)

    @staticmethod
    def _doc_tab_label(ir: DocumentIR) -> str:
        return (ir.title or Path(ir.path).name or "未命名")[:18]

    def _doc_tab_index(self, key: str) -> int:
        for index in range(self._doc_tabs.count()):
            if self._doc_tabs.tabData(index) == key:
                return index
        return -1

    def _stash_current(self) -> None:
        """切标签前把未保存的编辑留在内存里（下次切回来还在）。"""
        if self._current_ir is None:
            return
        stashed = self._viewer.current_ir()
        if stashed is not None:
            key = stashed.path or stashed.title
            self._open_docs[key] = stashed
            self._current_ir = stashed
            index = self._doc_tab_index(key)
            if index >= 0:
                self._doc_tabs.setTabText(index, self._doc_tab_label(stashed))

    def _on_doc_tab_changed(self, index: int) -> None:
        if index < 0:
            return
        key = self._doc_tabs.tabData(index)
        ir = self._open_docs.get(str(key))
        if ir is None:
            return
        current_key = (self._current_ir.path or self._current_ir.title) if self._current_ir else ""
        if str(key) == current_key:
            return
        self._stash_current()
        self._apply_ir(ir)
        W.note(self._task_bar, f"已切换到：{Path(str(key)).name}")

    def _on_doc_tab_closed(self, index: int) -> None:
        key = str(self._doc_tabs.tabData(index) or "")
        self._doc_tabs.removeTab(index)
        self._open_docs.pop(key, None)
        self._doc_tabs.setVisible(self._doc_tabs.count() > 0)
        if self._doc_tabs.count() == 0:
            self._current_ir = None
            self._viewer._bind(DocumentIR())          # noqa: SLF001  清空查看器
            for panel in (self._beautify, self._sheet, self._merge, self._loop,
                          self._security):
                panel.set_ir(None)
            W.note(self._task_bar, "已关闭全部文档标签")
            return
        self._on_doc_tab_changed(self._doc_tabs.currentIndex())

    def _serialize_ocr_hint(self, ir: DocumentIR) -> None:
        if ir.metadata.get("scanned"):
            self._toaster.info("这是扫描件 PDF：切到「权限安全」页可用 OCR（需 tesseract）")

    def _on_open_failed(self, path: str, message: str) -> None:
        self._worker = None
        self._update_status(path, "解析失败")
        self._toaster.error(f"解析失败：{message}")
        W.idle(self._task_bar, f"解析失败：{message}")

    def _apply_ir(self, ir: object) -> None:
        """面板改了文档：同步到查看器、其它面板与标签缓存。"""
        if not isinstance(ir, DocumentIR):
            return
        self._current_ir = ir
        if ir.path or ir.title:
            self._open_docs[ir.path or ir.title] = ir
            if not ir.path:
                # 无路径的新文档（合并结果）：单独占一个标签，否则切走再切回来
                # 会按原标签的 key 取回合并前的内容，看起来像"合并丢了"
                self._ensure_tab(ir)
        self._viewer.apply_ir(ir)
        for panel in (self._beautify, self._sheet, self._merge, self._loop, self._security):
            panel.set_ir(ir)

    def _ensure_tab(self, ir: DocumentIR) -> None:
        """给无路径的新文档补一个标签（标题为 key）。"""
        if not ir.title:
            return
        index = self._doc_tab_index(ir.title)
        if index < 0:
            index = self._doc_tabs.addTab(self._doc_tab_label(ir))
            self._doc_tabs.setTabData(index, ir.title)
        self._doc_tabs.setTabToolTip(index, "尚未保存的新文档（保存时另存为）")
        self._doc_tabs.setVisible(True)
        self._doc_tabs.blockSignals(True)
        self._doc_tabs.setCurrentIndex(index)
        self._doc_tabs.blockSignals(False)

    def _on_viewer_changed(self, ir: object) -> None:
        if not isinstance(ir, DocumentIR):
            return
        self._current_ir = ir
        if ir.path or ir.title:
            self._open_docs[ir.path or ir.title] = ir
        for panel in (self._beautify, self._sheet, self._merge, self._loop, self._security):
            panel.set_ir(ir)

    def _on_panel_status(self, message: str) -> None:
        W.note(self._task_bar, message)

    def _on_dirty(self, dirty: bool) -> None:
        if self._current_ir is None:
            return
        key = self._current_ir.path or self._current_ir.title
        index = self._doc_tab_index(key)
        if index >= 0:
            label = self._doc_tab_label(self._current_ir)
            self._doc_tabs.setTabText(index, f"• {label}" if dirty else label)
        if dirty:
            self._task_bar.note("有未保存的编辑（点「保存」写回文件；已开启自动保存时到点会自动写回）")

    def _on_settings_changed(self) -> None:
        self._library = app_context.refresh_library()
        self._viewer._library = self._library          # noqa: SLF001  权限改动后同步库引用
        self._toaster.info("设置已更新：权限与输出目录立即生效")

    # ---------------------------------------------------------------- 文件操作

    def _pick_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择文档", "", document_formats.extensions_for_dialog())
        self._add_paths(paths)

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹（递归收集支持的文档）")
        if not folder:
            return
        found = [str(path) for path in Path(folder).rglob("*")
                 if path.is_file() and format_key_for_path(path)]
        if not found:
            self._toaster.info("这个文件夹里没有可识别的文档")
            return
        self._add_paths(found)

    def _add_paths(self, paths: list[str]) -> None:
        added = 0
        for raw in paths:
            path = Path(raw)
            spec = get_spec(format_key_for_path(path))
            if spec is None or not path.is_file():
                continue
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            self._library.storage.upsert_document(
                str(path), title=path.stem, format_key=spec.key, category=spec.category,
                size_bytes=size)
            added += 1
        if added:
            self._reload_library()
            self._toaster.success(f"已加入 {added} 个文档")
            W.note(self._task_bar, f"文档库新增 {added} 篇")
        else:
            self._toaster.info("没有新增文档（可能格式不支持或已在库中）")

    def _remove_selected(self) -> None:
        row = self._selected_row()
        if row is None:
            self._toaster.info("先选一个文档")
            return
        self._library.remove_document(int(row["id"]))
        self._reload_library()
        self._toaster.success(f"已从文档库移除：{row['name']}（磁盘文件未删除）")

    def _commit_output_dir(self) -> None:
        text = self._output_dir.text().strip()
        self._settings.setValue("document/output_dir", text)
        self._settings.sync()
        self._library.output_dir = text
        W.note(self._task_bar, f"输出目录：{text or '（默认）'}")

    def _pick_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录", self._output_dir.text())
        if folder:
            self._output_dir.setText(folder)
            self._commit_output_dir()

    def _open_output_dir(self) -> None:
        import subprocess

        folder = Path(self._output_dir.text().strip() or str(app_context.document_output_dir()))
        if folder.is_dir():
            subprocess.Popen(["explorer", str(folder)])
        else:
            self._toaster.info(f"输出目录还不存在：{folder}")

    def _pdf_source_candidate(self) -> str:
        """PDF 工具箱的默认源文件：当前打开的 PDF，否则选中的行，否则空。"""
        current = self._current_ir
        if current is not None and current.path and current.format_key == "pdf":
            return current.path
        row = self._selected_row()
        if row is not None and row.get("format") == "pdf":
            return str(row.get("path") or "")
        return ""

    def _open_pdf_tools(self) -> None:
        from .pdf_dialog import PdfToolsDialog

        dialog = PdfToolsDialog(self._library, source=self._pdf_source_candidate(), parent=self)
        dialog.exec()
        self._reload_library()

    # ---------------------------------------------------------------- OCR

    def _ocr_candidate(self) -> str:
        """OCR 的目标文件：当前文档（图片/PDF），否则选中的行。"""
        current = self._current_ir
        if current is not None and current.format_key in ("pdf", "png", "jpg", "bmp", "tiff",
                                                          "webp", "gif"):
            source = current.path or str(current.metadata.get("source") or "")
            if source:
                return source
        row = self._selected_row()
        if row is not None and row.get("format") in ("pdf", "png", "jpg", "bmp", "tiff",
                                                     "webp", "gif"):
            return str(row.get("path") or "")
        return ""

    def _run_ocr(self) -> None:
        """OCR：把图片/扫描件识别成可编辑文本（不覆盖原文件，需另存为）。"""
        source = self._ocr_candidate()
        if not source:
            self._toaster.info("先选中一张图片或一份扫描 PDF（或打开它）再做 OCR")
            return
        if self._worker is not None:
            self._toaster.info("有任务正在进行，稍后再试")
            return
        from modu_workbench.core.document.ocr import available as ocr_available

        if not ocr_available():
            message = ("OCR 不可用：请安装 tesseract（或用 MODU_TESSERACT 指定路径）；"
                       "扫描 PDF 还需要 PyMuPDF")
            self._toaster.error(message)
            W.note(self._task_bar, message)
            return
        worker = W.DocumentWorker(
            lambda worker_: self._library.ocr_document(source), label="OCR 识别", parent=self)
        worker.message.connect(lambda text: W.note(self._task_bar, text))
        worker.done.connect(self._on_ocr_done)
        worker.failed.connect(self._on_ocr_failed)
        self._worker = worker
        W.busy(self._task_bar, f"正在 OCR：{Path(source).name}")
        worker.start()

    def _on_ocr_done(self, result: object) -> None:
        self._worker = None
        if not isinstance(result, DocumentIR):
            W.idle(self._task_bar, "OCR 结束")
            return
        self._open_document(result)
        message = (f"OCR 完成：识别出 {len(result.blocks)} 段文本（未保存，"
                   f"请校对后「另存为」TXT/DOCX）")
        self._toaster.success(message)
        W.idle(self._task_bar, message)

    def _on_ocr_failed(self, message: str) -> None:
        self._worker = None
        self._toaster.error(f"OCR 失败：{message}")
        W.idle(self._task_bar, f"OCR 失败：{message}")

    # ---------------------------------------------------------------- 生命周期

    def _cancel_worker(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()
            self._toaster.info("正在停止…")

    def on_shown(self) -> None:
        """进入板块时刷新（其它板块/设置页可能改了库）。"""
        self._reload_library()
        self._security.refresh_audit()

    def shutdown(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()
            self._worker.wait(3000)
        self._loop.shutdown()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.shutdown()
        super().closeEvent(event)


__all__ = ["DocumentBoardPage"]
