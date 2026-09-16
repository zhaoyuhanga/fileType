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
    from modu_workbench.core.convert.media_io import find_ffmpeg as _find

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
) -> tuple[Path, ResolvedPlay]:
    """下载某一集到 dest_dir，返回 (保存路径, 解析结果)。

    解析地址时优先使用原源；失败（接口变更 / 线路失效 / 网络）时由 registry
    换源搜索同一部片继续尝试，因此调用方无需关心「某个源挂掉」。
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
                                      cancel=cancel, timeout=timeout), resolved
        except _RetryableSourceError as error:
            # 注意：必须先于 RuntimeError 捕获（前者是后者的子类）
            if registry is None or not allow_cross_source:
                raise error.cause
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
) -> Path:
    """按已解析地址下载（失败抛可换源重试的内部异常）。"""
    from .sources import SourceError

    url = resolved.url
    if not url:
        raise _RetryableSourceError(SourceError("该条目没有可用的下载地址"))
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    headers = default_headers(url, resolved.referer)

    # HLS：交给 ffmpeg 直接封装（最稳），或退化到分片拼接
    if resolved.is_hls or url.lower().split("?", 1)[0].endswith(".m3u8"):
        try:
            return _download_hls(resolved, dest, video=video, headers=headers,
                                 on_progress=on_progress, cancel=cancel, timeout=timeout)
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


def _download_hls(
    resolved: ResolvedPlay,
    dest: Path,
    *,
    video: RemoteVideo,
    headers: dict,
    on_progress: ProgressFn | None,
    cancel: threading.Event | None,
    timeout: float,
) -> Path:
    """m3u8 → 本地文件。优先 ffmpeg 封装；不可用时拼接分片为 .ts。"""
    from .sources import SourceError

    url = resolved.url
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        target = unique_path(dest, build_filename(video, resolved.episode, ".mp4"))
        # 用清单总时长做百分比进度：-c copy 写 MP4 时输出文件长时间为 0 字节，
        # 只看文件大小会让用户以为卡死
        duration_ms = _playlist_duration_ms(url, headers)
        # AAC(ADTS) → MP4 需要 bitstream filter；音频不是 AAC 时会报错，因此失败后去掉再试一次
        try:
            _ffmpeg_download(ffmpeg, url, target, headers=headers,
                             on_progress=on_progress, cancel=cancel,
                             bitstream_filter="aac_adtstoasc", total_duration_ms=duration_ms)
        except SourceError:
            target.unlink(missing_ok=True)
            if cancel is not None and cancel.is_set():
                raise RuntimeError("下载已取消")
            _ffmpeg_download(ffmpeg, url, target, headers=headers,
                             on_progress=on_progress, cancel=cancel,
                             bitstream_filter="", total_duration_ms=duration_ms)
        if target.is_file() and target.stat().st_size >= _MIN_VIDEO_BYTES:
            return target
        target.unlink(missing_ok=True)
        raise SourceError("ffmpeg 未能产出有效视频（线路可能已失效或加密）")

    # 退化路径：抓分片后拼接
    return _download_segments(url, dest, video=video, episode=resolved.episode,
                              headers=headers, on_progress=on_progress,
                              cancel=cancel, timeout=timeout)


def _ffmpeg_download(ffmpeg: str, url: str, target: Path, *, headers: dict,
                     on_progress: ProgressFn | None, cancel: threading.Event | None,
                     bitstream_filter: str = "aac_adtstoasc",
                     total_duration_ms: int = 0) -> None:
    """用 ffmpeg 直接拉流封装为 mp4（-c copy 不重新编码，速度快）。

    请求头不通过 ffmpeg 选项传递 —— ffmpeg 各版本对 `-headers` / `-user_agent`
    的接受程度不一致（9.x 起会直接报 "Option not found"）。
    改为：清单与分片一律走本机流服务的代理（服务端补 Referer/UA），
    ffmpeg 只需要读取一个本地 m3u8 文件即可，跨版本都稳。
    """
    args = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    input_arg = url
    temp_playlist: Path | None = None
    if url.lower().split("?")[0].endswith(".m3u8"):
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

    from modu_workbench.services import media_server

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
    from modu_workbench.services import media_server
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
                stripped = f"{head}URI=\"{media_server.stream_url(absolute, headers=headers)}\"{rest}"
            lines.append(stripped)
            continue
        absolute = stripped if stripped.lower().startswith(("http://", "https://")) \
            else urljoin(base_url, stripped)
        lines.append(media_server.stream_url(absolute, headers=headers))
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

    progress_args = [ffmpeg, *args, "-progress", "pipe:1", "-nostats"]
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
    duration_ms = state["out_ms"]
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


def download_many(
    tasks: Iterable[tuple[RemoteVideo, Episode | None, Quality | None]],
    dest_dir: str | Path,
    *,
    on_progress: ProgressFn | None = None,
    on_task_done: Callable[[DownloadResult], None] | None = None,
    cancel: threading.Event | None = None,
    registry: VideoRegistry | None = None,
    allow_cross_source: bool = True,
) -> List[DownloadResult]:
    """批量下载：单个失败不影响后续，返回每条的结果（含实际使用的源）。"""
    items = list(tasks)
    results: List[DownloadResult] = []
    for index, (video, episode, quality) in enumerate(items, start=1):
        if cancel is not None and cancel.is_set():
            break
        label = f"{video.title} {episode.name if episode else ''}".strip()
        if on_progress:
            on_progress(index - 1, len(items), f"[{index}/{len(items)}] {label}")
        try:
            path, resolved = download_episode(
                video, episode, dest_dir, quality=quality, on_progress=on_progress,
                cancel=cancel, registry=registry, allow_cross_source=allow_cross_source,
            )
            size = path.stat().st_size if path.is_file() else 0
            result = DownloadResult(
                video=resolved.video, episode=resolved.episode, quality=resolved.quality,
                path=str(path), ok=True, message=resolved.note or "完成",
                source=resolved.source, switched_from=resolved.switched_from,
                note=resolved.note, size_bytes=size,
            )
        except (RuntimeError, OSError) as error:
            if isinstance(error, RuntimeError) and str(error) == "下载已取消":
                break
            result = DownloadResult(video=video, episode=episode, quality=quality,
                                    ok=False, message=str(error))
        except Exception as error:  # noqa: BLE001
            result = DownloadResult(video=video, episode=episode, quality=quality,
                                    ok=False, message=str(error))
        results.append(result)
        if on_task_done:
            on_task_done(result)
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
