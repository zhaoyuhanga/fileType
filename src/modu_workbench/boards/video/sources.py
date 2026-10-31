"""墨软影视 · 源设置：启用/停用、调整优先级、修改采集接口地址、测试连通性、测速排序。

这是「多数据源可切换、后续加源容易」的用户侧入口（诉求第 6 条）：
- 勾选启用哪些源（聚合搜索只走启用的）；
- 上/下移调整优先级（顺序决定搜索先后与去重保留谁）；
- 采集源地址可改（站点换域名时无需等版本更新）；
- 「测试」按钮立即验证某个源当前是否可用，并显示健康度；
- v3-e：**「测速」按钮**测延迟与首片吞吐并写回健康度，可按测速评分排序
  （默认关，见计划 3.3.5 —— 测速结果会波动，排序变化必须由用户明确开启）。
"""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.video import VideoRegistry
from . import context as app_context
from .widgets import video_settings
from modu_workbench.ui_kit.toast import Toaster

# 列：0 启用 / 1 数据源 / 2 能力 / 3 状态 / 4 测速 / 5 接口地址（可改）
COLUMN_ENABLED = 0
COLUMN_SOURCE = 1
COLUMN_KIND = 2
COLUMN_STATUS = 3
COLUMN_SPEED = 4
COLUMN_URL = 5
COLUMNS = ["启用", "数据源", "能力", "状态", "测速", "接口地址（可改）"]

# 每个源的测速摘要（延迟 / 速度 / 评分）会顶宽表格：把细节也放进 tooltip，
# 用户把列拖窄了照样看得到。标签保持短，别用长句（会顶宽整行）。
SPEED_COLUMN_HINT = "延迟（滑动 P50）/ 速度（首个分片估算）/ 评分（选源用，不决定缓冲档位）"


class _ProbeWorker(QThread):
    """测试某个源是否可用（跑一次真实搜索）。"""

    finishedProbe = Signal(str, bool, str)   # key, ok, message

    def __init__(self, registry: VideoRegistry, key: str, keyword: str = "电影", parent=None):
        super().__init__(parent)
        self._registry = registry
        self._key = key
        self._keyword = keyword

    def run(self) -> None:  # noqa: D102
        try:
            videos = self._registry.search_one(self._key, self._keyword, limit=3)
            self.finishedProbe.emit(self._key, True, f"可用（返回 {len(videos)} 条结果）")
        except Exception as error:  # noqa: BLE001
            self.finishedProbe.emit(self._key, False, str(error))


class _ProbeAllWorker(QThread):
    """逐个测试所有可用源（一键判断「是我这边网络的问题，还是某个源失效了」）。"""

    probed = Signal(str, bool, str)          # key, ok, message
    finishedAll = Signal(int, int)           # 可用数, 总数

    def __init__(self, registry: VideoRegistry, keyword: str = "电影", parent=None):
        super().__init__(parent)
        self._registry = registry
        self._keyword = keyword
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:  # noqa: D102
        ok = total = 0
        for provider in self._registry.providers(enabled_only=False):
            if self._cancel:
                break
            if not provider.available():          # 直链/自定义源没配置，跳过不算失败
                continue
            if not provider.supports_keyword_search():   # 直链源不能按关键词测试
                continue
            total += 1
            try:
                videos = self._registry.search_one(provider.key, self._keyword, limit=3)
                ok += 1
                self.probed.emit(provider.key, True, f"可用（返回 {len(videos)} 条结果）")
            except Exception as error:  # noqa: BLE001
                self.probed.emit(provider.key, False, str(error))
        self.finishedAll.emit(ok, total)


