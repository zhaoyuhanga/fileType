"""大模型设置页：按类型管理多份配置，支持优先级排序、启用/停用、连通性测试。

为什么要"按类型 + 多配置"：同一个能力（比如文字）可能有多个可用服务，
主用的挂了/超限了要能自动换下一个。这里就是那份"候选名单"的编辑界面：
左侧选类型，中间是候选列表（从上到下 = 调用优先级），右侧编辑选中项的参数。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.llm import (
    KIND_DESCRIPTIONS,
    KIND_LABELS,
    KIND_TEXT,
    PROVIDER_PRESETS,
    LlmStorage,
    ModelProfile,
    ModelRouter,
    kind_label,
    provider_preset,
)
from modu_workbench.services import app_context

from .base import SettingsPage

ITEM_ROLE = Qt.ItemDataRole.UserRole
KIND_ROLE = Qt.ItemDataRole.UserRole + 1


class LlmSettingsPage(SettingsPage):
    title = "大模型"
    icon = "🤖"

    def __init__(self, parent=None):  # noqa: ANN001
        super().__init__(parent)
        self._storage: LlmStorage = app_context.llm_storage()
        self._router: ModelRouter = app_context.llm_router()
        self._kind = KIND_TEXT
        self._current: ModelProfile | None = None
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title = QLabel("大模型配置")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        note = QLabel(
            "按<b>类型</b>分别配置：<b>文字大模型</b>用于关键词/参数建议，"
            "<b>图片大模型</b>用于看图生成描述与标签，<b>视频大模型</b>预留给后续视频理解。"
            "同一类型可以添加<b>多份配置</b>，列表从上到下就是<b>调用优先级</b> —— "
            "前一个调用失败（鉴权/限流/超时/模型不可用）会<b>自动降级</b>到下一个。"
        )
        note.setWordWrap(True)
        note.setObjectName("readerStatus")
        layout.addWidget(note)

        body = QHBoxLayout()
        body.setSpacing(12)

        # ---------------- 左：类型 ----------------
        kinds_box = QVBoxLayout()
        kinds_box.setSpacing(6)
        kinds_box.addWidget(QLabel("模型类型"))
        self._kinds = QListWidget()
        self._kinds.setFixedWidth(170)
        for key, label in KIND_LABELS.items():
            entry = QListWidgetItem(label)
            entry.setData(KIND_ROLE, key)
            self._kinds.addItem(entry)
        self._kinds.setCurrentRow(0)
        self._kinds.currentItemChanged.connect(lambda *_: self._on_kind_changed())
        kinds_box.addWidget(self._kinds, 1)
        body.addLayout(kinds_box)

        # ---------------- 中：候选列表 ----------------
        list_box = QVBoxLayout()
        list_box.setSpacing(6)
        header = QHBoxLayout()
        self._list_title = QLabel("候选列表")
        header.addWidget(self._list_title, 1)
        add_button = QPushButton("＋ 添加")
        add_button.setObjectName("primaryButton")
        add_button.clicked.connect(self._add_profile)
        header.addWidget(add_button)
        list_box.addLayout(header)

        self._profiles = QListWidget()
        self._profiles.setMinimumWidth(300)
        self._profiles.currentItemChanged.connect(lambda *_: self._on_profile_changed())
        self._profiles.itemChanged.connect(self._on_item_toggled)
        list_box.addWidget(self._profiles, 1)

        move_row = QHBoxLayout()
        for label, delta, tip in (("↑ 上移", -1, "提高调用优先级"),
                                  ("↓ 下移", 1, "降低调用优先级")):
            button = QPushButton(label)
            button.setToolTip(tip)
            button.clicked.connect(lambda _=False, d=delta: self._move(d))
            move_row.addWidget(button)
        delete_button = QPushButton("删除")
        delete_button.setObjectName("dangerButton")
        delete_button.clicked.connect(self._delete_profile)
        move_row.addWidget(delete_button)
        move_row.addStretch(1)
        list_box.addLayout(move_row)

        self._order_label = QLabel("")
        self._order_label.setObjectName("readerStatus")
        self._order_label.setWordWrap(True)
        list_box.addWidget(self._order_label)
        body.addLayout(list_box, 1)

        # ---------------- 右：参数 ----------------
        form_box = QVBoxLayout()
        form_box.setSpacing(6)
        form_box.addWidget(QLabel("配置详情"))

        self._form = QFormLayout()
        self._form.setSpacing(8)

        self._name = QLineEdit()
        self._name.setPlaceholderText("给这份配置起个名字，例如：主力 DeepSeek")
        self._form.addRow("名称", self._name)

        self._provider = QComboBox()
        for preset in PROVIDER_PRESETS:
            self._provider.addItem(preset.label, preset.key)
        self._provider.currentIndexChanged.connect(self._on_provider_changed)
        self._form.addRow("服务商", self._provider)

        self._base_url = QLineEdit()
        self._base_url.setPlaceholderText("https://api.deepseek.com/v1")
        self._form.addRow("接口地址", self._base_url)

        self._api_key = QLineEdit()
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setPlaceholderText("sk-...（只存在本机 llm.db；也可用环境变量）")
        self._form.addRow("API Key", self._api_key)

        self._model = QLineEdit()
        self._form.addRow("模型名", self._model)

        self._vision_model = QLineEdit()
        self._vision_model.setToolTip("图片类型用它调用；文本类型留空即可")
        self._form.addRow("多模态模型名", self._vision_model)

        self._temperature = QSpinBox()
        self._temperature.setRange(0, 20)
        self._temperature.setSuffix(" / 10")
        self._form.addRow("随机度", self._temperature)

        self._max_tokens = QSpinBox()
        self._max_tokens.setRange(64, 32768)
        self._form.addRow("最大输出", self._max_tokens)

        self._timeout = QSpinBox()
        self._timeout.setRange(5, 600)
        self._timeout.setSuffix(" 秒")
        self._form.addRow("超时", self._timeout)

        self._enabled = QCheckBox("启用（只有启用的配置会参与调用与降级）")
        self._form.addRow("状态", self._enabled)

        self._note = QLineEdit()
        self._note.setPlaceholderText("备注（可选）")
        self._form.addRow("备注", self._note)

        form_box.addLayout(self._form)

        buttons = QHBoxLayout()
        apply_button = QPushButton("保存这份配置")
        apply_button.setObjectName("primaryButton")
        apply_button.clicked.connect(self._apply_profile)
        buttons.addWidget(apply_button)
        test_button = QPushButton("测试这个配置")
        test_button.clicked.connect(self._test_current)
        buttons.addWidget(test_button)
        buttons.addStretch(1)
        form_box.addLayout(buttons)

        self._status = QLabel("")
        self._status.setObjectName("readerStatus")
        self._status.setWordWrap(True)
        form_box.addWidget(self._status)

        self._usage = QLabel("")
        self._usage.setObjectName("readerStatus")
        self._usage.setWordWrap(True)
        form_box.addWidget(self._usage)
        form_box.addStretch(1)

        holder = QWidget()
        holder.setLayout(form_box)
        holder.setMinimumWidth(340)
        body.addWidget(holder, 1)

        layout.addLayout(body, 1)
        self._reload_profiles()

    # ------------------------------------------------------------------ 列表

    @property
    def kind(self) -> str:
        entry = self._kinds.currentItem()
        return str(entry.data(KIND_ROLE)) if entry is not None else KIND_TEXT

    def _reload_profiles(self, select_id: int = 0) -> None:
        self._loading = True
        self._profiles.clear()
        profiles = self._storage.list_profiles(self.kind, enabled_only=False)
        for position, profile in enumerate(profiles, start=1):
            entry = QListWidgetItem(f"{position}. {profile.label}")
            entry.setData(ITEM_ROLE, profile.id)
            entry.setFlags(entry.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            entry.setCheckState(Qt.CheckState.Checked if profile.enabled
                                else Qt.CheckState.Unchecked)
            entry.setToolTip(profile.model or "未填模型名")
            self._profiles.addItem(entry)
        self._loading = False

        self._order_label.setText(self._router.describe(self.kind))
        self._list_title.setText(f"{kind_label(self.kind)} · 共 {len(profiles)} 份配置")
        if profiles:
            target = 0
            if select_id:
                for row in range(self._profiles.count()):
                    if self._profiles.item(row).data(ITEM_ROLE) == select_id:
                        target = row
                        break
            self._profiles.setCurrentRow(target)
        else:
            self._current = None
            self._fill_form(None)

    def _on_kind_changed(self) -> None:
        self._status.setText(KIND_DESCRIPTIONS.get(self.kind, ""))
        self._reload_profiles()

    def _on_profile_changed(self) -> None:
        entry = self._profiles.currentItem()
        if entry is None:
            self._current = None
            self._fill_form(None)
            return
        profile = self._storage.get_profile(int(entry.data(ITEM_ROLE)))
        self._current = profile
        self._fill_form(profile)

    def _on_item_toggled(self, entry: QListWidgetItem) -> None:
        if self._loading:
            return
        profile_id = int(entry.data(ITEM_ROLE))
        enabled = entry.checkState() == Qt.CheckState.Checked
        self._storage.set_enabled(profile_id, enabled)
        profile = self._storage.get_profile(profile_id)
        if profile is not None and self._current is not None and profile.id == self._current.id:
            self._current = profile
            self._enabled.setChecked(enabled)
        self._order_label.setText(self._router.describe(self.kind))

    # ------------------------------------------------------------------ 表单

    def _fill_form(self, profile: ModelProfile | None) -> None:
        self._loading = True
        has = profile is not None
        for widget in (self._name, self._base_url, self._api_key, self._model,
                       self._vision_model, self._note):
            widget.setEnabled(has)
        for widget in (self._provider, self._temperature, self._max_tokens,
                       self._timeout, self._enabled):
            widget.setEnabled(has)
        if not has:
            for widget in (self._name, self._base_url, self._api_key, self._model,
                           self._vision_model, self._note):
                widget.setText("")
            self._loading = False
            return
        self._name.setText(profile.name)
        index = self._provider.findData(profile.provider)
        self._provider.setCurrentIndex(index if index >= 0 else 0)
        self._base_url.setText(profile.base_url)
        self._api_key.setText(profile.api_key)
        self._model.setText(profile.model)
        self._vision_model.setText(profile.vision_model)
        self._temperature.setValue(int(round(profile.temperature * 10)))
        self._max_tokens.setValue(int(profile.max_tokens))
        self._timeout.setValue(int(profile.timeout))
        self._enabled.setChecked(profile.enabled)
        self._note.setText(profile.note)
        self._loading = False
        self._fill_model_placeholders()

    def _fill_model_placeholders(self) -> None:
        preset = provider_preset(str(self._provider.currentData() or "custom"))
        self._model.setPlaceholderText(preset.text_model or "模型名")
        self._vision_model.setPlaceholderText(preset.image_model or "多模态模型名")
        self._base_url.setPlaceholderText(preset.base_url or "https://…/v1")

    def _on_provider_changed(self) -> None:
        if self._loading:
            return
        preset = provider_preset(str(self._provider.currentData() or "custom"))
        self._fill_model_placeholders()
        # 只填空着的字段，避免覆盖用户已经填好的自定义值
        if preset.base_url and not self._base_url.text().strip():
            self._base_url.setText(preset.base_url)
        if not self._model.text().strip():
            self._model.setText(preset.text_model)
        if not self._vision_model.text().strip():
            self._vision_model.setText(preset.image_model)
        self._status.setText(preset.note)

    def _collect(self, profile: ModelProfile) -> ModelProfile:
        profile.name = self._name.text().strip()
        profile.provider = str(self._provider.currentData() or "custom")
        profile.base_url = self._base_url.text().strip()
        profile.api_key = self._api_key.text().strip()
        profile.model = self._model.text().strip()
        profile.vision_model = self._vision_model.text().strip()
        profile.temperature = self._temperature.value() / 10
        profile.max_tokens = self._max_tokens.value()
        profile.timeout = float(self._timeout.value())
        profile.enabled = self._enabled.isChecked()
        profile.note = self._note.text().strip()
        return profile

    # ------------------------------------------------------------------ 动作

    def _add_profile(self) -> None:
        preset = PROVIDER_PRESETS[0]
        profile = ModelProfile(
            kind=self.kind, name="", provider=preset.key, base_url=preset.base_url,
            model=preset.text_model, vision_model=preset.image_model,
            priority=self._storage.next_priority(self.kind),
        )
        profile_id = self._storage.save_profile(profile)
        self._reload_profiles(select_id=profile_id)
        self._status.setText("已添加一份配置；填好 API Key 与模型名后点「保存这份配置」。")

    def _apply_profile(self) -> None:
        if self._current is None:
            self._status.setText("先在上面的列表里选中一份配置。")
            return
        profile = self._collect(self._current)
        self._storage.save_profile(profile)
        self._current = self._storage.get_profile(profile.id)
        self._reload_profiles(select_id=profile.id)
        self._status.setText(f"已保存：{profile.label}")

    def _move(self, delta: int) -> None:
        if self._current is None:
            self._status.setText("先选中一份配置。")
            return
        if self._storage.move_profile(self._current.id, delta):
            self._reload_profiles(select_id=self._current.id)
        else:
            self._status.setText("已经在最前/最后了。")

    def _delete_profile(self) -> None:
        if self._current is None:
            return
        answer = QMessageBox.question(
            self, "删除配置", f"确定删除「{self._current.label}」？", 
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._storage.delete_profile(self._current.id)
        self._current = None
        self._reload_profiles()
        self._status.setText("已删除。")

    def _test_current(self) -> None:
        if self._current is None:
            self._status.setText("先选中一份配置。")
            return
        profile = self._collect(self._current)
        if not profile.configured:
            self._status.setText("请先填写 API Key 与模型名。")
            return
        self._status.setText(f"正在测试 {profile.label} …")
        try:
            reply = self._router.test_profile(profile)
        except Exception as error:  # noqa: BLE001
            self._status.setText(f"测试失败：{error}")
            return
        self._status.setText(f"连接成功，模型回复：{reply.strip()[:60]}")

    # ------------------------------------------------------------------ 载入/保存

    def load(self) -> None:
        # 旧版「图库 → DeepSeek」配置自动迁移成一份文字配置 + 一份图片配置
        try:
            self._migrate_legacy()
        except Exception:  # noqa: BLE001  迁移失败不影响页面可用
            pass
        self._status.setText(KIND_DESCRIPTIONS.get(self.kind, ""))
        self._reload_profiles()
        self._refresh_usage()

    def _migrate_legacy(self) -> None:
        from modu_workbench.core.image import load_ai_config

        try:
            legacy = load_ai_config(app_context.image_storage())
        except Exception:  # noqa: BLE001
            return
        if not legacy.configured:
            return
        self._storage.migrate_legacy(
            api_key=legacy.api_key, base_url=legacy.base_url, model=legacy.model,
            vision_model=legacy.vision_model, temperature=legacy.temperature,
            max_tokens=legacy.max_tokens)

    def _refresh_usage(self) -> None:
        calls = self._storage.recent_calls(limit=5)
        if not calls:
            self._usage.setText("还没有调用记录。调用时会自动按优先级降级，"
                                "这里会显示最近用的是哪一份配置。")
            return
        lines = []
        for call in calls:
            state = "成功" if call["ok"] else "失败"
            lines.append(f"{state} · {call['profile']}")
        self._usage.setText("最近调用：" + "；".join(lines))

    def save(self) -> bool:
        # 每份配置都是显式点「保存这份配置」写库的，这里只把当前编辑中的内容落库，
        # 避免用户改了参数直接点全局「保存」却丢掉。
        if self._current is not None:
            profile = self._collect(self._current)
            self._storage.save_profile(profile)
            self._current = self._storage.get_profile(profile.id)
        return True

    def hint(self) -> str:
        return ("同一类型的多份配置按列表顺序调用，失败会自动降级到下一个；"
                "「图库」页的 AI 描述/标签用的是图片大模型，检索与参数建议用文字大模型。")


__all__ = ["LlmSettingsPage"]
