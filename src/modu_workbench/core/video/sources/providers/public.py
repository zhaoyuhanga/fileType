"""公共领域 / 自由授权影视源：Internet Archive 与 Wikimedia Commons。

这两个源的价值在于「完全可自由下载与分发」，不依赖第三方采集站，
因此下载与离线观看体验最稳定（走标准 HTTP 直链，无需 m3u8 合流）。
"""
from __future__ import annotations

from typing import List
from urllib.parse import quote, urlencode

from ...models import Episode, Quality, RemoteVideo
from ..base import KIND_CC, KIND_FULL, SourceInfo, VideoSource
from ..http import HttpClient, SourceError, host_of
from .cms_vod import CmsVodSource

# Archive.org 上视频文件的常见容器格式（挑优顺序：mp4 优先，浏览器可直接播）
_ARCHIVE_VIDEO_FORMATS = (
    ("h.264", ".mp4"), ("mpeg4", ".mp4"), ("512kb mpeg4", ".mp4"),
    ("ogg video", ".ogv"), ("webm", ".webm"), ("matroska", ".mkv"),
    ("mpeg2", ".mpg"), ("windows media", ".wmv"), ("quicktime", ".mov"),
)
_ARCHIVE_VIDEO_EXTS = (".mp4", ".m4v", ".webm", ".ogv", ".mkv", ".mpg", ".mpeg", ".avi", ".mov", ".wmv", ".flv")


class ArchiveOrgVideoSource(VideoSource):
    """Internet Archive：公共领域电影、老动画与纪录片（可自由下载）。"""

    info = SourceInfo(
        key="archive",
        label="Internet Archive",
        note="公共领域电影 / 动画 / 纪录片，全部可自由下载与离线观看",
        kind=KIND_CC,
        homepage="https://archive.org",
    )

    SEARCH_URL = "https://archive.org/advancedsearch.php"
    META_URL = "https://archive.org/metadata/{identifier}"
    DOWNLOAD_URL = "https://archive.org/download/{identifier}/{name}"

    def search(self, keyword: str, kind: str = "all", limit: int = 30,
               page: int = 1) -> List[RemoteVideo]:
        if not (keyword or "").strip():
            raise SourceError("请输入搜索关键词")
        # 「完整长片」优先：feature_films / silent_films / classic_cartoons 才是长片馆藏。
        # 裸 mediatype:movies 会把 tvarchive、opensource_movies 里的**解说类短视频**一起搜出来
        # —— 用户反馈"archive 里都是解说"就是这个原因（实测：限定馆藏后 Sherlock 前 8 条有 7 条 ≥40 分钟）。
        # 精确检索无结果时退回宽检索，避免这个源因为收紧而变得不可用。
        docs = self._search_docs(
            f'mediatype:movies AND collection:(feature_films OR silent_films OR classic_cartoons)'
            f' AND ({keyword})',
            limit=limit, page=page,
        )
        if not docs:
            docs = self._search_docs(f'mediatype:movies AND ({keyword})', limit=limit, page=page)
        results: List[RemoteVideo] = []
        for doc in docs:
            identifier = str(doc.get("identifier") or "").strip()
            title = self._first(doc.get("title"))
            if not identifier or not title:
                continue
            creator = self._first(doc.get("creator"))
            results.append(RemoteVideo(
                source=self.key,
                remote_id=identifier,
                title=title,
                kind="movie",
                year=str(self._first(doc.get("year")) or "")[:4],
                region="",
                category="公共领域",
                tags="Internet Archive",
                actors="",
                director=creator,
                description=self._text(doc.get("description")),
                cover_url=f"https://archive.org/services/img/{identifier}",
                series_key=f"{self.key}:{identifier}",
            ))
        return results

    def _search_docs(self, query: str, *, limit: int, page: int) -> list:
        """跑一次 advancedsearch，返回 docs 列表（失败按空处理，由调用方决定是否退回宽检索）。"""
        params = [
            ("q", query),
            ("fl[]", "identifier"), ("fl[]", "title"), ("fl[]", "year"),
            ("fl[]", "description"), ("fl[]", "creator"), ("fl[]", "downloads"),
            ("rows", str(max(1, min(limit, 50)))),
            ("page", str(max(1, page))),
            ("output", "json"),
        ]
        payload = self.http.get_json(f"{self.SEARCH_URL}?{urlencode(params)}")
        return ((payload or {}).get("response") or {}).get("docs") or []

    def detail(self, video: RemoteVideo) -> RemoteVideo:
        """拉取文件清单，挑选可播放的视频文件作为分集。"""
        if not video.remote_id:
            return video
        payload = self.http.get_json(self.META_URL.format(identifier=quote(video.remote_id, safe="")))
        files = (payload or {}).get("files") or []
        episodes: list[Episode] = []
        for item in files:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            if not name or not self._is_video(item, name):
                continue
            url = self.DOWNLOAD_URL.format(identifier=quote(video.remote_id, safe=""),
                                          name=quote(name, safe=""))
            size = self._int(item.get("size"))
            episodes.append(Episode(
                name=name,
                url=url,
                index=len(episodes),
                route=str(item.get("format") or ""),
                qualities=[Quality(label=self._quality_of(item, name), url=url)],
            ))
        # 优先 mp4（浏览器与本地播放器兼容性最好）
        episodes.sort(key=lambda e: (0 if e.url.lower().endswith(".mp4") else 1, e.index))
        for index, episode in enumerate(episodes):
            episode.index = index
        video.episodes = episodes
        if not video.description:
            video.description = self._text(((payload or {}).get("metadata") or {}).get("description"))
        return video

    def play_url(self, video: RemoteVideo, episode: Episode,
                 quality: Quality | None = None) -> str:
        if quality is not None and quality.url:
            return quality.url
        if episode is not None and episode.url:
            return episode.url
        raise SourceError("该馆藏没有可播放的视频文件")

    @staticmethod
    def _is_video(item: dict, name: str) -> bool:
        lowered = name.lower()
        if not lowered.endswith(_ARCHIVE_VIDEO_EXTS):
            return False
        # 排除缩略图/字幕/元数据文件
        if any(token in lowered for token in ("_thumb", "thumb.", "spectrogram", ".gif")):
            return False
        return True

    @staticmethod
    def _quality_of(item: dict, name: str) -> str:
        text = f"{item.get('format') or ''} {name}".lower()
        if "1080" in text:
            return "1080P"
        if "720" in text:
            return "720P"
        if "480" in text:
            return "480P"
        if "h.264" in text or "mpeg4" in text or name.lower().endswith(".mp4"):
            return "高清"
        return "原片"

    @staticmethod
    def _first(value) -> str:  # noqa: ANN001
        if isinstance(value, list):
            return str(value[0]) if value else ""
        return str(value or "")

    @staticmethod
    def _text(value) -> str:  # noqa: ANN001
        text = ArchiveOrgVideoSource._first(value)
        return text[:600]

    @staticmethod
    def _int(value) -> int:  # noqa: ANN001
        try:
            return int(float(str(value)))
        except (TypeError, ValueError):
            return 0


