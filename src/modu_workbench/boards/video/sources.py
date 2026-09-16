"""墨软影视 · 源设置：启用/停用、调整优先级、修改采集接口地址、测试连通性。

这是「多数据源可切换、后续加源容易」的用户侧入口（诉求第 6 条）：
- 勾选启用哪些源（聚合搜索只走启用的）；
- 上/下移调整优先级（顺序决定搜索先后与去重保留谁）；
- 采集源地址可改（站点换域名时无需等版本更新）；
- 「测试」按钮立即验证某个源当前是否可用，并显示健康度。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.video import VideoRegistry
from . import context as app_context
from modu_workbench.ui_kit.toast import Toaster

COLUMNS = ["启用", "数据源", "能力", "状态", "接口地址（可改）"]


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
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
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

        self._status = QLabel("提示：采集类数据源来自第三方站点，可能随时变更或失效。")
        self._status.setObjectName("readerStatus")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._reload()

    # ------------------------------------------------------------------ 表格

    def _reload(self) -> None:
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        for provider in self._registry.providers(enabled_only=False):
            info = provider.info
            row = self._table.rowCount()
            self._table.insertRow(row)

            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(
                Qt.CheckState.Checked if self._registry.is_enabled(info.key) else Qt.CheckState.Unchecked
            )
            check.setData(Qt.ItemDataRole.UserRole, info.key)
            self._table.setItem(row, 0, check)

            self._table.setItem(row, 1, QTableWidgetItem(f"{info.label}（{info.key}）"))
            self._table.setItem(row, 2, QTableWidgetItem(info.kind_label))
            self._table.setItem(row, 3, QTableWidgetItem(self._registry.status_text(info.key)))
            editable = info.editable
            url_item = QTableWidgetItem(provider.credential or info.homepage or "-")
            if editable:
                url_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                                  | Qt.ItemFlag.ItemIsEditable)
            url_item.setToolTip(info.note)
            self._table.setItem(row, 4, url_item)
        self._table.blockSignals(False)

    def _key_at(self, row: int) -> str:
        item = self._table.item(row, 0)
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else ""

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            key = self._key_at(item.row())
            if key:
                self._registry.set_enabled(key, item.checkState() == Qt.CheckState.Checked)
                self._table.blockSignals(True)
                status = self._table.item(item.row(), 3)
                if status is not None:
                    status.setText(self._registry.status_text(key))
                self._table.blockSignals(False)
        elif item.column() == 4:
            key = self._key_at(item.row())
            provider = self._registry.get(key)
            if provider is not None and provider.info.editable:
                provider.set_credential(item.text().strip())

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
                item = self._table.item(row, 3)
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
        try:
            self._registry.save_settings(self._storage)
        except Exception as error:  # noqa: BLE001
            self._toaster.error(f"保存失败：{error}")
            return
        self._toaster.success("源设置已保存")
        self.accept()

    def closeEvent(self, event) -> None:  # noqa: N802
        for worker in (self._probe, self._probe_all_worker):
            if worker is not None and worker.isRunning():
                if isinstance(worker, _ProbeAllWorker):
                    worker.cancel()
                worker.wait(2000)
        super().closeEvent(event)
