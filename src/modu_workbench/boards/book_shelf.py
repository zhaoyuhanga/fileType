"""书架视图：导入 / 搜索 / 书籍卡片网格。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.reader import BookRecord, Library
from modu_workbench.services.config import walk_book_files
from modu_workbench.ui_kit.toast import Toaster


class BookCard(QFrame):
    """书架卡片：书名/作者/格式/进度 + 阅读与移除操作。"""

    open_requested = Signal(int)
    remove_requested = Signal(int)

    def __init__(self, record: BookRecord, parent: QWidget | None = None):
        super().__init__(parent)
        self._record = record
        self.setObjectName("boardCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(0, 150)
        self.setMaximumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)

        head = QHBoxLayout()
        title = QLabel(record.title or record.path)
        title.setObjectName("cardTitle")
        head.addWidget(title, 1)
        fmt = QLabel(record.format.upper() if record.format else "?")
        fmt.setObjectName("cardTagline")
        head.addWidget(fmt)
        layout.addLayout(head)

        meta = QLabel(record.author or "佚名")
        meta.setObjectName("cardDesc")
        layout.addWidget(meta)

        progress = self._progress_text(record)
        chip = QLabel(progress)
        chip.setObjectName("pageLine")
        layout.addWidget(chip)
        layout.addStretch(1)

        actions = QHBoxLayout()
        open_btn = QPushButton("阅读")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.clicked.connect(lambda: self.open_requested.emit(record.id))
        remove_btn = QPushButton("移除")
        remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_btn.clicked.connect(lambda: self.remove_requested.emit(record.id))
        actions.addWidget(open_btn)
        actions.addWidget(remove_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

    @staticmethod
    def _progress_text(record: BookRecord) -> str:
        if record.last_chapter_index <= 0 and record.last_progress <= 0:
            return "未开始"
        if record.last_progress >= 0.95:
            return f"已读完 · {record.last_chapter_index + 1} 章"
        return f"读到第 {record.last_chapter_index + 1} 章 · {int(record.last_progress * 100)}%"

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.open_requested.emit(self._record.id)
        super().mouseReleaseEvent(event)


class ShelfView(QWidget):
    """书架页：顶部操作栏 + 可滚动卡片网格。"""

    open_book = Signal(int)

    def __init__(self, library: Library, toaster: Toaster, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("shelfPage")
        self._library = library
        self._toaster = toaster
        self._filter = ""
        self._records: list[BookRecord] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(10)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        import_files = QPushButton("导入文件")
        import_files.clicked.connect(self._pick_files)
        import_folder = QPushButton("导入文件夹")
        import_folder.clicked.connect(self._pick_folder)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.reload)

        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索书名 / 作者…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search_changed)

        toolbar.addWidget(import_files)
        toolbar.addWidget(import_folder)
        toolbar.addWidget(refresh)
        toolbar.addStretch(1)
        toolbar.addWidget(self._search, 1)
        layout.addLayout(toolbar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll, 1)
        self._scroll = scroll
        self._grid_host: QWidget | None = None
        self._cols = 3
        self.reload()

    # ---------- 导入 ----------

    def _pick_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择要导入的书（TXT / EPUB）", "", "电子书 (*.txt *.epub)"
        )
        self._import_paths(paths)

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择书籍文件夹（递归扫描）")
        if folder:
            self._import_paths([folder])

    def _import_paths(self, raw_paths: list[str]) -> None:
        files = walk_book_files(raw_paths)
        if not files:
            self._toaster.info("未发现受支持的 TXT / EPUB 文件")
            return
        imported = 0
        skipped = 0
        for file_path in files:
            try:
                self._library.import_path(file_path)
                imported += 1
            except ValueError:
                skipped += 1
        self.reload()
        if imported:
            self._toaster.success(f"导入完成：新增 {imported} 本" + (f"，跳过 {skipped} 本" if skipped else ""))
        else:
            self._toaster.error("导入失败：" + f"{skipped} 个文件无法解析")

    # ---------- 展示 ----------

    def _on_search_changed(self, text: str) -> None:
        self._filter = text.strip().lower()
        self._rebuild_grid()

    def reload(self) -> None:
        try:
            self._records = self._library.list_books()
        except Exception:  # noqa: BLE001
            self._records = []
        self._rebuild_grid()

    def _rebuild_grid(self) -> None:
        host = QWidget()
        if self._records:
            grid = QGridLayout(host)
            grid.setContentsMargins(4, 4, 4, 4)
            grid.setSpacing(16)
            columns = self._cols
            column = 0
            row = 0
            for record in self._records:
                haystack = f"{record.title} {record.author}".lower()
                if self._filter and self._filter not in haystack:
                    continue
                card = BookCard(record)
                card.open_requested.connect(self.open_book)
                card.remove_requested.connect(self._remove_book)
                grid.addWidget(card, row, column)
                column += 1
                if column >= columns:
                    column = 0
                    row += 1
            if column > 0:
                row += 1
            for col in range(columns):
                grid.setColumnStretch(col, 1)
            grid.setRowStretch(row, 1)
        else:
            layout = QVBoxLayout(host)
            hint = QLabel(
                "书架空空如也" if not self._filter else "没有匹配的书"
            )
            hint.setObjectName("shelfHint")
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addStretch(1)
            layout.addWidget(hint)
            layout.addStretch(1)

        old = self._scroll.takeWidget()
        self._scroll.setWidget(host)
        if old is not None:
            old.deleteLater()

    def resizeEvent(self, event) -> None:  # noqa: N802
        count = max(1, int(self.width() // 340))
        if count != self._cols:
            self._cols = count
            self._rebuild_grid()
        super().resizeEvent(event)

    def _remove_book(self, book_id: int) -> None:
        record = self._library.get_book(book_id)
        if record is None:
            return
        self._library.remove(book_id)
        self.reload()
        self._toaster.info(f"已从书架移除：{record.title}")