class WikimediaVideoSource(VideoSource):
    """Wikimedia Commons：自由授权的影片/纪录片段（可自由下载）。"""

    info = SourceInfo(
        key="wikimedia",
        label="Wikimedia Commons",
        note="自由授权的影片与纪录片段，允许自由下载与再分发",
        kind=KIND_CC,
        homepage="https://commons.wikimedia.org",
    )

    API = "https://commons.wikimedia.org/w/api.php"

    def search(self, keyword: str, kind: str = "all", limit: int = 30,
               page: int = 1) -> List[RemoteVideo]:
        if not (keyword or "").strip():
            raise SourceError("请输入搜索关键词")
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f"filetype:video {keyword}",
            "gsrnamespace": "6",          # File:
            "gsrlimit": str(max(1, min(limit, 50))),
            "gsroffset": str(max(0, (max(1, page) - 1) * max(1, min(limit, 50)))),
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
        }
        payload = self.http.get_json(self.API, params=params)
        pages = ((payload or {}).get("query") or {}).get("pages") or {}
        results: List[RemoteVideo] = []
        for item in pages.values():
            if not isinstance(item, dict):
                continue
            infos = item.get("imageinfo") or []
            if not infos:
                continue
            info = infos[0]
            mime = str(info.get("mime") or "")
            if not mime.startswith("video/"):
                continue
            url = str(info.get("url") or "")
            title = str(item.get("title") or "").replace("File:", "").strip()
            if not url or not title:
                continue
            meta = info.get("extmetadata") or {}
            results.append(RemoteVideo(
                source=self.key,
                remote_id=str(item.get("pageid") or title),
                title=title,
                kind="other",
                year=self._meta_year(meta),
                category="自由授权",
                tags="Wikimedia Commons",
                director=self._meta_value(meta, "Artist"),
                description=self._meta_value(meta, "ImageDescription")[:600],
                cover_url=self._thumb_of(url),
                series_key=f"{self.key}:{item.get('pageid') or title}",
                episodes=[Episode(
                    name=title, url=url, index=0, route="原始文件",
                    qualities=[Quality(label=self._quality_of(info), url=url)],
                )],
            ))
        return results

    def detail(self, video: RemoteVideo) -> RemoteVideo:
        return video   # 搜索结果已含唯一视频文件

    @staticmethod
    def _quality_of(info: dict) -> str:
        height = 0
        try:
            height = int(info.get("height") or 0)
        except (TypeError, ValueError):
            height = 0
        if height >= 2000:
            return "4K"
        if height >= 1000:
            return "1080P"
        if height >= 700:
            return "720P"
        if height >= 400:
            return "480P"
        return "原片"

    @staticmethod
    def _thumb_of(url: str) -> str:
        """Commons 视频缩略图：同名 .jpg 变体。"""
        lowered = url.lower()
        for suffix in (".webm", ".ogv", ".mp4", ".mpg", ".mpeg"):
            if lowered.endswith(suffix):
                return url[: -len(suffix)] + ".jpg"
        return ""

    @staticmethod
    def _meta_value(meta: dict, key: str) -> str:
        entry = meta.get(key) or {}
        if isinstance(entry, dict):
            return str(entry.get("value") or "")
        return str(entry or "")

    @classmethod
    def _meta_year(cls, meta: dict) -> str:
        text = cls._meta_value(meta, "DateTimeOriginal") or cls._meta_value(meta, "DateTime")
        import re

        match = re.search(r"(19|20)\d{2}", text)
        return match.group(0) if match else ""