class _SpeedWorker(QThread):
    """批量测速（v3-e）：跑在后台，对话框保持可响应、可取消。

    真正的调度在 `VideoRegistry.measure_sources`（并发 3、最多 20 个源、
    单源 3–4 秒超时）；这里只负责"别卡住界面"和把进度/结果转成信号。
    """

    progressed = Signal(object, int, int)     # SpeedResult, done, total
    finishedAll = Signal(object)              # List[SpeedResult]
    failed = Signal(str)

    def __init__(self, registry: VideoRegistry, *, concurrency: int = 3,
                 keyword: str = "电影", parent=None):
        super().__init__(parent)
        self._registry = registry
        self._concurrency = max(1, int(concurrency))
        self._keyword = keyword
        self._cancel = threading.Event()

    def cancel(self) -> None:
        """取消：不再提交新任务；已提交的靠单源超时自然结束。"""
        self._cancel.set()

    def run(self) -> None:  # noqa: D102
        try:
            results = self._registry.measure_sources(
                concurrency=self._concurrency, keyword=self._keyword,
                cancel=self._cancel,
                on_progress=lambda result, done, total: self.progressed.emit(result, done, total),
            )
            self.finishedAll.emit(results)
        except Exception as error:  # noqa: BLE001  测速整体失败也要给用户一个交代
            self.failed.emit(str(error))


