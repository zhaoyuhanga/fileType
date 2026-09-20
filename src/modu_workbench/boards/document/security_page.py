"""墨软文档：权限与安全面板（MR-DOC-308）。

四块：
1. **脱敏**：勾选规则 →「预览命中」先看清楚会遮住什么 →「应用脱敏」写回文档；
2. **水印与追踪**：设置水印文字/页脚/追踪标识，导出 Word/Excel/PDF/HTML 时落地
   （csv/json 这类纯数据格式会明说"跳过水印"，不悄悄丢内容）；
3. **AI 权限**：是否允许云端上传、是否允许本地模型、上传前是否脱敏、长度上限、
   模型白名单 —— 默认「不允许云端 + 上传前脱敏」，保存后立即生效；
4. **审计**：AI 调用日志（模型/角色/工具/耗时/成本）与操作审计（打开/编辑/导出/合并/回滚…）。
"""
from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.document import (
    DEFAULT_MASK_RULES,
    DocumentIR,
    DocumentLibrary,
    audit_label,
    build_watermark,
    mask_rule_choices,
    new_tracking_id,
    scan_sensitive,
)
from modu_workbench.core.document import security as document_security
from modu_workbench.ui_kit.components import TaskBar
from modu_workbench.ui_kit.settings import app_settings
from modu_workbench.ui_kit.toast import Toaster

from . import widgets as W


