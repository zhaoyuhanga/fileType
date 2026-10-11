"""媒体代理配置：默认值 + 从设置读取（Qt 不可用时静默回退默认值）。

为什么单独一个模块：代理运行在**服务线程**里，可能早于/独立于 Qt 环境
（测试、ffmpeg 子进程、无界面自检），因此读取设置绝不能硬依赖 QSettings。
这里按「数据库 app_settings → QSettings → 默认值」三级回退。
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, replace
from pathlib import Path

# ---------------------------------------------------------------- 默认值
#: 分片缓存配额（MB）—— 与 docs/VIDEO_ANIME_PLAN.md 3.4 的 video/cache_quota_mb 一致
DEFAULT_CACHE_QUOTA_MB = 2048
#: 清单重写时顺手预取的分片数（0 = 关闭）
DEFAULT_PREFETCH_SEGMENTS = 3
#: 同时处理的代理请求上限（防止十几个分片一起打满出口带宽）
DEFAULT_PROXY_CONCURRENCY = 16

#: 单个分片允许的最大缓存体积（超过则不缓存，直接透传）
MAX_SEGMENT_BYTES = 64 * 1024 * 1024
#: 清单允许的最大体积（超过视为"不是清单"，不缓存）
MAX_PLAYLIST_BYTES = 4 * 1024 * 1024
#: 单张图片允许的最大缓存体积
MAX_IMAGE_BYTES = 16 * 1024 * 1024

#: 清单短缓存 TTL（秒）——同一份清单在 TTL 内直接返回上次重写结果
PLAYLIST_TTL_SECONDS = 3.0
#: 分片下发给播放器时的浏览器缓存时间（清单必须 no-store，见 V1-m）
SEGMENT_MAX_AGE = 300
#: 图片下发给界面的浏览器缓存时间
IMAGE_MAX_AGE = 86400

#: 分层超时：(连接, 读取)
TIMEOUT_PLAYLIST = (5.0, 15.0)
TIMEOUT_SEGMENT = (5.0, 20.0)
TIMEOUT_IMAGE = (5.0, 10.0)
TIMEOUT_DIRECT = (5.0, 60.0)

#: 分层重试：分片只快速重试 1 次（失败立刻 502，让播放器自己重试更聪明）
RETRY_PLAYLIST = 3
RETRY_SEGMENT = 1
RETRY_IMAGE = 2
#: 分片快速重试的退避（秒）—— 绝不阻塞播放线程 1.8s
SEGMENT_RETRY_BACKOFF = 0.15

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def video_cache_dir() -> Path:
    """分片缓存目录（可用 MODU_VIDEO_CACHE_DIR 覆盖，便于测试与便携模式）。"""
    override = os.environ.get("MODU_VIDEO_CACHE_DIR")
    if override:
        path = Path(override)
    else:
        from modu_workbench.core.platform.paths import video_dir

        path = video_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_setting(key: str, default: str) -> str:
    """按「数据库 → QSettings → 默认值」读取一个 `video/<key>` 设置。"""
    db_path = os.environ.get("MODU_DATA_DIR") or ""
    try:  # ① 应用库（打包环境与无界面自检都能用；库不存在时静默跳过）
        from modu_workbench.core.platform.paths import app_db_path

        if db_path or Path(app_db_path()).is_file():
            import sqlite3

            conn = sqlite3.connect(app_db_path())
            try:
                row = conn.execute(
                    "SELECT value FROM app_settings WHERE key = ?", (f"video/{key}",)
                ).fetchone()
            finally:
                conn.close()
            if row and row[0] not in (None, ""):
                return str(row[0])
    except Exception:  # noqa: BLE001  设置读不到就用默认值，绝不影响播放
        pass
    try:  # ② Qt 设置（界面上刚改完、尚未落库时命中这里）
        from PySide6.QtCore import QSettings

        value = QSettings("ModuWorkbench", "modu-workbench").value(f"video/{key}", "")
        if value not in (None, ""):
            return str(value)
    except Exception:  # noqa: BLE001
        pass
    return default


def _read_int(key: str, default: int, *, low: int, high: int) -> int:
    try:
        value = int(str(_read_setting(key, str(default))).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


@dataclass(frozen=True)
class ProxyConfig:
    """代理运行时参数（不可变；变更后调用 `reset_config()` 重新读取）。"""

    cache_dir: Path
    cache_quota_bytes: int
    prefetch_segments: int
    concurrency: int
    playlist_ttl: float
    enabled: bool = True

    @property
    def cache_quota_mb(self) -> int:
        return self.cache_quota_bytes // (1024 * 1024)


#: 缓冲档位 →（缓存配额 MB, 预取片数）
BUFFER_MODES: dict[str, tuple[int, int]] = {
    "low": (256, 2),        # 省流
    "balanced": (1024, 3),  # 均衡（默认）
    "high": (2048, 6),      # 高质
}


def buffer_mode_defaults(mode: str) -> tuple[int, int]:
    """缓冲档位预设（见 docs/VIDEO_ANIME_PLAN.md 3.1.5）。未知档位按均衡处理。"""
    return BUFFER_MODES.get((mode or "").strip().lower(), BUFFER_MODES["balanced"])


_config: ProxyConfig | None = None
_config_lock = threading.Lock()


def load_config() -> ProxyConfig:
    """从设置构造配置（每次调用都重新读，便于设置页改完即生效）。"""
    mode = _read_setting("buffer_mode", "balanced").strip().lower()
    mode_quota_mb, mode_prefetch = buffer_mode_defaults(mode)
    quota_mb = _read_int("cache_quota_mb", mode_quota_mb, low=256, high=20480)
    # 显式设置过 cache_quota_mb 时以它为准（档位只提供默认值）
    prefetch = _read_int("prefetch_segments", mode_prefetch, low=0, high=10)
    concurrency = _read_int("proxy_concurrency", DEFAULT_PROXY_CONCURRENCY, low=2, high=32)
    return ProxyConfig(
        cache_dir=video_cache_dir(),
        cache_quota_bytes=quota_mb * 1024 * 1024,
        prefetch_segments=prefetch,
        concurrency=concurrency,
        playlist_ttl=PLAYLIST_TTL_SECONDS,
    )


def get_config(*, refresh: bool = False) -> ProxyConfig:
    """进程级配置单例（服务线程高频调用，避免每次都读库）。"""
    global _config
    if _config is None or refresh:
        with _config_lock:
            if _config is None or refresh:
                _config = load_config()
    return _config


def set_config(config: ProxyConfig) -> None:
    """仅供测试：显式注入配置。"""
    global _config
    with _config_lock:
        _config = config


def reset_config() -> None:
    """下一次 `get_config()` 重新从设置读取。"""
    global _config
    with _config_lock:
        _config = None


def with_overrides(**kwargs) -> ProxyConfig:  # noqa: ANN003
    """在现有配置上做局部覆盖（测试与临时降级用）。"""
    return replace(get_config(), **kwargs)


__all__ = [
    "BUFFER_MODES", "DEFAULT_CACHE_QUOTA_MB", "DEFAULT_PREFETCH_SEGMENTS",
    "DEFAULT_PROXY_CONCURRENCY", "IMAGE_MAX_AGE", "MAX_IMAGE_BYTES",
    "MAX_PLAYLIST_BYTES", "MAX_SEGMENT_BYTES", "PLAYLIST_TTL_SECONDS",
    "ProxyConfig", "RETRY_IMAGE", "RETRY_PLAYLIST", "RETRY_SEGMENT",
    "SEGMENT_MAX_AGE", "SEGMENT_RETRY_BACKOFF", "TIMEOUT_DIRECT",
    "TIMEOUT_IMAGE", "TIMEOUT_PLAYLIST", "TIMEOUT_SEGMENT", "UA",
    "buffer_mode_defaults", "get_config", "load_config", "reset_config",
    "set_config", "video_cache_dir", "with_overrides",
]
