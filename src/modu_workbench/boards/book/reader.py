"""阅读视图：章节浏览 / 阅读主题 / 字号 / 进度记忆 / 快捷键。"""

from __future__ import annotations

import html as html_mod

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.book import Book, BookRecord, Library
from modu_workbench.core.platform.logging import swallow

# 5 套阅读主题（名称, 背景, 前景）
READ_THEMES: list[tuple[str, str, str]] = [
    ("米白", "#f7f1e3", "#3a342a"),
    ("纯白", "#ffffff", "#222222"),
    ("薄荷", "#e8f1ec", "#1f3a32"),
    ("灰白", "#e8e6e1", "#2d2d2d"),
    ("夜间", "#1a1a1a", "#c8c8c8"),
]

FONT_MIN, FONT_MAX, FONT_STEP = 12, 30, 2


def _describe_load_error(error: BaseException, path: str) -> str:
    """把载入异常翻译成用户看得懂、且能照着做的原因。

    为什么要翻译而不是直接 `str(error)`：底层抛的是
    `FileNotFoundError(2, 'No such file or directory')` 这类东西，
    对用户没有行动价值。这里按最常见的几类给出**下一步该做什么**。
    """
    from pathlib import Path as _Path

    if isinstance(error, KeyError):
        return "这本书的记录已不存在（可能刚被删除），请刷新书架。"
    if isinstance(error, FileNotFoundError):
        return f"原文件找不到了：{path}\n请把文件放回原位置，或重新导入这本书。"
    if isinstance(error, PermissionError):
        return f"没有权限读取：{path}\n请检查文件是否被其它程序占用，或换个位置。"
    if isinstance(error, UnicodeDecodeError):
        return ("这本书的文字编码无法识别（可能不是 UTF-8/GBK）。\n"
                "可以先用「墨软转换」把它转成 UTF-8 的 TXT 再导入。")
    if isinstance(error, ValueError):
        # `Library.load_for_reading` 对"解析不出正文"抛 ValueError，消息本身可读
        detail = str(error).strip() or "解析失败"
        return f"{detail}\n文件：{path}"
    if isinstance(error, OSError):
        return f"读取文件出错：{error}\n文件：{path}"
    return f"{type(error).__name__}: {error}\n文件：{path}"



