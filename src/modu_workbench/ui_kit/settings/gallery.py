"""墨软图库设置页：目录、缩略图缓存、分页、AI 隐私开关与模型状态。

关于大模型的说明在这里也必须可见：它只处理**文本**（描述/标签/关键词），
图像优化由本地算法完成 —— 避免用户误以为填个 Key 就能超分抠图。
API Key/模型/优先级统一放在「大模型」页（可配多份并降级调用）。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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

from modu_workbench.core.gallery import format_size, load_ai_config, save_ai_config
from modu_workbench.core.llm import KIND_IMAGE, KIND_TEXT, kind_label
from modu_workbench.services import app_context, config

from .base import SettingsPage, app_settings


class GallerySettingsPage(SettingsPage):
    title = "图库"
    icon = "🖼"

    def __init__(self, parent=None):  # noqa: ANN001
        super().__init__(parent)
        self._settings = app_settings()
        self._library = app_context.image_library()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(10)

        # ---- 目录 ----
        self._import_dir = QLineEdit()
        import_row = QHBoxLayout()
        import_row.addWidget(self._import_dir, 1)
        pick = QPushButton("选择…")
        pick.clicked.connect(lambda: self._pick_dir(self._import_dir, "选择默认导入目录"))
        import_row.addWidget(pick)
        holder = QWidget()
        holder.setLayout(import_row)
        form.addRow("默认导入目录", holder)

        library_dir = QLineEdit(str(config.gallery_dir()))
        library_dir.setReadOnly(True)
        form.addRow("图库目录", library_dir)

        cache_dir = QLineEdit(str(config.gallery_cache_dir()))
        cache_dir.setReadOnly(True)
        form.addRow("缩略图缓存", cache_dir)

        self._cache_label = QLabel("")
        self._cache_label.setObjectName("readerStatus")
        clear_cache = QPushButton("清空缩略图缓存")
        clear_cache.setToolTip("可安全清理，下次浏览时按需重新生成")
        clear_cache.clicked.connect(self._clear_cache)
        cache_row = QHBoxLayout()
        cache_row.addWidget(self._cache_label, 1)
        cache_row.addWidget(clear_cache)
        cache_holder = QWidget()
        cache_holder.setLayout(cache_row)
        form.addRow("缓存占用", cache_holder)

        # ---- 浏览偏好 ----
        self._columns = QSpinBox()
        self._columns.setRange(2, 8)
        form.addRow("默认列数", self._columns)

        self._prefetch = QCheckBox("导入后预生成缩略图（滚动更流畅，导入稍慢）")
        form.addRow("缩略图", self._prefetch)

        self._page_size = QSpinBox()
        self._page_size.setRange(30, 600)
        self._page_size.setSingleStep(30)
        self._page_size.setSuffix(" 张 / 页")
        self._page_size.setToolTip("图库网格分页大小：每页越少越流畅，翻页越频繁")
        form.addRow("每页张数", self._page_size)

        layout.addLayout(form)

        # ---- AI / 大模型 ----
        ai_title = QLabel("AI（大模型）")
        ai_title.setObjectName("sectionTitle")
        layout.addWidget(ai_title)

        ai_note = QLabel(
            "重要：大模型接口只处理文本，<b>不能生成或编辑图片</b>。图库用它做"
            "「AI 描述/标签」「自然语言检索」「推荐修图参数」；"
            "超分、抠图、去噪、消除等像素级优化由本地算法完成（图库 → AI 优化）。<br>"
            "API Key、模型、优先级都在 <b>设置 → 大模型</b> 里统一管理"
            "（同一类型可配多份，调用失败会自动降级）。"
        )
        ai_note.setWordWrap(True)
        ai_note.setObjectName("readerStatus")
        layout.addWidget(ai_note)

        ai_form = QFormLayout()
        ai_form.setSpacing(10)

        self._llm_status = QLabel("")
        self._llm_status.setObjectName("readerStatus")
        self._llm_status.setWordWrap(True)
        ai_form.addRow("当前模型", self._llm_status)

        self._allow_upload = QCheckBox("允许把图片发送到云端用于识别（默认关闭）")
        self._allow_upload.setToolTip("关闭时只发送文件名/EXIF 等元数据，不发送图片本身")
        ai_form.addRow("隐私", self._allow_upload)

        self._vision_enabled = QCheckBox("优先用图片大模型看图（需已配置图片类型）")
        self._vision_enabled.setToolTip("关闭后只用元数据生成标签，绝不上传图片")
        ai_form.addRow("看图识别", self._vision_enabled)

        test_row = QHBoxLayout()
        self._ai_status = QLabel("未测试")
        self._ai_status.setObjectName("readerStatus")
        self._ai_status.setWordWrap(True)
        test_button = QPushButton("测试连接")
        test_button.clicked.connect(self._test_ai)
        open_button = QPushButton("打开大模型设置…")
        open_button.clicked.connect(self._open_llm_settings)
        test_row.addWidget(self._ai_status, 1)
        test_row.addWidget(test_button)
        test_row.addWidget(open_button)
        test_holder = QWidget()
        test_holder.setLayout(test_row)
        ai_form.addRow("连通性", test_holder)

        layout.addLayout(ai_form)
        layout.addStretch(1)

    # ------------------------------------------------------------------ 动作

    @staticmethod
    def _pick_dir(field: QLineEdit, title: str) -> None:
        folder = QFileDialog.getExistingDirectory(None, title, field.text())
        if folder:
            field.setText(folder)

    def _clear_cache(self) -> None:
        removed = self._library.thumbs.clear_disk()
        self._refresh_cache_label()
        self._settings.setValue("gallery/cache_cleared", removed)

    def _refresh_cache_label(self) -> None:
        used = self._library.thumbs.disk_usage()
        self._cache_label.setText(f"{format_size(used)}（可安全清理）")

    def _test_ai(self) -> None:
        router = app_context.llm_router()
        kind = KIND_IMAGE if self._vision_enabled.isChecked() else KIND_TEXT
        if not router.has_any(kind):
            self._ai_status.setText(
                f"没有可用的{kind_label(kind)}：请到「设置 → 大模型」添加配置")
            return
        self._ai_status.setText(f"正在测试 {kind_label(kind)}（按优先级依次尝试）…")
        try:
            _text, profile = router.chat(
                kind, [{"role": "user", "content": "回复两个字：可用"}])
        except Exception as error:  # noqa: BLE001
            self._ai_status.setText(f"失败：{error}")
            return
        self._ai_status.setText(f"连接成功（{profile.label}）")

    def _open_llm_settings(self) -> None:
        """直接从图库页跳到「大模型」页，省得用户自己找。"""
        from . import SettingsDialog

        dialog = SettingsDialog(self, initial="llm")
        dialog.exec()
        self._refresh_llm_status()

    def _refresh_llm_status(self) -> None:
        router = app_context.llm_router()
        parts = []
        for kind in (KIND_TEXT, KIND_IMAGE):
            usable = router.usable_profiles(kind)
            parts.append(f"{kind_label(kind)} {len(usable)} 个")
        self._llm_status.setText("；".join(parts) + "。可在「大模型」页添加/排序。")

    # ------------------------------------------------------------------ 载入/保存

    def load(self) -> None:
        self._import_dir.setText(
            self._settings.value("gallery/import_dir", "", type=str)
            or str(config.gallery_import_dir())
        )
        self._columns.setValue(int(self._settings.value("gallery/columns", 4) or 4))
        self._prefetch.setChecked(
            self._settings.value("gallery/prefetch", True, type=bool)
        )
        self._page_size.setValue(int(self._settings.value("gallery/page_size", 120) or 120))
        self._refresh_cache_label()
        self._refresh_llm_status()

        ai = load_ai_config(self._library.storage)
        self._allow_upload.setChecked(ai.allow_upload)
        self._vision_enabled.setChecked(
            self._settings.value("gallery/vision", True, type=bool))
        self._ai_status.setText("已配置" if ai.configured else "未配置（不影响本地图片功能）")

    def save(self) -> bool:
        self._settings.setValue("gallery/import_dir", self._import_dir.text().strip())
        self._settings.setValue("gallery/columns", self._columns.value())
        self._settings.setValue("gallery/prefetch", self._prefetch.isChecked())
        self._settings.setValue("gallery/page_size", self._page_size.value())
        self._settings.setValue("gallery/vision", self._vision_enabled.isChecked())
        ai = load_ai_config(self._library.storage)
        ai.allow_upload = self._allow_upload.isChecked()
        save_ai_config(self._library.storage, ai)
        return True

    def hint(self) -> str:
        return ("缓存与数据库都可在「通用」页查看路径；图库数据库换了目录后需重启应用生效。"
                "大模型（Key/模型/优先级）统一在「大模型」页配置。")


__all__ = ["GallerySettingsPage"]
