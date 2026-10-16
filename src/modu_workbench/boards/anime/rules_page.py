"""墨软动漫 · 规则页：规则列表 / 启停 / 导入导出 / 订阅 / **试跑面板**。

规则是第七板块的核心资产，也是最容易"悄悄失效"的部分（站点改版就废），
所以这一页的重点不是"列个表"，而是**让用户能自己判断哪一环断了**：

- 列表给出版本、模式（XPath/API）、启用、最近试跑结果、来源（内置/用户/订阅），
  以及三类**诚实边界提示**：`api` 高于客户端 → 灰显「该规则需要更新的客户端」；
  `deprecated` → 「已退役」；`useWebview` → 「依赖网页渲染，可能解析失败」。
- 试跑面板按五步走：**搜索 URL → 命中 N 条 → 第一条作品名/链接 →
  详情命中线路数/集数 → 第一集解析结果（含嗅探命中策略）**；
  任一步失败都给出**具体原因**（选择器 0 条 / HTTP 403 / 没有媒体地址…），
  而不是一句"失败了"。

所有后端调用都包了 `try/except`：`core/anime` 由另一个模块并行开发，
缺实现时这一页必须还能打开（显示空列表 + 可读原因），不能整块崩掉。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.ui_kit.components import (
    ColumnPage,
    EmptyState,
    SectionCard,
    TaskBar,
    chip,
    ghost_button,
    hint_label,
    primary_button,
    row,
)
from modu_workbench.ui_kit.toast import Toaster
from modu_workbench.ui_kit.tokens import LIGHT

from .widgets import (
    episode_label,
    first_episode,
    rule_label,
    rule_mode,
    rule_search_url,
    rule_source_label,
    rule_warning,
    series_of,
)

COLUMNS = ["规则", "版本", "模式", "启用", "最近试跑", "来源", "提示"]

#: 试跑的五步标题（面板固定渲染这五行，测试逐行断言）
TRIAL_STEPS = ("搜索 URL", "命中条目", "第一条作品", "详情线路", "第一集解析")

#: 五步失败时的兜底原因（具体原因由后端异常文本给出，这里只在"没数据"时用）
NO_MEDIA_REASON = "解析不到媒体地址（嗅探未命中：站点可能改成 JS 动态加载，或播放页结构已变）"


class RuleSearchWorker(QThread):
    """试跑：逐步跑"搜索 → 详情 → 解析"，每一步都回报给面板。"""

    stepDone = Signal(int, str)     # (第几步 1..5, 文字)
    failed = Signal(int, str)       # (第几步, 具体原因)
    finishedAll = Signal()

    def __init__(self, rule, keyword: str, library, parent: QWidget | None = None):  # noqa: ANN001
        super().__init__(parent)
        self._rule = rule
        self._keyword = keyword
        self._library = library

    # -- 内部小工具 --------------------------------------------------------

    def _fail(self, step: int, reason: str) -> None:
        self.failed.emit(step, reason)

    def run(self) -> None:  # noqa: D102
        rule = self._rule
        library = self._library
        if library is None:
            self._fail(1, "动漫后端未就绪：核心模块还没装上，无法试跑")
            return

        # ---- 1) 搜索 URL ----
        url = rule_search_url(rule, self._keyword)
        self.stepDone.emit(1, f"{TRIAL_STEPS[0]}：{url or '（规则没写 searchURL）'}")
        if not url:
            self._fail(1, "规则缺少 searchURL：XPath 模式必须有搜索地址，否则无从下手")
            return

        # ---- 2) 命中条目 ----
        try:
            groups = library.search(
                self._keyword, rule_keys=[str(getattr(rule, "key", "") or "")], limit=5)
        except Exception as error:  # noqa: BLE001  后端异常就是"具体原因"
            self._fail(2, str(error) or error.__class__.__name__)
            return
        groups = [group for group in (groups or []) if group is not None]
        self.stepDone.emit(2, f"{TRIAL_STEPS[1]}：命中 {len(groups)} 条")
        if not groups:
            self._fail(2, "选择器返回 0 条：搜索 URL 能打开发但没匹配到作品"
                          "（检查 searchList / searchName / searchResult）")
            return

        # ---- 3) 第一条作品 ----
        group = groups[0]
        hits = list(getattr(group, "sources", None) or [])
        title = str(getattr(group, "title", "") or "（规则没给作品名）")
        link = str(getattr(hits[0], "url", "") if hits else getattr(group, "cover", "") or "")
        self.stepDone.emit(3, f"{TRIAL_STEPS[2]}：{title}" + (f" → {link}" if link else ""))
        if not hits:
            self._fail(3, "搜索命中里没有可用的作品链接（检查 searchResult 选择器）")
            return

        # ---- 4) 详情：线路数/集数 ----
        try:
            detail = library.detail(hits[0])
        except Exception as error:  # noqa: BLE001
            self._fail(4, str(error) or error.__class__.__name__)
            return
        roads = list(getattr(detail, "roads", None) or [])
        episodes_total = int(getattr(detail, "episode_count", 0) or 0)
        self.stepDone.emit(4, f"{TRIAL_STEPS[3]}：{len(roads)} 条线路 / {episodes_total} 集")
        if not roads or episodes_total == 0:
            self._fail(4, "详情页没解析出线路或剧集（检查 chapterRoads / chapterResult）")
            return

        # ---- 5) 第一集解析结果（含嗅探命中策略）----
        episode = first_episode(detail)
        if episode is None:
            self._fail(5, "第一条线路里没有剧集（chapterResult 命中了空列表）")
            return
        series = series_of(detail)
        try:
            resolved = library.resolve(series, episode)
        except Exception as error:  # noqa: BLE001
            self._fail(5, str(error) or error.__class__.__name__)
            return
        media = str(getattr(resolved, "url", "") or "")
        if not media:
            self._fail(5, NO_MEDIA_REASON)
            return
        strategy = str(getattr(resolved, "strategy", "") or "未标注")
        kind = "HLS" if bool(getattr(resolved, "is_hls", False)) else "直链"
        self.stepDone.emit(
            5, f"{TRIAL_STEPS[4]}：{episode_label(episode)} → {media}"
               f"（嗅探策略 {strategy} · {kind}）")
        self.finishedAll.emit()


class OfficialRepoWorker(QThread):
    """从 Kazumi 官方规则仓库拉取全部规则并落地成本地文件。

    为什么必须放在线程里：实测本机可用源是 GitHub API，拉全部 17 条要 **28 秒**；
    放 UI 线程就是一次 28 秒的假死。
    """

    progressed = Signal(int, int, str)   # (已完成, 总数, 当前规则名)
    finishedImport = Signal(object)      # ImportResult
    failed = Signal(str)

    def __init__(self, manager, parent: QWidget | None = None):  # noqa: ANN001
        super().__init__(parent)
        self._manager = manager

    def run(self) -> None:  # noqa: D102
        try:
            from modu_workbench.core.anime.rule_repo import import_from_repo

            result = import_from_repo(
                self._manager,
                on_progress=lambda done, total, name: self.progressed.emit(done, total, name))
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error) or error.__class__.__name__)
            return
        self.finishedImport.emit(result)


class RuleUrlWorker(QThread):
    """从 URL 导入规则：先下载文本，再交给后端导入（下载必须离开 UI 线程）。"""

    finishedImport = Signal(str)
    failed = Signal(str)

    def __init__(self, url: str, library, rules_manager=None,  # noqa: ANN001
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._url = url
        self._library = library
        self._rules = rules_manager

    def run(self) -> None:  # noqa: D102
        try:
            from urllib.request import Request, urlopen

            request = Request(self._url, headers={"User-Agent": "Mozilla/5.0 (ModuWorkbench)"})
            with urlopen(request, timeout=20) as response:  # noqa: S310  用户显式给的地址
                text = response.read().decode("utf-8", errors="replace")
        except Exception as error:  # noqa: BLE001
            self.failed.emit(f"下载失败：{error}")
            return
        try:
            saved = self._import(text)
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error) or error.__class__.__name__)
            return
        self.finishedImport.emit(f"已从 URL 导入 {saved} 条规则")

    def _import(self, text: str) -> int:
        if self._library is not None:
            found = self._library.import_rules(text)
            return len(found or [])
        if self._rules is not None:
            found = self._rules.import_text(text)
            return len(found or [])
        raise RuntimeError("动漫后端未就绪：无法导入规则")


class SubscriptionDialog(QDialog):
    """订阅管理：列出订阅、更新（重新拉取）、删除。"""

    def __init__(self, library, parent: QWidget | None = None,  # noqa: ANN001
                 *, toaster: Toaster | None = None):
        super().__init__(parent)
        self.setWindowTitle("订阅管理")
        self.setMinimumWidth(420)
        self._library = library
        self._toaster = toaster or Toaster(self)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("订阅是可按需更新的规则集合（远程清单 + 校验和）。"))
        self._list = QListWidget()
        layout.addWidget(self._list, 1)
        buttons = QHBoxLayout()
        update = QPushButton("更新所选")
        update.clicked.connect(self._update_selected)
        remove = QPushButton("删除所选")
        remove.setObjectName("dangerButton")
        remove.clicked.connect(self._remove_selected)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        buttons.addWidget(update)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.reload()

    def reload(self) -> None:
        self._list.clear()
        entries = []
        if self._library is not None:
            try:
                entries = list(self._library.subscriptions() or [])
            except Exception as error:  # noqa: BLE001
                self._toaster.error(f"读取订阅失败：{error}")
        for entry in entries:
            name = str(getattr(entry, "get", lambda *_: "")("name") or entry)
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, name)
            self._list.addItem(item)
        if not entries:
            self._list.addItem(QListWidgetItem("（还没有订阅）"))

    def _selected_name(self) -> str:
        item = self._list.currentItem()
        if item is None:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    def _update_selected(self) -> None:
        name = self._selected_name()
        if not name or self._library is None:
            self._toaster.info("请先选择一条订阅")
            return
        try:
            count, message = self._library.update_subscription(name)
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"更新失败：{error}")
            return
        self._toaster.success(f"订阅「{name}」更新了 {count} 条：{message}")
        self.reload()

    def _remove_selected(self) -> None:
        name = self._selected_name()
        if not name or self._library is None:
            self._toaster.info("请先选择一条订阅")
            return
        try:
            self._library.remove_subscription(name)
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"删除失败：{error}")
            return
        self._toaster.success(f"已删除订阅「{name}」")
        self.reload()


class AnimeRulesPage(QWidget):
    """规则管理页：列表 + 启停 + 导入导出 + 试跑。"""

    rulesChanged = Signal()

    def __init__(self, library=None, rules_manager=None, toaster: Toaster | None = None,  # noqa: ANN001
                 parent: QWidget | None = None, *, task_bar: TaskBar | None = None):
        super().__init__(parent)
        self._library = library
        self._rules_manager = rules_manager
        self._toaster = toaster or Toaster(self)
        self._task = task_bar
        self._rules: list = []
        self._trial_worker: RuleSearchWorker | None = None
        self._url_worker: RuleUrlWorker | None = None
        self._repo_worker: OfficialRepoWorker | None = None
        self._repo_reason = ""
        self._tail_stretch = False
        self._fallback_status = QLabel("就绪。")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._page = ColumnPage(
            "规则",
            "规则决定「从哪些站点、用什么选择器取番剧」。内置规则随包提供；"
            "自己写的规则放在数据目录的 anime/rules 下，可导入导出、可订阅更新。"
            "站点改版后规则会失效 —— 用下面的「试跑」按步骤定位是哪一环断了。",
        )
        outer.addWidget(self._page)

        # ---- 列表 ----
        self._count_chip = chip("0 条")
        # 「导入官方规则库」：用户反馈"动漫还是没有规则"—— 空着手进来没法用。
        # 一键从上游 KazumiRules 拉取并落地成本地文件（不把别人的规则打进安装包，
        # 见 core/anime/rule_repo.py 的模块说明）。
        self._repo_button = primary_button("⤓ 导入官方规则库")
        self._repo_button.setToolTip(
            "从 Kazumi 官方规则仓库（Predidit/KazumiRules）拉取全部规则并存成本地文件。\n"
            "注意：这批规则多数标注 useWebview，能搜到番、能看到剧集列表，\n"
            "但播放地址依赖网页脚本 —— 取不到时请用「用外部播放器打开」。")
        self._repo_button.clicked.connect(self._import_official_repo)
        import_file = ghost_button("导入文件")
        import_file.clicked.connect(self._import_file)
        import_clip = ghost_button("导入剪贴板")
        import_clip.clicked.connect(self._import_clipboard)
        import_url = ghost_button("导入 URL")
        import_url.clicked.connect(self._import_url)
        export = ghost_button("导出")
        export.clicked.connect(self._export)
        edit = ghost_button("编辑")
        edit.setToolTip("用系统默认编辑器打开用户规则文件（内置/订阅规则不可改）")
        edit.clicked.connect(self._edit_rule)
        delete = QPushButton("删除")
        delete.setObjectName("dangerButton")
        delete.clicked.connect(self._delete_rule)
        subscribe = ghost_button("订阅管理")
        subscribe.clicked.connect(self._manage_subscriptions)
        reload_button = ghost_button("重新扫描")
        reload_button.clicked.connect(self.reload)

        self._list_card = SectionCard(
            "规则列表", actions=[self._count_chip],
            hint="灰显＝需要更新的客户端；「已退役」不再参与搜索")
        self._list_card.add(row(self._repo_button, import_file, import_clip, import_url,
                                export, edit, delete, subscribe, reload_button,
                                stretch_last=True))

        self._table = QTableWidget()
        self._table.setColumnCount(len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.itemChanged.connect(self._on_item_changed)
        self._empty = EmptyState(
            "🧩", "还没有规则",
            "点「导入文件」或「导入剪贴板」加入一条规则 JSON；内置规则随包提供，可直接启用")
        self._list_card.add(self._empty)
        self._list_card.add(self._table)
        self._table.setVisible(False)
        self._page.add(self._list_card)

        # ---- 试跑 ----
        self._trial_card = SectionCard("试跑", hint="五步定位：搜索 → 命中 → 详情 → 线路/集数 → 解析")
        trial_row = QHBoxLayout()
        trial_row.setContentsMargins(0, 0, 0, 0)
        trial_row.addWidget(QLabel("规则"))
        self._trial_rule = QComboBox()
        self._trial_rule.setMinimumWidth(200)
        trial_row.addWidget(self._trial_rule)
        trial_row.addWidget(QLabel("关键词"))
        self._trial_keyword = QLineEdit("葬送的芙莉莲")
        self._trial_keyword.setObjectName("searchBox")
        trial_row.addWidget(self._trial_keyword, 1)
        run = primary_button("开始试跑")
        run.clicked.connect(self._run_trial)
        trial_row.addWidget(run)
        self._trial_card.add_layout(trial_row)

        self._step_labels: list[QLabel] = []
        for index, title in enumerate(TRIAL_STEPS, start=1):
            label = QLabel(f"{index}) {title}：还没试跑")
            label.setObjectName("readerStatus")
            label.setWordWrap(True)
            self._trial_card.add(label)
            self._step_labels.append(label)
        self._trial_note = QLabel("")
        self._trial_note.setObjectName("readerStatus")
        self._trial_note.setWordWrap(True)
        self._trial_card.add(self._trial_note)
        self._trial_card.add(hint_label(
            "试跑会真实联网访问规则里的站点。解析到媒体地址即成功；"
            "失败时上面会写明是搜索、详情还是嗅探哪一步断了。"))
        self._page.add(self._trial_card)
        self._set_tail_stretch(True)
        self.reload()

    # ------------------------------------------------------------------ 状态

    @property
    def _status(self) -> QLabel:
        """兼容旧引用：状态文字统一显示在板块任务条上。"""
        return self._task._label if self._task is not None else self._fallback_status  # noqa: SLF001

    def _note_fallback(self, message: str) -> None:
        label = getattr(self, "_fallback_status", None)
        if label is not None:
            label.setText(message)

    def _report_status(self, message: str, done: int = 0, total: int = 0) -> None:
        self._note_fallback(message)
        if self._task is not None:
            if done or total:
                self._task.report(message, done, total)
            else:
                self._task.note(message)

    def _idle(self, message: str = "") -> None:
        if message:
            self._note_fallback(message)
        if self._task is not None:
            self._task.idle(message)

    def _set_tail_stretch(self, enabled: bool) -> None:
        layout = self._page.body
        if enabled and not self._tail_stretch:
            layout.addStretch(1)
            self._tail_stretch = True
        elif not enabled and self._tail_stretch and layout.count():
            layout.takeAt(layout.count() - 1)
            self._tail_stretch = False

    # ------------------------------------------------------------------ 官方规则库

    def _import_official_repo(self) -> None:
        """手动触发：从 Kazumi 官方规则仓库拉取全部规则。"""
        self._start_official_import(reason="手动")

    def _start_official_import(self, *, reason: str) -> None:
        if self._repo_worker is not None and self._repo_worker.isRunning():
            self._toaster.info("官方规则库正在导入中，请稍候…")
            return
        manager = self._rules_manager
        if manager is None:
            try:
                from modu_workbench.core.anime.rules import RuleManager

                manager = RuleManager()
            except Exception as error:  # noqa: BLE001
                self._toaster.error(f"动漫后端未就绪，无法导入规则库：{error}")
                return
        self._repo_button.setEnabled(False)
        self._report_status("正在从 Kazumi 官方规则仓库拉取规则…（首次可能需要 20–30 秒）", 0, 1)
        worker = OfficialRepoWorker(manager, self)
        worker.progressed.connect(self._on_repo_progress)
        worker.finishedImport.connect(self._on_repo_finished)
        worker.failed.connect(self._on_repo_failed)
        worker.finished.connect(self._on_repo_thread_finished)
        self._repo_worker = worker
        self._repo_reason = reason
        worker.start()

    def _on_repo_progress(self, done: int, total: int, name: str) -> None:
        self._report_status(f"正在导入官方规则：{done}/{total}（{name}）…", done, total)

    def _on_repo_finished(self, result) -> None:  # noqa: ANN001
        self.reload()
        if not getattr(result, "ok", False):
            self._report_status("官方规则库导入失败：没有取到任何规则")
            self._toaster.error("官方规则库导入失败：没有取到任何规则")
            return
        message = f"官方规则库导入完成：{result.summary()}"
        self._report_status(message)
        # 全部是最新（没真下载）时别说"成功导入 N 条"——那是误导
        if getattr(result, "skipped", None) and not getattr(result, "installed", None):
            self._toaster.info(f"官方规则库已是最新（{len(result.skipped)} 条），无需重新下载")
        elif getattr(result, "failed", None):
            names = "、".join(name for name, _reason in result.failed[:3])
            self._toaster.info(f"{message}；失败：{names} 等")
        else:
            self._toaster.success(message)
        for warning in getattr(result, "warnings", []) or []:
            self._toaster.info(warning)

    def _on_repo_failed(self, message: str) -> None:
        self._report_status(f"官方规则库导入失败：{message}")
        self._toaster.error(f"官方规则库导入失败：{message}")

    def _on_repo_thread_finished(self) -> None:
        self._repo_worker = None
        self._repo_button.setEnabled(True)

    def is_importing_repo(self) -> bool:
        return self._repo_worker is not None and self._repo_worker.isRunning()

    # ------------------------------------------------------------------ 规则列表

    def maybe_auto_import_official(self) -> bool:
        """首次进入且**一条规则都没有**时，自动导入官方规则库（用户诉求：无需手动操作）。

        只在"真的一条都没有"时触发一次，并且用**标记文件**记住已经试过：

        - 有内置示例规则也算"有规则"，但那是模板、搜不到东西，所以这里看的是
          "用户规则 + 订阅规则是否为空"（内置示例不算数）；
        - 试过就写标记（成功或失败都写）—— 否则用户手动删光规则后每次进页面都会
          重新联网导入，那是骚扰；
        - 失败也不弹错误框，只把原因写进状态栏（首次自动行为不该用弹窗打断）。

        返回是否真的发起了导入。
        """
        if self._library is None or self._repo_worker is not None:
            return False
        try:
            manager = self._rules_manager
            if manager is None:
                from modu_workbench.core.anime.rules import RuleManager

                manager = RuleManager()
                self._rules_manager = manager
            if self._auto_marker().exists():
                return False
            # 只看"用户自己的规则 + 订阅规则"：内置的两条示例是模板
            # （baseURL 指向 example.com），有它们也等于没有可用的源。
            own = [rule for rule in manager.discover()
                   if not bool(getattr(rule, "builtin", False))]
            if own or list(manager.subscription_dir.glob("*/*.json")):
                return False
            self._write_auto_marker()
        except Exception:  # noqa: BLE001  自动行为绝不能因为探测失败而报错
            return False
        self._start_official_import(reason="首次自动")
        return True

    def _auto_marker(self):
        from pathlib import Path

        manager = self._rules_manager
        base = getattr(manager, "user_dir", None) or Path(".")
        return Path(base) / "official-import.attempted"

    def _write_auto_marker(self) -> None:
        try:
            self._auto_marker().write_text("1", encoding="utf-8")
        except OSError:
            pass

    def reload(self) -> None:
        rules: list = []
        reason = ""
        if self._library is None:
            reason = "动漫后端未就绪：核心模块还没装上，规则列表暂时为空"
        else:
            try:
                # `refresh=True` 必须给：导入/删除/订阅安装之后磁盘变了，
                # 而 `rules()` 默认可能直接返回旧缓存 —— 那正是"提示导入成功
                # 但列表还是空"的原因（要重启才能看到）。
                # 老替身库没有这个关键字参数，退化为不带参数调用（不因此崩页面）。
                try:
                    rules = list(self._library.rules(enabled_only=False, refresh=True) or [])
                except TypeError:
                    rules = list(self._library.rules(enabled_only=False) or [])
                # 扫描阶段失败的坏规则也要让用户看见原因，而不是静默少几条
                errors = list(getattr(self._library, "rule_errors", []) or [])
                if errors:
                    reason = "有规则没加载成功：" + "；".join(str(item) for item in errors[:2])
            except Exception as error:  # noqa: BLE001
                reason = f"读取规则失败：{error}"
        self._rules = rules
        self._fill_table(rules)
        self._fill_trial_combo(rules)
        self._count_chip.setText(f"{len(rules)} 条")
        self._empty.setVisible(not rules)
        self._table.setVisible(bool(rules))
        self._set_tail_stretch(not rules)
        if reason:
            self._report_status(reason)
        elif rules:
            self._report_status(f"已加载 {len(rules)} 条规则")

    def _fill_table(self, rules: list) -> None:
        self._table.blockSignals(True)
        self._table.setRowCount(len(rules))
        for index, rule in enumerate(rules):
            warning = rule_warning(rule)
            values = [
                rule_label(rule),
                str(getattr(rule, "version", "") or "-"),
                rule_mode(rule).upper(),
                "",
                self._last_test_text(rule),
                rule_source_label(rule),
                warning or "-",
            ]
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, str(getattr(rule, "key", "") or ""))
                    item.setToolTip(str(getattr(rule, "path", "") or "") or rule_label(rule))
                if column == 3:
                    item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                    item.setCheckState(Qt.CheckState.Checked if self._is_enabled(rule)
                                       else Qt.CheckState.Unchecked)
                if warning and column in (0, 6):
                    # 灰显：用了设计令牌里的"最浅文字色"，不新造颜色
                    item.setForeground(QBrush(QColor(LIGHT.text_faint)))
                self._table.setItem(index, column, item)
        self._table.blockSignals(False)
        self._table.resizeColumnsToContents()

    @staticmethod
    def _last_test_text(rule) -> str:  # noqa: ANN001
        text = str(getattr(rule, "last_test", "") or getattr(rule, "test_result", "") or "")
        return text or "未试跑"

    @staticmethod
    def _is_enabled(rule) -> bool:  # noqa: ANN001
        value = getattr(rule, "enabled", True)
        if isinstance(value, bool):
            return value
        return str(value).lower() not in ("false", "0", "no", "")

    def _fill_trial_combo(self, rules: list) -> None:
        current = self._trial_rule.currentData()
        self._trial_rule.blockSignals(True)
        self._trial_rule.clear()
        for rule in rules:
            note = rule_warning(rule)
            text = rule_label(rule) + (f"（{note}）" if note else "")
            self._trial_rule.addItem(text, str(getattr(rule, "key", "") or ""))
        position = self._trial_rule.findData(current)
        self._trial_rule.setCurrentIndex(position if position >= 0 else 0)
        self._trial_rule.blockSignals(False)

    def rule_at(self, row: int):  # noqa: ANN201
        if 0 <= row < len(self._rules):
            return self._rules[row]
        return None

    def current_rule(self):  # noqa: ANN201
        row = self._table.currentRow()
        rule = self.rule_at(row)
        if rule is not None:
            return rule
        key = self._trial_rule.currentData()
        for item in self._rules:
            if str(getattr(item, "key", "")) == str(key):
                return item
        return self._rules[0] if self._rules else None

    # ------------------------------------------------------------------ 启停

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 3:
            return
        rule = self.rule_at(item.row())
        if rule is None or self._library is None:
            return
        enabled = item.checkState() == Qt.CheckState.Checked
        try:
            self._library.set_rule_enabled(str(getattr(rule, "key", "") or ""), enabled)
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"切换启用状态失败：{error}")
            return
        self._report_status(f"规则「{rule_label(rule)}」已{'启用' if enabled else '停用'}")
        self.rulesChanged.emit()

    # ------------------------------------------------------------------ 导入导出

    def _import_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "导入规则文件", "", "规则 JSON (*.json)")
        if not path:
            return
        saved: list = []
        try:
            if self._library is not None:
                saved = list(self._library.import_rules(path) or [])
            elif self._rules_manager is not None:
                saved = list(self._rules_manager.import_file(path) or [])
            else:
                raise RuntimeError("动漫后端未就绪：无法导入规则")
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"导入失败：{error}")
            self._note_fallback(f"导入失败：{error}")
            return
        self._toaster.success(f"已导入 {len(saved)} 条规则")
        self.reload()
        self.rulesChanged.emit()

    def _import_clipboard(self) -> None:
        clipboard = QApplication.clipboard()
        text = clipboard.text() if clipboard is not None else ""
        if not (text or "").strip():
            self._toaster.info("剪贴板里没有内容")
            return
        try:
            if self._library is not None:
                saved = list(self._library.import_rules(text) or [])
            elif self._rules_manager is not None:
                saved = list(self._rules_manager.import_text(text) or [])
            else:
                raise RuntimeError("动漫后端未就绪：无法导入规则")
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"导入失败：{error}")
            return
        self._toaster.success(f"已从剪贴板导入 {len(saved)} 条规则")
        self.reload()
        self.rulesChanged.emit()

    def _import_url(self) -> None:
        url, ok = QInputDialog.getText(self, "从 URL 导入规则", "规则 JSON 的地址：")
        if not ok or not (url or "").strip():
            return
        if self._url_worker is not None and self._url_worker.isRunning():
            self._toaster.info("上一次导入还在进行中")
            return
        self._report_status(f"正在下载规则：{url.strip()}…")
        worker = RuleUrlWorker(url.strip(), self._library, self._rules_manager, self)
        worker.finishedImport.connect(self._on_url_imported)
        worker.failed.connect(self._on_url_failed)
        worker.finished.connect(lambda w=worker: self._forget_url_worker(w))
        self._url_worker = worker
        worker.start()

    def _forget_url_worker(self, worker: RuleUrlWorker) -> None:
        if self._url_worker is worker:
            self._url_worker = None

    def _on_url_imported(self, message: str) -> None:
        self._toaster.success(message)
        self._idle(message)
        self.reload()
        self.rulesChanged.emit()

    def _on_url_failed(self, message: str) -> None:
        self._toaster.error(message)
        self._idle(message)

    def _export(self) -> None:
        rule = self.current_rule()
        if rule is None:
            self._toaster.info("请先选中一条规则")
            return
        path, _filter = QFileDialog.getSaveFileName(
            self, "导出规则", f"{rule_label(rule)}.json", "规则 JSON (*.json)")
        if not path:
            return
        try:
            if self._rules_manager is not None:
                self._rules_manager.export_rule(rule, path)
            elif self._library is not None:
                self._library.export_rules([rule], path)
            else:
                raise RuntimeError("动漫后端未就绪：无法导出规则")
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"导出失败：{error}")
            return
        self._toaster.success(f"已导出：{path}")
        self._report_status(f"已导出规则到 {path}")

    def _edit_rule(self) -> None:
        rule = self.current_rule()
        if rule is None:
            self._toaster.info("请先选中一条规则")
            return
        if bool(getattr(rule, "builtin", False)) or str(getattr(rule, "key", "")).startswith("sub:"):
            self._toaster.info("内置/订阅规则不可直接改：先「导出」，改完再「导入文件」")
            return
        path = str(getattr(rule, "path", "") or "")
        if not path or not Path(path).is_file():
            self._toaster.error("规则文件不存在（可能已被移动或删除）")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _delete_rule(self) -> None:
        rule = self.current_rule()
        if rule is None:
            self._toaster.info("请先选中一条规则")
            return
        if self._rules_manager is None:
            self._toaster.error("动漫后端未就绪：无法删除规则")
            return
        try:
            removed = bool(self._rules_manager.delete_rule(rule))
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"删除失败：{error}")
            return
        if not removed:
            self._toaster.info("内置/订阅规则不会被删除（删了下次扫描还会回来）")
            return
        self._toaster.success(f"已删除规则「{rule_label(rule)}」")
        self.reload()
        self.rulesChanged.emit()

    def _manage_subscriptions(self) -> None:
        dialog = SubscriptionDialog(self._library, self, toaster=self._toaster)
        dialog.exec()
        self.reload()
        self.rulesChanged.emit()

    # ------------------------------------------------------------------ 试跑

    def step_texts(self) -> list[str]:
        """五步面板的当前文字（测试用它逐行断言）。"""
        return [label.text() for label in self._step_labels]

    def trial_note(self) -> str:
        return self._trial_note.text()

    def _run_trial(self) -> None:
        rule = self.current_rule()
        if rule is None:
            self._toaster.info("没有可试跑的规则：先导入或启用一条")
            return
        if self._trial_worker is not None and self._trial_worker.isRunning():
            self._toaster.info("上一次试跑还没结束")
            return
        keyword = self._trial_keyword.text().strip()
        if not keyword:
            self._toaster.error("请输入试跑关键词")
            return
        for index, title in enumerate(TRIAL_STEPS, start=1):
            self._step_labels[index - 1].setText(f"{index}) {title}：…")
        self._trial_note.setText("")
        self._report_status(f"正在试跑规则「{rule_label(rule)}」：{keyword}")
        worker = RuleSearchWorker(rule, keyword, self._library, self)
        worker.stepDone.connect(self._on_trial_step)
        worker.failed.connect(self._on_trial_failed)
        worker.finishedAll.connect(lambda: self._idle("试跑成功：解析到可播放的媒体地址"))
        worker.finished.connect(lambda w=worker: self._forget_trial_worker(w))
        self._trial_worker = worker
        worker.start()

    def _forget_trial_worker(self, worker: RuleSearchWorker) -> None:
        if self._trial_worker is worker:
            self._trial_worker = None

    def _on_trial_step(self, step: int, text: str) -> None:
        if 1 <= step <= len(self._step_labels):
            self._step_labels[step - 1].setText(f"{step}) {text}")

    def _on_trial_failed(self, step: int, reason: str) -> None:
        title = TRIAL_STEPS[step - 1] if 1 <= step <= len(TRIAL_STEPS) else "试跑"
        if 1 <= step <= len(self._step_labels):
            self._step_labels[step - 1].setText(f"{step}) {title}：失败")
        self._trial_note.setText(f"第 {step} 步失败（{title}）：{reason}")
        self._report_status(f"试跑失败：{reason}")
        self._toaster.error(f"试跑失败：{reason}")

    def on_shown(self) -> None:
        self.reload()

    def shutdown(self) -> None:
        for worker in (self._trial_worker, self._url_worker):
            if worker is not None and worker.isRunning():
                worker.wait(3000)
        self._trial_worker = None
        self._url_worker = None


__all__ = [
    "AnimeRulesPage",
    "COLUMNS",
    "NO_MEDIA_REASON",
    "RuleSearchWorker",
    "RuleUrlWorker",
    "SubscriptionDialog",
    "TRIAL_STEPS",
]
