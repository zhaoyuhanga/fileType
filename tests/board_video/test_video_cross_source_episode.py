"""换源后「第几集」必须对得上。

## 事故背景

用户下载多集，文件里出现的却是别的集数（前后顺序对不上）。原因有两个：

1. `VideoSource.play_url` 无条件优先 `quality.url`（当前预览那一集的地址）——
   于是每一集都下成了同一集；这一条在 `test_video_episode_url.py` 里守；
2. 换源兜底 `_pick_episode` **先按 index 匹配**：不同站点的分集顺序可能相反
   （新的在前），第 1 集会被映射成对方的最后一集。这里改成"集名 → 集号 → 序号"。

两条合起来才是用户看到的"每一集文件名不同、内容全一样"。
"""
from __future__ import annotations

from modu_workbench.core.video import Episode, RemoteVideo
from modu_workbench.core.video.sources import VideoRegistry


def _episode(name: str, index: int, url: str = "https://cdn/x.m3u8") -> Episode:
    return Episode(name=name, url=url, index=index)


def _matched(episodes: list[Episode]) -> RemoteVideo:
    video = RemoteVideo(source="dytm3u8", remote_id="1", title="潜伏2009")
    video.episodes = episodes
    return video


def test_number_wins_over_index_when_order_is_reversed() -> None:
    """对方站点倒序（第03集在最前）：按集号找到第01集，而不是 index=0 的第03集。"""
    registry = VideoRegistry([])
    matched = _matched(
        [
            _episode("dytm3u8 · 第03集", 0),
            _episode("dytm3u8 · 第02集", 1),
            _episode("dytm3u8 · 第01集", 2),
        ]
    )
    picked = registry._pick_episode(_episode("ffm3u8 · 第01集", 0), matched)  # noqa: SLF001
    assert picked is not None
    assert picked.name.endswith("第01集"), f"换源后集数错了：{picked.name}"


def test_ep_prefix_matches_chinese_number() -> None:
    """`EP07` 与 `第07集` 视为同一集。"""
    registry = VideoRegistry([])
    matched = _matched([_episode("第07集", 0), _episode("第08集", 1)])
    picked = registry._pick_episode(_episode("EP07", 0), matched)  # noqa: SLF001
    assert picked is not None and picked.name == "第07集"


def test_plain_names_still_fall_back_to_index() -> None:
    """两边都没有可识别集号时（正片/花絮），仍按序号匹配。"""
    registry = VideoRegistry([])
    matched = _matched([_episode("正片", 0), _episode("花絮", 1)])
    picked = registry._pick_episode(_episode("其它", 1), matched)  # noqa: SLF001
    assert picked is not None and picked.name == "花絮"


def test_tail_name_matches_across_route_prefixes() -> None:
    """线路前缀不同、集名相同：按去掉前缀后的名字匹。"""
    registry = VideoRegistry([])
    matched = _matched([_episode("dytm3u8 · 第05集（高清）", 0)])
    picked = registry._pick_episode(_episode("ffm3u8 · 第05集", 3), matched)  # noqa: SLF001
    assert picked is not None and "第05集" in picked.name


def test_missing_episodes_returns_none() -> None:
    registry = VideoRegistry([])
    matched = RemoteVideo(source="dytm3u8", remote_id="2", title="空")
    assert registry._pick_episode(_episode("第01集", 0), matched) is None  # noqa: SLF001