class ReaderView(QWidget):
    """电子书阅读页：返回书架时自动保存阅读位置。"""

    back_requested = Signal()
    #: 载入失败时发出（参数是可直接展示给用户的原因）。
    #: 为什么不直接抛：阅读页已经在构造流程里，抛异常会让板块只看到一个
    #: "无法打开这本书"的笼统错误；发信号能让板块显示**真实原因**
    #: （文件被删了 / 权限不足 / 解析不出正文）。
    load_failed = Signal(str)

    def __init__(self, library: Library, record: BookRecord, parent: QWidget | None = None):
        super().__init__(parent)
        self._library = library
        self._record_id = record.id
        self._index = max(0, record.last_chapter_index)
        self._font_size = 18
        self._theme_index = 0
        self._parsed: Book | None = None
        self._chapters = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_toolbar())

        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(False)
        self._browser.verticalScrollBar().valueChanged.connect(self._schedule_save)
        layout.addWidget(self._browser, 1)

        status_row = QHBoxLayout()
        self._chapter_label = QLabel("")
        self._chapter_label.setObjectName("readerStatus")
        self._status_label = QLabel("")
        self._status_label.setObjectName("readerStatus")
        status_row.addWidget(self._chapter_label)
        status_row.addStretch(1)
        status_row.addWidget(self._status_label)
        layout.addLayout(status_row)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save_progress)

        self._install_shortcuts()
        self._load_book(record)
        self._render()

    # ---------- 构建 ----------

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("bookReaderBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(14, 8, 14, 8)
        row.setSpacing(8)

        back = QPushButton("← 返回书架")
        back.clicked.connect(self._go_back)

        self._title_label = QLabel("")
        self._title_label.setObjectName("readerTitle")

        prev = QPushButton("上一章")
        prev.clicked.connect(lambda: self.go_chapter(-1))
        self._next = QPushButton("下一章")
        self._next.clicked.connect(lambda: self.go_chapter(+1))

        self._chapters_combo = QComboBox()
        self._chapters_combo.currentIndexChanged.connect(self._jump_to_chapter)

        font_down = QPushButton("A-")
        font_up = QPushButton("A+")
        font_down.clicked.connect(lambda: self.change_font(-FONT_STEP))
        font_up.clicked.connect(lambda: self.change_font(+FONT_STEP))

        self._theme_combo = QComboBox()
        for name, _bg, _fg in READ_THEMES:
            self._theme_combo.addItem(name)
        self._theme_combo.currentIndexChanged.connect(self._change_theme)

        row.addWidget(back)
        row.addWidget(self._title_label, 1)
        row.addWidget(prev)
        row.addWidget(self._next)
        row.addWidget(self._chapters_combo)
        row.addWidget(font_down)
        row.addWidget(font_up)
        row.addWidget(self._theme_combo)
        return bar

    def _install_shortcuts(self) -> None:
        left = QShortcut(QKeySequence(Qt.Key.Key_Left), self)
        left.activated.connect(lambda: self.go_chapter(-1))
        right = QShortcut(QKeySequence(Qt.Key.Key_Right), self)
        right.activated.connect(lambda: self.go_chapter(+1))
        bigger = QShortcut(QKeySequence("Ctrl+="), self)
        bigger.activated.connect(lambda: self.change_font(+FONT_STEP))
        smaller = QShortcut(QKeySequence("Ctrl+-"), self)
        smaller.activated.connect(lambda: self.change_font(-FONT_STEP))

    # ---------- 加载与渲染 ----------

    def _load_book(self, record: BookRecord) -> None:
        """解析并载入正文。失败时**必须让用户知道**，不能安静地显示空白。"""
        try:
            _, parsed = self._library.load_for_reading(self._record_id)
        except Exception as error:  # noqa: BLE001  解析可能因任意原因失败（文件、编码、格式）
            # 以前的写法是 `parsed = None` 静默吞掉：用户看到的是一个空阅读页，
            # 既不知道书没了、也不知道该做什么。这里把原因发出去。
            swallow("载入书籍正文", error)
            self._parsed = None
            self._chapters = []
            self.load_failed.emit(_describe_load_error(error, record.path))
            return
        self._parsed = parsed
        self._chapters = list(parsed.chapters) if parsed and parsed.chapters else []
        self._index = min(self._index, max(0, len(self._chapters) - 1))

    def _chapter_html(self, index: int) -> str:
        chapter = self._chapters[index]
        theme = READ_THEMES[self._theme_index]
        bg, fg = theme[1], theme[2]
        lines = [line.strip() for line in (chapter.content or "").splitlines() if line.strip()]
        if not lines:
            lines = [c for c in (chapter.paragraphs or []) if c]
        body = "".join(f"<p>{html_mod.escape(line)}</p>" for line in lines)
        title = html_mod.escape(chapter.title or "")
        return (
            f'<html><head><style>body{{background:{bg};color:{fg};'
            f"font-family:'PingFang SC','Microsoft YaHei',serif;"
            f'font-size:{self._font_size}px;line-height:1.9;}}</style></head>'
            f'<body><div style="max-width:820px;margin:0 auto;padding:18px 26px 60px;">'
            f"<h2 style=\"border-bottom:1px solid {fg}33;padding-bottom:8px;\">{title}</h2>"
            f"{body}</div></body></html>"
        )

    def _render(self) -> None:
        if not self._chapters:
            self._browser.setHtml(
                '<html><body><p style="color:#888;">（无可显示章节）</p></body></html>'
            )
            self._title_label.setText(self._parsed.title if self._parsed else "")
            self._status_label.setText("")
            self._next.setEnabled(False)
            return

        if self._chapters_combo.count() != len(self._chapters):
            self._chapters_combo.blockSignals(True)
            self._chapters_combo.clear()
            self._chapters_combo.addItems(
                [f"{i + 1}. {chapter.title}" for i, chapter in enumerate(self._chapters)]
            )
            self._chapters_combo.setCurrentIndex(self._index)
            self._chapters_combo.blockSignals(False)

        self._browser.setHtml(self._chapter_html(self._index))
        chapter = self._chapters[self._index]
        self._title_label.setText(f"{self._parsed.title if self._parsed else ''} · {chapter.title}")
        self._chapter_label.setText(f"第 {self._index + 1} / {len(self._chapters)} 章")
        self._next.setEnabled(self._index < len(self._chapters) - 1)
        # 恢复上次进度（滚动比例）
        progress = self._library.get_book(self._record_id)
        if progress is not None and progress.last_progress > 0:
            QTimer.singleShot(0, lambda: self._set_scroll(progress.last_progress))
        else:
            self._set_scroll(0.0)
        self._update_status()

    def _set_scroll(self, fraction: float) -> None:
        bar = self._browser.verticalScrollBar()
        maximum = max(1, bar.maximum() - bar.minimum())
        bar.setValue(bar.minimum() + int(fraction * maximum))

    def _update_status(self) -> None:
        bar = self._browser.verticalScrollBar()
        span = max(1, bar.maximum() - bar.minimum())
        progress = 0.0 if span <= 0 else (bar.value() - bar.minimum()) / span
        self._status_label.setText(f"进度 {int(progress * 100)}%")

    # ---------- 交互 ----------

    def go_chapter(self, delta: int) -> None:
        if not self._chapters:
            return
        self._save_now()
        target = min(max(0, self._index + delta), len(self._chapters) - 1)
        if target == self._index:
            return
        self._index = target
        self._chapters_combo.blockSignals(True)
        self._chapters_combo.setCurrentIndex(target)
        self._chapters_combo.blockSignals(False)
        self._log_chapter_switch()
        self._render()

    def _jump_to_chapter(self, index: int) -> None:
        if index < 0 or index == self._index or index >= len(self._chapters):
            return
        self._save_now()
        self._index = index
        self._log_chapter_switch()
        self._render()

    def _log_chapter_switch(self) -> None:
        chapter = self._chapters[self._index]
        try:
            self._library.save_position(
                self._record_id,
                self._index,
                0,
                0.0,
                chapter_title=chapter.title,
                action="chapter",
            )
        except Exception as error:  # noqa: BLE001  外部回调/第三方边界
            swallow("reader 中的可选步骤", error)

    def change_font(self, delta: int) -> None:
        self._font_size = min(FONT_MAX, max(FONT_MIN, self._font_size + delta))
        self._render()

    def _change_theme(self, index: int) -> None:
        self._theme_index = max(0, index)
        self._render()

    # ---------- 保存 ----------

    def _schedule_save(self) -> None:
        self._update_status()
        self._save_timer.start()

    def _save_now(self) -> None:
        if self._save_timer.isActive():
            self._save_timer.stop()
        self._save_progress()

    def _save_progress(self) -> None:
        if not self._chapters:
            return
        bar = self._browser.verticalScrollBar()
        span = bar.maximum() - bar.minimum()
        progress = 0.0 if span <= 0 else (bar.value() - bar.minimum()) / span
        chapter = self._chapters[self._index]
        offset = int(progress * max(0, len(chapter.content or "") - 1))
        try:
            self._library.storage.save_position(
                self._record_id, self._index, offset, min(1.0, max(0.0, progress))
            )
        except Exception as error:  # noqa: BLE001  外部回调/第三方边界
            swallow("reader 中的可选步骤", error)

    def _go_back(self) -> None:
        self._save_now()
        self.back_requested.emit()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_now()
        super().closeEvent(event)
