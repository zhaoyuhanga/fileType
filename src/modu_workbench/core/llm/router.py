"""OpenAI 兼容客户端 + 按优先级降级的路由器。

- `OpenAiCompatClient`：一份配置上的最小对话客户端（`POST {base}/chat/completions`）；
- `ModelRouter`：同一类型下按优先级依次尝试，**任何一次失败都自动降到下一份配置**，
  全部失败才抛 `LlmRequestError`，异常消息里列出每一份配置的失败原因。
"""
from __future__ import annotations

from typing import Callable, Iterable, Optional

from .models import (
    KIND_IMAGE,
    KIND_LABELS,
    KIND_TEXT,
    LlmConfigError,
    LlmRequestError,
    LlmStorage,
    ModelProfile,
    kind_label,
)


def describe_http_error(profile: ModelProfile, error: Exception) -> str:
    """把底层异常翻译成用户能直接照做的中文提示。"""
    text = str(error)
    lowered = text.lower()
    vendor = profile.label
    if "401" in lowered or "unauthorized" in lowered or "invalid api key" in lowered:
        return f"{vendor}：鉴权失败（401），请检查 API Key"
    if "402" in lowered or "insufficient" in lowered or "balance" in lowered:
        return f"{vendor}：账户余额不足（402）"
    if "403" in lowered:
        return f"{vendor}：无权限（403），可能是模型未开通"
    if "404" in lowered:
        return f"{vendor}：接口或模型不存在（404），请检查接口地址与模型名"
    if "429" in lowered or "rate limit" in lowered:
        return f"{vendor}：触发限流（429），稍后重试"
    if "timeout" in lowered or "timed out" in lowered:
        return f"{vendor}：连接超时，请检查网络/代理"
    if "getaddrinfo" in lowered or "name or service" in lowered or "nodename" in lowered:
        return f"{vendor}：域名解析失败，请检查接口地址与 DNS"
    if "connection" in lowered and ("refused" in lowered or "reset" in lowered):
        return f"{vendor}：连接被拒绝（本地服务未启动？）"
    return f"{vendor}：请求失败 {text[:160]}"


