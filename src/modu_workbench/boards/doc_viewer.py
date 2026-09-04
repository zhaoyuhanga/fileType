"""文档查看/编辑器（txt / md / json / mp4）。

- 文本：预览只读 ↔ 编辑切换；保存 / 另存为；JSON 提供"美化"；
- 预览渲染：优先 QtWebEngine（内嵌 Chromium，完整 CSS，与 main 分支 Web 预览一致），
  无头/不可用时回退 QTextBrowser；Markdown 规范排版 + 代码块语法高亮，
  JSON 语法高亮（无效黄条提示），TXT 等宽换行；
- mp4：本地播放（QMediaPlayer），仅可另存为复制；
- 未保存修改在关闭时确认。
"""
from __future__ import annotations

import html as html_mod
import os
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

# ---------- 预览页面 CSS（完整 CSS，经 QtWebEngine 渲染，对齐 main 分支） ----------

_PAGE_CSS = """
body {{
    margin: 20px 28px 46px;
    color: #1c2333;
    font-family: "PingFang SC", "Microsoft YaHei", -apple-system, sans-serif;
    font-size: 15px;
    line-height: 1.85;
    background: #ffffff;
}}
h1, h2, h3, h4, h5, h6 {{ color: #1c2333; line-height: 1.4; }}
h1 {{ font-size: 1.7em; border-bottom: 1px solid #e3e8f1; padding-bottom: 8px; }}
h2 {{ font-size: 1.4em; border-bottom: 1px solid #e3e8f1; padding-bottom: 6px; }}
h3 {{ font-size: 1.18em; }}
h4 {{ font-size: 1.05em; }}
p {{ margin: 10px 0; }}
a {{ color: #4f6ef7; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
code {{
    font-family: "JetBrains Mono", Consolas, "Cascadia Code", monospace;
    font-size: 13px;
    background: #eef1f7;
    color: #b02f60;
    padding: 2px 6px;
    border-radius: 5px;
}}
pre {{
    background: #1e2433;
    color: #e6e9f2;
    border-radius: 10px;
    padding: 14px 16px;
    overflow: auto;
}}
pre code {{
    background: transparent;
    padding: 0;
    color: inherit;
    font-size: 13px;
    line-height: 1.65;
}}
blockquote {{
    margin: 12px 0;
    padding: 2px 16px;
    border-left: 4px solid #4f6ef7;
    color: #47526a;
    background: #f7f9fd;
    border-radius: 0 6px 6px 0;
}}
table {{ border-collapse: collapse; margin: 12px 0; }}
th, td {{ border: 1px solid #e3e8f1; padding: 7px 13px; }}
th {{ background: #f7f9fd; font-weight: 600; }}
ul, ol {{ margin: 8px 0; padding-left: 26px; }}
hr {{ border: none; border-top: 2px solid #e3e8f1; margin: 18px 0; }}
img {{ max-width: 100%; border-radius: 7px; }}
.warn {{
    color: #b97f10;
    background: #fbf2dd;
    border: 1px solid #e9cf8b;
    border-radius: 6px;
    padding: 8px 12px;
    margin: 4px 0 12px;
}}
"""

_MONO = "Consolas,'Cascadia Mono','Courier New',monospace"
_CODE_FENCE = re.compile(r"<pre><code(?: class=\"language-([\\w+-]+)\")?>(.*?)</code></pre>", re.DOTALL)
_INLINE_CODE = re.compile(r"<code>(.*?)</code>", re.DOTALL)


def _wrap_page(inner: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{_PAGE_CSS}</style></head><body>{inner}</body></html>"
    )


def highlight_code(code: str, language: str | None = None) -> str:
    """Pygments 语法高亮，返回带内联 token 颜色的 <pre> 片段。"""
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
    try:
        lexer = _KNOWN.get((language or "").lower()) or (
            get_lexer_by_name(language) if language else guess_lexer(code)
        )
    except Exception:  # noqa: BLE001
        lexer = TextLexer()
    body = _pyg_highlight(code, lexer, HtmlFormatter(nowrap=True, noclasses=True))
    return f"<pre style=\"font-family:{_MONO};font-size:13px;\">{body}</pre>"


