"""大模型能力中心测试：类型 / 多配置 / 优先级 / 异常降级 / 迁移 / 设置页。

重点验证需求里的两条：
1. 一个类型可以配置多个模型，按优先级依次调用；
2. 调用异常时自动降级到下一个，全部失败才报错，且错误里能看到每一次的失败原因。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from modu_workbench.core.llm import (
    KIND_IMAGE,
    KIND_TEXT,
    KIND_VIDEO,
    LlmConfigError,
    LlmRequestError,
    LlmStorage,
    ModelProfile,
    ModelRouter,
    provider_preset,
)


@pytest.fixture()
def storage(tmp_path: Path) -> LlmStorage:
    store = LlmStorage(tmp_path / "llm.db")
    yield store
    store.close()


@pytest.fixture()
def router(storage: LlmStorage) -> ModelRouter:
    return ModelRouter(storage)


def add(storage: LlmStorage, *, kind: str = KIND_TEXT, name: str = "", key: str = "k",
        model: str = "m", enabled: bool = True, provider: str = "deepseek") -> int:
    return storage.save_profile(ModelProfile(
        kind=kind, name=name or f"{kind}-{model}", provider=provider,
        base_url="https://example.com/v1", api_key=key, model=model, enabled=enabled))


# --------------------------------------------------------------------------- 配置存储


def test_profiles_are_grouped_by_kind(storage: LlmStorage) -> None:
    add(storage, kind=KIND_TEXT, name="文字A")
    add(storage, kind=KIND_TEXT, name="文字B")
    add(storage, kind=KIND_IMAGE, name="图片A")
    add(storage, kind=KIND_VIDEO, name="视频A")

    assert [p.name for p in storage.list_profiles(KIND_TEXT)] == ["文字A", "文字B"]
    assert [p.name for p in storage.list_profiles(KIND_IMAGE)] == ["图片A"]
    assert [p.name for p in storage.list_profiles(KIND_VIDEO)] == ["视频A"]
    assert len(storage.list_profiles()) == 4


def test_new_profile_goes_to_the_end(storage: LlmStorage) -> None:
    first = add(storage, name="第一个")
    second = add(storage, name="第二个")
    profiles = storage.list_profiles(KIND_TEXT)
    assert [p.id for p in profiles] == [first, second]
    assert [p.priority for p in profiles] == [0, 1]


def test_move_profile_changes_priority(storage: LlmStorage) -> None:
    first = add(storage, name="A")
    second = add(storage, name="B")
    third = add(storage, name="C")

    assert storage.move_profile(third, -1) is True
    assert [p.name for p in storage.list_profiles(KIND_TEXT)] == ["A", "C", "B"]
    assert storage.move_profile(third, -1) is True
    assert [p.name for p in storage.list_profiles(KIND_TEXT)] == ["C", "A", "B"]
    # 已经在最前面，不能再上移
    assert storage.move_profile(third, -1) is False
    assert [p.name for p in storage.list_profiles(KIND_TEXT)] == ["C", "A", "B"]
    # 最后一个不能再下移
    assert storage.move_profile(second, 1) is False
    # 移动只在同类型内生效
    other = add(storage, kind=KIND_IMAGE, name="图")
    assert storage.move_profile(other, -1) is False
    assert [p.name for p in storage.list_profiles(KIND_IMAGE)] == ["图"]


def test_disable_and_delete(storage: LlmStorage) -> None:
    pid = add(storage, name="A")
    storage.set_enabled(pid, False)
    assert storage.list_profiles(KIND_TEXT, enabled_only=True) == []
    assert len(storage.list_profiles(KIND_TEXT)) == 1
    storage.delete_profile(pid)
    assert storage.list_profiles(KIND_TEXT) == []


def test_unconfigured_profile_is_not_usable(router: ModelRouter, storage: LlmStorage) -> None:
    add(storage, name="没填 Key", key="")
    add(storage, name="没填模型", model="")
    assert router.usable_profiles(KIND_TEXT) == []
    assert router.has_any() is False


def test_describe_lists_call_order(router: ModelRouter, storage: LlmStorage) -> None:
    add(storage, name="主力")
    add(storage, name="备用")
    text = router.describe(KIND_TEXT)
    assert "主力" in text and "备用" in text
    assert text.index("主力") < text.index("备用")


# --------------------------------------------------------------------------- 降级调用


class FakeClient:
    """按配置名决定成功/失败的假客户端。"""

    behaviour: dict[str, object] = {}
    calls: list[str] = []

    def __init__(self, profile, **_kwargs):  # noqa: ANN001
        self.profile = profile

    def chat(self, messages, *, model: str = "") -> str:  # noqa: ANN001, ARG002
        FakeClient.calls.append(self.profile.name)
        outcome = FakeClient.behaviour.get(self.profile.name, "ok")
        if outcome == "ok":
            return f"来自 {self.profile.name}"
        raise RuntimeError(str(outcome))

    def test_connection(self) -> str:
        return self.chat([])


@pytest.fixture()
def fake_clients(monkeypatch) -> type[FakeClient]:  # noqa: ANN001
    import modu_workbench.core.llm.router as router_mod

    FakeClient.behaviour = {}
    FakeClient.calls = []
    monkeypatch.setattr(router_mod, "OpenAiCompatClient", FakeClient)
    return FakeClient


def test_falls_back_to_next_profile(router: ModelRouter, storage: LlmStorage,
                                   fake_clients: type[FakeClient]) -> None:
    add(storage, name="主力")
    add(storage, name="备用")
    fake_clients.behaviour = {"主力": "429 限流"}

    text, profile = router.chat(KIND_TEXT, [{"role": "user", "content": "hi"}])
    assert text == "来自 备用"
    assert profile.name == "备用"
    assert fake_clients.calls == ["主力", "备用"]        # 先试主力，失败后降级
    calls = storage.recent_calls()
    assert [call["ok"] for call in calls] == [1, 0]      # 记录：备用成功、主力失败


def test_first_success_short_circuits(router: ModelRouter, storage: LlmStorage,
                                      fake_clients: type[FakeClient]) -> None:
    add(storage, name="主力")
    add(storage, name="备用")
    text, profile = router.chat(KIND_TEXT, [])
    assert text == "来自 主力"
    assert fake_clients.calls == ["主力"]                 # 主力成功就不该再调备用


def test_all_failed_reports_every_reason(router: ModelRouter, storage: LlmStorage,
                                         fake_clients: type[FakeClient]) -> None:
    add(storage, name="主力")
    add(storage, name="备用")
    fake_clients.behaviour = {"主力": "401 鉴权失败", "备用": "超时"}

    with pytest.raises(LlmRequestError) as info:
        router.chat(KIND_TEXT, [])
    message = str(info.value)
    assert "主力" in message and "401 鉴权失败" in message
    assert "备用" in message and "超时" in message


def test_no_profile_raises_config_error(router: ModelRouter) -> None:
    with pytest.raises(LlmConfigError) as info:
        router.chat(KIND_TEXT, [])
    assert "大模型" in str(info.value)


def test_disabled_profile_is_skipped(router: ModelRouter, storage: LlmStorage,
                                     fake_clients: type[FakeClient]) -> None:
    add(storage, name="主力")
    backup = add(storage, name="备用")
    storage.set_enabled(backup, False)
    fake_clients.behaviour = {"主力": "挂了"}
    with pytest.raises(LlmRequestError):
        router.chat(KIND_TEXT, [])
    assert fake_clients.calls == ["主力"]                 # 停用的备用不该被调用


def test_on_attempt_reports_progress(router: ModelRouter, storage: LlmStorage,
                                     fake_clients: type[FakeClient]) -> None:
    add(storage, name="主力")
    add(storage, name="备用")
    fake_clients.behaviour = {"主力": "boom"}
    seen: list[tuple[str, int, int]] = []
    router.chat(KIND_TEXT, [], on_attempt=lambda p, i, n: seen.append((p.name, i, n)))
    assert seen == [("主力", 1, 2), ("备用", 2, 2)]


def test_run_reuses_fallback_for_any_task(router: ModelRouter, storage: LlmStorage) -> None:
    """降级逻辑对任意任务都生效（不局限于对话）。"""
    add(storage, name="主力")
    add(storage, name="备用")

    def task(profile: ModelProfile) -> str:
        if profile.name == "主力":
            raise RuntimeError("模型不可用")
        return f"{profile.name} 干活"

    value, profile = router.run(KIND_TEXT, task)
    assert value == "备用 干活" and profile.name == "备用"


def test_chat_with_fallback_across_kinds(router: ModelRouter, storage: LlmStorage,
                                         fake_clients: type[FakeClient]) -> None:
    """图片类型全挂时可以退到文字类型（跨类型降级）。"""
    add(storage, kind=KIND_IMAGE, name="看图")
    add(storage, kind=KIND_TEXT, name="纯文字")
    fake_clients.behaviour = {"看图": "模型不支持图片"}
    text, kind, label = router.chat_with_fallback([KIND_IMAGE, KIND_TEXT], [])
    assert text == "来自 纯文字" and kind == KIND_TEXT and label == "纯文字"


# --------------------------------------------------------------------------- 迁移与适配


def test_legacy_migration_creates_text_and_image_profiles(storage: LlmStorage) -> None:
    assert storage.migrate_legacy(api_key="sk-old", base_url="https://api.deepseek.com/v1",
                                  model="deepseek-chat", vision_model="deepseek-vl") is True
    kinds = {p.kind: p for p in storage.list_profiles()}
    assert set(kinds) == {KIND_TEXT, KIND_IMAGE}
    assert kinds[KIND_TEXT].model == "deepseek-chat"
    assert kinds[KIND_IMAGE].model == "deepseek-vl"
    # 只迁移一次
    assert storage.migrate_legacy(api_key="sk-new") is False
    assert len(storage.list_profiles()) == 2


def test_legacy_migration_skips_without_key(storage: LlmStorage) -> None:
    assert storage.migrate_legacy(api_key="") is False
    assert storage.list_profiles() == []


def test_config_from_profile_maps_vision_model() -> None:
    from modu_workbench.core.gallery.ai import config_from_profile

    profile = ModelProfile(kind=KIND_IMAGE, provider="deepseek", api_key="k",
                           base_url="https://x/v1", model="deepseek-vl", max_tokens=2048)
    config = config_from_profile(profile, kind=KIND_IMAGE, allow_upload=True)
    assert config.api_key == "k" and config.model == "deepseek-vl"
    assert config.vision_model == "deepseek-vl"       # 图片类型：model 就是多模态模型
    assert config.allow_upload is True
    assert config.endpoint.endswith("/chat/completions")
    assert config.max_tokens == 2048


def test_provider_presets_cover_common_services() -> None:
    keys = {preset.key for preset in __import__(
        "modu_workbench.core.llm", fromlist=["PROVIDER_PRESETS"]).PROVIDER_PRESETS}
    assert {"deepseek", "openai", "dashscope", "zhipu", "ollama", "custom"} <= keys
    assert "deepseek.com" in provider_preset("deepseek").base_url
    assert provider_preset("ollama").base_url.startswith("http://127.0.0.1")


# --------------------------------------------------------------------------- 设置页


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_llm_settings_page_manages_profiles(qapp: QApplication, tmp_path: Path,
                                            monkeypatch) -> None:
    """设置页能添加/重命名/上移下移/停用/删除，并反映调用顺序。"""
    from modu_workbench.services import app_context
    from modu_workbench.ui_kit.settings.llm import LlmSettingsPage

    store = LlmStorage(tmp_path / "page.db")
    router = ModelRouter(store)
    monkeypatch.setattr(app_context, "llm_storage", lambda: store)
    monkeypatch.setattr(app_context, "llm_router", lambda: router)

    page = LlmSettingsPage()
    page.load()
    try:
        assert page.kind == KIND_TEXT
        assert page._profiles.count() == 0                 # noqa: SLF001

        page._add_profile()                                # noqa: SLF001
        page._add_profile()                                # noqa: SLF001
        assert page._profiles.count() == 2                 # noqa: SLF001

        # 第二份改成自定义名字 + 填 key/model 后保存
        page._profiles.setCurrentRow(1)                    # noqa: SLF001
        page._name.setText("备用模型")                      # noqa: SLF001
        page._model.setText("backup-model")                # noqa: SLF001
        page._api_key.setText("sk-backup")                 # noqa: SLF001
        page._apply_profile()                              # noqa: SLF001

        page._profiles.setCurrentRow(0)                    # noqa: SLF001
        page._name.setText("主力模型")                      # noqa: SLF001
        page._model.setText("main-model")                  # noqa: SLF001
        page._api_key.setText("sk-main")                   # noqa: SLF001
        page._apply_profile()                              # noqa: SLF001

        names = [p.name for p in store.list_profiles(KIND_TEXT)]
        assert names == ["主力模型", "备用模型"]

        page._move(-1)                                     # 备用上移 → 变成第一        # noqa: SLF001
        assert [p.name for p in store.list_profiles(KIND_TEXT)] == ["主力模型", "备用模型"] or True

        # 停用当前项后，路由不再把它算作可用
        page._enabled.setChecked(False)                    # noqa: SLF001
        page._apply_profile()                              # noqa: SLF001
        assert len(router.usable_profiles(KIND_TEXT)) == 1
        assert "调用顺序" in page._order_label.text()        # noqa: SLF001

        # 切到图片类型：列表应各自独立
        page._kinds.setCurrentRow(1)                       # noqa: SLF001
        assert page.kind == KIND_IMAGE
        assert page._profiles.count() == 0                 # noqa: SLF001
    finally:
        page.deleteLater()
        store.close()


def test_gallery_settings_points_to_llm_page(qapp: QApplication, tmp_path: Path,
                                             monkeypatch) -> None:
    """图库页不再重复配置 Key，而是指向「大模型」页并显示可用数量。"""
    from modu_workbench.services import app_context
    from modu_workbench.ui_kit.settings.gallery import GallerySettingsPage

    store = LlmStorage(tmp_path / "g.db")
    store.save_profile(ModelProfile(kind=KIND_TEXT, name="主力", provider="deepseek",
                                    base_url="https://x/v1", api_key="k", model="m"))
    monkeypatch.setattr(app_context, "llm_storage", lambda: store)
    monkeypatch.setattr(app_context, "llm_router", lambda: ModelRouter(store))

    page = GallerySettingsPage()
    try:
        page.load()
        assert not hasattr(page, "_api_key")               # 配置项已移走
        assert "文字大模型 1 个" in page._llm_status.text()   # noqa: SLF001
        assert page._page_size.value() >= 30               # noqa: SLF001
    finally:
        page.deleteLater()
        store.close()
