"""`update_check.py`：比对规则 + 并发/取消/失败隔离。

核心是**差异化更新**（"总集数变多"才报新集），因此这里的用例按方案 4.7 的场景逐一覆盖：
总集数增长、集数不变、末集改名、集数减少（绝不报"删集"）、首次记录、单条失败、取消。
"""
from __future__ import annotations

import threading

from conftest import import_anime

models = import_anime("models")
update_check = import_anime("update_check")

AnimeEpisode = models.AnimeEpisode
AnimeSeries = models.AnimeSeries
Road = models.Road
SeriesDetail = models.SeriesDetail
check_updates = update_check.check_updates


def make_series(remote_id: str = "s1", title: str = "进击的巨人",
                total: int = 0, latest: str = "") -> "AnimeSeries":
    return AnimeSeries(rule_key="builtin:a", remote_id=remote_id, title=title,
                       total_episodes=total, latest_episode=latest)


def make_detail(series: "AnimeSeries", names: list[str],
                roads: int = 1) -> "SeriesDetail":
    """按集名列表造详情（多线路时轮转切分，集号是**全局集号**）。

    集号按"整部番的顺序"编号而不是"线路内序号"：站点实际就是这样（第二个线路
    也从第 05 集接着编号），测试替身必须同构，否则会把 fixture 的错误当成代码行为。
    """
    chunks: list[list[tuple[int, str]]] = [[] for _ in range(max(1, roads))]
    for index, name in enumerate(names):
        chunks[index % len(chunks)].append((index + 1, name))
    built = [
        Road(index=road_index, name=f"线路{road_index + 1}",
             episodes=[AnimeEpisode(index=position, name=name, number=number)
                       for position, (number, name) in enumerate(chunk)])
        for road_index, chunk in enumerate(chunks)
    ]
    return SeriesDetail(series=series, roads=built)


def names(count: int, start: int = 1) -> list[str]:
    return [f"第{index:02d}集" for index in range(start, start + count)]


def test_new_episodes_when_total_grows() -> None:
    """复现方案里的"总集数变多"：只报多出来的那几集，顺序与站点一致。"""
    series = make_series(total=3, latest="第03集")
    detail = make_detail(series, names(5))
    report = check_updates([series], detail_fetcher=lambda _s: detail)
    assert report.checked == 1 and report.failed == 0
    item = report.items[0]
    assert item.new_total == 5 and item.old_total == 3
    assert item.new_episodes == ["第04集", "第05集"]
    assert item.latest_episode == "第05集"
    assert item.has_new is True
    assert [i.series.remote_id for i in report.with_new] == ["s1"]


def test_same_total_is_not_news_even_with_different_names() -> None:
    """集数没变 → 不报新集（站点换了片源名也不能当"更新"）。"""
    series = make_series(total=3, latest="第03集")
    detail = make_detail(series, ["第01集", "第02集", "第03集"])
    report = check_updates([series], detail_fetcher=lambda _s: detail)
    item = report.items[0]
    assert item.has_new is False and item.new_episodes == []
    assert item.latest_episode == "第03集"
    assert report.with_new == []
    assert item.message == "暂无更新"


def test_same_total_with_renamed_last_episode_reports_nothing() -> None:
    """集数、末集名都没变 → 即使中间某集改了名也不算更新（避免改名刷屏）。"""
    series = make_series(total=3, latest="第03集")
    detail = make_detail(series, ["第01集", "第02集（重制）", "第03集"])
    item = check_updates([series], detail_fetcher=lambda _s: detail).items[0]
    assert item.new_episodes == [] and item.has_new is False


def test_latest_episode_changed_reports_single_episode() -> None:
    """总数不变但末集名变了：只报这一集。"""
    series = make_series(total=3, latest="第03集")
    detail = make_detail(series, ["第01集", "第02集", "第03集（修正版）"])
    report = check_updates([series], detail_fetcher=lambda _s: detail)
    assert report.items[0].new_episodes == ["第03集（修正版）"]
    assert report.new_count == 1
    assert "新增 1 集" in report.items[0].message


