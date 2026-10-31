"""墨软文档：AI 循环美化引擎（MR-DOC-305）。

流程（对应 PRD 的 8 个步骤，每步都落到真实实现）：

1. 目标：用户输入"美化这份报告 / 统一为公文风格 / 优化表格并生成摘要"（`LoopOptions.goal`）；
2. 文档分析：`quality.analyze_document` 产出结构与语言问题；
3. 生成计划：`DocumentAi.plan`（AI 可用时由模型选工具，否则按体检结果走规则计划）；
4. 工具调用：`tools.call_tool` 真正改写文档（排版/替换/计算/翻译/摘要/合并/导出…）；
5. 质量评估：`DocumentAi.evaluate`（AI 打分 + 本地规则各半，AI 不可用时纯本地）；
6. 循环优化：未达标继续下一轮；
7. 停止条件：达阈值 / 达最大轮次 / 无可改进项 / 用户停止 / 成本或时间超限 / 出错；
8. 输出：最终文档 + 修改日志 + 每轮 diff + 版本快照（可回滚）。

引擎本身不碰界面：进度通过 `on_event` 回调抛给界面，因此可以无头测试。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from . import quality
from .ai import AiUnavailable, DocumentAi, heuristic_plan
from .models import (
    STOP_COST,
    STOP_MAX_ROUNDS,
    STOP_NO_CHANGE,
    STOP_THRESHOLD,
    STOP_TIME,
    STOP_USER,
    DocumentIR,
    LoopOptions,
    LoopResult,
    QualityScore,
    RoundReport,
)
from .tools import TOOL_REGISTRY, ToolContext, call_tool


@dataclass
class LoopEvent:
    """循环过程中的事件（界面据此刷新进度、日志与每轮卡片）。"""

    kind: str          # round_start / plan / tool / score / round_end / stop / error
    round: int = 0
    message: str = ""
    data: dict = field(default_factory=dict)


#: 计划里的工具 → 默认参数（AI 只给工具名时也能跑）
def default_tool_args(tool: str, options: LoopOptions) -> dict:
    if tool == "beautify":
        return {"template": options.template}
    if tool in ("polish", "proofread", "expand"):
        return {"requirement": options.goal}
    if tool == "condense":
        return {"ratio": "压缩到原文的 60% 左右"}
    if tool == "summarize":
        return {"length": "5 条要点"}
    if tool == "translate":
        target = "英文" if "英" in options.goal else ("中文" if "中" in options.goal else "英文")
        return {"target_language": target}
    if tool == "rewrite":
        return {"style": options.scene or "正式书面"}
    if tool == "build_toc":
        return {"depth": 3}
    if tool == "quality_check":
        return {"goal": options.goal, "use_ai": options.use_ai}
    return {}


class BeautifyLoop:
    """循环美化引擎。"""

    def __init__(self, ai: Optional[DocumentAi] = None, storage: Any = None, *,
                 library: Any = None, output_dir: str = ""):
        self._ai = ai
        self._storage = storage
        self._library = library
        self._output_dir = output_dir
        self._snapshots: dict[int, DocumentIR] = {}

    # ---------- 主流程 ----------

    def run(self, ir: DocumentIR, options: LoopOptions, *,
            on_event: Optional[Callable[[LoopEvent], None]] = None,
            should_stop: Optional[Callable[[], bool]] = None,
            confirm: Optional[Callable[[RoundReport], bool]] = None,
            document_id: int = 0) -> LoopResult:
        """跑循环美化；返回最终文档、每轮报告与停止原因。"""
        goal = (options.goal or "把这份文档整理得更规范").strip()
        working = ir.clone()
        log: list[str] = []
        rounds: list[RoundReport] = []
        self._snapshots = {}

        def emit(kind: str, message: str, round_index: int = 0, **data: Any) -> None:
            log.append(message)
            if on_event is not None:
                on_event(LoopEvent(kind, round_index, message, data))

        started = time.monotonic()
        before_score = self._score(working, goal, options, before=None, document_id=document_id)
        emit("start", f"开始循环美化：{goal}（起始质量 {before_score.overall * 100:.0f}）",
             data={"score": before_score.to_dict()})

        stopped = STOP_MAX_ROUNDS
        previous_ir = working.clone()
        previous_score: Optional[QualityScore] = None
        total_cost = 0.0
        max_rounds = max(1, int(options.max_rounds or 1))

        for index in range(1, max_rounds + 1):
            if should_stop is not None and should_stop():
                stopped = STOP_USER
                emit("stop", f"第 {index} 轮前收到停止请求", index)
                break
            if options.max_seconds and (time.monotonic() - started) > options.max_seconds:
                stopped = STOP_TIME
                emit("stop", f"已达时间上限（{options.max_seconds:g} 秒）", index)
                break
            if options.max_cost and total_cost >= options.max_cost:
                stopped = STOP_COST
                emit("stop", f"已达成本上限（{total_cost:.4f}）", index)
                break

            emit("round_start", f"第 {index} 轮开始", index)
            round_started = time.monotonic()
            context = ToolContext(
                ir=working.clone(), library=self._library, ai=self._ai if options.use_ai else None,
                output_dir=self._output_dir, document_id=document_id)
            context.extras["changes"] = []

            plan = self._plan(goal, working, options, index, document_id)
            emit("plan", f"第 {index} 轮计划：" + "、".join(plan) if plan else f"第 {index} 轮没有可执行的改进项",
                 index, plan=plan)

            executed: list[str] = []
            for tool in plan:
                spec = TOOL_REGISTRY.get(tool)
                if spec is None:
                    continue
                if spec.needs_ai and not (options.use_ai and self._ai is not None and self._ai.available):
                    emit("tool", f"跳过「{spec.label}」：未配置大模型", index, tool=tool, ok=False)
                    continue
                result = call_tool(tool, context, default_tool_args(tool, options),
                                   round_index=index)
                executed.append(tool)
                emit("tool", f"「{spec.label}」：{result.summary}", index,
                     tool=tool, ok=result.ok, summary=result.summary)

            working = context.ir
            changes = context.extras.get("changes", [])
            if options.mark_ai:
                for change in changes:
                    if change.kind in ("标点规范",):
                        change.ai_generated = False
            score = self._score(working, goal, options, before=previous_ir,
                               document_id=document_id)
            diff_lines = quality.diff_irs(previous_ir, working)
            ratio = quality.changed_ratio(previous_ir, working)
            duration_ms = int((time.monotonic() - round_started) * 1000)
            round_cost = self._round_cost(document_id)
            total_cost += round_cost

            version_id = 0
            if options.snapshot and self._storage is not None:
                try:
                    version_id = self._storage.save_version(
                        working, document_id=document_id,
                        label=f"循环美化 第{index}轮", kind="loop", round_index=index,
                        note=quality.summarize_diff(diff_lines))
                except Exception:  # noqa: BLE001  快照失败不影响本轮结果
                    version_id = 0
            self._snapshots[index] = working.clone()

            report = RoundReport(
                index=index, plan=plan,
                tool_calls=list(context.log), changes=list(changes), score=score,
                previous_score=previous_score, diff=diff_lines,
                summary=(f"{len(changes)} 处修改 · " + quality.summarize_diff(diff_lines)),
                duration_ms=duration_ms, cost=round_cost, version_id=version_id,
                ai_used=bool(options.use_ai and self._ai is not None and self._ai.available))
            rounds.append(report)
            emit("round_end",
                 f"第 {index} 轮完成：{report.summary}；{score.summary()}",
                 index, report=report.to_dict())
            log.extend(context.notes)

            if confirm is not None and not confirm(report):
                stopped = STOP_USER
                emit("stop", f"第 {index} 轮后用户选择停止", index)
                break

            if score.overall >= options.quality_threshold:
                stopped = STOP_THRESHOLD
                emit("stop", f"已达到目标质量（{score.overall * 100:.0f} ≥ "
                             f"{options.quality_threshold * 100:.0f}）", index)
                break
            if options.stop_on_no_change and (not executed or ratio <= 0.001):
                stopped = STOP_NO_CHANGE
                emit("stop", f"第 {index} 轮没有产生可改进的变化", index)
                break
            if index == max_rounds:
                stopped = STOP_MAX_ROUNDS
                emit("stop", f"已达最大轮次（{max_rounds}）", index)
                break

            previous_ir = working.clone()
            previous_score = score

        after_score = self._score(working, goal, options, before=ir, document_id=document_id)
        result = LoopResult(ir=working, rounds=rounds, stopped_reason=stopped,
                            before_score=before_score, after_score=after_score, log=log)
        emit("done", result.summary(), len(rounds),
             result={"rounds": len(rounds), "stop": stopped})
        return result

    # ---------- 回滚 ----------

    def rollback(self, result: LoopResult, round_index: int) -> Optional[DocumentIR]:
        """回到第 `round_index` 轮的快照（0 = 原始文档）。"""
        if round_index <= 0:
            return None
        if round_index in self._snapshots:
            return self._snapshots[round_index].clone()
        for report in result.rounds:
            if report.index == round_index and report.version_id and self._storage is not None:
                snapshot = self._storage.load_version_ir(report.version_id)
                if snapshot is not None:
                    return snapshot
        return None

    def round_snapshot(self, round_index: int) -> Optional[DocumentIR]:
        snapshot = self._snapshots.get(round_index)
        return snapshot.clone() if snapshot is not None else None

    # ---------- 内部 ----------

    def _plan(self, goal: str, ir: DocumentIR, options: LoopOptions, index: int,
              document_id: int) -> list[str]:
        allowed = [name for name in (options.allow_tools or ()) if name in TOOL_REGISTRY]
        if index > 1:
            # 第二轮起：优先补上"还没达标"的短板（语言/可读性靠 AI 场景工具）
            plan = self._followup_plan(ir, goal, options)
        elif options.use_ai and self._ai is not None and self._ai.available:
            try:
                plan = self._ai.plan(goal, ir, max_steps=5, document_id=document_id)
            except AiUnavailable:
                plan = heuristic_plan(goal, ir, max_steps=5)
        else:
            plan = heuristic_plan(goal, ir, max_steps=5)
        if allowed:
            plan = [name for name in plan if name in allowed]
        return plan

    @staticmethod
    def _followup_plan(ir: DocumentIR, goal: str, options: LoopOptions) -> list[str]:
        analysis = quality.analyze_document(ir)
        plan: list[str] = []
        if analysis["duplicate_paragraphs"]:
            plan.append("dedupe_paragraphs")
        if analysis["table_issues"]:
            plan.append("beautify_table")
        if analysis["long_sentences"] or analysis["punctuation_issues"]:
            plan.append("condense" if "缩写" in goal else "polish")
        if not plan:
            plan.append("normalize_text")
        if options.allow_tools:
            plan = [name for name in plan if name in options.allow_tools]
        return plan

    def _score(self, ir: DocumentIR, goal: str, options: LoopOptions, *,
               before: Optional[DocumentIR], document_id: int) -> QualityScore:
        if options.use_ai and self._ai is not None and self._ai.available:
            try:
                return self._ai.evaluate(ir, goal, before=before, document_id=document_id)
            except AiUnavailable:
                pass
        return quality.heuristic_score(ir, goal, before=before)

    def _round_cost(self, document_id: int) -> float:
        if self._storage is None:
            return 0.0
        try:
            usage = self._storage.ai_usage()
        except Exception:  # noqa: BLE001
            return 0.0
        previous = getattr(self, "_last_cost", 0.0)
        self._last_cost = float(usage.get("cost") or 0.0)
        return round(max(0.0, self._last_cost - previous), 6)


# ---------------------------------------------------------------- 便捷入口


def run_loop(ir: DocumentIR, options: LoopOptions, *, ai: Optional[DocumentAi] = None,
             storage: Any = None, library: Any = None, output_dir: str = "",
             on_event: Optional[Callable[[LoopEvent], None]] = None,
             should_stop: Optional[Callable[[], bool]] = None,
             confirm: Optional[Callable[[RoundReport], bool]] = None,
             document_id: int = 0) -> LoopResult:
    """一次性跑一轮循环美化（界面线程外调用）。"""
    engine = BeautifyLoop(ai, storage, library=library, output_dir=output_dir)
    return engine.run(ir, options, on_event=on_event, should_stop=should_stop,
                      confirm=confirm, document_id=document_id)


def capability_text() -> str:
    return (
        "循环美化：分析 → 计划 → 工具调用 → 五维评分 → 未达标继续下一轮；"
        "停止条件支持质量阈值、最大轮次、无可改进项、用户停止、成本与时间上限；"
        "每轮都有 diff、修改日志与版本快照，可随时回滚。"
    )


__all__ = [
    "BeautifyLoop",
    "LoopEvent",
    "capability_text",
    "default_tool_args",
    "run_loop",
]
