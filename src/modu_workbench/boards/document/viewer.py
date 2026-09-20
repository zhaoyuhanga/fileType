"""墨软文档：查看与编辑面板。

按格式能力矩阵选择编辑方式（`formats.RENDER_*`）：

| 渲染方式 | 控件 | 可编辑格式 |
|---|---|---|
| `rich` | QTextBrowser 预览（+ 源码/段落编辑） | md / html / docx / rtf |
| `text` | QPlainTextEdit 源码编辑 | txt / log / sql / 代码 |
| `data` | QPlainTextEdit + 结构化校验 | json / xml / yaml / ini |
| `sheet` | QTableWidget 单元格编辑 | xlsx / csv / tsv |
| `slides` | 逐页文本预览（+ 文本层编辑） | pptx |
| `image` | 图片预览 + OCR | png / jpg / … |

只读格式（pdf / epub / doc / xls / ods / ppt / odp / wps / et / dps）不会给出编辑框 ——
能力矩阵里 `edit=False` 就不假装能改，而是明确提示"另存为 DOCX/XLSX 后再编辑"。

界面上还有（对齐需求 6.2 的"查看编辑基础能力"）：
- **分屏 + 实时预览**：编辑框与预览并排，输入后约 0.25 秒自动刷新预览（Markdown 实时预览）；
- **大纲 / 目录**与**书签**：书签存在块的 `meta` 里，随快照一起保存；
- **查找替换**（可正则）、**文档体检**、**版本历史**（回滚）；
- **自动保存**：有未保存编辑时按设置间隔写回原文件（可在设置 → 文档里关闭）。

写回策略（保证"改了就真的改了"，且不破坏表格与标题）：
- 源码模式：整篇重新解析（md/html/txt/结构化数据）；
- 段落模式：编辑框里一行对应一个标题或正文块，按顺序写回；
- 表格模式：按单元格写回对应表。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.document import (
    BLOCK_CODE,
    Block,
    DocumentIR,
    DocumentLibrary,
    ParseError,
    analyze_document,
    format_label,
    get_spec,
    heuristic_score,
    parse_html,
    parse_markdown,
    parse_plain_text,
    render_html,
)
from modu_workbench.ui_kit.components import TaskBar
from modu_workbench.ui_kit.settings import app_settings
from modu_workbench.ui_kit.toast import Toaster

from . import widgets as W

#: 文档区（编辑框 + 预览，可单独显示或分屏）与其它内容的分栈索引
STACK_DOCUMENT = 0
STACK_SHEET = 1
STACK_IMAGE = 2
STACK_MESSAGE = 3

MODE_PREVIEW = "preview"
MODE_SOURCE = "source"
MODE_PARAGRAPH = "paragraph"
MODE_SHEET = "sheet"
MODE_IMAGE = "image"

#: 源码编辑的格式（其余可编辑格式走"段落模式"）
SOURCE_FORMATS = ("txt", "markdown", "html", "json", "xml", "yaml", "ini", "log", "sql", "code")
#: 段落模式（按行对应块）的格式
PARAGRAPH_FORMATS = ("docx", "rtf", "pptx")
#: 支持实时预览的格式（源码 → HTML 渲染）
LIVE_PREVIEW_FORMATS = ("markdown", "html", "txt", "docx", "rtf")

#: 预览保护（需求 6.9 性能）：块数超过这个值只渲染前 N 块。
#: 大文档整篇渲染成 HTML 会让界面卡住（万级块时 QTextDocument 布局要好几秒），
#: 而用户此刻真正需要的只是"看个大概"。保存/导出/合并等操作**不受影响**，仍是全文。
PREVIEW_BLOCK_LIMIT = 800
#: 纯文本长度上限（约 1MB 文本），超过也走保护
PREVIEW_CHAR_LIMIT = 400_000

_EDIT_HINT = {
    MODE_SOURCE: "源码编辑：保存时整篇重新解析，标题/表格会按语法重新识别；可开「分屏」边改边看。",
    MODE_PARAGRAPH: "段落编辑：一行 = 一个标题或正文块，保存时按顺序写回（表格不受影响）。",
    MODE_SHEET: "表格编辑：直接改单元格，保存时写回对应工作表。",
    MODE_PREVIEW: "只读预览：该格式在能力矩阵里不支持编辑，请先另存为可编辑格式。",
    MODE_IMAGE: "图片预览：可用「权限安全 → OCR」把文字提取成可编辑内容。",
}


class DocViewer(QWidget):
    """文档查看 / 编辑面板（板块右侧「查看编辑」页）。"""

    irChanged = Signal(object)
    dirtyChanged = Signal(bool)
    statusMessage = Signal(str)
    saved = Signal(str)

    def __init__(self, library: DocumentLibrary, *, task_bar: Optional[TaskBar] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._task = task_bar
        self._library = library
        self._toaster = Toaster(self)
        self._settings = app_settings()
        self._ir: Optional[DocumentIR] = None
        self._mode = MODE_PREVIEW
        self._dirty = False
        self._sheet_index = 0
        self._live_timer = QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.setInterval(250)
        self._live_timer.timeout.connect(self._render_live_preview)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self._autosave_tick)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addLayout(self._build_toolbar())
        layout.addLayout(self._build_find_bar())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_side())
        splitter.addWidget(self._build_content())
        splitter.setSizes([230, 760])
        layout.addWidget(splitter, 1)

        self._hint = QLabel(_EDIT_HINT[MODE_PREVIEW])
        self._hint.setObjectName("readerStatus")
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)
        self._start_autosave_timer()

    # ---------------------------------------------------------------- 构建

    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        self._title_label = QLabel("未打开文档")
        self._title_label.setObjectName("sectionTitle")
        row.addWidget(self._title_label)
        self._format_label = QLabel("")
        self._format_label.setObjectName("viewerMetaLabel")
        row.addWidget(self._format_label)
        row.addStretch(1)

        self._edit_button = QPushButton("编辑")
        self._edit_button.setCheckable(True)
        self._edit_button.setToolTip("在「预览」与「编辑」之间切换（只读格式不提供编辑）")
        self._edit_button.clicked.connect(self._toggle_edit)
        row.addWidget(self._edit_button)

        self._split_button = QPushButton("分屏")
        self._split_button.setCheckable(True)
        self._split_button.setToolTip("编辑框与预览并排，输入后自动刷新预览（Markdown 实时预览）")
        self._split_button.clicked.connect(self._on_split_toggled)
        row.addWidget(self._split_button)

        self._save_button = QPushButton("保存")
        self._save_button.setObjectName("primaryButton")
        self._save_button.setEnabled(False)
        self._save_button.clicked.connect(self.save)
        row.addWidget(self._save_button)

        save_as = QPushButton("另存为")
        save_as.clicked.connect(self.save_as)
        row.addWidget(save_as)

        print_button = QPushButton("打印")
        print_button.setToolTip("打印当前文档（用系统打印对话框，可先预览）")
        print_button.clicked.connect(self.print_document)
        row.addWidget(print_button)

        health = QPushButton("文档体检")
        health.setToolTip("结构、语言、表格问题与五维质量评分")
        health.clicked.connect(self._show_health)
        row.addWidget(health)

        versions = QPushButton("版本历史")
        versions.setToolTip("打开、保存、美化、合并、循环美化都会留快照，可回滚")
        versions.clicked.connect(self._show_versions)
        row.addWidget(versions)
        return row

    def _build_find_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        self._find_input = QLineEdit()
        self._find_input.setPlaceholderText("查找…")
        self._find_input.returnPressed.connect(self._find_next)
        row.addWidget(self._find_input, 1)
        self._replace_input = QLineEdit()
        self._replace_input.setPlaceholderText("替换为…")
        row.addWidget(self._replace_input, 1)
        self._regex_box = QCheckBox("正则")
        row.addWidget(self._regex_box)
        find = QPushButton("查找")
        find.clicked.connect(self._find_next)
        row.addWidget(find)
        replace = QPushButton("替换")
        replace.clicked.connect(lambda: self._replace(False))
        row.addWidget(replace)
        replace_all = QPushButton("全部替换")
        replace_all.clicked.connect(lambda: self._replace(True))
        row.addWidget(replace_all)
        self._find_status = QLabel("")
        self._find_status.setObjectName("viewerMetaLabel")
        row.addWidget(self._find_status)
        return row

    def _build_side(self) -> QWidget:
        holder = QWidget()
        holder.setObjectName("filePanel")
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        title = QLabel("大纲 / 目录")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        self._outline = QTreeWidget()
        self._outline.setHeaderHidden(True)
        self._outline.itemClicked.connect(self._jump_to_heading)
        layout.addWidget(self._outline, 3)

        bookmark_row = QHBoxLayout()
        bookmark_title = QLabel("书签")
        bookmark_title.setObjectName("sectionTitle")
        bookmark_row.addWidget(bookmark_title)
        bookmark_row.addStretch(1)
        add_bookmark = QPushButton("加/取消")
        add_bookmark.setToolTip("给选中大纲项（或光标所在块）加书签；书签随版本快照保存")
        add_bookmark.clicked.connect(self._toggle_bookmark)
        bookmark_row.addWidget(add_bookmark)
        clear_bookmarks = QPushButton("清空")
        clear_bookmarks.clicked.connect(self._clear_bookmarks)
        bookmark_row.addWidget(clear_bookmarks)
        layout.addLayout(bookmark_row)
        self._bookmarks = QListWidget()
        self._bookmarks.setMaximumHeight(140)
        self._bookmarks.itemClicked.connect(self._jump_to_bookmark)
        layout.addWidget(self._bookmarks, 1)

        comment_row = QHBoxLayout()
        comment_title = QLabel("批注")
        comment_title.setObjectName("sectionTitle")
        comment_row.addWidget(comment_title)
        comment_row.addStretch(1)
        add_comment = QPushButton("加/改")
        add_comment.setToolTip("给选中大纲项（或光标所在块）加批注；"
                               "导出 Word 时是原生批注，其它格式汇总到附录")
        add_comment.clicked.connect(self._add_comment)
        comment_row.addWidget(add_comment)
        remove_comment = QPushButton("删除")
        remove_comment.clicked.connect(self._remove_comment)
        comment_row.addWidget(remove_comment)
        layout.addLayout(comment_row)
        self._comment_list = QListWidget()
        self._comment_list.setMaximumHeight(150)
        self._comment_list.itemClicked.connect(self._jump_to_comment)
        layout.addWidget(self._comment_list, 1)

        self._meta_label = QLabel("")
        self._meta_label.setObjectName("viewerMetaLabel")
        self._meta_label.setWordWrap(True)
        layout.addWidget(self._meta_label)
        return holder

    def _build_content(self) -> QWidget:
        self._stack = QStackedWidget()

        # 文档区：编辑框 + 预览放在同一个 splitter 里，靠显隐实现"仅预览/仅编辑/分屏"
        self._document_split = QSplitter(Qt.Orientation.Horizontal)
        self._editor = QPlainTextEdit()
        self._editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._editor.textChanged.connect(self._on_text_changed)
        self._preview = QTextBrowser()
        self._preview.setOpenExternalLinks(False)
        self._document_split.addWidget(self._editor)
        self._document_split.addWidget(self._preview)
        self._document_split.setSizes([420, 420])
        self._stack.addWidget(self._document_split)

        self._sheet = QTableWidget(0, 0)
        self._sheet.setAlternatingRowColors(True)
        self._sheet.itemChanged.connect(self._on_sheet_changed)
        self._stack.addWidget(self._sheet)

        self._image_label = QLabel("")
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image_scroll = QScrollArea()
        image_scroll.setWidgetResizable(True)
        image_scroll.setWidget(self._image_label)
        self._stack.addWidget(image_scroll)

        self._message = QLabel("")
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setWordWrap(True)
        self._message.setObjectName("readerStatus")
        self._stack.addWidget(self._message)
        return self._stack

    # ---------------------------------------------------------------- 载入

    def load(self, ir: DocumentIR) -> None:
        """载入一份 IR 并切换到合适的视图。"""
        self._bind(ir)
        self.dirtyChanged.emit(False)

    def _bind(self, ir: DocumentIR) -> None:
        """绑定文档并按能力矩阵选择编辑方式（打开与外部改写都走这里）。"""
        self._ir = ir
        self._dirty = False
        self._sheet_index = 0
        spec = get_spec(ir.format_key)
        self._mode = self._mode_for(ir.format_key, editable=bool(spec and spec.edit))
        self._title_label.setText(ir.title or Path(ir.path).name or "未命名文档")
        self._format_label.setText(
            f"{format_label(ir.format_key)} · {spec.capability_text() if spec else '—'}")
        self._edit_button.setChecked(self._mode in (MODE_SOURCE, MODE_PARAGRAPH, MODE_SHEET))
        self._edit_button.setEnabled(self._mode != MODE_PREVIEW)
        self._split_button.setEnabled(self._mode in (MODE_SOURCE, MODE_PARAGRAPH)
                                      and ir.format_key in LIVE_PREVIEW_FORMATS)
        self._split_button.setChecked(self._split_button.isEnabled()
                                      and bool(self._settings.value("document/split_view", False,
                                                                    type=bool)))
        self._hint.setText(_EDIT_HINT.get(self._mode, ""))
        self._refresh()
        self._render_outline()
        self._render_bookmarks()
        self._render_comments()
        self._render_meta()

    @staticmethod
    def _mode_for(format_key: str, *, editable: bool) -> str:
        spec = get_spec(format_key)
        if spec is None:
            return MODE_PREVIEW
        if spec.render == "image":
            return MODE_IMAGE
        if spec.render == "sheet":
            return MODE_SHEET if editable else MODE_PREVIEW
        if not editable:
            return MODE_PREVIEW
        if format_key in SOURCE_FORMATS:
            return MODE_SOURCE
        if format_key in PARAGRAPH_FORMATS:
            return MODE_PARAGRAPH
        return MODE_PREVIEW

    # ---------------------------------------------------------------- 渲染

    def _refresh(self) -> None:
        if self._ir is None:
            self._stack.setCurrentIndex(STACK_MESSAGE)
            self._message.setText("左侧选择一个文档开始查看（或点「添加文件」）")
            return
        editing = self._edit_button.isChecked()
        if self._mode == MODE_IMAGE:
            self._render_image()
        elif self._mode == MODE_SHEET:
            self._render_sheet()
            self._stack.setCurrentIndex(STACK_SHEET)
        else:
            show_editor = editing and self._mode in (MODE_SOURCE, MODE_PARAGRAPH)
            show_preview = (not show_editor) or (self._split_button.isChecked()
                                                 and self._split_button.isEnabled())
            if show_editor:
                self._editor.blockSignals(True)
                self._editor.setPlainText(self._editor_text())
                self._editor.blockSignals(False)
            self._editor.setVisible(show_editor)
            self._preview.setVisible(show_preview)
            if show_preview:
                self._preview.setHtml(self._preview_html(self._ir))
            self._stack.setCurrentIndex(STACK_DOCUMENT)
        self._save_button.setEnabled(editing)

    # ---------------------------------------------------------------- 预览保护

    def _preview_html(self, ir: DocumentIR) -> str:
        """渲染预览；大文档只渲染前若干块并给出提示（性能保护）。"""
        stats = ir.stats()
        too_many_blocks = len(ir.blocks) > PREVIEW_BLOCK_LIMIT
        if not too_many_blocks and stats["chars"] <= PREVIEW_CHAR_LIMIT:
            return render_html(ir, standalone=False)
        capped = ir.clone()
        capped.blocks = capped.blocks[:PREVIEW_BLOCK_LIMIT]
        banner = (
            f"<div class='watermark'>文档较大（{len(ir.blocks)} 块 / 约 {stats['chars']} 字）："
            f"预览只渲染前 {PREVIEW_BLOCK_LIMIT} 块以保证流畅。"
            "保存、导出、美化、合并与循环美化仍按**全文**处理；"
            "要看后面的内容可用「编辑」定位或选中左侧大纲项跳转。</div>")
        return banner + render_html(capped, standalone=False)

    def is_preview_capped(self, ir: Optional[DocumentIR] = None) -> bool:
        """当前预览是否被截断（界面与测试共用）。"""
        target = ir or self._ir
        if target is None:
            return False
        return (len(target.blocks) > PREVIEW_BLOCK_LIMIT
                or target.stats()["chars"] > PREVIEW_CHAR_LIMIT)

    def _render_image(self) -> None:
        path = str(self._ir.metadata.get("src") or self._ir.path) if self._ir else ""
        pixmap = QPixmap(path) if path else QPixmap()
        if pixmap.isNull():
            self._message.setText(f"无法预览图片：{path}")
            self._stack.setCurrentIndex(STACK_MESSAGE)
            return
        self._image_label.setPixmap(pixmap)
        self._stack.setCurrentIndex(STACK_IMAGE)

    def _render_sheet(self) -> None:
        tables = self._ir.tables() if self._ir else []
        if not tables:
            self._message.setText("这份文档没有表格（可在「填充计算」页为 CSV/Excel 做计算）")
            self._stack.setCurrentIndex(STACK_MESSAGE)
            return
        table = tables[min(self._sheet_index, len(tables) - 1)]
        rows = table.normalized()
        self._sheet.blockSignals(True)
        self._sheet.setRowCount(len(rows))
        self._sheet.setColumnCount(max(1, table.width))
        self._sheet.setHorizontalHeaderLabels(
            [f"列{index + 1}" for index in range(max(1, table.width))])
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                self._sheet.setItem(row_index, column_index, QTableWidgetItem(value))
        self._sheet.blockSignals(False)
        self._sheet.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._sheet.resizeColumnsToContents()

    def _render_outline(self) -> None:
        self._outline.clear()
        if self._ir is None:
            return
        for level, text, index in self._ir.outline():
            item = QTreeWidgetItem(["　" * (level - 1) + text])
            item.setData(0, Qt.ItemDataRole.UserRole, index)
            self._outline.addTopLevelItem(item)

    def _render_bookmarks(self) -> None:
        self._bookmarks.clear()
        if self._ir is None:
            return
        for index, block in enumerate(self._ir.blocks):
            if block.meta.get("bookmark"):
                preview = (block.text or "").strip().replace("\n", " ")[:26] or "（空块）"
                item = QListWidgetItem(f"🔖 {preview}")
                item.setData(Qt.ItemDataRole.UserRole, index)
                self._bookmarks.addItem(item)

    def _render_comments(self) -> None:
        self._comment_list.clear()
        if self._ir is None:
            return
        for index, block in enumerate(self._ir.blocks):
            if not block.note:
                continue
            preview = (block.text or "").strip().replace("\n", " ")[:18] or "（空块）"
            author = str(block.meta.get("comment_author") or "local")
            item = QListWidgetItem(f"💬 {preview}｜{block.note[:24]}（{author}）")
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setToolTip(f"锚点：{preview}\n批注：{block.note}\n批注人：{author}")
            self._comment_list.addItem(item)

    def _render_meta(self) -> None:
        if self._ir is None:
            self._meta_label.setText("")
            return
        stats = self._ir.stats()
        analysis = analyze_document(self._ir)
        score = heuristic_score(self._ir)
        self._meta_label.setText(
            f"块 {stats['blocks']} · 标题 {stats['headings']} · 表格 {stats['tables']} · "
            f"{stats['chars']} 字\n质量 {score.overall * 100:.0f} · 问题 {len(analysis['issues'])} 项"
            + (f"\n警告：{self._ir.warnings[0]}" if self._ir.warnings else ""))

    # ---------------------------------------------------------------- 编辑

    def _editor_text(self) -> str:
        if self._ir is None:
            return ""
        if self._mode == MODE_SOURCE:
            return self._source_text()
        return self._paragraph_text()

    def _source_text(self) -> str:
        assert self._ir is not None
        if self._ir.format_key == "markdown":
            return self._ir.markdown()
        if self._ir.format_key == "html":
            return render_html(self._ir, standalone=True)
        if self._ir.format_key == "json":
            from modu_workbench.core.document.parser import format_structured

            try:
                return format_structured(self._ir.text(), "json")[0]
            except ParseError:
                return self._ir.text()
        return self._ir.text(include_tables=True)

    def _paragraph_text(self) -> str:
        assert self._ir is not None
        lines: list[str] = []
        for block in self._ir.blocks:
            if block.is_table:
                continue
            if block.kind == "slide":
                lines.append(f"[第 {block.meta.get('slide', '')} 页]")
                lines.extend(block.text.splitlines() or [""])
                if block.meta.get("notes"):
                    lines.append(f"备注：{block.meta['notes']}")
                continue
            if block.is_textual:
                lines.append(block.text)
        return "\n".join(lines)

    def _apply_editor(self) -> bool:
        """把编辑框内容写回 IR；返回是否有变化。"""
        if self._ir is None or not self._edit_button.isChecked():
            return False
        if self._mode == MODE_SHEET:
            return self._apply_sheet()
        text = self._editor.toPlainText()
        if self._mode == MODE_SOURCE:
            return self._apply_source(text)
        return self._apply_paragraphs(text)

    def _apply_source(self, text: str) -> bool:
        assert self._ir is not None
        key = self._ir.format_key
        try:
            if key == "markdown":
                parsed = parse_markdown(text, title=self._ir.title)
            elif key == "html":
                parsed = parse_html(text, title=self._ir.title)
            elif key in ("json", "xml", "yaml", "ini"):
                from modu_workbench.core.document.parser import format_structured

                pretty, _note = format_structured(text, key)
                original = [block.clone() for block in self._ir.blocks]
                if original:
                    original[0].text = pretty
                if original == self._ir.blocks:
                    return False
                self._ir.blocks = original
                return True
            else:
                parsed = parse_plain_text(text, title=self._ir.title)
        except ParseError as error:
            self._toaster.error(f"内容有语法错误：{error}")
            W.note(self._task, f"解析失败：{error}")
            return False
        if parsed.blocks == self._ir.blocks:
            return False
        self._ir.blocks = parsed.blocks
        return True

    def _apply_paragraphs(self, text: str) -> bool:
        assert self._ir is not None
        lines = [line.rstrip() for line in text.split("\n")]
        if self._ir.format_key == "pptx":
            return self._apply_slides(lines)
        targets = [block for block in self._ir.blocks
                   if block.is_textual and block.kind != "slide"]
        content = [line for line in lines if line.strip()]
        if len(content) != len(targets):
            tables = [block for block in self._ir.blocks if block.is_table]
            self._ir.blocks = parse_plain_text("\n\n".join(content)).blocks + tables
            W.note(self._task, f"段落数与原文不一致（{len(content)} vs {len(targets)}）："
                               "已按新文本重排正文，表格保持不变")
            return True
        changed = False
        for block, line in zip(targets, content):
            if line != block.text:
                block.text = line
                changed = True
        return changed

    def _apply_slides(self, lines: list[str]) -> bool:
        assert self._ir is not None
        slides = [block for block in self._ir.blocks if block.kind == "slide"]
        current: Optional[dict] = None
        grouped: list[dict] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[第") and stripped.endswith("页]"):
                current = {"text": [], "notes": ""}
                grouped.append(current)
                continue
            if current is None:
                continue
            if stripped.startswith("备注："):
                current["notes"] = stripped[3:]
                continue
            if stripped:
                current["text"].append(stripped)
        if len(grouped) != len(slides):
            self._toast_info("幻灯片页数与原文不一致，已按最新内容写回能对上的页")
        changed = False
        for block, group in zip(slides, grouped):
            text = "\n".join(group["text"])
            if text != block.text:
                block.text = text
                changed = True
            if group["notes"] and group["notes"] != block.meta.get("notes"):
                block.meta["notes"] = group["notes"]
                changed = True
        return changed

    def _apply_sheet(self) -> bool:
        assert self._ir is not None
        tables = self._ir.tables()
        if not tables:
            return False
        table = tables[min(self._sheet_index, len(tables) - 1)]
        rows: list[list[str]] = []
        for row_index in range(self._sheet.rowCount()):
            row: list[str] = []
            for column_index in range(self._sheet.columnCount()):
                item = self._sheet.item(row_index, column_index)
                row.append(item.text() if item is not None else "")
            rows.append(row)
        if rows == table.rows:
            return False
        table.rows = rows
        return True

    def _on_text_changed(self) -> None:
        if not self._edit_button.isChecked():
            return
        self._set_dirty(True)
        if self._split_button.isChecked() and self._split_button.isEnabled():
            self._live_timer.start()

    def _on_sheet_changed(self, *_args: object) -> None:
        if self._edit_button.isChecked():
            self._set_dirty(True)

    def _set_dirty(self, value: bool) -> None:
        if self._dirty == value:
            return
        self._dirty = value
        self.dirtyChanged.emit(value)

    def _on_split_toggled(self) -> None:
        self._settings.setValue("document/split_view", self._split_button.isChecked())
        self._refresh()

    def _toggle_edit(self) -> None:
        if self._ir is None:
            self._edit_button.setChecked(False)
            return
        if self._mode == MODE_PREVIEW:
            self._edit_button.setChecked(False)
            self._toaster.info("该格式为只读：请先「另存为」DOCX / XLSX / Markdown 后再编辑")
            return
        if not self._edit_button.isChecked() and self._dirty:
            self._apply_editor()
        self._refresh()

    def _render_live_preview(self) -> None:
        """实时预览：把编辑框里的文本重新解析后渲染（不写回 IR，也不动磁盘）。"""
        if self._ir is None or not self._edit_button.isChecked():
            return
        if self._mode not in (MODE_SOURCE, MODE_PARAGRAPH):
            return
        text = self._editor.toPlainText()
        try:
            parsed = self._preview_ir(text)
        except ParseError as error:
            self._preview.setHtml(
                f"<p style='color:#b3261e'>预览失败（内容有语法错误）：{error}</p>")
            return
        self._preview.setHtml(self._preview_html(parsed))

    def _preview_ir(self, text: str) -> DocumentIR:
        assert self._ir is not None
        if self._mode == MODE_PARAGRAPH:
            preview = self._ir.clone()
            lines = [line for line in text.split("\n") if line.strip()]
            targets = [block for block in preview.blocks if block.is_textual]
            for block, line in zip(targets, lines):
                block.text = line
            return preview
        key = self._ir.format_key
        if key == "markdown":
            return parse_markdown(text, title=self._ir.title)
        if key == "html":
            return parse_html(text, title=self._ir.title)
        if key in ("json", "xml", "yaml", "ini"):
            return DocumentIR(blocks=[Block(kind=BLOCK_CODE, text=text)], title=self._ir.title,
                              format_key=key)
        return parse_plain_text(text, title=self._ir.title)

    def current_ir(self) -> Optional[DocumentIR]:
        """取当前内容（含未保存的编辑）。"""
        self._apply_editor()
        self._set_dirty(False)
        return self._ir

    def apply_ir(self, ir: DocumentIR) -> None:
        """外部（美化/合并/循环/回滚）替换内容后刷新视图。"""
        self._bind(ir)
        self.irChanged.emit(ir)

    def is_dirty(self) -> bool:
        return self._dirty

    # ---------------------------------------------------------------- 书签

    def _bookmark_target(self) -> int:
        """书签目标块：优先取大纲选中项，其次按光标行匹配正文块。"""
        if self._ir is None:
            return -1
        selected = self._outline.selectedItems()
        if selected:
            index = selected[0].data(0, Qt.ItemDataRole.UserRole)
            if index is not None:
                return int(index)
        if self._edit_button.isChecked() and self._mode in (MODE_SOURCE, MODE_PARAGRAPH):
            line = self._editor.textCursor().block().text().strip()
            if line:
                needle = line.lstrip("#-> ").strip()
                for index, block in enumerate(self._ir.blocks):
                    if block.is_textual and needle and needle in block.text:
                        return index
        return -1

    def _toggle_bookmark(self) -> None:
        if self._ir is None:
            return
        index = self._bookmark_target()
        if index < 0:
            self._toaster.info("先在大纲里选中一项，或把光标放在正文段落上再点书签")
            return
        block = self._ir.blocks[index]
        block.meta["bookmark"] = not bool(block.meta.get("bookmark"))
        state = "已加书签" if block.meta["bookmark"] else "已取消书签"
        self._render_bookmarks()
        self._set_dirty(True)
        self.statusMessage.emit(f"{state}：{(block.text or '').strip()[:24]}")
        W.note(self._task, f"{state}：{(block.text or '').strip()[:30]}")

    def _clear_bookmarks(self) -> None:
        if self._ir is None:
            return
        removed = 0
        for block in self._ir.blocks:
            if block.meta.pop("bookmark", None):
                removed += 1
        self._render_bookmarks()
        if removed:
            self._set_dirty(True)
            self._toaster.success(f"已清空 {removed} 个书签")
        else:
            self._toaster.info("当前没有书签")

    def _jump_to_bookmark(self, item: QListWidgetItem) -> None:
        index = item.data(Qt.ItemDataRole.UserRole)
        if index is not None:
            self._jump_to_index(int(index))

    def _jump_to_index(self, index: int) -> None:
        if self._ir is None or not (0 <= index < len(self._ir.blocks)):
            return
        text = self._ir.blocks[index].text.strip()
        if self._editor.isVisible() and text:
            self._editor.setFocus()
            self._editor.find(text)
        elif self._preview.isVisible():
            self._preview.find(text)
        W.note(self._task, f"定位到：{text[:40]}")

    # ---------------------------------------------------------------- 批注

    def _add_comment(self) -> None:
        if self._ir is None:
            return
        index = self._bookmark_target()
        if index < 0:
            self._toaster.info("先在大纲里选中一项，或把光标放在正文段落上再加批注")
            return
        block = self._ir.blocks[index]
        existing = block.note or ""
        text = W.ask_text(self, "批注", f"批注内容（锚点：{(block.text or '').strip()[:20]}）",
                          existing)
        if not text:
            return
        author = str(block.meta.get("comment_author") or "") or W.ask_text(
            self, "批注人", "批注人（可留空）", "local") or "local"
        self._library.add_comment(self._ir, index, text, author=author)
        self._render_comments()
        self._set_dirty(True)
        message = f"已加批注（块 {index + 1}）：{text[:24]}"
        self.statusMessage.emit(message)
        W.note(self._task, message)
        self._library.snapshot(self._ir, label="批注", kind="comment", note=text[:60])

    def _remove_comment(self) -> None:
        if self._ir is None:
            return
        item = self._comment_list.currentItem() or (
            self._comment_list.item(0) if self._comment_list.count() else None)
        if item is None:
            self._toaster.info("当前文档没有批注")
            return
        index = int(item.data(Qt.ItemDataRole.UserRole))
        if self._library.remove_comment(self._ir, index):
            self._render_comments()
            self._set_dirty(True)
            self._toaster.success("已删除该批注")
            W.note(self._task, f"已删除块 {index + 1} 的批注")

    def _jump_to_comment(self, item: QListWidgetItem) -> None:
        index = item.data(Qt.ItemDataRole.UserRole)
        if index is not None:
            self._jump_to_index(int(index))

    def comments(self) -> list[dict]:
        """当前文档的批注列表（界面与导出共用）。"""
        return self._library.list_comments(self._ir) if self._ir is not None else []

    # ---------------------------------------------------------------- 自动保存

    def _start_autosave_timer(self) -> None:
        # type=bool：注册表里存的是 "true"/"false" 字符串，bool("false") 会是 True
        if not bool(self._settings.value("document/autosave", True, type=bool)):
            self._autosave_timer.stop()
            return
        seconds = max(30, int(self._settings.value("document/autosave_seconds", 120) or 120))
        self._autosave_timer.setInterval(seconds * 1000)
        self._autosave_timer.start()

    def _autosave_tick(self) -> None:
        if not self._dirty or self._ir is None or not self._edit_button.isChecked():
            return
        target = self._ir.path
        if not target or not Path(target).is_file():
            return
        self._apply_editor()
        try:
            # 自动保存不写版本快照：真正的版本由用户点「保存」或美化/合并产生，
            # 否则一个下午就能把 40 个快照额度刷满
            self._library.save(self._ir, target, overwrite=True, snapshot=False)
        except Exception as error:  # noqa: BLE001
            W.note(self._task, f"自动保存失败：{error}")
            return
        self._set_dirty(False)
        self.audit_autosave(target)
        W.note(self._task, f"已自动保存：{Path(target).name}")
        self.saved.emit(str(target))

    def audit_autosave(self, target: str) -> None:
        self._library.audit("edit", str(target), "自动保存")

    # ---------------------------------------------------------------- 保存

    def save(self) -> None:
        if self._ir is None:
            return
        if not self._edit_button.isChecked() and self._mode in (MODE_SOURCE, MODE_PARAGRAPH,
                                                                MODE_SHEET):
            self._edit_button.setChecked(True)
        self._apply_editor()
        self._set_dirty(False)
        target = self._ir.path
        if not target:
            self.save_as()
            return
        try:
            result = self._library.save(self._ir, target)
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"保存失败：{error}")
            W.note(self._task, f"保存失败：{error}")
            return
        self._toaster.success(f"已保存：{result.path.name}")
        W.note(self._task, f"已保存 {result.path}")
        self.saved.emit(str(result.path))
        self.irChanged.emit(self._ir)

    def save_as(self) -> None:
        if self._ir is None:
            return
        self._apply_editor()
        path, _ = QFileDialog.getSaveFileName(self, "另存为", self._ir.path or self._ir.title)
        if not path:
            return
        try:
            result = self._library.save(self._ir, path, overwrite=True)
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"另存失败：{error}")
            return
        self._toaster.success(f"已另存为 {result.path.name}")
        self._set_dirty(False)
        self.saved.emit(str(result.path))

    # ---------------------------------------------------------------- 打印

    def print_document(self) -> None:
        """打印当前文档：统一先弹打印对话框（选打印机/份数/页范围）。

        PDF 用 PyMuPDF 逐页按打印机分辨率渲染（保真且**能选打印机**），
        其它格式渲染成 HTML 交给 Qt 排版；不再直接 `os.startfile(..., "print")`
        盲打默认打印机。
        """
        if self._ir is None:
            self._toaster.info("先打开一个文档")
            return
        from PySide6.QtGui import QTextDocument
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter

        self._apply_editor()
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dialog = QPrintDialog(printer, self)
        dialog.setWindowTitle("打印文档")
        if dialog.exec() != QPrintDialog.DialogCode.Accepted:
            return
        if self._ir.format_key == "pdf" and self._ir.path and Path(self._ir.path).is_file():
            if self._print_pdf(printer):
                self._toaster.success(f"已发送到打印机：{Path(self._ir.path).name}")
                self._library.audit("export", self._ir.path, "打印（Qt 打印对话框）")
                return
            W.note(self._task, "PDF 渲染打印不可用（需要 PyMuPDF），改为按文本排版打印")
        document = QTextDocument()
        document.setHtml(render_html(self._ir, standalone=True))
        document.print_(printer)
        self._toaster.success("已发送到打印机")
        self._library.audit("export", self._ir.path or self._ir.title, "打印")

    def _print_pdf(self, printer) -> bool:  # noqa: ANN001
        """按页把 PDF 画到打印机上（尊重对话框里选的页范围）；无法渲染时返回 False。"""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPainter

        pages = _PdfPages(Path(self._ir.path), printer.resolution())
        try:
            total = pages.count
            if total <= 0:
                return False
            first = int(printer.fromPage() or 1)
            last = int(printer.toPage() or total)
            first = max(1, min(first, total))
            last = max(first, min(last, total))
            painter = QPainter(printer)
            try:
                for index in range(first - 1, last):
                    if index > first - 1:
                        printer.newPage()
                    area = painter.viewport()
                    image = pages.image(index, area.size())
                    if image is None or image.isNull():
                        continue
                    image = image.scaled(area.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
                    painter.drawImage(0, 0, image)
            finally:
                painter.end()
        finally:
            pages.close()
        return True


    # ---------------------------------------------------------------- 查找替换

    def _find_next(self) -> None:
        if self._ir is None:
            return
        needle = self._find_input.text()
        if not needle:
            return
        ir = self.current_ir()
        if ir is None:
            return
        text = ir.text()
        if self._regex_box.isChecked():
            import re

            try:
                re.search(needle, text)
            except re.error as error:
                self._find_status.setText(f"正则错误：{error}")
                return
            hits = len(re.findall(needle, text))
        else:
            hits = text.count(needle)
        self._find_status.setText(f"{hits} 处" if hits else "没有找到")
        if not hits:
            self._toaster.info(f"没有找到「{needle}」")
        elif self._preview.isVisible():
            self._preview.find(needle)

    def _replace(self, replace_all: bool) -> None:
        if self._ir is None:
            return
        needle = self._find_input.text()
        if not needle:
            return
        ir = self.current_ir()
        if ir is None:
            return
        replacement = self._replace_input.text()
        try:
            hits = ir.replace_text(needle, replacement, regex=self._regex_box.isChecked(),
                                   count=0 if replace_all else 1)
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"替换失败：{error}")
            return
        if not hits:
            self._toaster.info("没有可替换的内容")
        else:
            self._toaster.success(f"已替换 {hits} 处")
            self._set_dirty(True)
        self._refresh()
        self._render_outline()
        self._render_bookmarks()
        self._render_comments()
        self._render_meta()
        self.irChanged.emit(ir)

    def _jump_to_heading(self, item: QTreeWidgetItem) -> None:
        index = item.data(0, Qt.ItemDataRole.UserRole)
        if index is None:
            return
        self._jump_to_index(int(index))

    # ---------------------------------------------------------------- 体检与版本

    def _show_health(self) -> None:
        ir = self.current_ir()
        if ir is None:
            return
        analysis = analyze_document(ir)
        score = heuristic_score(ir)
        lines = [
            f"结构：{analysis['blocks']} 块 / 标题 {analysis['headings']} / "
            f"表格 {analysis['tables']} / 段落 {analysis['paragraphs']}",
            f"语言：{analysis['language']}；平均句长 {analysis['avg_sentence_length']} 字；"
            f"超长句 {analysis['long_sentences']} 个",
            f"质量：{score.summary()}",
            "",
            "问题清单：",
        ]
        lines.extend(f"· {item}" for item in analysis["issues"] or ["（没有发现明显问题）"])
        if score.notes:
            lines.append("")
            lines.append("评分说明：")
            lines.extend(f"· {note}" for note in score.notes)
        dialog = QMessageBox(self)
        dialog.setWindowTitle("文档体检")
        dialog.setText("\n".join(lines))
        dialog.exec()
        W.note(self._task, f"文档体检：{score.summary()}")

    def _show_versions(self) -> None:
        ir = self.current_ir()
        if ir is None:
            return
        versions = self._library.versions(ir)
        dialog = W.VersionDialog(versions, self)
        if dialog.exec() != W.VersionDialog.DialogCode.Accepted:
            return
        snapshot = self._library.rollback(dialog.selected_version)
        if snapshot is None:
            self._toaster.error("回滚失败：快照不存在或已过期")
            return
        self.apply_ir(snapshot)
        self._toaster.success("已回滚到所选版本（记得点保存写回文件）")
        W.note(self._task, "已回滚到历史版本")

    # ---------------------------------------------------------------- 杂项

    def _toast_info(self, message: str) -> None:
        self._toaster.info(message)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._dirty:
            answer = QMessageBox.question(
                self, "放弃未保存的修改？", "当前有未保存的编辑内容，确定关闭吗？")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._live_timer.stop()
        self._autosave_timer.stop()
        super().closeEvent(event)


class _PdfPages:
    """PDF 逐页取图：PyMuPDF（装了就用）→ Qt 自带 QtPdf（随包，默认可用）。

    打印保真靠它：把页面按打印机分辨率渲染成位图，再画到 `QPrinter` 上，
    同时还能用上打印对话框里的"页范围/份数/打印机"。
    """

    def __init__(self, path: Path, resolution: int) -> None:
        self._path = str(path)
        self._resolution = max(1, int(resolution or 300))
        self._fitz = None
        self._fitz_doc = None
        self._qt_doc = None
        self._count = 0
        self._open()

    def _open(self) -> None:
        try:                                   # 优先 PyMuPDF（装了的话，渲染质量最好）
            import fitz

            self._fitz = fitz
            self._fitz_doc = fitz.open(self._path)
            self._count = int(self._fitz_doc.page_count or 0)
            return
        except Exception:  # noqa: BLE001  没装 / 打不开 → 换 QtPdf
            self._fitz = None
            self._fitz_doc = None
        try:
            from PySide6.QtPdf import QPdfDocument

            document = QPdfDocument()
            if document.load(self._path) == QPdfDocument.Error.None_:
                self._qt_doc = document
                self._count = int(document.pageCount())
        except Exception:  # noqa: BLE001  QtPdf 也被裁掉了：调用方退回文本排版
            self._qt_doc = None

    @property
    def count(self) -> int:
        return self._count

    def image(self, index: int, size):  # noqa: ANN001, ANN202
        from PySide6.QtGui import QImage

        if self._fitz_doc is not None and self._fitz is not None:
            page = self._fitz_doc.load_page(index)
            scale = self._resolution / 72.0                 # PDF 点 → 设备像素
            pixmap = page.get_pixmap(matrix=self._fitz.Matrix(scale, scale), alpha=False)
            return QImage(pixmap.samples, pixmap.width, pixmap.height, pixmap.stride,
                          QImage.Format.Format_RGB888).copy()
        if self._qt_doc is not None:
            return self._qt_doc.render(index, size)
        return None

    def close(self) -> None:
        if self._fitz_doc is not None:
            try:
                self._fitz_doc.close()
            except Exception:  # noqa: BLE001  关闭失败不影响打印结果
                pass
            self._fitz_doc = None
        self._qt_doc = None


__all__ = ["DocViewer"]