def test_removals_are_never_reported_as_news() -> None:
    """站点下架/合并集数时绝不报"删集"（new_total < old_total → 0 条新增）。"""
    series = make_series(total=12, latest="第12集")
    detail = make_detail(series, names(8))
    report = check_updates([series], detail_fetcher=lambda _s: detail)
    item = report.items[0]
    assert item.new_episodes == [] and item.has_new is False
    assert (item.old_total, item.new_total) == (12, 8)


def test_first_record_is_not_treated_as_new_episodes() -> None:
    """库里还没记录集数（首次追番）时不算"新集"，否则一追番就弹 12 集提醒。"""
    series = make_series(total=0, latest="")
    detail = make_detail(series, names(12))
    report = check_updates([series], detail_fetcher=lambda _s: detail)
    item = report.items[0]
    assert item.new_episodes == []
    assert item.new_total == 12 and item.latest_episode == "第12集"


def test_declared_total_without_episode_list() -> None:
    """只有总数、没有剧集列表（API 模式常见）：给一条占位说明而不是编集名。"""
    series = make_series(total=3, latest="第03集")
    detail = SeriesDetail(series=make_series(total=5, latest="第05集"), roads=[])
    item = check_updates([series], detail_fetcher=lambda _s: detail).items[0]
    assert item.new_total == 5 and item.new_episodes == ["新增 2 集"]
    assert item.has_new is True


def test_episode_names_are_flattened_across_roads() -> None:
    """多线路时新增集要按"线路顺序 + 线路内顺序"摊平后按集号挑出来。"""
    series = make_series(total=4, latest="第04集")
    detail = make_detail(series, names(6), roads=2)
    item = check_updates([series], detail_fetcher=lambda _s: detail).items[0]
    assert item.new_total == 6
    assert item.new_episodes == ["第05集", "第06集"]


def test_new_episodes_use_episode_number_not_list_position() -> None:
    """线路里的顺序与更新顺序不一致时，必须按集号判新旧（不能按"列表末尾 N 项"切）。"""
    series = make_series(total=2, latest="第02集")
    detail = SeriesDetail(series=series, roads=[Road(index=0, name="乱序线路", episodes=[
        AnimeEpisode(index=0, name="第01集", number=1),
        AnimeEpisode(index=1, name="第03集", number=3),     # 新集排在中间
        AnimeEpisode(index=2, name="第02集", number=2),
    ])])
    item = check_updates([series], detail_fetcher=lambda _s: detail).items[0]
    assert item.new_episodes == ["第03集"]                  # 末尾 N 项会错报"第02集"


def test_number_gap_is_capped_by_total_growth() -> None:
    """整体重编号（旧的第 07 集变成新的"第 2 集"）时按"总集数增量"封顶，不误报。"""
    series = make_series(total=3, latest="第03集")
    detail = SeriesDetail(series=series, roads=[Road(index=0, name="线路", episodes=[
        AnimeEpisode(index=0, name="第07集", number=7),
        AnimeEpisode(index=1, name="第06集", number=6),
    ])])
    item = check_updates([series], detail_fetcher=lambda _s: detail).items[0]
    assert (item.old_total, item.new_total) == (3, 2)
    assert item.new_episodes == []                  # 集数没变多 → 不可能是"新增两集"
    assert item.has_new is False


def test_unnamed_episodes_fall_back_to_tail() -> None:
    """集名里没有集号（正片/OVA）时退回"末尾 N 项"，并剔除已记录的那一集。"""
    series = make_series(total=1, latest="正片")
    detail = SeriesDetail(series=series, roads=[Road(index=0, name="线路", episodes=[
        AnimeEpisode(index=0, name="正片"),
        AnimeEpisode(index=1, name="OVA 特别篇"),
        AnimeEpisode(index=2, name="完结纪念"),
    ])])
    item = check_updates([series], detail_fetcher=lambda _s: detail).items[0]
    assert item.new_episodes == ["OVA 特别篇", "完结纪念"]


