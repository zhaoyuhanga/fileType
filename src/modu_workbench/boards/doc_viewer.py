"""文档查看/编辑器（txt / md / json / mp4）。

- 文本：预览只读 ↔ 编辑切换；保存 / 另存为；JSON 提供"美化"；
- 预览排版：Markdown 渲染为规范文档（代码块语法高亮），JSON 语法高亮，TXT 等宽；
- mp4：本地播放（QMediaPlayer），仅可另存为复制；
- 未保存修改在关闭时确认。
"""
from __future__ import annotations

import html as html_mod
import re
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

# ---------- 语法高亮与排版 ----------

_MONO = "Consolas,'Cascadia Mono','Courier New',monospace"
_CODE_FENCE = re.compile(
    r"<pre><code(?: class=\"language-([\\w+-]+)\")?>(.*?)</code></pre>", re.DOTALL
)
_INLINE_CODE = re.compile(r"<code>(.*?)</code>", re.DOTALL)


def highlight_code(code: str, language: str | None = None) -> str:
    """Pygments 语法高亮，返回带内联 token 颜色的 <pre> 片段（无需外部 CSS）。

    常用语言直接引用词法器类（保证 PyInstaller 能静态收集）；
    其它语言走动态查找，失败时回退纯文本。
    """
    from pygments import highlight as _pyg_highlight
    from pygments.formatters import HtmlFormatter
    from pygments.lexers import (
        BashLexer,
        CssLexer,
        HtmlLexer,
        JavascriptLexer,
        PythonLexer,
        SqlLexer,
        TextLexer,
        XmlLexer,
        get_lexer_by_name,
        guess_lexer,
    )
    from pygments.lexers.data import JsonLexer
    from pygments.lexers.text import YamlLexer

    _KNOWN: dict[str, object] = {
        "json": JsonLexer(),
        "python": PythonLexer(),
        "py": PythonLexer(),
        "js": JavascriptLexer(),
        "javascript": JavascriptLexer(),
        "html": HtmlLexer(),
        "xml": XmlLexer(),
        "css": CssLexer(),
        "bash": BashLexer(),
        "sh": BashLexer(),
        "shell": BashLexer(),
        "sql": SqlLexer(),
        "yaml": YamlLexer(),
        "yml": YamlLexer(),
    }
    lexer = None
    try:
        lexer = _KNOWN.get((language or "").lower()) or (
            get_lexer_by_name(language) if language else guess_lexer(code)
        )
    except Exception:  # noqa: BLE001
        lexer = None
    if lexer is None:
        lexer = TextLexer()
    body = _pyg_highlight(code, lexer, HtmlFormatter(nowrap=True, noclasses=True))
    return f"<pre style=\"font-family:{_MONO};font-size:13px;line-height:1.6;\">{body}</pre>"


def _render_markdown(content: str) -> str:
    import markdown as md_lib

    html = md_lib.markdown(content, extensions=["extra", "sane_lists"])

    def replace_block(match: re.Match) -> str:
        language = (match.group(1) or "").strip() or None
        raw = html_mod.unescape(match.group(2))
        return highlight_code(raw, language)

    html = _CODE_FENCE.sub(replace_block, html)
    html = _INLINE_CODE.sub(
        lambda m: f"<code style=\"font-family:{_MONO};color:#a0401f;\">{m.group(1)}</code>",
        html,
    )
    return (
        f"<html><body style=\"font-family:'PingFang SC','Microsoft YaHei',sans-serif;"
        f"font-size:15px;color:#24292f;\">{html}</body></html>"
    )


def _render_json(content: str) -> str:
    warn_html = ""
    try:
        pretty = json_pretty(content)
    except JsonFormatError as error:
        pretty = content
        warn_html = (
            f"<p style=\"color:#b76a08;background:#fdf3e3;padding:8px 12px;\">"
            f"JSON 无效：{html_mod.escape(str(error))}</p>"
        )
    return f"<html><body style='color:#24292f;'>{warn_html}{highlight_code(pretty, 'json')}</body></html>"


def _render_txt(content: str) -> str:
    escaped = html_mod.escape(content, quote=False)
    return (
        f"<html><body style=\"font-family:{_MONO};font-size:13px;\">"
        f"<pre style='white-space:pre-wrap;'>{escaped}</pre></body></html>"
    )


VIEWABLE_TEXT = {".txt", ".md", ".json"}


class DocViewerDialog(QDialog):
    """统一文档查看器。"""

    def __init__(self, file_path: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._path = Path(file_path)
        self._saved = ""  # 与磁盘一致的内容快照
        self._dirty = False

        self.setWindowTitle(f"查看：{self._path.name}")
        self.resize(960, 660)

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
            self._preview.setHtml(f"<p style='color:#b3262b;'>读取失败：{error}</p>")

    def _current_text(self) -> str:
        return self._editor.toPlainText() if self._editor.isVisible() else self._saved

    def _render_preview(self) -> None:
        content = self._saved
        ext = self._path.suffix.lower()
        if ext == ".md":
            self._preview.setHtml(_render_markdown(content))
            self._mode_btn.setVisible(True)
            self._format_btn.setVisible(False)
        elif ext == ".json":
            self._preview.setHtml(_render_json(content))
            self._format_btn.setVisible(True)
            self._mode_btn.setVisible(True)
        else:
            self._preview.setHtml(_render_txt(content))
            self._mode_btn.setVisible(True)
            self._format_btn.setVisible(False)

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

    def _notify_edit(self) -> None:
        self._dirty = True
        self._refresh_title()
