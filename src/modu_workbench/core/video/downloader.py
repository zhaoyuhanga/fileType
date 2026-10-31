"""墨软影视下载器：解析地址 → 抓取 m3u8 分片 → 合流为本地文件 → 入库。

支持两类内容：
- **m3u8/HLS**（第三方采集站的常态）：按分片下载后合流。
  优先用 ffmpeg 直接 `-c copy` 拉流封装（最稳，且能处理加密流）；
  ffmpeg 不可用时退化为「抓分片 → 拼接为 .ts」（仍可本地播放）。
- **直链文件**（mp4/mkv…，公共领域源常用）：流式下载 + 基础校验。

下载在调用方线程（Qt 里是后台 QThread）执行，通过回调汇报进度，支持取消。
失败时由 registry 换源重试（用户诉求第 6 条）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, List
from urllib.parse import urlsplit

import requests

from .hls import parse_m3u8, segment_iv
from .models import Episode, Quality, RemoteVideo, safe_filename
from .sources import ResolvedPlay, VideoRegistry, describe_network_error, guess_referer

ProgressFn = Callable[[int, int, str], None]  # written, total, message

# 直接下载的文件类型（非 HLS）
_VIDEO_MIME_EXT = {
    "video/mp4": ".mp4", "video/x-m4v": ".m4v", "video/quicktime": ".mov",
    "video/webm": ".webm", "video/x-matroska": ".mkv", "video/x-msvideo": ".avi",
    "video/mpeg": ".mpg", "video/x-flv": ".flv", "video/ts": ".ts",
    "application/vnd.apple.mpegurl": ".m3u8", "application/x-mpegurl": ".m3u8",
    "audio/mpeg": ".mp3", "audio/mp4": ".m4a",
}
_VIDEO_SUFFIXES = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v", ".ts", ".mpg", ".mpeg", ".mp3", ".m4a")
_MIN_VIDEO_BYTES = 64 * 1024        # 小于此值基本是错误页/占位文件


@dataclass
class DownloadResult:
    video: RemoteVideo
    episode: Episode | None = None
    quality: Quality | None = None
    path: str = ""
    ok: bool = False
    message: str = ""
    source: str = ""          # 实际使用的源（可能是换源后的）
    switched_from: str = ""   # 原源（发生换源时填写）
    note: str = ""
    duration_ms: int = 0
    size_bytes: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def title(self) -> str:
        return self.video.title

    @property
    def episode_label(self) -> str:
        return self.episode.name if self.episode else ""

    @property
    def quality_label(self) -> str:
        return self.quality.label if self.quality else ""


# --------------------------------------------------------------------------- 工具


def unique_path(directory: Path, filename: str) -> Path:
    """目标文件已存在时自动加序号，避免覆盖。"""
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for index in range(2, 1000):
        candidate = directory / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    return directory / f"{stem} ({os.getpid()}){suffix}"


def build_filename(video: RemoteVideo, episode: Episode | None = None, suffix: str = ".mp4") -> str:
    """统一的下载文件命名：「片名 (年份) 第N集.mp4」。"""
    parts = [video.title.strip() or "video"]
    if video.year:
        parts.append(f"({video.year})")
    if episode is not None and episode.name:
        parts.append(safe_filename(episode.name, fallback=""))
    name = " ".join(part for part in parts if part).strip()
    return f"{safe_filename(name)}{suffix}"


def guess_extension(url: str, headers: dict | None = None, fallback: str = ".mp4") -> str:
    content_type = str((headers or {}).get("Content-Type", "")).split(";")[0].strip().lower()
    if content_type in _VIDEO_MIME_EXT:
        return _VIDEO_MIME_EXT[content_type]
    suffix = os.path.splitext(urlsplit(url).path)[1].lower()
    if suffix in _VIDEO_SUFFIXES:
        return suffix
    return fallback


def looks_like_video(path: str | Path, content_type: str = "") -> bool:
    """判断下载结果是否真的是视频/音频（防止把错误页当成影片入库）。"""
    lowered = (content_type or "").lower()
    if lowered.startswith("text/") or "html" in lowered:
        return False
    file_path = Path(path)
    try:
        size = file_path.stat().st_size
    except OSError:
        return False
    if size < _MIN_VIDEO_BYTES:
        return False
    with open(file_path, "rb") as handle:
        head = handle.read(16)
    # MPEG-TS 同步字节 / MP4 的 ftyp / Matroska / AVI / FLV / ASF / WebM
    if head[:1] == b"\x47":
        return True
    if head[4:8] == b"ftyp" or head[:4] in (b"\x1a\x45\xdf\xa3", b"RIFF", b"FLV\x01", b"0&\xb2u", b"OggS"):
        return True
    return False


def find_ffmpeg() -> str | None:
    """复用转换引擎的 ffmpeg 定位逻辑（含 MODU_FFMPEG 环境变量与 PATH）。"""
    from modu_workbench.core.platform.media import find_ffmpeg as _find

    return _find()


def default_headers(url: str = "", referer: str = "") -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "*/*",
    }
    final_referer = referer or guess_referer(url)
    if final_referer:
        headers["Referer"] = final_referer
    return headers


# --------------------------------------------------------------------------- 单集下载


def download_episode(
    video: RemoteVideo,
    episode: Episode | None,
    dest_dir: str | Path,
    *,
    quality: Quality | None = None,
    on_progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
    registry: VideoRegistry | None = None,
    allow_cross_source: bool = True,
    timeout: float = 30.0,
    resolved: ResolvedPlay | None = None,
    pause_event: threading.Event | None = None,
) -> tuple[Path, ResolvedPlay]:
    """下载某一集到 dest_dir，返回 (保存路径, 解析结果)。

    解析地址时优先使用原源；失败（接口变更 / 线路失效 / 网络）时由 registry
    换源搜索同一部片继续尝试，因此调用方无需关心「某个源挂掉」。

    `pause_event` 为 set 时在四个检查点暂停并保留 `.parts/`（见
    `core/video/segment_downloader.py`），再次调用时会接着下。
    """
    if on_progress:
        label = f"{video.title} {episode.name if episode else ''}".strip()
        on_progress(0, 0, f"解析播放地址：{label}")

    if resolved is None:
        if registry is not None:
            resolved = registry.resolve(video, episode, quality, allow_cross_source=allow_cross_source)
        else:
            from .sources import SourceError, registry as default_registry

            active = default_registry().get(video.source)
            target_episode = episode or (video.episodes[0] if video.episodes else None)
            if target_episode is None:
                raise SourceError("该条目没有可下载的剧集")
            url = active.play_url(video, target_episode, quality) if active else target_episode.url
            resolved = ResolvedPlay(url=url, source=video.source, video=video,
                                    episode=target_episode, quality=quality,
                                    is_hls=url.lower().split("?", 1)[0].endswith(".m3u8"),
                                    referer=guess_referer(url))

    if resolved.switched and on_progress:
        on_progress(0, 0, resolved.note)

    tried: list[str] = [resolved.source]
    while True:
        try:
            return _download_resolved(resolved, dest_dir, video=video, on_progress=on_progress,
                                      cancel=cancel, timeout=timeout,
                                      pause_event=pause_event), resolved
        except _RetryableSourceError as error:
            # 注意：必须先于 RuntimeError 捕获（前者是后者的子类）
            # 离线/无源可换时**直接抛原始错误**，不要被 "NoneType is not iterable"
            # 这类二次错误盖掉真实原因（测试与用户看到的都该是根因）
            cause = error.cause if isinstance(error.cause, BaseException) else error
            if registry is None or not allow_cross_source:
                raise cause
            if on_progress:
                on_progress(0, 0, f"{_label_of(registry, resolved.source)} 下载失败，尝试换源…")
            try:
                resolved = registry.resolve(video, episode, quality,
                                            allow_cross_source=True, exclude=tried)
            except Exception:  # noqa: BLE001
                raise error.cause from None
            tried.append(resolved.source)
            if on_progress:
                on_progress(0, 0, resolved.note or f"改用 {_label_of(registry, resolved.source)}")
        except RuntimeError:
            raise                       # 用户取消


class _RetryableSourceError(RuntimeError):
    """内部信号：本次下载失败但可以换源重试。"""

    def __init__(self, cause: Exception):
        super().__init__(str(cause))
        self.cause = cause


def _label_of(registry, key: str) -> str:  # noqa: ANN001
    provider = registry.get(key) if registry is not None else None
    return provider.label if provider is not None else key


def _download_resolved(
    resolved: ResolvedPlay,
    dest_dir: str | Path,
    *,
    video: RemoteVideo,
    on_progress: ProgressFn | None,
    cancel: threading.Event | None,
    timeout: float,
    pause_event: threading.Event | None = None,
) -> Path:
    """按已解析地址下载（失败抛可换源重试的内部异常）。"""
    from .sources import SourceError

    url = resolved.url
    if not url:
        raise _RetryableSourceError(SourceError("该条目没有可用的下载地址"))
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    headers = default_headers(url, resolved.referer)

    # HLS：并发分片下载器优先，ffmpeg 作直连/兜底
    if resolved.is_hls or url.lower().split("?", 1)[0].endswith(".m3u8"):
        try:
            return _download_hls(resolved, dest, video=video, headers=headers,
                                 on_progress=on_progress, cancel=cancel, timeout=timeout,
                                 pause_event=pause_event)
        except _RetryableSourceError:
            raise
        except RuntimeError:
            raise
        except Exception as error:  # noqa: BLE001
            raise _RetryableSourceError(SourceError(str(error))) from error

    # 直链文件
    try:
        return _download_direct(resolved, dest, video=video, headers=headers,
                                on_progress=on_progress, cancel=cancel, timeout=timeout)
    except _RetryableSourceError:
        raise
    except RuntimeError:
        raise
    except Exception as error:  # noqa: BLE001
        raise _RetryableSourceError(SourceError(str(error))) from error


# --------------------------------------------------------------------------- 直链下载


def _download_direct(
    resolved: ResolvedPlay,
    dest: Path,
    *,
    video: RemoteVideo,
    headers: dict,
    on_progress: ProgressFn | None,
    cancel: threading.Event | None,
    timeout: float,
) -> Path:
    from .sources import SourceError

    url = resolved.url
    response = None
    last_error: Exception | None = None
    for attempt in range(1, 4):
        if cancel is not None and cancel.is_set():
            raise RuntimeError("下载已取消")
        try:
            response = requests.get(url, headers=headers, stream=True, timeout=timeout)
            response.raise_for_status()
            break
        except requests.RequestException as error:
            last_error = error
            if response is not None:
                response.close()
            if attempt < 3:
                if on_progress:
                    on_progress(0, 0, f"网络抖动，重试 {attempt}/2：{video.title}")
                time.sleep(0.8 * attempt)
    if response is None:
        raise SourceError(describe_network_error(last_error or Exception("未知网络错误"), urlsplit(url).hostname or ""))

    with response:
        extension = guess_extension(response.url or url, dict(response.headers))
        target = unique_path(dest, build_filename(video, resolved.episode, extension))
        total = int(response.headers.get("Content-Length") or 0)
        written = 0
        tmp = target.with_suffix(target.suffix + ".part")
        try:
            with open(tmp, "wb") as handle:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    if cancel is not None and cancel.is_set():
                        raise RuntimeError("下载已取消")
                    if not chunk:
                        continue
                    handle.write(chunk)
                    written += len(chunk)
                    if on_progress:
                        on_progress(written, total, f"下载中 {written // 1048576:.1f} MB")
            tmp.replace(target)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

        if written == 0:
            target.unlink(missing_ok=True)
            raise SourceError("下载内容为空（可能链接已失效）")
        content_type = str(response.headers.get("Content-Type", "")).split(";")[0].strip().lower()
        if not looks_like_video(target, content_type):
            target.unlink(missing_ok=True)
            raise SourceError(
                f"下载到的不是有效视频（{content_type or '未知类型'}，{written // 1024} KB）："
                "该内容可能受版权限制或链接已失效"
            )

    if on_progress:
        on_progress(written, total or written, f"完成：{target.name}")
    return target


# --------------------------------------------------------------------------- HLS 下载


def _ffmpeg_supports(ffmpeg: str, option: str, *, protocol: str = "http") -> bool:
    """探测 ffmpeg 是否支持某个 protocol/format 选项（结果按 (ffmpeg, option) 缓存）。

    为什么要探测：`-headers` / `-reconnect` / `-http_persistent` 在不同构建里
    未必都有；写死会让"某个版本的 ffmpeg 直接报 Option not found"，
    而**直接把这些选项交给 ffmpeg** 能省掉我们自己的 Python 代理这一跳（V2-b）。
    """
    key = (ffmpeg, protocol, option)
    cached = _FFMPEG_CAPS.get(key)
    if cached is not None:
        return cached
    supported = False
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-h", f"protocol={protocol}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        text = (result.stdout or "") + (result.stderr or "")
        supported = option in text
    except Exception:  # noqa: BLE001  探测失败就当不支持（走代理，最稳）
        supported = False
    _FFMPEG_CAPS[key] = supported
    return supported


def _ffmpeg_network_args(ffmpeg: str, headers: dict) -> tuple[list[str], bool]:
    """返回 (网络参数, 是否已把请求头直接交给 ffmpeg)。

    支持 `-headers` 时**直连源站**（跳过本机代理这一跳）；否则返回空列表，
    由调用方回退到"本地 playlist + 本机代理"（跨版本最稳）。
    """
    args: list[str] = []
    direct = False
    if _ffmpeg_supports(ffmpeg, "-headers"):
        pairs = "".join(f"{key}: {value}\r\n" for key, value in (headers or {}).items()
                        if key and value)
        if pairs:
            args += ["-headers", pairs]
            direct = True
        if _ffmpeg_supports(ffmpeg, "-user_agent"):
            agent = str((headers or {}).get("User-Agent") or "")
            if agent:
                args += ["-user_agent", agent]
    # 网络健壮性参数：按版本探测，不支持就跳过（绝不让 ffmpeg 报 Option not found）
    if _ffmpeg_supports(ffmpeg, "-rw_timeout"):
        args += ["-rw_timeout", "20000000"]          # 20s，单位微秒
    if _ffmpeg_supports(ffmpeg, "-reconnect"):
        args += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
    if _ffmpeg_supports(ffmpeg, "-http_persistent"):
        args += ["-http_persistent", "1"]
    if _ffmpeg_supports(ffmpeg, "-thread_queue_size"):
        args += ["-thread_queue_size", "1024"]
    return args, direct


def _download_hls(
    resolved: ResolvedPlay,
    dest: Path,
    *,
    video: RemoteVideo,
    headers: dict,
    on_progress: ProgressFn | None,
    cancel: threading.Event | None,
    timeout: float,
    pause_event: threading.Event | None = None,
) -> Path:
    """m3u8 → 本地可播放文件。

    优先顺序（V2-a/V2-b）：

    1. **自研并发分片下载器**（默认，非加密流）：并发 6 + 每线程连接复用 +
       `.parts` 断点续传 + 坏片重试，然后一次 `-c copy` remux 成 MP4。
       —— 这是"下载慢"的正解：旧实现是严格串行 + 每片新建连接。
    2. **ffmpeg 直连源站**（带 `-headers`，跳过本机代理那一跳）；
    3. **ffmpeg 走本机代理**（跨版本最稳，旧路径；代理本身已被重写，不再拖后腿）。

    AES-128 加密流由 1 自行解密；SAMPLE-AES 等无法自行解密的直接走 2/3。
    """
    from .sources import SourceError

    url = _prefer_best_variant(resolved.url, headers)
    # 取清单本身失败（网络抖动/被拦截）也不能直接判死：继续让 ffmpeg 试一次，
    # 它是另一条取流实现（不同的超时与重连策略），实测能救回一部分"清单偶发失败"。
    try:
        playlist_text = _fetch_text(url, headers, timeout)
        playlist = parse_m3u8(playlist_text, url)
        if playlist.is_master:
            best = playlist.best_variant()
            if best is None:
                raise SourceError("m3u8 主清单里没有可用的清晰度")
            playlist_text = _fetch_text(best.url, headers, timeout)
            playlist = parse_m3u8(playlist_text, best.url)
            playlist.bandwidth = int(getattr(best, "bandwidth", 0) or 0)
        if not playlist.segments:
            raise SourceError("m3u8 清单里没有分片（可能是直播流或链接已失效）")
    except SourceError as error:
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            raise
        if on_progress:
            on_progress(0, 0, f"分片清单获取失败（{error}），改用 ffmpeg 重试…")
        return _ffmpeg_hls(ffmpeg, url, dest, video=video, episode=resolved.episode,
                           headers=headers, on_progress=on_progress, cancel=cancel)

    ffmpeg = find_ffmpeg()
    decodable = (not playlist.encrypted) or playlist.aes128
    if _skip_ad_segments_pref():
        kept = _drop_ad_segments(playlist, on_progress=on_progress)
        if kept:
            playlist = kept
    if decodable:
        try:
            ts_path = _segments_download(playlist, dest, video=video,
                                         episode=resolved.episode, headers=headers,
                                         on_progress=on_progress, cancel=cancel,
                                         timeout=timeout, pause_event=pause_event,
                                         playlist_text=playlist_text)
            if ts_path is not None:
                if ffmpeg:
                    remuxed = _remux_to_mp4(ffmpeg, ts_path, dest, video=video,
                                            episode=resolved.episode,
                                            on_progress=on_progress, cancel=cancel)
                    if remuxed is not None:
                        ts_path.unlink(missing_ok=True)
                        return remuxed
                return ts_path
        except RuntimeError:
            raise
        except _RetryableSourceError:
            raise
        except Exception:  # noqa: BLE001  并发下载失败 → 回退 ffmpeg（更"能忍"）
            if on_progress:
                on_progress(0, 0, "并发分片下载失败，改用 ffmpeg 重试…")

    if ffmpeg:
        return _ffmpeg_hls(ffmpeg, url, dest, video=video, episode=resolved.episode,
                           headers=headers, on_progress=on_progress, cancel=cancel)

    # 没有 ffmpeg：AES-128 还能自己解密，其它加密只能报错
    if not decodable:
        raise SourceError(
            f"该视频流使用 {playlist.key_method or '未知'} 加密，需要 ffmpeg 才能下载；"
            "请安装 ffmpeg 或改用其他源")
    return _segments_download(playlist, dest, video=video, episode=resolved.episode,
                              headers=headers, on_progress=on_progress, cancel=cancel,
                              timeout=timeout, pause_event=pause_event,
                              playlist_text=playlist_text, require_path=True)


def _segments_download(
    playlist,  # noqa: ANN001  HlsPlaylist
    dest: Path,
    *,
    video: RemoteVideo,
    episode: Episode | None,
    headers: dict,
    on_progress: ProgressFn | None,
    cancel: threading.Event | None,
    timeout: float,
    playlist_text: str = "",
    pause_event: threading.Event | None = None,
    require_path: bool = False,
) -> Path | None:
    """把分片并发抓下来并拼接为 `.ts`。

    `video/segment_concurrency_enabled=False`（或直接调 `_download_segments`）时
    走**旧的串行实现** —— 保留它有两个理由：① 并发被源站限流时的一键降级；
    ② 它是 AES-128 解密路径的既有回归基线。
    """
    if not _concurrent_segments_enabled():
        base = playlist.segments[0] if playlist.segments else ""
        playlist_url = base.rsplit("/", 1)[0] + "/index.m3u8" if base else ""
        return _download_segments(playlist_url, dest, video=video, episode=episode,
                                  headers=headers, on_progress=on_progress,
                                  cancel=cancel, timeout=timeout)

    from .segment_downloader import SegmentDownloader

    def relay(progress) -> None:  # noqa: ANN001
        if on_progress is None:
            return
        on_progress(progress.done_parts, progress.total_parts, progress.message)

    downloader = SegmentDownloader(
        concurrency=_segment_concurrency(),
        headers=headers,
        on_progress=relay,
        cancel=cancel,
        pause_event=pause_event,
        speed_limit_bps=_download_speed_limit_bps(),
        timeout=min(60.0, max(10.0, timeout)),
    )
    try:
        target = unique_path(dest, build_filename(video, episode, ".ts"))
        outcome = downloader.download(playlist, target)
    finally:
        downloader.close()
    if outcome.ok and outcome.path is not None:
        return outcome.path
    if on_progress:
        on_progress(0, 0, outcome.message or "分片下载未完成")
    if require_path:
        from .sources import SourceError

        raise SourceError(outcome.message or "分片下载失败")
    return None


def _concurrent_segments_enabled() -> bool:
    """是否用并发分片下载器（`video/segment_concurrency_enabled`，默认开）。"""
    try:
        from PySide6.QtCore import QSettings

        return bool(QSettings("ModuWorkbench", "modu-workbench").value(
            "video/segment_concurrency_enabled", True, type=bool))
    except Exception:  # noqa: BLE001
        return True


def _skip_ad_segments_pref() -> bool:
    """是否跳过疑似广告分片（`video/skip_ad_segments`，**默认关**）。

    默认关的原因很直接：误判会丢正片内容。开启后也只跳"独立成片、时长明显异常"
    的分片，且最多 3 片（见 `hls.looks_like_ad_segment`）。
    """
    try:
        from PySide6.QtCore import QSettings

        return bool(QSettings("ModuWorkbench", "modu-workbench").value(
            "video/skip_ad_segments", False, type=bool))
    except Exception:  # noqa: BLE001
        return False


def _drop_ad_segments(playlist, *, on_progress: ProgressFn | None = None):  # noqa: ANN001
    """从清单里剔除疑似广告分片；**先把命中原因报给用户**，再返回新清单。

    返回 None 表示没有命中（保持原清单，避免无谓的对象拷贝）。
    """
    from dataclasses import replace as _replace

    from .hls import find_ad_segments

    ads = find_ad_segments(playlist)
    if not ads:
        return None
    index_set = {ad.index for ad in ads}
    if on_progress:
        detail = "；".join(f"第 {ad.index + 1} 片（{ad.duration:.1f}s，{ad.reason}）"
                           for ad in ads[:3])
        on_progress(0, 0, f"疑似广告分片已跳过：{detail}")
    kept = _replace(
        playlist,
        segments=[url for index, url in enumerate(playlist.segments)
                  if index not in index_set],
        segment_durations=[value for index, value in enumerate(playlist.segment_durations)
                           if index not in index_set],
    )
    return kept


def _remux_to_mp4(ffmpeg: str, ts_path: Path, dest: Path, *, video: RemoteVideo,
                  episode: Episode | None, on_progress: ProgressFn | None,
                  cancel: threading.Event | None) -> Path | None:
    """把 TS 用 ffmpeg `-c copy` 重封装为 MP4（不重编码，几秒完成）。"""
    if on_progress:
        on_progress(0, 0, "正在封装为 MP4（不重编码）…")
    target = unique_path(dest, build_filename(video, episode, ".mp4"))
    args = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostats",
            "-i", str(ts_path), "-c", "copy", "-bsf:a", "aac_adtstoasc",
            "-movflags", "+faststart", str(target)]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", creationflags=creationflags, timeout=900)
    except (OSError, subprocess.SubprocessError):
        target.unlink(missing_ok=True)
        return None
    if result.returncode != 0 or not target.is_file():
        # 音频不是 AAC 时 `aac_adtstoasc` 会失败 → 去掉再试一次
        args = [item for item in args if item not in ("-bsf:a", "aac_adtstoasc")]
        try:
            result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", creationflags=creationflags, timeout=900)
        except (OSError, subprocess.SubprocessError):
            target.unlink(missing_ok=True)
            return None
    if result.returncode != 0 or not target.is_file() or target.stat().st_size < _MIN_VIDEO_BYTES:
        target.unlink(missing_ok=True)
        return None
    if on_progress:
        on_progress(target.stat().st_size, target.stat().st_size,
                    f"完成：{target.name}")
    return target


def _ffmpeg_hls(ffmpeg: str, url: str, dest: Path, *, video: RemoteVideo,
                episode: Episode | None, headers: dict, on_progress: ProgressFn | None,
                cancel: threading.Event | None) -> Path:
    """ffmpeg 路径：**直连优先**（能带 `-headers` 就不走本机代理）。"""
    from .sources import SourceError

    target = unique_path(dest, build_filename(video, episode, ".mp4"))
    duration_ms = _playlist_duration_ms(url, headers)
    # V2-b：优先把请求头直接交给 ffmpeg（直连源站，省掉本机代理这一跳）；
    # 探测不支持 `-headers` 时回退「本地 playlist + 本机代理」
    direct_args, direct = _ffmpeg_network_args(ffmpeg, headers) if _direct_enabled() else ([], False)
    attempts: list[list[str] | None] = [direct_args] if direct else [None]
    last_error: Exception | None = None
    for extra_args in attempts:
        try:
            _ffmpeg_download(ffmpeg, url, target, headers=headers,
                             on_progress=on_progress, cancel=cancel,
                             bitstream_filter="aac_adtstoasc",
                             total_duration_ms=duration_ms, extra_input_args=extra_args)
        except SourceError as error:
            last_error = error
            target.unlink(missing_ok=True)
            if cancel is not None and cancel.is_set():
                raise RuntimeError("下载已取消") from None
            try:
                _ffmpeg_download(ffmpeg, url, target, headers=headers,
                                 on_progress=on_progress, cancel=cancel,
                                 bitstream_filter="", total_duration_ms=duration_ms,
                                 extra_input_args=extra_args)
            except SourceError as second:
                last_error = second
                target.unlink(missing_ok=True)
                continue
        if target.is_file() and target.stat().st_size >= _MIN_VIDEO_BYTES:
            return target
        target.unlink(missing_ok=True)
    raise SourceError(f"ffmpeg 未能产出有效视频（线路可能已失效或加密）：{str(last_error)[:200]}")


def _direct_enabled() -> bool:
    """是否允许 ffmpeg 直连源站（`video/ffmpeg_direct`，默认开）。"""
    try:
        from PySide6.QtCore import QSettings

        return bool(QSettings("ModuWorkbench", "modu-workbench").value(
            "video/ffmpeg_direct", True, type=bool))
    except Exception:  # noqa: BLE001
        return True


def _segment_concurrency() -> int:
    """分片并发（`video/segment_concurrency`，默认 6，范围 2–16）。"""
    try:
        from PySide6.QtCore import QSettings

        value = int(QSettings("ModuWorkbench", "modu-workbench").value(
            "video/segment_concurrency", 6) or 6)
    except Exception:  # noqa: BLE001
        value = 6
    return max(2, min(16, value))


def _download_speed_limit_bps() -> float:
    """下载限速（`video/download_speed_limit_mbps`，0 = 不限）。"""
    try:
        from PySide6.QtCore import QSettings

        mbps = int(QSettings("ModuWorkbench", "modu-workbench").value(
            "video/download_speed_limit_mbps", 0) or 0)
    except Exception:  # noqa: BLE001
        mbps = 0
    return float(max(0, mbps) * 1024 * 1024)


#: ffmpeg 能力探测缓存（(ffmpeg, protocol, option) → bool）
_FFMPEG_CAPS: dict[tuple[str, str, str], bool] = {}


def _prefer_best_variant(url: str, headers: dict) -> str:
    """下载前把主清单锁定到最高清晰度的子清单（探测失败就原样返回）。

    否则 ffmpeg 自己挑变体，实测不可控：有的站点主清单里低码率排前面，
    结果用户明明有 1080P 却下载出一份 540P。
    """
    try:
        from .hls import best_variant_url
        from .sources.http import HttpClient

        client = HttpClient(headers=dict(headers or {}), timeout=15.0, attempts=1)
        return best_variant_url(url, http=client)
    except Exception:  # noqa: BLE001  探测失败不影响下载本身
        return url


def _ffmpeg_download(ffmpeg: str, url: str, target: Path, *, headers: dict,
                     on_progress: ProgressFn | None, cancel: threading.Event | None,
                     bitstream_filter: str = "aac_adtstoasc",
                     total_duration_ms: int = 0,
                     extra_input_args: list[str] | None = None) -> None:
    """用 ffmpeg 直接拉流封装为 mp4（-c copy 不重新编码，速度快）。

    两条输入路径（V2-b）：

    - `extra_input_args` 非 None：调用方已探测到 ffmpeg 支持 `-headers`，
      **直连源站**（省掉本机代理这一跳），并把网络健壮性参数一并传入；
    - `extra_input_args is None`：回退"本地 playlist + 本机代理"——
      请求头由代理在服务端补，ffmpeg 不需要任何 HTTP 选项（跨版本最稳）。

    为什么保留代理路径：ffmpeg 各版本对 `-headers` / `-user_agent` 的接受程度
    不一致（9.x 起曾直接报 Option not found），直连只能作为"探测成功才用"的加速项。
    """
    args = ["-y", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats"]
    input_arg = url
    temp_playlist: Path | None = None
    if extra_input_args:
        # 直连：把请求头与网络参数交给 ffmpeg（都要在 -i 之前）
        args += list(extra_input_args)
    elif url.lower().split("?")[0].endswith(".m3u8"):
        try:
            temp_playlist = _localize_playlist(url, headers)
            input_arg = str(temp_playlist)
            # ffmpeg 新建模默认只放行 file,crypto,data：
            # 读取「本地 m3u8 + 远程 http 分片」会被拒绝
            # （Protocol 'http' not on whitelist 'file,crypto,data'），故显式放行
            args += ["-protocol_whitelist", "file,http,https,tcp,tls,crypto,data"]
        except Exception:  # noqa: BLE001  代理不可用时退回直连（无自定义头）
            temp_playlist = None
    args += ["-i", input_arg, "-c", "copy"]
    if bitstream_filter:
        args += ["-bsf:a", bitstream_filter]
    args += ["-movflags", "+faststart", str(target)]

    try:
        _run_ffmpeg_download(ffmpeg, args, target, on_progress=on_progress, cancel=cancel,
                             total_duration_ms=total_duration_ms)
    finally:
        if temp_playlist is not None:
            temp_playlist.unlink(missing_ok=True)


def _playlist_duration_ms(url: str, headers: dict) -> int:
    """尽量算出 HLS 总时长（毫秒），用于下载百分比进度。

    只读清单、不下分片；主清单会再取一层子清单。失败返回 0（退化为按字节显示）。
    """
    try:
        text = _fetch_text(url, headers, timeout=20.0)
        playlist = parse_m3u8(text, url)
        if playlist.is_master:
            best = playlist.best_variant()
            if best is None:
                return 0
            text = _fetch_text(best.url, headers, timeout=20.0)
            playlist = parse_m3u8(text, best.url)
        total = playlist.duration_seconds
        return int(total * 1000) if total > 0 else 0
    except Exception:  # noqa: BLE001  进度信息拿不到不影响下载
        return 0


def _localize_playlist(url: str, headers: dict) -> Path:
    """把远程 m3u8 落到本地临时文件，并把内部地址改写成本机代理地址。

    这样 ffmpeg 无需任何自定义请求头（头由本机流服务在服务端补上）。
    """
    import tempfile


    text = _fetch_text(url, headers, timeout=20.0)
    rewritten = _proxy_playlist(text, url, headers)
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".m3u8", delete=False, encoding="utf-8", newline="\n"
    )
    try:
        handle.write(rewritten)
    finally:
        handle.close()
    return Path(handle.name)


def _proxy_playlist(text: str, base_url: str, headers: dict) -> str:
    """把 m3u8 里的分片/子清单地址改写为本机代理地址（保留相对地址语义）。"""
    from modu_workbench.services import media_proxy
    from urllib.parse import urljoin

    lines: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            # EXT-X-KEY 的 URI 属性同样需要带请求头，单独改写
            if stripped.startswith("#EXT-X-KEY") and 'URI="' in stripped:
                head, _, tail = stripped.partition('URI="')
                uri, _, rest = tail.partition('"')
                absolute = urljoin(base_url, uri)
                stripped = f"{head}URI=\"{media_proxy.stream_url(absolute, headers=headers)}\"{rest}"
            lines.append(stripped)
            continue
        absolute = stripped if stripped.lower().startswith(("http://", "https://")) \
            else urljoin(base_url, stripped)
        lines.append(media_proxy.stream_url(absolute, headers=headers))
    return "\n".join(lines) + "\n"


def _run_ffmpeg_download(ffmpeg: str, args: list[str], target: Path, *,
                         on_progress: ProgressFn | None,
                         cancel: threading.Event | None,
                         total_duration_ms: int = 0) -> None:
    """执行 ffmpeg 并把进度回报出去；失败时抛 SourceError。

    进度来源优先用 ffmpeg 的 `-progress` 输出，而不是输出文件大小：
    `-c copy` 写 MP4 时 ffmpeg 会先缓冲再落盘，文件大小长时间为 0
    （用户看到「一直 0 MB」以为卡死），而 `-progress` 能给出已处理时长与字节数。
    """
    import re

    from .sources import SourceError

    progress_args = [ffmpeg, *args]
    # 注意：`args` 里**不能**再包含 ffmpeg 路径 —— 曾经写成 `[ffmpeg, *args]`，
    # 而 args 本身以 ffmpeg 开头，于是命令行里 ffmpeg 路径出现两次：
    # ffmpeg 把多出来的那个当成**输出文件**，直接报
    # 「Error initializing the muxer for ...\ffmpeg.exe: Invalid argument」，
    # 导致所有走 ffmpeg 的 HLS 下载必然失败（回归测试见 tests/board_video/test_video_download.py）。
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # 必须显式指定 utf-8：Windows 下 text=True 默认用 GBK 解码，
    # 而 ffmpeg 的路径/错误信息是 UTF-8，会让读取线程抛 UnicodeDecodeError
    process = subprocess.Popen(progress_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace", bufsize=1,
                               creationflags=creationflags)

    state = {"out_ms": 0, "size": 0, "speed": ""}
    stop = threading.Event()

    def pump_progress() -> None:
        """读取 -progress 的 key=value 流（out_time_us / total_size / speed）。"""
        if process.stdout is None:
            return
        for line in process.stdout:
            if stop.is_set():
                break
            key, _, value = line.strip().partition("=")
            if key == "out_time_us":
                try:
                    state["out_ms"] = int(value) // 1000
                except ValueError:
                    pass
            elif key == "out_time_ms":
                try:
                    state["out_ms"] = int(value)          # 老版本单位是毫秒
                except ValueError:
                    pass
            elif key == "total_size":
                try:
                    state["size"] = int(value)
                except ValueError:
                    pass
            elif key == "speed":
                state["speed"] = value.strip()

    pump = threading.Thread(target=pump_progress, daemon=True)
    pump.start()

    last_report = 0.0
    while True:
        if cancel is not None and cancel.is_set():
            process.kill()
            process.wait()
            stop.set()
            target.unlink(missing_ok=True)
            raise RuntimeError("下载已取消")
        if process.poll() is not None:
            break
        now = time.time()
        if on_progress and now - last_report > 1.0:
            out_ms = state["out_ms"]
            size_mb = state["size"] / 1048576
            if total_duration_ms > 0 and out_ms > 0:
                percent = min(100, int(out_ms * 100 / total_duration_ms))
                on_progress(percent, 100,
                            f"下载中 {percent}%（已获取 {size_mb:.1f} MB，"
                            f"进度 {format_duration_ms(out_ms)} / "
                            f"{format_duration_ms(total_duration_ms)}"
                            + (f"，{state['speed']}" if state["speed"] else "") + "）")
            else:
                on_progress(0, 0, f"下载中 {size_mb:.1f} MB（ffmpeg 封装）")
            last_report = now
        time.sleep(0.3)

    stop.set()
    if process.returncode != 0:
        stderr = ""
        if process.stderr is not None:
            try:
                stderr = process.stderr.read()
            except Exception:  # noqa: BLE001
                stderr = ""
        stderr = re.sub(r"\s+", " ", stderr or "").strip()
        raise SourceError(f"ffmpeg 下载失败（退出码 {process.returncode}）：{stderr[-300:]}".strip())
    if on_progress:
        size = target.stat().st_size if target.is_file() else state["size"]
        if total_duration_ms > 0:
            on_progress(100, 100, f"完成：{target.name}（{size / 1048576:.1f} MB）")
        else:
            on_progress(size, size, f"完成：{target.name}")


def format_duration_ms(ms: int) -> str:
    """把毫秒格式化成 时:分:秒 / 分:秒（下载进度提示用）。"""
    total = max(0, int(ms // 1000))
    hours, minutes, seconds = total // 3600, (total % 3600) // 60, total % 60
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _download_segments(
    m3u8_url: str,
    dest: Path,
    *,
    video: RemoteVideo,
    episode: Episode | None,
    headers: dict,
    on_progress: ProgressFn | None,
    cancel: threading.Event | None,
    timeout: float,
) -> Path:
    """无 ffmpeg 时的退化方案：下载全部分片并顺序拼接为 .ts。"""
    from .sources import SourceError

    playlist_text = _fetch_text(m3u8_url, headers, timeout)
    playlist = parse_m3u8(playlist_text, m3u8_url)

    # 主清单：挑最高清晰度再抓一次
    if playlist.is_master:
        best = playlist.best_variant()
        if best is None:
            raise SourceError("m3u8 主清单里没有可用的清晰度")
        if on_progress:
            on_progress(0, 0, f"选用清晰度：{best.display}")
        playlist_text = _fetch_text(best.url, headers, timeout)
        playlist = parse_m3u8(playlist_text, best.url)

    if not playlist.segments:
        raise SourceError("m3u8 清单里没有分片（可能是直播流或链接已失效）")

    # AES-128 分片：用标准库解密（不需要 ffmpeg）；
    # 其它加密方式（SAMPLE-AES 等）确实只能交给 ffmpeg
    key: bytes | None = None
    if playlist.encrypted:
        if not playlist.aes128:
            raise SourceError(
                f"该视频流使用 {playlist.key_method or '未知'} 加密，需要 ffmpeg 才能下载；"
                "请安装 ffmpeg 或改用其他源"
            )
        if on_progress:
            on_progress(0, 0, "检测到 AES-128 加密，正在获取密钥…")
        key = _fetch_bytes(playlist.key_uri, headers, timeout)
        if len(key) not in (16, 24, 32):
            raise SourceError(f"解密密钥长度异常（{len(key)} 字节），无法解密该视频流")

    target = unique_path(dest, build_filename(video, episode, ".ts"))
    total = len(playlist.segments)
    tmp = target.with_suffix(target.suffix + ".part")
    written = 0
    try:
        with open(tmp, "wb") as handle:
            for index, segment in enumerate(playlist.segments, start=1):
                if cancel is not None and cancel.is_set():
                    raise RuntimeError("下载已取消")
                data = _fetch_bytes(segment, headers, timeout)
                if key is not None:
                    data = _decrypt_segment(data, key, playlist, index - 1)
                handle.write(data)
                written += len(data)
                if on_progress:
                    on_progress(index, total, f"分片 {index}/{total}（{written // 1048576:.1f} MB）")
        tmp.replace(target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    if written < _MIN_VIDEO_BYTES:
        target.unlink(missing_ok=True)
        raise SourceError("分片内容过小，可能线路已失效")
    if on_progress:
        suffix = "（AES-128 已解密）" if key is not None else ""
        on_progress(total, total,
                    f"完成：{target.name}{suffix}（TS 格式，建议安装 ffmpeg 以输出 MP4）")
    return target


def _decrypt_segment(data: bytes, key: bytes, playlist, index: int) -> bytes:  # noqa: ANN001
    """解密单个分片；失败时给出可读原因（而不是抛底层异常）。"""
    from .aes import aes128_cbc_decrypt
    from .sources import SourceError

    try:
        return aes128_cbc_decrypt(data, key[:16], segment_iv(playlist, index))
    except ValueError as error:
        raise SourceError(f"分片解密失败（第 {index + 1} 片）：{error}") from error


def _fetch_text(url: str, headers: dict, timeout: float) -> str:
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.text


def _fetch_bytes(url: str, headers: dict, timeout: float) -> bytes:
    last: Exception | None = None
    for attempt in range(1, 3):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response.content
        except requests.RequestException as error:
            last = error
            if attempt < 2:
                time.sleep(0.6 * attempt)
    from .sources import SourceError

    raise SourceError(describe_network_error(last, urlsplit(url).hostname or ""))


# --------------------------------------------------------------------------- 批量下载


def download_parallelism() -> int:
    """同时下载的集数（`video/download_parallel`，默认 2，范围 1–4）。

    与 Kazumi 的"同时下载集数默认 2"一致；再往上（12 集 × 6 分片并发）
    很容易被源站限流，也会把家里带宽打满。
    """
    try:
        from PySide6.QtCore import QSettings

        value = int(QSettings("ModuWorkbench", "modu-workbench").value(
            "video/download_parallel", 2) or 2)
    except Exception:  # noqa: BLE001
        value = 2
    return max(1, min(4, value))


def download_many(
    tasks: Iterable[tuple[RemoteVideo, Episode | None, Quality | None]],
    dest_dir: str | Path,
    *,
    on_progress: ProgressFn | None = None,
    on_task_done: Callable[[DownloadResult], None] | None = None,
    cancel: threading.Event | None = None,
    registry: VideoRegistry | None = None,
    allow_cross_source: bool = True,
    parallel: int | None = None,
    pause_event: threading.Event | None = None,
) -> List[DownloadResult]:
    """批量下载：**集间并发**（默认 2），单个失败不影响后续。

    旧实现是 `for 任务: download_episode(...)` 的严格串行 —— 一季 12 集就是
    12 次串行等待，这也是"下载慢"的一部分（方案 B1）。

    进度回调签名不变（`on_progress(done, total, message)`），UI 无需改动；
    并发时 `done/total` 改按"已完成集数"计数，`message` 带 `[i/N]` 前缀。
    """
    items = list(tasks)
    if not items:
        return []
    workers = download_parallelism() if parallel is None else max(1, min(4, int(parallel)))
    results: List[DownloadResult] = []
    lock = threading.Lock()
    done_count = [0]
    on_task_done_lock = threading.Lock()

    def run_one(index: int, video: RemoteVideo, episode: Episode | None,
                quality: Quality | None) -> DownloadResult:
        label = f"{video.title} {episode.name if episode else ''}".strip()
        if on_progress:
            on_progress(index - 1, len(items), f"[{index}/{len(items)}] {label}")
        try:
            path, resolved = download_episode(
                video, episode, dest_dir, quality=quality, on_progress=on_progress,
                cancel=cancel, registry=registry, allow_cross_source=allow_cross_source,
                pause_event=pause_event,
            )
            size = path.stat().st_size if path.is_file() else 0
            return DownloadResult(
                video=resolved.video, episode=resolved.episode, quality=resolved.quality,
                path=str(path), ok=True, message=resolved.note or "完成",
                source=resolved.source, switched_from=resolved.switched_from,
                note=resolved.note, size_bytes=size,
            )
        except (RuntimeError, OSError) as error:
            if isinstance(error, RuntimeError) and str(error) == "下载已取消":
                return DownloadResult(video=video, episode=episode, quality=quality,
                                      ok=False, message="下载已取消")
            return DownloadResult(video=video, episode=episode, quality=quality,
                                  ok=False, message=str(error))
        except Exception as error:  # noqa: BLE001
            return DownloadResult(video=video, episode=episode, quality=quality,
                                  ok=False, message=str(error))

    def report(result: DownloadResult) -> None:
        with on_task_done_lock:
            if on_task_done:
                on_task_done(result)

    if workers <= 1 or len(items) == 1:
        for index, (video, episode, quality) in enumerate(items, start=1):
            if cancel is not None and cancel.is_set():
                break
            result = run_one(index, video, episode, quality)
            if not result.ok and result.message == "下载已取消":
                break
            results.append(result)
            report(result)
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        indexed: dict[int, DownloadResult] = {}
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="video-dl") as pool:
            futures = {
                pool.submit(run_one, index, video, episode, quality): index
                for index, (video, episode, quality) in enumerate(items, start=1)
            }
            for future in as_completed(futures):
                index = futures[future]
                result = future.result()
                with lock:
                    indexed[index] = result
                    done_count[0] += 1
                    if on_progress:
                        on_progress(done_count[0], len(items),
                                    f"已处理 {done_count[0]}/{len(items)}")
                report(result)
        # 返回顺序与入参一致（并发完成顺序是乱的，UI 与测试都依赖稳定顺序）
        results = [indexed[index] for index in sorted(indexed)]
    if on_progress:
        on_progress(len(results), len(items), f"已处理 {len(results)}/{len(items)}")
    return results


def copy_local(source_path: str | Path, dest_dir: str | Path) -> Path:
    """把已有本地文件复制进影视库目录（导入外部影片用）。"""
    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(f"文件不存在：{source_path}")
    target = unique_path(Path(dest_dir), source.name)
    shutil.copy2(source, target)
    return target
