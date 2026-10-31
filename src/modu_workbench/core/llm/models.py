"""大模型能力中心：多类型、多配置、按优先级调用、异常自动降级。

设计目标（对应需求）：
- **按类型划分**：文字 / 图片（多模态理解）/ 视频，后续可继续加类型；
- **同类型可配多个**：每个配置有优先级（数字越小越先用）与启用开关；
- **异常降级**：调用时按优先级依次尝试，某个配置报错就自动换下一个，
  全部失败才抛错，并把每个配置的失败原因一并带出来（便于用户定位）。

与 `core/gallery/ai.py` 的关系：那里是"单配置的 DeepSeek 客户端"（文本/多模态的
具体提示词逻辑），这里负责**选哪个配置、按什么顺序试**。图库的 AI 功能统一走这里。
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from modu_workbench.core.platform.db import SqliteStore

# --------------------------------------------------------------------------- 类型

KIND_TEXT = "text"
KIND_IMAGE = "image"
KIND_VIDEO = "video"

#: 模型类型 → 中文名（顺序即界面顺序）
KIND_LABELS: dict[str, str] = {
    KIND_TEXT: "文字大模型",
    KIND_IMAGE: "图片大模型",
    KIND_VIDEO: "视频大模型",
}

KIND_DESCRIPTIONS: dict[str, str] = {
    KIND_TEXT: "负责对话、摘要、关键词、参数建议等纯文本任务（图库的自然语言检索、修图参数就在用）。",
    KIND_IMAGE: "负责「看懂」图片：生成描述与标签；需要模型本身支持图片输入。",
    KIND_VIDEO: "预留给视频理解类任务（摘要/字幕/精彩片段）；配置好并测试通过后即可被后续功能调用。",
}

KIND_ROLE_HINTS: dict[str, str] = {
    KIND_TEXT: "文本模型",
    KIND_IMAGE: "多模态模型",
    KIND_VIDEO: "视频理解模型",
}


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind or "未分类")


# --------------------------------------------------------------------------- 服务商预设

@dataclass(frozen=True)
class ProviderPreset:
    key: str
    label: str
    base_url: str
    text_model: str
    image_model: str
    note: str = ""


PROVIDER_PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset("deepseek", "DeepSeek", "https://api.deepseek.com/v1",
                   "deepseek-chat", "deepseek-vl",
                   "文本能力强、价格低；多模态模型请按账号可用情况填写"),
    ProviderPreset("openai", "OpenAI", "https://api.openai.com/v1",
                   "gpt-4o-mini", "gpt-4o", "需要能直连 api.openai.com"),
    ProviderPreset("dashscope", "通义千问（DashScope 兼容模式）",
                   "https://dashscope.aliyuncs.com/compatible-mode/v1",
                   "qwen-plus", "qwen-vl-plus", "阿里云百炼，OpenAI 兼容接口"),
    ProviderPreset("zhipu", "智谱 GLM", "https://open.bigmodel.cn/api/paas/v4",
                   "glm-4-flash", "glm-4v", "glm-4-flash 有免费额度"),
    ProviderPreset("moonshot", "月之暗面 Kimi", "https://api.moonshot.cn/v1",
                   "moonshot-v1-8k", "moonshot-v1-8k-vision-preview", "长文本见长"),
    ProviderPreset("siliconflow", "硅基流动 SiliconFlow",
                   "https://api.siliconflow.cn/v1",
                   "Qwen/Qwen2.5-7B-Instruct", "Qwen/Qwen2-VL-7B-Instruct",
                   "聚合多家开源模型"),
    ProviderPreset("ollama", "本地 Ollama", "http://127.0.0.1:11434/v1",
                   "qwen2.5:7b", "llava", "完全离线：先 ollama serve 再选它"),
    ProviderPreset("custom", "自定义（任意 OpenAI 兼容接口）", "", "", "",
                   "填自己的 base_url / 模型名，接口需兼容 /chat/completions"),
)

PROVIDER_KEYS = tuple(preset.key for preset in PROVIDER_PRESETS)


def provider_preset(key: str) -> ProviderPreset:
    for preset in PROVIDER_PRESETS:
        if preset.key == key:
            return preset
    return PROVIDER_PRESETS[-1]


def provider_label(key: str) -> str:
    return provider_preset(key).label


# --------------------------------------------------------------------------- 配置模型


@dataclass
class ModelProfile:
    """一份模型配置（同类型可以有多份，按 priority 依次调用）。"""

    id: int = 0
    kind: str = KIND_TEXT
    name: str = ""
    provider: str = "deepseek"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    vision_model: str = ""
    temperature: float = 0.3
    max_tokens: int = 1024
    timeout: float = 60.0
    priority: int = 0
    enabled: bool = True
    note: str = ""

    @property
    def label(self) -> str:
        return self.name.strip() or f"{provider_label(self.provider)} · {self.model or '未填模型'}"

    @property
    def configured(self) -> bool:
        return bool(self.api_key.strip() and self.model.strip())

    @property
    def endpoint(self) -> str:
        base = (self.base_url or provider_preset(self.provider).base_url).rstrip("/")
        return f"{base}/chat/completions"

    @property
    def effective_model(self) -> str:
        return self.model.strip()

    def to_row(self) -> dict:
        data = asdict(self)
        data["enabled"] = 1 if self.enabled else 0
        return data

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ModelProfile":
        return cls(
            id=int(row["id"]),
            kind=str(row["kind"]),
            name=str(row["name"] or ""),
            provider=str(row["provider"] or "deepseek"),
            base_url=str(row["base_url"] or ""),
            api_key=str(row["api_key"] or ""),
            model=str(row["model"] or ""),
            vision_model=str(row["vision_model"] or ""),
            temperature=float(row["temperature"] if row["temperature"] is not None else 0.3),
            max_tokens=int(row["max_tokens"] or 1024),
            timeout=float(row["timeout"] if row["timeout"] is not None else 60.0),
            priority=int(row["priority"] or 0),
            enabled=bool(row["enabled"]),
            note=str(row["note"] or ""),
        )


class LlmError(RuntimeError):
    """大模型调用的统一异常基类。"""


class LlmConfigError(LlmError):
    """没有任何可用配置（没填 Key / 没启用）。"""


class LlmRequestError(LlmError):
    """所有同类型配置都调用失败（消息里带每次失败的原因）。"""


# --------------------------------------------------------------------------- 存储

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_profiles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL DEFAULT 'text',
    name        TEXT NOT NULL DEFAULT '',
    provider    TEXT NOT NULL DEFAULT 'deepseek',
    base_url    TEXT NOT NULL DEFAULT '',
    api_key     TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT '',
    vision_model TEXT NOT NULL DEFAULT '',
    temperature REAL NOT NULL DEFAULT 0.3,
    max_tokens  INTEGER NOT NULL DEFAULT 1024,
    timeout     REAL NOT NULL DEFAULT 60.0,
    priority    INTEGER NOT NULL DEFAULT 0,
    enabled     INTEGER NOT NULL DEFAULT 1,
    note        TEXT NOT NULL DEFAULT '',
    updated_at  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS llm_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL DEFAULT '',
    profile_id INTEGER NOT NULL DEFAULT 0,
    profile    TEXT NOT NULL DEFAULT '',
    ok         INTEGER NOT NULL DEFAULT 0,
    detail     TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL DEFAULT 0
);
"""


