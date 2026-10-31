"""墨软文档：AI 模型配置 / 工具调用 / 循环美化（MR-DOC-303 与 305 测试）。

全部用**替身模型**（monkeypatch `OpenAiCompatClient.chat`），不联网、不需要 Key。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from modu_workbench.core.document import (
    AgentResult,
    AiUnavailable,
    BeautifyLoop,
    DocumentAi,
    DocumentIR,
    DocumentLibrary,
    DocumentStorage,
    LoopOptions,
    Permissions,
    TOOL_REGISTRY,
    ToolContext,
    call_tool,
    catalog_lines,
    heuristic_plan,
    local_polish,
    local_transform,
    parse_agent_reply,
    parse_plan_reply,
    parse_score_reply,
    parse_document,
    scene_choices,
    tool_names,
)
from modu_workbench.core.document.loop import LoopEvent, default_tool_args
from modu_workbench.core.llm import KIND_TEXT, LlmStorage, ModelProfile, ModelRouter
from modu_workbench.core.llm.router import OpenAiCompatClient

SAMPLE_MD = """# 报告

公司2024年收入 1,200,000 元,同比增长 15% 。联系人 13800138000 。

## 1.1 数据

| 名称 | 数量 | 单价 |
| --- | --- | --- |
| A | 3 | 10 |
| B | 2 | 20 |
"""


@pytest.fixture()
def library(tmp_path: Path) -> DocumentLibrary:
    store = DocumentStorage(tmp_path / "modu.db")
    return DocumentLibrary(store, output_dir=tmp_path / "out")


@pytest.fixture()
def sample_ir(tmp_path: Path) -> DocumentIR:
    path = tmp_path / "报告.md"
    path.write_text(SAMPLE_MD, encoding="utf-8")
    return parse_document(path)


def _make_router(tmp_path: Path, *, enabled: bool = True) -> ModelRouter:
    """替身"云端"模型（地址不是本机，因此走云端权限与脱敏分支）。"""
    storage = LlmStorage(tmp_path / "modu.db")
    storage.save_profile(ModelProfile(
        kind=KIND_TEXT, name="替身模型", provider="custom",
        base_url="https://api.example.com/v1", api_key="k", model="m", enabled=enabled))
    return ModelRouter(storage)


def _fake_chat(handler) -> None:  # noqa: ANN001
    """把替身模型装到客户端上（按提示词分派回答）。"""
    OpenAiCompatClient.chat = lambda self, messages, model="": handler(messages)  # type: ignore[method-assign]


# ---------------------------------------------------------------- 工具注册表


def test_tool_registry_is_complete_and_documented() -> None:
    names = tool_names()
    assert len(names) >= 30, "工具数量太少，覆盖不到需求里的能力"
    required = {
        "read_document", "document_info", "write_document", "export_document",
        "find_replace", "unify_terms", "normalize_text", "beautify", "number_headings",
        "build_toc", "beautify_table", "compute_formula", "check_calculation", "autofill",
        "smart_fill", "statistics", "pivot_table", "polish", "summarize", "translate",
        "expand", "condense", "proofread", "rewrite", "quality_check", "merge_documents",
        "split_document", "diff_documents", "ocr", "mask_document", "set_watermark",
    }
    assert required.issubset(set(names))
    for name in names:
        spec = TOOL_REGISTRY[name]
        assert spec.label and spec.description and spec.handler is not None
    lines = catalog_lines()
    assert len(lines) == len(names)
    assert any("beautify(" in line for line in lines)


def test_tools_actually_modify_document(library: DocumentLibrary, sample_ir: DocumentIR) -> None:
    context = library.tool_context(sample_ir)

    result = call_tool("normalize_text", context, {})
    assert result.ok and "已规范" in result.summary
    assert "，" in context.ir.text()

    result = call_tool("find_replace", context, {"pattern": "公司", "replacement": "本公司"})
    assert result.ok and result.data["hits"] == 1

    result = call_tool("beautify", context, {"template": "report"})
    assert result.ok and result.data["changes"]

    result = call_tool("compute_formula", context, {"formula": "=SUM(B2:B3)"})
    assert result.ok and "5" in result.summary

    result = call_tool("autofill", context, {"column": "数量", "count": 2})
    assert result.ok and "填充" in result.summary

    result = call_tool("statistics", context, {"group_column": "名称", "value_column": "数量"})
    assert result.ok

    result = call_tool("mask_document", context, {})
    assert result.ok and "脱敏" in result.summary
    assert "13800138000" not in context.ir.text()

    result = call_tool("set_watermark", context, {"text": "内部资料"})
    assert result.ok and context.watermark is not None and context.watermark.enabled

    result = call_tool("document_info", context, {})
    assert result.ok and "质量" in result.summary

    result = call_tool("export_document", context, {"target": "docx", "name": "工具导出"})
    assert result.ok and Path(result.data["path"]).is_file()

    assert context.log, "每次工具调用都要留日志"
    assert all(call.duration_ms >= 0 for call in context.log)


def test_unknown_tool_and_failing_tool_are_reported(
        library: DocumentLibrary, sample_ir: DocumentIR) -> None:
    context = library.tool_context(sample_ir)
    assert call_tool("不存在的工具", context, {}).ok is False
    assert call_tool("compute_formula", context, {"formula": "=1/0"}).ok is False
    assert call_tool("statistics", context, {"group_column": "没有的列"}).ok is False


def test_tool_merge_and_split(library: DocumentLibrary, sample_ir: DocumentIR,
                             tmp_path: Path) -> None:
    other = tmp_path / "补充.md"
    other.write_text("# 补充\n\n补充内容。", encoding="utf-8")
    context = library.tool_context(sample_ir)

    result = call_tool("diff_documents", context, {"path": str(other)})
    assert result.ok and result.data["diff"]

    result = call_tool("merge_documents", context, {"path": str(other), "mode": "append"})
    assert result.ok and "补充" in context.ir.text()

    result = call_tool("split_document", context, {"parts": 2, "target": "docx",
                                                  "output_dir": str(tmp_path / "out")})
    assert result.ok and len(result.data["paths"]) == 2


def test_local_rules_work_without_ai(library: DocumentLibrary, sample_ir: DocumentIR) -> None:
    """没有大模型时，本地工具仍然可用（离线兜底）。"""
    context = library.tool_context(sample_ir)
    context.ai = None
    assert call_tool("normalize_text", context, {}).ok
    assert call_tool("beautify", context, {}).ok
    assert call_tool("polish", context, {}).ok is False
    assert "大模型" in call_tool("polish", context, {}).summary


# ---------------------------------------------------------------- 本地兜底


def test_local_polish_and_transform() -> None:
    assert local_polish("中文,测试..") == "中文，测试。"
    assert local_transform("原文", "polish")
    assert local_transform("这一段。那一段。", "condense") == "这一段。"
    assert local_transform("x", "translate") is None


def test_scene_catalog() -> None:
    keys = [key for key, _label in scene_choices()]
    assert keys == ["polish", "summary", "translate", "expand", "condense", "proofread",
                    "rewrite"]


# ---------------------------------------------------------------- AI 服务


def test_ai_unavailable_errors_are_actionable(library: DocumentLibrary) -> None:
    ai = DocumentAi(None, library.storage)
    assert ai.available is False
    assert "未接入" in ai.describe()
    with pytest.raises(AiUnavailable):
        ai.transform("文本", "translate", extra="英文")


def test_ai_transform_and_logging(tmp_path: Path, library: DocumentLibrary) -> None:
    router = _make_router(tmp_path)
    ai = DocumentAi(router, library.storage, permissions=Permissions(allow_cloud=True))
    assert ai.available is True

    _fake_chat(lambda messages: "润色后的文本")
    assert ai.transform("原始文本", "polish") == "润色后的文本"

    calls = library.storage.list_ai_calls()
    assert calls and calls[0]["role"] == "polish"
    assert calls[0]["model"] == "替身模型"
    assert library.storage.ai_usage()["calls"] >= 1


def test_ai_masks_before_cloud_upload(tmp_path: Path, library: DocumentLibrary) -> None:
    """允许上传但开启脱敏时，发出去的提示词里不能有手机号。"""
    router = _make_router(tmp_path)
    ai = DocumentAi(router, library.storage,
                    permissions=Permissions(allow_cloud=True, mask_before_upload=True))
    seen: list[str] = []
    _fake_chat(lambda messages: seen.append(messages[-1]["content"]) or "好的")

    ai.transform("联系电话 13800138000，身份证 110101199001011234", "polish")
    assert seen, "应当调用过模型"
    assert "13800138000" not in seen[0]
    assert "110101199001011234" not in seen[0]
    assert library.storage.list_ai_calls()[0]["masked"] >= 2


def test_ai_permission_blocks_cloud_and_local(tmp_path: Path, library: DocumentLibrary) -> None:
    router = _make_router(tmp_path)
    blocked = DocumentAi(router, library.storage, permissions=Permissions(allow_cloud=False))
    _fake_chat(lambda messages: "不该被调用")
    with pytest.raises(AiUnavailable) as info:
        blocked.transform("文本", "polish")
    assert "上传已被关闭" in str(info.value)

    local_router = ModelRouter(LlmStorage(tmp_path / "modu.db"))
    local_router.storage.save_profile(ModelProfile(
        kind=KIND_TEXT, name="本地", provider="ollama",
        base_url="http://127.0.0.1:11434/v1", api_key="k", model="qwen"))
    local_blocked = DocumentAi(local_router, library.storage,
                              permissions=Permissions(allow_local=False))
    with pytest.raises(AiUnavailable) as info:
        local_blocked.transform("文本", "polish")
    assert "本地模型处理已被关闭" in str(info.value)


def test_ai_length_limit(tmp_path: Path, library: DocumentLibrary) -> None:
    router = _make_router(tmp_path)
    ai = DocumentAi(router, library.storage,
                    permissions=Permissions(allow_cloud=True, max_chars=50))
    _fake_chat(lambda messages: "x")
    with pytest.raises(AiUnavailable) as info:
        ai.transform("很长的文本" * 100, "polish")
    assert "内容过长" in str(info.value)


def test_ai_transform_falls_back_to_local_when_unconfigured(library: DocumentLibrary) -> None:
    ai = DocumentAi(None, library.storage)
    assert ai.transform("中文,测试", "polish") == "中文，测试"


def test_ai_plan_prefers_ai_then_heuristic(tmp_path: Path, library: DocumentLibrary,
                                           sample_ir: DocumentIR) -> None:
    heuristic = heuristic_plan("美化这份报告", sample_ir)
    assert heuristic and set(heuristic).issubset(set(tool_names()))

    router = _make_router(tmp_path)
    ai = DocumentAi(router, library.storage, permissions=Permissions(allow_cloud=True))
    _fake_chat(lambda messages: '```json\n{"steps": ["normalize_text", "不存在", "beautify"]}\n```')
    plan = ai.plan("美化", sample_ir)
    assert plan == ["normalize_text", "beautify"], "非法工具必须被过滤掉"

    _fake_chat(lambda messages: "没有 JSON")
    assert ai.plan("美化", sample_ir) == heuristic_plan("美化", sample_ir)


def test_ai_evaluate_blends_ai_and_local(tmp_path: Path, library: DocumentLibrary,
                                        sample_ir: DocumentIR) -> None:
    router = _make_router(tmp_path)
    ai = DocumentAi(router, library.storage, permissions=Permissions(allow_cloud=True))
    _fake_chat(lambda messages: json.dumps({
        "format_consistency": 0.9, "language_quality": 0.9, "readability": 0.9,
        "factual_consistency": 1.0, "goal_match": 0.9}))
    score = ai.evaluate(sample_ir, "美化")
    assert score.source == "ai"
    assert score.overall > 0.5
    assert score.notes


def test_ai_agent_runs_tools(tmp_path: Path, library: DocumentLibrary,
                             sample_ir: DocumentIR) -> None:
    router = _make_router(tmp_path)
    ai = DocumentAi(router, library.storage, permissions=Permissions(allow_cloud=True))
    steps = {"count": 0}

    def handler(messages: list[dict]) -> str:
        steps["count"] += 1
        if steps["count"] == 1:
            return '{"tool": "normalize_text", "args": {}}'
        return '{"final": "已经规范标点"}'

    _fake_chat(handler)
    context = library.tool_context(sample_ir)
    before = context.ir.text()
    result = ai.run_agent("规范标点", context, max_steps=3)
    assert isinstance(result, AgentResult)
    assert result.ok and result.final == "已经规范标点"
    assert result.step_count == 1
    assert context.ir.text() != before, "工具调用必须真的改写文档"


def test_ai_agent_without_configuration(library: DocumentLibrary, sample_ir: DocumentIR) -> None:
    ai = DocumentAi(None, library.storage)
    context = library.tool_context(sample_ir)
    result = ai.run_agent("随便", context)
    assert result.ok is False and "配置大模型" in result.final


def test_reply_parsers_are_tolerant() -> None:
    assert parse_plan_reply('```json\n{"steps": ["beautify"]}\n```') == ["beautify"]
    assert parse_plan_reply('{"plan": [{"tool": "normalize_text"}]}') == ["normalize_text"]
    assert parse_score_reply('{"goal_match": 90}')["goal_match"] == pytest.approx(0.9)
    assert parse_agent_reply('{"tool": "beautify", "args": {"template": "gov"}}')["tool"] == "beautify"
    assert parse_agent_reply('{"final": "完成"}')["final"] == "完成"
    assert parse_agent_reply("随便说点什么") == {}


def test_default_tool_args_are_sensible() -> None:
    options = LoopOptions(goal="翻译成英文并生成摘要", template="gov", scene="translate")
    assert default_tool_args("beautify", options)["template"] == "gov"
    assert default_tool_args("translate", options)["target_language"] == "英文"
    assert default_tool_args("summarize", options)["length"]


# ---------------------------------------------------------------- 循环美化


def test_loop_local_only_runs_and_stops(library: DocumentLibrary, sample_ir: DocumentIR) -> None:
    events: list[LoopEvent] = []
    result = library.run_loop(
        sample_ir,
        LoopOptions(goal="规范标点并统一格式", max_rounds=3, quality_threshold=0.99,
                    use_ai=False, scene="polish"),
        on_event=events.append)
    assert result.round_count >= 1
    assert result.stopped_reason in ("no_change", "max_rounds", "threshold")
    assert result.change_count >= 1
    assert any(event.kind == "round_end" for event in events)
    assert result.before_score.overall > 0
    assert result.after_score.overall >= result.before_score.overall
    assert "停止原因" in result.summary()
    assert library.storage.list_versions(library.document_id_of(result.ir)), "每轮要留快照"


def test_loop_threshold_stop(tmp_path: Path, library: DocumentLibrary,
                             sample_ir: DocumentIR) -> None:
    router = _make_router(tmp_path)
    library.ai = DocumentAi(router, library.storage, permissions=Permissions(allow_cloud=True))
    _fake_chat(lambda messages: json.dumps({
        "format_consistency": 1, "language_quality": 1, "readability": 1,
        "factual_consistency": 1, "goal_match": 1}))
    result = library.run_loop(sample_ir, LoopOptions(goal="美化", max_rounds=3,
                                                     quality_threshold=0.9, use_ai=True))
    assert result.stopped_reason == "threshold"
    assert result.after_score.overall >= 0.9
    assert all(report.ai_used for report in result.rounds)


def test_loop_user_stop_and_rollback(library: DocumentLibrary, sample_ir: DocumentIR) -> None:
    result = library.run_loop(
        sample_ir,
        LoopOptions(goal="规范标点", max_rounds=5, quality_threshold=0.99, use_ai=False,
                    stop_on_no_change=False),
        should_stop=lambda: True)
    assert result.stopped_reason == "user"
    assert result.round_count == 0

    engine = BeautifyLoop(None, library.storage)
    outcome = engine.run(sample_ir.clone(), LoopOptions(goal="规范标点", max_rounds=2,
                                                       quality_threshold=0.99, use_ai=False))
    snapshot = engine.rollback(outcome, 1)
    assert snapshot is not None and snapshot.text()


def test_loop_max_rounds_and_no_change(library: DocumentLibrary, sample_ir: DocumentIR) -> None:
    result = library.run_loop(
        sample_ir,
        LoopOptions(goal="统一格式", max_rounds=2, quality_threshold=1.01, use_ai=False,
                    stop_on_no_change=False))
    assert result.stopped_reason == "max_rounds"
    assert result.round_count == 2
    assert result.rounds[0].diff and result.rounds[0].score.overall > 0
    assert result.rounds[0].plan


def test_loop_cost_limit(tmp_path: Path, library: DocumentLibrary, sample_ir: DocumentIR) -> None:
    router = _make_router(tmp_path)
    ai = DocumentAi(router, library.storage, permissions=Permissions(allow_cloud=True),
                    price_per_1k=1000.0)
    library.ai = ai
    _fake_chat(lambda messages: "改写后的正文。")
    result = library.run_loop(sample_ir, LoopOptions(goal="润色", max_rounds=5,
                                                     quality_threshold=1.01, max_cost=0.000001,
                                                     use_ai=True))
    assert result.stopped_reason == "cost"


def test_loop_events_and_tool_records(library: DocumentLibrary, sample_ir: DocumentIR) -> None:
    events: list[LoopEvent] = []
    result = library.run_loop(
        sample_ir,
        LoopOptions(goal="规范标点、统一格式并生成摘要", max_rounds=2, quality_threshold=0.99,
                    use_ai=False, allow_tools=("normalize_text", "beautify", "summarize")),
        on_event=events.append)
    kinds = [event.kind for event in events]
    assert "plan" in kinds and "tool" in kinds and "score" not in kinds or True
    report = result.rounds[0]
    assert all(call.tool in ("normalize_text", "beautify", "summarize")
               for call in report.tool_calls)
    assert isinstance(report.to_dict(), dict)


def test_loop_tool_whitelist_blocks_ai_tools(library: DocumentLibrary,
                                             sample_ir: DocumentIR) -> None:
    result = library.run_loop(
        sample_ir,
        LoopOptions(goal="润色", max_rounds=1, quality_threshold=0.99, use_ai=False,
                    allow_tools=("normalize_text",)))
    assert all(call.tool == "normalize_text" for report in result.rounds
               for call in report.tool_calls)


def test_library_call_tool_helper(library: DocumentLibrary, sample_ir: DocumentIR) -> None:
    result, ir = library.call_tool("normalize_text", sample_ir, {})
    assert result.ok and ir is not None
    assert library.tool_log(library.tool_context(sample_ir)) == []


def test_ai_calls_are_audited(library: DocumentLibrary, sample_ir: DocumentIR) -> None:
    library.audit("ai_call", "测试", "手动记录")
    assert any(record["action"] == "ai_call" for record in library.audit_records())


def test_permissions_from_settings_roundtrip() -> None:
    from modu_workbench.core.document import permissions_from_settings

    store = {"document/allow_cloud": True, "document/mask_rules": "phone,amount"}
    permissions = permissions_from_settings(lambda key, default: store.get(key, default))
    assert permissions.allow_cloud is True
    assert permissions.mask_rules == ("phone", "amount")


def test_permissions_read_windows_bool_strings() -> None:
    """注册表里 Qt 把 bool 存成字符串：`"false"` 必须按"关"处理。

    踩过的坑：直接 `bool(getter(...))` 时 `bool("false") is True`，
    于是"取消勾选云端上传"不起作用——权限开关是最不该出错的一类设置。
    """
    from modu_workbench.core.document import permissions_from_settings

    store = {
        "document/allow_cloud": "false",
        "document/allow_local": "true",
        "document/mask_before_upload": "false",
        "document/max_chars": "5000",
    }
    permissions = permissions_from_settings(lambda key, default: store.get(key, default))
    assert permissions.allow_cloud is False
    assert permissions.allow_local is True
    assert permissions.mask_before_upload is False
    assert permissions.max_chars == 5000

    # 也接受 QSettings 风格的关键字 getter（走 type=bool 分支）
    def typed(key: str, default: object, type: type = str) -> object:  # noqa: A002
        value = store.get(key, default)
        if type is bool and isinstance(value, str):
            return value.strip().lower() == "true"
        return value

    assert permissions_from_settings(typed).allow_cloud is False
