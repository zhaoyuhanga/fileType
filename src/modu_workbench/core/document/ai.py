"""墨软文档：AI 语义层（MR-DOC-303）。

- **模型配置复用大模型能力中心**：`core/llm` 里已经支持"同类型多份配置 + 按优先级
  失败降级"，本模块不重复造轮子，只负责"把文档任务翻译成提示词、执行工具、记账"；
- **角色与场景**：润色 / 摘要 / 翻译 / 扩写 / 缩写 / 纠错 / 改写（`SCENES`）；
- **工具调用**：`run_agent()` 走"JSON 工具协议"（`{"tool": "...", "args": {...}}`），
  任何 OpenAI 兼容模型都能用，不依赖厂商各自的 function calling；
- **权限控制**：每次调用前检查 `Permissions`（是否允许云端/本地、模型白名单、长度上限），
  **默认不上传**；开启脱敏时先遮住手机号/身份证/金额再发送；
- **调用日志**：模型、提示词、工具、输入输出、耗时、成本估算全部落 `doc_ai_calls`；
- **离线兜底**：没配模型时 `local_polish` / 抽取式摘要 / 规则计划仍然可用，
  因此"格式化、合并、循环美化"在完全离线时也能跑（只是 AI 那几步不参与）。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

from modu_workbench.core.llm import (
    KIND_TEXT,
    LlmConfigError,
    ModelRouter,
    OpenAiCompatClient,
)

from . import quality
from . import security
from .models import AiCallRecord, DocumentIR, QualityScore, ToolCallRecord
from .tools import TOOL_REGISTRY, ToolContext, call_tool, catalog_lines


class AiUnavailable(RuntimeError):
    """没有可用的模型配置，或调用被权限/长度限制拦下。"""


# ---------------------------------------------------------------- 场景（角色）


@dataclass(frozen=True)
class SceneSpec:
    key: str
    label: str
    instruction: str
    system: str = "你是严谨的中文文档编辑，只输出处理后的正文，不要解释、不要客套话。"
    tool: str = ""


SCENES: dict[str, SceneSpec] = {
    "polish": SceneSpec(
        "polish", "润色",
        "润色下面的文档：修正病句与用词，保持原意与数据不变，保持原有段落结构。"),
    "summary": SceneSpec(
        "summary", "摘要",
        "为下面的文档写摘要：{extra}。用中文，条目化，不要编造原文没有的信息。"),
    "translate": SceneSpec(
        "translate", "翻译",
        "把下面的文档翻译成{extra}，保留段落结构与数字，专业术语保持准确。"),
    "expand": SceneSpec(
        "expand", "扩写",
        "在不改变事实的前提下扩写下面的文档：{extra}。补充必要的说明与过渡，不要注水。"),
    "condense": SceneSpec(
        "condense", "缩写",
        "压缩下面的文档篇幅{extra}，保留全部关键数据与结论，删除重复与冗余表达。"),
    "proofread": SceneSpec(
        "proofread", "纠错",
        "校对下面的文档：改正错别字、标点与语病{extra}。只输出改正后的正文。"),
    "rewrite": SceneSpec(
        "rewrite", "改写",
        "按「{extra}」的风格改写下面的文档，保留事实与数据，保持段落结构。"),
}

SCENE_KEYS = tuple(SCENES)
SCENE_LABELS = {key: spec.label for key, spec in SCENES.items()}


def scene_choices() -> list[tuple[str, str]]:
    return [(key, spec.label) for key, spec in SCENES.items()]


# ---------------------------------------------------------------- 本地兜底


_ASCII_TO_CN = {",": "，", ";": "；", ":": "：", "?": "？", "!": "！"}
_CN_CHAR = r"\u3400-\u9fff\u3000-\u303f"


def local_polish(text: str) -> str:
    """本地文本规范（不联网）：中英标点、重复标点、多余空格、句末句号。

    这是"没有大模型也能美化"的底线；有模型时 AI 润色会在此基础上继续做语言优化。
    """
    if not text:
        return text
    result = text
    # 1) 中文语境里的英文标点 → 中文标点
    for ascii_char, cn_char in _ASCII_TO_CN.items():
        result = re.sub(rf"(?<=[{_CN_CHAR}]){re.escape(ascii_char)}", cn_char, result)
    # 1b) 中文句子后的英文句点（含 ".."/"..."）→ 中文句号；"3.5"、"1.1" 这类数字不受影响
    result = re.sub(rf"(?<=[{_CN_CHAR}])\.+(?=$|\s|[{_CN_CHAR}])", "。", result)
    # 2) 中文之间的多余空格
    result = re.sub(rf"(?<=[{_CN_CHAR}])[ \t]+(?=[{_CN_CHAR}])", "", result)
    # 3) 连续重复标点
    result = re.sub(r"([，。！？；：])\1+", r"\1", result)
    # 4) 多个空格合并
    result = re.sub(r"[ \t]{2,}", " ", result)
    # 5) 段落末尾补句号（只处理以中文结尾且没有句末标点的正文行）
    lines: list[str] = []
    for line in result.split("\n"):
        stripped = line.rstrip()
        if (len(stripped) >= 12 and re.search(rf"[{_CN_CHAR}]$", stripped)
                and not re.search(r"[。！？；：，、）】」…\-—]$", stripped)):
            stripped += "。"
        lines.append(stripped)
    return "\n".join(lines)


def local_transform(text: str, scene: str, *, extra: str = "", max_items: int = 6) -> Optional[str]:
    """不需要大模型就能做的变换（做不到的场景返回 None）。"""
    if scene in ("polish", "proofread"):
        return local_polish(text)
    if scene == "condense":
        sentences = [item.strip() for item in re.split(r"(?<=[。！？!?])", text) if item.strip()]
        keep = max(1, len(sentences) // 2)
        return "".join(sentences[:keep])
    if scene == "summary":
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        picked = lines[:max_items]
        return "\n".join(f"{index}. {line[:60]}" for index, line in enumerate(picked, start=1))
    return None


# ---------------------------------------------------------------- 提示词


def build_messages(text: str, scene: str, *, extra: str = "") -> list[dict]:
    spec = SCENES.get(scene, SCENES["polish"])
    instruction = spec.instruction.format(extra=extra or "").strip()
    return [
        {"role": "system", "content": spec.system},
        {"role": "user", "content": f"{instruction}\n\n文档内容：\n{text}"},
    ]


PLAN_SYSTEM = (
    "你是文档整理助手。根据文档体检结果与用户目标，选择需要调用的工具。"
    '只输出 JSON，形如 {"steps": ["normalize_text", "beautify"]}，最多 5 个，按执行顺序。'
    "可用工具：\n"
)

EVALUATE_SYSTEM = (
    "你是文档质量评估员。按五项打分（0~1 的小数）：format_consistency（格式一致性）、"
    "language_quality（语言质量）、readability（可读性）、factual_consistency（事实一致性，"
    "数字是否被改动）、goal_match（与目标的匹配度）。"
    '只输出 JSON，形如 {"format_consistency":0.9,...}，不要解释。'
)

AGENT_SYSTEM = (
    "你是文档处理智能体。可以调用下列工具来完成任务：\n{tools}\n\n"
    "规则：\n"
    "1) 需要调用工具时，只输出一个 JSON 对象：{{\"tool\": \"工具名\", \"args\": {{...}}}}；\n"
    "2) 任务完成时输出：{{\"final\": \"一句话说明结果\"}}；\n"
    "3) 不要输出多余文字，不要输出 Markdown 代码块以外的解释。"
)


# ---------------------------------------------------------------- 调用结果


@dataclass
class AgentResult:
    """一次"AI 工具调用循环"的结果。"""

    ok: bool
    final: str = ""
    steps: list[ToolCallRecord] = field(default_factory=list)
    transcript: list[str] = field(default_factory=list)

    @property
    def step_count(self) -> int:
        return len(self.steps)


# ---------------------------------------------------------------- 主类


class DocumentAi:
    """文档 AI 服务：场景变换 + 工具调用 + 质量评估 + 记账。"""

    def __init__(self, router: Optional[ModelRouter] = None, storage: Any = None, *,
                 permissions: Optional[security.Permissions] = None,
                 kind: str = KIND_TEXT, price_per_1k: float = 0.0,
                 permissions_provider: Optional[Callable[[], security.Permissions]] = None):
        self._router = router
        self._storage = storage
        self.permissions = permissions or security.Permissions()
        # 权限可以"随设置实时变化"：给了 provider 就每次调用前重新读取，
        # 用户在设置页改完开关不必重启应用（板块界面也不用来回传播状态）
        self._permissions_provider = permissions_provider
        self.kind = kind
        self.price_per_1k = float(price_per_1k or 0.0)

    def current_permissions(self) -> security.Permissions:
        if self._permissions_provider is not None:
            try:
                return self._permissions_provider()
            except Exception:  # noqa: BLE001  读取设置失败时退回构造时的权限
                pass
        return self.permissions

    # ---------- 能力状态 ----------

    @property
    def available(self) -> bool:
        if self._router is None:
            return False
        try:
            return bool(self._router.usable_profiles(self.kind))
        except Exception:  # noqa: BLE001
            return False

    def describe(self) -> str:
        if self._router is None:
            return "未接入大模型（本地功能不受影响）"
        try:
            return self._router.describe(self.kind)
        except Exception as error:  # noqa: BLE001
            return f"大模型状态未知：{error}"

    def chat_callback(self) -> Callable[[list[dict]], str]:
        """给合并/循环引擎用的回调（自动带权限、脱敏与日志）。"""
        return lambda messages: self._chat(messages, role="callback")

    def test_connection(self) -> str:
        if not self.available:
            raise AiUnavailable(self.describe())
        return self._chat([{"role": "user", "content": "回复两个字：可用"}], role="test")

    # ---------- 场景变换 ----------

    def transform(self, text: str, scene: str, *, extra: str = "", document_id: int = 0,
                  on_attempt: Optional[Callable[[str, int, int], None]] = None) -> str:
        """按场景处理文本（AI 优先，离线时用本地兜底）。"""
        content = (text or "").strip()
        if not content:
            return ""
        if self.available:
            if on_attempt is not None:
                on_attempt(scene, 1, 1)
            return self._chat(build_messages(content, scene, extra=extra), role=scene,
                              document_id=document_id, scene=scene)
        fallback = local_transform(content, scene, extra=extra)
        if fallback is None:
            raise AiUnavailable(
                f"「{SCENE_LABELS.get(scene, scene)}」需要配置大模型："
                "请到「设置 → 大模型」添加文字模型（离线能力仍可用于美化/合并/计算）")
        self._record(AiCallRecord(kind="local", role=scene, model="本地规则", ok=True,
                                  detail="未配置大模型，使用本地规则", masked=0))
        return fallback

    def polish(self, text: str, **kwargs) -> str:  # noqa: ANN003
        return self.transform(text, "polish", **kwargs)

    def summarize(self, text: str, **kwargs) -> str:  # noqa: ANN003
        return self.transform(text, "summary", **kwargs)

    def translate(self, text: str, target_language: str = "英文", **kwargs) -> str:  # noqa: ANN003
        return self.transform(text, "translate", extra=target_language, **kwargs)

    # ---------- 计划 ----------

    def plan(self, goal: str, ir: DocumentIR, *, max_steps: int = 5,
             document_id: int = 0) -> list[str]:
        """把目标拆成工具调用序列（AI 优先；AI 不可用时按体检结果生成规则计划）。"""
        heuristic = heuristic_plan(goal, ir, max_steps=max_steps)
        if not self.available:
            return heuristic
        analysis = quality.analyze_document(ir)
        prompt = (
            f"用户目标：{goal or '把文档整理得更规范'}\n"
            f"文档体检：{json.dumps({k: analysis[k] for k in ('blocks', 'headings', 'tables', 'issues')}, ensure_ascii=False)}\n"
            f"文档开头：{ir.text()[:800]}"
        )
        messages = [
            {"role": "system", "content": PLAN_SYSTEM + "\n".join(catalog_lines())},
            {"role": "user", "content": prompt},
        ]
        try:
            reply = self._chat(messages, role="plan", document_id=document_id)
        except AiUnavailable:
            return heuristic
        steps = parse_plan_reply(reply)
        if not steps:
            return heuristic
        # 只保留真实存在的工具，且最多 max_steps 个
        valid = [name for name in steps if name in TOOL_REGISTRY][:max_steps]
        return valid or heuristic

    # ---------- 质量评估 ----------

    def evaluate(self, ir: DocumentIR, goal: str = "", *,
                 before: Optional[DocumentIR] = None,
                 document_id: int = 0) -> QualityScore:
        """质量评分：AI 打分与本地规则各占一半（AI 失败就纯本地，绝不因此中断）。"""
        baseline = quality.heuristic_score(ir, goal, before=before)
        if not self.available:
            return baseline
        messages = [
            {"role": "system", "content": EVALUATE_SYSTEM},
            {"role": "user", "content": f"目标：{goal or '（未指定）'}\n\n文档：\n{ir.text()[:6000]}"},
        ]
        try:
            reply = self._chat(messages, role="evaluate", document_id=document_id)
        except AiUnavailable:
            return baseline
        scores = parse_score_reply(reply)
        if not scores:
            baseline.notes.append("AI 评分解析失败，已回退本地评分")
            return baseline
        blended = QualityScore(
            format_consistency=_blend(scores.get("format_consistency"), baseline.format_consistency),
            language_quality=_blend(scores.get("language_quality"), baseline.language_quality),
            readability=_blend(scores.get("readability"), baseline.readability),
            factual_consistency=_blend(scores.get("factual_consistency"), baseline.factual_consistency),
            goal_match=_blend(scores.get("goal_match"), baseline.goal_match),
            notes=list(baseline.notes) + [f"AI 评分：{reply.strip()[:120]}"],
            source="ai",
        )
        return blended

    # ---------- 工具调用循环 ----------

    def run_agent(self, goal: str, context: ToolContext, *, max_steps: int = 4,
                  tool_names: Sequence[str] | None = None,
                  on_step: Optional[Callable[[ToolCallRecord], None]] = None,
                  round_index: int = 0) -> AgentResult:
        """让模型按"JSON 工具协议"多步调用工具，直到给出 final。"""
        if not self.available:
            return AgentResult(False, "需要先配置大模型才能让 AI 自主调用工具")
        allowed = [name for name in (tool_names or list(TOOL_REGISTRY)) if name in TOOL_REGISTRY]
        system = AGENT_SYSTEM.format(tools="\n".join(catalog_lines(only=allowed)))
        messages: list[dict] = [
            {"role": "system", "content": system},
            {"role": "user", "content": (
                f"目标：{goal}\n\n当前文档体检："
                f"{json.dumps(quality.analyze_document(context.ir)['issues'], ensure_ascii=False)}\n\n"
                f"文档开头：\n{context.ir.text()[:2000]}")},
        ]
        steps: list[ToolCallRecord] = []
        transcript: list[str] = []
        for step in range(1, max_steps + 1):
            try:
                reply = self._chat(messages, role="agent", document_id=context.document_id,
                                   tool_names=allowed)
            except AiUnavailable as error:
                return AgentResult(False, str(error), steps, transcript)
            transcript.append(f"模型：{reply.strip()[:200]}")
            action = parse_agent_reply(reply)
            if "final" in action:
                return AgentResult(True, str(action.get("final") or "完成"), steps, transcript)
            name = str(action.get("tool") or "").strip()
            if not name:
                return AgentResult(True, reply.strip()[:200] or "完成", steps, transcript)
            if name not in allowed:
                messages.append({"role": "assistant", "content": reply})
                messages.append({"role": "user", "content":
                                 f"工具 {name} 不存在，可用：{'、'.join(allowed[:12])}"})
                continue
            result = call_tool(name, context, action.get("args") or {}, round_index=round_index)
            steps.append(context.log[-1])
            if on_step is not None:
                on_step(context.log[-1])
            transcript.append(f"工具 {name}：{result.summary}")
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user", "content": f"工具结果：{result.summary[:400]}"})
        return AgentResult(False, f"达到最大步数（{max_steps}）仍未完成", steps, transcript)

    # ---------- 底层调用 ----------

    def _chat(self, messages: list[dict], *, role: str = "", document_id: int = 0,
              scene: str = "", tool_names: Sequence[str] = ()) -> str:
        if self._router is None:
            raise AiUnavailable("未接入大模型配置")
        try:
            profiles = self._router.usable_profiles(self.kind)
        except LlmConfigError as error:
            raise AiUnavailable(str(error)) from error
        if not profiles:
            raise AiUnavailable(
                "没有可用的文字大模型：请到「设置 → 大模型」添加并填写 API Key（本地 Ollama 也可）")

        payload = "\n".join(str(message.get("content") or "") for message in messages)
        permissions = self.current_permissions()
        reasons: list[str] = []
        for profile in profiles:
            local = security.is_local_profile(profile)
            reason = permissions.check(profile.label, is_local=local, payload=payload)
            if reason:
                reasons.append(reason)
                continue
            prepared, masked = self._prepare_messages(messages, local=local,
                                                      permissions=permissions)
            started = time.monotonic()
            client = OpenAiCompatClient(profile)
            try:
                reply = client.chat(prepared)
            except Exception as error:  # noqa: BLE001  降级到下一份配置
                duration = int((time.monotonic() - started) * 1000)
                message = str(error) or error.__class__.__name__
                self._record(AiCallRecord(
                    kind="chat", role=role or scene, model=profile.label,
                    prompt_preview=payload[:300], tools=list(tool_names), ok=False,
                    detail=message, duration_ms=duration, masked=masked))
                reasons.append(f"{profile.label}：{message}")
                continue
            duration = int((time.monotonic() - started) * 1000)
            cost = self._estimate_cost(prepared, reply)
            self._record(AiCallRecord(
                kind="chat", role=role or scene, model=profile.label,
                prompt_preview=payload[:300], output_preview=reply[:300],
                tools=list(tool_names), ok=True, duration_ms=duration,
                est_cost=cost, masked=masked))
            return reply
        raise AiUnavailable(
            "所有模型配置都不可用或被权限拦下：\n" + "\n".join(f"· {item}" for item in reasons))

    def _prepare_messages(self, messages: list[dict], *, local: bool,
                          permissions: Optional[security.Permissions] = None,
                          ) -> tuple[list[dict], int]:
        """按权限决定是否脱敏后再发送（返回处理后的消息与脱敏命中数）。"""
        permissions = permissions or self.current_permissions()
        if local or not permissions.mask_before_upload:
            return [dict(message) for message in messages], 0
        masked_total = 0
        prepared: list[dict] = []
        for message in messages:
            content = str(message.get("content") or "")
            masked, counts = security.mask_text(content, permissions.mask_rules)
            masked_total += sum(counts.values())
            prepared.append({**message, "content": masked})
        return prepared, masked_total

    def _estimate_cost(self, messages: list[dict], reply: str) -> float:
        if not self.price_per_1k:
            return 0.0
        chars = sum(len(str(message.get("content") or "")) for message in messages) + len(reply)
        return round(chars / 4 / 1000 * self.price_per_1k, 6)

    def _record(self, record: AiCallRecord) -> None:
        if self._storage is None:
            return
        try:
            self._storage.record_ai_call(record)
        except Exception:  # noqa: BLE001  记账失败不应影响业务
            pass


# ---------------------------------------------------------------- 解析与计划


def _blend(ai_value: Optional[float], baseline: float) -> float:
    if ai_value is None:
        return baseline
    return round(max(0.0, min(1.0, (float(ai_value) + baseline) / 2)), 4)


def _extract_json(text: str) -> Any:
    body = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", body, re.S)
    if fenced:
        body = fenced.group(1).strip()
    for candidate in (body, body[body.find("{"): body.rfind("}") + 1] if "{" in body else "",
                      body[body.find("["): body.rfind("]") + 1] if "[" in body else ""):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    return None


def parse_plan_reply(reply: str) -> list[str]:
    """从模型回复里取工具序列（容忍多种写法）。"""
    data = _extract_json(reply)
    steps: list[str] = []
    if isinstance(data, dict):
        raw = data.get("steps") or data.get("plan") or []
    elif isinstance(data, list):
        raw = data
    else:
        raw = re.findall(r"[a-z_]{4,}", reply or "")
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            name = str(item.get("tool") or item.get("name") or "").strip()
        else:
            name = str(item).strip()
        if name and name not in steps:
            steps.append(name)
    return steps


def parse_score_reply(reply: str) -> dict:
    data = _extract_json(reply)
    if not isinstance(data, dict):
        return {}
    scores: dict[str, float] = {}
    for key in QualityScore.WEIGHTS:
        value = data.get(key)
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        scores[key] = number / 100 if number > 1 else number
    return scores


def parse_agent_reply(reply: str) -> dict:
    """解析智能体的一步动作（`{"tool":…}` 或 `{"final":…}`）。"""
    data = _extract_json(reply)
    if isinstance(data, dict):
        if "tool" in data or "final" in data:
            return data
        if "action" in data and isinstance(data["action"], dict):
            return data["action"]
    text = reply or ""
    if "final" in text and "{" not in text:
        return {"final": text.strip()[:200]}
    return {}


def heuristic_plan(goal: str, ir: DocumentIR, *, max_steps: int = 5) -> list[str]:
    """规则计划：按体检结果选工具（完全离线可用）。"""
    analysis = quality.analyze_document(ir)
    steps: list[str] = []
    issues = " ".join(analysis["issues"])
    goal_text = goal or ""
    if "标点" in issues or "空格" in issues or "重复的标点" in issues:
        steps.append("normalize_text")
    if analysis["headings"] == 0 or "跳级" in issues or "编号" in issues:
        steps.append("set_heading_levels")
        steps.append("beautify")
    elif "样式" in issues or not ir.styles:
        steps.append("beautify")
    if analysis["table_issues"]:
        steps.append("beautify_table")
    if analysis["duplicate_paragraphs"]:
        steps.append("dedupe_paragraphs")
    if analysis["long_sentences"] or analysis["long_paragraphs"]:
        steps.append("polish" if "润色" in goal_text or not goal_text else "condense")
    if any(word in goal_text for word in ("目录", "提纲")):
        steps.append("build_toc")
    if any(word in goal_text for word in ("摘要", "总结", "要点")):
        steps.append("summarize")
    if any(word in goal_text for word in ("翻译", "英文", "中文")):
        steps.append("translate")
    if any(word in goal_text for word in ("表格", "统计", "汇总")):
        steps.append("statistics")
    if not steps:
        steps = ["normalize_text", "beautify"]
    # 去重并保持顺序（工具不存在时由调用方过滤）
    ordered: list[str] = []
    for name in steps:
        if name in TOOL_REGISTRY and name not in ordered:
            ordered.append(name)
    return ordered[:max_steps]


def capability_text() -> str:
    """能力说明（设置页/板块页头）。"""
    return (
        "AI 能力复用「设置 → 大模型」里的文字模型（同类型可配多份，失败自动降级）："
        f"支持 {len(SCENES)} 个场景（" + "、".join(spec.label for spec in SCENES.values())
        + f"），可调用 {len(TOOL_REGISTRY)} 个文档工具（读取/解析/写入/替换/排版/计算/"
        "表格/OCR/翻译/导出/合并/拆分/对比/摘要）；默认不上传云端、可自动脱敏。"
    )


def scene_matrix() -> list[dict[str, str]]:
    return [
        {"key": spec.key, "label": spec.label, "instruction": spec.instruction.replace(
            "{extra}", "（按用户要求）")}
        for spec in SCENES.values()
    ]


__all__ = [
    "AGENT_SYSTEM",
    "AgentResult",
    "AiUnavailable",
    "DocumentAi",
    "EVALUATE_SYSTEM",
    "PLAN_SYSTEM",
    "SCENES",
    "SCENE_KEYS",
    "SCENE_LABELS",
    "SceneSpec",
    "build_messages",
    "capability_text",
    "heuristic_plan",
    "local_polish",
    "local_transform",
    "parse_agent_reply",
    "parse_plan_reply",
    "parse_score_reply",
    "scene_choices",
    "scene_matrix",
]
