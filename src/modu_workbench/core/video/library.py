"""墨软影视本地库：导入、在线播放解析、下载入库、续播记忆、格式转换入口。

与 `core.music.library.MusicLibrary` 对齐的分层：
- `VideoStorage` 只管 SQLite；
- `VideoLibrary` 负责「业务动作」（导入 / 播放 / 下载 / 转换），
  并且是唯一知道 `VideoRegistry`（多源）的地方。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List

from modu_workbench.core.platform.media import (
    find_ffmpeg,
    probe_duration_ms as _probe_duration_ms,
)

from .downloader import DownloadResult, copy_local, download_episode, download_many
from .models import (
    VIDEO_EXTENSIONS,
    VIDEO_TARGETS,
    Episode,
    Quality,
    RemoteVideo,
    Video,
    parse_video_name,
)
from .sources import ResolvedPlay, SourceError, VideoRegistry, registry as default_registry
from .storage import VideoStorage, remote_to_video

ProgressFn = Callable[[int, int, str], None]


@dataclass
class HdCandidate:
    """跨源找到的「更清晰」候选：某个源上同一部片、同一集的最高分辨率线路。"""

    remote: RemoteVideo
    episode: Episode
    variant: object            # core.video.hls.HlsVariant
    source_key: str
    source_label: str

    @property
    def height(self) -> int:
        return int(getattr(self.variant, "height", 0) or 0)

    @property
    def label(self) -> str:
        return str(getattr(self.variant, "display", "") or "")

    @property
    def bandwidth(self) -> int:
        return int(getattr(self.variant, "bandwidth", 0) or 0)


def _probe_client(provider):  # noqa: ANN001
    """清晰度探测专用客户端：短超时 + 继承源的默认请求头（采集站基本都校验 Referer/UA）。

    单独抽出来是为了可注入：单测里替换掉它就能离线验证"锁定最高清晰度"这条链路。
    """
    from .sources.http import HttpClient

    headers: dict = {}
    if provider is not None:
        try:
            headers = dict(provider.default_headers())
        except Exception:  # noqa: BLE001
            headers = {}
    return HttpClient(headers=headers, timeout=4.0, attempts=1)


def _same_episode(remote: RemoteVideo, name: str, index: int) -> Episode | None:
    """在另一个源的剧集列表里找「同一集」（先按序号，再按集名，最后退第一集）。"""
    episodes = list(remote.episodes or [])
    if not episodes:
        return None
    for episode in episodes:
        if index and episode.index == index:
            return episode
    if name:
        for episode in episodes:
            if episode.name == name:
                return episode
        # 线路前缀不同（「量子 · 第01集」vs「第01集」）时按尾段比
        tail = name.split("·")[-1].strip()
        if tail:
            for episode in episodes:
                if episode.name.split("·")[-1].strip() == tail:
                    return episode
    return episodes[0]



def is_video_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS


def scan_video_files(paths: Iterable[str | Path]) -> List[str]:
    """展开文件/文件夹为视频文件列表（递归、去重、保持顺序）。"""
    found: list[str] = []
    for raw in paths:
        target = Path(raw)
        if target.is_file() and is_video_file(target):
            found.append(str(target))
        elif target.is_dir():
            for child in sorted(target.rglob("*")):
                if child.is_file() and is_video_file(child):
                    found.append(str(child))
    return list(dict.fromkeys(found))


def probe_duration_ms(path: str | Path) -> int:
    """用 ffprobe 读取时长（毫秒）；不可用时返回 0（不做硬依赖）。

    统一走 `core/platform/media`：影视不再依赖乐库的实现（v1.0.0 解耦）。
    """
    return _probe_duration_ms(path)


class VideoLibrary:
    """本地影视库（数据库位于应用数据目录 video.db）。"""

    def __init__(self, storage: VideoStorage, video_dir: Path,
                 registry: VideoRegistry | None = None):
        self.storage = storage
        self.video_dir = Path(video_dir)
        self.video_dir.mkdir(parents=True, exist_ok=True)
        self.registry = registry if registry is not None else default_registry()

    # ---------- 导入本地文件 ----------

    def build_video(self, path: str | Path, *, category: str = "", source: str = "local") -> Video:
        file_path = Path(path)
        title, episode_label, episode_index = parse_video_name(file_path.stem)
        try:
            size = file_path.stat().st_size
        except OSError:
            size = 0
        # 同一目录下的剧集聚成一个系列，便于「按剧归组」
        series_key = f"local:{file_path.parent}"
        return Video(
            file_path=str(file_path),
            title=title or file_path.stem,
            kind="other",
            series_key=series_key,
            episode_index=episode_index,
            episode_label=episode_label,
            category=category,
            duration_ms=probe_duration_ms(file_path),
            size_bytes=size,
            format=file_path.suffix.lstrip(".").lower(),
            source=source,
            quality="本地",
        )

    def import_paths(self, paths: Iterable[str | Path], *, category: str = "",
                     on_progress: ProgressFn | None = None) -> List[Video]:
        """导入文件/文件夹到影视库，返回新增或更新的条目。"""
        files = scan_video_files(paths)
        videos: list[Video] = []
        for index, file_path in enumerate(files, start=1):
            video = self.build_video(file_path, category=category)
            video.id = self.storage.upsert_video(video)
            videos.append(video)
            if on_progress:
                on_progress(index, len(files), f"已导入 {index}/{len(files)}：{video.display()}")
        return videos

    def import_and_copy(self, paths: Iterable[str | Path], *, category: str = "",
                        on_progress: ProgressFn | None = None) -> List[Video]:
        """把外部文件复制进影视库目录后再入库（便于统一管理）。"""
        copied: list[Path] = []
        for file_path in scan_video_files(paths):
            source = Path(file_path)
            target = source
            try:
                if self.video_dir not in source.parents:
                    target = copy_local(source, self.video_dir)
            except OSError:
                target = source
            copied.append(target)
        return self.import_paths(copied, category=category, on_progress=on_progress)

    # ---------- 在线条目入库 ----------

    def ensure_video(self, remote: RemoteVideo, episode: Episode | None = None) -> Video:
        """确保在线条目（某一集）在库里有对应行，返回它。

        在线播放不下载也会入库一行（file_path 为空），这样播放历史、收藏、
        分类都能正常引用，用户之后想下载也有稳定的 id 可用。
        """
        video = remote_to_video(remote, episode)
        existing = self.storage.find_remote_video(video.source, video.remote_id, video.episode_index)
        if existing is not None:
            # 保留用户已有的分类/收藏/统计，仅刷新元数据
            video.id = existing.id
            video.favorited = existing.favorited
            video.category = existing.category or video.category
            video.play_count = existing.play_count
            video.added_at = existing.added_at
            video.file_path = existing.file_path
            self.storage.upsert_video(video)
            return self.storage.get_video(existing.id) or video
        video.id = self.storage.upsert_video(video)
        return self.storage.get_video(video.id) or video

    def import_download_result(self, result: DownloadResult) -> Video | None:
        """把一次下载结果入库（失败项返回 None）。"""
        if not result.ok or not result.path:
            return None
        path = Path(result.path)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        video = remote_to_video(
            result.video, result.episode,
            file_path=str(path), size_bytes=size,
            duration_ms=result.duration_ms or probe_duration_ms(path),
            quality=result.quality_label or "原画",
        )
        video.format = path.suffix.lstrip(".").lower()
        video.source = result.source or video.source
        # 已下载的条目覆盖同源的在线行（保留收藏/分类）
        existing = self.storage.find_remote_video(video.source, video.remote_id, video.episode_index)
        if existing is not None:
            video.id = existing.id
            video.favorited = existing.favorited
            video.category = existing.category or video.category
            video.play_count = existing.play_count
        video.id = self.storage.upsert_video(video)
        self.storage.add_history(video.id, "download", episode_label=video.episode_label,
                                 quality=video.quality, source=video.source, title=video.title)
        return self.storage.get_video(video.id)

    def import_download_results(self, results: Iterable[DownloadResult]) -> List[Video]:
        videos: list[Video] = []
        for result in results:
            try:
                video = self.import_download_result(result)
            except Exception:  # noqa: BLE001  单条入库失败不影响其他
                video = None
            if video is not None:
                videos.append(video)
        return videos

    # ---------- 播放 ----------

    def resolve_playback(
        self,
        remote: RemoteVideo,
        episode: Episode | None = None,
        quality: Quality | None = None,
        *,
        allow_cross_source: bool = True,
        exclude: Iterable[str] = (),
    ) -> tuple[ResolvedPlay, Video]:
        """解析在线播放地址并确保条目入库；返回 (解析结果, 库中条目)。

        这是「只要有网就能看」的核心：拿不到直链就自动换源，
        解析成功后把直链记进 play_records，下次可直接续播。
        """
        target = episode or (remote.episodes[0] if remote.episodes else None)
        resolved = self.registry.resolve(remote, target, quality,
                                         allow_cross_source=allow_cross_source,
                                         exclude=exclude)
        resolved = self._lock_best_variant(resolved)
        video = self.ensure_video(resolved.video, resolved.episode)
        # 解析结果的直链是「本次可用」的，重新解析过就不再沿用旧清晰度
        video.remote_url = resolved.url
        if resolved.quality is not None:
            video.quality = resolved.quality.label
        if resolved.episode is not None:
            video.episode_label = resolved.episode.name
            video.episode_index = resolved.episode.index
        self.storage.save_play_record(
            video.id, video.episode_label,
            resolved.quality_label or video.quality, resolved.url, resolved.source,
        )
        return resolved, video

    def load_play_record(self, video_id: int, episode_label: str = "") -> str:
        """取上次播放用的直链（可能已过期，调用方需容忍失败并重新解析）。"""
        record = self.storage.get_play_record(video_id, episode_label)
        return record.url if record else ""

    def _lock_best_variant(self, resolved: ResolvedPlay) -> ResolvedPlay:
        """用户没指定画质时，尽量把地址锁定到最高清晰度。

        主清单直接交给播放器时「选哪一路」由播放器/ffmpeg 决定，行为不可控；
        锁定最高变体既能保证画质，也避免播放过程中的码率切换抖动。

        两条路径，先便宜后昂贵：
        1. 源自己声明了多个带地址的清晰度 → 直接用最高的（零网络开销）；
        2. 否则探测 m3u8 主清单 —— 用**短超时**客户端（拿源的默认头，避免被 403）。
        任何失败都保持原样：绝不因为"锦上添花"让播放失败或变慢。
        """
        if resolved.quality is not None or not resolved.url:
            return resolved

        episode = resolved.episode
        declared = [item for item in ((episode.qualities if episode is not None else []) or [])
                    if getattr(item, "url", "")]
        if len(declared) >= 2:
            best = max(declared, key=lambda item: item.rank)
            if best.url and best.url != resolved.url:
                resolved.url = best.url
            resolved.quality = best
            return resolved

        if not resolved.is_hls:
            return resolved
        try:
            from .hls import list_qualities
            from .sources.http import HttpClient

            provider = self.registry.get(resolved.source)
            client = _probe_client(provider)
            variants = list_qualities(resolved.url, http=client, referer=resolved.referer)
        except Exception:  # noqa: BLE001
            return resolved
        if len(variants) < 2:
            return resolved
        best_variant = max(variants, key=lambda item: item.rank)
        if not best_variant.url:
            return resolved
        resolved.url = best_variant.url
        resolved.quality = Quality(label=best_variant.display, url=best_variant.url)
        return resolved

    def find_higher_quality(
        self,
        remote: RemoteVideo,
        episode: Episode | None = None,
        *,
        current_height: int = 0,
        exclude: Iterable[str] = (),
        on_progress: Callable[[str], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> HdCandidate | None:
        """去**其他源**找同一部片、同一集里分辨率最高的线路。

        为什么需要它：采集站常常只给一条线路，画质完全由站点决定
        （实测 cms_360 的《流浪地球》主清单只有 1280x538 / 706 kbps，
        用户端表现为"只有 540P、很模糊"）。同一条片子在别的源上往往有 1080P，
        因此这里逐个源「搜索 → 匹配同一集 → 探测 m3u8 主清单」，
        返回比当前 `current_height` 更高的最佳候选；找不到就返回 None。

        只读探测：不改注册表健康度、不写库、不下载。
        """
        from .hls import list_qualities
        from .sources import best_match

        target_name = episode.name if episode is not None else ""
        target_index = episode.index if episode is not None else 0
        skipped = {remote.source, *exclude}
        best: HdCandidate | None = None

        for key in self.registry.order:
            if cancel is not None and cancel.is_set():
                return best
            if key in skipped or not self.registry.is_enabled(key):
                continue
            provider = self.registry.get(key)
            if provider is None or not provider.available():
                continue
            label = str(getattr(provider.info, "label", "") or key)
            if on_progress is not None:
                on_progress(f"正在检查「{label}」…")
            try:
                candidates = provider.search(remote.title, kind="all", limit=10)
            except Exception:  # noqa: BLE001  单个源失败不影响其它源
                continue
            matched = best_match(remote, candidates, threshold=0.62)
            if matched is None:
                continue
            if not matched.episodes:
                try:
                    matched = provider.detail(matched) or matched
                except Exception:  # noqa: BLE001
                    continue
            target = _same_episode(matched, target_name, target_index)
            if target is None:
                continue
            try:
                url = provider.play_url(matched, target)
            except Exception:  # noqa: BLE001
                continue
            if not url:
                continue
            referer = str(provider.default_headers().get("Referer", "") or "")
            variants = list_qualities(url, http=provider.http, referer=referer)
            if not variants:
                continue
            top = max(variants, key=lambda item: item.rank)
            floor = max(current_height, best.height if best is not None else 0)
            if top.height and top.height > floor:
                best = HdCandidate(remote=matched, episode=target, variant=top,
                                   source_key=key, source_label=label)
        return best


    def record_play(self, video: Video, *, quality: str = "", source: str = "") -> None:
        """记录一次播放：播放次数 + 历史 + 最后播放时间。"""
        if not video.id:
            return
        self.storage.mark_played(video.id)
        self.storage.add_history(
            video.id, "play", episode_label=video.episode_label,
            quality=quality or video.quality, source=source or video.source,
            title=video.title,
        )
        if quality:
            video.quality = quality

    def update_duration(self, video_id: int, duration_ms: int) -> bool:
        return self.storage.update_duration(video_id, duration_ms)

    def series_episodes(self, video: Video) -> List[Video]:
        """同剧的其他已入库剧集（按季/集排序）。"""
        if not video.series_key:
            return []
        return self.storage.list_series_episodes(video.series_key)

    # ---------- 收藏 / 分类 ----------

    def toggle_favorite(self, video_id: int) -> bool:
        return self.storage.toggle_favorite(video_id)

    def set_category(self, video_ids: Iterable[int], category: str) -> int:
        return self.storage.set_category(video_ids, category)

    def create_category(self, name: str) -> int:
        return self.storage.create_playlist(name, kind="category")

    def add_to_collection(self, playlist_id: int, video_ids: Iterable[int]) -> int:
        return self.storage.add_to_playlist(playlist_id, video_ids)

    # ---------- 下载 ----------

    def download(
        self,
        remote: RemoteVideo,
        episode: Episode | None = None,
        quality: Quality | None = None,
        *,
        dest_dir: str | Path | None = None,
        on_progress: ProgressFn | None = None,
        cancel: threading.Event | None = None,
        allow_cross_source: bool = True,
    ) -> DownloadResult:
        """下载单集到目标目录并入库，返回结果（含实际使用的源）。"""
        target_dir = Path(dest_dir) if dest_dir else self.video_dir
        try:
            path, resolved = download_episode(
                remote, episode, target_dir, quality=quality, on_progress=on_progress,
                cancel=cancel, registry=self.registry, allow_cross_source=allow_cross_source,
            )
        except Exception as error:  # noqa: BLE001
            return DownloadResult(video=remote, episode=episode, quality=quality,
                                  ok=False, message=str(error))
        size = path.stat().st_size if path.is_file() else 0
        result = DownloadResult(
            video=resolved.video, episode=resolved.episode, quality=resolved.quality,
            path=str(path), ok=True, source=resolved.source,
            switched_from=resolved.switched_from, note=resolved.note,
            size_bytes=size,
        )
        video = self.import_download_result(result)
        if video is not None:
            result.duration_ms = video.duration_ms
        return result

    def download_many(
        self,
        tasks: Iterable[tuple[RemoteVideo, Episode | None, Quality | None]],
        *,
        dest_dir: str | Path | None = None,
        on_progress: ProgressFn | None = None,
        on_task_done: Callable[[DownloadResult], None] | None = None,
        cancel: threading.Event | None = None,
        allow_cross_source: bool = True,
    ) -> List[DownloadResult]:
        """批量下载（逐集），完成后统一入库。"""
        target_dir = Path(dest_dir) if dest_dir else self.video_dir
        results = download_many(
            tasks, target_dir, on_progress=on_progress, on_task_done=on_task_done,
            cancel=cancel, registry=self.registry, allow_cross_source=allow_cross_source,
        )
        self.import_download_results(results)
        return results

    # ---------- 转换 ----------

    def convert(self, video_id: int, target_format: str,
                output_dir: str | Path | None = None,
                cancel: threading.Event | None = None,
                on_progress: ProgressFn | None = None) -> Path:
        """把本地视频转换为目标格式（含「提取音频」），返回输出路径。"""
        from modu_workbench.core.convert.media_io import convert_media

        target_format = (target_format or "").lower()
        if target_format not in VIDEO_TARGETS:
            raise ValueError(f"不支持的转换目标：{target_format}")
        video = self.storage.get_video(video_id)
        if video is None:
            raise ValueError("视频不存在")
        source = Path(video.file_path)
        if not source.is_file():
            raise FileNotFoundError(f"文件不存在：{video.file_path}（在线条目请先下载）")
        if video.format == target_format:
            raise ValueError(f"源文件已经是 {target_format.upper()} 格式")
        if not find_ffmpeg():
            raise ValueError("未找到 ffmpeg：请安装 ffmpeg 或设置 MODU_FFMPEG 指向二进制")

        target_dir = Path(output_dir) if output_dir else self.video_dir / "converted"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{source.stem}.{target_format}"
        index = 2
        while target.exists():
            target = target_dir / f"{source.stem} ({index}).{target_format}"
            index += 1

        if on_progress:
            on_progress(0, 0, f"转换中：{source.name} → {target_format.upper()}")
        convert_media(source, target_format, target, cancel=cancel)
        if on_progress:
            on_progress(1, 1, f"完成：{target.name}")

        # 转换出的视频也入库（音频产物不入视频库）
        if f".{target_format}" in VIDEO_EXTENSIONS:
            try:
                self.import_paths([target], category=video.category)
            except Exception:  # noqa: BLE001
                pass
        return target

    # ---------- 维护 ----------

    def refresh_durations(self, video_ids: Iterable[int] | None = None) -> int:
        """为缺少时长的本地条目补齐 duration_ms。"""
        ids = list(video_ids) if video_ids is not None else [
            v.id for v in self.storage.list_videos(local_only=True)
        ]
        updated = 0
        for video_id in ids:
            video = self.storage.get_video(video_id)
            if video is None or video.duration_ms or not video.exists:
                continue
            duration = probe_duration_ms(video.file_path)
            if duration:
                self.storage.update_duration(video_id, duration)
                updated += 1
        return updated

    def missing_files(self) -> List[Video]:
        """本地文件已丢失的条目（用于界面提示与清理）。"""
        return [v for v in self.storage.list_videos(local_only=True) if not v.exists]

    def delete_with_files(self, video_ids: Iterable[int], *, remove_file: bool = False) -> int:
        """删除条目；可选同时删除本地文件。"""
        ids = list(video_ids)
        for video_id in ids:
            video = self.storage.get_video(video_id)
            if video is None or not remove_file or not video.file_path:
                continue
            try:
                Path(video.file_path).unlink(missing_ok=True)
            except OSError:
                pass
        return self.storage.delete_videos(ids)

    def copy_into_library(self, path: str | Path) -> Path:
        """把外部文件复制进影视库目录。"""
        return copy_local(path, self.video_dir)

    def disk_usage(self) -> int:
        total = 0
        for video in self.storage.list_videos(local_only=True):
            try:
                total += Path(video.file_path).stat().st_size
            except OSError:
                continue
        return total


__all__ = [
    "SourceError",
    "VideoLibrary",
    "is_video_file",
    "probe_duration_ms",
    "scan_video_files",
]
