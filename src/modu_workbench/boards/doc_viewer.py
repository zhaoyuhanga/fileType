"""文档查看/编辑器（txt / md / json / mp4）。

- 文本：预览只读 ↔ 编辑切换；保存 / 另存为；JSON 提供"美化"；
- mp4：本地播放（QMediaPlayer），仅可另存为复制；
- 未保存修改在关闭时确认。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.convert.text_io import JsonFormatError, json_pretty, read_text_smart, write_text

VIEWABLE_TEXT = {".txt", ".md", ".json"}


class DocViewerDialog(QDialog):
    """统一文档查看器。"""

    def __init__(self, file_path: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._path = Path(file_path)
        self._saved = ""  # 与磁盘一致的内容快照
        self._dirty = False

        self.setWindowTitle(f"查看：{self._path.name}")
        self.resize(900, 640)

        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self._mode_btn = QPushButton("编辑")
        self._mode_btn.clicked.connect(self._toggle_mode)
        self._save_btn = QPushButton("保存")
        self._save_btn.clicked.connect(self._save)
        self._save_as_btn = QPushButton("另存为")
        self._save_as_btn.clicked.connect(self._save_as)
        self._format_btn = QPushButton("JSON 美化")
        self._format_btn.clicked.connect(self._format_json)
        self._format_btn.setVisible(False)

        toolbar.addWidget(QLabel(self._path.name))
        toolbar.addStretch(1)
        toolbar.addWidget(self._format_btn)
        toolbar.addWidget(self._mode_btn)
        toolbar.addWidget(self._save_btn)
        toolbar.addWidget(self._save_as_btn)
        layout.addLayout(toolbar)

        if self._path.suffix.lower() == ".mp4":
            self._build_media()
        else:
            self._build_text()

        self._load()
        self._refresh_title()

    # ---------- 构建 ----------

    def _build_text(self) -> None:
        splitter = QSplitter(Qt.Orientation.Vertical)
        self._preview = QTextBrowser()
        self._preview.setOpenExternalLinks(False)
        self._editor = QPlainTextEdit()
        self._editor.hide()
        self._editor.textChanged.connect(self._notify_edit)
        splitter.addWidget(self._preview)
        splitter.addWidget(self._editor)
        layout = self.layout()
        layout.addWidget(splitter, 1)
        layout.addWidget(QLabel("提示：预览为只读；切换到「编辑」后可修改，Ctrl+S 保存。"))
        self._is_text = True

    def _build_media(self) -> None:
        from PySide6.QtMultimedia import QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget

        self._player = QMediaPlayer(self)
        self._video = QVideoWidget(self)
        self._player.setVideoOutput(self._video)
        layout = self.layout()
        layout.addWidget(self._video, 1)
        layout.addWidget(QLabel("本地视频预览（只读）；可用「另存为」复制该文件。"))
        self._mode_btn.setEnabled(False)
        self._save_btn.setEnabled(False)
        self._format_btn.hide()
        self._is_text = False

    # ---------- 数据 ----------

    def _load(self) -> None:
        if not self._path.is_file():
            QMessageBox.warning(self, "无法打开", "文件不存在或已被移动")
            self._saved = ""
            return
        try:
            if self._path.suffix.lower() == ".mp4":
                from PySide6.QtCore import QUrl

                self._player.setSource(QUrl.fromLocalFile(str(self._path)))
                self._player.play()
                return
            content = read_text_smart(self._path)
            self._saved = content
            self._editor.setPlainText(content)
            self._render_preview()
        except Exception as error:  # noqa: BLE001
            self._saved = ""
            self._editor.setPlainText("")
            self._preview.setHtml(f"<p style='color:#d06;'>读取失败：{error}</p>")

    def _current_text(self) -> str:
        return self._editor.toPlainText() if self._editor.isVisible() else self._saved

    def _render_preview(self) -> None:
        content = self._saved
        ext = self._path.suffix.lower()
        if ext == ".md":
            import markdown as md_lib

            self._preview.setHtml(f"<body style='margin:18px;'>{md_lib.markdown(content, extensions=['extra'])}</body>")
            self._mode_btn.setVisible(True)
        elif ext == ".json":
            try:
                pretty = json_pretty(content)
                self._preview.setHtml(f"<pre style='font-family:monospace;'>{_escape(pretty)}</pre>")
            except JsonFormatError as error:
                self._preview.setHtml(
                    f"<p style='color:#d06000;'>JSON 无效：{error}</p><pre style='font-family:monospace;'>{_escape(content)}</pre>"
                )
            self._format_btn.setVisible(True)
        else:
            self._preview.setHtml(f"<pre style='font-family:monospace;white-space:pre-wrap;'>{_escape(content)}</pre>")

    def _toggle_mode(self) -> None:
        editing = not self._editor.isVisible()
        self._editor.setVisible(editing)
        self._preview.setVisible(not editing)
        self._mode_btn.setText("预览" if editing else "编辑")
        if editing:
            self._editor.setFocus()

    def _format_json(self) -> None:
        raw = self._current_text()
        try:
            pretty = json_pretty(raw)
        except JsonFormatError as error:
            QMessageBox.warning(self, "JSON 语法错误", str(error))
            return
        if pretty != raw:
            self._editor.setPlainText(pretty)
            if not self._editor.isVisible():
                self._toggle_mode()
            self._mark_dirty()

    def _save(self) -> None:
        try:
            write_text(self._path, self._editor.toPlainText())
            self._saved = self._editor.toPlainText()
            self._dirty = False
            self._render_preview()
            self._refresh_title()
        except Exception as error:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(error))

    def _save_as(self) -> None:
        target, _ = QFileDialog.getSaveFileName(self, "另存为", str(self._path))
        if not target:
            return
        try:
            if self._path.suffix.lower() == ".mp4":
                import shutil

                shutil.copyfile(self._path, target)
            else:
                write_text(target, self._editor.toPlainText())
            QMessageBox.information(self, "另存为", f"已保存到：{target}")
        except Exception as error:  # noqa: BLE001
            QMessageBox.critical(self, "另存为失败", str(error))

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._refresh_title()

    def _refresh_title(self) -> None:
        marker = " *" if self._dirty else ""
        self.setWindowTitle(f"查看：{self._path.name}{marker}")

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._dirty and self._saved != self._current_text():
            answer = QMessageBox.question(
                self, "未保存的修改", "当前文档有未保存的修改，关闭将丢失。是否放弃修改并关闭？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        if self._path.suffix.lower() == ".mp4":
            self._player.stop()
        super().closeEvent(event)

    # 供编辑器内容变更时外部调用
    def _notify_edit(self) -> None:
        self._dirty = True
        self._refresh_title()


def _escape(text: str) -> str:
    import html as html_mod

    return html_mod.escape(text, quote=False)
