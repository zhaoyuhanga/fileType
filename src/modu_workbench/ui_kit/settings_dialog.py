"""应用设置对话框（QSettings 持久化）。

- 在线书库合规开关（默认开启）；
- 墨读音乐：下载目录、Jamendo client_id；
- 环境信息：ffmpeg / LibreOffice 检测与数据目录展示。
"""
from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.convert.media_io import find_ffmpeg
from modu_workbench.core.convert.office_io import find_soffice
from modu_workbench.services import config


class SettingsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(520, 460)
        self._settings = QSettings("ModuWorkbench", "modu-workbench")

        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setSpacing(10)

        self._compliance = QCheckBox("在线书库下载需勾选“个人学习用途”声明（默认开启）")
        self._compliance.setChecked(self._settings.value("online/compliance_required", True, type=bool))
        form.addRow("在线书库", self._compliance)

        self._compliance.toggled.connect(lambda checked: self._settings.setValue("online/compliance_required", checked))

        # ---- 墨读音乐 ----
        music_row = QWidget()
        music_layout = QHBoxLayout(music_row)
        music_layout.setContentsMargins(0, 0, 0, 0)
        self._music_dir = QLineEdit(self._settings.value("music/download_dir", "", type=str)
                                    or str(config.music_download_dir()))
        pick = QPushButton("选择…")
        pick.clicked.connect(self._pick_music_dir)
        music_layout.addWidget(self._music_dir, 1)
        music_layout.addWidget(pick)
        form.addRow("音乐下载目录", music_row)

        self._jamendo = QLineEdit(self._settings.value("music/jamendo_client_id", "", type=str))
        self._jamendo.setPlaceholderText("可选：Jamendo 免费 client_id，用于完整曲目下载")
        form.addRow("Jamendo ID", self._jamendo)

        music_dir = QLabel(str(config.music_dir()))
        music_dir.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("音乐库目录", music_dir)

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

        hint = QLabel(
            "提示：可通过环境变量 MODU_DATA_DIR / MODU_FFMPEG / MODU_SOFFICE / MODU_MUSIC_DIR / "
            "MODU_JAMENDO_CLIENT_ID 覆盖对应路径与密钥。"
        )
        hint.setObjectName("readerStatus")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        actions = QHBoxLayout()
        save_btn = QPushButton("保存")
        save_btn.setObjectName("primaryButton")
        save_btn.clicked.connect(self._save)
        actions.addStretch(1)
        actions.addWidget(save_btn)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        actions.addWidget(close_btn)
        layout.addLayout(actions)

    def _pick_music_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择音乐下载目录", self._music_dir.text())
        if folder:
            self._music_dir.setText(folder)

    def _save(self) -> None:
        self._settings.setValue("music/download_dir", self._music_dir.text().strip())
        self._settings.setValue("music/jamendo_client_id", self._jamendo.text().strip())
        self._settings.sync()
        self.accept()