class LlmStorage(SqliteStore):
    """大模型配置的持久化（落在共享单库的 llm_* 表；设置走 app_settings 的 llm/ 命名空间）。"""

    settings_namespace = "llm"

    def __init__(self, db_path: str | Path):
        super().__init__(db_path)

    # ---------- 配置增删改查 ----------

    def list_profiles(self, kind: str = "", *, enabled_only: bool = False) -> list[ModelProfile]:
        sql = "SELECT * FROM llm_profiles"
        clauses: list[str] = []
        params: list = []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if enabled_only:
            clauses.append("enabled = 1")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY priority ASC, id ASC"
        rows = self._conn.execute(sql, params).fetchall()
        return [ModelProfile.from_row(row) for row in rows]

    def get_profile(self, profile_id: int) -> Optional[ModelProfile]:
        row = self._conn.execute(
            "SELECT * FROM llm_profiles WHERE id = ?", (int(profile_id),)).fetchone()
        return ModelProfile.from_row(row) if row else None

    def save_profile(self, profile: ModelProfile) -> int:
        """新增或更新；新增时自动排到该类型末尾。"""
        if profile.id:
            self._conn.execute(
                """UPDATE llm_profiles SET kind=?, name=?, provider=?, base_url=?, api_key=?,
                       model=?, vision_model=?, temperature=?, max_tokens=?, timeout=?,
                       enabled=?, note=?, updated_at=?
                   WHERE id=?""",
                (profile.kind, profile.name, profile.provider, profile.base_url,
                 profile.api_key, profile.model, profile.vision_model, profile.temperature,
                 profile.max_tokens, profile.timeout, 1 if profile.enabled else 0,
                 profile.note, int(time.time()), int(profile.id)),
            )
            self._conn.commit()
            return int(profile.id)
        priority = self._conn.execute(
            "SELECT COALESCE(MAX(priority), -1) + 1 FROM llm_profiles WHERE kind = ?",
            (profile.kind,)).fetchone()[0]
        cursor = self._conn.execute(
            """INSERT INTO llm_profiles
                   (kind, name, provider, base_url, api_key, model, vision_model,
                    temperature, max_tokens, timeout, priority, enabled, note, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (profile.kind, profile.name, profile.provider, profile.base_url, profile.api_key,
             profile.model, profile.vision_model, profile.temperature, profile.max_tokens,
             profile.timeout, int(priority), 1 if profile.enabled else 0, profile.note,
             int(time.time())),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def delete_profile(self, profile_id: int) -> None:
        self._conn.execute("DELETE FROM llm_profiles WHERE id = ?", (int(profile_id),))
        self._conn.commit()

    def set_enabled(self, profile_id: int, enabled: bool) -> None:
        self._conn.execute("UPDATE llm_profiles SET enabled = ? WHERE id = ?",
                           (1 if enabled else 0, int(profile_id)))
        self._conn.commit()

    def move_profile(self, profile_id: int, delta: int) -> bool:
        """在**同类型内**上移/下移一位（交换优先级），返回是否真的动了。"""
        profile = self.get_profile(profile_id)
        if profile is None:
            return False
        siblings = self.list_profiles(profile.kind)
        ids = [item.id for item in siblings]
        if profile.id not in ids:
            return False
        index = ids.index(profile.id)
        target = index + int(delta)
        if target < 0 or target >= len(siblings):
            return False
        # 优先级字段可能重复（手工改过库），这里按顺序重排一遍再交换，保证结果稳定
        for position, item in enumerate(siblings):
            self._conn.execute("UPDATE llm_profiles SET priority = ? WHERE id = ?",
                               (position, item.id))
        first, second = siblings[index], siblings[target]
        self._conn.execute("UPDATE llm_profiles SET priority = ? WHERE id = ?",
                           (target, first.id))
        self._conn.execute("UPDATE llm_profiles SET priority = ? WHERE id = ?",
                           (index, second.id))
        self._conn.commit()
        return True

    def next_priority(self, kind: str) -> int:
        return int(self._conn.execute(
            "SELECT COALESCE(MAX(priority), -1) + 1 FROM llm_profiles WHERE kind = ?",
            (kind,)).fetchone()[0])

    # ---------- 通用设置 ----------
    # get_setting / set_setting 继承自 SqliteStore：落到共享 app_settings，
    # 键自动加 llm/ 命名空间（原 llm_settings 表在 v1.0.0 统一时并入）。

    # ---------- 调用记录（用于界面展示"最近降级到谁"）----------

    def record_call(self, kind: str, profile: ModelProfile, ok: bool, detail: str = "") -> None:
        self._conn.execute(
            "INSERT INTO llm_calls (kind, profile_id, profile, ok, detail, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (kind, int(profile.id), profile.label, 1 if ok else 0, detail[:400], int(time.time())))
        self._conn.commit()

    def recent_calls(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM llm_calls ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(row) for row in rows]

    def clear_calls(self) -> None:
        self._conn.execute("DELETE FROM llm_calls")
        self._conn.commit()

    # ---------- 从旧版图库配置迁移 ----------

    def migrate_legacy(self, *, api_key: str, base_url: str = "", model: str = "",
                       vision_model: str = "", temperature: float = 0.3,
                       max_tokens: int = 1024) -> bool:
        """把旧版「图库 → DeepSeek」里的配置搬进来（只在完全没有配置时做一次）。"""
        if not api_key.strip():
            return False
        if self.has_migrated() or self.list_profiles():
            return False
        self.save_profile(ModelProfile(
            kind=KIND_TEXT, name="DeepSeek（从图库设置迁移）", provider="deepseek",
            base_url=base_url, api_key=api_key, model=model or "deepseek-chat",
            vision_model=vision_model, temperature=temperature, max_tokens=max_tokens,
            note="由旧版图库设置自动迁移",
        ))
        if vision_model.strip():
            self.save_profile(ModelProfile(
                kind=KIND_IMAGE, name=f"DeepSeek 多模态（{vision_model}）", provider="deepseek",
                base_url=base_url, api_key=api_key, model=vision_model,
                temperature=temperature, max_tokens=max_tokens,
                note="由旧版图库设置自动迁移；需模型确实支持图片输入",
            ))
        self.set_setting("legacy_migrated", "1")
        return True

    def has_migrated(self) -> bool:
        return self.get_setting("legacy_migrated", "0") == "1"


__all__ = [
    "KIND_DESCRIPTIONS",
    "KIND_IMAGE",
    "KIND_LABELS",
    "KIND_ROLE_HINTS",
    "KIND_TEXT",
    "KIND_VIDEO",
    "LlmConfigError",
    "LlmError",
    "LlmRequestError",
    "LlmStorage",
    "ModelProfile",
    "PROVIDER_KEYS",
    "PROVIDER_PRESETS",
    "ProviderPreset",
    "kind_label",
    "provider_label",
    "provider_preset",
]
