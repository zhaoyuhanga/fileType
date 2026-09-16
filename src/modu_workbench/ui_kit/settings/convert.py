"""墨软转换设置页：输出目录、输出防覆盖、外部转换工具状态。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.platform.media import find_ffmpeg
from modu_workbench.core.convert.office_io import find_soffice

from .base import SettingsPage, app_settings


class ConvertSettingsPage(SettingsPage):
    title = "转换"
    icon = "🔄"

    def __init__(self, parent=None):  # noqa: ANN001
        super().__init__(parent)
        self._settings = app_settings()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(10)

        self._output_dir = QLineEdit()
        self._output_dir.setPlaceholderText("留空则输出到源文件同目录")
        pick = QPushButton("选择…")
        pick.clicked.connect(self._pick_dir)
        row = QHBoxLayout()
        row.addWidget(self._output_dir, 1)
        row.addWidget(pick)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow("默认输出目录", holder)

        self._no_overwrite = QCheckBox("同名输出自动加序号（不覆盖已有文件）")
        form.addRow("输出安全", self._no_overwrite)

        self._keep_original = QCheckBox("转换后保留源文件（默认开启）")
        form.addRow("源文件", self._keep_original)

        ffmpeg = find_ffmpeg()
        soffice = find_soffice()
        engine = QLabel(
            f"ffmpeg：{ffmpeg or '未找到'}\nLibreOffice：{soffice or '未找到（使用内置兜底排版）'}"
        )
        engine.setWordWrap(True)
        engine.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("外部工具", engine)

        layout.addLayout(form)
        layout.addStretch(1)

        hint = QLabel(
            "媒体转换依赖 ffmpeg（已随包分发，缺失时可用 MODU_FFMPEG 指定）；"
            "Word/Excel 转 PDF 的高保真排版依赖 LibreOffice（MODU_SOFFICE）。"
        )
        hint.setObjectName("readerStatus")
        hint.setWordWrap(True)
        layout.addWidget(hint)

    def _pick_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(None, "选择默认输出目录", self._output_dir.text())
        if folder:
            self._output_dir.setText(folder)

    def load(self) -> None:
        self._output_dir.setText(
            str(self._settings.value("convert/output_dir", "", type=str) or "")
        )
        self._no_overwrite.setChecked(
            self._settings.value("convert/no_overwrite", True, type=bool)
        )
        self._keep_original.setChecked(
            self._settings.value("convert/keep_original", True, type=bool)
        )

    def save(self) -> bool:
        text = self._output_dir.text().strip()
        if text and not Path(text).exists():
            try:
                Path(text).mkdir(parents=True, exist_ok=True)
            except OSError:
                text = ""
        self._settings.setValue("convert/output_dir", text)
        self._settings.setValue("convert/no_overwrite", self._no_overwrite.isChecked())
        self._settings.setValue("convert/keep_original", self._keep_original.isChecked())
        return True


__all__ = ["ConvertSettingsPage"]
