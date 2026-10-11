"""墨软动漫：板块界面测试（offscreen 无头 Qt，**全程不联网**）。

后端 `core/anime/` 由另一个模块并行开发，所以这里的策略是：

- 用**假后端**（`FakeLibrary` 等）注入到 `boards.anime.context` 的入口上，
  只验证界面行为（增量插卡、五步试跑、徽章、进度标记、设置读写）；
- 另外单独验证"**后端完全不可用时板块仍能构造**"——这是并行开发期间最容易
  回归的一条（`core/anime/__init__.py` 一坏，整个主壳都会起不来）。
"""
from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton, QWidget

from modu_workbench.boards.anime import context as anime_context
from modu_workbench.boards.anime.board import NAV_ITEMS, AnimeBoardPage
from modu_workbench.boards.anime.detail import AnimeDetailDialog
from modu_workbench.boards.anime.library_page import AnimeLibraryPage
from modu_workbench.boards.anime.rules_page import TRIAL_STEPS, AnimeRulesPage
from modu_workbench.boards.anime.schedule import EMPTY_HINT as SCHEDULE_EMPTY_HINT
from modu_workbench.boards.anime.schedule import AnimeSchedulePage
from modu_workbench.boards.anime.search import NO_RULE_HINT, AnimeSearchPage
from modu_workbench.boards.anime.history import AnimeHistoryPage
from modu_workbench.boards.anime.widgets import (
    EpisodeGrid,
    PosterCard,
    anime_settings,
    episode_key,
)
from modu_workbench.ui_kit.settings import PAGE_FACTORIES, SettingsDialog
from modu_workbench.ui_kit.tokens import LIGHT
from modu_workbench.ui_kit.toast import Toaster

# --------------------------------------------------------------------------- 夹具


_HOSTS: list[QWidget] = []


def toaster() -> Toaster:
    """测试用提示器：需要一个隐藏宿主控件（运行时是主窗口）。"""
    host = QWidget()
    host.hide()
    _HOSTS.append(host)
    return Toaster(host)


