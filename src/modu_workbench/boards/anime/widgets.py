"""墨软动漫界面共享零件：海报卡、线路选择器、追番按钮、剧集网格、设置读取。

三条硬约定（都踩过坑，别改）：

1. **本模块不 import `core.anime`**。第七板块的后端（引擎/库/存储）与界面并行开发，
   而 `core/anime/__init__.py` 一旦拉不起来，模块级 import 就会连带把整块界面炸掉
   —— 连"显示一个空状态"都做不到。所以后端类型一律**鸭子类型**读取，
   真需要实现时放在函数内部 `try/except` 导入。
2. **颜色/间距只取 `ui_kit/tokens.py`**，不新造数值（`docs/UI_GUIDE.md`）。
3. 网络相关一律异步（海报图），失败静默 —— 采集站的图片接口经常 403/超时，
   绝不能因为它把搜索结果卡住。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.platform import paths
from modu_workbench.ui_kit.components import chip, ghost_button
from modu_workbench.ui_kit.tokens import CARD_PADDING, ROW_GAP, SPACE

SETTINGS_ORG = "ModuWorkbench"
SETTINGS_APP = "modu-workbench"

#: 海报尺寸（卡片左侧固定区域；比例贴近 3:4，避免不同站点图片把卡片撑得高低不一）
POSTER_W = 78
POSTER_H = 104


# --------------------------------------------------------------------------- 设置


def anime_settings():  # noqa: ANN201
    """应用设置（与设置对话框「动漫」页共用同一个 QSettings）。"""
    from PySide6.QtCore import QSettings

    return QSettings(SETTINGS_ORG, SETTINGS_APP)


def setting_bool(key: str, default: bool = False) -> bool:
    """读布尔设置：Qt 在 Windows 注册表里把 bool 存成 "true"/"false" 字符串。

    `bool("false") is True` —— 影视与文档板块都因为直接 `bool(...)` 让"关掉"的开关失效过。
    """
    value = anime_settings().value(key, default)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("false", "0", "no", "off", ""):
        return False
    if text in ("true", "1", "yes", "on"):
        return True
    return bool(default)


def setting_int(key: str, default: int = 0) -> int:
    try:
        return int(anime_settings().value(key, default) or 0)
    except (TypeError, ValueError):
        return int(default)


def anime_download_dir_pref() -> str:
    """动漫下载目录：优先用户设置，否则用户视频目录下的「墨软动漫」。

    与「影视」一致：下载产物放在**用户可见**的目录，程序自己管的东西才进数据目录
    （见 `core/platform/paths.py` 的动漫小节）。
    """
    stored = str(anime_settings().value("anime/download_dir", "", type=str) or "").strip()
    if stored:
        return stored
    getter = getattr(paths, "anime_download_dir", None)
    if callable(getter):
        try:
            return str(getter())
        except Exception:  # noqa: BLE001  路径函数存在但不可用：退回默认位置
            pass
    try:
        return str(Path(str(paths.video_download_dir())).parent / "墨软动漫")
    except Exception:  # noqa: BLE001
        return str(Path.home() / "Videos" / "墨软动漫")


def open_in_browser(url: str) -> bool:
    """用系统默认浏览器打开（返回是否成功发起）。"""
    text = (url or "").strip()
    if not text:
        return False
    if not text.lower().startswith(("http://", "https://")):
        text = "http://" + text
    try:
        return bool(QDesktopServices.openUrl(QUrl(text)))
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- 后端鸭子类型


_API_LEVEL: int | None = None
_NORMALIZE_IMPL = None
_NORMALIZE_PROBED = False


def supported_api_level() -> int:
    """客户端支持的规则 API 等级（优先取后端常量，后端缺失时退回 8）。"""
    global _API_LEVEL
    if _API_LEVEL is None:
        try:
            from modu_workbench.core.anime import SUPPORTED_API_LEVEL

            _API_LEVEL = int(SUPPORTED_API_LEVEL)
        except Exception:  # noqa: BLE001
            _API_LEVEL = 8
    return _API_LEVEL


def normalize_title_safe(title: str) -> str:
    """标题归一化：优先用 `core.anime.normalize_title`，缺后端时退回等价实现。

    搜索页靠它把"同一部作品、多条规则命中"合并成一张卡。归一化规则一旦不一致，
    就会出现"同一部片子两张卡"或"两部片子被合并"，所以兜底实现与
    `core/anime/models.py::normalize_title` 保持同一套规则（NFKC → 去 HTML → 去标点 → casefold）。
    """
    global _NORMALIZE_IMPL, _NORMALIZE_PROBED
    if not _NORMALIZE_PROBED:
        _NORMALIZE_PROBED = True
        try:
            from modu_workbench.core.anime import normalize_title

            _NORMALIZE_IMPL = normalize_title
        except Exception:  # noqa: BLE001
            _NORMALIZE_IMPL = None
    if _NORMALIZE_IMPL is not None:
        try:
            return _NORMALIZE_IMPL(title)
        except Exception:  # noqa: BLE001
            pass
    import unicodedata

    text = unicodedata.normalize("NFKC", _strip_html(title or ""))
    return _PUNCT.sub("", text).strip().casefold()


_HTML_TAG = re.compile(r"<[^>]+>")
_PUNCT = re.compile(r"[\s\-_·・:：,，.。!！?？~～/\\|()（）\[\]【】\"'`]+")


def _strip_html(text: str) -> str:
    return _HTML_TAG.sub("", (text or "").replace("&nbsp;", " ")).strip()


def rule_api_level(rule) -> int:  # noqa: ANN001
    value = getattr(rule, "api_level", None)
    if value is None:
        try:
            value = int(str(getattr(rule, "api", "") or supported_api_level()))
        except (TypeError, ValueError):
            value = supported_api_level()
    try:
        return int(value)
    except (TypeError, ValueError):
        return supported_api_level()


def rule_supported(rule) -> bool:  # noqa: ANN001
    """规则是否需要更新的客户端（`api` 高于本客户端时加载但灰显）。"""
    flag = getattr(rule, "supported", None)
    if isinstance(flag, bool):
        return flag
    return rule_api_level(rule) <= supported_api_level()


def rule_label(rule) -> str:  # noqa: ANN001
    return str(getattr(rule, "name", "") or getattr(rule, "key", "") or "未命名规则")


def rule_mode(rule) -> str:  # noqa: ANN001
    """规则模式展示：优先后端的 `kind`（xpath/api），否则读 searchMode。"""
    kind = str(getattr(rule, "kind", "") or "").strip().lower()
    if kind in ("xpath", "api"):
        return kind
    kind = str(getattr(rule, "search_mode", "") or "xpath").strip().lower()
    return kind if kind in ("xpath", "api") else "xpath"


def rule_source_label(rule) -> str:  # noqa: ANN001
    key = str(getattr(rule, "key", "") or "")
    if key.startswith("sub:"):
        return "订阅"
    if bool(getattr(rule, "builtin", False)):
        return "内置"
    return "用户"


def rule_warning(rule) -> str:  # noqa: ANN001
    """规则的诚实边界提示；返回空串表示没有已知限制。

    三种情况必须显式说出来，不能让用户对着"搜不到"猜：
    1. `api` 高于客户端 → 灰显 + "该规则需要更新的客户端"；
    2. `deprecated` → "已退役"（KazumiRules 用 CI 打这个标记）；
    3. `useWebview` → "依赖网页渲染，可能解析失败"（我们无浏览器内核）。
    """
    parts: list[str] = []
    if not rule_supported(rule):
        parts.append(f"该规则需要更新的客户端（api {rule_api_level(rule)}）")
    if bool(getattr(rule, "deprecated", False)):
        parts.append("已退役")
    if bool(getattr(rule, "use_webview", False)):
        parts.append("依赖网页渲染，可能解析失败")
    return "；".join(parts)


def rule_search_url(rule, keyword: str) -> str:  # noqa: ANN001
    """规则搜索 URL（把 `@keyword` 换成关键词；替换不了就原样返回，便于试跑面板指出问题）。"""
    template = str(getattr(rule, "search_url", "") or "")
    if not template:
        return ""
    try:
        from modu_workbench.core.anime.rules import substitute_url

        return substitute_url(template, {"keyword": keyword})
    except Exception:  # noqa: BLE001  后端 rules 还没就绪
        from urllib.parse import quote

        return template.replace("@keyword", quote(keyword, safe=""))


def search_hits(group) -> list:  # noqa: ANN001
    return list(getattr(group, "sources", None) or [])


def series_of(payload):  # noqa: ANN001, ANN201
    """从任意载荷里取出"番剧"对象（SearchGroup / SeriesDetail / AnimeSeries / SearchHit）。"""
    if payload is None:
        return None
    for name in ("series",):
        found = getattr(payload, name, None)
        if found is not None:
            return found
    if hasattr(payload, "sources"):
        hits = search_hits(payload)
        return series_of(hits[0]) if hits else None
    return payload


def detail_roads(detail) -> list:  # noqa: ANN001
    return list(getattr(detail, "roads", None) or [])


def road_episodes(road) -> list:  # noqa: ANN001
    return list(getattr(road, "episodes", None) or [])


def episode_label(episode, fallback_index: int = 0) -> str:  # noqa: ANN001
    label = str(getattr(episode, "label", "") or getattr(episode, "name", "") or "").strip()
    if label:
        return label
    number = getattr(episode, "number", 0) or (fallback_index + 1)
    return f"第 {number} 集"


def episode_key(episode) -> str:  # noqa: ANN001
    key = str(getattr(episode, "key", "") or "")
    if key:
        return key
    return f"{getattr(episode, 'road_index', 0)}:{getattr(episode, 'index', 0)}"


def first_episode(detail) -> object | None:  # noqa: ANN001
    for road in detail_roads(detail):
        episodes = road_episodes(road)
        if episodes:
            return episodes[0]
    return None


@dataclass
class ResumeOnlyProgress:
    """只有播放位置、没有时长的**降级**进度：能标"在看"，但算不出百分比。

    后端冻结接口只承诺 `resume_position()`（毫秒），拿不到 duration 就没有百分比。
    这里不编造数字：`percent` 恒为 0，界面显示"在看"而不是一个假的百分比。
    """

    episode_key: str = ""
    position_ms: int = 0
    duration_ms: int = 0
    watched: bool = False

    @property
    def percent(self) -> int:
        return 0

    @property
    def finished(self) -> bool:
        return bool(self.watched)


_PROGRESS_METHODS = ("series_progress", "progress_map", "progress_for")


def _progress_key(item) -> str:  # noqa: ANN001
    return str(getattr(item, "episode_key", "") or "")


def _same_series(candidate, series) -> bool:  # noqa: ANN001
    if candidate is None or series is None:
        return False
    for left, right in ((getattr(candidate, "series_id", None), getattr(series, "id", None)),
                        (getattr(candidate, "id", None), getattr(series, "id", None)),
                        (getattr(candidate, "key", None), getattr(series, "key", None))):
        if left not in (None, "", 0) and left == right:
            return True
    return False


def progress_map(library, series) -> dict:  # noqa: ANN001
    """取某部番剧的按集进度（键 = 剧集 key）。

    后端 `AnimeLibrary` 的冻结接口没承诺"批量进度"，所以这里按顺序降级：

    1. `series_progress()/progress_map()/progress_for()`（最完整，直接给 dict）；
    2. `continue_watching()` 里与本剧匹配的条目（拿得到时长 → 有百分比）；
    3. 逐集 `resume_position()`（只有位置 → 标"在看"，没有百分比）。

    三条都拿不到就返回空字典：**界面显示"没有进度"，不假装有**。
    """
    if library is None or series is None:
        return {}
    for name in _PROGRESS_METHODS:
        getter = getattr(library, name, None)
        if not callable(getter):
            continue
        try:
            found = getter(series)
        except Exception:  # noqa: BLE001  后端实现报错不该影响界面
            continue
        if isinstance(found, dict) and found:
            return {str(key): value for key, value in found.items()}
        if isinstance(found, (list, tuple)) and found:
            items = {_progress_key(item): item for item in found if _progress_key(item)}
            if items:
                return items
    items: dict = {}
    try:
        entries = library.continue_watching() or []
    except Exception:  # noqa: BLE001
        entries = []
    for entry in entries:
        owner, progress = entry, entry
        if isinstance(entry, (list, tuple)) and len(entry) >= 3:
            owner, progress = entry[0], entry[2]
        if not _same_series(owner, series):
            continue
        key = _progress_key(progress)
        if key:
            items[key] = progress
    return items


def collect_progress(library, series, episodes: list) -> dict:  # noqa: ANN001
    """一次拿全"剧集网格要用的进度"：批量优先，逐集兜底。

    逐集兜底只在**批量接口完全没给这部番剧的进度**时逐集问 `resume_position()`，
    避免后端已经给了数据还去多发 N 次查询。
    """
    items = progress_map(library, series)
    missing = [item for item in episodes if episode_key(item) not in items]
    if not missing:
        return items
    resume = getattr(library, "resume_position", None)
    if not callable(resume):
        return items
    for episode in missing:
        key = episode_key(episode)
        try:
            position = int(resume(series, episode) or 0)
        except Exception:  # noqa: BLE001  后端未实现/签名不同 → 放弃兜底，不猜
            return items
        if position > 0:
            items[key] = ResumeOnlyProgress(episode_key=key, position_ms=position)
    return items


def progress_state(progress) -> tuple[bool, int]:  # noqa: ANN001
    """把任意进度对象归一成 `(是否看完, 百分比)`（未知形态一律按"没进度"处理）。"""
    if progress is None:
        return False, 0
    watched = bool(getattr(progress, "watched", False) or getattr(progress, "finished", False))
    percent = getattr(progress, "percent", 0)
    try:
        percent = int(percent)
    except (TypeError, ValueError):
        percent = 0
    return watched or percent >= 100, max(0, min(100, percent))


# --------------------------------------------------------------------------- 海报卡


class PosterCard(QFrame):
    """作品卡：海报 + 标题 + 副标题 + 徽章 + 四个动作。

    `payload` 是"跟着这张卡走"的数据（SearchGroup / AnimeSeries），
    所有信号都把它带出去，界面层不用再去表里反查 —— 搜索页是**边搜边插卡**的，
    用行号反查在并发插入时必然错位。
    """

    clicked = Signal(object)
    playRequested = Signal(object)
    detailRequested = Signal(object)
    followRequested = Signal(object)
    downloadRequested = Signal(object)

    def __init__(self, payload=None, *, title: str = "", subtitle: str = "",  # noqa: ANN001
                 cover: str = "", badges: list[str] | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("sectionCard")
        self.setProperty("flat", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.payload = payload
        self.title = title
        self.cover_url = cover
        #: 同一作品命中的全部来源（SearchHit），搜索页合并同名片时往里追加
        self.sources: list = []
        self.badges: list[str] = list(badges or [])

        layout = QHBoxLayout(self)
        layout.setContentsMargins(CARD_PADDING, CARD_PADDING, CARD_PADDING, CARD_PADDING)
        layout.setSpacing(SPACE["md"])

        self.cover = QLabel("🎞")
        self.cover.setObjectName("animePoster")
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setFixedSize(POSTER_W, POSTER_H)
        self.cover.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.cover)

        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(SPACE["xs"])

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(ROW_GAP)
        self.title_label = QLabel(title or "未命名")
        self.title_label.setObjectName("cardTitle")
        self.title_label.setWordWrap(True)
        head.addWidget(self.title_label, 1)
        self._badge_labels: list[QLabel] = []
        for text in self.badges:
            head.addWidget(self._make_badge(text))
        head.addStretch(0)
        texts.addLayout(head)

        self.subtitle_label = QLabel(subtitle or "")
        self.subtitle_label.setObjectName("cardTagline")
        self.subtitle_label.setWordWrap(True)
        texts.addWidget(self.subtitle_label)

        self.source_label = QLabel("")
        self.source_label.setObjectName("readerStatus")
        self.source_label.setWordWrap(True)
        texts.addWidget(self.source_label)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(ROW_GAP)
        self.play_button = ghost_button("▶ 播放")
        self.play_button.clicked.connect(lambda: self.playRequested.emit(self.payload))
        self.detail_button = ghost_button("详情")
        self.detail_button.clicked.connect(lambda: self.detailRequested.emit(self.payload))
        self.follow_button = ghost_button("追番")
        self.follow_button.clicked.connect(lambda: self.followRequested.emit(self.payload))
        self.download_button = ghost_button("⬇ 下载")
        self.download_button.clicked.connect(lambda: self.downloadRequested.emit(self.payload))
        for button in (self.play_button, self.detail_button,
                       self.follow_button, self.download_button):
            actions.addWidget(button)
        actions.addStretch(1)
        texts.addLayout(actions)
        layout.addLayout(texts, 1)

        if self.cover_url:
            load_cover(self.cover, self.cover_url)

    # ------------------------------------------------------------------ 展示

    def _make_badge(self, text: str) -> QLabel:
        badge = chip(text)
        badge.setObjectName("statChip")
        self._badge_labels.append(badge)
        return badge

    def add_badge(self, text: str) -> None:
        """加一个徽章（同一文案只加一次，避免刷新时越叠越多）。"""
        if not text or text in self.badge_texts():
            return
        self.badges.append(text)
        head = self.layout().itemAt(1).layout().itemAt(0)
        badge = self._make_badge(text)
        head.insertWidget(head.count() - 1, badge)

    def badge_texts(self) -> list[str]:
        return [label.text() for label in self._badge_labels]

    def clear_badges(self, keep: tuple[str, ...] = ()) -> None:
        """清掉运行时徽章（例如"有新集"），保留固定徽章。"""
        for label in list(self._badge_labels):
            if label.text() in keep:
                continue
            self._badge_labels.remove(label)
            self.badges = [item for item in self.badges if item != label.text()]
            label.setParent(None)
            label.deleteLater()

    def add_source(self, hit, rule_name: str = "") -> None:  # noqa: ANN001
        """记录一个来源；来源摘要写在卡片副标题下方（多个来源逐个追加）。"""
        if hit is not None:
            self.sources.append(hit)
        label = self._source_text(hit, rule_name)
        if not label:
            return
        current = self.source_label.text()
        if label in current:
            return
        self.source_label.setText(f"{current} · {label}" if current else f"来源：{label}")

    @staticmethod
    def _source_text(hit, rule_name: str = "") -> str:  # noqa: ANN001
        if hit is None:
            return rule_name
        name = rule_name or str(getattr(hit, "rule_name", "") or getattr(hit, "rule_key", ""))
        note = str(getattr(hit, "note", "") or "")
        year = str(getattr(hit, "year", "") or "")
        parts = [item for item in (name, year, note) if item]
        return " · ".join(parts)

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(text or "")

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
                event.position().toPoint()):
            self.clicked.emit(self.payload)
        super().mouseReleaseEvent(event)


# --------------------------------------------------------------------------- 海报异步加载

_COVERS: dict[str, QPixmap] = {}
_COVER_MANAGER = None


def _cover_manager():  # noqa: ANN202
    """按需创建 QNetworkAccessManager（不联网时零开销，测试里不会触发）。"""
    global _COVER_MANAGER
    if _COVER_MANAGER is None:
        from PySide6.QtNetwork import QNetworkAccessManager

        _COVER_MANAGER = QNetworkAccessManager()
    return _COVER_MANAGER


def load_cover(label: QLabel, url: str, referer: str = "") -> None:
    """异步加载海报：命中缓存立即设置，否则发起一次请求（失败静默）。

    为什么不用同步下载：搜索页是**边搜边插卡**的，同步抓图会把 UI 线程按在网络上，
    用户看到的是"搜到一半界面死了"。图片只是装饰，失败就保留 🎞 占位。
    """
    text = (url or "").strip()
    if not text.lower().startswith(("http://", "https://")):
        return
    cached = _COVERS.get(text)
    if cached is not None:
        if not cached.isNull():
            label.setPixmap(_scaled(cached))
        return
    try:
        from PySide6.QtNetwork import QNetworkRequest
    except Exception:  # noqa: BLE001  QtNetwork 不可用：海报只是装饰，直接放弃
        return
    request = QNetworkRequest(QUrl(text))
    request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                         QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
    if referer:
        request.setRawHeader(b"Referer", referer.encode("utf-8"))
    try:
        reply = _cover_manager().get(request)
    except Exception:  # noqa: BLE001
        return
    reply.finished.connect(lambda r=reply, target=label, key=text: _on_cover(r, target, key))


def _on_cover(reply, label: QLabel, key: str) -> None:  # noqa: ANN001
    try:
        data = bytes(reply.readAll())
        pixmap = QPixmap()
        pixmap.loadFromData(data)
        _COVERS[key] = pixmap
        if not pixmap.isNull():
            label.setPixmap(_scaled(pixmap))
    except Exception:  # noqa: BLE001  控件已销毁/数据损坏：装饰失败不报错
        pass
    finally:
        try:
            reply.deleteLater()
        except Exception:  # noqa: BLE001
            pass


def _scaled(pixmap: QPixmap) -> QPixmap:
    return pixmap.scaled(POSTER_W, POSTER_H, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                         Qt.TransformationMode.SmoothTransformation)


# --------------------------------------------------------------------------- 线路 / 追番


class RoadSelector(QComboBox):
    """线路下拉：换线路只改"看哪条线路"，不动剧集数据。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumWidth(160)
        self.setToolTip("同一部番剧的多条线路（不同规则/不同播放源）")

    def set_roads(self, roads: list) -> None:
        current = self.currentData()
        self.blockSignals(True)
        self.clear()
        for road in roads:
            index = int(getattr(road, "index", self.count()))
            name = str(getattr(road, "label", "") or getattr(road, "name", "")
                       or f"线路 {index + 1}")
            count = int(getattr(road, "count", 0) or len(road_episodes(road)))
            self.addItem(f"{name}（{count} 集）", index)
        target = self.findData(current)
        self.setCurrentIndex(target if target >= 0 else 0)
        self.blockSignals(False)

    def current_road(self, detail) -> object | None:  # noqa: ANN001
        index = self.currentData()
        for road in detail_roads(detail):
            if int(getattr(road, "index", -1)) == int(index if index is not None else 0):
                return road
        roads = detail_roads(detail)
        return roads[0] if roads else None


