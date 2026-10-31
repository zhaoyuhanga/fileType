"""墨软文档：两个文档合并面板（MR-DOC-304，P0 核心能力）。

界面流程：选主文档 + 副文档 → 选合并方式与选项 → 「预览合并」看报告与逐条差异 →
「应用到文档」把结果送进查看器 → 「导出结果」写成文件；整个过程都留版本快照。

需要 AI 增强（语义去重、过渡段、摘要）时，如果没配大模型会自动退回本地规则，
并在报告里说明到底用了什么 —— 不会出现"勾了 AI 却什么都没发生"的情况。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.document import (
    EXPORT_TARGETS,
    MERGE_MODES,
    DocumentIR,
    DocumentLibrary,
    MergeOptions,
    MergeResult,
    export_label,
    template_choices,
)
from modu_workbench.core.document import merge as merge_engine
from modu_workbench.ui_kit.components import TaskBar
from modu_workbench.ui_kit.toast import Toaster

from . import widgets as W


class MergePanel(QWidget):
    """两个文档合并。"""

    irChanged = Signal(object)
    statusMessage = Signal(str)

    def __init__(self, library: DocumentLibrary, *, task_bar: Optional[TaskBar] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._task = task_bar
        self._library = library
        self._toaster = Toaster(self)
        self._result: Optional[MergeResult] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        body = QHBoxLayout()
        body.setSpacing(10)

        # ---- 左：选项 ----
        host = QWidget()
        host.setObjectName("actionPanel")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        title = QLabel("合并设置")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(8)
        main_row, self._main_path = W.file_row(self, "主文档")
        form.addRow("主文档", main_row)
        other_row, self._other_path = W.file_row(self, "副文档")
        form.addRow("副文档", other_row)

        self._mode = QComboBox()
        for key, label, note in MERGE_MODES:
            self._mode.addItem(label, key)
            self._mode.setItemData(self._mode.count() - 1, note, Qt.ItemDataRole.ToolTipRole)
        self._mode.currentIndexChanged.connect(self._on_mode_changed)
        form.addRow("合并方式", self._mode)

        self._section_title = QLineEdit()
        self._section_title.setPlaceholderText("章节合并：插入到哪个标题之后")
        form.addRow("章节标题", self._section_title)

        self._key_column = QLineEdit()
        self._key_column.setPlaceholderText("表格按主键合并：主键列名，如「编号」")
        form.addRow("主键列", self._key_column)

        self._term_map = QLineEdit()
        self._term_map.setPlaceholderText("术语统一：旧名=新名,旧名2=新名2")
        form.addRow("术语表", self._term_map)

        self._template = QComboBox()
        for key, label in template_choices():
            self._template.addItem(label, key)
        form.addRow("风格模板", self._template)
        layout.addLayout(form)

        self._dedupe = QCheckBox("语义去重（删重复段落/条款）")
        self._dedupe.setChecked(True)
        layout.addWidget(self._dedupe)
        threshold_row = QHBoxLayout()
        threshold_row.addWidget(QLabel("相似度阈值"))
        self._threshold = QDoubleSpinBox()
        self._threshold.setRange(0.5, 1.0)
        self._threshold.setSingleStep(0.02)
        self._threshold.setValue(0.9)
        threshold_row.addWidget(self._threshold)
        threshold_row.addStretch(1)
        layout.addLayout(threshold_row)

        self._unify_terms = QCheckBox("启用术语统一（需填术语表）")
        layout.addWidget(self._unify_terms)
        self._unify_style = QCheckBox("风格统一（按模板重新排版并重排编号）")
        self._unify_style.setChecked(True)
        layout.addWidget(self._unify_style)
        self._add_toc = QCheckBox("合并后生成目录")
        self._add_toc.setChecked(True)
        layout.addWidget(self._add_toc)
        self._add_summary = QCheckBox("生成合并摘要")
        self._add_summary.setChecked(True)
        layout.addWidget(self._add_summary)
        self._add_transition = QCheckBox("自动生成章节过渡段")
        self._add_transition.setChecked(True)
        layout.addWidget(self._add_transition)
        self._keep_revisions = QCheckBox("保留修订标记与来源标注")
        self._keep_revisions.setChecked(True)
        layout.addWidget(self._keep_revisions)
        self._use_ai = QCheckBox("优先使用大模型做语义增强")
        self._use_ai.setChecked(True)
        layout.addWidget(self._use_ai)

        buttons = QHBoxLayout()
        self._preview_button = QPushButton("预览合并")
        self._preview_button.setObjectName("primaryButton")
        self._preview_button.clicked.connect(self._preview)
        buttons.addWidget(self._preview_button)
        self._apply_button = QPushButton("应用到文档")
        self._apply_button.setEnabled(False)
        self._apply_button.clicked.connect(self._apply)
        buttons.addWidget(self._apply_button)
        layout.addLayout(buttons)

        export_row = QHBoxLayout()
        export_row.addWidget(QLabel("导出："))
        self._export_target = QComboBox()
        for key, label in EXPORT_TARGETS:
            self._export_target.addItem(label, key)
        export_row.addWidget(self._export_target)
        self._export_button = QPushButton("导出结果")
        self._export_button.setEnabled(False)
        self._export_button.clicked.connect(self._export)
        export_row.addWidget(self._export_button)
        export_row.addStretch(1)
        layout.addLayout(export_row)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(host)
        body.addWidget(scroll, 3)

        # ---- 右：报告与差异 ----
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        self._summary = QLabel("选择两个文档后点「预览合并」")
        self._summary.setObjectName("readerStatus")
        self._summary.setWordWrap(True)
        right_layout.addWidget(self._summary)
        report_title = QLabel("合并报告（方式 / 冲突处理 / 变更明细 / 摘要）")
        report_title.setObjectName("sectionTitle")
        right_layout.addWidget(report_title)
        self._report_browser = W.ReportBrowser()
        right_layout.addWidget(self._report_browser, 3)
        changes_title = QLabel("变更明细")
        changes_title.setObjectName("sectionTitle")
        right_layout.addWidget(changes_title)
        self._changes = W.ChangeTable()
        right_layout.addWidget(self._changes, 2)
        diff_title = QLabel("逐条差异（相对主文档）")
        diff_title.setObjectName("sectionTitle")
        right_layout.addWidget(diff_title)
        self._diff = W.DiffBrowser()
        right_layout.addWidget(self._diff, 3)
        body.addWidget(right, 4)

        outer.addLayout(body, 1)
        self._on_mode_changed()

    # ---------------------------------------------------------------- 选项

    def _on_mode_changed(self, *_args: object) -> None:
        mode = self._mode.currentData()
        self._section_title.setEnabled(mode == "section")
        self._key_column.setEnabled(mode == "table_key")

    def options(self) -> MergeOptions:
        terms: dict[str, str] = {}
        for chunk in self._term_map.text().split(","):
            if "=" in chunk:
                old, _, new = chunk.partition("=")
                if old.strip():
                    terms[old.strip()] = new.strip()
        return MergeOptions(
            mode=self._mode.currentData() or "append",
            section_title=self._section_title.text().strip(),
            key_column=self._key_column.text().strip(),
            dedupe=self._dedupe.isChecked(),
            dedupe_threshold=self._threshold.value(),
            unify_terms=self._unify_terms.isChecked() and bool(terms),
            term_map=terms,
            unify_style=self._unify_style.isChecked(),
            template=self._template.currentData() or "general",
            add_toc=self._add_toc.isChecked(),
            add_summary=self._add_summary.isChecked(),
            add_transition=self._add_transition.isChecked(),
            keep_revisions=self._keep_revisions.isChecked(),
        )

    # ---------------------------------------------------------------- 执行

    def _preview(self) -> None:
        main_path = self._main_path.text().strip()
        other_path = self._other_path.text().strip()
        if not main_path or not other_path:
            self._toaster.info("请先选择主文档与副文档")
            return
        if main_path == other_path:
            self._toaster.info("主文档与副文档是同一个文件：请选两个不同文档")
            return
        W.busy(self._task, "正在合并两个文档…")
        try:
            result = self._library.merge_paths(main_path, other_path, self.options(),
                                               use_ai=self._use_ai.isChecked())
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"合并失败：{error}")
            W.idle(self._task, f"合并失败：{error}")
            return
        self._result = result
        self._apply_button.setEnabled(True)
        self._export_button.setEnabled(True)
        self._show(result)
        W.idle(self._task, "合并完成：" + result.report.mode_label)

    def _show(self, result: MergeResult) -> None:
        report = result.report
        lines = report.report_lines()
        lines.append("")
        lines.append("摘要：")
        lines.extend(f"  {line}" for line in (report.summary or "（无）").splitlines())
        self._report_browser.set_lines(lines)
        self._changes.set_changes(report.changes)
        self._diff.set_diff(report.diff)
        ai_note = "已使用大模型" if report.ai_used else "未配置/未使用大模型（本地规则完成）"
        summary = (f"{report.mode_label}：{len(report.changes)} 处变更"
                   f"（AI 参与 {report.ai_change_count} 处）· 去重 {report.deduped} 处 · {ai_note}")
        if report.conflicts:
            summary += f" · 冲突 {len(report.conflicts)} 项（见报告）"
        self._summary.setText(summary)
        self.statusMessage.emit(summary)

    def _apply(self) -> None:
        if self._result is None:
            return
        ir = self._result.ir
        # 合并结果是"还没落盘的新文档"：断掉主文档路径，避免 Ctrl+S / 自动保存
        # 直接把原文件替换成合并稿（查看器遇到空路径会走「另存为」，见 viewer.save）。
        # 版本快照仍记在主文档名下（与合并时一致），所以把文档 id 带过去，
        # 免得到一个 path 为空、在文档库里看着像"垃圾行"的记录。
        document_id = self._library.document_id_of(ir)
        if ir.path:
            ir.metadata.setdefault("merge_source_path", ir.path)
            ir.path = ""
        if document_id:
            setattr(ir, "_document_id", document_id)
        self.irChanged.emit(ir)
        self._toaster.success("合并结果已送入查看器：确认后「保存」会另存为新文件")
        W.note(self._task, "合并结果已应用到当前文档（保存时另存为新文件，不覆盖原稿）")

    def _export(self) -> None:
        if self._result is None:
            return
        target = self._export_target.currentData() or "docx"
        try:
            written = self._library.export(
                self._result.ir, target,
                name=f"{self._result.ir.title or '合并结果'}-合并")
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"导出失败：{error}")
            return
        self._result.report.output_path = str(written.path)
        self._library.storage.save_merge_report(self._result.report)
        message = f"已导出 {export_label(target)}：{written.path}"
        self._toaster.success(message)
        W.note(self._task, message)

    # ---------------------------------------------------------------- 兼容

    def set_ir(self, ir: Optional[DocumentIR]) -> None:
        """把当前文档自动填进「主文档」（少一步选择）。"""
        if ir is not None and ir.path and not self._main_path.text().strip():
            self._main_path.setText(ir.path)

    def capability_hint(self) -> str:
        return merge_engine.capability_text()


__all__ = ["MergePanel"]
