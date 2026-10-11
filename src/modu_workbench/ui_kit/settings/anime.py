"""墨软动漫设置页：播放 / 下载 / 弹幕 / 元数据。

与「影视」页的差别都在第七板块特有的东西上：
- 下载并发沿用影视调好的两档（单集分片并发 2–16、同时下载集数 1–4），
  动漫的下载器复用同一套分片下载实现，参数语义一致；
- 弹幕是**只读**的弹弹play 弹幕：需要 AppId/AppSecret（在 dev.dandanplay.com 申请），
  这里只存 `QSettings`（**不进代码库、不写进规则文件**）；默认不开，开了才会请求；
- Bangumi 元数据用于详情页评分/简介与时间表，默认开启但**只在需要时联网**。

**本页不能 import `boards.anime`**：架构闸门禁止 `ui_kit → boards.*`
（`tests/architecture/test_architecture.py`），所以路径与默认值在这里就地实现，
与板块内 `widgets.py` 的 `anime_download_dir_pref()` 保持同一套规则。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.platform import paths as config

from .base import SettingsPage, app_settings

#: 弹幕同屏上限的档位说明（0 = 按性能自动，对应 ui_kit/media/danmaku.py 的分级）
DANMAKU_LINES_HINT = "0 = 按性能自动（高/中/低三档：60 / 40 / 25）"


def anime_dir() -> Path:
    """动漫数据目录：优先用 `paths.anime_dir()`，没有就用数据目录下的 anime/。"""
    getter = getattr(config, "anime_dir", None)
    if callable(getter):
        try:
            return Path(getter())
        except Exception:  # noqa: BLE001  路径函数存在但不可用：退回默认位置
            pass
    target = config.app_data_dir() / "anime"
    target.mkdir(parents=True, exist_ok=True)
    return target


def danmaku_cache_dir() -> Path:
    """弹幕缓存目录（磁盘文件才是弹幕本体，DB 只记索引）。"""
    target = anime_dir() / "danmaku"
    target.mkdir(parents=True, exist_ok=True)
    return target


def anime_download_dir_default() -> str:
    """默认下载目录：优先 `paths.anime_download_dir()`，否则 视频目录/墨软动漫。"""
    getter = getattr(config, "anime_download_dir", None)
    if callable(getter):
        try:
            return str(getter())
        except Exception:  # noqa: BLE001
            pass
    try:
        return str(Path(str(config.video_download_dir())).parent / "墨软动漫")
    except Exception:  # noqa: BLE001
        return str(Path.home() / "Videos" / "墨软动漫")


def clear_danmaku_cache(directory: str | Path | None = None) -> tuple[int, int]:
    """清空弹幕缓存；返回 `(删除文件数, 释放字节数)`。

    只删目录里的**文件**（缓存是"删了会自动重新下载"的东西），子目录一并递归；
    单个文件删不掉（被占用）就跳过并把它算进"未删除"，不抛异常打断设置页。
    """
    target = Path(directory) if directory is not None else danmaku_cache_dir()
    removed = 0
    freed = 0
    if not target.is_dir():
        return 0, 0
    for path in sorted(target.rglob("*")):
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError:
            continue
        removed += 1
        freed += size
    return removed, freed


class AnimeSettingsPage(SettingsPage):
    """动漫板块设置（播放 / 下载 / 弹幕 / 元数据）。"""

    title = "动漫"
    icon = "🎞"

    def __init__(self, parent: QWidget | None = None):  # noqa: ANN001
        super().__init__(parent)
        self._settings = app_settings()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        # ---- 播放 ----
        layout.addWidget(self._section("播放"))
        play_form = QFormLayout()
        play_form.setSpacing(10)
        self._auto_next = QCheckBox("播完自动连播下一集")
        self._auto_next.setToolTip("按「同线路下一集 → 换线路第一集 → 换规则」的顺序找下一集")
        play_form.addRow("连播", self._auto_next)

        self._player_hint = QLabel("播放器与影视板块共用（ui_kit/media/player.py）：支持弹幕层、字幕与 F12 诊断。")
        self._player_hint.setObjectName("readerStatus")
        self._player_hint.setWordWrap(True)
        play_form.addRow("播放器", self._player_hint)
        layout.addLayout(play_form)

        # ---- 下载 ----
        layout.addWidget(self._section("下载"))
        download_form = QFormLayout()
        download_form.setSpacing(10)
        self._download_dir = QLineEdit()
        pick = QPushButton("选择…")
        pick.clicked.connect(self._pick_dir)
        holder = QHBoxLayout()
        holder.addWidget(self._download_dir, 1)
        holder.addWidget(pick)
        wrapper = QWidget()
        wrapper.setLayout(holder)
        download_form.addRow("下载目录", wrapper)

        self._segment_concurrency = QSpinBox()
        self._segment_concurrency.setRange(2, 16)
        self._segment_concurrency.setSuffix(" 路")
        self._segment_concurrency.setToolTip("单个视频内同时下载的分片数（默认 6，太高会被站点限速）")
        download_form.addRow("分片并发", self._segment_concurrency)

        self._download_parallel = QSpinBox()
        self._download_parallel.setRange(1, 4)
        self._download_parallel.setSuffix(" 集")
        self._download_parallel.setToolTip("同时下载几集（默认 2，采集站限流时调到 1）")
        download_form.addRow("同时下载", self._download_parallel)
        layout.addLayout(download_form)

        # ---- 弹幕 ----
        layout.addWidget(self._section("弹幕（弹弹play，只读）"))
        danmaku_form = QFormLayout()
        danmaku_form.setSpacing(10)
        self._danmaku_enabled = QCheckBox("启用弹幕（默认关闭）")
        self._danmaku_enabled.setToolTip("弹幕只读：我们不发送弹幕（受限接口未开放），也不做批量抓取")
        danmaku_form.addRow("开关", self._danmaku_enabled)

        self._danmaku_app_id = QLineEdit()
        self._danmaku_app_id.setPlaceholderText("在 dev.dandanplay.com 申请后填入")
        danmaku_form.addRow("AppId", self._danmaku_app_id)

        self._danmaku_app_secret = QLineEdit()
        self._danmaku_app_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self._danmaku_app_secret.setPlaceholderText("只存本机设置，不会写进规则文件")
        danmaku_form.addRow("AppSecret", self._danmaku_app_secret)

        self._danmaku_opacity = QSpinBox()
        self._danmaku_opacity.setRange(20, 100)
        self._danmaku_opacity.setSuffix(" %")
        danmaku_form.addRow("不透明度", self._danmaku_opacity)

        self._danmaku_speed = QSpinBox()
        self._danmaku_speed.setRange(4, 12)
        self._danmaku_speed.setSuffix(" 秒 / 屏")
        danmaku_form.addRow("滚动速度", self._danmaku_speed)

        self._danmaku_lines = QSpinBox()
        self._danmaku_lines.setRange(0, 60)
        self._danmaku_lines.setSpecialValueText("按性能自动")
        self._danmaku_lines.setToolTip(DANMAKU_LINES_HINT)
        danmaku_form.addRow("同屏上限", self._danmaku_lines)

        self._danmaku_block = QLineEdit()
        self._danmaku_block.setPlaceholderText("关键词用逗号分隔；/正则/ 生效，例如：广告,/^\\d+$/")
        danmaku_form.addRow("屏蔽词", self._danmaku_block)

        self._danmaku_usage = QLabel("")
        self._danmaku_usage.setObjectName("readerStatus")
        clear_cache = QPushButton("清空弹幕缓存")
        clear_cache.clicked.connect(self._clear_danmaku)
        cache_row = QHBoxLayout()
        cache_row.addWidget(self._danmaku_usage, 1)
        cache_row.addWidget(clear_cache)
        cache_holder = QWidget()
        cache_holder.setLayout(cache_row)
        danmaku_form.addRow("缓存占用", cache_holder)
        layout.addLayout(danmaku_form)

        # ---- 元数据 ----
        layout.addWidget(self._section("元数据"))
        meta_form = QFormLayout()
        meta_form.setSpacing(10)
        self._bangumi_enabled = QCheckBox("启用 Bangumi 元数据（评分/简介/放送日）")
        self._bangumi_enabled.setToolTip("只在详情页与时间表需要时联网；关掉后详情页只用规则给的数据")
        meta_form.addRow("Bangumi", self._bangumi_enabled)
        self._update_on_start = QCheckBox("启动时检查追番更新")
        self._update_on_start.setToolTip("追番多时启动会慢一些；默认关闭，需要时手动点「检查更新」")
        meta_form.addRow("更新检查", self._update_on_start)

        storage_hint = QLabel("规则订阅与校验和由「规则」页管理，这里不重复提供开关。")
        storage_hint.setObjectName("readerStatus")
        storage_hint.setWordWrap(True)
        meta_form.addRow("订阅", storage_hint)
        layout.addLayout(meta_form)

        layout.addStretch(1)
        hint = QLabel(
            "能力边界：规则驱动的采集没有浏览器内核 —— 纯 JS 渲染的站点解析不了；"
            "弹幕只读；跨设备同步走「番剧库」的 JSON 导入导出。"
        )
        hint.setObjectName("readerStatus")
        hint.setWordWrap(True)
        layout.addWidget(hint)

    # ------------------------------------------------------------------ 辅助

    @staticmethod
    def _section(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def _pick_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择动漫下载目录",
                                                  self._download_dir.text())
        if folder:
            self._download_dir.setText(folder)

    def _clear_danmaku(self) -> None:
        removed, freed = clear_danmaku_cache()
        self._settings.setValue("anime/danmaku_cache_files", removed)
        if removed:
            self._danmaku_usage.setText(
                f"已清空 {removed} 个文件，释放 {freed / 1048576:.1f} MB · 现在 {self._usage_text()}")
        else:
            self._danmaku_usage.setText("缓存已经是空的（弹幕会在播放时按需重新下载）")

    @staticmethod
    def _usage_text() -> str:
        target = danmaku_cache_dir()
        count = 0
        total = 0
        try:
            for path in target.rglob("*"):
                if path.is_file():
                    count += 1
                    total += path.stat().st_size
        except OSError:
            pass
        return f"{count} 个文件 / {total / 1048576:.1f} MB（可安全清理，会按需重新下载）"

    def _refresh_danmaku_usage(self) -> None:
        self._danmaku_usage.setText(self._usage_text())

    # ------------------------------------------------------------------ 存取

    def load(self) -> None:
        self._auto_next.setChecked(self._settings.value("anime/auto_next", True, type=bool))
        self._download_dir.setText(
            self._settings.value("anime/download_dir", "", type=str)
            or anime_download_dir_default())
        self._segment_concurrency.setValue(
            int(self._settings.value("anime/segment_concurrency", 6) or 6))
        self._download_parallel.setValue(
            int(self._settings.value("anime/download_parallel", 2) or 2))

        self._danmaku_enabled.setChecked(
            self._settings.value("anime/danmaku_enabled", False, type=bool))
        self._danmaku_app_id.setText(
            self._settings.value("anime/danmaku_app_id", "", type=str))
        self._danmaku_app_secret.setText(
            self._settings.value("anime/danmaku_app_secret", "", type=str))
        self._danmaku_opacity.setValue(
            int(self._settings.value("anime/danmaku_opacity", 80) or 80))
        self._danmaku_speed.setValue(
            int(self._settings.value("anime/danmaku_speed", 8) or 8))
        self._danmaku_lines.setValue(
            int(self._settings.value("anime/danmaku_max_lines", 0) or 0))
        self._danmaku_block.setText(
            self._settings.value("anime/danmaku_block_words", "", type=str))
        self._refresh_danmaku_usage()

        self._bangumi_enabled.setChecked(
            self._settings.value("anime/bangumi_enabled", True, type=bool))
        self._update_on_start.setChecked(
            self._settings.value("anime/update_on_start", False, type=bool))

    def save(self) -> bool:
        self._settings.setValue("anime/auto_next", self._auto_next.isChecked())
        self._settings.setValue("anime/download_dir", self._download_dir.text().strip())
        self._settings.setValue("anime/segment_concurrency", self._segment_concurrency.value())
        self._settings.setValue("anime/download_parallel", self._download_parallel.value())

        self._settings.setValue("anime/danmaku_enabled", self._danmaku_enabled.isChecked())
        self._settings.setValue("anime/danmaku_app_id", self._danmaku_app_id.text().strip())
        self._settings.setValue("anime/danmaku_app_secret", self._danmaku_app_secret.text().strip())
        self._settings.setValue("anime/danmaku_opacity", self._danmaku_opacity.value())
        self._settings.setValue("anime/danmaku_speed", self._danmaku_speed.value())
        self._settings.setValue("anime/danmaku_max_lines", self._danmaku_lines.value())
        self._settings.setValue("anime/danmaku_block_words", self._danmaku_block.text().strip())

        self._settings.setValue("anime/bangumi_enabled", self._bangumi_enabled.isChecked())
        self._settings.setValue("anime/update_on_start", self._update_on_start.isChecked())
        self._settings.sync()
        return True

    def hint(self) -> str:
        return ("弹幕需要弹弹play 的 AppId/AppSecret（只存本机），默认关闭；"
                "下载目录与并发对动漫板块的所有下载生效；Bangumi 元数据只在需要时联网。")


__all__ = [
    "AnimeSettingsPage",
    "anime_dir",
    "anime_download_dir_default",
    "clear_danmaku_cache",
    "danmaku_cache_dir",
]
