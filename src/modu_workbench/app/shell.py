"""应用主壳：顶栏导航 + 页面栈（首页 / 各板块）。"""
from __future__ import annotations

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
    """主壳：顶栏导航 + 页面栈。**板块页按需构造**。

    为什么要按需：旧实现把 6 个板块的页面全部在 `__init__` 里构造完
    （实测 offscreen 下 154ms，真机上还要给每个板块建表格/卡片/播放器，
    更慢且更费内存）。而用户一次只会看一个板块 —— 没进过的板块不该先付这笔钱。
    现在 `go_page()` 第一次进入某板块时才构造，后续复用同一个实例。
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} v{__version__}")   # 任务栏/标题栏显示墨软·工作台
        self._nav_buttons: dict[str, QPushButton] = {}
        self._pages: dict[str, int] = {}          # key -> 栈索引（构造后才有）
        self._factories: dict[str, object] = {}   # key -> 页面工厂（BoardSpec.page）
        self._built: dict[str, QWidget] = {}      # key -> 已构造的页面
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
        self._built[HOME_KEY] = self._home_page
        self._factories = {spec.key: spec.page for spec in ACTIVE_BOARDS}

        self.go_page(HOME_KEY)

    # ------------------------------------------------------------------ 页面构造

    def _ensure_page(self, key: str) -> QWidget | None:
        """按需构造板块页；key 不存在返回 None。失败时**不缓存半成品**。"""
        existing = self._built.get(key)
        if existing is not None:
            return existing
        factory = self._factories.get(key)
        if factory is None:
            return None
        page = factory()                      # type: ignore[operator]  契约：BoardSpec.page 可调用
        self._stack.addWidget(page)
        self._pages[key] = self._stack.count() - 1
        self._built[key] = page
        return page

    def is_page_built(self, key: str) -> bool:
        """该板块页是否已经构造过（测试与诊断用）。"""
        return key in self._built

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
        """切换板块；**首次进入时才构造页面**。"""
        index = self._pages.get(key)
        if index is None:
            if self._ensure_page(key) is None:
                return
            index = self._pages[key]
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
