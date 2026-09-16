"""墨软书库设置页：在线书库合规开关、书库目录与阅读偏好。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QFormLayout, QLabel, QSpinBox, QVBoxLayout

from modu_workbench.core.platform import paths as config

from .base import SettingsPage, app_settings


class BookSettingsPage(SettingsPage):
    title = "书库"
    icon = "📚"

    def __init__(self, parent=None):  # noqa: ANN001
        super().__init__(parent)
        self._settings = app_settings()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(10)

        self._compliance = QCheckBox("下载在线书籍前需勾选「个人学习用途」声明")
        form.addRow("合规", self._compliance)

        self._font_size = QSpinBox()
        self._font_size.setRange(12, 40)
        self._font_size.setSuffix(" px")
        form.addRow("默认字号", self._font_size)

        self._line_spacing = QSpinBox()
        self._line_spacing.setRange(120, 260)
        self._line_spacing.setSuffix(" %")
        form.addRow("行距", self._line_spacing)

        self._remember = QCheckBox("记住每本书的阅读进度")
        form.addRow("阅读进度", self._remember)

        for label, value in (
            ("书库数据库", str(config.library_db_path())),
            ("数据目录", str(config.app_data_dir())),
        ):
            entry = QLabel(value)
            entry.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            entry.setWordWrap(True)
            form.addRow(label, entry)

        layout.addLayout(form)
        layout.addStretch(1)

        hint = QLabel("支持 TXT / EPUB。首次启动会自动迁移旧版 win-e-book 的书库。")
        hint.setObjectName("readerStatus")
        hint.setWordWrap(True)
        layout.addWidget(hint)

    def load(self) -> None:
        self._compliance.setChecked(
            self._settings.value("online/compliance_required", True, type=bool)
        )
        self._font_size.setValue(int(self._settings.value("reader/font_size", 18) or 18))
        self._line_spacing.setValue(int(self._settings.value("reader/line_spacing", 170) or 170))
        self._remember.setChecked(
            self._settings.value("reader/remember_progress", True, type=bool)
        )

    def save(self) -> bool:
        self._settings.setValue("online/compliance_required", self._compliance.isChecked())
        self._settings.setValue("reader/font_size", self._font_size.value())
        self._settings.setValue("reader/line_spacing", self._line_spacing.value())
        self._settings.setValue("reader/remember_progress", self._remember.isChecked())
        return True


__all__ = ["BookSettingsPage"]
