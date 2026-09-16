"""墨软乐库本地曲库：导入扫描、元数据补全、时长探测、格式转换入口。"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable, Iterable, List

from modu_workbench.core.convert.media_io import AUDIO_TARGETS, find_ffmpeg

from .models import AUDIO_EXTENSIONS, MUSIC_TARGETS, RemoteTrack, Track, parse_track_name
from .storage import MusicStorage

ProgressFn = Callable[[int, int, str], None]


def is_audio_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() in AUDIO_EXTENSIONS


def scan_audio_files(paths: Iterable[str | Path]) -> List[str]:
    """展开文件/文件夹为音频文件列表（递归、去重、保持顺序）。"""
    found: list[str] = []
    for raw in paths:
        target = Path(raw)
        if target.is_file() and is_audio_file(target):
            found.append(str(target))
        elif target.is_dir():
            for child in sorted(target.rglob("*")):
                if child.is_file() and is_audio_file(child):
                    found.append(str(child))
    return list(dict.fromkeys(found))


def find_ffprobe() -> str | None:
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        candidate = Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if candidate.is_file():
            return str(candidate)
        sibling = Path(ffmpeg).with_name("ffprobe")
        if sibling.is_file():
            return str(sibling)
    return shutil.which("ffprobe")


def probe_duration_ms(path: str | Path) -> int:
    """用 ffprobe 读取时长（毫秒）；不可用时返回 0（不做硬依赖）。"""
    ffprobe = find_ffprobe()
    if not ffprobe:
        return 0
    try:
        output = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        value = (output.stdout or "").strip().splitlines()
        seconds = float(value[0]) if value and value[0] else 0.0
        return int(seconds * 1000)
    except Exception:  # noqa: BLE001
        return 0


class MusicLibrary:
    """本地音乐库（存放于应用数据目录的 music.db）。"""

    def __init__(self, storage: MusicStorage, music_dir: Path):
        self.storage = storage
        self.music_dir = Path(music_dir)
        self.music_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 导入 ----------

    def build_track(self, path: str | Path, *, source: str = "local", category: str = "") -> Track:
        file_path = Path(path)
        artist, title = parse_track_name(file_path.stem)
        try:
            size = file_path.stat().st_size
        except OSError:
            size = 0
        return Track(
            path=str(file_path),
            title=title or file_path.stem,
            artist=artist,
            album=file_path.parent.name if artist else "",
            duration_ms=probe_duration_ms(file_path),
            size_bytes=size,
            format=file_path.suffix.lstrip(".").lower(),
            source=source,
            category=category,
        )

    def import_paths(self, paths: Iterable[str | Path], *, category: str = "",
                     on_progress: ProgressFn | None = None) -> List[Track]:
        """导入文件/文件夹到曲库，返回新增或更新的曲目。"""
        files = scan_audio_files(paths)
        tracks: list[Track] = []
        for index, file_path in enumerate(files, start=1):
            track = self.build_track(file_path, category=category)
            track_id = self.storage.upsert_track(track)
            track.id = track_id
            tracks.append(track)
            if on_progress:
                on_progress(index, len(files), f"已导入 {index}/{len(files)}：{track.display()}")
        return tracks

    def import_and_copy(self, paths: Iterable[str | Path], *, category: str = "",
                        on_progress: ProgressFn | None = None) -> List[Track]:
        """把外部文件复制进音乐库目录后再入库（便于统一管理）。"""
        copied: list[Path] = []
        for file_path in scan_audio_files(paths):
            source = Path(file_path)
            target = source
            try:
                if self.music_dir not in source.parents:
                    target = self.music_dir / source.name
                    if target.exists() and target.stat().st_size != source.stat().st_size:
                        target = self.music_dir / f"{source.stem} ({os.getpid()}){source.suffix}"
                    shutil.copy2(source, target)
            except OSError:
                target = source
            copied.append(target)
        return self.import_paths(copied, category=category, on_progress=on_progress)

    def import_remote(self, remote: RemoteTrack, path: str | Path) -> Track:
        """把下载完成的在线曲目入库（保留音源元数据，便于回溯与去重）。"""
        file_path = Path(path)
        cover = ""
        for suffix in (".jpg", ".png", ".webp"):
            candidate = file_path.with_suffix(suffix)
            if candidate.is_file():
                cover = str(candidate)
                break
        try:
            size = file_path.stat().st_size
        except OSError:
            size = 0
        track = Track(
            path=str(file_path),
            title=remote.title or file_path.stem,
            artist=remote.artist,
            album=remote.album,
            duration_ms=remote.duration_ms or probe_duration_ms(file_path),
            size_bytes=size,
            format=file_path.suffix.lstrip(".").lower(),
            source=remote.source,
            remote_id=remote.remote_id,
            cover_path=cover,
            category=remote.category,
        )
        track.id = self.storage.upsert_track(track)
        self.storage.add_history(track.id, "download")
        return track

    def import_download_results(self, results: Iterable) -> List[Track]:
        """批量把下载结果入库（跳过失败项）。"""
        tracks: list[Track] = []
        for result in results:
            if not getattr(result, "ok", False) or not getattr(result, "path", ""):
                continue
            try:
                tracks.append(self.import_remote(result.track, result.path))
            except Exception:  # noqa: BLE001
                continue
        return tracks

    def refresh_durations(self, track_ids: Iterable[int] | None = None) -> int:
        """为缺少时长的曲目补齐 duration_ms。"""
        ids = list(track_ids) if track_ids is not None else [t.id for t in self.storage.list_tracks()]
        updated = 0
        for track_id in ids:
            track = self.storage.get_track(track_id)
            if track is None or track.duration_ms or not track.exists:
                continue
            duration = probe_duration_ms(track.path)
            if duration:
                track.duration_ms = duration
                self.storage.upsert_track(track)
                updated += 1
        return updated

    # ---------- 播放 ----------

    def resolve_playable(self, tracks: Iterable[Track]) -> List[Track]:
        """过滤掉文件已丢失的曲目。"""
        return [track for track in tracks if track.exists]

    def record_play(self, track_id: int) -> None:
        self.storage.mark_played(track_id)
        self.storage.add_history(track_id, "play")

    def update_duration(self, track_id: int, duration_ms: int) -> bool:
        """播放时由播放器补全时长（无 ffprobe 也能拿到准确时长）。"""
        if duration_ms <= 0:
            return False
        track = self.storage.get_track(track_id)
        if track is None or track.duration_ms == duration_ms:
            return False
        track.duration_ms = int(duration_ms)
        self.storage.upsert_track(track)
        return True

    # ---------- 收藏 / 分类 ----------

    def toggle_favorite(self, track_id: int) -> bool:
        return self.storage.toggle_favorite(track_id)

    def set_category(self, track_ids: Iterable[int], category: str) -> int:
        return self.storage.set_category(track_ids, category)

    # ---------- 转换 ----------

    def convert(self, track_id: int, target_format: str, output_dir: str | Path | None = None,
                cancel: threading.Event | None = None,
                on_progress: ProgressFn | None = None) -> Path:
        """把曲目转换为目标音频格式，返回输出路径。"""
        from modu_workbench.core.convert.media_io import convert_media

        if target_format not in MUSIC_TARGETS:
            raise ValueError(f"不支持的转换目标：{target_format}")
        track = self.storage.get_track(track_id)
        if track is None:
            raise ValueError("曲目不存在")
        source = Path(track.path)
        if not source.is_file():
            raise FileNotFoundError(f"文件不存在：{track.path}")
        if track.format == target_format:
            raise ValueError(f"源文件已经是 {target_format.upper()} 格式")

        target_dir = Path(output_dir) if output_dir else self.music_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        base = source.stem
        target = target_dir / f"{base}.{target_format}"
        index = 2
        while target.exists():
            target = target_dir / f"{base} ({index}).{target_format}"
            index += 1

        if on_progress:
            on_progress(0, 0, f"转换中：{source.name} → {target_format.upper()}")
        convert_media(source, target_format, target, cancel=cancel)
        if on_progress:
            on_progress(1, 1, f"完成：{target.name}")

        # 转换结果自动入库（同目录时）
        try:
            self.import_paths([target], category=track.category)
        except Exception:  # noqa: BLE001
            pass
        return target


def audio_target_count() -> int:
    return len([fmt for fmt in MUSIC_TARGETS if fmt in AUDIO_TARGETS])
