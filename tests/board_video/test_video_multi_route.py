"""多线路 = 同一批集的不同线路，不是「集数翻倍」。

## 现场

实际 30 集的剧，详情页显示「共 60 集」：苹果 CMS 的 `vod_play_url` 用 `$$$`
分隔线路，同一部剧的「feifan」和「ffm3u8」两条线路各有 30 集。旧实现把每条
线路都展开成独立分集，还把线路名拼进集名（`feifan · 第01集`），于是：

- 集数翻倍（60 集）；
- 下载时同一集出现两次，容易下错/重复。

现在按「集号 → 集名」合并：一集只出现一次，线路进 `episode.qualities`，
播放/下载仍可切线路。
"""
from __future__ import annotations

from modu_workbench.core.video import Quality
from modu_workbench.core.video.sources.providers.cms_vod import parse_play_urls


def _two_routes(count: int = 30) -> tuple[str, str]:
    first = "#".join(f"第{index:02d}集$https://feifan/{index}.m3u8" for index in range(1, count + 1))
    second = "#".join(
        f"第{index:02d}集$https://ffm3u8/{index}.m3u8" for index in range(1, count + 1)
    )
    return f"{first}$$${second}", "feifan$$$ffm3u8"


def test_two_routes_do_not_double_episode_count() -> None:
    episodes = parse_play_urls(*_two_routes())
    assert len(episodes) == 30, f"两条线路把 30 集变成了 {len(episodes)} 集"
    assert [episode.name for episode in episodes] == [
        f"第{index:02d}集" for index in range(1, 31)
    ], "集名不该再带线路前缀"


def test_primary_route_is_the_episode_url() -> None:
    episodes = parse_play_urls(*_two_routes())
    assert episodes[4].url == "https://feifan/5.m3u8"
    assert episodes[4].index == 4


def test_other_routes_become_qualities_and_can_be_selected() -> None:
    episodes = parse_play_urls(*_two_routes())
    labels = [quality.label for quality in episodes[4].qualities]
    assert labels == ["feifan", "ffm3u8"], f"线路没有进清晰度：{labels}"
    chosen = next(item for item in episodes[4].qualities if item.label == "ffm3u8")
    assert chosen.url == "https://ffm3u8/5.m3u8"


def test_quality_hint_from_route_name_is_preferred() -> None:
    """线路名里的画质线索优先当标签（`1080p` → `1080P`）。"""
    urls = [
        "第01集$https://a/1.m3u8",
        "第01集$https://b/1.m3u8",
    ]
    episodes = parse_play_urls("$$$".join(urls), "1080p$$$720p")
    labels = [quality.label for quality in episodes[0].qualities]
    assert labels == ["1080P", "720P"]


def test_single_route_without_hint_keeps_no_fake_quality() -> None:
    episodes = parse_play_urls("第01集$https://a/1.m3u8", "")
    assert len(episodes) == 1
    assert episodes[0].qualities == []


def test_reversed_order_is_sorted_by_episode_number() -> None:
    """站点按「新→旧」列分集时，界面仍从第 1 集排起。"""
    route = "#".join(f"第{index:02d}集$https://a/{index}.m3u8" for index in (3, 2, 1))
    episodes = parse_play_urls(route, "")
    assert [episode.name for episode in episodes] == ["第01集", "第02集", "第03集"]
    assert [episode.index for episode in episodes] == [0, 1, 2]

