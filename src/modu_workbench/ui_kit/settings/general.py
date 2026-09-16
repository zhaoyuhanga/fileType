"""通用设置页：环境信息、数据目录、全局合规开关。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
)

from modu_workbench.core.convert.media_io import find_ffmpeg, find_ffprobe
from modu_workbench.core.convert.office_io import find_soffice
from modu_workbench.services import config

from .base import SettingsPage, app_settings


class GeneralSettingsPage(SettingsPage):
    title = "通用"
    icon = "🧩"

    def __init__(self, parent=None):  # noqa: ANN001
        super().__init__(parent)
        self._settings = app_settings()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(10)

        self._compliance = QCheckBox("在线书库下载需勾选「个人学习用途」声明")
        form.addRow("在线书库", self._compliance)

        form.addRow("数据目录", self._pickable(str(config.app_data_dir())))
        form.addRow("书库数据库", self._pickable(str(config.library_db_path())))
        form.addRow("乐库数据库", self._pickable(str(config.music_db_path())))
        form.addRow("影视数据库", self._pickable(str(config.video_db_path())))
        form.addRow("图库数据库", self._pickable(str(config.gallery_db_path())))

        ffmpeg = find_ffmpeg()
        ffprobe = find_ffprobe()
        soffice = find_soffice()
        engine = QLabel(
            f"ffmpeg：{ffmpeg or '未找到（音视频转换 / HLS 合流不可用）'}\n"
            f"ffprobe：{ffprobe or '未找到（时长探测不可用）'}\n"
            f"LibreOffice：{soffice or '未找到（Word/Excel→PDF 使用内置兜底）'}"
        )
        engine.setWordWrap(True)
        form.addRow("外部工具", engine)
        layout.addLayout(form)
        layout.addStretch(1)

        hint = QLabel(
            "可用环境变量覆盖：MODU_DATA_DIR / MODU_FFMPEG / MODU_FFPROBE / MODU_SOFFICE / "
            "MODU_MUSIC_DIR / MODU_VIDEO_DIR / MODU_GALLERY_DIR / MODU_DEEPSEEK_KEY。"
        )
        hint.setObjectName("readerStatus")
        hint.setWordWrap(True)
        layout.addWidget(hint)

    @staticmethod
    def _pickable(text: str) -> QLabel:
        label = QLabel(text)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setWordWrap(True)
        return label

    def load(self) -> None:
        self._compliance.setChecked(
            self._settings.value("online/compliance_required", True, type=bool)
        )

    def save(self) -> bool:
        before = self._settings.value("online/compliance_required", True, type=bool)
        after = self._compliance.isChecked()
        self._settings.setValue("online/compliance_required", after)
        return before != after


__all__ = ["GeneralSettingsPage"]
