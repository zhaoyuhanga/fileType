"""`anime_*` 表 CRUD：唯一约束、追番、进度、历史、弹幕索引、下载任务、导出导入。

这些测试是"动漫板块数据层"的闸门：表结构由 `tests/platform/test_db_schema.py`
快照锁定，本文件锁定**行为**（幂等性/顺序/边界），两者缺一不可。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import import_anime

storage_module = import_anime("storage")
models_module = import_anime("models")

AnimeStorage = storage_module.AnimeStorage
ANIME_DOWNLOAD_STATES = storage_module.ANIME_DOWNLOAD_STATES
load_subscriptions = storage_module.load_subscriptions
save_subscriptions = storage_module.save_subscriptions
AnimeRule = models_module.AnimeRule
AnimeSeries = models_module.AnimeSeries


@pytest.fixture()
def store(tmp_path: Path) -> "AnimeStorage":
    """每个用例一个新库：存储层不共享状态（迁移在构造时自动跑完整套）。"""
    storage = AnimeStorage(tmp_path / "anime.db")
    yield storage
    storage.close()


def make_series(rule_key: str = "builtin:a", remote_id: str = "s1",
                title: str = "进击的巨人 第一季") -> "AnimeSeries":
    return AnimeSeries(rule_key=rule_key, remote_id=remote_id, title=title,
                       cover="https://img/1.jpg", year="2013", summary="简介",
                       total_episodes=25, latest_episode="第25集")


def make_rule(key: str = "builtin:a", name: str = "站点A", path: str = "") -> "AnimeRule":
    return AnimeRule(key=key, name=name, version="1", base_url="https://a.example", path=path)


# --------------------------------------------------------------------------- 建表


def test_storage_self_migrates_on_new_file(tmp_path: Path) -> None:
    """`AnimeStorage(tmp_path/"anime.db")` 必须自己把 anime_* 表建出来（无需外部初始化）。"""
    db = tmp_path / "anime.db"
    storage = AnimeStorage(db)
    assert db.is_file()
    names = {row["name"] for row in storage.query(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'anime%'")}
    assert names == {"anime_rules", "anime_series", "anime_favorites", "anime_progress",
                     "anime_history", "anime_danmaku", "anime_download_tasks"}
    indexes = {row["name"] for row in storage.query(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_anime%'")}
    assert indexes == {"idx_anime_series_rule", "idx_anime_history_time",
                       "idx_anime_progress_time", "idx_anime_download_state"}
    storage.close()


def test_empty_db_queries_are_safe(store: "AnimeStorage") -> None:
    """空库上每个读取接口都要能安全调用（UI 首次打开就是这个状态）。"""
    assert store.list_rules() == []
    assert store.get_rule("nope") is None
    assert store.enabled_keys() == []
    assert store.get_series(1) is None
    assert store.find_series("a", "b") is None
    assert store.list_series() == []
    assert store.favorite_series() == []
    assert store.list_favorites() == []
    assert store.is_favorite(1) is False
    assert store.list_progress(1) == []
    assert store.continue_watching() == []
    assert store.get_progress(1, "k") is None
    assert store.list_history() == []
    assert store.list_danmaku_index(1) == []
    assert store.get_danmaku_index(1, "k") is None
    assert store.list_download_tasks() == []
    assert store.get_download_task(1) is None
    assert store.export_payload()["series"] == []


def test_download_states_constant_matches_plan() -> None:
    assert ANIME_DOWNLOAD_STATES == ("pending", "running", "paused", "failed", "done")


# --------------------------------------------------------------------------- 规则索引


def test_upsert_rule_keeps_user_choice(store: "AnimeStorage") -> None:
    """重新 upsert（每次扫描都会发生）不得覆盖用户的启用开关。"""
    first = store.upsert_rule(make_rule())
    assert first > 0
    assert store.get_rule("builtin:a")["enabled"] == 1        # 新规则默认启用

    store.set_rule_enabled("builtin:a", False)
    store.upsert_rule(make_rule(name="站点A（改名）"))         # 模拟下次启动重新扫描
    row = store.get_rule("builtin:a")
    assert row["enabled"] == 0                                 # 用户的选择被保留
    assert row["name"] == "站点A（改名）"                        # 展示字段仍然刷新
    assert store.upsert_rule(make_rule()) == first             # 同一 key 不新增行
    assert len(store.list_rules()) == 1


def test_upsert_rule_explicit_enabled_overrides(store: "AnimeStorage") -> None:
    store.upsert_rule(make_rule())
    store.upsert_rule(make_rule(), enabled=False)
    assert store.get_rule("builtin:a")["enabled"] == 0
    store.upsert_rule(make_rule(), enabled=True)
    assert store.get_rule("builtin:a")["enabled"] == 1
    assert store.enabled_keys() == ["builtin:a"]


def test_rule_enabled_and_test_state(store: "AnimeStorage") -> None:
    store.upsert_rule(make_rule("builtin:a"))
    store.upsert_rule(make_rule("user:b"))
    assert store.enabled_keys() == ["builtin:a", "user:b"]
    assert store.set_rule_enabled("user:b", False) is True
    assert store.enabled_keys() == ["builtin:a"]
    assert store.set_rule_enabled("missing", True) is False    # 没命中要如实返回 False

    assert store.set_rule_test("builtin:a", "ok", "命中 12 条") is True
    row = store.get_rule("builtin:a")
    assert (row["test_state"], row["test_note"]) == ("ok", "命中 12 条")
    assert store.set_rule_test("missing", "fail", "") is False


def test_delete_rule_and_prune_missing(store: "AnimeStorage") -> None:
    """规则文件消失只标记、不删行；显式删除才删（不影响已追的番）。"""
    store.upsert_rule(make_rule("builtin:a", "A"))
    store.upsert_rule(make_rule("user:b", "B"))
    store.upsert_rule(make_rule("user:c", "C"))

    marked = store.prune_missing({"builtin:a"})                 # c 的文件不见了
    assert marked == 2                                          # b 与 c 都不在文件列表里
    row = store.get_rule("user:c")
    assert row["test_state"] == "missing" and row["path"] == ""
    assert store.get_rule("builtin:a")["test_state"] == ""     # 其它规则不受影响
    assert store.prune_missing({"builtin:a"}) == 0             # 幂等：已标记的不重复计数

    # 文件回来后重新 upsert：missing 标记必须被清掉（否则规则页一直显示"文件已不存在"）
    store.upsert_rule(make_rule("user:c", "C", path="/rules/c.json"))
    restored = store.get_rule("user:c")
    assert restored["test_state"] == "" and restored["test_note"] == ""
    assert restored["path"] == "/rules/c.json"

    series_id = store.upsert_series(make_series())
    store.add_favorite(series_id)
    assert store.delete_rule("user:c") is True
    assert store.delete_rule("user:c") is False
    assert store.is_favorite(series_id) is True                # 删规则索引不动追番


# --------------------------------------------------------------------------- 番剧


def test_upsert_series_dedupes_and_preserves_created_at(store: "AnimeStorage") -> None:
    series = make_series()
    first = store.upsert_series(series)
    assert first > 0
    created = store.get_series(first).created_at

    series2 = make_series(title="进击的巨人 第一季（修正）")
    second = store.upsert_series(series2)
    assert second == first                                     # UNIQUE(rule_key, remote_id)
    assert len(store.list_series()) == 1
    assert store.get_series(first).created_at == created       # 首次入库时间不跳
    assert store.get_series(first).title == "进击的巨人 第一季（修正）"


def test_upsert_series_fills_title_norm(store: "AnimeStorage") -> None:
    from modu_workbench.core.anime.models import normalize_title

    series_id = store.upsert_series(AnimeSeries(rule_key="r", remote_id="1",
                                                title="【进击的巨人】 第一季！"))
    row = store.get_series(series_id)
    assert row.title_norm == normalize_title("【进击的巨人】 第一季！")
    assert row.title_norm and row.title_norm != row.title      # 归一化确实生效了


def test_upsert_series_does_not_blank_existing_fields(store: "AnimeStorage") -> None:
    """详情刷新时抓到空值，不该把库里的简介/封面抹掉。"""
    series_id = store.upsert_series(make_series())
    store.upsert_series(AnimeSeries(rule_key="builtin:a", remote_id="s1",
                                    title="进击的巨人 第一季"))
    row = store.get_series(series_id)
    assert row.summary == "简介"
    assert row.cover == "https://img/1.jpg"
    assert row.total_episodes == 25


def test_series_queries_and_meta(store: "AnimeStorage") -> None:
    a = store.upsert_series(make_series("builtin:a", "s1", "进击的巨人"))
    b = store.upsert_series(make_series("builtin:b", "s1", "进击的巨人"))     # 另一条规则 = 另一行
    assert a != b
    assert store.find_series("builtin:b", "s1").id == b
    assert [s.id for s in store.list_series()] == [b, a]                     # updated_at DESC

    store.add_favorite(a)
    assert [s.id for s in store.list_series(favorite=True)] == [a]
    assert [s.id for s in store.list_series(favorite=False)] == [b]
    assert [s.id for s in store.list_series(keyword="巨人")] == [b, a]
    assert store.list_series(keyword="不存在的番") == []

    assert store.set_series_meta(a, total_episodes=26, latest_episode="第26集") is True
    updated = store.get_series(a)
    assert (updated.total_episodes, updated.latest_episode) == (26, "第26集")
    assert store.set_series_meta(a, id=999) is False          # 白名单外的键直接忽略


def test_delete_series_clears_user_state_but_keeps_history(store: "AnimeStorage") -> None:
    series_id = store.upsert_series(make_series())
    store.add_favorite(series_id)
    store.save_progress(series_id, "0:1:ep", "第01集", 1, 100, 1000)
    store.save_danmaku_index(series_id, "0:1:ep", "dandanplay", 1, 10, "p.xml")
    store.add_history(series_id, "builtin:a", "进击的巨人", "第01集")

    assert store.delete_series(series_id) == 1
    assert store.get_series(series_id) is None
    assert store.list_favorites() == []
    assert store.list_progress(series_id) == []
    assert store.list_danmaku_index(series_id) == []
    assert len(store.list_history()) == 1                      # 历史是快照，删番剧不清
    assert store.delete_series(series_id) == 0


# --------------------------------------------------------------------------- 追番


def test_favorite_add_remove_roundtrip(store: "AnimeStorage") -> None:
    series_id = store.upsert_series(make_series())
    assert store.is_favorite(series_id) is False
    assert store.add_favorite(series_id, "周末看") is True      # 首次是新建
    assert store.is_favorite(series_id) is True
    assert store.add_favorite(series_id, "改备注") is False     # 重复追番只更新备注
    assert len(store.list_favorites()) == 1

    series, info = store.list_favorites()[0]
    assert series.id == series_id and info["note"] == "改备注"
    assert store.favorite_series()[0].id == series_id
    assert store.remove_favorite(series_id) is True
    assert store.remove_favorite(series_id) is False
    assert store.is_favorite(series_id) is False
    assert store.add_favorite(0) is False                      # 非法 id 不该写入


def test_favorite_requires_series_or_orphan_allowed(store: "AnimeStorage") -> None:
    """追番列表是 JOIN：番剧行不存在时不会凭空冒出一行（避免 UI 显示空白卡片）。"""
    store.add_favorite(12345)
    assert store.list_favorites() == []
    assert store.favorite_series() == []


# --------------------------------------------------------------------------- 进度


def test_progress_upsert_by_series_and_episode_key(store: "AnimeStorage") -> None:
    series_id = store.upsert_series(make_series())
    store.save_progress(series_id, "0:1:ep1", "第01集", 1, 1000, 10000)
    store.save_progress(series_id, "0:1:ep1", "第01集", 1, 2500, 10000)
    assert len(store.list_progress(series_id)) == 1             # 同集只留一行

    progress = store.get_progress(series_id, "0:1:ep1")
    assert progress.position_ms == 2500 and progress.episode_index == 1
    assert progress.percent == 25 and progress.finished is False

    # episode_index 缺省（0）时保留旧值，避免"标记已看"把集序号冲掉
    store.save_progress(series_id, "0:1:ep1", position_ms=3000)
    assert store.get_progress(series_id, "0:1:ep1").episode_index == 1
    assert store.get_progress(series_id, "0:1:ep1").duration_ms == 10000
    assert store.save_progress(0, "k") is False
    assert store.save_progress(series_id, "") is False


def test_mark_watched_and_continue_watching_excludes_finished(store: "AnimeStorage") -> None:
    series_id = store.upsert_series(make_series())
    store.save_progress(series_id, "ep1", "第01集", 1, 1000, 10000)
    store.save_progress(series_id, "ep2", "第02集", 2, 500, 10000)
    assert [p.episode_key for _, p in store.continue_watching()] == ["ep2", "ep1"]

    store.mark_watched(series_id, "ep1")
    assert [p.episode_key for _, p in store.continue_watching()] == ["ep2"]
    assert store.get_progress(series_id, "ep1").finished is True

    store.mark_watched(series_id, "ep1", False)
    assert [p.episode_key for _, p in store.continue_watching()] == ["ep2", "ep1"]
    assert store.mark_watched(0, "ep") is False


def test_continue_watching_skips_nearly_finished_by_position(store: "AnimeStorage") -> None:
    """播到 96% 还没写 watched 的也要算"看完"（否则"继续观看"永远清不掉）。"""
    series_id = store.upsert_series(make_series())
    store.save_progress(series_id, "ep1", "第01集", 1, 9600, 10000)
    store.save_progress(series_id, "ep2", "第02集", 2, 9000, 10000)
    assert [p.episode_key for _, p in store.continue_watching()] == ["ep2"]


def test_continue_watching_respects_limit_and_series(store: "AnimeStorage") -> None:
    first = store.upsert_series(make_series("r", "1", "甲"))
    second = store.upsert_series(make_series("r", "2", "乙"))
    for index in range(3):
        store.save_progress(first, f"a{index}", f"甲{index}", index, 10, 1000)
        store.save_progress(second, f"b{index}", f"乙{index}", index, 10, 1000)
    items = store.continue_watching(limit=2)
    assert len(items) == 2
    assert all(isinstance(series, AnimeSeries) for series, _ in items)


# --------------------------------------------------------------------------- 历史


def test_history_ordering_and_clear(store: "AnimeStorage") -> None:
    series_id = store.upsert_series(make_series())
    keys = [store.add_history(series_id, "builtin:a", "进击的巨人", f"第{i:02d}集", "player_aaaa")
            for i in range(1, 4)]
    assert all(key > 0 for key in keys)

    rows = store.list_history()
    assert len(rows) == 3
    # 同一秒内写入时用 id DESC 兜底，顺序必须稳定（"刚看的排最前"）
    assert [row["episode_name"] for row in rows] == ["第03集", "第02集", "第01集"]
    assert store.list_history(limit=2)[0]["episode_name"] == "第03集"

    store.clear_history()
    assert store.list_history() == []


def test_history_keeps_snapshot_after_series_deleted(store: "AnimeStorage") -> None:
    series_id = store.upsert_series(make_series())
    store.add_history(series_id, "builtin:a", "进击的巨人", "第01集", "sniffer")
    store.delete_series(series_id)
    row = store.list_history()[0]
    assert row["title"] == "进击的巨人" and row["series_id"] == series_id


# --------------------------------------------------------------------------- 弹幕索引


def test_danmaku_index_upsert_by_provider(store: "AnimeStorage") -> None:
    series_id = store.upsert_series(make_series())
    store.save_danmaku_index(series_id, "0:1:ep1", "dandanplay", 42, 100, "a.xml")
    store.save_danmaku_index(series_id, "0:1:ep1", "dandanplay", 42, 250, "a.xml")
    assert len(store.list_danmaku_index(series_id)) == 1
    row = store.get_danmaku_index(series_id, "0:1:ep1")
    assert row["count"] == 250 and row["episode_id"] == 42

    store.save_danmaku_index(series_id, "0:1:ep1", "local", 0, 5, "b.xml")
    assert len(store.list_danmaku_index(series_id)) == 2       # 不同 provider 各存一份
    assert store.get_danmaku_index(series_id, "0:1:ep1", "local")["path"] == "b.xml"

    other = store.upsert_series(make_series("r", "2", "乙"))
    assert store.purge_danmaku(series_id) == 2
    assert store.list_danmaku_index(series_id) == []
    assert store.get_danmaku_index(series_id, "0:1:ep1") is None
    _ = other
    assert store.purge_danmaku(0) == 0                         # 全清（此时已无记录）


def test_purge_danmaku_all(store: "AnimeStorage") -> None:
    a = store.upsert_series(make_series("r", "1", "甲"))
    b = store.upsert_series(make_series("r", "2", "乙"))
    store.save_danmaku_index(a, "k1", "dandanplay", 1, 1, "1.xml")
    store.save_danmaku_index(b, "k2", "dandanplay", 2, 2, "2.xml")
    assert store.purge_danmaku(0) == 2
    assert store.list_danmaku_index(a) == [] and store.list_danmaku_index(b) == []


# --------------------------------------------------------------------------- 下载任务


def test_download_task_lifecycle(store: "AnimeStorage") -> None:
    series_id = store.upsert_series(make_series())
    task_id = store.upsert_download_task(series_id, "0:1:ep1", "第01集", "builtin:a",
                                         url="https://x/1.m3u8", state="pending")
    assert task_id > 0
    row = store.get_download_task(task_id)
    assert row["state"] == "pending" and row["done_parts"] == 0

    assert store.update_download_task(task_id, state="running", total_parts=10) is True
    assert store.update_download_task(task_id, state="paused", done_parts=4,
                                      parts_dir="d.parts") is True
    assert store.update_download_task(task_id, state="running", done_parts=10) is True
    assert store.update_download_task(task_id, state="done", message="完成") is True

    row = store.get_download_task(task_id)
    assert (row["state"], row["done_parts"], row["total_parts"]) == ("done", 10, 10)
    assert store.list_download_tasks("done")[0]["id"] == task_id
    assert store.list_download_tasks("running") == []
    assert store.update_download_task(task_id, id=7, series_id=7) is False   # 白名单外
    assert store.update_download_task(999, state="done") is False

    assert store.delete_download_task(task_id) is True
    assert store.get_download_task(task_id) is None
    assert store.delete_download_task(task_id) is False


def test_download_task_upsert_dedupes_same_episode(store: "AnimeStorage") -> None:
    """同一集反复点下载/续传应该更新同一行，否则任务列表会堆一串同名条目。"""
    series_id = store.upsert_series(make_series())
    first = store.upsert_download_task(series_id, "ep1", "第01集", state="pending")
    second = store.upsert_download_task(series_id, "ep1", "第01集", state="running",
                                        done_parts=3, total_parts=10)
    assert first == second
    assert len(store.list_download_tasks()) == 1
    assert store.get_download_task(first)["state"] == "running"

    store.upsert_download_task(series_id, "ep2", "第02集")
    assert len(store.list_download_tasks()) == 2
    assert store.list_download_tasks()[0]["episode_key"] == "ep2"      # updated_at DESC


def test_download_task_state_transitions_are_tracked(store: "AnimeStorage") -> None:
    series_id = store.upsert_series(make_series())
    task_id = store.upsert_download_task(series_id, "ep1", "第01集")
    seen = []
    for state in ANIME_DOWNLOAD_STATES:
        store.update_download_task(task_id, state=state, message=f"进入 {state}")
        seen.append(store.get_download_task(task_id)["state"])
    assert seen == list(ANIME_DOWNLOAD_STATES)


# --------------------------------------------------------------------------- 导出 / 导入


def test_export_import_roundtrip_preserves_favorites_and_progress(tmp_path: Path) -> None:
    source = AnimeStorage(tmp_path / "a.db")
    series_id = source.upsert_series(make_series())
    source.add_favorite(series_id, "追起来")
    source.save_progress(series_id, "0:1:ep1", "第01集", 1, 1234, 24000)
    source.save_progress(series_id, "0:2:ep2", "第02集", 2, 24000, 24000, watched=True)
    source.upsert_rule(make_rule("builtin:a"))
    source.upsert_rule(make_rule("user:b", "B"))
    source.set_rule_enabled("user:b", False)
    payload = source.export_payload()
    assert payload["kind"] == "anime-follow" and payload["version"] == 1
    assert payload["enabled_rules"] == ["builtin:a"]
    assert len(payload["series"]) == 1 and len(payload["favorites"]) == 1
    assert len(payload["progress"]) == 2

    target = AnimeStorage(tmp_path / "b.db")
    counts = target.import_payload(payload)
    assert counts == {"series": 1, "favorites": 1, "progress": 2}
    assert [s.title for s in target.favorite_series()] == ["进击的巨人 第一季"]
    assert target.list_favorites()[0][1]["note"] == "追起来"
    imported = target.upsert_series(make_series())            # 同键：不该新增行
    assert len(target.list_series()) == 1
    assert target.get_progress(imported, "0:1:ep1").position_ms == 1234
    assert target.get_progress(imported, "0:2:ep2").watched is True
    source.close()
    target.close()


def test_import_payload_tolerates_missing_keys(store: "AnimeStorage") -> None:
    """导出文件是用户手里传来传去的文本：缺键、类型错、空对象都不能崩。"""
    assert store.import_payload({}) == {"series": 0, "favorites": 0, "progress": 0}
    assert store.import_payload({"series": [], "favorites": [], "progress": []}) == \
        {"series": 0, "favorites": 0, "progress": 0}
    assert store.import_payload({"series": None, "favorites": "坏掉了"}) == \
        {"series": 0, "favorites": 0, "progress": 0}
    assert store.import_payload(None) == {"series": 0, "favorites": 0, "progress": 0}

    # 缺 remote_id / 缺 series_id 的孤儿行要被跳过，而不是写进垃圾数据
    counts = store.import_payload({
        "series": [{"title": "只有标题，没有标识"}],
        "favorites": [{"series_id": 999}],
        "progress": [{"series_id": 999, "episode_key": "k"}],
    })
    assert counts == {"series": 0, "favorites": 0, "progress": 0}
    assert store.list_series() == []


def test_import_payload_merges_instead_of_replacing(store: "AnimeStorage") -> None:
    """导入是"合并"：本机已有数据不许被清掉。"""
    mine = store.upsert_series(make_series("r", "mine", "我本地的番"))
    store.add_favorite(mine)
    counts = store.import_payload({"series": [
        {"id": 1, "rule_key": "r", "remote_id": "other", "title": "别人导出的番"}]})
    assert counts["series"] == 1
    assert len(store.list_series()) == 2
    assert store.is_favorite(mine) is True
    assert len(store.favorite_series()) == 1


def test_import_payload_keeps_original_created_at(store: "AnimeStorage") -> None:
    """跨机器还原要保留原"追番时间"，否则列表顺序会全乱。"""
    counts = store.import_payload({
        "series": [{"id": 5, "rule_key": "r", "remote_id": "1", "title": "甲",
                    "created_at": 1000000}],
        "favorites": [{"series_id": 5, "created_at": 1000001, "note": ""}],
    })
    assert counts == {"series": 1, "favorites": 1, "progress": 0}
    assert store.list_series()[0].created_at == 1000000


# --------------------------------------------------------------------------- 订阅清单


def test_subscriptions_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """订阅清单存在 app_settings 的 `anime/rule_subscriptions`，且自带降级。"""
    from modu_workbench.core.platform import paths

    monkeypatch.setattr(paths, "app_db_path", lambda: str(tmp_path / "app.db"))
    assert load_subscriptions() == []                          # 库还不存在也要安全
    entries = [{"name": "kazumi", "url": "https://x/index.json", "enabled": True}]
    save_subscriptions(entries)
    assert load_subscriptions() == entries

    save_subscriptions([{"bad": object()}])                    # 无法序列化 → 静默保留旧值
    assert load_subscriptions() == entries
    save_subscriptions([])
    assert load_subscriptions() == []


def test_subscriptions_survive_garbage_in_setting(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """设置值被写坏时只返回空列表，绝不抛异常（规则页要能照常打开）。

    订阅的读写都以"应用数据库"为准，所以这里把 `app_db_path` 指到临时库上，
    再用存储对象往**同一个库**写坏值（比手工拼 app_settings 更贴近真实路径）。
    """
    from modu_workbench.core.platform import paths

    monkeypatch.setattr(paths, "app_db_path", lambda: str(tmp_path / "app.db"))
    store = AnimeStorage(tmp_path / "app.db")
    store.set_setting("rule_subscriptions", "{不是数组}")
    assert load_subscriptions() == []
    store.set_setting("rule_subscriptions", '[1, {"name": "ok"}]')
    assert load_subscriptions() == [{"name": "ok"}]           # 非 dict 项被过滤
    store.close()
