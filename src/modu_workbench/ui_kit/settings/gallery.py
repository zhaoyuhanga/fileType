"""墨软图库设置页：目录、缩略图缓存、DeepSeek（AI）配置、隐私开关。

关于 DeepSeek 的说明在这里也必须可见：它只处理**文本**（描述/标签/关键词），
图像优化由本地算法完成 —— 避免用户误以为填个 Key 就能超分抠图。
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

from modu_workbench.core.image import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_VISION_MODEL,
    AiConfig,
    format_size,
    load_ai_config,
    save_ai_config,
)
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

        layout.addLayout(form)

        # ---- DeepSeek ----
        ai_title = QLabel("DeepSeek（AI）配置")
        ai_title.setObjectName("sectionTitle")
        layout.addWidget(ai_title)

        ai_note = QLabel(
            "重要：DeepSeek 的接口只处理文本，<b>不能生成或编辑图片</b>。它在图库里用于"
            "「AI 描述/标签」「自然语言检索」「推荐修图参数」；"
            "超分、抠图、去噪、消除等像素级优化由本地算法完成（图库 → AI 优化）。"
        )
        ai_note.setWordWrap(True)
        ai_note.setObjectName("readerStatus")
        layout.addWidget(ai_note)

        ai_form = QFormLayout()
        ai_form.setSpacing(10)

        self._api_key = QLineEdit()
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setPlaceholderText("sk-...（仅保存在本机数据库；也可用环境变量 MODU_DEEPSEEK_KEY）")
        ai_form.addRow("API Key", self._api_key)

        self._base_url = QLineEdit()
        self._base_url.setPlaceholderText(DEFAULT_BASE_URL)
        ai_form.addRow("接口地址", self._base_url)

        self._model = QLineEdit()
        self._model.setPlaceholderText(DEFAULT_MODEL)
        ai_form.addRow("文本模型", self._model)

        self._vision_model = QLineEdit()
        self._vision_model.setPlaceholderText(DEFAULT_VISION_MODEL)
        self._vision_model.setToolTip("仅当所用模型支持图片输入时才有效；不支持会自动退回元数据模式")
        ai_form.addRow("多模态模型", self._vision_model)

        self._allow_upload = QCheckBox("允许把图片发送到云端用于识别（默认关闭）")
        self._allow_upload.setToolTip("关闭时只发送文件名/EXIF 等元数据，不发送图片本身")
        ai_form.addRow("隐私", self._allow_upload)

        self._temperature = QSpinBox()
        self._temperature.setRange(0, 20)
        self._temperature.setSuffix(" / 10")
        ai_form.addRow("随机度", self._temperature)

        self._max_tokens = QSpinBox()
        self._max_tokens.setRange(64, 8192)
        ai_form.addRow("最大输出", self._max_tokens)

        test_row = QHBoxLayout()
        self._ai_status = QLabel("未测试")
        self._ai_status.setObjectName("readerStatus")
        self._ai_status.setWordWrap(True)
        test_button = QPushButton("测试连接")
        test_button.clicked.connect(self._test_ai)
        test_row.addWidget(self._ai_status, 1)
        test_row.addWidget(test_button)
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
        from modu_workbench.core.image import DeepSeekClient

        config_value = self._collect_ai_config()
        if not config_value.configured:
            self._ai_status.setText("请先填写 API Key")
            return
        self._ai_status.setText("正在测试…")
        try:
            reply = DeepSeekClient(config_value).test_connection()
            self._ai_status.setText(f"连接成功，模型回复：{reply.strip()[:60]}")
        except Exception as error:  # noqa: BLE001
            self._ai_status.setText(f"失败：{error}")

    def _collect_ai_config(self) -> AiConfig:
        return AiConfig(
            api_key=self._api_key.text().strip(),
            base_url=self._base_url.text().strip() or DEFAULT_BASE_URL,
            model=self._model.text().strip() or DEFAULT_MODEL,
            vision_model=self._vision_model.text().strip() or DEFAULT_VISION_MODEL,
            temperature=self._temperature.value() / 10,
            max_tokens=self._max_tokens.value(),
            allow_upload=self._allow_upload.isChecked(),
        )

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
        self._refresh_cache_label()

        ai = load_ai_config(self._library.storage)
        self._api_key.setText(ai.api_key)
        self._base_url.setText(ai.base_url)
        self._model.setText(ai.model)
        self._vision_model.setText(ai.vision_model)
        self._allow_upload.setChecked(ai.allow_upload)
        self._temperature.setValue(int(round(ai.temperature * 10)))
        self._max_tokens.setValue(int(ai.max_tokens))
        self._ai_status.setText("已配置" if ai.configured else "未配置（不影响本地图片功能）")

    def save(self) -> bool:
        self._settings.setValue("gallery/import_dir", self._import_dir.text().strip())
        self._settings.setValue("gallery/columns", self._columns.value())
        self._settings.setValue("gallery/prefetch", self._prefetch.isChecked())
        save_ai_config(self._library.storage, self._collect_ai_config())
        return True

    def hint(self) -> str:
        return ("缓存与数据库都可在「通用」页查看路径；图库数据库换了目录后需重启应用生效。")


__all__ = ["GallerySettingsPage"]
