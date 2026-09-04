"""应用设置对话框（QSettings 持久化）。

- 在线书库合规开关（默认开启）；
- 环境信息：ffmpeg / LibreOffice 检测与数据目录展示。
"""
from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from modu_workbench.core.convert.media_io import find_ffmpeg
from modu_workbench.core.convert.office_io import find_soffice
from modu_workbench.services import config


class SettingsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(460, 320)
        self._settings = QSettings("ModuWorkbench", "modu-workbench")

        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setSpacing(10)

        self._compliance = QCheckBox("在线书库下载需勾选“个人学习用途”声明（默认开启）")
        self._compliance.setChecked(self._settings.value("online/compliance_required", True, type=bool))
        form.addRow("在线书库", self._compliance)

        self._compliance.toggled.connect(lambda checked: self._settings.setValue("online/compliance_required", checked))

        data_dir = QLabel(str(config.app_data_dir()))
        data_dir.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("数据目录", data_dir)

        ffmpeg = find_ffmpeg()
        soffice = find_soffice()
        engine_info = QLabel(
            f"ffmpeg：{'可用（' + ffmpeg + '）' if ffmpeg else '未安装（媒体转换不可用）'}\n"
            f"LibreOffice：{'可用' if soffice else '未安装（Word/Excel→PDF 使用内置兜底）'}"
        )
        engine_info.setWordWrap(True)
        form.addRow("转换引擎", engine_info)
        layout.addLayout(form)
        layout.addStretch(1)

        hint = QLabel("提示：可通过环境变量 MODU_DATA_DIR / MODU_FFMPEG / MODU_SOFFICE 覆盖对应路径。")
        hint.setObjectName("readerStatus")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        actions = QHBoxLayout()
        actions.addStretch(1)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        actions.addWidget(close_btn)
        layout.addLayout(actions)