class SecurityPanel(QWidget):
    """脱敏 / 水印 / 权限 / 审计。"""

    irChanged = Signal(object)
    statusMessage = Signal(str)
    settingsChanged = Signal()

    def __init__(self, library: DocumentLibrary, *, task_bar: Optional[TaskBar] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._task = task_bar
        self._library = library
        self._toaster = Toaster(self)
        self._ir: Optional[DocumentIR] = None
        self._settings = app_settings()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_mask_tab(), "脱敏")
        self._tabs.addTab(self._build_watermark_tab(), "水印与追踪")
        self._tabs.addTab(self._build_permission_tab(), "AI 权限")
        self._tabs.addTab(self._build_audit_tab(), "审计日志")
        layout.addWidget(self._tabs, 1)

        self._status = QLabel("")
        self._status.setObjectName("readerStatus")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        self._load_settings()

    # ---------------------------------------------------------------- 脱敏

    def _build_mask_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        hint = QLabel("默认规则覆盖手机号、身份证、银行卡、邮箱与金额；"
                      "上传云端模型前会自动脱敏（可在「AI 权限」里关闭）。")
        hint.setObjectName("readerStatus")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        grid = QGridLayout()
        grid.setSpacing(4)
        self._mask_boxes: dict[str, QCheckBox] = {}
        for index, (key, label) in enumerate(mask_rule_choices()):
            box = QCheckBox(f"{label}（{key}）")
            box.setChecked(key in DEFAULT_MASK_RULES)
            self._mask_boxes[key] = box
            grid.addWidget(box, index // 3, index % 3)
        layout.addLayout(grid)

        row = QHBoxLayout()
        preview = QPushButton("预览命中")
        preview.clicked.connect(self._preview_mask)
        row.addWidget(preview)
        apply_button = QPushButton("应用脱敏")
        apply_button.setObjectName("primaryButton")
        apply_button.clicked.connect(self._apply_mask)
        row.addWidget(apply_button)
        row.addStretch(1)
        layout.addLayout(row)

        self._mask_table = QTableWidget(0, 3)
        self._mask_table.setHorizontalHeaderLabels(["规则", "命中", "示例（脱敏后）"])
        header = self._mask_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._mask_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self._mask_table, 1)
        return page

    def _selected_rules(self) -> list[str]:
        return [key for key, box in self._mask_boxes.items() if box.isChecked()]

    def _preview_mask(self) -> None:
        if self._ir is None:
            self._toaster.info("先在左侧打开一个文档")
            return
        rules = self._selected_rules()
        counts = scan_sensitive(self._ir.text(), rules)
        from modu_workbench.core.document import mask_text

        masked, _counts = mask_text(self._ir.text(), rules)
        self._mask_table.setRowCount(len(rules))
        for row, key in enumerate(rules):
            self._mask_table.setItem(row, 0, QTableWidgetItem(document_security.mask_label(key)))
            self._mask_table.setItem(row, 1, QTableWidgetItem(str(counts.get(key, 0))))
            sample = ""
            if counts.get(key):
                rule = document_security.MASK_RULES[key]
                match = rule.pattern.search(self._ir.text())
                if match:
                    raw = match.group(0)
                    sample = f"{raw} → {document_security.mask_text(raw, [key])[0]}"
            self._mask_table.setItem(row, 2, QTableWidgetItem(sample))
        total = sum(counts.values())
        self._status.setText(f"命中 {total} 处敏感信息"
                             + ("（可点「应用脱敏」写回文档）" if total else "（没有需要脱敏的内容）"))
        W.note(self._task, f"脱敏预览：{total} 处命中（脱敏后文本 {len(masked)} 字）")

    def _apply_mask(self) -> None:
        if self._ir is None:
            self._toaster.info("先在左侧打开一个文档")
            return
        rules = self._selected_rules()
        if not rules:
            self._toaster.info("至少勾选一条脱敏规则")
            return
        masked, report = self._library.mask(self._ir, rules)
        self._ir = masked
        self._toaster.success(report.summary())
        self._status.setText(report.summary())
        W.note(self._task, report.summary())
        self.irChanged.emit(masked)

    # ---------------------------------------------------------------- 水印

    def _build_watermark_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        form = QFormLayout()
        form.setSpacing(8)
        self._watermark_text = QLineEdit()
        self._watermark_text.setPlaceholderText("例如：内部资料 请勿外传")
        form.addRow("水印文字", self._watermark_text)
        self._watermark_footer = QLineEdit()
        self._watermark_footer.setPlaceholderText("页脚附加说明（可留空）")
        form.addRow("页脚", self._watermark_footer)
        tracking_row = QHBoxLayout()
        self._tracking_id = QLineEdit()
        self._tracking_id.setPlaceholderText("追踪标识（导出后可按它追查来源）")
        generate = QPushButton("生成")
        generate.clicked.connect(lambda: self._tracking_id.setText(new_tracking_id()))
        tracking_row.addWidget(self._tracking_id, 1)
        tracking_row.addWidget(generate)
        holder = QWidget()
        holder.setLayout(tracking_row)
        form.addRow("追踪标识", holder)
        layout.addLayout(form)

        row = QHBoxLayout()
        apply_button = QPushButton("应用到导出")
        apply_button.setObjectName("primaryButton")
        apply_button.clicked.connect(self._apply_watermark)
        row.addWidget(apply_button)
        clear = QPushButton("清除水印")
        clear.clicked.connect(self._clear_watermark)
        row.addWidget(clear)
        row.addStretch(1)
        layout.addLayout(row)

        note = QLabel(
            "落地方式：Word 写入页眉页脚（含页码域）、Excel 写入页眉页脚、"
            "PDF/HTML 写入版式化水印条、纯文本写末尾一行；\n"
            "CSV/TSV/JSON 属于纯数据格式，会跳过水印并在导出说明里写明，避免污染数据。")
        note.setObjectName("readerStatus")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        return page

    def _apply_watermark(self) -> None:
        tracking = self._tracking_id.text().strip() or new_tracking_id()
        self._tracking_id.setText(tracking)
        watermark = build_watermark(self._watermark_text.text().strip(),
                                    footer=self._watermark_footer.text().strip(),
                                    tracking_id=tracking)
        self._library.watermark = watermark
        self._settings.setValue("document/watermark_text", watermark.text)
        self._settings.setValue("document/watermark_footer", watermark.footer)
        self._settings.setValue("document/watermark_tracking", tracking)
        message = document_security.describe_watermark(watermark)
        self._toaster.success(message)
        self._status.setText(message)
        W.note(self._task, message)

    def _clear_watermark(self) -> None:
        self._library.watermark = None
        self._watermark_text.clear()
        self._watermark_footer.clear()
        self._tracking_id.clear()
        self._toaster.info("已清除导出水印")
        self._status.setText("已清除导出水印")

    # ---------------------------------------------------------------- 权限

    def _build_permission_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        hint = QLabel("默认「本地优先」：允许本地处理、不允许上传云端；开启脱敏后上传前会遮住敏感信息。")
        hint.setObjectName("readerStatus")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._allow_cloud = QCheckBox("允许把文档内容发送到云端模型（默认关闭）")
        layout.addWidget(self._allow_cloud)
        self._allow_local = QCheckBox("允许调用本地模型（Ollama / 本地 OpenAI 兼容服务）")
        self._allow_local.setChecked(True)
        layout.addWidget(self._allow_local)
        self._mask_before_upload = QCheckBox("上传前自动脱敏")
        self._mask_before_upload.setChecked(True)
        layout.addWidget(self._mask_before_upload)

        form = QFormLayout()
        form.setSpacing(8)
        self._max_chars = QSpinBox()
        self._max_chars.setRange(1000, 200000)
        self._max_chars.setSingleStep(1000)
        self._max_chars.setSuffix(" 字")
        form.addRow("单次长度上限", self._max_chars)
        self._allowed_models = QLineEdit()
        self._allowed_models.setPlaceholderText("允许的模型名，逗号分隔（留空 = 不限制）")
        form.addRow("模型白名单", self._allowed_models)
        self._price = QLineEdit()
        self._price.setPlaceholderText("每千 token 价格（用于成本统计，可留空）")
        form.addRow("模型单价", self._price)
        layout.addLayout(form)

        row = QHBoxLayout()
        save = QPushButton("保存权限")
        save.setObjectName("primaryButton")
        save.clicked.connect(self._save_permissions)
        row.addWidget(save)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)
        return page

    def _save_permissions(self) -> None:
        self._settings.setValue("document/allow_cloud", self._allow_cloud.isChecked())
        self._settings.setValue("document/allow_local", self._allow_local.isChecked())
        self._settings.setValue("document/mask_before_upload", self._mask_before_upload.isChecked())
        self._settings.setValue("document/max_chars", self._max_chars.value())
        self._settings.setValue("document/allowed_models", self._allowed_models.text().strip())
        try:
            self._settings.setValue("document/price_per_1k", float(self._price.text() or 0.0))
        except ValueError:
            self._settings.setValue("document/price_per_1k", 0.0)
        self._settings.sync()
        self._toaster.success("权限已保存并立即生效")
        W.note(self._task, "AI 权限已更新")
        self.settingsChanged.emit()

    # ---------------------------------------------------------------- 审计

    def _build_audit_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        row = QHBoxLayout()
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh_audit)
        row.addWidget(refresh)
        clear = QPushButton("清空日志")
        clear.clicked.connect(self._clear_audit)
        row.addWidget(clear)
        row.addStretch(1)
        self._usage_label = QLabel("")
        self._usage_label.setObjectName("readerStatus")
        row.addWidget(self._usage_label)
        layout.addLayout(row)

        sub = QTabWidget()
        self._audit_table = QTableWidget(0, 4)
        self._audit_table.setHorizontalHeaderLabels(["时间", "操作", "对象", "说明"])
        self._configure_table(self._audit_table, stretch_column=3)
        sub.addTab(self._audit_table, "操作审计")

        self._ai_table = QTableWidget(0, 7)
        self._ai_table.setHorizontalHeaderLabels(
            ["时间", "角色", "模型", "工具", "耗时", "成本", "结果"])
        self._configure_table(self._ai_table, stretch_column=6)
        sub.addTab(self._ai_table, "AI 调用")
        layout.addWidget(sub, 1)
        return page

    @staticmethod
    def _configure_table(table: QTableWidget, *, stretch_column: int) -> None:
        header = table.horizontalHeader()
        for column in range(table.columnCount()):
            header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.Stretch if column == stretch_column
                else QHeaderView.ResizeMode.ResizeToContents)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)

    def refresh_audit(self) -> None:
        records = self._library.audit_records(limit=300)
        self._audit_table.setRowCount(len(records))
        for row, record in enumerate(records):
            created = time.strftime("%m-%d %H:%M:%S",
                                    time.localtime(float(record.get("created_at") or 0)))
            values = [created, audit_label(str(record.get("action") or "")),
                      str(record.get("target") or ""), str(record.get("detail") or "")]
            for column, value in enumerate(values):
                self._audit_table.setItem(row, column, QTableWidgetItem(value))

        calls = self._library.ai_calls(limit=300)
        self._ai_table.setRowCount(len(calls))
        for row, record in enumerate(calls):
            created = time.strftime("%m-%d %H:%M:%S",
                                    time.localtime(float(record.get("created_at") or 0)))
            values = [
                created, str(record.get("role") or record.get("kind") or ""),
                str(record.get("model") or ""), str(record.get("tools") or ""),
                f"{int(record.get('duration_ms') or 0)} ms",
                f"{float(record.get('est_cost') or 0):.4f}",
                ("成功" if record.get("ok") else f"失败：{record.get('detail') or ''}"),
            ]
            for column, value in enumerate(values):
                self._ai_table.setItem(row, column, QTableWidgetItem(str(value)))

        usage = self._library.ai_usage()
        self._usage_label.setText(
            f"AI 调用 {usage['calls']} 次（失败 {usage['failed']}）· "
            f"累计 {usage['duration_ms'] / 1000:.1f}s · 成本 {usage['cost']:.4f}")

    def _clear_audit(self) -> None:
        self._library.storage.clear_audit()
        self._library.storage.clear_ai_calls()
        self.refresh_audit()
        self._toaster.info("日志已清空（审计与 AI 调用记录）")

    # ---------------------------------------------------------------- 设置载入

    def _load_settings(self) -> None:
        settings = self._settings
        # 开关一律 type=bool：注册表里 Qt 存的是 "true"/"false" 字符串，
        # bool("false") 会得到 True（表现为"取消了云端上传却还是允许"）
        self._allow_cloud.setChecked(
            bool(settings.value("document/allow_cloud", False, type=bool)))
        self._allow_local.setChecked(
            bool(settings.value("document/allow_local", True, type=bool)))
        self._mask_before_upload.setChecked(
            bool(settings.value("document/mask_before_upload", True, type=bool)))
        self._max_chars.setValue(int(settings.value("document/max_chars", 20000) or 20000))
        self._allowed_models.setText(str(settings.value("document/allowed_models", "") or ""))
        price = settings.value("document/price_per_1k", 0.0)
        self._price.setText("" if not price else str(price))
        self._watermark_text.setText(str(settings.value("document/watermark_text", "") or ""))
        self._watermark_footer.setText(str(settings.value("document/watermark_footer", "") or ""))
        self._tracking_id.setText(str(settings.value("document/watermark_tracking", "") or ""))
        rules = str(settings.value("document/mask_rules", ",".join(DEFAULT_MASK_RULES)) or "")
        selected = {item.strip() for item in rules.split(",") if item.strip()}
        for key, box in self._mask_boxes.items():
            box.setChecked(key in selected)
        self.refresh_audit()

    def set_ir(self, ir: Optional[DocumentIR]) -> None:
        self._ir = ir
        if ir is not None:
            self._status.setText(f"当前文档：{ir.title or ir.path}"
                                 f"（{ir.stats()['chars']} 字）")


__all__ = ["SecurityPanel"]
