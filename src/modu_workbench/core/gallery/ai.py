"""DeepSeek 集成（文本能力）。

**能力边界（重要）**：DeepSeek 的对话接口只处理文本，**不能生成或编辑图片**。
因此本模块**不负责**超分、抠图、去噪、消除等像素级操作 —— 那些由
`core/gallery/enhance.py` 的本地算法完成。DeepSeek 在图库里负责的是文本类工作：

1. 依据图片生成**标题 / 描述 / 标签**（用于检索，配合用户手动修正）；
2. 依据自然语言描述**推荐修图参数**（亮度/对比度/饱和/风格等），再由本地算法执行；
3. 依据关键词**生成检索建议**（例如"找去年在海边的照片" → 关键词列表）。

接口与 OpenAI 兼容：`POST {base_url}/chat/completions`，`Authorization: Bearer <key>`。
未配置 key 时所有方法都会给出明确的"未配置"提示，不会静默失败。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

# 默认端点与模型（可在设置里改）
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_VISION_MODEL = "deepseek-vl"
# 图片发给模型前的最长边（控制 token 与上传体积）
VISION_MAX_SIDE = 1024

SETTING_PREFIX = "ai/deepseek"


class AiConfigError(RuntimeError):
    """未配置密钥或配置不合法。"""


class AiRequestError(RuntimeError):
    """请求失败（网络、鉴权、配额、返回格式）。"""


@dataclass
class AiConfig:
    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    vision_model: str = DEFAULT_VISION_MODEL
    timeout: float = 60.0
    max_tokens: int = 1024
    temperature: float = 0.3
    # 是否允许把图片发送到云端（隐私开关，默认关闭）
    allow_upload: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.api_key.strip())

    @property
    def endpoint(self) -> str:
        base = (self.base_url or DEFAULT_BASE_URL).rstrip("/")
        return f"{base}/chat/completions"


def _to_data_url(path: str | Path) -> str:
    """把本地图片压成 JPEG data URL（控制体积，避免请求过大）。"""
    with Image.open(path) as image:
        image.load()
        working = image.convert("RGB")
        working.thumbnail((VISION_MAX_SIDE, VISION_MAX_SIDE), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        working.save(buffer, format="JPEG", quality=85)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def image_digest(path: str | Path) -> str:
    """图片内容指纹（用于跳过重复的 AI 分析）。"""
    try:
        with open(path, "rb") as handle:
            return hashlib.sha1(handle.read(1 << 20)).hexdigest()
    except OSError:
        return ""


class DeepSeekClient:
    """DeepSeek 文本/多模态客户端（同步调用，请在后台线程里用）。"""

    def __init__(self, config: AiConfig, *, http=None):
        self.config = config
        self._http = http

    # ---------- 基础 ----------

    def _client(self):  # noqa: ANN202
        if self._http is not None:
            return self._http
        from modu_workbench.core.platform.http import HttpClient

        return HttpClient(timeout=self.config.timeout, attempts=2)

    def _require_key(self) -> None:
        if not self.config.configured:
            raise AiConfigError(
                "尚未配置 DeepSeek API Key。请在「设置 → 图库 → AI 优化」里填写，"
                "或设置环境变量 MODU_DEEPSEEK_KEY。"
            )

    def chat(self, messages: list[dict], *, model: str = "") -> str:
        """发一轮对话，返回助手文本。"""
        self._require_key()
        payload = {
            "model": model or self.config.model,
            "messages": messages,
            "max_tokens": int(self.config.max_tokens),
            "temperature": float(self.config.temperature),
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key.strip()}",
            "Content-Type": "application/json",
        }
        try:
            response = self._client().post(
                self.config.endpoint, json=payload, headers=headers,
            )
        except Exception as error:  # noqa: BLE001
            raise AiRequestError(self._describe(error)) from error
        try:
            data = response.json()
        except ValueError as error:
            raise AiRequestError("DeepSeek 返回的不是有效 JSON（可能被网络中间设备拦截）") from error
        if isinstance(data, dict) and data.get("error"):
            message = data["error"]
            if isinstance(message, dict):
                message = message.get("message") or message
            raise AiRequestError(f"DeepSeek 返回错误：{message}")
        try:
            return str(data["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, TypeError) as error:
            raise AiRequestError(f"DeepSeek 返回结构异常：{str(data)[:200]}") from error

    def _describe(self, error: Exception) -> str:
        text = str(error)
        lowered = text.lower()
        if "401" in lowered or "unauthorized" in lowered:
            return "DeepSeek 鉴权失败（401）：请检查 API Key 是否正确、是否已过期。"
        if "402" in lowered or "insufficient" in lowered or "balance" in lowered:
            return "DeepSeek 账户余额不足（402）：请充值后重试。"
        if "429" in lowered or "rate limit" in lowered:
            return "DeepSeek 触发限流（429）：请稍后重试或降低批量并发。"
        if "timeout" in lowered or "timed out" in lowered:
            return "连接 DeepSeek 超时：请检查网络或代理设置。"
        if "getaddrinfo" in lowered or "name or service" in lowered:
            return "无法解析 api.deepseek.com：请检查 DNS / 网络连通性。"
        return f"DeepSeek 请求失败：{text[:200]}"

    def test_connection(self) -> str:
        """连通性自检，返回模型的简短回话。"""
        return self.chat([{"role": "user", "content": "回复两个字：可用"}])

    # ---------- 文本任务 ----------

    def suggest_search_keywords(self, query: str) -> list[str]:
        """把自然语言检索意图转成关键词（用于本地筛选）。"""
        prompt = (
            "你是图库检索助手。请把用户的检索意图拆成适合文件名/标签匹配的中文关键词，"
            "只输出 JSON 数组，例如 [\"海边\",\"日落\"]，不要输出其他内容。\n"
            f"用户输入：{query}"
        )
        text = self.chat([{"role": "user", "content": prompt}])
        return _parse_str_list(text)

    def suggest_edit_params(self, description: str) -> dict:
        """把自然语言修图需求转成可执行的本地参数。"""
        prompt = (
            "你是修图参数助手。根据用户的描述，输出一个 JSON 对象，字段只能是下列之一："
            "filter(黑白/复古/暖调/冷调/胶片/褪色/鲜艳/硬黑白)、"
            "brightness/contrast/saturation(0.5~1.8)、temperature(-1~1)、"
            "enhance(一键增强/超分/去噪/去模糊/人像柔化/老照片修复)。"
            "只输出 JSON，不要解释。\n"
            f"用户描述：{description}"
        )
        text = self.chat([{"role": "user", "content": prompt}])
        return _parse_json_object(text)

    # ---------- 图片理解（多模态，需模型支持） ----------

    def analyze_image(self, path: str | Path, *, want_tags: bool = True,
                      want_caption: bool = True) -> dict:
        """让模型看图，返回 {"caption": str, "tags": [str]}。

        需要多模态模型；若所用的 DeepSeek 模型不支持图片输入，会返回明确报错，
        此时可退化为「用文件名/EXIF 生成关键词」的纯文本模式。
        """
        if not self.config.allow_upload:
            raise AiConfigError(
                "已禁止上传图片到云端。请在「设置 → 图库 → AI 优化」中开启"
                "「允许把图片发送到云端用于识别」，或改用手动打标签。"
            )
        instruction = (
            "请描述这张图片，并给出标签。只输出 JSON："
            '{"caption": "一句话描述", "tags": ["标签1", "标签2"]}。'
            "标签用中文，3~8 个，覆盖主体、场景、颜色、风格。"
        )
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": instruction},
                {"type": "image_url", "image_url": {"url": _to_data_url(path)}},
            ],
        }]
        text = self.chat(messages, model=self.config.vision_model)
        data = _parse_json_object(text)
        caption = str(data.get("caption", "")).strip() if want_caption else ""
        tags = data.get("tags") or []
        if isinstance(tags, str):
            tags = [t for t in re.split(r"[,，\s]+", tags) if t]
        tags = [str(t).strip() for t in tags if str(t).strip()] if want_tags else []
        return {"caption": caption, "tags": tags[:12]}

    def analyze_by_metadata(self, item) -> dict:  # noqa: ANN001  ImageItem
        """纯文本替代方案：只用文件名/EXIF/尺寸推断标签（不联网传图）。"""
        parts = [f"文件名：{Path(item.path).name if item.path else item.title}"]
        if item.camera:
            parts.append(f"相机：{item.camera}")
        if item.width and item.height:
            parts.append(f"尺寸：{item.width}x{item.height}（宽高比 {item.aspect:.2f}）")
        if item.taken_at:
            parts.append(f"拍摄时间：{item.taken_text}")
        if item.exif_json:
            try:
                display = json.loads(item.exif_json).get("display") or {}
                extras = [f"{k}={v}" for k, v in list(display.items())[:6]]
                if extras:
                    parts.append("EXIF：" + "，".join(extras))
            except ValueError:
                pass
        prompt = (
            "根据以下图片元数据，推断该图片可能的标签（3~6 个中文词，"
            "例如证件/截图/风景/人像/文档/夜景）。只输出 JSON 数组。\n"
            + "\n".join(parts)
        )
        tags = _parse_str_list(self.chat([{"role": "user", "content": prompt}]))
        return {"caption": "", "tags": tags[:8]}


# --------------------------------------------------------------------------- 解析工具


def config_from_profile(profile, *, kind: str = "", allow_upload: bool = False) -> AiConfig:
    """把「大模型」里的一份配置适配成 `AiConfig`（复用本模块的提示词逻辑）。

    图片类型配置的 `model` 就是多模态模型名，所以两种类型都能直接映射。
    """
    model = str(getattr(profile, "model", "") or DEFAULT_MODEL)
    vision = str(getattr(profile, "vision_model", "") or "")
    if kind == "image":
        vision = model
    return AiConfig(
        api_key=str(getattr(profile, "api_key", "") or ""),
        base_url=str(getattr(profile, "base_url", "") or DEFAULT_BASE_URL),
        model=model,
        vision_model=vision or DEFAULT_VISION_MODEL,
        timeout=float(getattr(profile, "timeout", 60.0) or 60.0),
        max_tokens=int(getattr(profile, "max_tokens", 1024) or 1024),
        temperature=float(getattr(profile, "temperature", 0.3) or 0.3),
        allow_upload=allow_upload,
    )


def _strip_code_fence(text: str) -> str:
    """去掉 ```json ... ``` 包裹。"""
    cleaned = (text or "").strip()
    fence = re.match(r"^```[a-zA-Z]*\s*(.*?)\s*```$", cleaned, flags=re.S)
    if fence:
        return fence.group(1).strip()
    return cleaned


def _parse_json_object(text: str) -> dict:
    cleaned = _strip_code_fence(text)
    try:
        data = json.loads(cleaned)
    except ValueError:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
        except ValueError:
            return {}
    return data if isinstance(data, dict) else {}


def _parse_str_list(text: str) -> list[str]:
    cleaned = _strip_code_fence(text)
    try:
        data = json.loads(cleaned)
        if isinstance(data, list):
            return [str(item).strip() for item in data if str(item).strip()]
    except ValueError:
        pass
    match = re.search(r"\[.*\]", cleaned, flags=re.S)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, list):
                return [str(item).strip() for item in data if str(item).strip()]
        except ValueError:
            pass
    # 退回按分隔符切
    return [part.strip() for part in re.split(r"[,，、\s]+", cleaned) if part.strip()][:12]


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DEFAULT_VISION_MODEL",
    "SETTING_PREFIX",
    "AiConfig",
    "AiConfigError",
    "AiRequestError",
    "DeepSeekClient",
    "config_from_profile",
    "image_digest",
]
