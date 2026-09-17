"""墨软乐库设置页：下载目录、Jamendo 凭据、音源启用与优先级。

音源的详细拖拽排序在板块内的「源设置」里；这里提供同样的启用开关，
方便"按板块找设置"的用户直接在此完成。
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
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.app import context as app_context
from modu_workbench.core.music.sources.quality import DEFAULT_MIN_FULL_SECONDS
from modu_workbench.core.platform import paths as config

from .base import SettingsPage, app_settings


class MusicSettingsPage(SettingsPage):
    title = "乐库"
    icon = "🎧"

    def __init__(self, parent=None):  # noqa: ANN001
        super().__init__(parent)
        self._settings = app_settings()
        self._registry = app_context.music_registry()

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

        self._jamendo = QLineEdit()
        self._jamendo.setPlaceholderText("可选：Jamendo 免费 client_id")
        form.addRow("Jamendo ID", self._jamendo)

        self._auto_switch = QCheckBox("下载/试听失败时自动换源重试")
        form.addRow("容错", self._auto_switch)

        self._hide_preview = QCheckBox("搜索结果隐藏「试听/片段」条目（推荐）")
        self._hide_preview.setToolTip(
            "酷我等音源会返回同一首歌的片段/铃声/串烧版本（时长常常只有十几秒），\n"
            "它们下载后不能完整播放。勾选后在搜索页默认不显示这类条目。"
        )
        form.addRow("试听过滤", self._hide_preview)

        self._min_full = QSpinBox()
        self._min_full.setRange(15, 600)
        self._min_full.setSuffix(" 秒")
        self._min_full.setToolTip(
            "短于此时长的结果视为「试听/片段」：会被隐藏、排在完整曲目之后，\n"
            "下载时也会直接换源去找完整版（实测酷我片段多为 10~40 秒）"
        )
        form.addRow("完整曲目最短时长", self._min_full)

        library_dir = QLabel(str(config.music_dir()))
        library_dir.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("乐库目录", library_dir)
        layout.addLayout(form)

        layout.addWidget(QLabel("音源（停用后不参与搜索与自动换源）"))
        self._source_checks: dict[str, QCheckBox] = {}
        for info in self._registry.infos():
            check = QCheckBox(
                f"{info.label} · {info.kind_label} — 当前：{self._registry.status_text(info.key)}"
            )
            check.setToolTip(info.note)
            self._source_checks[info.key] = check
            layout.addWidget(check)

        layout.addStretch(1)
        hint = QLabel("音源顺序（跨源兜底优先级）在板块内「源设置」里调整。")
        hint.setObjectName("readerStatus")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self._jamendo.textChanged.connect(self._sync_jamendo)

    def _pick_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(None, "选择音乐下载目录",
                                                 self._download_dir.text())
        if folder:
            self._download_dir.setText(folder)

    def _sync_jamendo(self) -> None:
        check = self._source_checks.get("jamendo")
        if check is not None:
            has_key = bool(self._jamendo.text().strip())
            check.setEnabled(has_key)
            if not has_key:
                check.setChecked(False)

    def load(self) -> None:
        self._download_dir.setText(
            self._settings.value("music/download_dir", "", type=str)
            or str(config.music_download_dir())
        )
        self._jamendo.setText(self._settings.value("music/jamendo_client_id", "", type=str))
        self._auto_switch.setChecked(
            self._settings.value("music/auto_switch", True, type=bool)
        )
        self._hide_preview.setChecked(
            self._settings.value("music/hide_preview", True, type=bool)
        )
        self._min_full.setValue(
            int(self._settings.value("music/min_full_seconds",
                                     DEFAULT_MIN_FULL_SECONDS) or DEFAULT_MIN_FULL_SECONDS)
        )
        for key, check in self._source_checks.items():
            check.setChecked(self._registry.is_enabled(key))
        self._sync_jamendo()

    def save(self) -> bool:
        self._settings.setValue("music/download_dir", self._download_dir.text().strip())
        self._settings.setValue("music/jamendo_client_id", self._jamendo.text().strip())
        self._settings.setValue("music/auto_switch", self._auto_switch.isChecked())
        self._settings.setValue("music/hide_preview", self._hide_preview.isChecked())
        self._settings.setValue("music/min_full_seconds", self._min_full.value())
        # 立刻生效：核心判定用新阈值（无需重启）
        from modu_workbench.core.music.sources.quality import set_min_full_seconds

        set_min_full_seconds(self._min_full.value())
        for key, check in self._source_checks.items():
            self._registry.set_enabled(key, check.isChecked())
        provider = self._registry.get("jamendo")
        if provider is not None:
            provider.set_credential(self._jamendo.text().strip())
        try:
            self._registry.save_settings(app_context.music_storage())
        except Exception:  # noqa: BLE001
            pass
        return True


__all__ = ["MusicSettingsPage"]
