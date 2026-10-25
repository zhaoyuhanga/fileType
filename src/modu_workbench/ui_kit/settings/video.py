"""墨软影视设置页：下载目录、自动换源、播放/缓存/弹幕、数据源启用与接口地址。

数据源顺序与连通性测试在板块内「源设置」里（那里更顺手）；
这里提供"按板块找设置"时需要的下载与容错开关，以及接口地址一览。

v1.1.0 新增三组（对应 docs/VIDEO_PLAN.md 3.4 的设置键）：

- **播放**：解码后端与硬解开关（进程级环境变量，改了要重开播放窗口）；
- **缓存/网络**：缓冲档位（省流/均衡/高质）、磁盘配额、预取深度、并发与限速；
- **弹幕**：总开关、不透明度、速度、屏蔽词（关键词 + `/正则/`）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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

from modu_workbench.app import context as app_context
from modu_workbench.core.platform import paths as config

from .base import SettingsPage, app_settings
from modu_workbench.core.platform.logging import swallow

#: 解码后端选项 → (值, 展示文案)
MEDIA_BACKENDS = (
    ("auto", "自动（推荐：FFmpeg 后端）"),
    ("ffmpeg", "强制 FFmpeg（HLS 必需）"),
    ("wmf", "Windows Media Foundation"),
)
#: 硬解选项
HW_DECODE_MODES = (
    ("auto", "自动（优先硬解，失败可回退）"),
    ("on", "强制开启硬解"),
    ("off", "关闭硬解（软解，CPU 高但不花屏）"),
)
#: 渲染路径
RENDER_PATHS = (
    ("auto", "自动（优先 QGraphicsVideoItem）"),
    ("graphics", "QGraphicsView + QGraphicsVideoItem"),
    ("widget", "QVideoWidget（旧路径，不能叠加弹幕）"),
)
#: 画质策略
QUALITY_POLICIES = (
    ("auto", "自动（按实测带宽选档）"),
    ("highest", "总是最高清晰度（旧行为）"),
    ("1080p", "目标 1080P"),
    ("720p", "目标 720P（省流量）"),
    ("lowest", "最低清晰度（极弱网）"),
)
#: 缓冲档位 → (值, 展示文案, 说明)
BUFFER_MODES = (
    ("low", "省流（256 MB / 预取 2 片）", "移动热点、按流量计费、老机器"),
    ("balanced", "均衡（1 GB / 预取 3 片）", "家用宽带，默认"),
    ("high", "高质（2 GB / 预取 6 片）", "千兆或局域网源，追求不卡"),
)


class VideoSettingsPage(SettingsPage):
    title = "影视"
    icon = "🎬"

    def __init__(self, parent=None):  # noqa: ANN001
        super().__init__(parent)
        self._settings = app_settings()
        self._registry = app_context.video_registry()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(10)

        self._download_dir = QLineEdit()
        pick = QPushButton("选择…")
        pick.clicked.connect(self._pick_dir)
        row = QHBoxLayout()
        row.addWidget(self._download_dir, 1)
        row.addWidget(pick)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow("下载目录", holder)

        self._auto_switch = QCheckBox("播放/下载失败时自动换源重试")
        form.addRow("容错", self._auto_switch)

        self._compliance = QCheckBox("已阅读并遵守各站点条款（仅用于个人学习）")
        form.addRow("合规", self._compliance)

        ffmpeg = QLabel("随包 ffmpeg 用于 HLS 合流为 MP4；缺失时下载退化为 .ts")
        ffmpeg.setObjectName("readerStatus")
        ffmpeg.setWordWrap(True)
        form.addRow("下载", ffmpeg)

        # 路径用只读输入框而不是 QLabel：长路径会把整页最小宽度顶到 900px 以上，
        # 设置窗口不够宽时就会横向滚动（gallery 页就是这个做法）。
        self._library_dir = QLineEdit(str(config.video_dir()))
        self._library_dir.setReadOnly(True)
        self._library_dir.setToolTip(str(config.video_dir()))
        form.addRow("影视库目录", self._library_dir)
        layout.addLayout(form)

        layout.addWidget(QLabel("数据源（停用后不参与搜索与自动换源）"))
        self._source_checks: dict[str, QCheckBox] = {}
        for info in self._registry.infos():
            # 标签保持短（长文本会把整页顶宽、出现横向滚动条），细节放 tooltip
            check = QCheckBox(f"{info.label}（{info.kind_label}）")
            check.setToolTip(
                f"{info.note}\n状态：{self._registry.status_text(info.key)}\n"
                f"接口：{self._registry.get(info.key).credential or info.homepage or '-'}"
            )
            self._source_checks[info.key] = check
            layout.addWidget(check)

        layout.addWidget(self._section("播放（解码与渲染）"))
        play_form = QFormLayout()
        play_form.setSpacing(10)

        self._media_backend = QComboBox()
        for value, label in MEDIA_BACKENDS:
            self._media_backend.addItem(label, value)
        self._media_backend.setToolTip(
            "Windows 的 Media Foundation 不支持 HLS，所以默认固定 FFmpeg 后端。"
            "环境变量在进程启动时注入，**改动后需重开播放窗口/重启应用生效**。"
        )
        play_form.addRow("解码后端", self._media_backend)

        self._hw_decode = QComboBox()
        for value, label in HW_DECODE_MODES:
            self._hw_decode.addItem(label, value)
        self._hw_decode.setToolTip(
            "硬解（d3d11va/d3d12va/dxva2）能把 1080p 软解的 CPU 占用从 ~100% 降到 ~25%。"
            "若出现花屏/黑屏请改为「关闭硬解」。"
        )
        play_form.addRow("硬件解码", self._hw_decode)

        self._texture_conversion = QCheckBox("启用 GPU 纹理转换（关闭可解决部分显卡的花屏）")
        play_form.addRow("纹理转换", self._texture_conversion)

        self._profile_mismatch = QCheckBox("允许硬解 profile 不匹配（有风险，仅在花屏时尝试）")
        play_form.addRow("兜底", self._profile_mismatch)

        self._render_path = QComboBox()
        for value, label in RENDER_PATHS:
            self._render_path.addItem(label, value)
        self._render_path.setToolTip(
            "QGraphicsVideoItem 才能叠加弹幕/字幕/统计浮层；异常时切回旧路径。"
        )
        play_form.addRow("渲染路径", self._render_path)

        self._quality_policy = QComboBox()
        for value, label in QUALITY_POLICIES:
            self._quality_policy.addItem(label, value)
        play_form.addRow("清晰度策略", self._quality_policy)

        self._auto_downgrade = QCheckBox("卡顿时自动降档（推荐）")
        self._auto_upgrade = QCheckBox("长时间流畅后自动升档（可能来回横跳，默认关）")
        play_form.addRow("自动调档", self._auto_downgrade)
        play_form.addRow("", self._auto_upgrade)

        self._auto_next = QCheckBox("播放结束自动连播下一集")
        play_form.addRow("连播", self._auto_next)
        layout.addLayout(play_form)

        layout.addWidget(self._section("缓存与网络（代理/下载）"))
        cache_form = QFormLayout()
        cache_form.setSpacing(10)

        self._buffer_mode = QComboBox()
        for value, label, tip in BUFFER_MODES:
            self._buffer_mode.addItem(label, value)
            self._buffer_mode.setItemData(
                self._buffer_mode.count() - 1, tip, Qt.ItemDataRole.ToolTipRole
            )
        self._buffer_mode.setToolTip("缓冲档位同时决定磁盘缓存配额与预取深度。")
        cache_form.addRow("缓冲档位", self._buffer_mode)

        self._cache_dir = QLineEdit(str(config.video_dir() / "cache"))
        self._cache_dir.setReadOnly(True)
        self._cache_dir.setToolTip("分片磁盘缓存目录（清空即删这里的文件）")
        cache_form.addRow("缓存目录", self._cache_dir)

        self._cache_quota = QSpinBox()
        self._cache_quota.setRange(256, 20480)
        self._cache_quota.setSingleStep(256)
        self._cache_quota.setSuffix(" MB")
        cache_form.addRow("缓存配额", self._cache_quota)

        self._prefetch = QSpinBox()
        self._prefetch.setRange(0, 10)
        self._prefetch.setSuffix(" 片")
        self._prefetch.setToolTip("清单加载后顺手预取前 N 个分片到本地缓存（0 = 关闭）")
        cache_form.addRow("预取深度", self._prefetch)

        self._proxy_concurrency = QSpinBox()
        self._proxy_concurrency.setRange(2, 32)
        self._proxy_concurrency.setSuffix(" 并发")
        cache_form.addRow("代理并发", self._proxy_concurrency)

        self._segment_concurrency = QSpinBox()
        self._segment_concurrency.setRange(2, 16)
        self._segment_concurrency.setSuffix(" 路")
        self._segment_concurrency.setToolTip("单个视频内同时下载的分片数（默认 6）")
        cache_form.addRow("分片并发", self._segment_concurrency)

        self._download_parallel = QSpinBox()
        self._download_parallel.setRange(1, 4)
        self._download_parallel.setSuffix(" 集")
        cache_form.addRow("同时下载集数", self._download_parallel)

        self._speed_limit = QSpinBox()
        self._speed_limit.setRange(0, 50)
        self._speed_limit.setSuffix(" MB/s")
        self._speed_limit.setSpecialValueText("不限速")
        cache_form.addRow("下载限速", self._speed_limit)

        self._skip_ads = QCheckBox("下载时跳过疑似广告分片（实验性，默认关）")
        self._skip_ads.setToolTip(
            "只跳「时长明显短于中位且与前一 片不同目录」的分片，最多 3 片，并会提示跳了哪些。"
            "误判会丢正片内容，所以默认关闭。"
        )
        cache_form.addRow("广告分片", self._skip_ads)

        # v3-e：测速排序默认关（测速结果会波动，顺序变化必须由用户显式开启，见 3.3.5）
        self._sort_by_speed = QCheckBox("按源测速结果排序（默认关，测速会波动）")
        self._sort_by_speed.setToolTip(
            "关闭时：搜索先后、去重保留谁、换源顺序都按「源设置」里的顺序；"
            "打开后改为按延迟/速度/分辨率的综合评分降序"
            "（评分只用来选源，不决定缓冲档位）。测速在「源设置」里点「测速」。"
        )
        cache_form.addRow("源排序", self._sort_by_speed)

        self._speed_concurrency = QSpinBox()
        self._speed_concurrency.setRange(1, 6)
        self._speed_concurrency.setSuffix(" 并发")
        self._speed_concurrency.setToolTip(
            "同时测几个源；最多测 20 个（顺序最靠前的 5 个 + 其余随机 15 个）"
        )
        cache_form.addRow("测速并发", self._speed_concurrency)

        self._cache_usage = QLabel("")
        self._cache_usage.setObjectName("readerStatus")
        clear_cache = QPushButton("清空分片缓存")
        clear_cache.clicked.connect(self._clear_cache)
        cache_row = QHBoxLayout()
        cache_row.addWidget(self._cache_usage, 1)
        cache_row.addWidget(clear_cache)
        cache_holder = QWidget()
        cache_holder.setLayout(cache_row)
        cache_form.addRow("缓存占用", cache_holder)
        layout.addLayout(cache_form)

        layout.addWidget(self._section("弹幕（弹弹play，只读）"))
        danmaku_form = QFormLayout()
        danmaku_form.setSpacing(10)

        self._danmaku_enabled = QCheckBox("启用弹幕（默认关闭，首次需手动开启）")
        danmaku_form.addRow("开关", self._danmaku_enabled)

        self._danmaku_app_id = QLineEdit()
        self._danmaku_app_id.setPlaceholderText("在 dev.dandanplay.com 申请后填入")
        danmaku_form.addRow("弹弹play AppId", self._danmaku_app_id)

        self._danmaku_secret = QLineEdit()
        self._danmaku_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self._danmaku_secret.setPlaceholderText("AppSecret（只存本机设置，不进代码库）")
        danmaku_form.addRow("AppSecret", self._danmaku_secret)

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
        self._danmaku_lines.setSpecialValueText("按性能自动（25/40/60）")
        danmaku_form.addRow("同屏上限", self._danmaku_lines)

        self._danmaku_block = QLineEdit()
        self._danmaku_block.setPlaceholderText("关键词用逗号分隔；/正则/ 生效，例如：广告,/^\\d+$/")
        danmaku_form.addRow("屏蔽词", self._danmaku_block)

        self._danmaku_dedupe = QCheckBox("同屏重复弹幕合并")
        danmaku_form.addRow("去重", self._danmaku_dedupe)
        layout.addLayout(danmaku_form)

        layout.addStretch(1)
        hint = QLabel(
            "源顺序、改动接口地址、连通性测试在板块内「源设置」里完成；"
            "采集类接口来自第三方站点，可能随时变更。"
        )
        hint.setObjectName("readerStatus")
        hint.setWordWrap(True)
        layout.addWidget(hint)

    def _section(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    @staticmethod
    def _select_data(box: QComboBox, value: str) -> None:
        index = box.findData(value)
        box.setCurrentIndex(index if index >= 0 else 0)

    def _pick_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            None, "选择视频下载目录", self._download_dir.text()
        )
        if folder:
            self._download_dir.setText(folder)

    def _clear_cache(self) -> None:
        from modu_workbench.services import media_proxy

        removed = media_proxy.get_cache().clear()
        self._refresh_cache_usage()
        # `setValue("video/cache_cleared_mb", ...)` 已删除：全仓无人读取（死键）。
        # 改成把"这次清了多少"写进缓存用量标签 —— 清完没有反馈会让人以为没生效。
        if removed > 0:
            self._cache_usage.setText(
                f"已清理 {removed / 1048576:.0f} MB｜" + self._cache_usage.text()
            )

    def _refresh_cache_usage(self) -> None:
        from modu_workbench.services import media_proxy

        info = media_proxy.get_cache().stats()
        used_mb = info["bytes"] / 1048576
        quota_mb = max(1, info["quota_bytes"] // 1048576)
        self._cache_usage.setText(
            f"{used_mb:.0f} MB / {quota_mb} MB（{info['count']} 个分片，可安全清理）"
        )

    def load(self) -> None:
        self._download_dir.setText(
            self._settings.value("video/download_dir", "", type=str)
            or str(config.video_download_dir())
        )
        self._auto_switch.setChecked(self._settings.value("video/auto_switch", True, type=bool))
        self._compliance.setChecked(self._settings.value("video/compliance", True, type=bool))
        for key, check in self._source_checks.items():
            check.setChecked(self._registry.is_enabled(key))

        # ---- 播放 ----
        self._select_data(
            self._media_backend, self._settings.value("video/media_backend", "auto", type=str)
        )
        self._select_data(
            self._hw_decode, self._settings.value("video/hw_decode", "auto", type=str)
        )
        self._texture_conversion.setChecked(
            self._settings.value("video/hw_texture_conversion", True, type=bool)
        )
        self._profile_mismatch.setChecked(
            self._settings.value("video/hw_profile_mismatch", False, type=bool)
        )
        self._select_data(
            self._render_path, self._settings.value("video/render_path", "auto", type=str)
        )
        self._select_data(
            self._quality_policy, self._settings.value("video/quality_policy", "auto", type=str)
        )
        self._auto_downgrade.setChecked(
            self._settings.value("video/auto_downgrade", True, type=bool)
        )
        self._auto_upgrade.setChecked(self._settings.value("video/auto_upgrade", False, type=bool))
        self._auto_next.setChecked(self._settings.value("video/auto_next", True, type=bool))

        # ---- 缓存与网络 ----
        self._select_data(
            self._buffer_mode, self._settings.value("video/buffer_mode", "balanced", type=str)
        )
        self._cache_quota.setValue(int(self._settings.value("video/cache_quota_mb", 1024) or 1024))
        self._prefetch.setValue(int(self._settings.value("video/prefetch_segments", 3) or 0))
        self._proxy_concurrency.setValue(
            int(self._settings.value("video/proxy_concurrency", 16) or 16)
        )
        self._segment_concurrency.setValue(
            int(self._settings.value("video/segment_concurrency", 6) or 6)
        )
        self._download_parallel.setValue(
            int(self._settings.value("video/download_parallel", 2) or 1)
        )
        self._speed_limit.setValue(
            int(self._settings.value("video/download_speed_limit_mbps", 0) or 0)
        )
        self._skip_ads.setChecked(self._settings.value("video/skip_ad_segments", False, type=bool))
        self._sort_by_speed.setChecked(
            self._settings.value("video/sort_by_speed", False, type=bool)
        )
        self._speed_concurrency.setValue(
            int(self._settings.value("video/speed_test_concurrency", 3) or 3)
        )
        # 让核心层拿到同一份排序开关（它不读 QSettings；此处兜住"启动后没进过设置页"的场景）
        try:
            self._registry.set_sort_by_speed(self._sort_by_speed.isChecked())
        except Exception as error:  # noqa: BLE001  外部回调/第三方边界
            swallow("video 中的可选步骤", error)
        self._refresh_cache_usage()

        # ---- 弹幕 ----
        self._danmaku_enabled.setChecked(
            self._settings.value("video/danmaku_enabled", False, type=bool)
        )
        self._danmaku_app_id.setText(self._settings.value("video/danmaku_app_id", "", type=str))
        self._danmaku_secret.setText(self._settings.value("video/danmaku_app_secret", "", type=str))
        self._danmaku_opacity.setValue(int(self._settings.value("video/danmaku_opacity", 80) or 80))
        self._danmaku_speed.setValue(int(self._settings.value("video/danmaku_speed", 8) or 8))
        self._danmaku_lines.setValue(int(self._settings.value("video/danmaku_max_lines", 0) or 0))
        self._danmaku_block.setText(self._settings.value("video/danmaku_block_words", "", type=str))
        self._danmaku_dedupe.setChecked(
            self._settings.value("video/danmaku_dedupe", False, type=bool)
        )

    def save(self) -> bool:
        self._settings.setValue("video/download_dir", self._download_dir.text().strip())
        self._settings.setValue("video/auto_switch", self._auto_switch.isChecked())
        self._settings.setValue("video/compliance", self._compliance.isChecked())
        for key, check in self._source_checks.items():
            self._registry.set_enabled(key, check.isChecked())
        try:
            self._registry.save_settings(app_context.video_storage())
        except Exception as error:  # noqa: BLE001  外部回调/第三方边界
            swallow("保存/加载设置", error)
        # ---- 播放 ----
        self._settings.setValue("video/media_backend", self._media_backend.currentData())
        self._settings.setValue("video/hw_decode", self._hw_decode.currentData())
        self._settings.setValue("video/hw_texture_conversion", self._texture_conversion.isChecked())
        self._settings.setValue("video/hw_profile_mismatch", self._profile_mismatch.isChecked())
        self._settings.setValue("video/render_path", self._render_path.currentData())
        self._settings.setValue("video/quality_policy", self._quality_policy.currentData())
        self._settings.setValue("video/auto_downgrade", self._auto_downgrade.isChecked())
        self._settings.setValue("video/auto_upgrade", self._auto_upgrade.isChecked())
        self._settings.setValue("video/auto_next", self._auto_next.isChecked())

        # ---- 缓存与网络 ----
        self._settings.setValue("video/buffer_mode", self._buffer_mode.currentData())
        self._settings.setValue("video/cache_quota_mb", self._cache_quota.value())
        self._settings.setValue("video/prefetch_segments", self._prefetch.value())
        self._settings.setValue("video/proxy_concurrency", self._proxy_concurrency.value())
        self._settings.setValue("video/segment_concurrency", self._segment_concurrency.value())
        self._settings.setValue("video/download_parallel", self._download_parallel.value())
        self._settings.setValue("video/download_speed_limit_mbps", self._speed_limit.value())
        self._settings.setValue("video/skip_ad_segments", self._skip_ads.isChecked())
        self._settings.setValue("video/sort_by_speed", self._sort_by_speed.isChecked())
        self._settings.setValue("video/speed_test_concurrency", self._speed_concurrency.value())
        # 排序开关是核心层与界面共用的状态：核心层不读 QSettings，
        # 所以保存后立刻把值注入注册表，否则要重启应用才生效。
        try:
            self._registry.set_sort_by_speed(self._sort_by_speed.isChecked())
        except Exception as error:  # noqa: BLE001  外部回调/第三方边界
            swallow("video 中的可选步骤", error)
        # ---- 弹幕 ----
        self._settings.setValue("video/danmaku_enabled", self._danmaku_enabled.isChecked())
        self._settings.setValue("video/danmaku_app_id", self._danmaku_app_id.text().strip())
        self._settings.setValue("video/danmaku_app_secret", self._danmaku_secret.text().strip())
        self._settings.setValue("video/danmaku_opacity", self._danmaku_opacity.value())
        self._settings.setValue("video/danmaku_speed", self._danmaku_speed.value())
        self._settings.setValue("video/danmaku_max_lines", self._danmaku_lines.value())
        self._settings.setValue("video/danmaku_block_words", self._danmaku_block_text())
        self._settings.setValue("video/danmaku_dedupe", self._danmaku_dedupe.isChecked())

        # 代理读的是进程级配置：保存后立刻刷新，不必重启
        try:
            from modu_workbench.services import media_proxy

            media_proxy.reset_config()
            media_proxy.get_config(refresh=True)
        except Exception as error:  # noqa: BLE001  可忽略但必须留痕
            swallow("video 中的可选步骤", error)
        return True

    def _danmaku_block_text(self) -> str:
        return self._danmaku_block.text().strip()

    def hint(self) -> str:
        return (
            "解码后端/硬解是**进程级**环境变量：改动后需重开播放窗口（必要时重启应用）生效。"
            "缓冲档位同时决定磁盘缓存配额与预取深度；分片缓存可随时清空，会自动重建。"
        )


__all__ = ["VideoSettingsPage"]
