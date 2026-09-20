"""墨软文档：格式美化面板（MR-DOC-301）。

界面上把"一键美化"拆成**可解释的参数**：模板 → 字体字号行距段距 → 标题层级与编号 →
表格样式 → 目录 → 品牌与页眉页脚。右侧先「预览改动」看清楚要改什么，再「应用美化」；
应用结果会进入查看器并留下版本快照（可回滚）。

导出区与美化共用同一份参数，因此"导出 PDF/Word/HTML 保持版式一致"是靠同一条管线实现的。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.document import (
    EXPORT_TARGETS,
    BeautifyOptions,
    DocumentIR,
    DocumentLibrary,
    export_label,
    template_label,
)
from modu_workbench.core.document import templates as document_templates
from modu_workbench.core.document.beautify import template_options
from modu_workbench.core.document.writer import render_html
from modu_workbench.ui_kit.components import TaskBar
from modu_workbench.ui_kit.settings import app_settings
from modu_workbench.ui_kit.toast import Toaster

from . import widgets as W

#: 常用中文字体（下拉里给常见选择，也允许直接输入）
FONT_CHOICES = (
    "宋体", "仿宋_GB2312", "黑体", "微软雅黑", "方正小标宋简体", "楷体", "思源宋体",
    "Times New Roman",
)
NUMBER_STYLES = (("arabic", "1 / 1.1 / 1.1.1"), ("chinese", "第一章 / 一、/（一）"),
                 ("legal", "第X条"))
ALIGN_CHOICES = (("left", "左对齐"), ("justify", "两端对齐"), ("center", "居中"), ("right", "右对齐"))


class BeautifyPanel(QWidget):
    """格式美化 + 导出。"""

    irChanged = Signal(object)
    statusMessage = Signal(str)

    def __init__(self, library: DocumentLibrary, *, task_bar: Optional[TaskBar] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._task = task_bar
        self._library = library
        self._toaster = Toaster(self)
        self._ir: Optional[DocumentIR] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        splitter = QHBoxLayout()
        splitter.setSpacing(10)

        # ---- 左：参数 ----
        form_host = QWidget()
        form_host.setObjectName("actionPanel")
        form_layout = QVBoxLayout(form_host)
        form_layout.setContentsMargins(12, 12, 12, 12)
        form_layout.setSpacing(8)

        title = QLabel("美化参数")
        title.setObjectName("sectionTitle")
        form_layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(8)

        self._template = QComboBox()
        self._refresh_templates()
        self._template.currentIndexChanged.connect(self._apply_template_defaults)
        form.addRow("模板", self._template)

        template_row = QHBoxLayout()
        save_tpl = QPushButton("另存为模板")
        save_tpl.setToolTip("把当前参数存进本地模板库（JSON，可分享/可版本管理）")
        save_tpl.clicked.connect(self._save_template)
        template_row.addWidget(save_tpl)
        import_tpl = QPushButton("导入模板")
        import_tpl.clicked.connect(self._import_template)
        template_row.addWidget(import_tpl)
        export_tpl = QPushButton("导出模板")
        export_tpl.clicked.connect(self._export_template)
        template_row.addWidget(export_tpl)
        delete_tpl = QPushButton("删除模板")
        delete_tpl.setToolTip("只删除本地模板库里的模板，不影响内置模板")
        delete_tpl.clicked.connect(self._delete_template)
        template_row.addWidget(delete_tpl)
        template_row.addStretch(1)
        form.addRow("模板库", template_row)

        self._font_cn = QComboBox()
        self._font_cn.setEditable(True)
        self._font_cn.addItems(FONT_CHOICES)
        form.addRow("正文字体", self._font_cn)

        self._heading_font = QComboBox()
        self._heading_font.setEditable(True)
        self._heading_font.addItem("（同正文字体）", "")
        self._heading_font.addItems(FONT_CHOICES)
        form.addRow("标题字体", self._heading_font)

        self._body_size = QDoubleSpinBox()
        self._body_size.setRange(6.0, 48.0)
        self._body_size.setSingleStep(0.5)
        self._body_size.setSuffix(" pt")
        form.addRow("正文字号", self._body_size)

        self._line_spacing = QDoubleSpinBox()
        self._line_spacing.setRange(1.0, 3.0)
        self._line_spacing.setSingleStep(0.05)
        form.addRow("行距", self._line_spacing)

        self._paragraph_spacing = QDoubleSpinBox()
        self._paragraph_spacing.setRange(0.0, 36.0)
        self._paragraph_spacing.setSuffix(" pt")
        form.addRow("段距", self._paragraph_spacing)

        self._indent = QDoubleSpinBox()
        self._indent.setRange(0.0, 4.0)
        self._indent.setSingleStep(0.5)
        self._indent.setSuffix(" 字")
        form.addRow("首行缩进", self._indent)

        self._align = QComboBox()
        for key, label in ALIGN_CHOICES:
            self._align.addItem(label, key)
        form.addRow("正文对齐", self._align)

        self._number_style = QComboBox()
        for key, label in NUMBER_STYLES:
            self._number_style.addItem(label, key)
        form.addRow("编号风格", self._number_style)

        color_row = QHBoxLayout()
        self._brand_color = QLineEdit("#1452C4")
        pick_color = QPushButton("选择…")
        pick_color.clicked.connect(self._pick_color)
        color_row.addWidget(self._brand_color, 1)
        color_row.addWidget(pick_color)
        color_holder = QWidget()
        color_holder.setLayout(color_row)
        form.addRow("品牌色", color_holder)
        form_layout.addLayout(form)

        toggles = QGridLayout()
        toggles.setSpacing(4)
        self._unify_styles = QCheckBox("样式统一")
        self._infer_levels = QCheckBox("识别标题层级")
        self._auto_number = QCheckBox("自动编号")
        self._beautify_tables = QCheckBox("表格美化")
        self._zebra = QCheckBox("表格斑马纹")
        self._freeze_header = QCheckBox("导出 Excel 冻结表头")
        self._conditional_format = QCheckBox("条件格式（高于平均值）")
        self._data_validation = QCheckBox("数据验证（下拉清单）")
        self._build_toc = QCheckBox("生成目录")
        self._page_numbers = QCheckBox("页码")
        for index, box in enumerate((self._unify_styles, self._infer_levels, self._auto_number,
                                     self._beautify_tables, self._zebra, self._freeze_header,
                                     self._conditional_format, self._data_validation,
                                     self._build_toc, self._page_numbers)):
            box.setChecked(True)
            toggles.addWidget(box, index // 2, index % 2)
        self._conditional_format.setChecked(False)
        self._data_validation.setChecked(False)
        self._conditional_format.setToolTip("给数值列加「高于平均值」高亮（导出 xlsx 时写入）")
        self._data_validation.setToolTip("取值少的短文本列写下拉清单（导出 xlsx 时写入）")
        form_layout.addLayout(toggles)

        brand_form = QFormLayout()
        brand_form.setSpacing(8)
        self._header_text = QLineEdit()
        self._header_text.setPlaceholderText("页眉（可留空）")
        brand_form.addRow("页眉", self._header_text)
        self._footer_text = QLineEdit()
        self._footer_text.setPlaceholderText("页脚（可留空）")
        brand_form.addRow("页脚", self._footer_text)
        self._logo_text = QLineEdit()
        self._logo_text.setPlaceholderText("Logo / 品牌文案（可留空）")
        brand_form.addRow("品牌", self._logo_text)
        self._toc_depth = QSpinBox()
        self._toc_depth.setRange(1, 6)
        self._toc_depth.setValue(3)
        brand_form.addRow("目录深度", self._toc_depth)
        form_layout.addLayout(brand_form)

        buttons = QHBoxLayout()
        preview = QPushButton("预览改动")
        preview.clicked.connect(self._preview)
        buttons.addWidget(preview)
        self._apply_button = QPushButton("应用美化")
        self._apply_button.setObjectName("primaryButton")
        self._apply_button.clicked.connect(self._apply)
        buttons.addWidget(self._apply_button)
        reset = QPushButton("恢复模板默认")
        reset.clicked.connect(lambda: self._apply_template_defaults(force=True))
        buttons.addWidget(reset)
        buttons.addStretch(1)
        form_layout.addLayout(buttons)

        export_row = QHBoxLayout()
        export_row.addWidget(QLabel("导出："))
        self._export_target = QComboBox()
        for key, label in EXPORT_TARGETS:
            self._export_target.addItem(label, key)
        export_row.addWidget(self._export_target)
        export = QPushButton("导出结果")
        export.clicked.connect(self._export)
        export_row.addWidget(export)
        export_images = QPushButton("导出为图片")
        export_images.setToolTip("导出 PNG（Office 文档先经 LibreOffice 转 PDF，再逐页渲染）")
        export_images.clicked.connect(self._export_images)
        export_row.addWidget(export_images)
        export_row.addStretch(1)
        form_layout.addLayout(export_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(form_host)
        splitter.addWidget(scroll, 3)

        # ---- 右：改动与预览 ----
        right = QWidget()
        right.setObjectName("actionPanel")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(8)
        right_title = QLabel("改动明细（预览 / 应用后都会列出）")
        right_title.setObjectName("sectionTitle")
        right_layout.addWidget(right_title)
        self._changes = W.ChangeTable()
        right_layout.addWidget(self._changes, 2)
        self._summary = QLabel("还没有预览")
        self._summary.setObjectName("readerStatus")
        self._summary.setWordWrap(True)
        right_layout.addWidget(self._summary)
        self._preview_browser = QTextBrowser()
        right_layout.addWidget(self._preview_browser, 3)
        splitter.addWidget(right, 4)

        outer.addLayout(splitter, 1)
        self._apply_template_defaults()
        self._apply_settings_defaults()

    # ---------------------------------------------------------------- 参数

    def set_ir(self, ir: Optional[DocumentIR]) -> None:
        self._ir = ir
        has_doc = ir is not None
        self._apply_button.setEnabled(has_doc)
        if not has_doc:
            self._summary.setText("先在左侧打开一个文档")
            self._preview_browser.setHtml("")

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(parent=self)
        if color.isValid():
            self._brand_color.setText(color.name().upper())

    # ---------------------------------------------------------------- 模板库

    def _refresh_templates(self, *, select: str = "") -> None:
        """重建模板下拉（内置 + 本地模板库）。"""
        previous = select or self._template.currentData() if self._template.count() else select
        self._template.blockSignals(True)
        self._template.clear()
        for value, label in document_templates.choices():
            self._template.addItem(label, value)
        if previous:
            index = self._template.findData(previous)
            if index >= 0:
                self._template.setCurrentIndex(index)
        self._template.blockSignals(False)
        self._log_template_state()

    def _log_template_state(self) -> None:
        count = len(document_templates.list_user_templates())
        self._template.setToolTip(
            f"{document_templates.capability_text()}；当前本地模板 {count} 个")

    def _selected_template(self) -> tuple[BeautifyOptions, str, str]:
        """当前选中的模板 →（参数, 展示名, 原始 value）。"""
        value = str(self._template.currentData() or "general")
        try:
            options, label = document_templates.resolve_choice(value)
        except document_templates.TemplateError as error:
            self._toaster.error(str(error))
            return template_options("general"), "通用（一键美化）", "general"
        return options, label, value

    def _apply_template_defaults(self, *_args: object, force: bool = False) -> None:
        options, label, _value = self._selected_template()
        self._apply_options(options)
        if force:
            self._toaster.info(f"已恢复「{label}」模板默认参数")

    def _apply_options(self, options: BeautifyOptions) -> None:
        """把一份参数写进各控件（模板默认与设置默认共用）。"""
        self._font_cn.setCurrentText(options.font_cn)
        self._heading_font.setCurrentText(options.heading_font_cn or "")
        self._body_size.setValue(options.body_size)
        self._line_spacing.setValue(options.line_spacing)
        self._paragraph_spacing.setValue(options.paragraph_spacing)
        self._indent.setValue(options.first_line_indent)
        self._align.setCurrentIndex(max(0, self._align.findData(options.align)))
        self._number_style.setCurrentIndex(max(0, self._number_style.findData(options.number_style)))
        self._brand_color.setText(options.brand_color)
        self._unify_styles.setChecked(options.unify_styles)
        self._infer_levels.setChecked(options.infer_heading_levels)
        self._auto_number.setChecked(options.auto_number)
        self._beautify_tables.setChecked(options.beautify_tables)
        self._zebra.setChecked(options.zebra)
        self._freeze_header.setChecked(options.freeze_header)
        self._conditional_format.setChecked(options.conditional_format)
        self._data_validation.setChecked(options.data_validation)
        self._build_toc.setChecked(options.build_toc)
        self._page_numbers.setChecked(options.page_numbers)
        self._toc_depth.setValue(options.toc_depth)

    def _apply_settings_defaults(self) -> None:
        """把设置页的「美化默认参数」带进面板。

        只覆盖**用户显式保存过**的键：没保存过的项继续用模板默认值，
        否则"公文体"这类模板的字体字号会被设置页的通用默认值冲掉。
        """
        settings = app_settings()
        template = str(settings.value("document/template", "") or "")
        if template:
            index = self._template.findData(template)
            if index >= 0 and index != self._template.currentIndex():
                self._template.setCurrentIndex(index)         # 会重新套用该模板默认
        if settings.contains("document/font_cn"):
            font = str(settings.value("document/font_cn") or "").strip()
            if font:
                self._font_cn.setCurrentText(font)
        if settings.contains("document/body_size"):
            try:
                self._body_size.setValue(float(settings.value("document/body_size")))
            except (TypeError, ValueError):
                pass
        if settings.contains("document/brand_color"):
            color = str(settings.value("document/brand_color") or "").strip()
            if color:
                self._brand_color.setText(color)
        for key, box in (("document/auto_number", self._auto_number),
                         ("document/build_toc", self._build_toc),
                         ("document/beautify_tables", self._beautify_tables),
                         ("document/freeze_header", self._freeze_header),
                         ("document/conditional_format", self._conditional_format),
                         ("document/data_validation", self._data_validation)):
            if settings.contains(key):
                # type=bool：注册表里存的是 "true"/"false" 字符串，bool("false") 会是 True
                box.setChecked(bool(settings.value(key, box.isChecked(), type=bool)))

    def _save_template(self) -> None:
        default = str(self._template.currentText() or "我的模板")
        name = W.ask_text(self, "另存为模板", "模板名", f"{default}-自定义")
        if not name:
            return
        note = W.ask_text(self, "模板备注", "备注（可留空）", "")
        try:
            path = document_templates.save_template(name, self.options(), note=note)
        except document_templates.TemplateError as error:
            self._toaster.error(str(error))
            return
        self._refresh_templates(select=document_templates.token_for_user_template(path))
        message = f"已保存模板「{name}」到本地模板库"
        self._toaster.success(message)
        self.statusMessage.emit(message)

    def _import_template(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入模板", "", "模板文件 (*.json);;所有文件 (*.*)")
        if not path:
            return
        try:
            template = document_templates.import_template(path)
        except document_templates.TemplateError as error:
            self._toaster.error(str(error))
            return
        self._refresh_templates(select=document_templates.token_for_user_template(template.path))
        message = f"已导入模板「{template.name}」"
        self._toaster.success(message)
        self.statusMessage.emit(message)

    def _export_template(self) -> None:
        _options, label, value = self._selected_template()
        if not value.startswith("user:"):
            self._toaster.info("内置模板无需导出：先「另存为模板」再导出即可分享")
            return
        target, _ = QFileDialog.getSaveFileName(self, "导出模板",
                                               f"{label.replace('我的模板：', '')}.json",
                                               "模板文件 (*.json)")
        if not target:
            return
        try:
            produced = document_templates.export_template(value[len("user:"):], target)
        except document_templates.TemplateError as error:
            self._toaster.error(str(error))
            return
        self._toaster.success(f"已导出模板：{produced}")
        self.statusMessage.emit(f"已导出模板 → {produced}")

    def _delete_template(self) -> None:
        _options, label, value = self._selected_template()
        if not value.startswith("user:"):
            self._toaster.info("内置模板不能删除（可另存为本地模板后再改）")
            return
        if not document_templates.delete_template(value[len("user:"):]):
            self._toaster.error("删除失败：模板文件不存在")
            return
        self._refresh_templates()
        self._toaster.success(f"已删除模板「{label}」")
        self.statusMessage.emit(f"已删除模板「{label}」")

    def options(self) -> BeautifyOptions:
        """把界面参数组装成引擎参数（模板名只作元数据，便于追溯来源）。"""
        options = BeautifyOptions()
        selected, _label, _value = self._selected_template()
        options.template = selected.template or "general"
        options.font_cn = self._font_cn.currentText().strip() or "宋体"
        options.heading_font_cn = self._heading_font.currentText().strip()
        options.body_size = self._body_size.value()
        options.line_spacing = round(self._line_spacing.value(), 2)
        options.paragraph_spacing = self._paragraph_spacing.value()
        options.first_line_indent = self._indent.value()
        options.align = self._align.currentData() or "left"
        options.number_style = self._number_style.currentData() or "arabic"
        options.brand_color = self._brand_color.text().strip() or "#1452C4"
        options.unify_styles = self._unify_styles.isChecked()
        options.infer_heading_levels = self._infer_levels.isChecked()
        options.auto_number = self._auto_number.isChecked()
        options.beautify_tables = self._beautify_tables.isChecked()
        options.zebra = self._zebra.isChecked()
        options.freeze_header = self._freeze_header.isChecked()
        options.conditional_format = self._conditional_format.isChecked()
        options.data_validation = self._data_validation.isChecked()
        options.build_toc = self._build_toc.isChecked()
        options.page_numbers = self._page_numbers.isChecked()
        options.toc_depth = self._toc_depth.value()
        options.header_text = self._header_text.text().strip()
        options.footer_text = self._footer_text.text().strip()
        options.logo_text = self._logo_text.text().strip()
        return options

    def _describe(self, template_key: str) -> str:
        return template_label(template_key)

    # ---------------------------------------------------------------- 预览与应用

    def _preview(self) -> None:
        result = self._run_preview()
        if result is None:
            return
        self._changes.set_changes(result.changes)
        self._preview_browser.setHtml(render_html(result.ir, standalone=False))
        self._summary.setText(
            f"「{self._describe(result.template)}」预览：{result.change_count} 处修改"
            f"{f'，目录 {result.toc_entries} 条' if result.toc_entries else ''}")
        self.statusMessage.emit(self._summary.text())

    def _run_preview(self):  # noqa: ANN201
        if self._ir is None:
            self._toaster.info("先在左侧打开一个文档")
            return None
        try:
            return self._library.preview_beautify(self._ir, self.options())
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"预览失败：{error}")
            return None

    def _apply(self) -> None:
        if self._ir is None:
            self._toaster.info("先在左侧打开一个文档")
            return
        try:
            result = self._library.beautify(self._ir, self.options())
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"美化失败：{error}")
            W.note(self._task, f"美化失败：{error}")
            return
        self._ir = result.ir
        self._changes.set_changes(result.changes)
        self._preview_browser.setHtml(render_html(result.ir, standalone=False))
        summary = (f"已按「{self._describe(result.template)}」美化：{result.change_count} 处修改"
                   f"{f'，目录 {result.toc_entries} 条' if result.toc_entries else ''}")
        self._summary.setText(summary)
        self._toaster.success(summary)
        W.note(self._task, summary)
        self.irChanged.emit(result.ir)

    def _export(self) -> None:
        if self._ir is None:
            self._toaster.info("先在左侧打开一个文档")
            return
        target = self._export_target.currentData() or "docx"
        try:
            result = self._library.export(self._ir, target, options=self.options())
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"导出失败：{error}")
            W.note(self._task, f"导出失败：{error}")
            return
        message = f"已导出 {export_label(target)}：{result.path}"
        if result.notes:
            message += "（" + "；".join(result.notes) + "）"
        self._toaster.success(message)
        W.note(self._task, message)

    def _export_images(self) -> None:
        """导出为图片（Office 文档经 LibreOffice 转 PDF 再逐页渲染成 PNG）。"""
        if self._ir is None:
            self._toaster.info("先在左侧打开一个文档")
            return
        W.busy(self._task, "正在导出图片…")
        try:
            produced = self._library.export_images(self._ir, dpi=150)
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"导出图片失败：{error}")
            W.idle(self._task, f"导出图片失败：{error}")
            return
        message = f"已导出 {len(produced)} 张 PNG → {produced[0].parent if produced else ''}"
        self._toaster.success(message)
        W.idle(self._task, message)


__all__ = ["BeautifyPanel"]
