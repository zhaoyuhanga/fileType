"""书架视图：导入 / 搜索 / 书籍卡片网格。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
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

from modu_workbench.core.book import BookRecord, Library
from modu_workbench.core.book.files import walk_book_files
from modu_workbench.core.platform.logging import swallow
from modu_workbench.ui_kit.components import EmptyState
from modu_workbench.ui_kit.toast import Toaster


class BookImportWorker(QThread):
    """后台批量导入书籍（可取消）。

    ## 为什么必须后台跑

    导入 100 本书要逐个"读文件 → 猜编码 → 解析章节 → 入库"，
    一本 50MB 的 TXT 解析实测约 256 ms，100 本累计十几秒。
    原来 `_import_paths()` 直接在 **UI 线程**同步循环，这段时间窗口完全不响应。

    与图库 / 乐库同一套模式（`progressed` / `finishedImport` / `failed` + `cancel()`）。
    """

    progressed = Signal(int, int, str)
    finishedImport = Signal(int, int, list)   # (新增, 跳过, 失败文件名)
    failed = Signal(str)

    def __init__(self, library: Library, files: list[str], parent=None):  # noqa: ANN001
        super().__init__(parent)
        self._library = library
        self._files = list(files)
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:  # noqa: D102
        imported = 0
        skipped = 0
        failed: list[str] = []
        total = len(self._files)
        try:
            for index, file_path in enumerate(self._files, start=1):
                if self._cancel:
                    break
                try:
                    self._library.import_path(file_path)
                    imported += 1
                except ValueError:
                    # 无法解析（编码/格式）：计入跳过，不算致命
                    skipped += 1
                except OSError as error:
                    # 权限/占用/磁盘：**不能中断整批**（原来会直接抛出去，
                    # 导致后面的书一本都不导入）
                    swallow("导入单本书籍", error)
                    failed.append(file_path)
                self.progressed.emit(index, total, f"导入中 {index}/{total}")
            self.finishedImport.emit(imported, skipped, failed)
        except Exception as error:  # noqa: BLE001  线程里不能让异常冒出去
            self.failed.emit(str(error))


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
        #: 后台导入线程（必须持引用：局部变量被回收而线程还在跑 → 崩溃）
        self._import_worker: BookImportWorker | None = None
        self._import_total = 0
        self._import_progress: tuple[int, int] = (0, 0)
        #: 书架读取失败的原因（非空时界面要显示出来，而不是假装"没有书"）
        self._load_error = ""
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
        """批量导入书籍（后台线程）。

        **必须放后台线程**：一本书要"读文件 → 猜编码 → 解析章节 → 入库"，
        50MB 的 TXT 实测约 256 ms；100 本累计十几秒，期间界面完全无响应。
        """
        files = walk_book_files(raw_paths)
        if not files:
            self._toaster.info("未发现受支持的 TXT / EPUB 文件")
            return
        if self._import_worker is not None and self._import_worker.isRunning():
            self._toaster.info("正在导入中，请等这一批完成")
            return

        worker = BookImportWorker(self._library, files, parent=self)
        self._import_worker = worker
        worker.progressed.connect(self._on_import_progress)
        worker.finishedImport.connect(self._on_import_finished)
        worker.failed.connect(self._on_import_failed)
        worker.finished.connect(lambda: setattr(self, "_import_worker", None))
        self._import_total = len(files)
        self._toaster.info(f"正在导入 {len(files)} 个文件…")
        worker.start()

    def _on_import_progress(self, done: int, total: int, message: str) -> None:
        # 只是占位：书架没有进度条，真正的反馈在完成时给
        self._import_progress = (done, total)

    def _on_import_finished(self, imported: int, skipped: int, failed: list) -> None:
        """导入完成（在主线程执行，可以安全刷界面）。"""
        self.reload()
        if imported:
            text = f"导入完成：新增 {imported} 本"
            if skipped:
                text += f"，跳过 {skipped} 本（格式或编码不支持）"
            if failed:
                text += f"，{len(failed)} 个读取失败"
            self._toaster.success(text)
        elif failed:
            # 读取失败要给文件名，否则用户不知道是哪个文件的问题
            names = "、".join(Path(item).name for item in failed[:3])
            more = f" 等 {len(failed)} 个" if len(failed) > 3 else ""
            self._toaster.error(f"导入失败：无法读取 {names}{more}（文件被占用或没有权限）")
        else:
            self._toaster.error(f"导入失败：{skipped} 个文件无法解析")

    def _on_import_failed(self, reason: str) -> None:
        self._toaster.error(f"导入失败：{reason}")

    # ---------- 展示 ----------

    def _on_search_changed(self, text: str) -> None:
        self._filter = text.strip().lower()
        self._rebuild_grid()

    def reload(self) -> None:
        """从库读回书籍列表。

        **读取失败必须让用户看见**：原来 `except Exception: self._records = []`
        会让书架显示成"空空如也"—— 数据库被占用/损坏/没权限时，
        用户第一反应是"我的书全没了"。现在把原因记下来并在空状态里显示。
        """
        try:
            self._records = self._library.list_books()
            self._load_error = ""
        except Exception as error:  # noqa: BLE001  读库失败不能崩界面，但要说出来
            swallow("读取书架列表", error)
            self._records = []
            self._load_error = str(error) or error.__class__.__name__
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
            if self._load_error and not self._filter:
                # 读取失败 ≠ 没有书：把原因说清楚，并提示可以重试
                empty = EmptyState(
                    "⚠",
                    "书架读取失败",
                    f"原因：{self._load_error}\n点上方「刷新」重试；"
                    "若反复失败，检查数据目录是否被其它程序占用或没有写入权限。",
                )
            else:
                empty = EmptyState(
                    "📚",
                    "书架空空如也" if not self._filter else "没有匹配的书",
                    "用上方「导入文件 / 导入文件夹」把 TXT / EPUB 加进来"
                    if not self._filter else "换个关键词试试，或清空搜索框",
                )
            empty.setMaximumHeight(240)
            layout.addStretch(1)
            layout.addWidget(empty)
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