class DirectUrlVideoSource(VideoSource):
    """直链源：把用户粘贴的地址直接当作一个可播放条目。"""

    info = SourceInfo(
        key="url",
        label="视频直链",
        note="直接粘贴 m3u8 / mp4 等地址播放或下载（自备链接）",
        kind=KIND_CC,
        downloadable=True,
    )

    def search(self, keyword: str, kind: str = "all", limit: int = 30,
               page: int = 1) -> List[RemoteVideo]:
        url = (keyword or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            raise SourceError("请输入以 http(s):// 开头的视频直链")
        name = url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1] or "视频直链"
        lowered = url.lower()
        is_hls = ".m3u8" in lowered
        suffix = ".m3u8" if is_hls else ("." + name.rsplit(".", 1)[-1].lower() if "." in name else "")
        return [RemoteVideo(
            source=self.key,
            remote_id=url,
            title=name,
            kind="other",
            category="直链",
            tags=host_of(url),
            episodes=[Episode(
                name="正片", url=url, index=0, route="直链",
                qualities=[Quality(label="原画", url=url)],
            )],
            series_key=f"{self.key}:{url}",
            extra={"format": suffix.lstrip("."), "is_hls": is_hls},
        )]

    def detail(self, video: RemoteVideo) -> RemoteVideo:
        return video

    def supports_keyword_search(self) -> bool:
        """直链源只能凭地址检索，不参与关键词聚合搜索（否则每次搜索都多一条报错）。"""
        return False


class CustomVodSource(CmsVodSource):
    """用户自定义采集接口：地址存于设置里，可在「源设置」中增删改。

    复用苹果 CMS 解析逻辑 —— 绝大多数第三方采集接口都遵循该协议。
    """

    info = SourceInfo(
        key="custom",
        label="自定义采集源",
        note="填入任意苹果CMS协议的采集接口地址（api.php/provide/vod）",
        kind=KIND_FULL,
        homepage="",
        editable=True,
    )

    def __init__(self, *, http: HttpClient | None = None, base_url: str = ""):
        super().__init__(self.info, http=http, base_url=base_url)

    def available(self) -> bool:
        return bool(self.base_url)

    def unavailable_reason(self) -> str:
        return "尚未配置自定义采集接口地址（可在「源设置」中填写）"
