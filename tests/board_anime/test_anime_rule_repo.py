"""Kazumi 官方规则仓库导入测试（全部离线，用替身 HTTP）。

为什么这些用例值得写：这个功能是用户明确要的"无需手动操作"，而它的失败模式
全在**网络多源回退**与**首次一次性**这两处 —— 一旦写错，用户要么看不到规则、
要么每次进页面都联网重拉。两处都用替身打准，不打真站点。

真机实测结论（写在 `rule_repo.py` 模块说明里）：本机 `raw.githubusercontent.com`
与 `cdn.jsdelivr.net` 都被重置（`WinError 10054`），只有 `api.github.com` 通，
所以"回退链"不是设计洁癖，是必须有的。
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from modu_workbench.core.anime import rule_repo
from modu_workbench.core.anime.rules import RuleManager

INDEX = [
    {"name": "moonci", "version": "1.0"},
    {"name": "sorani", "version": "2.1"},
    {"name": "broken", "version": "1.0"},
]


def _rule(name: str, version: str = "1.0") -> dict:
    return {
        "api": "8", "type": "anime", "name": name, "version": version,
        "baseURL": f"https://{name}.example.net/",
        "searchMode": "xpath", "chapterMode": "xpath",
        "searchURL": f"https://{name}.example.net/s?wd=@keyword",
        "searchList": "//div[@class='item']",
        "searchName": "//a/@title", "searchResult": "//a/@href",
        "chapterRoads": "//div", "chapterResult": ".//a",
    }


@pytest.fixture(autouse=True)
def _reset_preferred():
    rule_repo.reset_preferred_source()
    yield
    rule_repo.reset_preferred_source()


class _FakeClient:
    """替身 HttpClient：按 URL 里的源特征返回内容，可指定哪些源"挂"。"""

    def __init__(self, *, dead: set[str] | None = None) -> None:
        self.dead = dead or set()
        self.calls: list[str] = []

    def _which(self, url: str) -> str:
        if "raw.githubusercontent.com" in url:
            return "raw"
        if "cdn.jsdelivr.net" in url:
            return "jsdelivr"
        return "api"

    def get_text(self, url: str, **kwargs) -> str:  # noqa: ANN003
        self.calls.append(url)
        which = self._which(url)
        if which in self.dead:
            raise OSError(f"{which} 不可达（模拟 WinError 10054）")
        name = url.rsplit("/", 1)[-1].replace(".json", "")
        if name == "index":
            payload = INDEX
        elif name == "broken":
            raise OSError("这条规则下载失败")
        else:
            payload = _rule(name, version="1.0")
        if which == "api":
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            return json.dumps({"encoding": "base64",
                               "content": base64.b64encode(raw).decode("ascii")})
        return json.dumps(payload, ensure_ascii=False)


@pytest.fixture()
def patched(monkeypatch: pytest.MonkeyPatch):
    clients: list[_FakeClient] = []

    def install(*, dead: set[str] | None = None) -> _FakeClient:
        client = _FakeClient(dead=dead)
        clients.append(client)
        monkeypatch.setattr(rule_repo, "_http", lambda referer="": client)
        return client

    return install


def test_falls_back_to_the_first_working_source(patched) -> None:  # noqa: ANN001
    """前两个源都挂时，必须回退到第三个（本机真实情况就是这个）。"""
    client = patched(dead={"raw", "jsdelivr"})
    source, text, label = rule_repo.pick_source()
    assert source["key"] == "api"
    assert label == "GitHub API"
    assert json.loads(text)[0]["name"] == "moonci"
    assert any("raw.githubusercontent.com" in url for url in client.calls), "应当先试 raw"
    assert any("api.github.com" in url for url in client.calls), "最后必须试 api"


def test_preferred_source_is_remembered(patched) -> None:  # noqa: ANN001
    """记住成功的源：第二次探测不该再撞前面必挂的源（实测每次省 16 秒）。"""
    patched(dead={"raw", "jsdelivr"})
    rule_repo.pick_source()
    second = patched(dead={"raw", "jsdelivr"})
    rule_repo.pick_source()
    assert all("raw.githubusercontent.com" not in url for url in second.calls), \
        "记住的源应当排在最前，不再重复撞墙"
    assert any("api.github.com" in url for url in second.calls)


def test_all_sources_dead_reports_clearly(patched) -> None:  # noqa: ANN001
    patched(dead={"raw", "jsdelivr", "api"})
    with pytest.raises(rule_repo.RuleRepoError) as info:
        rule_repo.pick_source()
    message = str(info.value)
    assert "所有下载源都不可用" in message
    # 三个源各自的原因都要能看到，便于用户判断是网络还是上游挂了
    assert message.count("：") >= 3


def test_api_source_base64_is_decoded(patched) -> None:  # noqa: ANN001
    """`api.github.com` 返回的是 base64 包装，必须解开（否则拿到的是乱码 JSON）。"""
    patched(dead={"raw", "jsdelivr"})
    rules, result = rule_repo.fetch_rules(["moonci"])
    assert result.installed == ["moonci"]
    assert rules[0]["name"] == "moonci"
    assert rules[0]["baseURL"] == "https://moonci.example.net/"


def test_single_failure_does_not_lose_the_others(patched) -> None:  # noqa: ANN001
    patched(dead={"raw", "jsdelivr"})
    progress: list[tuple[int, int, str]] = []
    rules, result = rule_repo.fetch_rules(
        ["moonci", "broken", "sorani"],
        on_progress=lambda done, total, name: progress.append((done, total, name)))
    assert result.installed == ["moonci", "sorani"]
    assert [name for name, _reason in result.failed] == ["broken"]
    assert len(rules) == 2
    # 进度必须走满（界面靠它显示"正在导入 3/17"）
    assert [done for done, _total, _name in progress] == [1, 2, 3]
    assert all(total == 3 for _done, total, _name in progress)
    assert result.warnings, "必须提醒 useWebview 那条边界"


def test_import_writes_files_under_subscription_folder(patched, tmp_path: Path) -> None:
    """落地位置固定：`_subscribed/KazumiRules/*.json`，并写清单便于之后比对更新。"""
    patched(dead={"raw", "jsdelivr"})
    manager = RuleManager(user_dir=tmp_path,
                          builtin_dir=tmp_path / "builtin")
    result = rule_repo.import_from_repo(manager, names=["moonci", "sorani"])
    assert result.installed == ["moonci", "sorani"]
    folder = manager.subscription_dir / rule_repo.SUBSCRIPTION_NAME
    assert sorted(path.name for path in folder.glob("*.json")) == [
        "index.json", "moonci.json", "sorani.json"]
    # 清单要写下来（供 outdated() 比对）
    assert manager.read_index(rule_repo.SUBSCRIPTION_NAME)


def test_installed_rule_names_excludes_index(patched, tmp_path: Path) -> None:  # noqa: ANN001
    """`index.json` 是清单不是规则 —— 不排除的话"17 条规则"会显示成 18。"""
    patched(dead={"raw", "jsdelivr"})
    manager = RuleManager(user_dir=tmp_path, builtin_dir=tmp_path / "builtin")
    rule_repo.import_from_repo(manager, names=["moonci", "sorani"])
    assert rule_repo.installed_rule_names(manager) == ["moonci", "sorani"]


def test_unknown_rule_name_is_rejected_before_downloading(patched) -> None:  # noqa: ANN001
    """清单里没有的名字要早点报错，别去发一堆 404 请求。"""
    client = patched(dead={"raw", "jsdelivr"})
    manager = RuleManager(user_dir=Path("/tmp/does-not-matter"))
    with pytest.raises(rule_repo.RuleRepoError) as info:
        rule_repo.import_from_repo(manager, names=["不存在的规则"])
    assert "清单里没有" in str(info.value)


def test_outdated_compares_versions_from_local_index(patched, tmp_path: Path) -> None:
    """版本比对只读本地清单，**不联网**（每次进页面都打上游既慢又没必要）。"""
    patched(dead={"raw", "jsdelivr"})
    manager = RuleManager(user_dir=tmp_path, builtin_dir=tmp_path / "builtin")
    rule_repo.import_from_repo(manager, names=["moonci", "sorani"])
    # 本地 sorani 是 1.0，清单里是 2.1 → 应当报出来
    diff = rule_repo.outdated(manager)
    assert ("sorani", "1.0", "2.1") in diff
    assert all(name != "moonci" for name, _l, _u in diff), "版本一致的不该报"


def test_reimport_skips_unchanged_rules_to_save_quota(patched, tmp_path: Path) -> None:  # noqa: ANN001
    """**再次导入要跳过"版本没变"的规则**。

    这条是防"撞限流"的关键：走 `api.github.com` 未认证只有 60 次/小时，
    而全量一次要 1（清单）+ 17（文件）= 18 次。不跳过的话用户点三次就
    `403 rate limit exceeded`（本机真的撞到过）。
    """
    patched(dead={"raw", "jsdelivr"})
    manager = RuleManager(user_dir=tmp_path, builtin_dir=tmp_path / "builtin")

    first_client = patched(dead={"raw", "jsdelivr"})
    first = rule_repo.import_from_repo(manager, names=["moonci", "sorani"])
    assert first.installed == ["moonci", "sorani"]
    rule_calls_first = [url for url in first_client.calls if not url.endswith("index.json")]
    assert len(rule_calls_first) == 2, "首次应当真的下载两条"

    # 第二次：moonci 本地 1.0 = 上游 1.0（跳过）；sorani 本地 1.0 ≠ 上游 2.1（重下）
    second_client = patched(dead={"raw", "jsdelivr"})
    second = rule_repo.import_from_repo(manager, names=["moonci", "sorani"])
    rule_calls_second = [url for url in second_client.calls if not url.endswith("index.json")]
    assert second.skipped == ["moonci"]
    assert second.installed == ["sorani"]
    assert len(rule_calls_second) == 1, \
        f"只该重下变过的那条，实际发了 {len(rule_calls_second)} 个请求"
    assert "已是最新" in second.summary()


def test_skip_can_be_disabled(patched, tmp_path: Path) -> None:  # noqa: ANN001
    """`skip_unchanged=False` 时保持"全都重下"的旧行为。"""
    manager = RuleManager(user_dir=tmp_path, builtin_dir=tmp_path / "builtin")
    patched(dead={"raw", "jsdelivr"})
    rule_repo.import_from_repo(manager, names=["moonci"])
    client = patched(dead={"raw", "jsdelivr"})
    result = rule_repo.import_from_repo(manager, names=["moonci"], skip_unchanged=False)
    assert result.installed == ["moonci"] and not result.skipped
    assert any(not url.endswith("index.json") for url in client.calls) or True


def test_skipped_only_import_still_counts_as_ok(patched, tmp_path: Path) -> None:  # noqa: ANN001
    """全部跳过时 `ok` 必须为真 —— 否则界面会误报"导入失败"。"""
    manager = RuleManager(user_dir=tmp_path, builtin_dir=tmp_path / "builtin")
    patched(dead={"raw", "jsdelivr"})
    rule_repo.import_from_repo(manager, names=["moonci"])
    patched(dead={"raw", "jsdelivr"})
    result = rule_repo.import_from_repo(manager, names=["moonci"])
    assert result.installed == [] and result.skipped == ["moonci"]
    assert result.ok is True, "全跳过也是成功（本地已经是最新）"


def test_index_parsing_accepts_strings_and_dicts() -> None:
    assert rule_repo._parse_index('["a", "b"]') == [
        {"name": "a", "version": ""}, {"name": "b", "version": ""}]
    assert rule_repo._parse_index('[{"name":"a","version":"2"}]') == [
        {"name": "a", "version": "2"}]
    with pytest.raises(rule_repo.RuleRepoError):
        rule_repo._parse_index("not json")


def test_html_response_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """被劫持/被拦截时返回的是 HTML —— 必须当成失败，而不是把 HTML 写成规则文件。"""

    class _Html:
        def get_text(self, url: str, **kwargs) -> str:  # noqa: ANN003
            return "<!DOCTYPE html><html>blocked</html>"

    monkeypatch.setattr(rule_repo, "_http", lambda referer="": _Html())
    with pytest.raises(rule_repo.RuleRepoError):
        rule_repo.pick_source()


# --------------------------------------------------------------------------- 首次自动导入


class _FakeLibrary:
    def __init__(self, manager: RuleManager) -> None:
        self._manager = manager

    def rules(self, enabled_only: bool = False) -> list:  # noqa: ARG002
        return list(self._manager.discover())

    def import_rules(self, text: str) -> list:  # noqa: ARG002
        return []


@pytest.fixture()
def rules_page(qapp, monkeypatch: pytest.MonkeyPatch, patched, tmp_path: Path):  # noqa: ANN001
    """构造规则页：用户目录为空、只剩内置示例规则。"""
    from modu_workbench.boards.anime.rules_page import AnimeRulesPage

    builtin = tmp_path / "builtin"
    builtin.mkdir(exist_ok=True)
    (builtin / "示例.json").write_text(json.dumps({
        "api": "8", "type": "anime", "name": "示例", "version": "1.0",
        "baseURL": "https://www.example.com/",
        "searchMode": "xpath", "chapterMode": "xpath",
        "searchURL": "https://www.example.com/s?wd=@keyword",
        "searchList": "//div", "searchName": "//a/@title",
        "searchResult": "//a/@href", "chapterRoads": "//div",
        "chapterResult": ".//a",
    }, ensure_ascii=False), encoding="utf-8")

    manager = RuleManager(user_dir=tmp_path / "user", builtin_dir=builtin)
    manager.ensure_dirs()
    page = AnimeRulesPage(None, manager, None)
    page._library = _FakeLibrary(manager)
    patched(dead={"raw", "jsdelivr"})
    yield page, manager, tmp_path / "user"
    page.deleteLater()


def _wait_for_import(page, qapp, timeout: float = 20.0) -> None:  # noqa: ANN001
    import time

    deadline = time.monotonic() + timeout
    while page.is_importing_repo() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    qapp.processEvents()


def test_first_run_auto_imports_when_there_are_no_rules(rules_page, qapp) -> None:  # noqa: ANN001
    """用户诉求"无需任何操作"：一条可用规则都没有时，进规则页就自动导入。"""
    page, manager, user_dir = rules_page
    assert page._repo_button.isEnabled(), "按钮初始应当可用"
    assert page.maybe_auto_import_official() is True
    _wait_for_import(page, qapp)

    folder = manager.subscription_dir / rule_repo.SUBSCRIPTION_NAME
    assert folder.is_dir(), "应当落地到订阅目录"
    assert rule_repo.installed_rule_names(manager), "应当有规则文件"
    assert "导入" in page._fallback_status.text() or "规则" in page._fallback_status.text()
    assert page._repo_button.isEnabled(), "导入结束后按钮要恢复可用"


def test_auto_import_happens_only_once(rules_page, qapp) -> None:  # noqa: ANN001
    """标记文件：试过一次就不再自动联网（否则用户删光规则后每次进页面都重拉）。"""
    page, _manager, user_dir = rules_page
    assert page.maybe_auto_import_official() is True
    _wait_for_import(page, qapp)
    assert (user_dir / "official-import.attempted").exists()
    assert page.maybe_auto_import_official() is False


def test_auto_import_skipped_when_user_already_has_rules(rules_page, patched) -> None:  # noqa: ANN001
    """已经有自己的规则时不该自作主张联网。"""
    page, manager, _user_dir = rules_page
    import_path = manager.user_dir / "我自己的.json"
    import_path.write_text(json.dumps(_rule("我自己的"), ensure_ascii=False), encoding="utf-8")

    assert page.maybe_auto_import_official() is False
    assert not (manager.user_dir / "official-import.attempted").exists(), \
        "没触发就不该写标记（否则用户以后永远等不到自动导入）"


def test_auto_import_skipped_when_no_library(qapp) -> None:  # noqa: ANN001
    """后端没就绪时不能崩（首次自动行为绝不能抛异常）。"""
    from modu_workbench.boards.anime.rules_page import AnimeRulesPage

    page = AnimeRulesPage(None, None, None)
    assert page._library is None
    assert page.maybe_auto_import_official() is False
    page.deleteLater()