class OpenAiCompatClient:
    """一份模型配置上的同步客户端（请在后台线程调用）。"""

    def __init__(self, profile: ModelProfile, *, http=None):
        self.profile = profile
        self._http = http

    # ---------- 基础 ----------

    def _client(self):  # noqa: ANN202
        if self._http is not None:
            return self._http
        from modu_workbench.core.platform.http import HttpClient

        return HttpClient(timeout=float(self.profile.timeout or 60.0), attempts=2)

    def require_ready(self) -> None:
        if not self.profile.api_key.strip():
            raise LlmConfigError(f"{self.profile.label}：还没有填写 API Key")
        if not self.profile.model.strip():
            raise LlmConfigError(f"{self.profile.label}：还没有填写模型名")

    def chat(self, messages: list[dict], *, model: str = "") -> str:
        """发一轮对话，返回助手文本。"""
        self.require_ready()
        payload = {
            "model": model or self.profile.effective_model,
            "messages": messages,
            "max_tokens": int(self.profile.max_tokens or 1024),
            "temperature": float(self.profile.temperature),
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.profile.api_key.strip()}",
            "Content-Type": "application/json",
        }
        try:
            response = self._client().post(self.profile.endpoint, json=payload, headers=headers)
        except Exception as error:  # noqa: BLE001
            raise LlmRequestError(describe_http_error(self.profile, error)) from error
        try:
            data = response.json()
        except ValueError as error:
            raise LlmRequestError(
                f"{self.profile.label}：返回的不是有效 JSON（可能被网络中间设备拦截）") from error
        if isinstance(data, dict) and data.get("error"):
            detail = data["error"]
            if isinstance(detail, dict):
                detail = detail.get("message") or detail
            raise LlmRequestError(f"{self.profile.label}：接口返回错误：{str(detail)[:200]}")
        try:
            return str(data["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, TypeError) as error:
            raise LlmRequestError(
                f"{self.profile.label}：返回结构异常 {str(data)[:160]}") from error

    def test_connection(self) -> str:
        """连通性自检（返回模型的简短回话）。"""
        return self.chat([{"role": "user", "content": "回复两个字：可用"}])


class ModelRouter:
    """按类型选配置：优先级升序依次尝试，失败自动降级。"""

    def __init__(self, storage: LlmStorage):
        self._storage = storage

    # ---------- 配置查询 ----------

    @property
    def storage(self) -> LlmStorage:
        return self._storage

    def profiles(self, kind: str, *, enabled_only: bool = True) -> list[ModelProfile]:
        return self._storage.list_profiles(kind, enabled_only=enabled_only)

    def usable_profiles(self, kind: str) -> list[ModelProfile]:
        """既启用又填好 Key/模型的配置（真正可以调用的候选）。"""
        return [p for p in self.profiles(kind) if p.configured]

    def available_kinds(self) -> list[str]:
        return [kind for kind in KIND_LABELS if self.usable_profiles(kind)]

    def has_any(self, kind: str = "") -> bool:
        if kind:
            return bool(self.usable_profiles(kind))
        return bool(self.available_kinds())

    def describe(self, kind: str) -> str:
        """给界面用的一句话：当前会按什么顺序调用。"""
        usable = self.usable_profiles(kind)
        if not usable:
            return f"尚未配置可用的{kind_label(kind)}（设置 → 大模型）"
        names = " → ".join(profile.label for profile in usable)
        return f"调用顺序（{len(usable)} 个）：{names}"

    # ---------- 调用 ----------

    def run(self, kind: str, task: Callable[[ModelProfile], object], *,
            on_attempt: Optional[Callable[[ModelProfile, int, int], None]] = None,
            record: bool = True):
        """按优先级把 `task(profile)` 跑一遍，失败自动降级到下一份配置。

        任何"调用类"任务都能复用这套降级逻辑（对话、看图、关键词提取…），
        返回 `(task 的返回值, 实际使用的配置)`；全部失败抛 `LlmRequestError`，
        消息里按顺序列出每一份配置的失败原因。
        """
        candidates = self.usable_profiles(kind)
        if not candidates:
            raise LlmConfigError(
                f"没有可用的{kind_label(kind)}。请在「设置 → 大模型」里添加并填好 API Key，"
                "或检查是否被停用。"
            )
        errors: list[str] = []
        total = len(candidates)
        for position, profile in enumerate(candidates, start=1):
            if on_attempt is not None:
                on_attempt(profile, position, total)
            try:
                value = task(profile)
            except Exception as error:  # noqa: BLE001  降级到下一个候选
                message = str(error) or error.__class__.__name__
                # 带上配置名，用户才知道是哪一份挂了（客户端消息里通常已含名字，避免重复）
                if profile.label and profile.label not in message:
                    message = f"{profile.label}：{message}"
                errors.append(message)
                if record:
                    self._storage.record_call(kind, profile, False, message)
                continue
            if record:
                self._storage.record_call(kind, profile, True, "OK")
            return value, profile
        raise LlmRequestError(
            f"{kind_label(kind)}全部 {total} 个配置都调用失败：\n" + "\n".join(
                f"{index}. {message}" for index, message in enumerate(errors, start=1))
        )

    def chat(self, kind: str, messages: list[dict], *, model: str = "",
             on_attempt: Optional[Callable[[ModelProfile, int, int], None]] = None,
             record: bool = True) -> tuple[str, ModelProfile]:
        """按优先级发一轮对话；返回 (回复文本, 实际使用的配置)。

        失败会**自动降级**到下一份配置；全部失败抛 `LlmRequestError`；
        一份都没配则抛 `LlmConfigError`。
        """
        return self.run(
            kind,
            lambda profile: OpenAiCompatClient(profile).chat(messages, model=model),
            on_attempt=on_attempt, record=record,
        )

    def test_profile(self, profile: ModelProfile) -> str:
        """测试单个配置（不做降级，直接把真实原因抛出来）。"""
        return OpenAiCompatClient(profile).test_connection()

    def chat_with_fallback(self, kinds: Iterable[str], messages: list[dict]) -> tuple[str, str, str]:
        """跨类型降级：按给定类型顺序尝试（例如 图片 → 文字）。

        返回 (回复文本, 类型, 配置名)。
        """
        problems: list[str] = []
        for kind in kinds:
            try:
                text, profile = self.chat(kind, messages)
            except Exception as error:  # noqa: BLE001
                problems.append(str(error))
                continue
            return text, kind, profile.label
        raise LlmRequestError("；".join(problems) if problems else "没有可用的大模型配置")


__all__ = [
    "KIND_IMAGE",
    "KIND_TEXT",
    "ModelRouter",
    "OpenAiCompatClient",
    "describe_http_error",
]