def _render_markdown(content: str) -> str:
    import markdown as md_lib
    # 直接实例化扩展类（静态导入），避免打包版中按名字加载扩展失败。
    from markdown.extensions.attr_list import AttrListExtension
    from markdown.extensions.def_list import DefListExtension
    from markdown.extensions.fenced_code import FencedCodeExtension
    from markdown.extensions.footnotes import FootnoteExtension
    from markdown.extensions.md_in_html import MarkdownInHtmlExtension
    from markdown.extensions.sane_lists import SaneListExtension
    from markdown.extensions.tables import TableExtension

    extensions = [
        FencedCodeExtension(),
        AttrListExtension(),
        DefListExtension(),
        TableExtension(),
        FootnoteExtension(),
        MarkdownInHtmlExtension(),
        SaneListExtension(),
    ]
    html = md_lib.markdown(content, extensions=extensions)

    def replace_block(match: re.Match) -> str:
        language = (match.group(1) or "").strip() or None
        raw = html_mod.unescape(match.group(2))
        return highlight_code(raw, language)

    html = _CODE_FENCE.sub(replace_block, html)
    html = _INLINE_CODE.sub(
        lambda m: f"<code style=\"color:#b02f60;\">{m.group(1)}</code>",
        html,
    )
    return _wrap_page(html)


def _render_json(content: str) -> str:
    warn = ""
    try:
        pretty = json_pretty(content)
    except JsonFormatError as error:
        pretty = content
        warn = f"<div class='warn'>JSON 无效：{html_mod.escape(str(error))}</div>"
    return _wrap_page(warn + highlight_code(pretty, "json"))


def _render_txt(content: str) -> str:
    escaped = html_mod.escape(content, quote=False)
    return _wrap_page(f"<pre style='font-family:{_MONO};font-size:13px;white-space:pre-wrap;'>{escaped}</pre>")


def _make_preview_widget(parent: QWidget) -> QWidget:
    """创建预览控件：优先 QtWebEngine（完整 CSS），无头环境回退 QTextBrowser。"""
    if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView

            view = QWebEngineView(parent)
            settings = view.settings()
            from PySide6.QtWebEngineCore import QWebEngineSettings

            settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, False)
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
            view._preview_engine = "Chromium"  # type: ignore[attr-defined]
            return view
        except Exception:  # noqa: BLE001
            pass
    browser = QTextBrowser(parent)
    browser.setOpenExternalLinks(False)
    browser._preview_engine = "兼容文本预览"  # type: ignore[attr-defined]
    return browser



