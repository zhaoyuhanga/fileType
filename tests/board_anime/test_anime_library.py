"""`AnimeLibrary` 的非网络部分：规则、订阅、搜索合并、追番、进度、翻页、下载、更新检查。

引擎通过 `engine_factory` 注入替身，下载器通过 `download_fn` 注入替身 —— 整个文件
不联网、不碰 Qt、也不依赖 `core/anime/engine.py` 是否已经写完（这正是 `library.py`
把引擎做成懒加载的目的）。

注意：订阅状态存在**应用数据库**里，所以每个用到订阅的用例都要把 `paths.app_db_path`
指到临时库，否则会写到真实用户数据目录。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from conftest import import_anime

models = import_anime("models")
rules = import_anime("rules")
storage_module = import_anime("storage")
library_module = import_anime("library")

AnimeEpisode = models.AnimeEpisode
AnimeRule = models.AnimeRule
AnimeSeries = models.AnimeSeries
Road = models.Road
SeriesDetail = models.SeriesDetail
RuleManager = rules.RuleManager
AnimeStorage = storage_module.AnimeStorage
AnimeLibrary = library_module.AnimeLibrary

#: 一条最小可用规则（字段齐、选择器合法），用于真实 RuleManager 的落盘与扫描
RULE_JSON = {
    "name": "站点A",
    "version": "1.0",
    "api": "8",
    "type": "anime",
    "baseURL": "https://a.example",
    "searchURL": "https://a.example/search?wd=@keyword",
    "searchList": "//div[@class='item']",
    "searchName": "//a/@title",
    "searchResult": "//a/@href",
    "chapterRoads": "//div[@id='playlist']",
    "chapterResult": "//a/@href",
}


# --------------------------------------------------------------------------- 替身


class FakeHit:
    def __init__(self, remote_id: str, title: str, cover: str = "", year: str = "",
                 url: str = "") -> None:
        self.remote_id = remote_id
        self.title = title
        self.cover = cover
        self.year = year
        self.url = url


class FakeEngine:
    """假引擎：记录调用次数，并支持按关键词定制"某条规则搜不到"。"""

    def __init__(self) -> None:
        self.search_calls: list[tuple[str, str]] = []
        self.detail_calls = 0
        self.resolve_calls = 0
        self.episodes: list[str] = [f"第{index:02d}集" for index in range(1, 4)]
        self.extra_road: list[str] = ["线路B 第01集"]
        self.empty_rules: set[str] = set()

    def search_rule(self, rule: "AnimeRule", keyword: str):
        self.search_calls.append((rule.key, keyword))
        if rule.key in self.empty_rules:
            return []
        return [FakeHit(f"{rule.key}-1", f"{keyword} 第一季", cover="https://img/1.jpg",
                        year="2013", url=f"https://a.example/{rule.key}-1"),
                FakeHit(f"{rule.key}-2", f"{keyword} 第二季", url=f"https://a.example/{rule.key}-2")]

    def detail(self, hit):
        self.detail_calls += 1
        remote_id = getattr(hit, "remote_id", "") or "?"
        roads = [Road(index=0, name="线路A", episodes=[
            AnimeEpisode(index=position, name=name, url=f"https://media/{position}.m3u8",
                         road_index=0, road_name="线路A", number=position + 1)
            for position, name in enumerate(self.episodes)])]
        if self.extra_road:
            roads.append(Road(index=1, name="线路B", episodes=[
                AnimeEpisode(index=0, name=self.extra_road[0], url="https://media/b0.m3u8",
                             road_index=1, road_name="线路B", number=1)]))
        # 规则归属取自入参（真实引擎也是这样：详情属于"发起它的那条规则"），
        # 写死 rule_key 会让"入库行"与"发起查询的行"对不上，测出来的是替身的 bug。
        rule_key = getattr(hit, "rule_key", "") or "a"
        return SeriesDetail(series=AnimeSeries(rule_key=rule_key, remote_id=remote_id,
                                               title=f"{remote_id} 的番剧"),
                            roads=roads)

    def resolve(self, detail, road, episode):
        self.resolve_calls += 1
        if not episode.url:
            raise RuntimeError("没有可解析的播放页")
        return {"url": episode.url, "strategy": "player_aaaa", "note": ""}


class BrokenEngine(FakeEngine):
    """搜索与详情都失败（规则站点全面 403）的引擎替身。"""

    def search_rule(self, rule, keyword):
        raise RuntimeError("HTTP 403 Forbidden")

    def detail(self, hit):
        self.detail_calls += 1
        raise RuntimeError("HTTP 403 Forbidden")


def write_rule(directory: Path, stem: str, **overrides) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    payload = dict(RULE_JSON)
    payload.update(overrides)
    target = directory / f"{stem}.json"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return target


# --------------------------------------------------------------------------- 夹具


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """一套隔离环境：临时数据目录 + 临时应用库 + 注入替身的库对象。"""
    from modu_workbench.core.platform import paths

    monkeypatch.setattr(paths, "app_db_path", lambda: str(tmp_path / "app.db"))
    monkeypatch.setenv("MODU_DATA_DIR", str(tmp_path / "data"))
    user_dir = tmp_path / "rules"
    user_dir.mkdir(parents=True, exist_ok=True)
    engine = FakeEngine()
    manager = RuleManager(user_dir=user_dir, builtin_dir=tmp_path / "builtin")
    storage = AnimeStorage(tmp_path / "anime.db")
    library = AnimeLibrary(storage, rule_manager=manager, engine_factory=lambda: engine,
                           data_dir=tmp_path)
    yield {"tmp": tmp_path, "user_dir": user_dir, "engine": engine, "manager": manager,
           "storage": storage, "library": library, "paths": paths}
    storage.close()


def make_rule(key: str, name: str, path: str = "") -> "AnimeRule":
    return AnimeRule(key=key, name=name, version="1", base_url="https://a.example", path=path)


# --------------------------------------------------------------------------- 规则


def test_reload_rules_discovers_and_upserts(env: dict) -> None:
    write_rule(env["user_dir"], "a", name="乙站点")
    write_rule(env["user_dir"], "b", name="甲站点")
    found = env["library"].reload_rules()
    # 排序按 Unicode 码位（casefold 对中文无影响）：乙(U+4E59) 在 甲(U+7532) 之前
    assert [rule.name for rule in found] == ["乙站点", "甲站点"]
    rows = env["storage"].list_rules()
    assert {row["key"] for row in rows} == {"a", "b"}
    assert all(row["enabled"] == 1 for row in rows)                   # 新规则默认启用
    assert all(row["path"] for row in rows)                           # 记录规则文件位置


def test_reload_rules_marks_missing_but_keeps_user_choice(env: dict) -> None:
    write_rule(env["user_dir"], "a")
    env["library"].reload_rules()
    env["library"].set_rule_enabled("a", False)

    (env["user_dir"] / "a.json").unlink()
    env["library"].reload_rules()
    row = env["storage"].get_rule("a")
    assert row["test_state"] == "missing" and row["enabled"] == 0     # 标记失效但保留开关

    write_rule(env["user_dir"], "a")
    env["library"].reload_rules()
    row = env["storage"].get_rule("a")
    assert row["test_state"] == "" and row["enabled"] == 0            # 文件回来，开关仍是关
    assert env["library"].rules(enabled_only=True) == []


def test_broken_rule_file_does_not_stop_discovery(env: dict) -> None:
    write_rule(env["user_dir"], "good")
    (env["user_dir"] / "bad.json").write_text("{不是合法 JSON", encoding="utf-8")
    found = env["library"].reload_rules()
    assert [rule.key for rule in found] == ["good"]
    assert env["library"].rule_errors                                   # 坏规则原因要能看到


def test_test_rule_success_and_failure_paths(env: dict) -> None:
    write_rule(env["user_dir"], "a")
    rule = env["library"].reload_rules()[0]
    ok, note = env["library"].test_rule(rule, "巨人")
    assert ok is True and "通过" in note and "线路" in note
    assert env["storage"].get_rule("a")["test_state"] == "ok"

    env["engine"].empty_rules = {"a"}
    ok, note = env["library"].test_rule(rule, "巨人")
    assert ok is False and "0 条" in note
    assert env["storage"].get_rule("a")["test_state"] == "fail"

    ok, note = env["library"].test_rule(rule, "")
    assert ok is False and "关键词" in note

    unsupported = make_rule("x", "未来规则")
    unsupported.api = "99"
    ok, note = env["library"].test_rule(unsupported, "巨人")
    assert ok is False and "api 99" in note


def test_import_and_export_rules(env: dict) -> None:
    text = json.dumps(RULE_JSON, ensure_ascii=False)
    imported = env["library"].import_rules(text)
    assert len(imported) == 1 and imported[0].name == "站点A"
    assert env["storage"].get_rule(imported[0].key) is not None        # 导入即入库

    bundle = {"version": 1, "rules": [RULE_JSON, {**RULE_JSON, "name": "站点B"}]}
    bundle_path = env["tmp"] / "bundle.json"
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    imported = env["library"].import_rules(bundle_path)                # 传路径也要能导入
    assert len(imported) == 2

    out = env["library"].export_rules(imported, env["tmp"] / "out.json")
    payload = json.loads(Path(out).read_text(encoding="utf-8"))
    assert payload["version"] == 1 and len(payload["rules"]) == 2


def test_import_rules_rejects_empty_input(env: dict) -> None:
    with pytest.raises(rules.RuleError):
        env["library"].import_rules("")


# --------------------------------------------------------------------------- 订阅


def test_subscription_install_and_update_keeps_old_on_validation_failure(env: dict) -> None:
    library = env["library"]
    good = {**RULE_JSON, "name": "好规则"}
    saved = library.install_subscription("订阅甲", [good])
    assert [rule.name for rule in saved] == ["好规则"]
    # 订阅落的 key 形如 sub:<文件夹>:<文件名>，RuleManager.discover 依赖这个格式
    assert saved[0].key.startswith("sub:") and saved[0].path
    assert [entry["name"] for entry in library.subscriptions()] == ["订阅甲"]

    # 更新：好规则 v2 覆盖，坏规则（缺 baseURL/searchURL）必须保留旧文件
    bad = {"name": "好规则", "type": "anime"}                 # 同名但内容不合法
    written, failures = library._write_subscription("订阅甲", [bad])
    assert written == [] and failures and "好规则" in failures[0]
    kept = json.loads(Path(saved[0].path).read_text(encoding="utf-8"))
    assert kept["baseURL"] == "https://a.example"             # 旧版本没被坏内容覆盖

    # 再更新一版：内容合法 → 覆盖成功
    updated = {**RULE_JSON, "name": "好规则", "version": "2.0"}
    written, failures = library._write_subscription("订阅甲", [updated])
    assert failures == [] and len(written) == 1
    assert json.loads(Path(written[0].path).read_text(encoding="utf-8"))["version"] == "2.0"

    assert library.remove_subscription("订阅甲") is True
    assert library.subscriptions() == []
    assert not Path(saved[0].path).exists()
    assert library.remove_subscription("订阅甲") is False


def test_update_subscription_uses_injected_fetcher(env: dict) -> None:
    library = env["library"]
    library.install_subscription("订阅乙", [{**RULE_JSON, "name": "旧规则"}])
    library._remember_subscription("订阅乙", url="https://example/sub.json")
    payload = json.dumps({"rules": [{**RULE_JSON, "name": "新规则", "version": "3.0"}]},
                         ensure_ascii=False)
    saved, failures = library.update_subscription("订阅乙", fetcher=lambda url: payload)
    assert failures == [] and [rule.name for rule in saved] == ["新规则"]

    saved, failures = library.update_subscription("订阅乙",
                                                  fetcher=lambda url: "不是 JSON")
    assert saved == [] and failures and "合法" in failures[0]

    saved, failures = library.update_subscription(
        "订阅乙", fetcher=lambda url: (_ for _ in ()).throw(RuntimeError("网络不通")))
    assert saved == [] and "网络不通" in failures[0]

    # 没有 URL 又没有 fetcher：给可读原因，而不是静默无事发生
    library.install_subscription("订阅丙", [{**RULE_JSON, "name": "规则丙"}])
    saved, failures = library.update_subscription("订阅丙", fetcher=lambda url: payload)
    assert saved and saved[0].name == "新规则"


# --------------------------------------------------------------------------- 搜索合并


def test_search_merges_same_work_across_rules(env: dict) -> None:
    write_rule(env["user_dir"], "a", name="规则A")
    write_rule(env["user_dir"], "b", name="规则B")
    library = env["library"]
    library.reload_rules()

    seen: list[tuple[str, int]] = []
    groups = library.search("巨人", on_rule_done=lambda rule, hits: seen.append(
        (rule.key, len(hits))))
    assert [key for key, _ in seen] == ["a", "b"]                 # 边搜边出，每条规则一次回调
    assert all(count == 2 for _, count in seen)
    # 两条规则返回同样的标题 → 合并成一张卡，来源保留两条
    assert [group.title for group in groups] == ["巨人 第一季", "巨人 第二季"]
    first = groups[0]
    assert first.count == 2 and first.primary.rule_key == "a"
    assert {hit.rule_key for hit in first.sources} == {"a", "b"}
    assert "规则A" in first.rule_labels and "规则B" in first.rule_labels
    assert first.primary.title_norm == models.normalize_title(first.title)


def test_search_respects_rule_filter_and_limit(env: dict) -> None:
    write_rule(env["user_dir"], "a", name="规则A")
    write_rule(env["user_dir"], "b", name="规则B")
    library = env["library"]
    library.reload_rules()

    groups = library.search("巨人", rule_keys=["b"])
    assert {hit.rule_key for group in groups for hit in group.sources} == {"b"}
    assert groups[0].primary.rule_label == "规则B"

    # limit 是"每条规则最多取几张卡"：限 1 时每条规则只留第一张，
    # 两条规则的第一张恰好是同一部作品 → 合并成一张卡、两个来源
    limited = library.search("巨人", limit=1)
    assert len(limited) == 1 and limited[0].count == 2

    env["library"].set_rule_enabled("a", False)
    disabled = library.search("巨人")
    assert {hit.rule_key for group in disabled for hit in group.sources} == {"b"}


def test_search_merges_sources_across_rules_without_limit(env: dict) -> None:
    write_rule(env["user_dir"], "a", name="规则A")
    write_rule(env["user_dir"], "b", name="规则B")
    library = env["library"]
    library.reload_rules()

    groups = library.search("巨人")
    assert len(groups) == 2                                   # 第一季 / 第二季
    assert all(group.count == 2 for group in groups)          # 两条规则都并进了来源
    assert groups[0].primary.rule_key == "a"


def test_search_degrades_when_engine_fails(env: dict) -> None:
    write_rule(env["user_dir"], "a")
    library = env["library"]
    library.reload_rules()
    library._engine = BrokenEngine()          # 直接替换：模拟规则站点全面 403
    seen: list[int] = []
    assert library.search("巨人", on_rule_done=lambda rule, hits: seen.append(len(hits))) == []
    assert seen == [0]                        # 失败的规则也要回调（UI 才好显示"这条挂了"）
    assert library.search("") == []           # 空关键词直接返回


def test_missing_engine_yields_readable_failure(env: dict) -> None:
    """没有引擎（缺依赖/未完工）时：搜索为空、详情为 None、解析给中文提示，不抛异常。"""
    write_rule(env["user_dir"], "a")
    storage = AnimeStorage(env["tmp"] / "broken.db")

    def boom():
        raise RuntimeError("没有可用的规则引擎（缺少 lxml）")

    library = AnimeLibrary(storage, rule_manager=env["manager"], engine_factory=boom)
    assert library.reload_rules()                                     # 规则列表照常可用
    assert library.search("巨人") == []
    assert library.detail(FakeHit("1", "某番")) is None
    series = library.follow(FakeHit("1", "某番"))                      # 追番不依赖引擎
    resolved = library.resolve(series, AnimeEpisode(name="第01集"))
    assert resolved.ok is False and "规则引擎不可用" in resolved.note
    assert library.check_updates().failed == 1                         # 引擎不可用 → 报失败而不是谎报无更新
    storage.close()


def test_search_group_does_not_merge_different_works(env: dict) -> None:
    """"斗罗大陆" 不该被 "斗罗大陆2绝世唐门" 吞掉（包含关系但要满足长度比例约束）。"""
    write_rule(env["user_dir"], "a", name="规则A")
    library = env["library"]
    library.reload_rules()

    class TwoHitEngine(FakeEngine):
        def search_rule(self, rule, keyword):
            return [FakeHit("1", "斗罗大陆"), FakeHit("2", "斗罗大陆2绝世唐门"),
                    FakeHit("3", "斗罗大陆 第一季")]

    library._engine = TwoHitEngine()
    groups = library.search("斗罗")
    titles = [group.title for group in groups]
    assert "斗罗大陆" in titles and "斗罗大陆2绝世唐门" in titles
    # 短标题 + 后缀（长度接近）会被合并进第一张卡，而"2绝世唐门"那条保持独立
    merged = next(group for group in groups if group.title == "斗罗大陆")
    assert merged.count == 2
    assert len(groups) == 2


# --------------------------------------------------------------------------- 详情 / 追番


def test_detail_caches_metadata_and_episode_count(env: dict) -> None:
    write_rule(env["user_dir"], "a", name="规则A")
    library = env["library"]
    library.reload_rules()
    hit = FakeHit("s1", "进击的巨人", cover="https://img/1.jpg", year="2013")
    detail = library.detail(hit)
    assert detail is not None and detail.episode_count == 4       # 线路A 3 集 + 线路B 1 集
    series = env["storage"].find_series("a", "s1")
    assert series is not None
    assert series.total_episodes == 4                             # 详情里的全部剧集（含备用线路）
    assert series.latest_episode == "第03集"                       # 主线路（线路A）的末集
    assert library.detail(FakeHit("s1", "进击的巨人")) is not None
    assert env["storage"].find_series("a", "s1").id == series.id   # 不新增行


def test_follow_unfollow_and_favorites(env: dict) -> None:
    write_rule(env["user_dir"], "a", name="规则A")
    library = env["library"]
    library.reload_rules()
    hit = FakeHit("s1", "进击的巨人", year="2013")
    series = library.follow(hit)
    assert series.id > 0 and library.is_followed(series) is True
    assert [item.title for item in library.favorites()] == ["进击的巨人"]

    library.follow(hit)                                          # 重复追番幂等
    assert len(library.favorites()) == 1

    episode = AnimeEpisode(index=0, name="第01集", road_index=0, number=1)
    library.save_progress(series, episode, position_ms=1000, duration_ms=10000)
    assert library.unfollow(series) is True
    assert library.is_followed(series) is False
    assert library.favorites() == []
    assert env["storage"].get_progress(series.id, episode.key) is not None    # 取消追番不删进度
    assert library.unfollow(series) is False


def test_follow_without_rule_key_uses_first_available_rule(env: dict) -> None:
    """引擎的搜索对象通常没有 rule_key：追番时要补上，否则番剧行没法再拉详情。"""
    write_rule(env["user_dir"], "a", name="规则A")
    library = env["library"]
    library.reload_rules()
    series = library.follow(FakeHit("s1", "进击的巨人"))
    assert series.rule_key == "a"
    assert library.detail(series) is not None


def test_continue_watching_and_progress(env: dict) -> None:
    library = env["library"]
    series = library.follow(FakeHit("s1", "进击的巨人"))
    episode = AnimeEpisode(index=0, name="第01集", road_index=0, number=1)
    assert library.resume_position(series, episode) == 0
    library.save_progress(series, episode, position_ms=3000, duration_ms=10000)
    assert library.resume_position(series, episode) == 3000
    assert [(item.title, progress.position_ms) for item, progress in library.continue_watching()] \
        == [("进击的巨人", 3000)]

    library.save_progress(series, episode, position_ms=10000, duration_ms=10000, watched=True)
    assert library.continue_watching() == []
    assert library.resume_position(series, episode) == 0          # 看完的集不续播


# --------------------------------------------------------------------------- 播放


def test_resolve_records_history_and_progress(env: dict) -> None:
    library = env["library"]
    hit = FakeHit("s1", "进击的巨人")
    detail = library.detail(hit)
    series = library.follow(hit)
    episode = detail.roads[0].episodes[1]

    resolved = library.resolve(series, episode)
    assert resolved.ok is True
    assert resolved.url == episode.url
    assert resolved.is_hls is True and resolved.strategy == "player_aaaa"
    assert resolved.series.id == series.id and resolved.episode.key == episode.key

    history = env["storage"].list_history()
    assert len(history) == 1
    assert history[0]["title"] == "进击的巨人"
    assert history[0]["episode_name"] == episode.label
    assert env["storage"].get_progress(series.id, episode.key) is not None


def test_resolve_failure_returns_readable_note(env: dict) -> None:
    library = env["library"]
    hit = FakeHit("s1", "进击的巨人")
    library.detail(hit)
    series = library.follow(hit)
    broken = AnimeEpisode(index=0, name="第09集", url="", road_index=0, number=9)
    resolved = library.resolve(series, broken)
    assert resolved.ok is False
    assert "解析失败" in resolved.note or "没有解析出" in resolved.note
    assert env["storage"].list_history() == []                    # 失败不写历史


def test_resolve_requires_known_series(env: dict) -> None:
    """站点打不开时给"没拿到详情页"这种真实原因（不是抛异常、也不是"未入库"）。"""
    write_rule(env["user_dir"], "a", name="规则A")
    library = env["library"]
    library.reload_rules()
    library._engine = BrokenEngine()                 # 站点全面 403
    series = library.follow(FakeHit("ghost", "没入库的番"))
    series.id = 0                                   # 模拟"对象没带 id"
    resolved = library.resolve(series, AnimeEpisode(name="第01集"))
    assert resolved.ok is False and "详情" in resolved.note
    assert env["storage"].list_history() == []      # 失败不写历史


def test_resolve_without_engine_gives_readable_note(env: dict) -> None:
    """引擎不可用时给中文提示（不是"没入库"这种误导性原因）。"""
    storage = AnimeStorage(env["tmp"] / "no-engine.db")
    library = AnimeLibrary(storage, rule_manager=env["manager"],
                           engine_factory=lambda: (_ for _ in ()).throw(RuntimeError("缺 lxml")))
    series = library.follow(FakeHit("s1", "进击的巨人"))
    resolved = library.resolve(series, AnimeEpisode(name="第01集"))
    assert resolved.ok is False and "规则引擎不可用" in resolved.note
    storage.close()


def test_resolve_without_episode(env: dict) -> None:
    library = env["library"]
    resolved = library.resolve(FakeHit("s1", "进击的巨人"), "不是剧集对象")
    assert resolved.ok is False and "剧集" in resolved.note


# --------------------------------------------------------------------------- 下一集 / 上一集


def test_next_and_prev_episode_walking(env: dict) -> None:
    library = env["library"]
    hit = FakeHit("s1", "进击的巨人")
    detail = library.detail(hit)
    series = library.follow(hit)
    assert len(detail.roads) == 2 and len(detail.roads[0].episodes) == 3

    assert library.next_episode(series, 0, 0) == (0, 1)
    assert library.next_episode(series, 0, 2) == (1, 0)      # 同线路末尾 → 下一条线路第一集
    assert library.next_episode(series, 1, 0) is None        # 最后一条线路末尾 → 交给 UI 换规则
    assert library.prev_episode(series, 1, 0) == (0, 2)      # 上一条线路的最后一集
    assert library.prev_episode(series, 0, 0) is None
    assert library.prev_episode(series, 0, 2) == (0, 1)
    assert library.next_episode(series, 9, 0) is None        # 越界不抛
    assert library.next_episode(series, 0, 99) == (1, 0)


def test_episode_walking_with_single_road(env: dict) -> None:
    library = env["library"]
    env["engine"].extra_road = []
    hit = FakeHit("s1", "进击的巨人")
    library.detail(hit)
    series = library.follow(hit)
    assert library.next_episode(series, 0, 2) is None        # 只有一条线路时走到底就结束


def test_episode_walking_unknown_series(env: dict) -> None:
    library = env["library"]
    library._engine_factory = lambda: None
    library._engine = None
    assert library.next_episode(FakeHit("ghost", "没入库"), 0, 0) is None
    assert library.prev_episode(FakeHit("ghost", "没入库"), 0, 0) is None


# --------------------------------------------------------------------------- 下载


def test_download_episode_updates_task_table(tmp_path: Path, env: dict) -> None:
    downloaded: list[tuple[str, str]] = []

    def fake_download(video, episode, dest, on_progress=None, cancel=None):
        downloaded.append((video.title, episode.name))
        if on_progress:
            on_progress(1, 1, "完成")
        return {"ok": True, "path": str(Path(dest) / f"{episode.name}.mp4"),
                "message": "完成", "source": video.source}

    library = AnimeLibrary(env["storage"], rule_manager=env["manager"],
                           engine_factory=lambda: env["engine"], download_fn=fake_download)
    library.reload_rules()
    hit = FakeHit("a-1", "进击的巨人")
    detail = library.detail(hit)
    series = library.follow(hit)
    episode = detail.roads[0].episodes[0]

    outcome = library.download_episode(series, episode, tmp_path / "dl")
    assert outcome.ok is True and outcome.path.endswith("第01集.mp4")
    assert downloaded == [("进击的巨人", "第01集")]           # 适配成共享层模型后交给下载器
    tasks = env["storage"].list_download_tasks()
    assert len(tasks) == 1
    assert tasks[0]["state"] == "done" and tasks[0]["done_parts"] == 1
    assert tasks[0]["dest_path"].endswith("第01集.mp4")
    assert tasks[0]["series_id"] == series.id


def test_download_episode_failure_is_reported_not_raised(tmp_path: Path, env: dict) -> None:
    library = AnimeLibrary(env["storage"], rule_manager=env["manager"],
                           engine_factory=lambda: env["engine"],
                           download_fn=lambda *a, **k: (_ for _ in ()).throw(
                               RuntimeError("磁盘满了")))
    library.reload_rules()
    hit = FakeHit("a-1", "进击的巨人")
    detail = library.detail(hit)
    series = library.follow(hit)
    outcome = library.download_episode(series, detail.roads[0].episodes[0], tmp_path / "dl")
    assert outcome.ok is False and "磁盘满了" in outcome.message
    tasks = env["storage"].list_download_tasks()
    assert tasks[0]["state"] == "failed" and tasks[0]["message"] == "磁盘满了"


def test_enqueue_season_downloads_all_episodes(tmp_path: Path, env: dict) -> None:
    done: list[str] = []

    def fake_download(video, episode, dest, on_progress=None, cancel=None):
        return {"ok": True, "path": str(Path(dest) / f"{episode.name}.mp4"), "message": "完成"}

    library = AnimeLibrary(env["storage"], rule_manager=env["manager"],
                           engine_factory=lambda: env["engine"], download_fn=fake_download)
    library.reload_rules()
    hit = FakeHit("a-1", "进击的巨人")
    series = library.follow(hit)
    results = library.enqueue_season(series, tmp_path / "dl", parallel=2,
                                     on_task_done=lambda item: done.append(item.episode_label))
    assert [item.episode_label for item in results] == ["第01集", "第02集", "第03集"]
    assert all(item.ok for item in results)
    assert sorted(done) == ["第01集", "第02集", "第03集"]
    # 整季下载只抓一次详情（enqueue_season 抓的那次），不是每集都重新请求
    assert env["engine"].detail_calls == 1
    assert len(env["storage"].list_download_tasks("done")) == 3


def test_enqueue_season_without_detail_returns_empty(tmp_path: Path, env: dict) -> None:
    library = AnimeLibrary(env["storage"], rule_manager=env["manager"],
                           engine_factory=lambda: BrokenEngine())
    library.reload_rules()
    series = library.follow(FakeHit("a-1", "进击的巨人"))
    assert library.enqueue_season(series, tmp_path / "dl") == []


# --------------------------------------------------------------------------- 更新检查


def test_check_updates_writes_back_and_flags_new_episodes(env: dict) -> None:
    write_rule(env["user_dir"], "a", name="规则A")
    library = env["library"]
    library.reload_rules()
    hit = FakeHit("a-1", "进击的巨人")
    series = library.follow(hit)
    assert env["storage"].get_series(series.id).total_episodes == 0

    report = library.check_updates()
    assert report.checked == 1 and report.failed == 0
    stored = env["storage"].get_series(series.id)
    assert stored.total_episodes == 4                       # 首次记录：总集数写回，但不算新集
    assert stored.latest_episode == "线路B 第01集"
    assert report.with_new == []

    # 线路A 3 → 6 集，详情里总共 7 集（含线路B 的那一集）
    env["engine"].episodes = [f"第{index:02d}集" for index in range(1, 7)]
    report = library.check_updates()
    assert [item.new_episodes for item in report.items] == [["第05集", "第06集"]]
    assert report.new_count == 1
    assert env["storage"].get_series(series.id).total_episodes == 7


def test_check_updates_without_follows_or_engine(env: dict) -> None:
    library = env["library"]
    messages: list[str] = []
    report = library.check_updates(on_progress=lambda done, total, msg: messages.append(msg))
    assert report.checked == 0 and messages and "追番" in messages[0]

    library.follow(FakeHit("s1", "进击的巨人"))

    def boom():
        raise RuntimeError("引擎没装好")

    broken = AnimeLibrary(env["storage"], rule_manager=env["manager"], engine_factory=boom)
    report = broken.check_updates()
    assert report.checked == 0 and report.failed == 1
    assert report.items == []


def test_check_updates_cancel_is_respected(env: dict) -> None:
    write_rule(env["user_dir"], "a", name="规则A")
    library = env["library"]
    library.reload_rules()
    library.follow(FakeHit("a-1", "进击的巨人"))
    cancel = threading.Event()
    cancel.set()
    report = library.check_updates(cancel=cancel)
    assert report.checked == 0 and report.items == []
    assert env["engine"].detail_calls == 0
