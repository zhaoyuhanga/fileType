"""墨软图库板块：图库展示 / 相册标签 / 编辑美化 / AI 优化 + 大图查看。

界面分区（对应 PRD 的功能架构）：
- 顶栏：视图切换（网格/瀑布流/时间轴）、缩略图尺寸、排序、搜索；
- 左栏：相册、标签、来源、时间轴快捷筛选；
- 中间：缩略图网格（懒加载）+ 大图查看器；
- 右栏：选中图片的详情（尺寸/EXIF/标签），可折叠；
- 底栏：导入 / 收集 / 打标签 / 收藏 / 编辑 / AI 优化 / 去重检测的进度与操作。

所有重活（扫描、解码、算法、AI）都走后台线程，界面不阻塞。
"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.image import (
    DEFAULT_SORT,
    SORT_MODES,
    SOURCE_LABELS,
    VIEW_MODES,
    VIEW_MODE_LABELS,
    ImageItem,
    ImageLibrary,
    THUMB_LARGE,
    THUMB_MEDIUM,
    THUMB_SMALL,
    format_size,
    source_label,
)
from modu_workbench.services import app_context
from modu_workbench.ui_kit.toast import Toaster
from modu_workbench.ui_kit.widgets import make_nav_button, set_nav_active

from .gallery_widgets import (
    ITEM_ROLE,
    AlbumPicker,
    AnalyzeWorker,
    DuplicateWorker,
    EnhanceWorker,
    ImageViewer,
    ImportWorker,
    TagInputDialog,
    ThumbnailGrid,
    UrlCollectWorker,
    ask_text,
)

BROWSE_KEY = "browse"
VIEWER_KEY = "viewer"

# 缩略图档位：列数 → 像素
THUMB_STEPS = ((6, THUMB_SMALL), (4, THUMB_MEDIUM), (3, 260), (2, THUMB_LARGE))


class GalleryBoardPage(QWidget):
    """墨软图库板块。"""

    def __init__(self, parent: QWidget | None = None, *, library: ImageLibrary | None = None):
        super().__init__(parent)
        self._toaster = Toaster(self)
        self._library: ImageLibrary = library or app_context.image_library()

        self._items: list[ImageItem] = []          # 当前页
        self._all_items: list[ImageItem] = []      # 当前筛选条件下的全部结果
        self._page = 1
        self._page_size = self._preferred_page_size()
        self._browse_worker: ImportWorker | None = None
        self._url_worker: UrlCollectWorker | None = None
        self._enhance_worker: EnhanceWorker | None = None
        self._analyze_worker: AnalyzeWorker | None = None
        self._duplicate_worker: DuplicateWorker | None = None
        self._duplicates: list[list[ImageItem]] = []
        # 「最近 N 天导入」筛选（由左侧树设置；改动其它筛选时重置）
        self._recent_days = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_nav())
        layout.addWidget(self._build_toolbar())

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_browse())
        self._stack.addWidget(self._build_viewer())
        layout.addWidget(self._stack, 1)

        self._bottom = self._build_bottom()
        layout.addWidget(self._bottom)

        self._install_shortcuts()
        self.reload()

    # ------------------------------------------------------------------ 顶栏

    def _build_nav(self) -> QWidget:
        nav = QWidget()
        row = QHBoxLayout(nav)
        row.setContentsMargins(16, 8, 16, 4)
        row.setSpacing(8)

        self._nav_buttons: dict[str, QPushButton] = {}
        for key, label in ((BROWSE_KEY, "🖼 图库"), (VIEWER_KEY, "🔍 大图查看")):
            button = make_nav_button(label)
            button.clicked.connect(lambda _=False, k=key: self.show_page(k))
            self._nav_buttons[key] = button
            row.addWidget(button)

        row.addStretch(1)
        self._count_label = QLabel("")
        self._count_label.setObjectName("readerStatus")
        row.addWidget(self._count_label)
        return nav

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("bottomBar")
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(16, 6, 16, 6)
        outer.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(8)

        self._search = QLineEdit()
        self._search.setObjectName("searchBox")
        self._search.setPlaceholderText("搜索文件名 / 相机 / 标签 / AI 描述（回车）")
        self._search.returnPressed.connect(self._on_filter_changed)
        row.addWidget(self._search, 1)

        search_button = QPushButton("搜索")
        search_button.clicked.connect(self._on_filter_changed)
        row.addWidget(search_button)

        self._ai_search = QPushButton("AI 检索")
        self._ai_search.setToolTip("用自然语言描述（如“去年海边的照片”），由 DeepSeek 拆成关键词再本地筛选")
        self._ai_search.clicked.connect(self._ai_search_clicked)
        row.addWidget(self._ai_search)

        self._view_combo = QComboBox()
        for key in VIEW_MODES:
            self._view_combo.addItem(VIEW_MODE_LABELS[key], key)
        self._view_combo.currentIndexChanged.connect(self._on_view_changed)
        row.addWidget(self._view_combo)

        self._size_combo = QComboBox()
        for columns, size in THUMB_STEPS:
            self._size_combo.addItem(f"{size}px", (columns, size))
        self._size_combo.setCurrentIndex(1)
        self._size_combo.currentIndexChanged.connect(self._on_size_changed)
        row.addWidget(self._size_combo)

        self._sort_combo = QComboBox()
        for key, label in SORT_MODES.items():
            self._sort_combo.addItem(label, key)
        self._sort_combo.currentIndexChanged.connect(self.reload)
        row.addWidget(self._sort_combo)

        outer.addLayout(row)

        # 第二行：筛选
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self._kind_combo = QComboBox()
        self._kind_combo.addItem("全部相册", 0)
        self._kind_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self._kind_combo)

        self._tag_combo = QComboBox()
        self._tag_combo.addItem("全部标签", "")
        self._tag_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self._tag_combo)

        self._source_combo = QComboBox()
        self._source_combo.addItem("全部来源", "")
        for key, label in SOURCE_LABELS.items():
            self._source_combo.addItem(label, key)
        self._source_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self._source_combo)

        self._favorite_only = QCheckBox("仅收藏")
        self._favorite_only.toggled.connect(self._on_filter_changed)
        filter_row.addWidget(self._favorite_only)

        self._timeline_combo = QComboBox()
        self._timeline_combo.addItem("全部时间", "")
        self._timeline_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self._timeline_combo)

        filter_row.addStretch(1)
        self._dup_button = QPushButton("重复检测")
        self._dup_button.setToolTip("用感知哈希找出重复/相似图片")
        self._dup_button.clicked.connect(self._detect_duplicates)
        filter_row.addWidget(self._dup_button)

        outer.addLayout(filter_row)

        # ---- 分页条 ----
        # 300+ 张图一次性塞进网格会又慢又乱，改成按页渲染：每页只建这么多控件，
        # 缩略图当页就能全部补齐，滚动不再出现"半灰半图"的杂乱感。
        page_row = QHBoxLayout()
        page_row.setSpacing(8)
        self._page_label = QLabel("")
        self._page_label.setObjectName("readerStatus")
        page_row.addWidget(self._page_label)

        self._page_size_combo = QComboBox()
        for size in (60, 120, 240, 480, 0):
            self._page_size_combo.addItem("全部（不分页）" if size == 0 else f"{size} 张/页", size)
        self._page_size_combo.setToolTip("每页显示多少张：每页越少越流畅")
        self._page_size_combo.currentIndexChanged.connect(self._on_page_size_changed)
        page_row.addWidget(self._page_size_combo)

        self._prev_button = QPushButton("◀ 上一页")
        self._prev_button.clicked.connect(lambda: self._turn_page(-1))
        page_row.addWidget(self._prev_button)

        self._next_button = QPushButton("下一页 ▶")
        self._next_button.clicked.connect(lambda: self._turn_page(1))
        page_row.addWidget(self._next_button)

        page_row.addWidget(QLabel("跳至"))
        self._page_spin = QSpinBox()
        self._page_spin.setRange(1, 1)
        self._page_spin.setFixedWidth(78)
        self._page_spin.setToolTip("输入页码后回车跳转")
        self._page_spin.editingFinished.connect(self._on_page_jump)
        page_row.addWidget(self._page_spin)

        page_row.addStretch(1)
        outer.addLayout(page_row)
        return bar

    # ------------------------------------------------------------------ 浏览页

    def _build_browse(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(10)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：按「图库 / 相册 / 时间 / 标签 / 来源」分组的可折叠导航树。
        # 用户希望"左侧显示有哪些图片、并能按时间或搜索条件分类查看"，
        # 因此这里做成树形：点任意节点即把右侧网格筛成对应集合。
        self._side = QTreeWidget()
        self._side.setObjectName("gallerySide")
        self._side.setHeaderHidden(True)
        self._side.setMinimumWidth(190)
        self._side.itemClicked.connect(self._on_side_clicked)
        self._splitter.addWidget(self._side)

        self._grid = ThumbnailGrid(self._library)
        self._grid.setMinimumWidth(260)
        self._grid.itemActivated.connect(self._open_viewer)
        self._grid.selectionChangedItems.connect(self._on_selection_changed)
        self._grid.customContextMenuRequested.connect(self._on_grid_menu)
        self._splitter.addWidget(self._grid)

        self._details = QTextBrowser()
        self._details.setOpenExternalLinks(True)
        self._details.setMinimumWidth(200)
        self._splitter.addWidget(self._details)
        self._splitter.setSizes([210, 700, 260])
        self._splitter.setStretchFactor(1, 1)          # 只有网格随窗口拉伸
        self._splitter.setCollapsible(1, False)        # 网格不允许被拖没
        layout.addWidget(self._splitter)
        return page

    def _schedule_side_refresh(self) -> None:
        """把左侧树的重建延后到当前点击处理结束之后。

        必要性：`_on_side_clicked` 里直接重建树会把**正在处理点击事件的那个节点**
        销毁，Qt/PySide 随后访问它就抛
        "Internal C++ object (QTreeWidgetItem) already deleted"。
        """
        if getattr(self, "_side_refresh_pending", False):
            return
        self._side_refresh_pending = True

        def run() -> None:
            self._side_refresh_pending = False
            self._refresh_side()

        QTimer.singleShot(0, run)

    # ------------------------------------------------------------------ 大图页

    def _build_viewer(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        body = QHBoxLayout()
        body.setSpacing(8)

        self._viewer = ImageViewer()
        # 缩放比例标签必须随缩放变化更新（此前只在换图时刷新，看起来"不变"）
        self._viewer.zoomChanged.connect(self._update_zoom_label)
        body.addWidget(self._viewer, 1)

        # 右侧详情：竖图左右本来就有一大片空白，用它放信息比留白好看，
        # 也省得为了看参数再切回网格页。
        details_box = QVBoxLayout()
        details_box.setSpacing(4)
        self._viewer_info = QLabel("未选择图片")
        self._viewer_info.setObjectName("readerStatus")
        self._viewer_info.setWordWrap(True)
        details_box.addWidget(self._viewer_info)
        self._viewer_details = QTextBrowser()
        self._viewer_details.setOpenExternalLinks(True)
        details_box.addWidget(self._viewer_details, 1)
        holder = QWidget()
        holder.setLayout(details_box)
        holder.setFixedWidth(272)
        body.addWidget(holder)
        layout.addLayout(body, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        for label, handler, tip in (
            ("◀ 上一张", lambda: self._step_viewer(-1), "上一张（←）"),
            ("下一张 ▶", lambda: self._step_viewer(1), "下一张（→）"),
            ("−", self._viewer.zoom_out, "缩小（-）"),
            ("100%", self._viewer.actual_size, "原始大小（1）"),
            ("+", self._viewer.zoom_in, "放大（+）"),
            ("适应窗口", self._viewer.reset_zoom, "适应窗口（0 / 双击）"),
        ):
            button = QPushButton(label)
            button.setToolTip(tip)
            button.clicked.connect(handler)
            row.addWidget(button)

        self._slide_toggle = QPushButton("幻灯片")
        self._slide_toggle.setCheckable(True)
        self._slide_toggle.setToolTip("按固定间隔自动播放（空格开始/停止）")
        self._slide_toggle.toggled.connect(self._toggle_slideshow)
        row.addWidget(self._slide_toggle)

        self._slide_interval = QComboBox()
        for seconds in (3, 5, 10, 20):
            self._slide_interval.addItem(f"{seconds}s", seconds)
        self._slide_interval.setCurrentIndex(1)
        row.addWidget(self._slide_interval)

        row.addStretch(1)
        self._viewer_zoom = QLabel("")
        self._viewer_zoom.setObjectName("readerStatus")
        row.addWidget(self._viewer_zoom)
        layout.addLayout(row)
        return page

    # ------------------------------------------------------------------ 底栏

    def _build_bottom(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("bottomBar")
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(16, 6, 16, 8)
        outer.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(8)

        import_button = QPushButton("导入图片…")
        import_button.setObjectName("primaryButton")
        import_button.setToolTip("选择一个或多个图片文件")
        import_button.clicked.connect(self._import_files)
        row.addWidget(import_button)

        folder_button = QPushButton("扫描文件夹…")
        folder_button.setToolTip("递归扫描文件夹（增量导入，自动去重）")
        folder_button.clicked.connect(self._import_folder)
        row.addWidget(folder_button)

        screenshot_button = QPushButton("扫描截图")
        screenshot_button.setToolTip("扫描系统截图目录")
        screenshot_button.clicked.connect(self._import_screenshots)
        row.addWidget(screenshot_button)

        url_button = QPushButton("网址收集…")
        url_button.setToolTip("粘贴图片直链，下载入库（标记来源）")
        url_button.clicked.connect(self._collect_url)
        row.addWidget(url_button)

        paste_button = QPushButton("剪贴板收集")
        paste_button.setToolTip("把剪贴板里的图片保存入库")
        paste_button.clicked.connect(self._collect_clipboard)
        row.addWidget(paste_button)

        row.addStretch(1)
        outer.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self._tag_button = QPushButton("打标签…")
        self._tag_button.clicked.connect(self._tag_selected)
        row2.addWidget(self._tag_button)

        self._favorite_button = QPushButton("☆ 收藏")
        self._favorite_button.clicked.connect(self._toggle_favorite)
        row2.addWidget(self._favorite_button)

        self._album_button = QPushButton("加入相册…")
        self._album_button.clicked.connect(self._add_to_album)
        row2.addWidget(self._album_button)

        self._edit_button = QPushButton("编辑美化…")
        self._edit_button.setObjectName("primaryButton")
        self._edit_button.clicked.connect(self._open_editor)
        row2.addWidget(self._edit_button)

        self._enhance_button = QPushButton("AI 优化…")
        self._enhance_button.clicked.connect(self._open_enhance)
        row2.addWidget(self._enhance_button)

        self._analyze_button = QPushButton("AI 描述/标签")
        self._analyze_button.setToolTip("用 DeepSeek 生成描述与标签（纯文本，不上传图片除非已授权）")
        self._analyze_button.clicked.connect(self._analyze_selected)
        row2.addWidget(self._analyze_button)

        self._delete_button = QPushButton("删除条目")
        self._delete_button.setObjectName("dangerButton")
        self._delete_button.clicked.connect(self._delete_selected)
        row2.addWidget(self._delete_button)

        row2.addStretch(1)
        self._cancel_button = QPushButton("取消任务")
        self._cancel_button.setObjectName("dangerButton")
        self._cancel_button.setEnabled(False)
        self._cancel_button.clicked.connect(self._cancel_tasks)
        row2.addWidget(self._cancel_button)
        outer.addLayout(row2)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        outer.addWidget(self._progress)

        self._status = QLabel("就绪。导入图片后会生成缩略图并读取 EXIF。")
        self._status.setObjectName("readerStatus")
        self._status.setWordWrap(True)
        outer.addWidget(self._status)
        return bar

    def _install_shortcuts(self) -> None:
        for keys, handler in (
            (("Left",), lambda: self._step_viewer(-1)),
            (("Right",), lambda: self._step_viewer(1)),
            (("+", "="), self._viewer.zoom_in),
            (("-",), self._viewer.zoom_out),
            (("0",), self._viewer.reset_zoom),
            (("1",), self._viewer.actual_size),
            (("Space",), lambda: self._slide_toggle.toggle()),
            (("F5",), self.reload),
            (("PgDown", "Ctrl+Right"), lambda: self._turn_page(1)),
            (("PgUp", "Ctrl+Left"), lambda: self._turn_page(-1)),
        ):
            for key in keys:
                shortcut = QShortcut(QKeySequence(key), self)
                shortcut.activated.connect(handler)

    # ------------------------------------------------------------------ 导航

    def show_page(self, key: str) -> None:
        index = 0 if key == BROWSE_KEY else 1
        self._stack.setCurrentIndex(index)
        for nav_key, button in self._nav_buttons.items():
            set_nav_active(button, nav_key == key)
        # 大图查看时收起"导入/整理"底栏：这些按钮只对网格里的选中项有意义，
        # 留着会白占近百像素的高度（用户反馈"大图页面空白太多"）。
        bottom = getattr(self, "_bottom", None)
        if bottom is not None:
            bottom.setVisible(key == BROWSE_KEY)

    def on_shown(self) -> None:
        self.reload()

    def _on_filter_changed(self) -> None:
        """用户手动改筛选条件时，清掉左侧树的「最近 N 天」范围，避免叠加成空结果。"""
        self._recent_days = 0
        self._page = 1                      # 筛选条件变了就回到第一页
        self.reload(keep_page=False)

    # ------------------------------------------------------------------ 数据

    def reload(self, *, keep_page: bool = True) -> None:
        """按当前筛选条件重新加载图库（分页：只把当前页交给网格）。"""
        self._refresh_filters()
        keyword = self._search.text().strip()
        album_id = self._kind_combo.currentData() or 0
        tag = self._tag_combo.currentData() or ""
        source = self._source_combo.currentData() or ""
        order = self._sort_combo.currentData() or DEFAULT_SORT
        date_from = self._timeline_combo.currentData() or 0

        items = self._library.list_images(
            keyword=keyword, album_id=int(album_id) or None, tag=tag, source=source,
            favorite_only=self._favorite_only.isChecked(), order=order,
            date_from=int(date_from),
        )
        # 「最近导入」节点：在结果集上再按时间收窄（放在最后，避免与其它筛选冲突）
        recent_days = getattr(self, "_recent_days", 0)
        if recent_days:
            cutoff = int(time.time()) - recent_days * 86400
            items = [item for item in items if (item.added_at or 0) >= cutoff]
        self._all_items = items

        # ---- 分页 ----
        previous_page = self._page
        pages = self.page_count
        if not keep_page:
            self._page = 1
        self._page = max(1, min(pages, self._page))
        page_changed = self._page != previous_page
        self._items = self.page_items(self._page)
        self._grid.set_items(self._items, keep_selection=keep_page and not page_changed)
        if page_changed:
            self._grid.scrollToTop()

        total = self._library.storage.count_images()
        shown = len(self._all_items)
        suffix = f"（当前筛选 {shown} 张）" if shown != total else ""
        scope = self._scope_text()
        self._count_label.setText(
            f"{scope}共 {total} 张{suffix} · 磁盘 {format_size(self._library.disk_usage())}"
        )
        self._refresh_page_bar()
        self._update_buttons()

    # ------------------------------------------------------------------ 分页

    @staticmethod
    def _preferred_page_size() -> int:
        from PySide6.QtCore import QSettings

        value = QSettings("ModuWorkbench", "modu-workbench").value("gallery/page_size", 120)
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 120

    @property
    def page_size(self) -> int:
        """每页张数；0 表示不分页。"""
        return int(self._page_size)

    @property
    def page_count(self) -> int:
        if not self._page_size:
            return 1
        return max(1, (len(self._all_items) + self._page_size - 1) // self._page_size)

    def page_items(self, page: int) -> list[ImageItem]:
        if not self._page_size:
            return list(self._all_items)
        start = max(0, (page - 1) * self._page_size)
        return self._all_items[start:start + self._page_size]

    def _refresh_page_bar(self) -> None:
        pages = self.page_count
        size_index = self._page_size_combo.findData(self._page_size)
        if size_index >= 0 and size_index != self._page_size_combo.currentIndex():
            self._page_size_combo.blockSignals(True)
            self._page_size_combo.setCurrentIndex(size_index)
            self._page_size_combo.blockSignals(False)
        if self._page_size:
            first = (self._page - 1) * self._page_size + 1 if self._all_items else 0
            last = min(len(self._all_items), self._page * self._page_size)
            self._page_label.setText(
                f"第 {self._page} / {pages} 页 · 本页 {last - first + 1 if self._all_items else 0} 张"
                f"（{first}-{last} / 共 {len(self._all_items)} 张）"
            )
        else:
            self._page_label.setText(f"不分页 · 共 {len(self._all_items)} 张")
        self._page_spin.blockSignals(True)
        self._page_spin.setRange(1, pages)
        self._page_spin.setValue(self._page)
        self._page_spin.blockSignals(False)
        self._prev_button.setEnabled(self._page > 1)
        self._next_button.setEnabled(self._page < pages)

    def _turn_page(self, delta: int) -> None:
        target = self._page + int(delta)
        if target < 1 or target > self.page_count or target == self._page:
            return
        self._page = target
        self.reload()

    def _on_page_jump(self) -> None:
        target = int(self._page_spin.value())
        if target != self._page:
            self._page = target
            self.reload()

    def _on_page_size_changed(self) -> None:
        size = self._page_size_combo.currentData()
        self._page_size = int(size if size is not None else 120)
        self._page = 1
        self.reload(keep_page=False)

    def goto_item_page(self, image_id: int) -> None:
        """把包含该图片的那一页翻出来（上一张/下一张跨页时用）。"""
        if not self._page_size:
            return
        for index, item in enumerate(self._all_items):
            if item.id == image_id:
                page = index // self._page_size + 1
                if page != self._page:
                    self._page = page
                    self.reload()
                return

    def _scope_text(self) -> str:
        """当前筛选范围的可读描述（让用户一眼知道自己在看哪个集合）。"""
        parts: list[str] = []
        if getattr(self, "_recent_days", 0):
            parts.append(f"最近 {self._recent_days} 天导入")
        if self._favorite_only.isChecked():
            parts.append("仅收藏")
        album_id = self._kind_combo.currentData()
        if album_id:
            album = self._library.storage.get_album(int(album_id))
            if album is not None:
                parts.append(f"相册「{album.name}」")
        tag = self._tag_combo.currentData()
        if tag:
            parts.append(f"标签「{tag}」")
        source = self._source_combo.currentData()
        if source:
            parts.append(source_label(source))
        month = self._timeline_combo.currentText()
        if self._timeline_combo.currentData():
            parts.append(month.split("（")[0])
        keyword = self._search.text().strip()
        if keyword:
            parts.append(f"搜索「{keyword}」")
        return (" · ".join(parts) + " · ") if parts else ""

    def _refresh_filters(self) -> None:
        current_album = self._kind_combo.currentData()
        self._kind_combo.blockSignals(True)
        self._kind_combo.clear()
        self._kind_combo.addItem("全部相册", 0)
        for album in self._library.list_albums():
            prefix = "★ " if album.kind == "favorite" else ""
            self._kind_combo.addItem(f"{prefix}{album.name}（{album.image_count}）", album.id)
        index = self._kind_combo.findData(current_album)
        self._kind_combo.setCurrentIndex(index if index >= 0 else 0)
        self._kind_combo.blockSignals(False)

        current_tag = self._tag_combo.currentData()
        self._tag_combo.blockSignals(True)
        self._tag_combo.clear()
        self._tag_combo.addItem("全部标签", "")
        for tag in self._library.list_tags():
            self._tag_combo.addItem(f"{tag.name}（{tag.use_count}）", tag.name)
        index = self._tag_combo.findData(current_tag)
        self._tag_combo.setCurrentIndex(index if index >= 0 else 0)
        self._tag_combo.blockSignals(False)

        current_time = self._timeline_combo.currentData()
        self._timeline_combo.blockSignals(True)
        self._timeline_combo.clear()
        self._timeline_combo.addItem("全部时间", "")
        for bucket, count in self._library.storage.timeline()[:36]:
            try:
                stamp = int(time.mktime(time.strptime(bucket + "-01", "%Y-%m-%d")))
            except ValueError:
                continue
            self._timeline_combo.addItem(f"{bucket}（{count}）", stamp)
        index = self._timeline_combo.findData(current_time)
        self._timeline_combo.setCurrentIndex(index if index >= 0 else 0)
        self._timeline_combo.blockSignals(False)

        # 延后重建：直接重建会在左侧树节点点击事件处理中销毁该节点（shiboken 崩溃）
        self._schedule_side_refresh()

    def _refresh_side(self) -> None:
        """重建左侧导航树：图库概览 / 相册 / 时间 / 标签 / 来源。

        每个节点携带筛选条件（payload），点击即筛选右侧网格。
        """
        self._side.clear()

        def add(parent, text: str, payload: dict | None, *, bold: bool = False):  # noqa: ANN001
            entry = QTreeWidgetItem([text])
            if payload is not None:
                entry.setData(0, ITEM_ROLE, payload)
            else:
                entry.setFlags(Qt.ItemFlag.ItemIsEnabled)      # 纯分组标题不可点
            if bold:
                font = entry.font(0)
                font.setBold(True)
                entry.setFont(0, font)
            if parent is None:
                self._side.addTopLevelItem(entry)
            else:
                parent.addChild(entry)
            return entry

        total = self._library.storage.count_images()
        root = add(None, f"🖼 全部图片（{total}）", {"all": True}, bold=True)
        add(root, f"★ 我的收藏（{self._favorite_count()}）", {"favorite": True})
        add(root, "🆕 最近导入（7 天）", {"recent_days": 7})

        albums_node = add(None, "📁 相册", None, bold=True)
        albums = [a for a in self._library.list_albums() if not a.is_favorite]
        if albums:
            for album in albums:
                add(albums_node, f"{album.name}（{album.image_count}）", {"album": album.id})
        else:
            add(albums_node, "（还没有相册，右键图片可加入）", None)

        time_node = add(None, "🗓 时间", None, bold=True)
        for bucket, count in self._library.storage.timeline()[:48]:
            add(time_node, f"{bucket}（{count}）", {"month": bucket})

        tags_node = add(None, "🏷 标签", None, bold=True)
        tags = self._library.list_tags()
        if tags:
            for tag in tags[:40]:
                add(tags_node, f"{tag.name}（{tag.use_count}）", {"tag": tag.name})
        else:
            add(tags_node, "（还没有标签）", None)

        source_node = add(None, "📥 来源", None, bold=True)
        counts = self._source_counts()
        for key, label in SOURCE_LABELS.items():
            if counts.get(key):
                add(source_node, f"{label}（{counts[key]}）", {"source": key})

        self._side.expandAll()

    def _favorite_count(self) -> int:
        return len(self._library.storage.list_images(favorite_only=True, limit=100000))

    def _source_counts(self) -> dict:
        counts: dict = {}
        for item in self._library.storage.list_images(limit=100000):
            counts[item.source or "unknown"] = counts.get(item.source or "unknown", 0) + 1
        return counts

    def _on_side_clicked(self, entry: QTreeWidgetItem, _column: int = 0) -> None:
        payload = entry.data(0, ITEM_ROLE)
        if not payload:
            return
        self.show_page(BROWSE_KEY)
        # 清空其它筛选，避免叠加后结果为空让用户困惑
        self._search.blockSignals(True)
        self._search.clear()
        self._search.blockSignals(False)
        self._kind_combo.blockSignals(True)
        self._kind_combo.setCurrentIndex(0)
        self._kind_combo.blockSignals(False)
        self._tag_combo.blockSignals(True)
        self._tag_combo.setCurrentIndex(0)
        self._tag_combo.blockSignals(False)
        self._source_combo.blockSignals(True)
        self._source_combo.setCurrentIndex(0)
        self._source_combo.blockSignals(False)
        self._favorite_only.blockSignals(True)
        self._favorite_only.setChecked(False)
        self._favorite_only.blockSignals(False)
        self._timeline_combo.blockSignals(True)
        self._timeline_combo.setCurrentIndex(0)
        self._timeline_combo.blockSignals(False)

        # 清掉上一次的「最近 N 天」范围，避免与本次点击的节点叠加成空结果
        self._recent_days = 0

        if payload.get("all"):
            self._sort_combo.blockSignals(True)
            self._sort_combo.setCurrentIndex(list(SORT_MODES).index(DEFAULT_SORT))
            self._sort_combo.blockSignals(False)
        elif payload.get("favorite"):
            self._favorite_only.setChecked(True)
        elif payload.get("album"):
            index = self._kind_combo.findData(payload["album"])
            if index >= 0:
                self._kind_combo.setCurrentIndex(index)
        elif payload.get("tag"):
            index = self._tag_combo.findData(payload["tag"])
            if index >= 0:
                self._tag_combo.setCurrentIndex(index)
        elif payload.get("source"):
            index = self._source_combo.findData(payload["source"])
            if index >= 0:
                self._source_combo.setCurrentIndex(index)
        elif payload.get("month"):
            # 时间轴节点：切到「按拍摄时间」排序并用月份筛选
            self._sort_combo.blockSignals(True)
            self._sort_combo.setCurrentIndex(list(SORT_MODES).index("taken"))
            self._sort_combo.blockSignals(False)
            index = self._timeline_combo.findData(self._month_start(payload["month"]))
            if index >= 0:
                self._timeline_combo.setCurrentIndex(index)
        elif payload.get("recent_days"):
            self._timeline_combo.blockSignals(True)
            self._timeline_combo.setCurrentIndex(0)
            self._timeline_combo.blockSignals(False)
            self._recent_days = int(payload["recent_days"])
            self._sort_combo.blockSignals(True)
            self._sort_combo.setCurrentIndex(list(SORT_MODES).index("added"))
            self._sort_combo.blockSignals(False)

        # 换了分类节点就从第一页看起
        self._page = 1
        self.reload(keep_page=False)
        # 树的重建延后执行：本次点击的节点此刻还在事件处理中，不能立即销毁
        self._schedule_side_refresh()

    @staticmethod
    def _month_start(bucket: str) -> int:
        try:
            return int(time.mktime(time.strptime(bucket + "-01", "%Y-%m-%d")))
        except ValueError:
            return 0

    # ------------------------------------------------------------------ 视图

    def _on_view_changed(self) -> None:
        mode = self._view_combo.currentData()
        # 瀑布流/时间轴当前用「不同列数与文案」体现；网格为默认
        columns, size = self._size_combo.currentData() or (4, THUMB_MEDIUM)
        if mode == "waterfall":
            self._grid.set_thumb_size(size, columns=columns)
            self._status.setText("瀑布流视图：按图片比例自适应（缩略图保持原比例）")
        elif mode == "timeline":
            self._sort_combo.setCurrentIndex(list(SORT_MODES).index("taken"))
            self._status.setText("时间轴视图：按拍摄时间倒序，可用上方「时间」筛选到具体月份")
        else:
            self._grid.set_thumb_size(size, columns=columns)
            self._status.setText("网格视图")

    def _on_size_changed(self) -> None:
        payload = self._size_combo.currentData()
        if payload:
            columns, size = payload
            self._grid.set_thumb_size(size, columns=columns)

    # ------------------------------------------------------------------ 详情

    def _on_selection_changed(self, items: list) -> None:
        if not items:
            self._details.setHtml("<p style='color:#66728a'>未选择图片</p>")
            self._update_buttons()
            return
        if len(items) > 1:
            self._details.setHtml(f"<h3>已选择 {len(items)} 张图片</h3>"
                                  "<p style='color:#66728a'>可批量打标签、收藏、加入相册、"
                                  "编辑或在「AI 优化」里批量处理。</p>")
            self._update_buttons()
            return
        self._details.setHtml(self._details_html(items[0]))
        self._update_buttons()

    def _details_html(self, item: ImageItem) -> str:
        rows: list[tuple[str, str]] = [
            ("文件名", item.display()),
            ("尺寸", item.resolution),
            ("大小", item.size_text),
            ("格式", (item.ext or "?").upper()),
            ("拍摄时间", item.taken_text),
            ("导入时间", item.added_text),
            ("来源", item.source_text),
        ]
        if item.camera:
            rows.append(("相机", item.camera))
        if item.favorited:
            rows.append(("收藏", "★"))
        if item.rating:
            rows.append(("评分", "★" * item.rating))

        import json

        if item.exif_json:
            try:
                display = (json.loads(item.exif_json) or {}).get("display") or {}
                for key, value in display.items():
                    rows.append((key, str(value)))
            except ValueError:
                pass

        tags = item.tag_list
        if tags:
            rows.append(("标签", "、".join(tags)))
        if item.ai_caption:
            rows.append(("AI 描述", item.ai_caption))

        html = ["<h3 style='margin:2px 0 8px 0'>图片详情</h3>", "<table cellspacing='0' cellpadding='3'>"]
        for key, value in rows:
            html.append(
                f"<tr><td style='color:#66728a;white-space:nowrap'>{key}</td>"
                f"<td style='color:#141b2e'>{value}</td></tr>"
            )
        html.append("</table>")
        if item.path:
            html.append(f"<p style='color:#97a2b8;font-size:11px;word-break:break-all'>{item.path}</p>")
        return "".join(html)

    # ------------------------------------------------------------------ 操作

    def _import_files(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in
                            (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif"))
        files, _ = QFileDialog.getOpenFileNames(self, "选择图片", "", f"图片文件 ({patterns})")
        if files:
            self._start_import(files, source="local")

    def _import_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择要扫描的文件夹",
                                                 str(Path.home() / "Pictures"))
        if folder:
            self._start_import([folder], source="folder")

    def _import_screenshots(self) -> None:
        from modu_workbench.core.image import scan_screenshot_dirs

        folders = scan_screenshot_dirs()
        if not folders:
            self._toaster.info("未找到常见截图目录（通常在 图片/Screenshots）")
            return
        self._start_import(folders, source="screenshot")

    def _start_import(self, paths: list[str], *, source: str) -> None:
        if self._browse_worker is not None and self._browse_worker.isRunning():
            self._toaster.info("已有导入任务在进行")
            return
        album_id = self._kind_combo.currentData() or None
        if self._kind_combo.currentIndex() == 0:
            album_id = None
        self._status.setText("开始导入…")
        self._cancel_button.setEnabled(True)
        self._browse_worker = ImportWorker(
            self._library, paths, source=source,
            album_id=int(album_id) if album_id else None, parent=self,
        )
        self._browse_worker.progressed.connect(self._on_progress)
        self._browse_worker.finishedImport.connect(self._on_import_done)
        self._browse_worker.failed.connect(self._on_task_failed)
        self._browse_worker.finished.connect(self._on_worker_finished)
        self._browse_worker.start()

    def _on_import_done(self, result) -> None:  # noqa: ANN001
        self._progress.setValue(100)
        self._status.setText(f"导入完成：{result.summary()}")
        self._toaster.success(f"导入完成：{result.summary()}")
        self.reload()

    def _collect_url(self) -> None:
        url = ask_text(self, "网址收集", "图片直链（http/https）：")
        if not url:
            return
        self._url_worker = UrlCollectWorker(self._library, url, parent=self)
        self._url_worker.collected.connect(self._on_url_collected)
        self._url_worker.failed.connect(self._on_task_failed)
        self._url_worker.start()
        self._status.setText("正在下载图片…")

    def _on_url_collected(self, item) -> None:  # noqa: ANN001
        self._toaster.success(f"已收集：{item.display()}")
        self._status.setText(f"已收集：{item.display()}")
        self.reload()

    def _collect_clipboard(self) -> None:
        try:
            item = self._library.collect_from_clipboard()
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"剪贴板收集失败：{error}")
            return
        if item is None:
            self._toaster.info("剪贴板里没有图片（可先截图再试）")
            return
        self._toaster.success(f"已从剪贴板收集：{item.display()}")
        self.reload()

    def _tag_selected(self) -> None:
        items = self._grid.action_items()
        if not items:
            self._toaster.info("请先选择图片")
            return
        dialog = TagInputDialog(self._library, len(items), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        names = dialog.tag_names()
        if not names:
            return
        ids = [item.id for item in items]
        added = 0
        for name in names:
            added += self._library.tag_images(ids, name)
        self._toaster.success(f"已添加 {len(names)} 个标签（新增关联 {added}）")
        self.reload()

    def _toggle_favorite(self) -> None:
        items = self._grid.action_items()
        if not items:
            self._toaster.info("请先选择图片")
            return
        target = not items[0].favorited
        for item in items:
            if item.favorited != target:
                self._library.toggle_favorite(item.id)
        self._toaster.success("已收藏" if target else "已取消收藏")
        self.reload()

    def _add_to_album(self) -> None:
        items = self._grid.action_items()
        if not items:
            self._toaster.info("请先选择图片")
            return
        picker = AlbumPicker(self._library, self)
        if picker.exec() != QDialog.DialogCode.Accepted:
            return
        album_id = picker.selected_album_id()
        if album_id is None:
            return
        added = self._library.add_to_album(album_id, [item.id for item in items])
        album = self._library.storage.get_album(album_id)
        self._toaster.success(f"已加入「{album.name if album else '相册'}」：{added} 张")
        self.reload()

    def _delete_selected(self) -> None:
        items = self._grid.action_items()
        if not items:
            self._toaster.info("请先选择图片")
            return
        from PySide6.QtWidgets import QMessageBox

        answer = QMessageBox.question(
            self, "删除图片",
            f"将移除 {len(items)} 张图片的记录。\n是否同时删除本地文件？\n"
            "（选择 No＝只移除记录，保留文件）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return
        removed = self._library.delete_images(
            [item.id for item in items],
            remove_file=answer == QMessageBox.StandardButton.Yes,
        )
        self._toaster.success(f"已删除 {removed} 张")
        self.reload()

    # ------------------------------------------------------------------ 编辑/AI

    @staticmethod
    def _app_settings():  # noqa: ANN205
        """应用级 QSettings（读图库浏览偏好的默认值）。"""
        from PySide6.QtCore import QSettings

        return QSettings("ModuWorkbench", "modu-workbench")

    def _open_editor(self) -> None:
        item = self._grid.current_item() or (self._grid.action_items() or [None])[0]
        if item is None:
            self._toaster.info("请先选择一张图片")
            return
        from .gallery_editor import ImageEditorDialog

        dialog = ImageEditorDialog(self._library, item, self)
        dialog.saved.connect(self._on_edit_saved)
        dialog.exec()

    def _open_enhance(self) -> None:
        items = self._grid.action_items()
        if not items:
            self._toaster.info("请先选择图片")
            return
        from .gallery_enhance import EnhanceDialog

        dialog = EnhanceDialog(self._library, items, self)
        dialog.finishedAll.connect(self._on_enhance_done)
        dialog.exec()

    def _after_edit(self) -> None:
        self.reload()

    def _on_edit_saved(self, path: str) -> None:
        """编辑保存后回报到底栏 —— 否则进度条一直停在 0%，看起来像没执行。"""
        self._progress.setValue(100)
        self._status.setText(f"已保存编辑结果：{Path(path).name}（原图未改动）")
        self._after_edit()

    def _on_enhance_done(self, outputs: list, errors: list) -> None:
        """AI 优化（本地增强）结束后把进度与结果写回底栏。"""
        total = len(outputs) + len(errors)
        self._progress.setValue(100 if not errors else int(len(outputs) * 100 / max(1, total)))
        message = f"AI 优化完成：生成 {len(outputs)} 张新图"
        if errors:
            message += f"，失败 {len(errors)} 张（{errors[0]}）"
        self._status.setText(message + "；原图未改动")
        self._after_edit()

    def _analyze_selected(self) -> None:
        items = self._grid.action_items()
        if not items:
            self._toaster.info("请先选择图片")
            return
        from modu_workbench.core.image import load_ai_config
        from modu_workbench.core.llm import KIND_IMAGE, KIND_TEXT
        router = app_context.llm_router()
        allow_upload = load_ai_config(self._library.storage).allow_upload
        if not (router.has_any(KIND_IMAGE) or router.has_any(KIND_TEXT)):
            self._toaster.error("尚未配置大模型：请在「设置 → 大模型」里添加并填写 API Key")
            return
        if self._analyze_worker is not None and self._analyze_worker.isRunning():
            self._toaster.info("正在分析中")
            return
        prefer_vision = self._app_settings().value("gallery/vision", True, type=bool)
        self._cancel_button.setEnabled(True)
        self._analyze_worker = AnalyzeWorker(self._library, items,
                                            use_vision=allow_upload, parent=self,
                                            prefer_vision=prefer_vision)
        self._analyze_worker.progressed.connect(self._on_progress)
        self._analyze_worker.finishedAll.connect(self._on_analyze_done)
        self._analyze_worker.failed.connect(self._on_task_failed)
        self._analyze_worker.finished.connect(self._on_worker_finished)
        self._analyze_worker.start()
        self._status.setText(
            f"AI 分析 {len(items)} 张（{'图片大模型看图' if allow_upload and prefer_vision else '文字大模型·仅元数据'}）…")

    def _on_analyze_done(self, ok: int, errors: list) -> None:
        self._progress.setValue(100)
        message = f"AI 分析完成：成功 {ok}，失败 {len(errors)}"
        if errors:
            message += "；" + "；".join(errors[:2])
        self._status.setText(message)
        if ok:
            self._toaster.success(f"已为 {ok} 张图片生成描述/标签")
        else:
            self._toaster.error("AI 分析失败：请检查 API Key 与网络")
        self.reload()

    def _detect_duplicates(self) -> None:
        if self._duplicate_worker is not None and self._duplicate_worker.isRunning():
            return
        self._status.setText("正在检测重复/相似图片…")
        self._duplicate_worker = DuplicateWorker(self._library, parent=self)
        self._duplicate_worker.finishedGroups.connect(self._on_duplicates)
        self._duplicate_worker.failed.connect(self._on_task_failed)
        self._duplicate_worker.start()

    def _on_duplicates(self, groups: list) -> None:
        self._duplicates = groups
        if not groups:
            self._toaster.info("未发现重复或相似图片")
            self._status.setText("重复检测完成：无重复")
            return
        total = sum(len(group) for group in groups)
        self._toaster.info(f"发现 {len(groups)} 组相似图片（共 {total} 张）")
        self._status.setText(f"发现 {len(groups)} 组相似图片，可在右键菜单里按组删除多余的")
        self._show_duplicate_groups(groups)

    def _show_duplicate_groups(self, groups: list) -> None:
        from PySide6.QtWidgets import QMessageBox

        lines = []
        for index, group in enumerate(groups[:12], start=1):
            names = "、".join(item.display() for item in group[:4])
            lines.append(f"{index}. {len(group)} 张：{names}")
        QMessageBox.information(self, "重复/相似图片",
                                "检测到以下分组（用感知哈希判定）：\n\n" + "\n".join(lines))

    # ------------------------------------------------------------------ 大图查看

    def _open_viewer(self, item: ImageItem) -> None:
        self.show_page(VIEWER_KEY)
        self._show_in_viewer(item)

    def _show_in_viewer(self, item: ImageItem) -> None:
        ok = self._viewer.load_item(self._library, item)
        self._viewer_info.setText(
            f"{item.display()} · {item.resolution} · {item.size_text} · {item.taken_text}"
            + ("" if ok else "（无法显示，可能是格式不支持）")
        )
        self._viewer_details.setHtml(self._details_html(item))
        self._update_zoom_label()
        self._library.storage.mark_opened(item.id)

    def _update_zoom_label(self, *_args) -> None:  # noqa: ANN002
        percent = self._viewer.zoom_percent
        if self._viewer.is_fitted:
            self._viewer_zoom.setText(f"适应窗口（当前显示 {percent}%）")
        else:
            self._viewer_zoom.setText(f"缩放 {percent}%")

    def _step_viewer(self, delta: int) -> None:
        """切换上一张/下一张。

        走的是「当前筛选结果集」（跨页），而不是只在本页里转圈：
        搜索/筛选后应当只在结果集内翻页；翻到别的页时会自动把那一页翻出来。
        """
        if not self._all_items:
            self._toaster.info("当前没有可查看的图片（请先调整筛选条件）")
            return
        current = self._grid.current_item() or self._grid.ensure_current()
        if current is not None and current in self._all_items:
            index = self._all_items.index(current)
        else:
            index = (self._page - 1) * self._page_size
        target = max(0, min(len(self._all_items) - 1, index + delta))
        item = self._all_items[target]
        self.goto_item_page(item.id)          # 跨页时先把目标页翻出来
        row = next((position for position, entry in enumerate(self._grid.items())
                    if entry.id == item.id), -1)
        if row >= 0:
            self._grid.setCurrentRow(row)
            self._grid.scrollToItem(self._grid.item(row))
        self.show_page(VIEWER_KEY)
        self._show_in_viewer(item)

    def _toggle_slideshow(self, enabled: bool) -> None:
        from PySide6.QtCore import QTimer

        if enabled:
            interval = int(self._slide_interval.currentData() or 5) * 1000
            self._slide_timer = QTimer(self)
            self._slide_timer.setInterval(interval)
            self._slide_timer.timeout.connect(lambda: self._step_viewer(1))
            self._slide_timer.start()
            self._status.setText(f"幻灯片播放中（每 {interval // 1000} 秒切换）")
        else:
            timer = getattr(self, "_slide_timer", None)
            if timer is not None:
                timer.stop()
            self._status.setText("已停止幻灯片")

    # ------------------------------------------------------------------ 线程与状态

    def _on_progress(self, done: int, total: int, message: str) -> None:
        self._progress.setValue(int(done * 100 / total) if total else 0)
        self._status.setText(message)

    def _on_task_failed(self, message: str) -> None:
        self._status.setText(f"任务失败：{message}")
        self._toaster.error(message)

    def _on_worker_finished(self) -> None:
        self._cancel_button.setEnabled(False)

    def _cancel_tasks(self) -> None:
        for worker in (self._browse_worker, self._enhance_worker,
                       self._analyze_worker):
            if worker is not None and worker.isRunning():
                worker.cancel()
        self._status.setText("正在取消任务…")

    def _on_grid_menu(self, pos) -> None:  # noqa: ANN001
        # 右键落点即操作对象：默认右键不改选中项，会出现"菜单作用于上一张图"
        # 甚至没有任何选中项时菜单根本不弹（表现：右键没反应）
        clicked = self._grid.focus_at(pos)
        items = self._grid.action_items()
        if clicked is not None:
            items = [clicked]
        if not items:
            self._toaster.info("请右键点在某张图片上")
            return
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self._grid)
        actions = (
            ("🔍 大图查看", lambda: self._open_viewer(items[0])),
            ("✏️ 编辑美化…", self._open_editor),
            ("✨ AI 优化…", self._open_enhance),
            ("🤖 AI 描述/标签", self._analyze_selected),
            ("🏷 打标签…", self._tag_selected),
            ("📁 加入相册…", self._add_to_album),
            ("★ 收藏 / 取消", self._toggle_favorite),
            ("📂 打开所在文件夹", lambda: self._reveal(items[0])),
            ("🗑 删除…", self._delete_selected),
        )
        for label, handler in actions:
            action = menu.addAction(label)
            action.triggered.connect(lambda _checked=False, fn=handler: fn())
        menu.exec(self._grid.viewport().mapToGlobal(pos))

    def _reveal(self, item: ImageItem) -> None:
        if not item.path or not Path(item.path).is_file():
            self._toaster.error("文件不存在（可能已被移动或删除）")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(item.path).parent)))

    def _ai_search_clicked(self) -> None:
        """自然语言检索：大模型（文字类型，多配置降级）拆关键词 → 本地筛选。"""
        query = self._search.text().strip()
        if not query:
            self._toaster.info("请先在搜索框输入一句话，例如：去年海边的照片")
            return
        if not app_context.llm_router().has_any("text"):
            self._toaster.error("尚未配置文字大模型（设置 → 大模型）")
            return
        try:
            keywords = self._library.ai_suggest_keywords(query)
        except Exception as error:  # noqa: BLE001
            self._toaster.error(str(error))
            return
        if not keywords:
            self._toaster.info("未能拆出关键词，已按原词搜索")
            return
        self._search.setText(keywords[0])
        self._status.setText("AI 关键词：" + "、".join(keywords))
        self.reload()

    def _update_buttons(self) -> None:
        has = bool(self._grid.action_items())
        for button in (self._tag_button, self._favorite_button, self._album_button,
                       self._edit_button, self._enhance_button, self._analyze_button,
                       self._delete_button):
            button.setEnabled(has)
        self._edit_button.setEnabled(bool(self._grid.current_item() or has))

    def shutdown(self) -> None:
        timer = getattr(self, "_slide_timer", None)
        if timer is not None:
            timer.stop()
        for worker in (self._browse_worker, self._enhance_worker,
                       self._analyze_worker, self._duplicate_worker, self._url_worker):
            if worker is None:
                continue
            try:
                if worker.isRunning():
                    for method in ("cancel",):
                        if hasattr(worker, method):
                            getattr(worker, method)()
                    worker.wait(2500)
            except RuntimeError:
                continue


__all__ = ["BROWSE_KEY", "VIEWER_KEY", "GalleryBoardPage"]