class FollowButton(QPushButton):
    """追番按钮：checkable，文案随状态切换（追番 / 已追番）。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__("☆ 追番", parent)
        self.setCheckable(True)
        self.setObjectName("ghostButton")
        self.toggled.connect(self._refresh_text)

    def _refresh_text(self, checked: bool) -> None:
        self.setText("★ 已追番" if checked else "☆ 追番")

    def set_followed(self, followed: bool) -> None:
        self.blockSignals(True)
        self.setChecked(bool(followed))
        self.blockSignals(False)
        self._refresh_text(bool(followed))


# --------------------------------------------------------------------------- 剧集网格


class EpisodeCell(QPushButton):
    """剧集格子：显示集名，看完打 ✓，在看显示百分比。"""

    playRequested = Signal(object)

    def __init__(self, episode, parent: QWidget | None = None):  # noqa: ANN001
        super().__init__(episode_label(episode), parent)
        self.episode = episode
        self.setObjectName("ghostButton")
        self.setMinimumWidth(84)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.clicked.connect(lambda: self.playRequested.emit(self.episode))

    def set_progress(self, watched: bool, percent: int) -> None:
        base = episode_label(self.episode)
        if watched:
            text = f"{base} ✓"
        elif percent > 0:
            text = f"{base} {percent}%"
        else:
            text = base
        self.setText(text)
        tip = [base]
        if watched:
            tip.append("已看完")
        elif percent > 0:
            tip.append(f"看到 {percent}%")
        self.setToolTip(" · ".join(tip))

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.playRequested.emit(self.episode)
        super().mouseDoubleClickEvent(event)


class EpisodeGrid(QWidget):
    """剧集网格：线路内所有剧集；双击播放、右键两条自救动作。"""

    playRequested = Signal(object)             # episode
    externalPlayerRequested = Signal(object)   # episode：用外部播放器打开
    reparseRequested = Signal(object)          # episode：重新解析地址

    def __init__(self, parent: QWidget | None = None, *, columns: int = 8):
        super().__init__(parent)
        self._columns = max(1, int(columns))
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(ROW_GAP)
        self._grid.setVerticalSpacing(ROW_GAP)
        self._cells: list[EpisodeCell] = []

    # ------------------------------------------------------------------ 数据

    def set_episodes(self, episodes: list, progress: dict | None = None) -> None:
        self.clear()
        for index, episode in enumerate(episodes):
            cell = EpisodeCell(episode, self)
            watched, percent = progress_state((progress or {}).get(episode_key(episode)))
            cell.set_progress(watched, percent)
            cell.playRequested.connect(self.playRequested)
            cell.customContextMenuRequested.connect(
                lambda pos, item=episode, widget=cell: self._show_menu(item, widget, pos))
            self._grid.addWidget(cell, index // self._columns, index % self._columns)
            self._cells.append(cell)
        self.setVisible(bool(self._cells))

    def clear(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._cells.clear()

    def cells(self) -> list[EpisodeCell]:
        return list(self._cells)

    def cell_for(self, episode) -> EpisodeCell | None:  # noqa: ANN001
        key = episode_key(episode)
        for cell in self._cells:
            if episode_key(cell.episode) == key:
                return cell
        return None

    # ------------------------------------------------------------------ 右键

    def _show_menu(self, episode, widget: EpisodeCell, pos) -> None:  # noqa: ANN001
        menu = QMenu(widget)
        external = menu.addAction("用外部播放器打开")
        reparse = menu.addAction("重新解析地址")
        chosen = menu.exec(widget.mapToGlobal(pos))
        if chosen is external:
            self.externalPlayerRequested.emit(episode)
        elif chosen is reparse:
            self.reparseRequested.emit(episode)


# --------------------------------------------------------------------------- 空状态


def set_empty_hint(state, text: str) -> None:  # noqa: ANN001
    """更新 `EmptyState` 的说明文字。

    `EmptyState` 只接受构造时的 hint，而搜索页的下一步提示是**随状态变化**的
    （没有规则时要说"先去规则页导入"）。这里按 objectName 找到那个 QLabel，
    找不到就静默跳过（宁可提示旧一点，也不要因为找不到控件抛异常）。
    """
    label = state.findChild(QLabel, "emptyHint")
    if label is not None:
        label.setText(text)


__all__ = [
    "EpisodeCell",
    "EpisodeGrid",
    "FollowButton",
    "POSTER_H",
    "POSTER_W",
    "PosterCard",
    "ResumeOnlyProgress",
    "RoadSelector",
    "SETTINGS_APP",
    "SETTINGS_ORG",
    "anime_download_dir_pref",
    "anime_settings",
    "collect_progress",
    "detail_roads",
    "episode_key",
    "episode_label",
    "first_episode",
    "load_cover",
    "normalize_title_safe",
    "open_in_browser",
    "progress_map",
    "progress_state",
    "road_episodes",
    "rule_api_level",
    "rule_label",
    "rule_mode",
    "rule_search_url",
    "rule_source_label",
    "rule_supported",
    "rule_warning",
    "search_hits",
    "series_of",
    "set_empty_hint",
    "setting_bool",
    "setting_int",
    "supported_api_level",
]