class VideoSourceDialog(QDialog):
    """视频源设置对话框。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("墨软影视 · 源设置")
        self.resize(940, 520)
        self._toaster = Toaster(self)
        self._registry = app_context.video_registry()
        self._storage = app_context.video_storage()
        self._probe: _ProbeWorker | None = None
        self._probe_all_worker: _ProbeAllWorker | None = None
        self._speed_worker: _SpeedWorker | None = None
        # 排序开关是设置页与这里共用的一个键：默认从设置读，改了就写回
        self._settings = video_settings()
        self._registry.set_sort_by_speed(
            self._settings.value("video/sort_by_speed", False, type=bool))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        title = QLabel("视频源管理")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        hint = QLabel(
            "搜索与播放按此处的「启用 + 顺序」进行；某个源解析不出地址时会自动改用其他已启用源。"
            "采集类接口地址若失效，可直接在此改成新域名。"
        )
        hint.setObjectName("readerStatus")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._table = QTableWidget()
        self._table.setColumnCount(len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setShowGrid(False)
        header = self._table.horizontalHeader()
        for column in (COLUMN_ENABLED, COLUMN_SOURCE, COLUMN_KIND, COLUMN_STATUS, COLUMN_SPEED):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COLUMN_URL, QHeaderView.ResizeMode.Stretch)
        self._table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._table, 1)

        actions = QHBoxLayout()
        up = QPushButton("↑ 上移")
        up.clicked.connect(lambda: self._move(-1))
        down = QPushButton("↓ 下移")
        down.clicked.connect(lambda: self._move(1))
        probe = QPushButton("测试选中源")
        probe.setObjectName("primaryButton")
        probe.clicked.connect(self._probe_selected)
        probe_all = QPushButton("测试全部")
        probe_all.setToolTip("逐个源跑一次真实搜索：可快速看出是网络问题还是某个源失效")
        probe_all.clicked.connect(self._probe_all)
        detail = QPushButton("编辑接口地址…")
        detail.clicked.connect(self._edit_url)
        reset = QPushButton("恢复默认顺序")
        reset.clicked.connect(self._reset)
        for widget in (up, down, probe, probe_all, detail, reset):
            actions.addWidget(widget)
        actions.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        actions.addWidget(buttons)
        layout.addLayout(actions)

        # ---- 测速（v3-e）：按钮 + 取消 + 排序开关 + 并发 ----
        speed_row = QHBoxLayout()
        self._speed_button = QPushButton("测速")
        self._speed_button.setToolTip(
            "测每个源的延迟（搜索请求 P50）与速度（首个分片 bytes/耗时）；"
            "默认并发 3、最多测 20 个源（顺序最靠前的 5 个 + 其余随机 15 个）"
        )
        self._speed_button.clicked.connect(self._start_speed_test)
        self._cancel_speed_button = QPushButton("取消")
        self._cancel_speed_button.setEnabled(False)
        self._cancel_speed_button.clicked.connect(self._cancel_speed_test)
        self._sort_by_speed = QCheckBox("按测速结果排序")
        self._sort_by_speed.setToolTip(
            "打开后，搜索/换源顺序改为按「评分」降序（默认关闭：测速会波动，排序变化需你显式开启）"
        )
        self._sort_by_speed.setChecked(self._registry.sort_by_speed)
        self._sort_by_speed.toggled.connect(self._on_sort_toggled)
        self._speed_concurrency = QSpinBox()
        self._speed_concurrency.setRange(1, 6)
        self._speed_concurrency.setSuffix(" 并发")
        self._speed_concurrency.setToolTip("测速时同时测几个源；默认 3（与计划里的量级一致）")
        self._speed_concurrency.setValue(
            int(self._settings.value("video/speed_test_concurrency", 3) or 3))
        for widget in (self._speed_button, self._cancel_speed_button,
                       self._sort_by_speed, self._speed_concurrency):
            speed_row.addWidget(widget)
        speed_row.addStretch(1)
        layout.addLayout(speed_row)

        self._status = QLabel("提示：采集类数据源来自第三方站点，可能随时变更或失效。")
        self._status.setObjectName("readerStatus")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._reload()

    # ------------------------------------------------------------------ 表格

    def _reload(self) -> None:
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        for provider in self._ordered_providers():
            info = provider.info
            row = self._table.rowCount()
            self._table.insertRow(row)

            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(
                Qt.CheckState.Checked if self._registry.is_enabled(info.key) else Qt.CheckState.Unchecked
            )
            check.setData(Qt.ItemDataRole.UserRole, info.key)
            self._table.setItem(row, COLUMN_ENABLED, check)

            self._table.setItem(row, COLUMN_SOURCE, QTableWidgetItem(f"{info.label}（{info.key}）"))
            self._table.setItem(row, COLUMN_KIND, QTableWidgetItem(info.kind_label))
            self._table.setItem(row, COLUMN_STATUS, QTableWidgetItem(self._registry.status_text(info.key)))

            speed_item = QTableWidgetItem(self._registry.health(info.key).speed_text())
            speed_item.setToolTip(SPEED_COLUMN_HINT)
            self._table.setItem(row, COLUMN_SPEED, speed_item)

            editable = info.editable
            url_item = QTableWidgetItem(provider.credential or info.homepage or "-")
            if editable:
                url_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                                  | Qt.ItemFlag.ItemIsEditable)
            url_item.setToolTip(info.note)
            self._table.setItem(row, COLUMN_URL, url_item)
        self._table.blockSignals(False)

    def _ordered_providers(self) -> list:
        """表格里的显示顺序：默认用户顺序；勾了「按测速结果排序」就按评分降序。

        **不改注册表里的用户顺序** —— 这里只是"换个看法"，
        否则用户一打开对话框、再点确定，自己排的顺序就被测速结果覆盖了。
        """
        providers = self._registry.providers(enabled_only=False)
        if not self._registry.sort_by_speed:
            return providers
        return sorted(providers, key=lambda provider: self._registry.score_of(provider.key),
                      reverse=True)

    def _key_at(self, row: int) -> str:
        item = self._table.item(row, COLUMN_ENABLED)
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else ""

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == COLUMN_ENABLED:
            key = self._key_at(item.row())
            if key:
                self._registry.set_enabled(key, item.checkState() == Qt.CheckState.Checked)
                self._table.blockSignals(True)
                status = self._table.item(item.row(), COLUMN_STATUS)
                if status is not None:
                    status.setText(self._registry.status_text(key))
                self._table.blockSignals(False)
        elif item.column() == COLUMN_URL:
            key = self._key_at(item.row())
            provider = self._registry.get(key)
            if provider is not None and provider.info.editable:
                provider.set_credential(item.text().strip())

    # ------------------------------------------------------------------ 排序与测速

    def _on_sort_toggled(self, checked: bool) -> None:
        """排序开关：同步到注册表（核心层不读 QSettings，靠这里注入）并刷新表格。"""
        self._registry.set_sort_by_speed(bool(checked))
        self._settings.setValue("video/sort_by_speed", bool(checked))
        self._reload()

    def _start_speed_test(self) -> None:
        """启动批量测速（后台线程）。已在跑就提示，不重复开。"""
        if self._speed_worker is not None and self._speed_worker.isRunning():
            self._toaster.info("正在测速中，请稍候")
            return
        self._speed_button.setEnabled(False)
        self._cancel_speed_button.setEnabled(True)
        self._status.setText("正在测速（延迟 + 首个分片）…")
        worker = _SpeedWorker(self._registry, concurrency=self._speed_concurrency.value(),
                              parent=self)
        worker.progressed.connect(self._on_speed_progress)
        worker.finishedAll.connect(self._on_speed_finished)
        worker.failed.connect(self._on_speed_failed)
        self._speed_worker = worker
        worker.start()

    def _cancel_speed_test(self) -> None:
        if self._speed_worker is not None and self._speed_worker.isRunning():
            self._speed_worker.cancel()
            self._status.setText("正在取消测速（已发出的请求会在单源超时后结束）…")

    def _on_speed_progress(self, result, done: int, total: int) -> None:  # noqa: ANN001
        speed_item = self._table.item(self._row_of(result.key), COLUMN_SPEED)
        if speed_item is not None:
            speed_item.setText(result.as_text())
            if result.error:
                speed_item.setToolTip(f"{SPEED_COLUMN_HINT}\n{result.error}")
        self._status.setText(f"测速中 {done}/{total}：{result.label or result.key} —— {result.as_text()}")

    def _on_speed_finished(self, results) -> None:  # noqa: ANN001
        self._speed_button.setEnabled(True)
        self._cancel_speed_button.setEnabled(False)
        self._speed_worker = None
        usable = [item for item in results if item.ok]
        measured = [item for item in results if item.mbit_per_s > 0]
        if not results:
            self._status.setText("测速已取消")
        else:
            self._status.setText(
                f"测速完成：{len(usable)}/{len(results)} 个源响应正常，"
                f"其中 {len(measured)} 个测到了速度（速度按首个分片估算）"
            )
        # 顺序按测速结果重排（开关打开时），并把新数字填进表格
        self._reload()
        if results and len(measured) < len(results):
            # 失败详情只在状态栏留一句话，不弹错误框 —— 测速本来就是"尽力而为"
            self._toaster.info("部分源没有测到速度（详情见「测速」列 tooltip）")

    def _on_speed_failed(self, message: str) -> None:
        self._speed_button.setEnabled(True)
        self._cancel_speed_button.setEnabled(False)
        self._speed_worker = None
        self._status.setText(f"测速失败：{message}")
        self._toaster.error(f"测速失败：{message}")

    def _row_of(self, key: str) -> int:
        for row in range(self._table.rowCount()):
            if self._key_at(row) == key:
                return row
        return -1

    # ------------------------------------------------------------------ 操作

    def _move(self, delta: int) -> None:
        row = self._table.currentRow()
        if row < 0:
            self._toaster.info("请先选中一行")
            return
        target = row + delta
        if not (0 <= target < self._table.rowCount()):
            return
        order = [self._key_at(index) for index in range(self._table.rowCount())]
        order[row], order[target] = order[target], order[row]
        self._registry.set_order([key for key in order if key])
        self._reload()
        self._table.setCurrentCell(target, 1)

    def _probe_selected(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            self._toaster.info("请先选中要测试的源")
            return
        key = self._key_at(row)
        if not key:
            return
        provider = self._registry.get(key)
        label = provider.label if provider is not None else key
        self._status.setText(f"正在测试「{label}」…")
        self._probe = _ProbeWorker(self._registry, key, parent=self)
        self._probe.finishedProbe.connect(self._on_probe)
        self._probe.start()

    def _on_probe(self, key: str, ok: bool, message: str) -> None:
        label = key
        provider = self._registry.get(key)
        if provider is not None:
            label = provider.label
        self._status.setText(f"{label}：{message}")
        if ok:
            self._toaster.success(f"{label} 可用")
        else:
            self._toaster.error(f"{label} 不可用：{message}")
        self._reload()

    def _probe_all(self) -> None:
        """一键测试所有可用源：快速区分「本机网络问题」与「个别源失效」。"""
        if self._probe_all_worker is not None and self._probe_all_worker.isRunning():
            self._toaster.info("正在测试中，请稍候")
            return
        self._status.setText("正在逐个测试数据源（每个源跑一次真实搜索）…")
        worker = _ProbeAllWorker(self._registry, parent=self)
        worker.probed.connect(self._on_probed_one)
        worker.finishedAll.connect(self._on_probed_all)
        self._probe_all_worker = worker
        worker.start()

    def _on_probed_one(self, key: str, ok: bool, message: str) -> None:
        for row in range(self._table.rowCount()):
            if self._key_at(row) == key:
                item = self._table.item(row, COLUMN_STATUS)
                if item is not None:
                    item.setText(f"{'可用' if ok else '不可用'}（{message[:60]}）")
                break

    def _on_probed_all(self, ok: int, total: int) -> None:
        if ok >= total:
            self._status.setText(f"测试完成：{ok}/{total} 个源可用")
            self._toaster.success(f"{ok}/{total} 个源可用")
        elif ok == 0:
            self._status.setText(
                f"测试完成：{ok}/{total} 个源可用 —— 全部不可用通常是本机网络/代理问题"
                "（可检查代理是否开启、或稍后重试）"
            )
            self._toaster.error("所有源都不可用：请检查网络或代理设置")
        else:
            self._status.setText(
                f"测试完成：{ok}/{total} 个源可用 —— 不可用的源可停用、或改接口地址后用其他源"
            )
            self._toaster.info(f"{ok}/{total} 个源可用")
        self._reload()

    def _edit_url(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            self._toaster.info("请先选中一行")
            return
        key = self._key_at(row)
        provider = self._registry.get(key)
        if provider is None:
            return
        if not provider.info.editable:
            self._toaster.info("该源地址固定，不可编辑（可直接在表格里修改可编辑列的源）")
            return
        from PySide6.QtWidgets import QInputDialog

        text, ok = QInputDialog.getText(
            self, "编辑接口地址",
            f"{provider.label} 的采集接口地址：\n（形如 https://站点域名，或完整的 api.php/provide/vod）",
            text=provider.credential or provider.info.homepage or "",
        )
        if not ok:
            return
        provider.set_credential((text or "").strip())
        self._reload()
        self._toaster.success("已更新接口地址（点确定后保存）")

    def _reset(self) -> None:
        from modu_workbench.core.video.sources import DEFAULT_PROVIDER_ORDER

        self._registry.set_order(list(DEFAULT_PROVIDER_ORDER))
        for provider in self._registry.providers(enabled_only=False):
            # 恢复内置默认地址（用户改过错域名时也能一键回到出厂设置）
            provider.reset_credential()
        self._registry.set_enabled_keys(DEFAULT_PROVIDER_ORDER)
        self._reload()
        self._toaster.info("已恢复默认源顺序与地址")

    # ------------------------------------------------------------------ 保存

    def _save(self) -> None:
        # 测速并发与排序开关是应用设置（不是源清单），跟着设置页那一份走
        self._settings.setValue("video/sort_by_speed", self._sort_by_speed.isChecked())
        self._settings.setValue("video/speed_test_concurrency", self._speed_concurrency.value())
        try:
            self._registry.save_settings(self._storage)
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"保存失败：{error}")
            return
        self._toaster.success("源设置已保存")
        self.accept()

    def closeEvent(self, event) -> None:  # noqa: N802
        """关闭前收尾：**绝不能丢掉仍在运行的 QThread 引用**（会让进程直接崩）。"""
        for worker in (self._probe, self._probe_all_worker, self._speed_worker):
            if worker is None or not worker.isRunning():
                continue
            if isinstance(worker, (_ProbeAllWorker, _SpeedWorker)):
                worker.cancel()
            worker.wait(2000)
        super().closeEvent(event)
