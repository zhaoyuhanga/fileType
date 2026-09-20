"""墨软文档：PDF 工具箱对话框（拆分 / 合并 / 压缩 / 加密 / 水印 / 表单 / 转图片）。

需求 6.2 点名的 PDF 页面级能力都在这里，与 `core/document/pdf_tools.py` 一一对应。
界面只负责收参数、显示结果与原因，所有产物都写进「输出目录」，**绝不覆盖用户已有文件**
（同名自动加序号）。

对话框刻意做成独立窗口：PDF 操作常常一次做好几步（先拆分、再压缩、最后加密），
把所有步骤的日志留在同一个窗口里比来回弹框更好用。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.document import DocumentLibrary, pdf_tools
from modu_workbench.core.document import security as document_security
from modu_workbench.core.document.writer import unique_output_path
from modu_workbench.ui_kit.toast import Toaster

from . import widgets as W

SPLIT_MODES = (("each", "每页一个文件"), ("every", "每 N 页一组"), ("ranges", "按页码段"))
COMPRESS_LEVELS = (("light", "轻度（重写内容流）"), ("medium", "中度（+去重对象）"),
                   ("strong", "高强度（+清理元数据）"))


class PdfToolsDialog(QDialog):
    """PDF 工具箱。"""

    def __init__(self, library: DocumentLibrary, *, source: str = "",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("PDF 工具箱")
        self.resize(940, 680)
        self._library = library
        self._toaster = Toaster(self)
        self._source = source
        self._fields: list[dict] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        layout.addLayout(self._build_source_row())
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_split_tab(), "拆分与抽取")
        self._tabs.addTab(self._build_merge_tab(), "合并")
        self._tabs.addTab(self._build_secure_tab(), "压缩与加密")
        self._tabs.addTab(self._build_watermark_tab(), "水印与旋转")
        self._tabs.addTab(self._build_comment_tab(), "批注")
        self._tabs.addTab(self._build_form_tab(), "表单与图片")
        layout.addWidget(self._tabs, 3)

        log_title = QLabel("操作日志（每一步的产物与原因都记在这里）")
        log_title.setObjectName("sectionTitle")
        layout.addWidget(log_title)
        self._log = W.ReportBrowser()
        self._log.setMinimumHeight(120)
        layout.addWidget(self._log, 2)

        buttons = QDialogButtonBox()
        open_out = QPushButton("打开输出目录")
        open_out.clicked.connect(self._open_output_dir)
        buttons.addButton(open_out, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton("关闭", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if source:
            self._source_field.setText(source)
        self._refresh_source()
        self._append(f"PDF 工具箱就绪。{pdf_tools.capability_text()}")

    # ---------------------------------------------------------------- 构建

    def _build_source_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("源 PDF："))
        self._source_field = QLineEdit()
        self._source_field.setPlaceholderText("选择要处理的 PDF")
        self._source_field.editingFinished.connect(self._refresh_source)
        row.addWidget(self._source_field, 1)
        pick = QPushButton("选择…")
        pick.clicked.connect(self._pick_source)
        row.addWidget(pick)
        row.addWidget(QLabel("输出目录："))
        self._output_dir = QLineEdit(str(library_output_dir(self._library)))
        row.addWidget(self._output_dir, 1)
        browse = QPushButton("选择…")
        browse.clicked.connect(self._pick_output_dir)
        row.addWidget(browse)
        self._info_label = QLabel("")
        self._info_label.setObjectName("viewerMetaLabel")
        row.addWidget(self._info_label)
        return row

    def _section(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def _build_split_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)
        layout.addWidget(self._section("拆分：每页 / 每 N 页 / 按页码段"))
        form = QFormLayout()
        form.setSpacing(6)
        self._split_mode = QComboBox()
        for key, label in SPLIT_MODES:
            self._split_mode.addItem(label, key)
        form.addRow("拆分方式", self._split_mode)
        self._split_every = QSpinBox()
        self._split_every.setRange(1, 200)
        self._split_every.setValue(1)
        form.addRow("每 N 页", self._split_every)
        self._split_ranges = QLineEdit()
        self._split_ranges.setPlaceholderText("页码段，如 1-3,5,8-（每段成为一个文件）")
        form.addRow("页码段", self._split_ranges)
        layout.addLayout(form)
        split = QPushButton("开始拆分")
        split.setObjectName("primaryButton")
        split.clicked.connect(self._do_split)
        layout.addWidget(split)

        layout.addWidget(self._section("抽取：把指定页另存为一份新 PDF"))
        form2 = QFormLayout()
        form2.setSpacing(6)
        self._extract_pages = QLineEdit()
        self._extract_pages.setPlaceholderText("留空 = 全部页面；如 2-4,7")
        form2.addRow("页码", self._extract_pages)
        self._extract_name = QLineEdit()
        self._extract_name.setPlaceholderText("输出文件名（可留空）")
        form2.addRow("文件名", self._extract_name)
        layout.addLayout(form2)
        extract = QPushButton("抽取指定页")
        extract.clicked.connect(self._do_extract)
        layout.addWidget(extract)
        layout.addStretch(1)
        return page

    def _build_merge_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)
        layout.addWidget(self._section("合并多份 PDF（顺序即合并顺序）"))
        self._merge_list = QListWidget()
        self._merge_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self._merge_list, 1)
        row = QHBoxLayout()
        add = QPushButton("添加文件…")
        add.clicked.connect(self._add_merge_files)
        row.addWidget(add)
        add_current = QPushButton("加入当前源文件")
        add_current.clicked.connect(self._add_current_to_merge)
        row.addWidget(add_current)
        remove = QPushButton("移除选中")
        remove.clicked.connect(self._remove_merge_selected)
        row.addWidget(remove)
        up = QPushButton("上移")
        up.clicked.connect(lambda: self._move_merge(-1))
        row.addWidget(up)
        down = QPushButton("下移")
        down.clicked.connect(lambda: self._move_merge(1))
        row.addWidget(down)
        clear = QPushButton("清空")
        clear.clicked.connect(lambda: self._merge_list.clear())
        row.addWidget(clear)
        row.addStretch(1)
        layout.addLayout(row)

        form = QFormLayout()
        form.setSpacing(6)
        self._merge_bookmarks = QCheckBox("为每份来源加书签（按文件名）")
        self._merge_bookmarks.setChecked(True)
        form.addRow("书签", self._merge_bookmarks)
        self._merge_name = QLineEdit()
        self._merge_name.setPlaceholderText("输出文件名（可留空）")
        form.addRow("文件名", self._merge_name)
        layout.addLayout(form)
        merge = QPushButton("开始合并")
        merge.setObjectName("primaryButton")
        merge.clicked.connect(self._do_merge)
        layout.addWidget(merge)
        return page

    def _build_secure_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)

        layout.addWidget(self._section("压缩"))
        form = QFormLayout()
        form.setSpacing(6)
        self._compress_level = QComboBox()
        for key, label in COMPRESS_LEVELS:
            self._compress_level.addItem(label, key)
        self._compress_level.setCurrentIndex(1)
        form.addRow("压缩级别", self._compress_level)
        layout.addLayout(form)
        compress = QPushButton("压缩并另存")
        compress.clicked.connect(self._do_compress)
        layout.addWidget(compress)

        layout.addWidget(self._section("加密（设置打开密码与打印/复制权限）"))
        secure_form = QFormLayout()
        secure_form.setSpacing(6)
        self._user_password = QLineEdit()
        self._user_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._user_password.setPlaceholderText("打开密码（必填）")
        secure_form.addRow("打开密码", self._user_password)
        self._owner_password = QLineEdit()
        self._owner_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._owner_password.setPlaceholderText("所有者密码（可留空，默认同打开密码）")
        secure_form.addRow("所有者密码", self._owner_password)
        self._allow_printing = QCheckBox("允许打印")
        self._allow_printing.setChecked(True)
        secure_form.addRow("打印", self._allow_printing)
        self._allow_copying = QCheckBox("允许复制文本")
        secure_form.addRow("复制", self._allow_copying)
        self._allow_modifying = QCheckBox("允许修改")
        secure_form.addRow("修改", self._allow_modifying)
        layout.addLayout(secure_form)
        encrypt = QPushButton("加密并另存")
        encrypt.setObjectName("primaryButton")
        encrypt.clicked.connect(self._do_encrypt)
        layout.addWidget(encrypt)

        layout.addWidget(self._section("解密（另存为不加密副本）"))
        decrypt_form = QFormLayout()
        decrypt_form.setSpacing(6)
        self._decrypt_password = QLineEdit()
        self._decrypt_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._decrypt_password.setPlaceholderText("这份 PDF 的打开密码")
        decrypt_form.addRow("密码", self._decrypt_password)
        layout.addLayout(decrypt_form)
        decrypt = QPushButton("解密并另存")
        decrypt.clicked.connect(self._do_decrypt)
        layout.addWidget(decrypt)
        layout.addStretch(1)
        return page

    def _build_watermark_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)
        layout.addWidget(self._section("逐页水印（浅灰文字 + 页脚 + 追踪标识）"))
        form = QFormLayout()
        form.setSpacing(6)
        self._watermark_text = QLineEdit()
        self._watermark_text.setPlaceholderText("例如：内部资料 请勿外传")
        form.addRow("水印文字", self._watermark_text)
        self._watermark_footer = QLineEdit()
        self._watermark_footer.setPlaceholderText("页脚说明（可留空）")
        form.addRow("页脚", self._watermark_footer)
        row = QHBoxLayout()
        self._tracking_id = QLineEdit()
        self._tracking_id.setPlaceholderText("追踪标识（导出后可按它追查来源）")
        generate = QPushButton("生成")
        generate.clicked.connect(lambda: self._tracking_id.setText(
            document_security.new_tracking_id("PDF")))
        row.addWidget(self._tracking_id, 1)
        row.addWidget(generate)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow("追踪标识", holder)
        layout.addLayout(form)
        watermark = QPushButton("加水印并另存")
        watermark.setObjectName("primaryButton")
        watermark.clicked.connect(self._do_watermark)
        layout.addWidget(watermark)

        layout.addWidget(self._section("旋转页面"))
        rotate_form = QFormLayout()
        rotate_form.setSpacing(6)
        self._rotate_angle = QComboBox()
        for angle in (90, 180, 270):
            self._rotate_angle.addItem(f"{angle}°", angle)
        rotate_form.addRow("角度", self._rotate_angle)
        self._rotate_pages = QLineEdit()
        self._rotate_pages.setPlaceholderText("留空 = 全部页面；如 1-3")
        rotate_form.addRow("页码", self._rotate_pages)
        layout.addLayout(rotate_form)
        rotate = QPushButton("旋转并另存")
        rotate.clicked.connect(self._do_rotate)
        layout.addWidget(rotate)
        layout.addStretch(1)
        return page

    def _build_comment_tab(self) -> QWidget:
        """批注：读取已有标注 + 写入新的 FreeText 标注。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)
        layout.addWidget(self._section("PDF 批注（真实 /FreeText 标注，写在页面上）"))
        row = QHBoxLayout()
        read = QPushButton("读取已有批注")
        read.clicked.connect(self._load_annotations)
        row.addWidget(read)
        add_row = QPushButton("添加一行")
        add_row.clicked.connect(lambda: self._annotations.addItem(self._new_annotation_item()))
        row.addWidget(add_row)
        remove = QPushButton("移除选中行")
        remove.clicked.connect(self._remove_annotation_row)
        row.addWidget(remove)
        row.addStretch(1)
        layout.addLayout(row)

        self._annotations = QListWidget()
        self._annotations.setToolTip("格式：页码｜批注文字（双击可编辑所在行的文字）")
        layout.addWidget(self._annotations, 1)
        hint = QLabel("提示：批注按「页码｜文字」逐行写入；同一页多条会自动向下排开，"
                      "不会互相遮挡。导出 Word 时批注会写成 Word 原生批注，"
                      "其它格式汇总到文末附录。")
        hint.setObjectName("readerStatus")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        write = QPushButton("写入批注并另存")
        write.setObjectName("primaryButton")
        write.clicked.connect(self._do_annotate)
        layout.addWidget(write)
        layout.addStretch(1)
        return page

    def _build_form_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(8)
        layout.addWidget(self._section("表单域：读取 → 填值 → 另存"))
        row = QHBoxLayout()
        read = QPushButton("读取表单域")
        read.clicked.connect(self._load_form_fields)
        row.addWidget(read)
        self._flatten = QCheckBox("填写后拍平（不可再改）")
        row.addWidget(self._flatten)
        row.addStretch(1)
        layout.addLayout(row)

        self._field_table = QTableWidget(0, 4)
        self._field_table.setHorizontalHeaderLabels(["字段名", "类型", "当前值", "填写为"])
        header = self._field_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._field_table.setColumnWidth(0, 200)
        layout.addWidget(self._field_table, 1)
        fill = QPushButton("填写并另存")
        fill.setObjectName("primaryButton")
        fill.clicked.connect(self._do_fill_form)
        layout.addWidget(fill)

        layout.addWidget(self._section("PDF → PNG（扫描件 OCR 的前置步骤，需要 PyMuPDF）"))
        image_form = QFormLayout()
        image_form.setSpacing(6)
        self._image_pages = QLineEdit()
        self._image_pages.setPlaceholderText("留空 = 全部页面；如 1-3")
        image_form.addRow("页码", self._image_pages)
        self._image_dpi = QSpinBox()
        self._image_dpi.setRange(72, 600)
        self._image_dpi.setValue(150)
        image_form.addRow("分辨率 dpi", self._image_dpi)
        layout.addLayout(image_form)
        to_images = QPushButton("导出为图片")
        to_images.clicked.connect(self._do_to_images)
        layout.addWidget(to_images)
        layout.addStretch(1)
        return page

    # ---------------------------------------------------------------- 助手

    def _pick_source(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 PDF", self._source_field.text(),
                                             "PDF 文件 (*.pdf);;所有文件 (*.*)")
        if path:
            self._source_field.setText(path)
            self._refresh_source()

    def _pick_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录", self._output_dir.text())
        if folder:
            self._output_dir.setText(folder)

    def _open_output_dir(self) -> None:
        import subprocess

        folder = Path(self._output_dir.text().strip() or ".")
        if folder.is_dir():
            subprocess.Popen(["explorer", str(folder)])
        else:
            self._toaster.info(f"输出目录还不存在：{folder}")

    def source_path(self) -> str:
        return self._source_field.text().strip()

    def output_dir(self) -> Path:
        folder = Path(self._output_dir.text().strip() or library_output_dir(self._library))
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _refresh_source(self) -> None:
        source = self.source_path()
        if not source or not Path(source).is_file():
            self._info_label.setText("未选择文件")
            return
        try:
            info = pdf_tools.pdf_info(source)
        except pdf_tools.PdfError as error:
            self._info_label.setText("读取失败")
            self._append(f"⚠ 读取失败：{error}")
            return
        self._info_label.setText(info.summary())
        if not self._watermark_text.text():
            self._watermark_text.setText(self._default_watermark())

    def _default_watermark(self) -> str:
        watermark = self._library.watermark
        if watermark is not None and watermark.enabled:
            return watermark.text
        return ""

    def _target(self, suffix: str, name: str = "") -> Path:
        folder = self.output_dir()
        source = Path(self.source_path())
        stem = (name or "").strip() or f"{source.stem}-{suffix}"
        return unique_output_path(folder / f"{stem}.pdf")

    def _append(self, message: str) -> None:
        text = self._log.toPlainText().rstrip()
        self._log.set_lines([*(text.splitlines() if text else []), message])

    def _fail(self, action: str, error: Exception) -> None:
        self._append(f"✘ {action}失败：{error}")
        self._toaster.error(f"{action}失败：{error}")

    def _ok(self, message: str, path: Optional[Path] = None) -> None:
        detail = f"✔ {message}" + (f" → {path}" if path is not None else "")
        self._append(detail)
        self._toaster.success(message)
        self._library.audit("pdf", self.source_path(), f"{message}{f' → {path}' if path else ''}")

    def _require_source(self) -> bool:
        source = self.source_path()
        if not source:
            self._toaster.info("请先选择源 PDF")
            return False
        if not Path(source).is_file():
            self._toaster.error(f"文件不存在：{source}")
            return False
        return True

    # ---------------------------------------------------------------- 动作

    def _do_split(self) -> None:
        if not self._require_source():
            return
        try:
            produced = pdf_tools.split_pdf(
                self.source_path(), self.output_dir(),
                mode=self._split_mode.currentData() or "each",
                ranges=self._split_ranges.text().strip(),
                every=self._split_every.value())
        except pdf_tools.PdfError as error:
            self._fail("拆分", error)
            return
        self._ok(f"已拆分为 {len(produced)} 个 PDF",
                 Path(produced[0]).parent if produced else None)

    def _do_extract(self) -> None:
        if not self._require_source():
            return
        try:
            produced = pdf_tools.extract_pages(
                self.source_path(), self._target("抽取", self._extract_name.text()),
                pages=self._extract_pages.text().strip())
        except pdf_tools.PdfError as error:
            self._fail("抽取页面", error)
            return
        self._ok("已抽取页面", produced)

    def _add_merge_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "选择要合并的 PDF", "",
                                               "PDF 文件 (*.pdf);;所有文件 (*.*)")
        for path in paths:
            self._merge_list.addItem(path)

    def _add_current_to_merge(self) -> None:
        source = self.source_path()
        if source and Path(source).is_file():
            self._merge_list.addItem(source)
        else:
            self._toaster.info("请先选择源 PDF")

    def _remove_merge_selected(self) -> None:
        for item in self._merge_list.selectedItems():
            self._merge_list.takeItem(self._merge_list.row(item))

    def _move_merge(self, delta: int) -> None:
        row = self._merge_list.currentRow()
        target = row + delta
        if row < 0 or target < 0 or target >= self._merge_list.count():
            return
        item = self._merge_list.takeItem(row)
        self._merge_list.insertItem(target, item)
        self._merge_list.setCurrentRow(target)

    def _do_merge(self) -> None:
        paths = [self._merge_list.item(index).text() for index in range(self._merge_list.count())]
        if len(paths) < 2:
            self._toaster.info("合并至少需要两个 PDF（先「添加文件」或「加入当前源文件」）")
            return
        name = self._merge_name.text().strip() or f"{Path(paths[0]).stem}-合并"
        target = unique_output_path(self.output_dir() / f"{name}.pdf")
        try:
            produced = pdf_tools.merge_pdfs(paths, target,
                                           bookmarks=self._merge_bookmarks.isChecked())
        except pdf_tools.PdfError as error:
            self._fail("合并", error)
            return
        self._ok(f"已合并 {len(paths)} 份 PDF", produced)

    def _do_compress(self) -> None:
        if not self._require_source():
            return
        try:
            produced = pdf_tools.compress_pdf(self.source_path(), self._target("压缩"),
                                             level=self._compress_level.currentData() or "medium")
        except pdf_tools.PdfError as error:
            self._fail("压缩", error)
            return
        before = Path(self.source_path()).stat().st_size
        after = produced.stat().st_size
        ratio = (1 - after / before) * 100 if before else 0
        self._ok(f"压缩完成：{before / 1024:.0f} KB → {after / 1024:.0f} KB（省 {ratio:.0f}%）",
                 produced)

    def _do_encrypt(self) -> None:
        if not self._require_source():
            return
        password = self._user_password.text()
        if not password:
            self._toaster.info("请填写打开密码")
            return
        try:
            produced = pdf_tools.encrypt_pdf(
                self.source_path(), self._target("已加密"),
                user_password=password, owner_password=self._owner_password.text(),
                allow_printing=self._allow_printing.isChecked(),
                allow_copying=self._allow_copying.isChecked(),
                allow_modifying=self._allow_modifying.isChecked())
        except pdf_tools.PdfError as error:
            self._fail("加密", error)
            return
        self._ok("已加密（请牢记密码，忘记后无法恢复）", produced)

    def _do_decrypt(self) -> None:
        if not self._require_source():
            return
        try:
            produced = pdf_tools.decrypt_pdf(self.source_path(), self._target("已解密"),
                                            password=self._decrypt_password.text())
        except pdf_tools.PdfError as error:
            self._fail("解密", error)
            return
        self._ok("已解密并另存为不加密副本", produced)

    def _do_watermark(self) -> None:
        if not self._require_source():
            return
        text = self._watermark_text.text().strip()
        footer = self._watermark_footer.text().strip()
        tracking = self._tracking_id.text().strip()
        if not (text or footer or tracking):
            self._toaster.info("请填写水印文字、页脚或追踪标识")
            return
        try:
            produced = pdf_tools.watermark_pdf(
                self.source_path(), self._target("水印"), text=text, footer=footer,
                tracking_id=tracking)
        except pdf_tools.PdfError as error:
            self._fail("加水印", error)
            return
        self._ok("已为每一页加水印", produced)

    def _do_rotate(self) -> None:
        if not self._require_source():
            return
        try:
            produced = pdf_tools.rotate_pdf(
                self.source_path(), self._target("旋转"),
                angle=int(self._rotate_angle.currentData() or 90),
                pages=self._rotate_pages.text().strip())
        except pdf_tools.PdfError as error:
            self._fail("旋转", error)
            return
        self._ok(f"已旋转 {self._rotate_angle.currentData()}°", produced)

    # ---------------------------------------------------------------- 批注

    def _new_annotation_item(self) -> QListWidgetItem:
        """新建一行批注（默认第 1 页），并允许直接编辑。"""
        item = QListWidgetItem("1｜")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        return item

    def _remove_annotation_row(self) -> None:
        row = self._annotations.currentRow()
        if row >= 0:
            self._annotations.takeItem(row)

    def _load_annotations(self) -> None:
        if not self._require_source():
            return
        try:
            found = pdf_tools.list_annotations(self.source_path())
        except pdf_tools.PdfError as error:
            self._fail("读取批注", error)
            return
        self._annotations.clear()
        for item in found:
            entry = QListWidgetItem(f"{item['page']}｜{item['text']}")
            entry.setFlags(entry.flags() | Qt.ItemFlag.ItemIsEditable)
            entry.setToolTip(f"类型：{item.get('type', '')}｜作者：{item.get('author', '')}")
            self._annotations.addItem(entry)
        if not found:
            self._append("这份 PDF 暂时没有批注")
            self._toaster.info("这份 PDF 暂时没有批注")
        else:
            self._append(f"读取到 {len(found)} 条批注（可编辑后重新写入）")

    def _collect_annotations(self) -> list[dict]:
        notes: list[dict] = []
        for row in range(self._annotations.count()):
            text = self._annotations.item(row).text()
            page_text, _, body = text.partition("｜")
            try:
                page = int(page_text.strip() or "1")
            except ValueError:
                page = 1
            if body.strip():
                notes.append({"page": page, "text": body.strip()})
        return notes

    def _do_annotate(self) -> None:
        if not self._require_source():
            return
        notes = self._collect_annotations()
        if not notes:
            self._toaster.info("请先添加批注行（格式：页码｜文字）")
            return
        try:
            produced = pdf_tools.annotate_pdf(self.source_path(), self._target("批注"), notes)
        except pdf_tools.PdfError as error:
            self._fail("写入批注", error)
            return
        self._ok(f"已写入 {len(notes)} 条 PDF 批注", produced)

    # ---------------------------------------------------------------- 表单与图片

    def _load_form_fields(self) -> None:
        if not self._require_source():
            return
        try:
            self._fields = pdf_tools.list_form_fields(self.source_path())
        except pdf_tools.PdfError as error:
            self._fail("读取表单域", error)
            return
        self._field_table.setRowCount(len(self._fields))
        for row, field in enumerate(self._fields):
            self._field_table.setItem(row, 0, QTableWidgetItem(str(field.get("name") or "")))
            self._field_table.setItem(row, 1, QTableWidgetItem(str(field.get("type") or "")))
            self._field_table.setItem(row, 2, QTableWidgetItem(str(field.get("value") or "")))
            self._field_table.setItem(row, 3, QTableWidgetItem(""))
        if not self._fields:
            self._append("这份 PDF 没有可填写的表单域（AcroForm）")
            self._toaster.info("这份 PDF 没有可填写的表单域")
        else:
            self._append(f"读取到 {len(self._fields)} 个表单域："
                         + "、".join(str(item.get("name")) for item in self._fields[:8]))

    def _do_fill_form(self) -> None:
        if not self._require_source():
            return
        if not self._fields:
            self._load_form_fields()
        values: dict[str, str] = {}
        for row in range(self._field_table.rowCount()):
            name_item = self._field_table.item(row, 0)
            value_item = self._field_table.item(row, 3)
            if name_item is None or value_item is None:
                continue
            name = name_item.text().strip()
            value = value_item.text().strip()
            if name and value:
                values[name] = value
        if not values:
            self._toaster.info("请先在「填写为」列里填值")
            return
        try:
            produced = pdf_tools.fill_form(self.source_path(), self._target("已填表"), values,
                                          flatten=self._flatten.isChecked())
        except pdf_tools.PdfError as error:
            self._fail("填写表单", error)
            return
        self._ok(f"已填写 {len(values)} 个字段", produced)

    def _do_to_images(self) -> None:
        if not self._require_source():
            return
        try:
            produced = pdf_tools.to_images(self.source_path(), self.output_dir(),
                                          pages=self._image_pages.text().strip(),
                                          dpi=self._image_dpi.value())
        except pdf_tools.PdfError as error:
            self._fail("导出图片", error)
            return
        self._ok(f"已导出 {len(produced)} 张 PNG（可用于 OCR）", self.output_dir())


def library_output_dir(library: DocumentLibrary) -> str:
    """板块输出目录（对话框默认值）。"""
    return str(library.output_dir or ".")


__all__ = ["PdfToolsDialog", "library_output_dir"]
