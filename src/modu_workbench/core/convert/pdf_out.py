"""Qt 文本 → PDF（中文字体跟随系统，跨平台）。"""
from __future__ import annotations

import os
from pathlib import Path


def render_text_pdf(text: str, output_path: str | Path, title: str = "") -> None:
    """把纯文本渲染为 PDF（A4，上下留白，自动分页）。

    说明：依赖 Qt GUI（QTextDocument），调用方应保证存在 QGuiApplication。
    """
    from PySide6.QtGui import QFont, QPageSize, QTextDocument
    from PySide6.QtPrintSupport import QPrinter

    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(output_path))
    printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))

    doc = QTextDocument()
    doc.setDefaultFont(QFont(_font_family(), 11))
    body = text if text else title
    doc.setPlainText(body)
    doc.print_(printer)


def _font_family() -> str:
    if os.name == "nt":
        return "Microsoft YaHei"
    if os.path.exists("/System/Library/Fonts/PingFang.ttc"):
        return "PingFang SC"
    return "Noto Sans CJK SC"
