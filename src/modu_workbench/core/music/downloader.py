"""墨读音乐下载器：解析直链 → 流式下载 → 库内落库（可选封面/歌词）。

下载在调用方线程（Qt 里是后台 QThread）执行，通过回调汇报进度，支持取消。
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List

import requests

from .models import RemoteTrack, safe_filename
from .sources import MusicSource, SourceError, get_source, clean_lyrics

ProgressFn = Callable[[int, int, str], None]  # written, total, message

_AUDIO_MIME_EXT = {
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/wav": ".wav",
    "audio/x-ms-wma": ".wma",
}

# 音频文件头（用于识别“下载到的其实是网页/错误页”这类伪成功）
_AUDIO_MAGIC = (
    b"ID3", b"fLaC", b"OggS", b"RIFF", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2",
    b"\xff\xfa", b"\x00\x00\x00", b"ftyp", b"wav",
)
_MIN_AUDIO_BYTES = 16 * 1024


def looks_like_audio(path: str | Path, content_type: str = "") -> bool:
    """判断下载结果是否真的是音频（防止把错误页/占位文件当成歌曲入库）。"""
    if content_type and content_type.startswith("text/") or "html" in content_type:
        return False
    file_path = Path(path)
    try:
        size = file_path.stat().st_size
    except OSError:
        return False
    if size < _MIN_AUDIO_BYTES:
        return False
    with open(file_path, "rb") as handle:
        head = handle.read(16)
    if head[4:8] == b"ftyp" or head[:4] in (b"fLaC", b"OggS", b"RIFF", b"wav "):
        return True
    return any(head.startswith(magic) for magic in _AUDIO_MAGIC)


@dataclass
class DownloadResult:
    track: RemoteTrack
    path: str = ""
    ok: bool = False
    message: str = ""


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


def guess_extension(url: str, headers: dict | None = None, fallback: str = ".mp3") -> str:
    content_type = str((headers or {}).get("Content-Type", "")).split(";")[0].strip().lower()
    if content_type in _AUDIO_MIME_EXT:
        return _AUDIO_MIME_EXT[content_type]
    suffix = os.path.splitext(url.split("?", 1)[0])[1].lower()
    if suffix in (".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma"):
        return suffix
    return fallback


def download_track(
    track: RemoteTrack,
    dest_dir: str | Path,
    *,
    source: MusicSource | None = None,
    on_progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
    save_cover: bool = True,
    save_lyrics: bool = True,
    timeout: float = 20.0,
) -> Path:
    """下载单曲到 dest_dir，返回保存路径。失败抛出 SourceError / RuntimeError。"""
    source = source or get_source(track.source)

    if on_progress:
        on_progress(0, 0, f"解析直链：{track.display()}")

    url = source.download_url(track)
    if not url:
        raise SourceError("该曲目没有可用的下载地址")

    dest = Path(dest_dir)
    try:
        response = requests.get(url, headers=_default_headers(url), stream=True, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as error:
        raise SourceError(f"下载失败：{error}") from error

    with response:
        extension = guess_extension(response.url or url, dict(response.headers))
        content_type = str(response.headers.get("Content-Type", "")).split(";")[0].strip().lower()
        base_name = safe_filename(track.display() or track.title or "track")
        target = unique_path(dest, f"{base_name}{extension}")
        total = int(response.headers.get("Content-Length") or 0)
        written = 0
        tmp = target.with_suffix(target.suffix + ".part")
        try:
            with open(tmp, "wb") as handle:
                for chunk in response.iter_content(chunk_size=128 * 1024):
                    if cancel is not None and cancel.is_set():
                        raise RuntimeError("下载已取消")
                    if not chunk:
                        continue
                    handle.write(chunk)
                    written += len(chunk)
                    if on_progress:
                        on_progress(written, total, f"下载中 {written // 1024} KB")
            tmp.replace(target)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        if written == 0:
            target.unlink(missing_ok=True)
            raise SourceError("下载内容为空（可能受版权限制或链接失效）")

        # 校验确实是音频：避免把“版权受限/需要登录”的网页当成歌曲入库后无法播放
        if not looks_like_audio(target, content_type):
            target.unlink(missing_ok=True)
            raise SourceError(
                f"下载到的不是有效音频（{content_type or '未知类型'}，{written // 1024} KB）："
                "该曲目可能受版权限制、需要 VIP 或链接已失效"
            )

    if save_cover and track.cover_url:
        _try_save_cover(track.cover_url, target)
    if save_lyrics:
        try:
            text = clean_lyrics(source.lyrics(track))
        except Exception:  # noqa: BLE001
            text = ""
        if text:
            target.with_suffix(".lrc").write_text(text, encoding="utf-8")

    if on_progress:
        on_progress(written, total or written, f"完成：{target.name}")
    return target


def _default_headers(url: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "*/*",
    }
    if "music.163.com" in url:
        headers["Referer"] = "https://music.163.com/"
    return headers


def _try_save_cover(cover_url: str, audio_path: Path) -> None:
    try:
        response = requests.get(cover_url, headers=_default_headers(cover_url), timeout=15)
        response.raise_for_status()
    except requests.RequestException:
        return
    suffix = ".jpg"
    content_type = str(response.headers.get("Content-Type", "")).lower()
    if "png" in content_type:
        suffix = ".png"
    elif "webp" in content_type:
        suffix = ".webp"
    try:
        audio_path.with_suffix(suffix).write_bytes(response.content)
    except OSError:
        return


def download_many(
    tracks: Iterable[RemoteTrack],
    dest_dir: str | Path,
    *,
    on_progress: ProgressFn | None = None,
    on_track_done: Callable[[DownloadResult], None] | None = None,
    cancel: threading.Event | None = None,
    save_cover: bool = True,
    save_lyrics: bool = True,
) -> List[DownloadResult]:
    """批量下载：单曲失败不影响后续，返回每首的结果。"""
    tracks = list(tracks)
    results: List[DownloadResult] = []
    for index, track in enumerate(tracks, start=1):
        if cancel is not None and cancel.is_set():
            break
        if on_progress:
            on_progress(index - 1, len(tracks), f"[{index}/{len(tracks)}] {track.display()}")
        try:
            path = download_track(track, dest_dir, on_progress=on_progress, cancel=cancel,
                                  save_cover=save_cover, save_lyrics=save_lyrics)
            result = DownloadResult(track=track, path=str(path), ok=True, message="完成")
        except (SourceError, RuntimeError, OSError) as error:
            result = DownloadResult(track=track, ok=False, message=str(error))
        results.append(result)
        if on_track_done:
            on_track_done(result)
    if on_progress:
        on_progress(len(results), len(tracks), f"已处理 {len(results)}/{len(tracks)}")
    return results
