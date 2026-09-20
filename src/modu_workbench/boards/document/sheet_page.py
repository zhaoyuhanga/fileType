"""墨软文档：自动填充与计算面板（MR-DOC-302）。

- **自动填充**：识别等差数列 / 日期 / 工作日 / 月份 / 星期 / 中文序号 / 文本序号 / 公式，
  一键继续填充 N 行（公式填充会把相对引用平移，和 Excel 的下拉一致）；
- **智能填充**：按"示例输入 → 示例输出"反推规则（拆分姓名、提取号码/邮箱/金额、
  加前后缀、大小写、编号补零、按关键词归类），可一次产出多列；
- **公式计算**：本地 Excel 子集引擎（跨表引用、数组公式、循环引用检测），
  还能用自然语言让 AI 生成公式；
- **数据统计**：求和/平均/计数/分组/透视表/文本图表 + 计算检查。

所有改写都会写回当前文档并留下版本快照（可回滚）。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.document import sheet as sheet_engine
from modu_workbench.core.document import (
    Block,
    DocumentIR,
    DocumentLibrary,
    FormulaError,
    TableData,
    autofill,
    chart_series,
    check_formulas,
    column_letters,
    group_by,
    infer_patterns,
    parse_cell_ref,
    pivot,
    sheet_name_of,
    smart_fill,
    summarize_table,
)
from modu_workbench.ui_kit.components import TaskBar
from modu_workbench.ui_kit.toast import Toaster

from . import widgets as W

AGG_CHOICES = (("sum", "求和"), ("count", "计数"), ("average", "平均"),
               ("max", "最大值"), ("min", "最小值"))


class SheetPanel(QWidget):
    """表格计算面板（任何带表格的文档都能用）。"""

    irChanged = Signal(object)
    statusMessage = Signal(str)

    def __init__(self, library: DocumentLibrary, *, task_bar: Optional[TaskBar] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._task = task_bar
        self._library = library
        self._toaster = Toaster(self)
        self._ir: Optional[DocumentIR] = None
        self._table_index = 0
        self._table_backup: dict[int, list[list[str]]] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        head = QHBoxLayout()
        head.addWidget(QLabel("工作表"))
        self._table_pick = QComboBox()
        self._table_pick.currentIndexChanged.connect(self._on_table_changed)
        head.addWidget(self._table_pick)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(lambda: self.set_ir(self._ir))
        head.addWidget(refresh)
        head.addStretch(1)
        self._shape_label = QLabel("")
        self._shape_label.setObjectName("viewerMetaLabel")
        head.addWidget(self._shape_label)
        outer.addLayout(head)

        self._table = QTableWidget(0, 0)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setMaximumHeight(220)
        outer.addWidget(self._table)

        body = QHBoxLayout()
        body.setSpacing(10)

        tools_host = QWidget()
        tools_host.setObjectName("actionPanel")
        tools = QVBoxLayout(tools_host)
        tools.setContentsMargins(12, 12, 12, 12)
        tools.setSpacing(8)

        # ---- 自动填充 ----
        tools.addWidget(self._section_title("自动填充"))
        fill_form = QFormLayout()
        fill_form.setSpacing(6)
        self._fill_column = QComboBox()
        fill_form.addRow("填充列", self._fill_column)
        self._fill_count = QSpinBox()
        self._fill_count.setRange(1, 2000)
        self._fill_count.setValue(5)
        fill_form.addRow("行数", self._fill_count)
        self._fill_custom = QLineEdit()
        self._fill_custom.setPlaceholderText("自定义列表（逗号分隔，可留空）")
        fill_form.addRow("自定义", self._fill_custom)
        fill_button = QPushButton("识别规律并填充")
        fill_button.clicked.connect(self._do_autofill)
        fill_form.addRow("", fill_button)
        tools.addLayout(fill_form)

        # ---- 智能填充 ----
        tools.addWidget(self._section_title("智能填充"))
        smart_form = QFormLayout()
        smart_form.setSpacing(6)
        self._smart_input = QComboBox()
        smart_form.addRow("示例输入列", self._smart_input)
        self._smart_output = QLineEdit()
        self._smart_output.setPlaceholderText("示例输出列（逗号分隔，可多列）")
        smart_form.addRow("示例输出", self._smart_output)
        self._smart_target = QComboBox()
        smart_form.addRow("待处理列", self._smart_target)
        smart_button = QPushButton("推断规则并填充")
        smart_button.clicked.connect(self._do_smart_fill)
        smart_form.addRow("", smart_button)
        tools.addLayout(smart_form)

        # ---- 公式 ----
        tools.addWidget(self._section_title("公式计算"))
        formula_form = QFormLayout()
        formula_form.setSpacing(6)
        self._formula = QLineEdit()
        self._formula.setPlaceholderText("例如 =SUM(B2:B9) 或 {=SUM(A2:A9*B2:B9)}")
        formula_form.addRow("公式", self._formula)
        self._formula_cell = QLineEdit()
        self._formula_cell.setPlaceholderText("写回单元格，如 D2（可留空只看结果）")
        formula_form.addRow("单元格", self._formula_cell)
        formula_row = QHBoxLayout()
        compute = QPushButton("计算")
        compute.clicked.connect(lambda: self._do_compute(write=False))
        formula_row.addWidget(compute)
        write_back = QPushButton("计算并写回")
        write_back.clicked.connect(lambda: self._do_compute(write=True))
        formula_row.addWidget(write_back)
        formula_holder = QWidget()
        formula_holder.setLayout(formula_row)
        formula_form.addRow("", formula_holder)
        self._ai_prompt = QLineEdit()
        self._ai_prompt.setPlaceholderText("自然语言需求，如「按单价乘数量算出金额」")
        formula_form.addRow("AI 生成", self._ai_prompt)
        ai_button = QPushButton("让 AI 写公式")
        ai_button.clicked.connect(self._do_ai_formula)
        formula_form.addRow("", ai_button)
        tools.addLayout(formula_form)

        # ---- 统计 ----
        tools.addWidget(self._section_title("数据统计"))
        stat_form = QFormLayout()
        stat_form.setSpacing(6)
        self._stat_group = QComboBox()
        stat_form.addRow("分组列", self._stat_group)
        self._stat_value = QComboBox()
        stat_form.addRow("数值列", self._stat_value)
        self._stat_how = QComboBox()
        for key, label in AGG_CHOICES:
            self._stat_how.addItem(label, key)
        stat_form.addRow("方式", self._stat_how)
        stat_row = QHBoxLayout()
        stat_button = QPushButton("分组统计")
        stat_button.clicked.connect(self._do_statistics)
        stat_row.addWidget(stat_button)
        pivot_button = QPushButton("生成透视表")
        pivot_button.clicked.connect(self._do_pivot)
        stat_row.addWidget(pivot_button)
        stat_holder = QWidget()
        stat_holder.setLayout(stat_row)
        stat_form.addRow("", stat_holder)
        self._pivot_column = QComboBox()
        stat_form.addRow("透视列字段", self._pivot_column)
        check_button = QPushButton("检查公式（错误/循环引用）")
        check_button.clicked.connect(self._do_check)
        stat_form.addRow("", check_button)
        tools.addLayout(stat_form)

        # ---- 排序与筛选 ----
        tools.addWidget(self._section_title("排序与筛选（表格查看）"))
        sort_form = QFormLayout()
        sort_form.setSpacing(6)
        self._sort_column = QComboBox()
        sort_form.addRow("排序列", self._sort_column)
        self._sort_desc = QCheckBox("降序（数值按大小、文本按字符）")
        sort_form.addRow("顺序", self._sort_desc)
        sort_row = QHBoxLayout()
        sort_button = QPushButton("排序")
        sort_button.clicked.connect(self._do_sort)
        sort_row.addWidget(sort_button)
        filter_button = QPushButton("筛选")
        filter_button.clicked.connect(self._do_filter)
        sort_row.addWidget(filter_button)
        restore_button = QPushButton("还原表格")
        restore_button.setToolTip("恢复到本次排序/筛选之前的内容")
        restore_button.clicked.connect(self._restore_table)
        sort_row.addWidget(restore_button)
        sort_holder = QWidget()
        sort_holder.setLayout(sort_row)
        sort_form.addRow("", sort_holder)
        self._filter_column = QComboBox()
        sort_form.addRow("筛选列", self._filter_column)
        self._filter_mode = QComboBox()
        for key, label in sheet_engine.FILTER_MODES:
            self._filter_mode.addItem(label, key)
        sort_form.addRow("筛选方式", self._filter_mode)
        self._filter_value = QLineEdit()
        self._filter_value.setPlaceholderText("筛选值（包含/等于/大于/小于时用）")
        sort_form.addRow("筛选值", self._filter_value)
        tools.addLayout(sort_form)
        tools.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(tools_host)
        body.addWidget(scroll, 3)

        self._report_browser = W.ReportBrowser()
        body.addWidget(self._report_browser, 2)
        outer.addLayout(body, 1)

    # ---------------------------------------------------------------- 基础

    @staticmethod
    def _section_title(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def set_ir(self, ir: Optional[DocumentIR]) -> None:
        self._ir = ir
        current = max(0, self._table_pick.currentIndex())
        self._table_pick.blockSignals(True)
        self._table_pick.clear()
        for index, table in enumerate(ir.tables() if ir else []):
            self._table_pick.addItem(table.name or f"表 {index + 1}")
        if self._table_pick.count() == 0:
            self._table_pick.addItem("（没有表格）")
        self._table_pick.setCurrentIndex(min(current, self._table_pick.count() - 1))
        self._table_pick.blockSignals(False)
        self._table_index = max(0, self._table_pick.currentIndex())
        self._refresh()

    def _on_table_changed(self, index: int) -> None:
        self._table_index = max(0, index)
        self._refresh()

    def _current_table(self) -> Optional[TableData]:
        if self._ir is None:
            return None
        tables = self._ir.tables()
        if not tables:
            return None
        return tables[min(self._table_index, len(tables) - 1)]

    def _refresh(self) -> None:
        table = self._current_table()
        if table is None:
            self._table.setRowCount(0)
            self._table.setColumnCount(0)
            self._shape_label.setText("当前文档没有表格")
            self._report_browser.set_lines([
                "这份文档没有表格。可以先打开 xlsx/csv 文件，"
                "或用「墨软转换」把 CSV ↔ XLSX 互转后再回到这里计算。"])
            self._fill_columns([])
            return
        rows = table.normalized()
        self._table.setRowCount(len(rows))
        self._table.setColumnCount(max(1, table.width))
        header_names = table.header_row() + [""] * (max(1, table.width) - len(table.header_row()))
        self._table.setHorizontalHeaderLabels([
            f"{column_letters(index)}｜{name}" if name else column_letters(index)
            for index, name in enumerate(header_names)])
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                self._table.setItem(row_index, column_index, QTableWidgetItem(value))
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.resizeColumnsToContents()
        self._shape_label.setText(f"{table.height} 行 × {table.width} 列")
        self._fill_columns([name for name in table.header_row()])

    def _fill_columns(self, names: list[str]) -> None:
        for combo in (self._fill_column, self._smart_input, self._smart_target,
                      self._stat_group, self._stat_value, self._pivot_column,
                      self._sort_column, self._filter_column):
            previous = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems([name for name in names if name.strip()] or ["（无表头）"])
            if previous:
                index = combo.findText(previous)
                if index >= 0:
                    combo.setCurrentIndex(index)
            combo.blockSignals(False)

    def _column_values(self, table: TableData, name: str) -> list[str]:
        index = table.column_index(name)
        if index < 0:
            return []
        return [row[index] if index < len(row) else "" for row in table.body_rows()]

    def _notify(self, message: str, ir: Optional[DocumentIR]) -> None:
        self._report_browser.set_lines([message])
        self.statusMessage.emit(message)
        if ir is not None:
            self.irChanged.emit(ir)
        self.set_ir(ir)

    # ---------------------------------------------------------------- 自动填充

    def _do_autofill(self) -> None:
        table = self._current_table()
        if table is None or self._ir is None:
            self._toaster.info("当前文档没有表格")
            return
        column = self._fill_column.currentText()
        values = self._column_values(table, column)
        if not values:
            self._toaster.info(f"列「{column}」没有可参考的数据")
            return
        index = table.column_index(column)
        if index < 0:
            self._toaster.error(f"找不到列：{column}")
            return
        custom = [item.strip() for item in self._fill_custom.text().split(",") if item.strip()]
        filled, spec = autofill(values, self._fill_count.value(), custom_list=custom or None)
        for value in filled:
            row = [""] * table.width
            row[index] = value
            table.rows.append(row)
        self._library.snapshot(self._ir, label=f"自动填充（{spec.label}）")
        self._notify(f"已按「{spec.label}」在「{column}」列填充 {len(filled)} 行："
                     + "、".join(filled[:8]) + ("…" if len(filled) > 8 else ""), self._ir)

    # ---------------------------------------------------------------- 智能填充

    def _do_smart_fill(self) -> None:
        table = self._current_table()
        if table is None or self._ir is None:
            self._toaster.info("当前文档没有表格")
            return
        examples = self._column_values(table, self._smart_input.currentText())
        output_names = [item.strip() for item in self._smart_output.text().split(",")
                        if item.strip()]
        if not output_names:
            self._toaster.info("请填写示例输出列（逗号分隔）")
            return
        targets = [self._column_values(table, name) for name in output_names]
        for name, values in zip(output_names, targets):
            if len(values) != len(examples):
                self._toaster.error(f"示例列「{name}」的行数与输入列不一致")
                return
        new_values = self._column_values(table, self._smart_target.currentText())
        if not new_values:
            self._toaster.info("待处理列没有数据")
            return
        specs = infer_patterns(examples, targets)
        columns = smart_fill(new_values, specs)
        labels = [spec.label if spec else "无法识别" for spec in specs]
        if not any(specs):
            self._toaster.info("没能从示例里推断出规则：" + "、".join(labels))
            return
        next_column = table.width
        for name, spec, values in zip(output_names, specs, columns):
            label = spec.label if spec else "未识别"
            if table.header and table.rows:
                table.rows[0] = list(table.rows[0]) + [f"{name}（{label}）"]
            start = 1 if (table.header and len(table.rows) > 1) else 0
            for offset, row in enumerate(table.rows[start:]):
                while len(row) <= next_column:
                    row.append("")
                row[next_column] = values[offset] if offset < len(values) else ""
            next_column += 1
        self._library.snapshot(self._ir, label="智能填充")
        self._notify("智能填充：" + "、".join(labels) + f"；已写入 {len(output_names)} 列", self._ir)

    # ---------------------------------------------------------------- 公式

    def _do_compute(self, *, write: bool = False) -> None:
        table = self._current_table()
        if table is None or self._ir is None:
            self._toaster.info("当前文档没有表格")
            return
        formula = self._formula.text().strip()
        if not formula:
            self._toaster.info("请填写公式")
            return
        try:
            value = self._library.calculate(formula, self._ir, table_index=self._table_index)
        except FormulaError as error:
            self._report_browser.set_lines([f"公式错误：{error}"])
            self._toaster.error(f"公式错误：{error}")
            return
        lines = [f"{formula} = {value}"]
        cell = self._formula_cell.text().strip()
        if write and cell:
            reference = parse_cell_ref(cell, default_sheet=sheet_name_of(table))
            if reference is None:
                self._toaster.error(f"单元格地址无效：{cell}")
                return
            while len(table.rows) <= reference.row:
                table.rows.append([""] * table.width)
            row = table.rows[reference.row]
            while len(row) <= reference.column:
                row.append("")
            row[reference.column] = str(value)
            self._library.snapshot(self._ir, label=f"公式写回 {cell}")
            lines.append(f"已写入单元格 {cell}")
        elif write:
            lines.append("提示：填「单元格」后可把结果写回表格")
        self._notify("；".join(lines), self._ir)

    def _do_ai_formula(self) -> None:
        table = self._current_table()
        description = self._ai_prompt.text().strip()
        if not description:
            self._toaster.info("请描述需求，例如「按单价乘数量算出金额」")
            return
        ai = self._library.ai
        if ai is None or not ai.available:
            self._toaster.info("AI 生成公式需要先在「设置 → 大模型」配置文字模型")
            return
        header = " | ".join(table.header_row()) if table is not None else ""
        try:
            reply = ai.chat_callback()([
                {"role": "system", "content": "你是表格公式助手，只输出一条以 = 开头的公式。"},
                {"role": "user", "content": f"需求：{description}\n表头：{header}"},
            ])
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"AI 生成失败：{error}")
            return
        from modu_workbench.core.document.sheet import parse_formula_reply

        formula = parse_formula_reply(reply)
        if not formula:
            self._toaster.error("模型没有给出可用的公式，请补充描述后重试")
            return
        self._formula.setText(formula)
        self._report_browser.set_lines([
            f"AI 建议公式：{formula}",
            "确认无误后点「计算」查看结果，或填好单元格再点「计算并写回」。"])
        self.statusMessage.emit(f"AI 建议公式：{formula}")

    # ---------------------------------------------------------------- 统计与检查

    def _do_statistics(self) -> None:
        table = self._current_table()
        if table is None:
            self._toaster.info("当前文档没有表格")
            return
        group = self._stat_group.currentText()
        value = self._stat_value.currentText()
        how = self._stat_how.currentData() or "sum"
        lines: list[str] = []
        if group.strip() and group != "（无表头）":
            try:
                rows = group_by(table, group, value_column=value, how=how)
            except ValueError as error:
                self._toaster.error(str(error))
                return
            lines.append(f"按「{group}」分组，对「{value}」{self._stat_how.currentText()}：")
            lines.extend(f"  {key or '（空）'}：{item:g}" for key, item in rows[:30])
            chart = self.chart_text(group, value)
            if chart:
                lines.extend(["", chart])
        summary = summarize_table(table)
        if summary:
            lines.append("")
            lines.append("整表概况：")
            lines.extend(
                f"  {item['column']}：{item['count']} 项，合计 {item['sum']:g}，"
                f"平均 {item['average']:g}，范围 {item['min']:g} ~ {item['max']:g}"
                for item in summary[:8])
        self._report_browser.set_lines(lines or ["没有可统计的数据"])
        self.statusMessage.emit("统计完成")

    def _do_pivot(self) -> None:
        table = self._current_table()
        if table is None or self._ir is None:
            self._toaster.info("当前文档没有表格")
            return
        try:
            result = pivot(table, self._stat_group.currentText(),
                           self._pivot_column.currentText(), self._stat_value.currentText(),
                           how=self._stat_how.currentData() or "sum")
        except ValueError as error:
            self._toaster.error(str(error))
            return
        self._ir.blocks.append(Block(kind="table", table=result))
        self._library.snapshot(self._ir, label="生成透视表")
        lines = ["透视表已插入文档末尾：", "  " + " | ".join(result.header_row())]
        lines.extend("  " + " | ".join(row) for row in result.body_rows()[:12])
        self._notify("\n".join(lines), self._ir)

    def _do_check(self) -> None:
        if self._ir is None:
            return
        issues = check_formulas(self._ir.tables())
        if not issues:
            self._report_browser.set_lines(["公式检查通过：没有错误值，也没有循环引用。"])
            self._toaster.success("公式检查通过")
            self.statusMessage.emit("公式检查通过")
            return
        lines = [f"发现 {len(issues)} 处公式问题："]
        lines.extend(f"  {item['cell']} {item['code']}：{item['detail']}\n    {item['formula']}"
                     for item in issues[:20])
        self._report_browser.set_lines(lines)
        self._toaster.error(f"发现 {len(issues)} 处公式问题")
        self.statusMessage.emit(f"公式检查：{len(issues)} 处问题")

    # ---------------------------------------------------------------- 排序与筛选

    def _backup_table(self, table: TableData) -> None:
        key = self._table_index
        if key not in self._table_backup:
            self._table_backup[key] = [list(row) for row in table.rows]

    def _do_sort(self) -> None:
        table = self._current_table()
        if table is None or self._ir is None:
            self._toaster.info("当前文档没有表格")
            return
        try:
            sorted_table = sheet_engine.sort_table(
                table, self._sort_column.currentText(), descending=self._sort_desc.isChecked())
        except ValueError as error:
            self._toaster.error(str(error))
            return
        self._backup_table(table)
        table.rows = sorted_table.rows
        table.meta.update(sorted_table.meta)
        self._library.snapshot(self._ir, label="表格排序")
        self._notify(f"已按「{self._sort_column.currentText()}」"
                     f"{'降序' if self._sort_desc.isChecked() else '升序'}排序"
                     f"（{len(table.rows) - 1} 行）", self._ir)

    def _do_filter(self) -> None:
        table = self._current_table()
        if table is None or self._ir is None:
            self._toaster.info("当前文档没有表格")
            return
        try:
            filtered = sheet_engine.filter_table(
                table, self._filter_column.currentText(), self._filter_value.text(),
                mode=self._filter_mode.currentData() or "contains")
        except ValueError as error:
            self._toaster.error(str(error))
            return
        self._backup_table(table)
        table.rows = filtered.rows
        table.meta.update(filtered.meta)
        matched = len(table.rows) - 1
        removed = int(filtered.meta.get("filtered_out") or 0)
        self._library.snapshot(self._ir, label="表格筛选")
        self._notify(f"筛选「{self._filter_column.currentText()}」"
                     f"{self._filter_mode.currentText()}「{self._filter_value.text()}」："
                     f"保留 {matched} 行，过滤掉 {removed} 行（可点「还原表格」恢复）", self._ir)

    def _restore_table(self) -> None:
        table = self._current_table()
        if table is None or self._ir is None:
            return
        backup = self._table_backup.pop(self._table_index, None)
        if backup is None:
            self._toaster.info("没有可还原的表格（本次没有排序或筛选过）")
            return
        table.rows = [list(row) for row in backup]
        table.meta.pop("sorted_by", None)
        table.meta.pop("filtered_by", None)
        table.meta.pop("filtered_out", None)
        self._library.snapshot(self._ir, label="还原表格")
        self._notify("已还原到排序/筛选之前的表格内容", self._ir)

    # ---------------------------------------------------------------- 图表

    def chart_text(self, label_column: str, value_column: str) -> str:
        """简单的文本柱状图（不依赖 QtCharts，复制到报告里也看得懂）。"""
        table = self._current_table()
        if table is None:
            return ""
        try:
            data = chart_series(table, label_column, value_column)
        except ValueError:
            return ""
        if not data["labels"]:
            return ""
        top = max(data["values"]) or 1.0
        rows = [f"{data['title']}"]
        for label, value in zip(data["labels"], data["values"]):
            bar = "█" * max(1, int(round(value / top * 24)))
            rows.append(f"  {label[:12]:<12}{bar} {value:g}")
        return "\n".join(rows)


__all__ = ["SheetPanel"]
