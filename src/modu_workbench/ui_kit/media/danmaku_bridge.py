"""弹幕接线：读取设置 → 匹配 → 取弹幕 → 交给播放器。

**为什么住在共享层（`ui_kit/media/`）而不是影视板块里**：架构闸门
（`tests/architecture/test_architecture.py`）禁止 `boards/*` 引用 `core/<别的板块>`，
而弹弹play 客户端住在 `services/dandanplay.py`（v2.0.0 从 `core/anime/` 迁出）。
影视板块要用它就必须经共享层 —— 这与播放器上移到 `ui_kit/media/` 是同一个理由。

**只做「能看」**，三条边界：

1. 数据源**只有弹弹play**（Kazumi 另外接了 BiliBili/Gamer，但那两条没有公开文档
   与稳定约定，我们不猜）；
2. **只读**：发送弹幕属受限接口，官方未开放 → 不做；
3. 拿不到就如实说（图省事静默失败会让用户以为"这集就是没弹幕"）。

渲染与解析在 `ui_kit.media.danmaku`（`DanmakuItem` 等），这里补的是
"标题+集数 → episodeId → XML → 落盘缓存"这一段取数逻辑。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from .danmaku import (
    DanmakuItem,
    load_danmaku_file,
    save_danmaku_file,
)

#: 弹幕缓存的目录名（数据目录/video/danmaku）
CACHE_DIR_NAME = "danmaku"


def danmaku_settings():  # noqa: ANN201
    """应用设置（与「设置 → 影视 → 弹幕」共用同一组 QSettings 键）。"""
    from PySide6.QtCore import QSettings

    return QSettings("ModuWorkbench", "modu-workbench")


def danmaku_enabled() -> bool:
    """弹幕总开关（默认关：第一次由用户显式开启，避免"打开就刷屏"）。"""
    return bool(danmaku_settings().value("video/danmaku_enabled", False, type=bool))


def danmaku_block_words() -> str:
    return str(danmaku_settings().value("video/danmaku_block_words", "", type=str) or "")


def danmaku_opacity() -> int:
    try:
        return int(danmaku_settings().value("video/danmaku_opacity", 80) or 80)
    except (TypeError, ValueError):
        return 80


def danmaku_speed_seconds() -> float:
    try:
        return float(danmaku_settings().value("video/danmaku_speed", 8) or 8)
    except (TypeError, ValueError):
        return 8.0


def danmaku_max_lines() -> int:
    try:
        return int(danmaku_settings().value("video/danmaku_max_lines", 0) or 0)
    except (TypeError, ValueError):
        return 0


def danmaku_dedupe() -> bool:
    return bool(danmaku_settings().value("video/danmaku_dedupe", False, type=bool))


def danmaku_cache_dir() -> Path:
    from modu_workbench.core.platform.paths import video_dir

    path = video_dir() / CACHE_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def episode_number_of(episode) -> int:  # noqa: ANN001
    """从剧集名里解析集数（弹幕匹配要用）；解析不出返回 0。

    v2.0.0 起解析逻辑在 `core/platform/textutil.py`（共享层），
    不再依赖已移除的 `core/anime/models`。
    """
    if episode is None:
        return 0
    from modu_workbench.core.platform.textutil import parse_episode_number

    return parse_episode_number(getattr(episode, "name", "") or "")


def cache_path_for(title: str, episode_label: str = "") -> Path:
    """缓存文件名：标题 + 集数（用归一化后的短 hash，避免超长文件名）。"""
    import hashlib

    from modu_workbench.core.platform.textutil import normalize_title

    key = f"{normalize_title(title)}|{normalize_title(episode_label)}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return danmaku_cache_dir() / f"{digest}.xml"


@dataclass
class DanmakuResult:
    """一次弹幕获取的结果（含给用户看的原因）。"""

    items: list[DanmakuItem] = field(default_factory=list)
    episode_id: int = 0
    matched_title: str = ""
    from_cache: bool = False
    note: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.items)


class DanmakuFetcher:
    """把「标题 + 集数」变成弹幕列表（带磁盘缓存）。"""

    def __init__(self, *, client=None, cache_dir: Path | None = None) -> None:  # noqa: ANN001
        self._client = client
        self._cache_dir = cache_dir

    # ------------------------------------------------------------------ 客户端

    def client(self):  # noqa: ANN201
        """惰性构造弹弹play 客户端（未配置 AppId/AppSecret 时也返回对象，只是不可用）。

        凭证只读「设置 → 影视 → 弹幕」。v2.0.0 移除了动漫设置页，
        但**保留读取旧键 `anime/danmaku_*` 的兜底**：v1.1.0 用户可能只在那里填过，
        升级后不该突然"弹幕用不了"。
        """
        if self._client is not None:
            return self._client
        settings = danmaku_settings()

        def _pref(new_key: str, legacy_key: str) -> str:
            return (str(settings.value(new_key, "", type=str) or "")
                    or str(settings.value(legacy_key, "", type=str) or ""))

        app_id = _pref("video/danmaku_app_id", "anime/danmaku_app_id")
        secret = _pref("video/danmaku_app_secret", "anime/danmaku_app_secret")
        from modu_workbench.services.dandanplay import DandanplayClient

        self._client = DandanplayClient(
            app_id=app_id, app_secret=secret,
            cache_dir=self._cache_dir or danmaku_cache_dir(),
        )
        return self._client

    # ------------------------------------------------------------------ 取数

    def fetch(self, title: str, episode_label: str = "", *,
              episode_number: int = 0, year: str = "",
              cancel: threading.Event | None = None) -> DanmakuResult:
        """取弹幕：**先看本地缓存**，再走"搜索→匹配→取弹幕"。失败只返回原因。"""
        cache = cache_path_for(title, episode_label)
        cached = load_danmaku_file(cache)
        if cached:
            return DanmakuResult(items=cached, from_cache=True,
                                 note=f"已从本地缓存读取 {len(cached)} 条弹幕")

        client = self.client()
        if not getattr(client, "configured", False):
            return DanmakuResult(
                note="未配置弹弹play 的 AppId/AppSecret（设置 → 影视 → 弹幕）："
                     "可在 dev.dandanplay.com 申请后填入；也可手动导入本地弹幕 XML")
        if cancel is not None and cancel.is_set():
            return DanmakuResult(note="已取消")

        try:
            match = client.match_series(title, episode_label,
                                        episode_number=episode_number, year=year)
        except Exception as error:  # noqa: BLE001  弹幕是可选增强，失败不影响播放
            return DanmakuResult(note=f"弹幕匹配失败：{error}")
        if not getattr(match, "ok", False):
            return DanmakuResult(note=match.note or "没有匹配到对应的番剧/剧集")

        try:
            items = client.fetch_items(match.episode_id)
        except Exception as error:  # noqa: BLE001
            return DanmakuResult(note=f"弹幕获取失败：{error}",
                                 episode_id=match.episode_id,
                                 matched_title=match.anime_title)
        if not items:
            # 客户端自己会把"为什么没有"写在 last_note 里（限流/403/空弹幕库）
            note = getattr(client, "last_note", "") or "该集没有弹幕数据"
            return DanmakuResult(note=note, episode_id=match.episode_id,
                                 matched_title=match.anime_title)
        try:
            save_danmaku_file(cache, items)
        except OSError:
            pass
        return DanmakuResult(
            items=list(items), episode_id=match.episode_id,
            matched_title=match.anime_title,
            note=f"已匹配「{match.anime_title}」{match.episode_title}，{len(items)} 条弹幕")


def import_danmaku_file(path: str | Path) -> DanmakuResult:
    """导入本地弹幕 XML（手动兜底：匹配不上时用户自己找一份）。"""
    target = Path(path)
    items = load_danmaku_file(target)
    if not items:
        return DanmakuResult(note=f"「{target.name}」里没有解析出弹幕（需要标准 XML 格式）")
    return DanmakuResult(items=items, note=f"已导入 {len(items)} 条弹幕（{target.name}）")


def export_danmaku_file(path: str | Path, items: list[DanmakuItem]) -> Path:
    """导出当前弹幕为 XML（与主流播放器互导）。"""
    return save_danmaku_file(path, items)


__all__ = [
    "CACHE_DIR_NAME", "DanmakuFetcher", "DanmakuResult", "cache_path_for",
    "danmaku_block_words", "danmaku_cache_dir", "danmaku_dedupe", "danmaku_enabled",
    "danmaku_max_lines", "danmaku_opacity", "danmaku_speed_seconds",
    "danmaku_settings", "episode_number_of", "export_danmaku_file",
    "import_danmaku_file",
]
