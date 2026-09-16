"""设置对话框：按板块分页（通用 / 书库 / 转换 / 乐库 / 影视 / 图库）。

各板块顶栏的「⚙ 设置」可直接跳到本板块那一页（`initial="gallery"` 等），
未进入过的板块页在首次切换时才构造，避免启动时把所有板块的注册表都拉起来。
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .base import SETTINGS_APP, SETTINGS_ORG, SettingsPage, app_settings
from .book import BookSettingsPage
from .convert import ConvertSettingsPage
from .gallery import GallerySettingsPage
from .general import GeneralSettingsPage
from .llm import LlmSettingsPage
from .music import MusicSettingsPage
from .video import VideoSettingsPage

PAGE_ROLE = Qt.ItemDataRole.UserRole

# (key, 标题, 图标, 页面工厂)
PAGE_FACTORIES: tuple[tuple[str, str, str, Callable[[], SettingsPage]], ...] = (
    ("general", "通用", "🧩", GeneralSettingsPage),
    ("llm", "大模型", "🤖", LlmSettingsPage),
    ("book", "书库", "📚", BookSettingsPage),
    ("convert", "转换", "🔄", ConvertSettingsPage),
    ("music", "乐库", "🎧", MusicSettingsPage),
    ("video", "影视", "🎬", VideoSettingsPage),
    ("gallery", "图库", "🖼", GallerySettingsPage),
)

PAGE_TITLES = {key: title for key, title, _icon, _factory in PAGE_FACTORIES}


class SettingsDialog(QDialog):
    """统一设置入口：左侧按板块分区，右侧显示对应设置页。

    右侧每一页都套在 `QScrollArea` 里并且**操作条固定在底部**：
    设置项多的板块（图库/大模型）在小窗口下会把内容撑高，
    以前会把「保存 / 关闭」顶出可视区域，用户根本点不到。
    """

    def __init__(self, parent: QWidget | None = None, *, initial: str = ""):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.resize(980, 700)
        self.setMinimumSize(760, 520)
        self._settings = app_settings()
        self._pages: dict[str, SettingsPage] = {}
        self._scrolls: dict[str, QScrollArea] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._nav = QListWidget()
        self._nav.setObjectName("settingsNav")
        self._nav.setFixedWidth(190)
        for key, title, icon, _factory in PAGE_FACTORIES:
            entry = QListWidgetItem(f"{icon}  {title}")
            entry.setData(PAGE_ROLE, key)
            entry.setSizeHint(QSize(0, 38))
            self._nav.addItem(entry)
        self._nav.currentItemChanged.connect(lambda *_: self._on_nav_changed())
        body.addWidget(self._nav)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)

        self._stack = QStackedWidget()
        right.addWidget(self._stack, 1)

        self._hint = QLabel("")
        self._hint.setObjectName("readerStatus")
        self._hint.setWordWrap(True)
        self._hint.setContentsMargins(18, 6, 18, 0)
        right.addWidget(self._hint)

        # 操作条与内容之间加一条分隔线，滚动时视觉上"钉"在底部
        separator = QFrame()
        separator.setObjectName("hline")
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFixedHeight(1)
        right.addWidget(separator)

        actions = QHBoxLayout()
        actions.setContentsMargins(18, 8, 18, 12)
        actions.addStretch(1)
        save_button = QPushButton("保存")
        save_button.setObjectName("primaryButton")
        save_button.clicked.connect(self._save)
        actions.addWidget(save_button)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.reject)
        actions.addWidget(close_button)
        right.addLayout(actions)
        body.addLayout(right, 1)
        layout.addLayout(body)

        self._select(initial or "general")

    # ------------------------------------------------------------------ 分页

    def _ensure_page(self, key: str) -> SettingsPage | None:
        if key in self._pages:
            return self._pages[key]
        factory = next((item[3] for item in PAGE_FACTORIES if item[0] == key), None)
        if factory is None:
            return None
        try:
            page: SettingsPage = factory()
            page.load()
        except Exception as error:  # noqa: BLE001
            # 单个板块设置页构造/加载失败（例如该板块数据库损坏）不应拖垮整个对话框：
            # 用占位页把原因显示出来
            page = _ErrorPage(f"{PAGE_TITLES.get(key, key)} 设置加载失败：{error}")
        self._pages[key] = page
        # 内容套滚动区：页面再高也只会出现滚动条，不会把「保存/关闭」挤出窗口
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(page)
        self._scrolls[key] = scroll
        self._stack.addWidget(scroll)
        return page

    def _select(self, key: str) -> None:
        for row in range(self._nav.count()):
            entry = self._nav.item(row)
            if entry.data(PAGE_ROLE) == key:
                self._nav.setCurrentRow(row)
                return
        self._nav.setCurrentRow(0)

    def _on_nav_changed(self) -> None:
        entry = self._nav.currentItem()
        if entry is None:
            return
        key = entry.data(PAGE_ROLE)
        page = self._ensure_page(key)
        if page is not None:
            self._stack.setCurrentWidget(self._scrolls[key])
        self._hint.setText(page.hint() if page is not None else "")

    # ------------------------------------------------------------------ 保存

    def _save(self) -> None:
        changed = False
        for page in self._pages.values():
            try:
                changed = page.save() or changed
            except Exception as error:  # noqa: BLE001  单页保存失败不影响其它页
                self._hint.setText(f"部分设置保存失败：{error}")
        self._settings.sync()
        self.accept()


class _ErrorPage(SettingsPage):
    """设置页构造失败时的占位（把原因显示出来，而不是整窗崩掉）。"""

    title = "错误"
    icon = "⚠"

    def __init__(self, message: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._message = message
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        label = QLabel(message)
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addStretch(1)

    def hint(self) -> str:
        return f"{self._message}；若刚安装，请先启动一次应用以初始化数据库。"


__all__ = [
    "PAGE_FACTORIES",
    "PAGE_ROLE",
    "PAGE_TITLES",
    "SETTINGS_APP",
    "SETTINGS_ORG",
    "SettingsDialog",
    "SettingsPage",
]