def test_number_in_episode_title_does_not_skew_new_episodes() -> None:
    """「OVA 2」这类标题里带数字时要优先信 `number`，不能被标题数字带偏。"""
    series = make_series(total=2, latest="第02集")
    detail = SeriesDetail(series=series, roads=[Road(index=0, name="线路", episodes=[
        AnimeEpisode(index=0, name="第01集", number=1),
        AnimeEpisode(index=1, name="第02集", number=2),
        AnimeEpisode(index=2, name="OVA 2", number=3),
    ])])
    item = check_updates([series], detail_fetcher=lambda _s: detail).items[0]
    assert item.new_episodes == ["OVA 2"]


def test_error_is_captured_not_raised() -> None:
    """单条番剧抛异常只记 error（整轮检查不能被它打断）。"""
    good = make_series("good", "能查到的番", total=1, latest="第01集")
    bad = make_series("bad", "打不开的番", total=1, latest="第01集")

    def fetch(series: "AnimeSeries"):
        if series.remote_id == "bad":
            raise RuntimeError("HTTP 403 Forbidden")
        return make_detail(series, names(2))

    report = check_updates([good, bad], detail_fetcher=fetch)
    assert report.checked == 2 and report.failed == 1
    assert report.items[1].error == "HTTP 403 Forbidden"
    assert report.items[1].new_episodes == []
    assert "检查失败" in report.items[1].message
    assert report.items[0].new_episodes == ["第02集"]      # 好的那条照常出结果


def test_none_detail_is_counted_as_failed_but_keeps_series() -> None:
    series = make_series(total=2, latest="第02集")
    report = check_updates([series], detail_fetcher=lambda _s: None)
    item = report.items[0]
    assert report.failed == 1 and item.has_new is False
    assert "详情" in item.error and item.series is series


def test_empty_series_list_returns_empty_report() -> None:
    calls = []
    report = check_updates([], detail_fetcher=lambda s: calls.append(s))
    assert report.items == [] and report.checked == 0 and report.failed == 0
    assert calls == []


def test_cancel_stops_dispatch() -> None:
    series = [make_series(f"s{index}", f"番{index}") for index in range(6)]
    cancel = threading.Event()
    cancel.set()
    seen = []
    report = check_updates(series, detail_fetcher=lambda s: seen.append(s),
                           cancel=cancel)
    assert seen == []                                      # 已取消 → 不派发任何请求
    assert report.items == [] and report.checked == 0


def test_progress_callback_and_order_are_stable() -> None:
    series = [make_series(f"s{index}", f"番{index}", total=2, latest="第02集")
              for index in range(4)]
    progress: list[tuple[int, int]] = []
    detail = {}

    def fetch(item: "AnimeSeries"):
        detail[item.remote_id] = make_detail(item, names(3))
        return detail[item.remote_id]

    report = check_updates(series, detail_fetcher=fetch, max_workers=2,
                           on_progress=lambda done, total, message: progress.append((done, total)))
    assert [item.series.remote_id for item in report.items] == ["s0", "s1", "s2", "s3"]
    # 前 4 次是逐条进度，最后一次是"检查完成：N 部有新集"的汇总（也是 done=total）
    assert [done for done, _ in progress] == [1, 2, 3, 4, 4]
    assert all(total == 4 for _, total in progress)
    # 并发跑也不能丢结果（每个番剧各报一集新集）
    assert report.new_count == 4


def test_series_list_ignores_non_series_items() -> None:
    """入参里混进非 AnimeSeries（UI 传错类型）时不要炸，直接忽略。"""
    series = make_series(total=1, latest="第01集")
    report = check_updates([series, None, "字符串", 42],
                           detail_fetcher=lambda s: make_detail(s, names(2)))
    assert report.checked == 1 and report.items[0].series is series