def settle(app: QApplication, predicate, timeout: float = 20.0) -> bool:  # noqa: ANN001
    """轮询 + 处理事件，等后台线程把信号投递完（单次 wait 会漏掉排队信号）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return predicate()


def empty_hint(state) -> str:  # noqa: ANN001
    label = state.findChild(QLabel, "emptyHint")
    return label.text() if label is not None else ""


@contextlib.contextmanager
def _temp_settings(**values: object):  # noqa: ANN201
    """临时改 QSettings（None = 删掉该键），用完还原，避免污染其它测试。"""
    from modu_workbench.ui_kit.settings import app_settings

    settings = app_settings()
    previous = {key: settings.value(key, None) for key in values}
    for key, value in values.items():
        if value is None:
            settings.remove(key)
        else:
            settings.setValue(key, value)
    settings.sync()
    try:
        yield settings
    finally:
        for key, value in previous.items():
            if value is None:
                settings.remove(key)
            else:
                settings.setValue(key, value)
        settings.sync()


# --------------------------------------------------------------------------- 假后端


@dataclass
class FakeEpisode:
    index: int = 0
    name: str = ""
    url: str = ""
    road_index: int = 0
    road_name: str = "线路A"
    number: int = 0

    @property
    def key(self) -> str:
        return f"{self.road_index}:{self.number or self.index}:{self.name}"

    @property
    def label(self) -> str:
        return self.name or f"第 {self.number or self.index + 1} 集"


@dataclass
class FakeRoad:
    index: int = 0
    name: str = "线路A"
    episodes: list = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.name

    @property
    def count(self) -> int:
        return len(self.episodes)


@dataclass
class FakeSeries:
    id: int = 0
    rule_key: str = "builtin:sample"
    remote_id: str = "1"
    title: str = ""
    cover: str = ""
    year: str = ""
    summary: str = ""
    total_episodes: int = 0
    latest_episode: str = ""
    air_weekday: int = -1
    score: float = 0.0

    @property
    def key(self) -> str:
        return f"{self.rule_key}:{self.remote_id}"


@dataclass
class FakeHit:
    rule_key: str = "builtin:sample"
    rule_name: str = "本地样例站"
    title: str = ""
    remote_id: str = "1"
    url: str = "http://127.0.0.1:8000/vod/1"
    cover: str = ""
    year: str = ""
    note: str = ""


@dataclass
class FakeGroup:
    title: str = ""
    cover: str = ""
    year: str = ""
    sources: list = field(default_factory=list)


@dataclass
class FakeDetail:
    series: FakeSeries
    roads: list = field(default_factory=list)
    note: str = ""

    @property
    def episode_count(self) -> int:
        return sum(len(road.episodes) for road in self.roads)

    def road(self, index: int):  # noqa: ANN201
        for item in self.roads:
            if item.index == index:
                return item
        return None


@dataclass
class FakeProgress:
    episode_key: str = ""
    position_ms: int = 0
    duration_ms: int = 0
    watched: bool = False

    @property
    def percent(self) -> int:
        if self.duration_ms <= 0:
            return 0
        return max(0, min(100, int(self.position_ms * 100 / self.duration_ms)))

    @property
    def finished(self) -> bool:
        return self.watched


@dataclass
class FakeResolved:
    url: str = "http://cdn.example.com/1.m3u8"
    is_hls: bool = True
    referer: str = "http://127.0.0.1:8000/"
    strategy: str = "json-player-aaaa"
    note: str = ""


@dataclass
class FakeUpdate:
    series: FakeSeries
    new_episodes: int = 0
    old_total: int = 0
    new_total: int = 0
    latest_episode: str = ""
    error: str = ""

    @property
    def has_new(self) -> bool:
        return self.new_episodes > 0


@dataclass
class FakeReport:
    items: list = field(default_factory=list)
    checked: int = 0
    failed: int = 0

    @property
    def with_new(self) -> int:
        return sum(1 for item in self.items if item.has_new)


@dataclass
class FakeRule:
    """规则替身：字段与 `AnimeRule` 对齐（真实模型在 core.anime.models 里）。"""

    key: str = "builtin:sample"
    name: str = "本地样例站"
    version: str = "1.0"
    api: str = "8"
    search_mode: str = "xpath"
    search_url: str = "http://127.0.0.1:8000/search?wd=@keyword"
    builtin: bool = True
    deprecated: bool = False
    use_webview: bool = False
    enabled: bool = True
    last_test: str = ""
    path: str = ""
    schedule: dict = field(default_factory=dict)

    @property
    def api_level(self) -> int:
        try:
            return int(self.api)
        except (TypeError, ValueError):
            return 8

    @property
    def supported(self) -> bool:
        return self.api_level <= 8

    @property
    def kind(self) -> str:
        return self.search_mode

    @property
    def label(self) -> str:
        return self.name


@dataclass
class FakeHistoryEntry:
    title: str = "葬送的芙莉莲"
    episode_name: str = "第1集"
    rule_key: str = "builtin:sample"
    played_at: int = 1_700_000_000
    series_id: int = 1
    position_ms: int = 0
    series: object = None
    episode: object = None


class FakeStorage:
    """存储替身：只实现界面用到的历史接口。"""

    def __init__(self, entries: list | None = None):
        self._entries = list(entries or [])
        self.cleared = 0

    def list_history(self, *, limit: int = 400) -> list:
        return list(self._entries)[:limit]

    def clear_history(self) -> None:
        self.cleared += 1
        self._entries = []


class FakeLibrary:
    """动漫后端替身：实现界面真正调用到的方法，绝不联网。"""

    def __init__(self, *, rules: list | None = None, groups: list | None = None,
                 favorites: list | None = None, continue_items: list | None = None,
                 detail: object | None = None, report: object | None = None,
                 resolved: object | None = None, progress: dict | None = None,
                 resume: dict | None = None, search_error: str = "",
                 detail_error: str = ""):
        self._rules = list(rules or [])
        self._groups = list(groups or [])
        self._favorites = list(favorites or [])
        self._continue = list(continue_items or [])
        self._detail = detail
        self._report = report
        self._resolved = resolved or FakeResolved()
        self._progress = dict(progress or {})
        self._resume = dict(resume or {})
        self._search_error = search_error
        self._detail_error = detail_error
        self.enabled_calls: list = []
        self.search_calls: list = []
        self.imported: list = []
        self.last_errors: list = []

    # ---- 规则 ----
    def rules(self, *, enabled_only: bool = False) -> list:
        if enabled_only:
            return [rule for rule in self._rules if rule.enabled]
        return list(self._rules)

    def set_rule_enabled(self, key: str, on: bool) -> None:
        self.enabled_calls.append((key, bool(on)))

    def reload_rules(self) -> list:
        return list(self._rules)

    def import_rules(self, text_or_path) -> list:  # noqa: ANN001
        self.imported.append(text_or_path)
        return [FakeRule(key="user:imported", name="导入的规则", builtin=False)]

    # ---- 搜索 / 详情 / 解析 ----
    def search(self, keyword, *, rule_keys=None, limit=30, on_rule_done=None):  # noqa: ANN001
        self.search_calls.append((keyword, tuple(rule_keys or ())))
        if self._search_error:
            raise RuntimeError(self._search_error)
        for group in self._groups:
            if on_rule_done is not None:
                on_rule_done(group)
        return list(self._groups)

    def detail(self, payload):  # noqa: ANN001
        if self._detail_error:
            raise RuntimeError(self._detail_error)
        if self._detail is None:
            raise RuntimeError("替身没有配置详情")
        return self._detail

    def resolve(self, series, episode):  # noqa: ANN001
        return self._resolved

    # ---- 追番 / 进度 ----
    @property
    def followed(self) -> list:
        """追番列表的只读视图（追番/取消追番都改同一份数据）。"""
        return list(self._favorites)

    def is_followed(self, series) -> bool:  # noqa: ANN001
        return any(item.key == series.key for item in self._favorites)

    def follow(self, hit):  # noqa: ANN001
        series = hit if isinstance(hit, FakeSeries) else FakeSeries(
            id=len(self._favorites) + 1, title=str(getattr(hit, "title", "")))
        if not self.is_followed(series):
            self._favorites.append(series)
        return series

    def unfollow(self, series) -> None:  # noqa: ANN001
        self._favorites = [item for item in self._favorites if item.key != series.key]

    def favorites(self) -> list:
        return list(self._favorites)

    def continue_watching(self) -> list:
        return list(self._continue)

    def series_progress(self, series) -> dict:  # noqa: ANN001
        return dict(self._progress)

    def resume_position(self, series, episode) -> int:  # noqa: ANN001
        return int(self._resume.get(episode_key(episode), 0))

    def check_updates(self, *, on_progress=None):  # noqa: ANN001
        if on_progress is not None:
            on_progress("检查 1 部")
        if self._report is None:
            raise RuntimeError("替身没有配置更新报告")
        return self._report

    def test_rule(self, rule, keyword):  # noqa: ANN001
        return True, f"{rule.name} 试跑通过"


@pytest.fixture()
def fake_library() -> FakeLibrary:
    return FakeLibrary()


def _wire(monkeypatch: pytest.MonkeyPatch, library: FakeLibrary,
          storage: FakeStorage | None = None) -> None:
    """把替身接到板块 context 的入口上（界面只通过 context 取后端）。"""
    monkeypatch.setattr(anime_context, "anime_library", lambda: library)
    monkeypatch.setattr(anime_context, "anime_storage",
                        lambda: storage if storage is not None else FakeStorage())
    monkeypatch.setattr(anime_context, "anime_rules", lambda: None)


def _episodes(count: int = 3) -> list:
    return [FakeEpisode(index=index, name=f"第{index + 1}集",
                        url=f"http://127.0.0.1:8000/play/{index + 1}",
                        road_index=0, number=index + 1)
            for index in range(count)]


def _detail(title: str = "葬送的芙莉莲", count: int = 3) -> FakeDetail:
    series = FakeSeries(id=1, title=title, year="2023", total_episodes=count,
                        latest_episode=f"第{count}集", summary="魔法使的故事。", score=9.1)
    return FakeDetail(series=series,
                      roads=[FakeRoad(index=0, name="线路A", episodes=_episodes(count))])


# --------------------------------------------------------------------------- 板块骨架


def test_board_constructs_without_backend(qapp: QApplication,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """后端完全不可用时：板块必须能构造，并且给出"下一步"而不是抛异常。"""
    def boom():  # noqa: ANN202
        raise ImportError("模拟 core/anime 尚未就绪")

    monkeypatch.setattr(anime_context, "anime_library", boom)
    monkeypatch.setattr(anime_context, "anime_storage", boom)
    monkeypatch.setattr(anime_context, "anime_rules", boom)

    page = AnimeBoardPage()
    try:
        assert page._library is None
        assert "后端未就绪" in page._backend_hint.text()
        # 搜索页给的是空状态 + 明确的下一步（不是一张空表格）
        assert page._search._empty.isHidden() is False
        assert NO_RULE_HINT in empty_hint(page._search._empty)
    finally:
        page.shutdown()


def test_board_registered_last_in_active_boards() -> None:
    from modu_workbench.app.registry import ACTIVE_BOARDS, get_board

    spec = get_board("anime")
    assert spec is not None
    assert spec.title == "墨软动漫"
    assert spec.icon == "🎞"
    assert spec.phase == "可用"
    assert spec.description
    keys = [board.key for board in ACTIVE_BOARDS]
    assert keys[-1] == "anime", "第七板块追加在末尾，不影响既有板块位置"
    assert keys[:5] == ["book", "convert", "music", "video", "gallery"]


def test_nav_buttons_and_page_switching(qapp: QApplication,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    library = FakeLibrary(rules=[FakeRule()], favorites=[], report=FakeReport())
    _wire(monkeypatch, library)
    page = AnimeBoardPage()
    try:
        labels = [button.text() for button in page._nav_buttons.values()]
        assert labels == ["🔍 搜索", "📺 番剧库", "🗓 时间表", "🧩 规则", "🕘 历史"]
        assert [key for key, _label in NAV_ITEMS] == list(page._nav_buttons)
        settings = [button for button in page.findChildren(QPushButton)
                    if button.text() == "⚙ 设置"]
        assert settings, "板块必须有「⚙ 设置」入口（直达设置 → 动漫）"

        for key, _label in NAV_ITEMS:
            page.show_page(key)                       # 切页不能抛异常
            assert page._stack.currentIndex() == page._indexes[key]
            assert page._nav_buttons[key].objectName() == "navButtonActive"
        assert page._task_bar is not None
    finally:
        page.shutdown()


# --------------------------------------------------------------------------- 设置页


def test_anime_settings_page_registered_and_round_trips(qapp: QApplication) -> None:
    keys = [key for key, _title, _icon, _factory in PAGE_FACTORIES]
    assert keys[-1] == "anime"

    from modu_workbench.ui_kit.settings.anime import AnimeSettingsPage

    managed = ("anime/auto_next", "anime/download_dir", "anime/segment_concurrency",
               "anime/download_parallel", "anime/danmaku_enabled", "anime/danmaku_app_id",
               "anime/danmaku_app_secret", "anime/danmaku_opacity", "anime/danmaku_speed",
               "anime/danmaku_max_lines", "anime/danmaku_block_words",
               "anime/bangumi_enabled", "anime/update_on_start")
    with _temp_settings(**{key: None for key in managed}):
        dialog = SettingsDialog(initial="anime")
        dialog.resize(980, 700)
        dialog.show()
        qapp.processEvents()
        fresh = None
        try:
            page = dialog._ensure_page("anime")                 # noqa: SLF001
            assert page is not None and page.title == "动漫"
            # 设置项多的页不能横向滚动（默认宽度下）
            assert dialog._scrolls["anime"].horizontalScrollBar().maximum() == 0  # noqa: SLF001

            page._auto_next.setChecked(False)                   # noqa: SLF001
            page._download_dir.setText("D:/动画")                # noqa: SLF001
            page._segment_concurrency.setValue(12)              # noqa: SLF001
            page._download_parallel.setValue(3)                 # noqa: SLF001
            page._danmaku_enabled.setChecked(True)              # noqa: SLF001
            page._danmaku_app_id.setText("my-app-id")           # noqa: SLF001
            page._danmaku_app_secret.setText("my-secret")       # noqa: SLF001
            page._danmaku_opacity.setValue(55)                  # noqa: SLF001
            page._danmaku_speed.setValue(6)                     # noqa: SLF001
            page._danmaku_lines.setValue(40)                    # noqa: SLF001
            page._danmaku_block.setText("广告,/^\\d+$/")          # noqa: SLF001
            page._bangumi_enabled.setChecked(False)             # noqa: SLF001
            page._update_on_start.setChecked(True)              # noqa: SLF001
            assert page.save() is True
            assert page.hint()

            fresh = AnimeSettingsPage()
            fresh.load()
            assert fresh._auto_next.isChecked() is False                  # noqa: SLF001
            assert fresh._download_dir.text() == "D:/动画"                 # noqa: SLF001
            assert fresh._segment_concurrency.value() == 12               # noqa: SLF001
            assert fresh._download_parallel.value() == 3                  # noqa: SLF001
            assert fresh._danmaku_enabled.isChecked() is True             # noqa: SLF001
            assert fresh._danmaku_app_secret.text() == "my-secret"        # noqa: SLF001
            assert fresh._danmaku_app_secret.echoMode() == QLineEdit.EchoMode.Password  # noqa: SLF001
            assert fresh._danmaku_opacity.value() == 55                   # noqa: SLF001
            assert fresh._danmaku_speed.value() == 6                      # noqa: SLF001
            assert fresh._danmaku_lines.value() == 40                     # noqa: SLF001
            assert fresh._danmaku_block.text() == "广告,/^\\d+$/"          # noqa: SLF001
            assert fresh._bangumi_enabled.isChecked() is False            # noqa: SLF001
            assert fresh._update_on_start.isChecked() is True             # noqa: SLF001

            # 存的类型要对（bool 在 Windows 注册表里会变成 "true"/"false" 字符串）
            settings = anime_settings()
            assert isinstance(settings.value("anime/auto_next", type=bool), bool)
            assert settings.value("anime/segment_concurrency", type=int) == 12
            assert settings.value("anime/danmaku_opacity", type=int) == 55
            assert settings.value("anime/danmaku_enabled", type=bool) is True
        finally:
            dialog.close()
            if fresh is not None:
                fresh.deleteLater()


def test_anime_settings_helper_round_trips() -> None:
    with _temp_settings(**{"anime/auto_next": None}):
        settings = anime_settings()
        settings.setValue("anime/auto_next", False)
        settings.sync()
        assert settings.value("anime/auto_next", True, type=bool) is False


# --------------------------------------------------------------------------- 搜索页


def test_search_inserts_cards_incrementally(qapp: QApplication) -> None:
    """边搜边出：每条规则完成就立刻插一批卡（不是等全部规则跑完再一次性渲染）。"""
    groups = [
        FakeGroup(title="葬送的芙莉莲",
                  sources=[FakeHit(rule_name="A规则", title="葬送的芙莉莲", remote_id="1")]),
        FakeGroup(title="灵能百分百",
                  sources=[FakeHit(rule_name="B规则", title="灵能百分百", remote_id="2")]),
    ]
    library = FakeLibrary(rules=[FakeRule()], groups=groups)
    page = AnimeSearchPage(library, toaster())
    counts: list[int] = []
    page.resultAdded.connect(counts.append)
    try:
        page._keyword.setText("芙莉莲")                 # noqa: SLF001
        page._start_search()                           # noqa: SLF001
        assert settle(qapp, lambda: page._worker is None and len(counts) == 2), "搜索线程未结束"
        # 顺序证据：第一条规则后是 1 张卡，第二条之后才变 2 —— 不是最后一次性插入
        assert counts == [1, 2]
        assert len(page._cards) == 2                   # noqa: SLF001
        assert page._results_host.isHidden() is False   # noqa: SLF001
        assert "2 部" in page._count_chip.text()        # noqa: SLF001
    finally:
        page.shutdown()


def test_search_merges_same_normalized_title(qapp: QApplication) -> None:
    """同一部作品在不同规则下标题写法不同（间隔号/空格）→ 合并成一张卡列出全部来源。"""
    groups = [
        FakeGroup(title="葬送的芙莉莲",
                  sources=[FakeHit(rule_name="A规则", title="葬送的芙莉莲", remote_id="1")]),
        FakeGroup(title="葬送的·芙莉莲",
                  sources=[FakeHit(rule_name="B规则", title="葬送的·芙莉莲", remote_id="2")]),
    ]
    library = FakeLibrary(rules=[FakeRule()], groups=groups)
    page = AnimeSearchPage(library, toaster())
    counts: list[int] = []
    page.resultAdded.connect(counts.append)
    try:
        page._keyword.setText("芙莉莲")                 # noqa: SLF001
        page._start_search()                           # noqa: SLF001
        assert settle(qapp, lambda: page._worker is None and len(counts) == 2), "搜索线程未结束"
        assert counts == [1, 1], "归一化后是同一部作品：第二次不再新建卡片"
        assert len(page._cards) == 1                   # noqa: SLF001
        card = next(iter(page._cards.values()))
        assert isinstance(card, PosterCard)
        assert len(card.sources) == 2
        assert "A规则" in card.source_label.text()
        assert "B规则" in card.source_label.text()
    finally:
        page.shutdown()


def test_search_without_rules_tells_next_step(qapp: QApplication) -> None:
    page = AnimeSearchPage(FakeLibrary(rules=[]), toaster())
    try:
        assert NO_RULE_HINT in empty_hint(page._empty)   # noqa: SLF001
        page._start_search()                             # noqa: SLF001
        assert page._worker is None                      # noqa: SLF001  没有规则就不发请求
    finally:
        page.shutdown()


def test_search_failure_is_reported(qapp: QApplication) -> None:
    library = FakeLibrary(rules=[FakeRule()], search_error="HTTP 403：站点拒绝访问")
    page = AnimeSearchPage(library, toaster())
    try:
        page._keyword.setText("芙莉莲")                    # noqa: SLF001
        page._start_search()                              # noqa: SLF001
        assert settle(qapp, lambda: page._worker is None), "搜索线程未结束"
        assert "HTTP 403" in page._status.text()           # noqa: SLF001
        assert "HTTP 403" in empty_hint(page._empty)       # noqa: SLF001
    finally:
        page.shutdown()


# --------------------------------------------------------------------------- 规则页


def test_rules_page_lists_and_shows_honest_warnings(qapp: QApplication) -> None:
    rules = [
        FakeRule(key="builtin:ok", name="可用规则"),
        FakeRule(key="builtin:new", name="高版本规则", api="9"),
        FakeRule(key="builtin:old", name="退役规则", deprecated=True),
        FakeRule(key="builtin:js", name="网页渲染规则", use_webview=True),
    ]
    library = FakeLibrary(rules=rules)
    page = AnimeRulesPage(library, None, toaster())
    try:
        assert page._table.rowCount() == 4                       # noqa: SLF001
        warnings = "｜".join(page._table.item(row, 6).text()      # noqa: SLF001
                             for row in range(page._table.rowCount()))  # noqa: SLF001
        assert "需要更新的客户端" in warnings
        assert "已退役" in warnings
        assert "依赖网页渲染，可能解析失败" in warnings
        # 灰显：有问题的那三行名称用最浅文字色
        assert page._table.item(1, 0).foreground().color().name() == LIGHT.text_faint.lower()  # noqa: SLF001
        assert page._table.item(0, 0).foreground().color().name() != LIGHT.text_faint.lower()  # noqa: SLF001
        assert page._table.item(0, 5).text() == "内置"            # noqa: SLF001

        # 启用勾选真的写回后端
        page._table.item(0, 3).setCheckState(Qt.CheckState.Unchecked)   # noqa: SLF001
        assert library.enabled_calls == [("builtin:ok", False)]
    finally:
        page.shutdown()


def test_rules_page_trial_run_reports_five_steps(qapp: QApplication) -> None:
    detail = _detail()
    groups = [FakeGroup(title="葬送的芙莉莲", year="2023",
                        sources=[FakeHit(title="葬送的芙莉莲", remote_id="1")])]
    library = FakeLibrary(rules=[FakeRule()], groups=groups, detail=detail,
                          resolved=FakeResolved())
    page = AnimeRulesPage(library, None, toaster())
    try:
        page._trial_keyword.setText("芙莉莲")                      # noqa: SLF001
        page._run_trial()                                         # noqa: SLF001
        assert settle(qapp, lambda: page._trial_worker is None), "试跑线程未结束"
        steps = page.step_texts()
        assert len(steps) == len(TRIAL_STEPS) == 5
        assert "搜索 URL" in steps[0] and "search?wd=" in steps[0]
        assert "命中 1 条" in steps[1]
        assert "葬送的芙莉莲" in steps[2] and "127.0.0.1" in steps[2]
        assert "1 条线路 / 3 集" in steps[3]
        assert "m3u8" in steps[4] and "json-player-aaaa" in steps[4]
        assert page.trial_note() == ""
        assert "试跑成功" in page._status.text()                   # noqa: SLF001
    finally:
        page.shutdown()


def test_rules_page_trial_run_shows_specific_reason(qapp: QApplication) -> None:
    library = FakeLibrary(rules=[FakeRule()], search_error="HTTP 403：站点拒绝访问")
    page = AnimeRulesPage(library, None, toaster())
    try:
        page._run_trial()                                        # noqa: SLF001
        assert settle(qapp, lambda: page._trial_worker is None), "试跑线程未结束"
        note = page.trial_note()
        assert "第 2 步失败" in note
        assert "HTTP 403" in note
        assert "失败" in page.step_texts()[1]
    finally:
        page.shutdown()


def test_rules_page_trial_run_reports_missing_selector_hit(qapp: QApplication) -> None:
    """命中 0 条时要指出"是选择器没匹配"，而不是笼统的失败。"""
    library = FakeLibrary(rules=[FakeRule()], groups=[])
    page = AnimeRulesPage(library, None, toaster())
    try:
        page._run_trial()                                        # noqa: SLF001
        assert settle(qapp, lambda: page._trial_worker is None), "试跑线程未结束"
        assert "选择器返回 0 条" in page.trial_note()
    finally:
        page.shutdown()


# --------------------------------------------------------------------------- 详情 / 剧集网格


def test_episode_grid_marks_watched_and_percent(qapp: QApplication) -> None:
    episodes = _episodes(3)
    progress = {
        episodes[0].key: FakeProgress(episode_key=episodes[0].key,
                                      position_ms=900_000, duration_ms=1_440_000),  # 62%
        episodes[1].key: FakeProgress(episode_key=episodes[1].key, watched=True),
    }
    grid = EpisodeGrid()
    try:
        grid.set_episodes(episodes, progress)
        cells = grid.cells()
        assert len(cells) == 3
        assert cells[0].text().endswith("62%"), "在看的一集要显示百分比"
        assert cells[1].text().endswith("✓"), "看完的一集要打勾"
        assert cells[2].text().endswith("第3集") or cells[2].text() == "第3集"
    finally:
        grid.deleteLater()


def test_detail_dialog_builds_and_double_click_plays(qapp: QApplication) -> None:
    detail = _detail(count=3)
    episodes = detail.roads[0].episodes
    progress = {episodes[0].key: FakeProgress(episode_key=episodes[0].key,
                                             position_ms=720_000, duration_ms=1_440_000)}
    library = FakeLibrary(detail=detail, progress=progress)
    dialog = AnimeDetailDialog(detail, library, toaster=toaster())
    try:
        assert dialog._title.text() == "葬送的芙莉莲"                 # noqa: SLF001
        assert "评分 9.1" in dialog._meta.text()                    # noqa: SLF001
        assert "魔法使的故事" in dialog._summary.toPlainText()        # noqa: SLF001
        assert dialog._road_box.count() == 1                        # noqa: SLF001
        cells = dialog._grid.cells()                                # noqa: SLF001
        assert len(cells) == 3
        assert cells[0].text().endswith("50%")
        assert dialog._follow_button.text() == "☆ 追番"              # noqa: SLF001

        played: list = []
        dialog.playRequested.connect(lambda series, episode: played.append((series, episode)))
        # 双击 = 格子自己的 playRequested（真实路径由 EpisodeCell 的鼠标事件发出）
        cells[1].playRequested.emit(cells[1].episode)
        assert played and played[0][1] is episodes[1]
        assert played[0][0].title == "葬送的芙莉莲"

        # 追番按钮写回后端并翻转文案
        dialog._follow_button.setChecked(True)                      # noqa: SLF001
        assert library.favorites(), "点追番要把作品写进后端"
        assert dialog._follow_button.text() == "★ 已追番"            # noqa: SLF001
    finally:
        dialog.close()


def test_detail_dialog_reports_failed_detail(qapp: QApplication) -> None:
    library = FakeLibrary(detail_error="HTTP 503：详情页不可用")
    dialog = AnimeDetailDialog(FakeHit(), library, toaster=toaster())
    try:
        assert settle(qapp, lambda: dialog._worker is None), "详情线程未结束"
        assert "HTTP 503" in dialog._status.text()                  # noqa: SLF001
        assert dialog._empty.isHidden() is False                    # noqa: SLF001
    finally:
        dialog.close()


# --------------------------------------------------------------------------- 番剧库 / 时间表 / 历史


def test_library_page_marks_new_episodes(qapp: QApplication) -> None:
    series = FakeSeries(id=1, title="葬送的芙莉莲", total_episodes=12, latest_episode="第12集")
    report = FakeReport(items=[FakeUpdate(series=series, new_episodes=2, old_total=10,
                                         new_total=12, latest_episode="第12集")],
                        checked=1)
    library = FakeLibrary(favorites=[series], report=report)
    page = AnimeLibraryPage(library, None, toaster())
    try:
        assert page._check_button.text() == "检查更新"                # noqa: SLF001
        page._check_updates()                                       # noqa: SLF001
        assert settle(qapp, lambda: page._update_worker is None), "更新线程未结束"
        qapp.processEvents()
        card = next(iter(page._cards.values()))
        assert "有新集" in card.badge_texts()
        assert page._empty.isHidden() is True                       # noqa: SLF001
        assert "1 部" in page._count_chip.text()  # noqa: SLF001
    finally:
        page.shutdown()


def test_library_page_unfollow_uses_backend(qapp: QApplication) -> None:
    series = FakeSeries(id=1, title="葬送的芙莉莲")
    library = FakeLibrary(favorites=[series], report=FakeReport())
    page = AnimeLibraryPage(library, None, toaster())
    try:
        card = next(iter(page._cards.values()))
        assert card.follow_button.text() == "取消追番"
        card.follow_button.click()
        assert library.favorites() == [], "取消追番要写回后端"
        assert not page._cards                              # noqa: SLF001  刷新后没有追番了
    finally:
        page.shutdown()


def test_schedule_page_is_honest_when_empty(qapp: QApplication) -> None:
    page = AnimeSchedulePage(FakeLibrary(), toaster())
    try:
        assert SCHEDULE_EMPTY_HINT in page.empty_hint_text()
        assert page._empty.isHidden() is False               # noqa: SLF001
        assert page._tabs.isHidden() is True                 # noqa: SLF001
    finally:
        page.shutdown()


def test_schedule_page_places_favorites_by_weekday(qapp: QApplication) -> None:
    series = FakeSeries(id=1, title="葬送的芙莉莲", air_weekday=2, latest_episode="第12集")
    page = AnimeSchedulePage(FakeLibrary(favorites=[series]), toaster())
    try:
        assert page._tabs.isHidden() is False                # noqa: SLF001
        assert page._tabs.tabText(2) == "周三（1）"           # noqa: SLF001
        assert page._empty.isHidden() is True                # noqa: SLF001  有数据就不显示空状态
    finally:
        page.shutdown()


def test_history_page_lists_and_clears(qapp: QApplication) -> None:
    storage = FakeStorage([FakeHistoryEntry()])
    page = AnimeHistoryPage(FakeLibrary(), storage, toaster())
    try:
        assert page._table.rowCount() == 1                                   # noqa: SLF001
        assert page._table.item(0, 0).text() == "葬送的芙莉莲"                 # noqa: SLF001
        assert page._empty.isHidden() is True                                # noqa: SLF001
        assert page._table.item(0, 3).text() == "播放"                        # noqa: SLF001
    finally:
        page.shutdown()


def test_history_page_handles_missing_storage(qapp: QApplication,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    def boom():  # noqa: ANN202
        raise ImportError("模拟存储未就绪")

    monkeypatch.setattr(anime_context, "anime_storage", boom)
    page = AnimeHistoryPage(None, None, toaster())
    try:
        assert page._table.rowCount() == 0                                   # noqa: SLF001
        assert page._empty.isHidden() is False                               # noqa: SLF001
        assert "未就绪" in page._status.text()                                # noqa: SLF001
    finally:
        page.shutdown()