class DocViewerDialog(QDialog):
    """统一文档查看器。"""

    def __init__(self, file_path: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._path = Path(file_path)
        self._saved = ""
        self._dirty = False

        self.setWindowTitle(f"查看：{self._path.name}")
        self.resize(960, 700)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_header())
        layout.addWidget(self._build_meta())

        if self._path.suffix.lower() == ".mp4":
            self._build_media(layout)
        else:
            self._build_text(layout)

        layout.addWidget(self._build_footer())

        self._load()
        self._refresh_title()

    # ---------- 结构 ----------

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("viewerHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(16, 11, 16, 11)
        row.setSpacing(8)

        self._name_label = QLabel(self._path.name)
        self._name_label.setObjectName("viewerName")
        row.addWidget(self._name_label)
        row.addStretch(1)

        if self._path.suffix.lower() != ".mp4":
            segmented = QWidget()
            segmented.setObjectName("viewerSegmented")
            seg = QHBoxLayout(segmented)
            seg.setContentsMargins(3, 3, 3, 3)
            seg.setSpacing(2)
            self._mode_btn = QPushButton("预览")
            self._edit_btn = QPushButton("编辑")
            for btn in (self._mode_btn, self._edit_btn):
                btn.setObjectName("viewerTool")
            self._mode_btn.setProperty("active", True)
            self._edit_btn.setProperty("active", False)
            self._mode_btn.clicked.connect(lambda: self._set_mode("preview"))
            self._edit_btn.clicked.connect(lambda: self._set_mode("edit"))
            seg.addWidget(self._mode_btn)
            seg.addWidget(self._edit_btn)
            row.addWidget(segmented)
        else:
            self._mode_btn = QPushButton("预览")
            self._mode_btn.setObjectName("viewerTool")
            self._mode_btn.setEnabled(False)

        self._format_btn = QPushButton("JSON 美化")
        self._format_btn.setObjectName("viewerTool")
        self._format_btn.clicked.connect(self._format_json)
        self._format_btn.setVisible(self._path.suffix.lower() == ".json")
        row.addWidget(self._format_btn)

        self._save_btn = QPushButton("保存")
        self._save_btn.setObjectName("viewerTool")
        self._save_btn.setProperty("primary", True)
        self._save_btn.clicked.connect(self._save)
        self._save_btn.setEnabled(self._path.suffix.lower() != ".mp4")
        self._save_as_btn = QPushButton("另存为")
        self._save_as_btn.setObjectName("viewerTool")
        self._save_as_btn.clicked.connect(self._save_as)
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("viewerTool")
        close_btn.clicked.connect(self._request_close)
        row.addWidget(self._save_btn)
        row.addWidget(self._save_as_btn)
        row.addWidget(close_btn)
        return header

    def _build_meta(self) -> QWidget:
        meta = QWidget()
        meta.setObjectName("viewerMeta")
        row = QHBoxLayout(meta)
        row.setContentsMargins(18, 6, 18, 6)
        row.setSpacing(16)
        labels = [str(self._path), "", "", ""]
        self._meta_labels: list[QLabel] = []
        for text in labels:
            label = QLabel(text)
            label.setObjectName("viewerMetaLabel")
            self._meta_labels.append(label)
            row.addWidget(label)
        row.addStretch(1)
        return meta

    def _build_text(self, layout: QVBoxLayout) -> None:
        splitter = QSplitter(Qt.Orientation.Vertical)
        self._preview = _make_preview_widget(self)
        engine = getattr(self._preview, "_preview_engine", "未知")
        self._editor = QPlainTextEdit()
        self._editor.hide()
        self._editor.textChanged.connect(self._notify_edit)
        splitter.addWidget(self._preview)
        splitter.addWidget(self._editor)
        layout.addWidget(splitter, 1)
        self._engine = engine

    def _build_media(self, layout: QVBoxLayout) -> None:
        from PySide6.QtMultimedia import QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget

        self._player = QMediaPlayer(self)
        self._video = QVideoWidget(self)
        self._player.setVideoOutput(self._video)
        layout.addWidget(self._video, 1)
        self._engine = "视频"

    def _build_footer(self) -> QWidget:
        footer = QWidget()
        footer.setObjectName("viewerFooter")
        row = QHBoxLayout(footer)
        row.setContentsMargins(18, 7, 18, 7)
        row.setSpacing(12)
        self._dirty_label = QLabel("")
        self._dirty_label.setObjectName("viewerDirty")
        self._hint_label = QLabel("")
        self._hint_label.setObjectName("viewerHint")
        row.addWidget(self._dirty_label)
        row.addStretch(1)
        row.addWidget(self._hint_label)
        return footer

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
                self._hint_label.setText("本地视频预览（只读）；可用「另存为」复制该文件。")
                self._update_meta()
                return
            content = read_text_smart(self._path)
            self._saved = content
            self._editor.setPlainText(content)
            self._update_meta()
            self._render_preview()
        except Exception as error:  # noqa: BLE001
            self._saved = ""
            self._editor.setPlainText("")
            self._preview.setHtml(f"<p style='color:#b3262b;'>读取失败：{error}</p>")

    def _update_meta(self) -> None:
        size = self._path.stat().st_size if self._path.exists() else 0

        def fmt_size(n: int) -> str:
            if n < 1024:
                return f"{n} B"
            if n < 1024 * 1024:
                return f"{n / 1024:.1f} KB"
            return f"{n / 1024 / 1024:.1f} MB"

        texts = [str(self._path), fmt_size(size), "", ""]
        for label, text in zip(self._meta_labels, texts):
            label.setText(text)

    def _current_text(self) -> str:
        return self._editor.toPlainText() if self._editor.isVisible() else self._saved

    def _render_preview(self) -> None:
        content = self._saved
        ext = self._path.suffix.lower()
        if ext == ".md":
            self._preview.setHtml(_render_markdown(content))
        elif ext == ".json":
            self._preview.setHtml(_render_json(content))
        else:
            self._preview.setHtml(_render_txt(content))
        self._hint_label.setText(
            f"预览引擎：{self._engine}（预览只读；切换到「编辑」后可修改，Ctrl+S 保存）"
        )

    def _set_mode(self, mode: str) -> None:
        editing = mode == "edit"
        self._editor.setVisible(editing)
        self._preview.setVisible(not editing)
        for btn, active in ((self._mode_btn, not editing), (self._edit_btn, editing)):
            btn.setProperty("active", active)
            style = btn.style()
            style.unpolish(btn)
            style.polish(btn)
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
                self._set_mode("edit")
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
        self._name_label.setText(self._path.name + marker)
        self._dirty_label.setText("● 未保存的修改" if self._dirty else "")
        self.setWindowTitle(f"查看：{self._path.name}{marker}")

    def _request_close(self) -> None:
        if self._dirty and self._saved != self._current_text():
            answer = QMessageBox.question(
                self, "未保存的修改", "当前文档有未保存的修改，关闭将丢失。是否放弃修改并关闭？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.close()

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
