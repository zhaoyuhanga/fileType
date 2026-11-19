"""批量下载"每一集都下成同一集"的回归测试。

## 事故

用户在详情页勾选多集下载，结果所有文件**大小完全一样、内容都是同一集**，
只有文件名不同。根因在 `VideoSource.play_url`：

```python
if quality is not None and quality.url:
    return quality.url      # ← 无条件优先
```

详情页传给下载的是"当前预览那一集"的画质对象（`Quality` 自带 url）。
批量下载时每一集都拿到这同一个 `quality.url`，于是 40 集全下成同一集。

## 契约

`quality` 只在该画质**确实属于这一集**时才生效：

1. 先按画质标签在本集的候选线路里找（跨集时 `1080P` 才对得上）；
2. 再按 url 在本集的候选线路里找；
3. 都对不上就用本集自己的 `episode.url`；
4. 只有在"这一集没有自己的地址"时，才退回 `quality.url`。
"""
from __future__ import annotations

import pytest

from modu_workbench.core.video import Episode, Quality, RemoteVideo
from modu_workbench.core.video.sources import SourceInfo, VideoSource


class _Source(VideoSource):
    info = SourceInfo(key="t", label="桩源", note="test")

    def search(self, keyword: str, kind: str = "all", limit: int = 30, page: int = 1):  # noqa: ANN001
        return []

    def detail(self, video: RemoteVideo) -> RemoteVideo:
        return video


def _series() -> RemoteVideo:
    video = RemoteVideo(source="t", remote_id="1", title="潜伏2009")
    video.episodes = [
        Episode(
            name="第01集",
            url="https://cdn/1.m3u8",
            index=0,
            qualities=[Quality(label="1080P", url="https://cdn/1-1080.m3u8")],
        ),
        Episode(
            name="第02集",
            url="https://cdn/2.m3u8",
            index=1,
            qualities=[Quality(label="1080P", url="https://cdn/2-1080.m3u8")],
        ),
    ]
    return video


def test_quality_from_another_episode_does_not_leak() -> None:
    """详情页那集的画质对象，不能把其它集也带到同一个地址。"""
    source = _Source()
    video = _series()
    first_quality = video.episodes[0].qualities[0]

    assert source.play_url(video, video.episodes[0], first_quality) == "https://cdn/1-1080.m3u8"
    assert source.play_url(video, video.episodes[1], first_quality) == "https://cdn/2-1080.m3u8", (
        "第 2 集仍然拿到了第 1 集的画质地址 —— 批量下载会全部下成同一集"
    )


def test_same_label_quality_is_matched_per_episode() -> None:
    """跨集时按画质标签匹配（用户选了 1080P，每集都该是它自己的 1080P）。"""
    source = _Source()
    video = _series()
    # 只带标签、url 属于第一集：仍应按标签命中第二集自己的 1080P
    quality = Quality(label="1080P", url="https://cdn/1-1080.m3u8")
    assert source.play_url(video, video.episodes[1], quality) == "https://cdn/2-1080.m3u8"


def test_episode_url_used_when_no_quality_matches() -> None:
    """画质对不上（标签/url 都不在本集）时，退回本集自己的地址。"""
    source = _Source()
    video = _series()
    stranger = Quality(label="4K", url="https://other/1-4k.m3u8")
    assert source.play_url(video, video.episodes[0], stranger) == "https://cdn/1.m3u8"


def test_quality_url_still_works_without_episode_url() -> None:
    """没有分集地址的来源（直链/变体）仍要能用 quality.url。"""
    source = _Source()
    video = RemoteVideo(source="t", remote_id="2", title="直链")
    video.episodes = [Episode(name="正片", url="", index=0)]
    quality = Quality(label="1080P", url="https://cdn/direct-1080.m3u8")
    assert source.play_url(video, video.episodes[0], quality) == "https://cdn/direct-1080.m3u8"


def test_missing_everything_raises() -> None:
    source = _Source()
    video = RemoteVideo(source="t", remote_id="3", title="空")
    video.episodes = [Episode(name="正片", url="", index=0)]
    with pytest.raises(Exception):
        source.play_url(video, video.episodes[0], None)
