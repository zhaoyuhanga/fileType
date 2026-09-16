"""墨软影视设置页：下载目录、自动换源、数据源启用与接口地址。

数据源顺序与连通性测试在板块内「源设置」里（那里更顺手）；
这里提供"按板块找设置"时需要的下载与容错开关，以及接口地址一览。
"""
from __future__ import annotations

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

from modu_workbench.app import context as app_context
from modu_workbench.core.platform import paths as config

from .base import SettingsPage, app_settings


class VideoSettingsPage(SettingsPage):
    title = "影视"
    icon = "🎬"

    def __init__(self, parent=None):  # noqa: ANN001
        super().__init__(parent)
        self._settings = app_settings()
        self._registry = app_context.video_registry()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(10)

        self._download_dir = QLineEdit()
        pick = QPushButton("选择…")
        pick.clicked.connect(self._pick_dir)
        row = QHBoxLayout()
        row.addWidget(self._download_dir, 1)
        row.addWidget(pick)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow("下载目录", holder)

        self._auto_switch = QCheckBox("播放/下载失败时自动换源重试")
        form.addRow("容错", self._auto_switch)

        self._compliance = QCheckBox("已阅读并遵守各站点条款（仅用于个人学习）")
        form.addRow("合规", self._compliance)

        ffmpeg = QLabel("随包 ffmpeg 用于 HLS 合流为 MP4；缺失时下载退化为 .ts")
        ffmpeg.setObjectName("readerStatus")
        ffmpeg.setWordWrap(True)
        form.addRow("下载", ffmpeg)

        library_dir = QLabel(str(config.video_dir()))
        library_dir.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("影视库目录", library_dir)
        layout.addLayout(form)

        layout.addWidget(QLabel("数据源（停用后不参与搜索与自动换源）"))
        self._source_checks: dict[str, QCheckBox] = {}
        for info in self._registry.infos():
            label = f"{info.label} · {info.kind_label} — 当前：{self._registry.status_text(info.key)}"
            check = QCheckBox(label)
            check.setToolTip(f"{info.note}\n接口：{self._registry.get(info.key).credential or info.homepage or '-'}")
            self._source_checks[info.key] = check
            layout.addWidget(check)

        layout.addStretch(1)
        hint = QLabel(
            "源顺序、改动接口地址、连通性测试在板块内「源设置」里完成；"
            "采集类接口来自第三方站点，可能随时变更。"
        )
        hint.setObjectName("readerStatus")
        hint.setWordWrap(True)
        layout.addWidget(hint)

    def _pick_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(None, "选择视频下载目录",
                                                 self._download_dir.text())
        if folder:
            self._download_dir.setText(folder)

    def load(self) -> None:
        self._download_dir.setText(
            self._settings.value("video/download_dir", "", type=str)
            or str(config.video_download_dir())
        )
        self._auto_switch.setChecked(
            self._settings.value("video/auto_switch", True, type=bool)
        )
        self._compliance.setChecked(
            self._settings.value("video/compliance", True, type=bool)
        )
        for key, check in self._source_checks.items():
            check.setChecked(self._registry.is_enabled(key))

    def save(self) -> bool:
        self._settings.setValue("video/download_dir", self._download_dir.text().strip())
        self._settings.setValue("video/auto_switch", self._auto_switch.isChecked())
        self._settings.setValue("video/compliance", self._compliance.isChecked())
        for key, check in self._source_checks.items():
            self._registry.set_enabled(key, check.isChecked())
        try:
            self._registry.save_settings(app_context.video_storage())
        except Exception:  # noqa: BLE001
            pass
        return True


__all__ = ["VideoSettingsPage"]
