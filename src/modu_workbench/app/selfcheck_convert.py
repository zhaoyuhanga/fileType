"""转换与预览子自检：Markdown/JSON 渲染、PDF 导出、QtMultimedia 播放

**为什么拆到这里**：这些检查验的是「转换板块装进包里还能不能真的转/真的播」。它们只用转换板块的公开函数 + 系统 QtMultimedia，与板块内核无关，因此单独成模块：读的人一眼就知道动这里不会影响书库/乐库/影视/图库/文档。

本模块只放 `check_*` 函数（纯函数、无参数、返回 bool 或抛出）。它们由
`app/selfcheck.py` 的 `_CHECKS` 表按固定顺序调用 —— 拆分**不改变任何检查的内容与顺序**，
只是把原来一个 800 多行的 `run_checks` 按「验的是谁」切开，让每一条都能被单独调用与单独测试。
"""
from __future__ import annotations

import os


def check_markdown_extra() -> bool:
    """Markdown 渲染可用（标题带样式属性，因此只断言标签与文字，不写死整串）。

    顺带验证新排版管线（`_qt_style_html` 依赖 bs4）在打包环境里同样生效：
    表格必须被属性化成带底色的 Qt 友好 HTML。
    """
    from modu_workbench.boards.convert.doc_viewer import _render_markdown

    html = _render_markdown("# 标题\n\n正文。\n\n| A | B |\n|---|---|\n| 1 | 2 |\n")
    return "<h1" in html and "标题" in html and "正文" in html and 'bgcolor="#eef1f8"' in html.lower()


def check_markdown_codeblock() -> bool:
    """代码块：保留 <pre> 且被带底色的单元格包住（Qt 单栈下的排版约定）。"""
    from modu_workbench.boards.convert.doc_viewer import _render_markdown

    html = _render_markdown("# 标题\n\n```json\n{\"a\": 1}\n```")
    return "<pre" in html and "#f6f8fc" in html.lower()


def check_json_highlight() -> bool:
    from modu_workbench.boards.convert.doc_viewer import _render_json

    return "color:" in _render_json('{"a": 1}')


def check_lexer_by_name() -> bool:
    from pygments.lexers import get_lexer_by_name

    return get_lexer_by_name("json") is not None


def check_pdf_convert() -> bool:
    """墨软转换：PDF 文本抽取依赖 pypdf（打包时必须随包）。"""
    try:
        from pypdf import PdfWriter, PdfReader

        import io

        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        buffer = io.BytesIO()
        writer.write(buffer)
        buffer.seek(0)
        return len(PdfReader(buffer).pages) == 1
    except Exception:  # noqa: BLE001
        return False


def check_multimedia() -> bool:
    """墨软乐库播放依赖 QtMultimedia（打包必须带上）。"""
    try:
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer  # noqa: F401

        from modu_workbench.core.music import MULTIMEDIA_AVAILABLE

        return bool(MULTIMEDIA_AVAILABLE)
    except Exception:  # noqa: BLE001
        return False


def check_multimedia_playback() -> bool:
    """真实解码自检：播放一段静音 WAV，确认播放位置前进（验证打包后的解码后端）。"""
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return True  # 无头环境不要求音频后端
    import tempfile
    import wave

    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtWidgets import QApplication
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    except Exception:  # noqa: BLE001
        return False

    wav_path = ""
    try:
        # 只要求 QApplication 存在（QMediaPlayer 需要），返回值本身用不上
        QApplication.instance() or QApplication([])
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            wav_path = handle.name
        # 3 秒静音 WAV（纯 Python 生成，无需 ffmpeg；静音避免自检发声）
        with wave.open(wav_path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(22050)
            wav.writeframes(b"\x00\x00" * 22050 * 3)

        player = QMediaPlayer()
        audio = QAudioOutput()
        audio.setVolume(0.5)
        player.setAudioOutput(audio)
        state = {"position": 0, "error": "", "ended": False}
        loop = QEventLoop()

        def on_position(value: int) -> None:
            state["position"] = max(state["position"], int(value))
            if state["position"] > 0:
                loop.quit()

        def on_status(status) -> None:  # noqa: ANN001
            if QMediaPlayer is not None and status == QMediaPlayer.MediaStatus.EndOfMedia:
                state["ended"] = True
                loop.quit()

        player.positionChanged.connect(on_position)
        player.mediaStatusChanged.connect(on_status)
        player.errorOccurred.connect(lambda error, message="": state.update(error=message or str(error)))
        player.setSource(QUrl.fromLocalFile(wav_path))
        player.play()

        QTimer.singleShot(6000, loop.quit)
        loop.exec()
        player.stop()
        return bool((state["position"] > 0 or state["ended"]) and not state["error"])
    except Exception:  # noqa: BLE001
        return False
    finally:
        if wav_path:
            try:
                os.unlink(wav_path)
            except OSError:
                pass


__all__ = [
    "check_markdown_extra",
    "check_markdown_codeblock",
    "check_json_highlight",
    "check_lexer_by_name",
    "check_pdf_convert",
    "check_multimedia",
    "check_multimedia_playback",
]
