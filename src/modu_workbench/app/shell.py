"""应用主壳：顶栏导航 + 页面栈（首页 / 各板块）。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, APP_SLOGAN, __version__
from ..boards.home.page import HomePage
from .registry import ACTIVE_BOARDS
from ..ui_kit.widgets import make_nav_button, set_nav_active

HOME_KEY = "home"


class AppShell(QMainWindow):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} v{__version__}")   # 任务栏/标题栏显示墨软·工作台
        self._nav_buttons: dict[str, QPushButton] = {}
        self._pages: dict[str, int] = {}
        self.current_key = HOME_KEY

        root = QWidget()
        root.setObjectName("rootPage")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_top_bar())
        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)

        self._home_page = HomePage()
        self._home_page.open_board.connect(self.go_page)
        self._stack.addWidget(self._home_page)
        self._pages[HOME_KEY] = 0

        for spec in ACTIVE_BOARDS:
            page = spec.page()
            self._stack.addWidget(page)
            self._pages[spec.key] = self._stack.count() - 1

        self.go_page(HOME_KEY)

    def _build_top_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("topBar")
        bar.setFixedHeight(56)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(18, 0, 18, 0)
        layout.setSpacing(16)

        brand = QVBoxLayout()
        brand.setSpacing(0)
        title = QLabel("墨软·工作台")
        title.setObjectName("brandTitle")
        sub = QLabel(f"{APP_SLOGAN} · v{__version__}")
        sub.setObjectName("brandSub")
        brand.addWidget(title)
        brand.addWidget(sub)
        layout.addLayout(brand)
        layout.addStretch(1)

        self._nav_buttons[HOME_KEY] = make_nav_button("首页")
        self._nav_buttons[HOME_KEY].clicked.connect(lambda: self.go_page(HOME_KEY))
        layout.addWidget(self._nav_buttons[HOME_KEY])

        for spec in ACTIVE_BOARDS:
            button = make_nav_button(spec.title)
            button.clicked.connect(lambda _=False, key=spec.key: self.go_page(key))
            self._nav_buttons[spec.key] = button
            layout.addWidget(button)

        settings_btn = make_nav_button("⚙ 设置")
        settings_btn.clicked.connect(lambda: self._open_settings())
        layout.addWidget(settings_btn)

        return bar

    def _open_settings(self, board_key: str = "") -> None:
        """打开设置对话框；传入板块 key 时直接定位到该板块那一页。

        各板块顶栏的「⚙ 设置」会带上自己的 key，实现「设置区分是哪个板块的」。
        """
        from ..ui_kit.settings import SettingsDialog

        dialog = SettingsDialog(self, initial=board_key or "general")
        dialog.exec()

    def open_settings_for_current_board(self) -> None:
        """供板块调用：打开当前板块对应的设置页。"""
        key = self.current_key if self.current_key != HOME_KEY else ""
        self._open_settings(key)

    def go_page(self, key: str) -> None:
        index = self._pages.get(key)
        if index is None:
            return
        self._stack.setCurrentIndex(index)
        self.current_key = key
        for nav_key, button in self._nav_buttons.items():
            set_nav_active(button, nav_key == key)
        # 板块可选实现 on_shown()：进入时刷新数据（例如音乐板块曲库/歌单）
        page = self._stack.currentWidget()
        hook = getattr(page, "on_shown", None)
        if callable(hook):
            hook()

    def closeEvent(self, event) -> None:  # noqa: N802
        """退出前停止各板块的后台线程与播放。"""
        for index in range(self._stack.count()):
            page = self._stack.widget(index)
            hook = getattr(page, "shutdown", None)
            if callable(hook):
                try:
                    hook()
                except Exception:  # noqa: BLE001
                    pass
        super().closeEvent(event)

    def current_page(self) -> QWidget:
        return self._stack.currentWidget()
