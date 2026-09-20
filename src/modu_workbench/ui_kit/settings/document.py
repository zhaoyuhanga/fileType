"""墨软文档设置页：输出目录、默认美化参数、OCR/外部工具状态、AI 默认权限。

与其它板块一致：这一页只管"本板块"的配置（`document/*` 键），
API Key 与模型优先级仍在「大模型」页统一管理；权限开关放这里是因为它属于
"文档能不能上传"的产品策略，而不是模型本身的配置。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.document import (
    SCENES,
    mask_rule_choices,
    permissions_from_settings,
    summary_text,
    template_choices,
)
from modu_workbench.core.document import ocr as document_ocr
from modu_workbench.core.document.parser import describe_support
from modu_workbench.core.llm import KIND_TEXT, kind_label
from modu_workbench.core.llm.context import llm_router

from .base import SettingsPage, app_settings

FONT_CHOICES = ("宋体", "仿宋_GB2312", "黑体", "微软雅黑", "方正小标宋简体", "楷体")


class DocumentSettingsPage(SettingsPage):
    title = "文档"
    icon = "📄"

    def __init__(self, parent=None):  # noqa: ANN001
        super().__init__(parent)
        self._settings = app_settings()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        # ---- 目录 ----
        form = QFormLayout()
        form.setSpacing(10)
        self._output_dir = QLineEdit()
        self._output_dir.setPlaceholderText("留空则用数据目录下的 document/")
        pick = QPushButton("选择…")
        pick.clicked.connect(self._pick_dir)
        row = QHBoxLayout()
        row.addWidget(self._output_dir, 1)
        row.addWidget(pick)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow("默认输出目录", holder)
        layout.addLayout(form)

        # ---- 默认美化参数 ----
        style_title = QLabel("默认美化参数（进入板块时生效）")
        style_title.setObjectName("sectionTitle")
        layout.addWidget(style_title)
        style_form = QFormLayout()
        style_form.setSpacing(10)
        self._template = QComboBox()
        for key, label in template_choices():
            self._template.addItem(label, key)
        style_form.addRow("默认模板", self._template)

        self._font_cn = QComboBox()
        self._font_cn.setEditable(True)
        self._font_cn.addItems(FONT_CHOICES)
        style_form.addRow("正文字体", self._font_cn)

        self._body_size = QDoubleSpinBox()
        self._body_size.setRange(6.0, 48.0)
        self._body_size.setSingleStep(0.5)
        self._body_size.setSuffix(" pt")
        style_form.addRow("正文字号", self._body_size)

        self._brand_color = QLineEdit()
        self._brand_color.setPlaceholderText("#1452C4")
        style_form.addRow("品牌色", self._brand_color)

        self._auto_number = QCheckBox("默认自动编号（多级标题）")
        style_form.addRow("编号", self._auto_number)
        self._build_toc = QCheckBox("默认生成目录")
        style_form.addRow("目录", self._build_toc)
        self._beautify_tables = QCheckBox("默认美化表格（边框/表头/斑马纹）")
        style_form.addRow("表格", self._beautify_tables)
        self._freeze_header = QCheckBox("导出 Excel 时冻结表头")
        style_form.addRow("冻结窗格", self._freeze_header)
        self._conditional_format = QCheckBox("导出 Excel 时写「高于平均值」条件格式")
        style_form.addRow("条件格式", self._conditional_format)
        self._data_validation = QCheckBox("导出 Excel 时为取值少的列写下拉数据验证")
        style_form.addRow("数据验证", self._data_validation)

        self._max_versions = QSpinBox()
        self._max_versions.setRange(5, 200)
        self._max_versions.setSuffix(" 个 / 文档")
        self._max_versions.setToolTip("版本快照上限：超出后自动删除最旧的（避免库无限增长）")
        style_form.addRow("版本保留", self._max_versions)

        self._autosave = QCheckBox("自动保存编辑内容（默认开启）")
        self._autosave.setToolTip("只写回原文件，不额外产生版本快照")
        style_form.addRow("自动保存", self._autosave)
        self._autosave_seconds = QSpinBox()
        self._autosave_seconds.setRange(30, 3600)
        self._autosave_seconds.setSingleStep(30)
        self._autosave_seconds.setSuffix(" 秒")
        style_form.addRow("自动保存间隔", self._autosave_seconds)
        layout.addLayout(style_form)

        # ---- 循环美化默认值 ----
        loop_title = QLabel("AI 循环美化默认值（进入板块时生效）")
        loop_title.setObjectName("sectionTitle")
        layout.addWidget(loop_title)
        loop_form = QFormLayout()
        loop_form.setSpacing(10)
        self._max_rounds = QSpinBox()
        self._max_rounds.setRange(1, 10)
        loop_form.addRow("最大轮次", self._max_rounds)
        self._threshold = QDoubleSpinBox()
        self._threshold.setRange(0.5, 1.0)
        self._threshold.setSingleStep(0.01)
        loop_form.addRow("质量阈值", self._threshold)
        self._max_cost = QDoubleSpinBox()
        self._max_cost.setRange(0.0, 1000.0)
        self._max_cost.setSingleStep(0.1)
        self._max_cost.setToolTip("0 = 不限制；在「文档 → 权限安全」里填模型单价后才会累计成本")
        loop_form.addRow("成本上限", self._max_cost)
        self._require_confirm = QCheckBox("每轮结束人工确认后再继续")
        loop_form.addRow("人工确认", self._require_confirm)
        layout.addLayout(loop_form)

        # ---- 权限与脱敏 ----
        privacy_title = QLabel("AI 权限与脱敏")
        privacy_title.setObjectName("sectionTitle")
        layout.addWidget(privacy_title)
        note = QLabel(
            "默认「本地优先」：允许本地模型、不把文档发到云端。"
            "上传前脱敏会在发送前遮住手机号 / 身份证 / 银行卡 / 邮箱 / 金额。"
        )
        note.setObjectName("readerStatus")
        note.setWordWrap(True)
        layout.addWidget(note)

        self._allow_cloud = QCheckBox("允许把文档内容发送到云端模型（默认关闭）")
        layout.addWidget(self._allow_cloud)
        self._allow_local = QCheckBox("允许调用本地模型")
        layout.addWidget(self._allow_local)
        self._mask_before_upload = QCheckBox("上传前自动脱敏")
        layout.addWidget(self._mask_before_upload)

        mask_title = QLabel("默认脱敏规则")
        mask_title.setObjectName("sectionTitle")
        layout.addWidget(mask_title)
        grid = QGridLayout()
        grid.setSpacing(4)
        self._mask_boxes: dict[str, QCheckBox] = {}
        for index, (key, label) in enumerate(mask_rule_choices()):
            box = QCheckBox(label)
            self._mask_boxes[key] = box
            grid.addWidget(box, index // 4, index % 4)
        layout.addLayout(grid)

        # ---- 能力状态 ----
        tools_title = QLabel("能力状态")
        tools_title.setObjectName("sectionTitle")
        layout.addWidget(tools_title)
        self._tools_label = QLabel("")
        self._tools_label.setObjectName("readerStatus")
        self._tools_label.setWordWrap(True)
        self._tools_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._tools_label)

        # ---- 插件（自定义工具）----
        plugin_title = QLabel("插件（自定义工具）")
        plugin_title.setObjectName("sectionTitle")
        layout.addWidget(plugin_title)
        plugin_note = QLabel(
            "插件是插件目录里的普通 Python 文件，定义 <code>register(api)</code> 后可用 "
            "<code>api.register_tool(...)</code> 注册自定义工具，与内置工具同权（AI 也能调用）。"
            "<br>⚠️ 插件是本机可执行代码，默认<b>关闭</b>；加载失败只记录原因，不影响板块其它功能。")
        plugin_note.setObjectName("readerStatus")
        plugin_note.setWordWrap(True)
        layout.addWidget(plugin_note)

        self._enable_plugins = QCheckBox("启用插件目录（勾选后下次进入板块时加载）")
        layout.addWidget(self._enable_plugins)

        self._plugin_dir_field = QLineEdit()
        self._plugin_dir_field.setReadOnly(True)
        plugin_row = QHBoxLayout()
        plugin_row.addWidget(self._plugin_dir_field, 1)
        open_plugin_dir = QPushButton("打开插件目录")
        open_plugin_dir.clicked.connect(self._open_plugin_dir)
        plugin_row.addWidget(open_plugin_dir)
        make_example = QPushButton("生成示例插件")
        make_example.setToolTip("写一个能跑起来的示例插件（注册两个小工具）")
        make_example.clicked.connect(self._write_example_plugin)
        plugin_row.addWidget(make_example)
        plugin_holder = QWidget()
        plugin_holder.setLayout(plugin_row)
        layout.addWidget(plugin_holder)

        self._plugin_label = QLabel("")
        self._plugin_label.setObjectName("readerStatus")
        self._plugin_label.setWordWrap(True)
        layout.addWidget(self._plugin_label)

        # ---- 素材缓存（Word 等文档里的图片）----
        media_title = QLabel("素材缓存（文档里的图片）")
        media_title.setObjectName("sectionTitle")
        layout.addWidget(media_title)
        media_row = QHBoxLayout()
        self._media_label = QLabel("")
        self._media_label.setObjectName("readerStatus")
        self._media_label.setWordWrap(True)
        media_row.addWidget(self._media_label, 1)
        open_media_dir = QPushButton("打开素材目录")
        open_media_dir.clicked.connect(self._open_media_dir)
        media_row.addWidget(open_media_dir)
        clear_media = QPushButton("清空素材缓存")
        clear_media.setToolTip("清掉之后，已打开文档里的图片会变成 [图片] 占位；重新打开源文件会自动补回来")
        clear_media.clicked.connect(self._clear_media_cache)
        media_row.addWidget(clear_media)
        media_holder = QWidget()
        media_holder.setLayout(media_row)
        layout.addWidget(media_holder)

        ai_row = QHBoxLayout()
        self._ai_label = QLabel("")
        self._ai_label.setObjectName("readerStatus")
        self._ai_label.setWordWrap(True)
        ai_row.addWidget(self._ai_label, 1)
        open_llm = QPushButton("打开大模型设置…")
        open_llm.clicked.connect(self._open_llm_settings)
        ai_row.addWidget(open_llm)
        layout.addLayout(ai_row)
        layout.addStretch(1)

    # ---------------------------------------------------------------- 动作

    def _pick_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(None, "选择默认输出目录", self._output_dir.text())
        if folder:
            self._output_dir.setText(folder)

    def _open_llm_settings(self) -> None:
        from . import SettingsDialog

        SettingsDialog(self, initial="llm").exec()
        self._refresh_status()

    # ---------------------------------------------------------------- 插件

    def _open_plugin_dir(self) -> None:
        import subprocess

        from modu_workbench.core.document import plugins as document_plugins

        folder = document_plugins.plugin_dir()
        self._plugin_dir_field.setText(str(folder))
        try:
            if folder.is_dir():
                subprocess.Popen(["explorer", str(folder)])
        except Exception as error:  # noqa: BLE001
            self._plugin_label.setText(f"无法打开插件目录：{error}")

    def _write_example_plugin(self) -> None:
        from modu_workbench.core.document import plugins as document_plugins

        try:
            path = document_plugins.write_example_plugin()
        except OSError as error:
            self._plugin_label.setText(f"生成示例插件失败：{error}")
            return
        self._refresh_plugins()
        self._plugin_label.setText(
            f"示例插件已生成：{path}（勾选「启用插件目录」并保存后，"
            "重新进入板块即可看到「示例：转大写 / 示例：统计字数」两个工具）")

    def _refresh_plugins(self) -> None:
        from modu_workbench.core.document import plugins as document_plugins

        self._plugin_dir_field.setText(str(document_plugins.plugin_dir()))
        files = document_plugins.discover()
        if not files:
            self._plugin_label.setText("插件目录里还没有文件：可点「生成示例插件」看一个能跑的例子")
            return
        lines = [f"发现 {len(files)} 个插件文件："]
        for path in files[:6]:
            size = path.stat().st_size if path.is_file() else 0
            lines.append(f"  · {path.name}（{size / 1024:.1f} KB）")
        if len(files) > 6:
            lines.append(f"  … 其余 {len(files) - 6} 个")
        lines.append("说明：勾选启用并保存后，进入板块时会加载；加载结果记在「权限安全 → 审计日志」。")
        self._plugin_label.setText("\n".join(lines))

    # ---------------------------------------------------------------- 素材缓存

    def _open_media_dir(self) -> None:
        import subprocess

        from modu_workbench.core.document import media

        folder = media.media_dir()
        try:
            if folder.is_dir():
                subprocess.Popen(["explorer", str(folder)])
        except Exception as error:  # noqa: BLE001
            self._media_label.setText(f"无法打开素材目录：{error}")

    def _clear_media_cache(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        from modu_workbench.core.document import media

        answer = QMessageBox.question(
            self, "清空素材缓存",
            "清掉之后，已打开文档里的图片会显示成 [图片] 占位（重新打开源文件会自动补回来）。\n"
            "确定清空吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = media.clear_cache()
        self._refresh_media()
        self._media_label.setText(
            f"已清空 {removed['files']} 个素材（{removed['bytes'] / 1024:.0f} KB）")

    def _refresh_media(self) -> None:
        from modu_workbench.core.document import media

        stats = media.cache_stats()
        folder = media.media_dir()
        # 目录只放提示里：路径太长会把设置页撑出横向滚动条
        self._media_label.setToolTip(str(folder))
        self._media_label.setText(
            f"共 {stats['files']} 个文件 · {stats['bytes'] / 1024 / 1024:.1f} MB。"
            "Word 里的图片按内容去重存这里，版本快照只存引用，不会因图片变大。")

    def _refresh_status(self) -> None:
        tools = ["文档引擎：本地解析 / 排版 / 公式计算（无需联网）",
                 summary_text()]
        tools.extend(f"{name}：{'可用' if ok else '未检测到'}" for name, ok in describe_support())
        tools.append(document_ocr.describe())
        self._tools_label.setText("\n".join(tools))

        usable = llm_router().usable_profiles(KIND_TEXT)
        state = f"已配置 {len(usable)} 份" if usable else "未配置"
        scenes = "、".join(spec.label for spec in SCENES.values())
        self._ai_label.setText(
            f"{kind_label(KIND_TEXT)}：{state}。可用场景：{scenes}。"
            "Key/模型/优先级在「大模型」页管理，调用失败会自动降级。")

    # ---------------------------------------------------------------- 载入/保存

    def load(self) -> None:
        settings = self._settings
        self._output_dir.setText(str(settings.value("document/output_dir", "", type=str) or ""))
        template = str(settings.value("document/template", "general") or "general")
        index = self._template.findData(template)
        self._template.setCurrentIndex(max(0, index))
        self._font_cn.setCurrentText(str(settings.value("document/font_cn", "宋体") or "宋体"))
        self._body_size.setValue(float(settings.value("document/body_size", 12.0) or 12.0))
        self._brand_color.setText(str(settings.value("document/brand_color", "#1452C4")
                                      or "#1452C4"))
        # 开关一律 type=bool：Windows 注册表里 Qt 把 bool 存成 "true"/"false" 字符串，
        # bool("false") 会得到 True（用户明明取消了却还勾着）
        self._auto_number.setChecked(
            bool(settings.value("document/auto_number", False, type=bool)))
        self._build_toc.setChecked(
            bool(settings.value("document/build_toc", False, type=bool)))
        self._beautify_tables.setChecked(
            bool(settings.value("document/beautify_tables", True, type=bool)))
        self._freeze_header.setChecked(
            bool(settings.value("document/freeze_header", True, type=bool)))
        self._conditional_format.setChecked(
            bool(settings.value("document/conditional_format", False, type=bool)))
        self._data_validation.setChecked(
            bool(settings.value("document/data_validation", False, type=bool)))
        self._max_versions.setValue(int(settings.value("document/max_versions", 40) or 40))
        self._autosave.setChecked(
            bool(settings.value("document/autosave", True, type=bool)))
        self._autosave_seconds.setValue(int(settings.value("document/autosave_seconds", 120) or 120))
        self._max_rounds.setValue(int(settings.value("document/max_rounds", 3) or 3))
        self._threshold.setValue(float(settings.value("document/quality_threshold", 0.9) or 0.9))
        self._max_cost.setValue(float(settings.value("document/max_cost", 0.0) or 0.0))
        self._require_confirm.setChecked(
            bool(settings.value("document/require_confirm", False, type=bool)))

        permissions = permissions_from_settings(settings.value)
        self._allow_cloud.setChecked(permissions.allow_cloud)
        self._allow_local.setChecked(permissions.allow_local)
        self._mask_before_upload.setChecked(permissions.mask_before_upload)
        selected = set(permissions.mask_rules)
        for key, box in self._mask_boxes.items():
            box.setChecked(key in selected)
        from modu_workbench.core.document import DocumentStorage, plugins as document_plugins
        from modu_workbench.core.platform.paths import app_db_path

        try:
            store = DocumentStorage(app_db_path())
            self._enable_plugins.setChecked(document_plugins.plugins_enabled(store))
        except Exception:  # noqa: BLE001  数据库异常时按默认关闭显示
            self._enable_plugins.setChecked(False)
        self._refresh_plugins()
        self._refresh_media()
        self._refresh_status()

    def save(self) -> bool:
        settings = self._settings
        text = self._output_dir.text().strip()
        if text and not Path(text).exists():
            try:
                Path(text).mkdir(parents=True, exist_ok=True)
            except OSError:
                text = ""
        settings.setValue("document/output_dir", text)
        settings.setValue("document/template", self._template.currentData() or "general")
        settings.setValue("document/font_cn", self._font_cn.currentText().strip() or "宋体")
        settings.setValue("document/body_size", self._body_size.value())
        settings.setValue("document/brand_color",
                          self._brand_color.text().strip() or "#1452C4")
        settings.setValue("document/auto_number", self._auto_number.isChecked())
        settings.setValue("document/build_toc", self._build_toc.isChecked())
        settings.setValue("document/beautify_tables", self._beautify_tables.isChecked())
        settings.setValue("document/freeze_header", self._freeze_header.isChecked())
        settings.setValue("document/conditional_format", self._conditional_format.isChecked())
        settings.setValue("document/data_validation", self._data_validation.isChecked())
        settings.setValue("document/max_versions", self._max_versions.value())
        settings.setValue("document/autosave", self._autosave.isChecked())
        settings.setValue("document/autosave_seconds", self._autosave_seconds.value())
        settings.setValue("document/max_rounds", self._max_rounds.value())
        settings.setValue("document/quality_threshold", self._threshold.value())
        settings.setValue("document/max_cost", self._max_cost.value())
        settings.setValue("document/require_confirm", self._require_confirm.isChecked())
        settings.setValue("document/allow_cloud", self._allow_cloud.isChecked())
        settings.setValue("document/allow_local", self._allow_local.isChecked())
        settings.setValue("document/mask_before_upload", self._mask_before_upload.isChecked())
        settings.setValue("document/mask_rules", ",".join(
            key for key, box in self._mask_boxes.items() if box.isChecked()))
        settings.sync()

        # 插件开关存在板块库里（默认关闭），保存时同步过去
        from modu_workbench.core.document import DocumentStorage
        from modu_workbench.core.document import plugins as document_plugins
        from modu_workbench.core.platform.paths import app_db_path

        try:
            store = DocumentStorage(app_db_path())
            document_plugins.set_enabled(store, self._enable_plugins.isChecked())
            if self._enable_plugins.isChecked():
                records = document_plugins.load_plugins(enabled=True, storage=store)
                loaded = [record for record in records if record.ok]
                failed = [record for record in records if not record.ok]
                self._plugin_label.setText(
                    f"已加载 {len(loaded)} 个插件"
                    + (f"；{len(failed)} 个失败：" + "；".join(
                        record.summary() for record in failed[:3]) if failed else "")
                    + "（重新进入板块后生效）")
        except Exception as error:  # noqa: BLE001  插件开关写失败不应影响其它设置
            self._plugin_label.setText(f"插件设置保存失败：{error}")
        # 权限是"每次调用前实时读取"的（见 core/document/ai.py 的 permissions_provider），
        # 因此这里保存完就立即生效，无需重启或重建板块单例。
        return True

    def hint(self) -> str:
        return ("文档数据都在单库 modu.db 的 doc_* 表里（文档库/版本快照/合并报告/AI 与审计日志）；"
                "API Key 与模型优先级在「大模型」页配置。"
                "OCR 需要本机安装 tesseract（可用 MODU_TESSERACT 指定），"
                "ODS / 旧版 DOC·PPT / WPS 旧格式需要 LibreOffice。")


__all__ = ["DocumentSettingsPage"]
