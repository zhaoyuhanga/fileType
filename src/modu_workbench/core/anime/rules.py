"""规则解析 / 校验 / 变量替换 / 导入导出 / 订阅校验和。

规则文件是**唯一真相**（DB 里的 `anime_rules` 只存索引与测试状态）：

    src/modu_workbench/core/anime/rules_builtin/*.json   随包只读
    数据目录/anime/rules/*.json                          用户可编辑
    数据目录/anime/rules/_subscribed/<订阅名>/*.json      订阅（只读 + 校验和）

字段与 Kazumi 规则一一对应（便于直接吃 KazumiRules 仓库的规则），
另外扩展了 `catalogue` / `schedule` / `episodeParse` / `timeout` / `rateLimit`。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .models import (
    CHAPTER_MODES,
    RULE_TYPES,
    SEARCH_MODES,
    SUPPORTED_API_LEVEL,
    AnimeRule,
)
from .selectors import validate_selector

#: 订阅清单文件（KazumiRules 的 `index.json` 结构）
INDEX_FILE = "index.json"
#: 订阅目录名（用户规则目录下）
SUBSCRIPTION_DIR = "_subscribed"

#: 变量占位符
VARIABLE_RE = re.compile(r"@([A-Za-z_][\w]*)")


class RuleError(ValueError):
    """规则加载/校验失败（消息必须可直接展示给用户）。"""


# --------------------------------------------------------------------------- 变量


def substitute(template: str, variables: dict) -> str:
    """替换模板里的 `@变量`（URL 编码由调用方决定，见 `substitute_url`）。

    未提供的变量保持原样（便于试跑面板指出"缺了哪个变量"）。
    """
    if not template:
        return ""
    text = str(template)

    def replace(match: re.Match) -> str:
        name = match.group(1)
        if name in variables and variables[name] is not None:
            return str(variables[name])
        return match.group(0)

    return VARIABLE_RE.sub(replace, text)


def substitute_url(template: str, variables: dict) -> str:
    """替换 URL 模板里的变量；`@keyword` 会做 URL 编码，其余原样。"""
    from urllib.parse import quote

    encoded = dict(variables or {})
    if "keyword" in encoded:
        encoded["keyword"] = quote(str(encoded["keyword"]), safe="")
    return substitute(template, encoded)


def missing_variables(template: str, variables: dict) -> list[str]:
    """模板里尚未提供值的变量名（试跑面板用来指出原因）。"""
    if not template:
        return []
    return sorted({name for name in VARIABLE_RE.findall(str(template))
                   if name not in (variables or {})})


# --------------------------------------------------------------------------- 解析


def _pick(data: dict, *names: str, default=None):  # noqa: ANN001, ANN202
    """按多个候选键取值（兼容 Kazumi 的大小写差异）。"""
    for name in names:
        if name in data and data[name] not in (None, ""):
            return data[name]
    # 再做一次大小写不敏感匹配
    lowered = {str(key).lower(): value for key, value in data.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return value
    return default


def _as_bool(value, default: bool = False) -> bool:  # noqa: ANN001
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return default


def _as_float(value, default: float) -> float:  # noqa: ANN001
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default: int) -> int:  # noqa: ANN001
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def rule_from_dict(data: dict, *, key: str = "", path: str = "", builtin: bool = False,
                   source_url: str = "") -> AnimeRule:
    """把规则 JSON 解析成 `AnimeRule`（**不做校验**，校验见 `validate_rule`）。"""
    if not isinstance(data, dict):
        raise RuleError("规则必须是一个 JSON 对象")
    search_api = _pick(data, "searchApiConfig", "search_api_config", default={}) or {}
    chapter_api = _pick(data, "chapterApiConfig", "chapter_api_config", default={}) or {}
    if not isinstance(search_api, dict):
        search_api = {}
    if not isinstance(chapter_api, dict):
        chapter_api = {}
    rate_limit = _pick(data, "rateLimit", "rate_limit", default={}) or {}
    qps = 2.0
    if isinstance(rate_limit, dict):
        qps = _as_float(rate_limit.get("qps"), 2.0)
    elif rate_limit:
        qps = _as_float(rate_limit, 2.0)

    return AnimeRule(
        name=str(_pick(data, "name", default="") or "").strip(),
        version=str(_pick(data, "version", default="") or "").strip(),
        api=str(_pick(data, "api", "apiLevel", default=SUPPORTED_API_LEVEL)),
        type=str(_pick(data, "type", default="anime") or "anime").strip().lower(),
        base_url=str(_pick(data, "baseURL", "baseUrl", "base_url", default="") or "").strip(),
        key=key,
        path=path,
        builtin=builtin,
        source_url=source_url or str(_pick(data, "sourceUrl", default="") or ""),
        deprecated=_as_bool(_pick(data, "deprecated"), False),
        raw=dict(data),
        referer=str(_pick(data, "referer", default="") or "").strip(),
        user_agent=str(_pick(data, "userAgent", "user_agent", default="") or "").strip(),
        search_mode=str(_pick(data, "searchMode", "search_mode",
                              default="xpath") or "xpath").strip().lower(),
        chapter_mode=str(_pick(data, "chapterMode", "chapter_mode",
                               default="xpath") or "xpath").strip().lower(),
        use_post=_as_bool(_pick(data, "usePost", "use_post"), False),
        use_legacy_parser=_as_bool(_pick(data, "useLegacyParser", "use_legacy_parser"), False),
        ad_blocker=_as_bool(_pick(data, "adBlocker", "ad_blocker"), False),
        use_webview=_as_bool(_pick(data, "useWebview", "use_webview"), False),
        timeout=_as_float(_pick(data, "timeout", default=20), 20.0),
        retries=_as_int(_pick(data, "retries", default=2), 2),
        qps=max(0.1, qps),
        episode_parse=str(_pick(data, "episodeParse", "episode_parse", default="") or ""),
        search_url=str(_pick(data, "searchURL", "searchUrl", "search_url", default="") or "").strip(),
        search_list=str(_pick(data, "searchList", "search_list", default="") or "").strip(),
        search_name=str(_pick(data, "searchName", "search_name", default="") or "").strip(),
        search_result=str(_pick(data, "searchResult", "search_result", default="") or "").strip(),
        chapter_roads=str(_pick(data, "chapterRoads", "chapter_roads", default="") or "").strip(),
        chapter_result=str(_pick(data, "chapterResult", "chapter_result", default="") or "").strip(),
        search_api=search_api,
        chapter_api=chapter_api,
        catalogue=_pick(data, "catalogue", default={}) or {},
        schedule=_pick(data, "schedule", default={}) or {},
    )


def load_rule(path: str | Path, *, builtin: bool = False, key: str = "",
              source_url: str = "") -> AnimeRule:
    """从文件加载规则（容错 BOM 与 CRLF）。"""
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raw = target.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise RuleError(f"规则文件读取失败：{error}") from error
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuleError(f"规则文件不是合法 JSON（第 {error.lineno} 行）：{error.msg}") from error
    return rule_from_dict(data, key=key or target.stem, path=str(target),
                          builtin=builtin, source_url=source_url)


def parse_rule(text: str, *, key: str = "clipboard", builtin: bool = False) -> AnimeRule:
    """从 JSON 文本解析规则（剪贴板导入用）。"""
    try:
        data = json.loads((text or "").lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise RuleError(f"不是合法 JSON（第 {error.lineno} 行）：{error.msg}") from error
    if isinstance(data, list):
        raise RuleError("这是规则**数组**：请用「批量导入」而不是单条导入")
    return rule_from_dict(data, key=key, builtin=builtin)


def rule_to_dict(rule: AnimeRule) -> dict:
    """导出为 JSON 字典（优先用原始数据，保证不改动用户未识别的字段）。"""
    data = dict(rule.raw or {})
    data.setdefault("api", rule.api or str(SUPPORTED_API_LEVEL))
    data.setdefault("type", rule.type or "anime")
    data["name"] = rule.name
    data.setdefault("version", rule.version)
    data.setdefault("baseURL", rule.base_url)
    return data


def rule_to_json(rule: AnimeRule, *, indent: int = 2) -> str:
    return json.dumps(rule_to_dict(rule), ensure_ascii=False, indent=indent)


def rule_checksum(rule: AnimeRule) -> str:
    """规则内容校验和（订阅更新时判断"是否真的变了"）。"""
    payload = json.dumps(rule_to_dict(rule), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- 校验


def validate_rule(rule: AnimeRule, *, require_play: bool = True) -> list[str]:
    """校验规则；返回问题列表（空 = 通过）。

    **不抛异常**：调用方要把问题展示在规则页上（"失败：搜索选择器返回 0 条"
    这类可读原因），所以统一返回列表。
    """
    problems: list[str] = []
    if not rule.name:
        problems.append("缺少 name（规则名）")
    if not rule.base_url:
        problems.append("缺少 baseURL")
    if rule.type not in RULE_TYPES:
        problems.append(f"type 只支持 {'/'.join(RULE_TYPES)}，当前是 {rule.type!r}")
    if rule.api_level > SUPPORTED_API_LEVEL:
        problems.append(
            f"规则需要 api {rule.api_level}，本客户端支持到 {SUPPORTED_API_LEVEL}"
            "（会加载但灰显，不参与搜索）")

    if rule.search_mode not in SEARCH_MODES:
        problems.append(f"searchMode 只支持 {'/'.join(SEARCH_MODES)}")
    if rule.chapter_mode not in CHAPTER_MODES:
        problems.append(f"chapterMode 只支持 {'/'.join(CHAPTER_MODES)}")

    # ---- 搜索 ----
    if rule.search_mode == "xpath":
        if not rule.search_url:
            problems.append("XPath 模式缺少 searchURL")
        elif "@keyword" not in rule.search_url and not rule.use_post:
            problems.append("searchURL 里必须包含 @keyword（否则搜不到东西）")
        for label, expression in (("searchList", rule.search_list),
                                  ("searchName", rule.search_name),
                                  ("searchResult", rule.search_result)):
            if require_play and label in ("searchName", "searchResult") and not expression:
                problems.append(f"缺少 {label}")
            if expression:
                error = validate_selector(expression)
                if error:
                    problems.append(f"{label}：{error}")
    else:
        request = rule.search_api.get("request") if isinstance(rule.search_api, dict) else None
        if not isinstance(request, dict) or not request.get("url"):
            problems.append("API 模式缺少 searchApiConfig.request.url")
        for label in ("listPath", "namePath"):
            expression = rule.search_api.get(label) if isinstance(rule.search_api, dict) else ""
            if not expression:
                problems.append(f"API 模式缺少 searchApiConfig.{label}")
                continue
            error = validate_selector(expression)
            if error:
                problems.append(f"searchApiConfig.{label}：{error}")

    # ---- 选集 ----
    if rule.chapter_mode == "xpath":
        if not rule.chapter_roads:
            problems.append("XPath 模式缺少 chapterRoads")
        else:
            error = validate_selector(rule.chapter_roads)
            if error:
                problems.append(f"chapterRoads：{error}")
        if not rule.chapter_result:
            problems.append("XPath 模式缺少 chapterResult")
        else:
            error = validate_selector(rule.chapter_result)
            if error:
                problems.append(f"chapterResult：{error}")
    else:
        request = rule.chapter_api.get("request") if isinstance(rule.chapter_api, dict) else None
        if not isinstance(request, dict) or not request.get("url"):
            problems.append("API 模式缺少 chapterApiConfig.request.url")
        for label in ("roadsPath", "episodesPath"):
            expression = rule.chapter_api.get(label) if isinstance(rule.chapter_api, dict) else ""
            if not expression:
                problems.append(f"API 模式缺少 chapterApiConfig.{label}")
                continue
            error = validate_selector(expression)
            if error:
                problems.append(f"chapterApiConfig.{label}：{error}")

    if rule.episode_parse:
        try:
            re.compile(rule.episode_parse)
        except re.error as error:
            problems.append(f"episodeParse 不是合法正则：{error}")

    if rule.use_webview:
        problems.append(
            "该规则部分源依赖网页 JS 渲染（useWebview=true）：我们无浏览器内核，可能解析失败")
    return problems


def validate_rule_dict(data: dict) -> list[str]:
    """直接校验一份规则 JSON（导入前预检用）。"""
    try:
        rule = rule_from_dict(data)
    except RuleError as error:
        return [str(error)]
    return validate_rule(rule)


# --------------------------------------------------------------------------- 批量/订阅


def parse_rule_bundle(text: str) -> list[dict]:
    """解析"批量/订阅"体：单个对象、对象数组，或 `{"rules":[...]}`。"""
    try:
        data = json.loads((text or "").lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise RuleError(f"不是合法 JSON（第 {error.lineno} 行）：{error.msg}") from error
    if isinstance(data, dict) and isinstance(data.get("rules"), list):
        data = data["rules"]
    elif isinstance(data, dict) and isinstance(data.get("data"), list):
        data = data["data"]
    elif isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise RuleError("订阅体必须是规则对象、规则数组，或 {\"rules\": [...]}")
    items = [item for item in data if isinstance(item, dict)]
    if not items:
        raise RuleError("订阅体里没有任何规则对象")
    return items


def parse_index(text: str) -> list[dict]:
    """解析 KazumiRules 风格的目录清单 `index.json`。

    兼容结构：`[{"name":..., "version":..., "url":...}]` 或
    `{"rules":[{"name":..., "file"/"url":...}]}`；条目里没有 URL 时用 `index.json`
    同目录的 `<name>.json` 兜底。
    """
    try:
        data = json.loads((text or "").lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise RuleError(f"目录清单不是合法 JSON：{error.msg}") from error
    if isinstance(data, dict):
        data = data.get("rules") or data.get("data") or data.get("items") or []
    if not isinstance(data, list):
        raise RuleError("目录清单必须是数组或含 rules 数组的对象")
    entries: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("fileName") or "").strip()
        url = str(item.get("url") or item.get("file") or item.get("downloadUrl") or "").strip()
        entries.append({
            "name": name,
            "url": url,
            "version": str(item.get("version") or ""),
            "author": str(item.get("author") or ""),
            "last_update": str(item.get("lastUpdate") or item.get("last_update") or ""),
            "deprecated": _as_bool(item.get("deprecated"), False),
            "use_native_player": _as_bool(item.get("useNativePlayer"), True),
            "anti_crawler": _as_bool(item.get("antiCrawlerEnabled"), False),
        })
    return entries


def parse_subscription(text: str) -> list[dict]:
    """解析订阅体（与批量导入同构，但记录来源）。"""
    return parse_rule_bundle(text)


# --------------------------------------------------------------------------- 目录与规则管理器


def builtin_rules_dir() -> Path:
    return Path(__file__).with_name("rules_builtin")


def user_rules_dir() -> Path:
    """用户规则目录（数据目录/anime/rules）。"""
    from modu_workbench.core.platform.paths import anime_rules_dir

    return anime_rules_dir()


def safe_rule_filename(name: str, fallback: str = "rule") -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", (name or "").strip())
    cleaned = re.sub(r"_{2,}", "_", cleaned).strip(" ._")
    return (cleaned or fallback)[:80]


@dataclass
class RuleSource:
    """规则文件来源分类（规则页展示与"可编辑性"判断）。"""

    BUILTIN = "builtin"
    USER = "user"
    SUBSCRIBED = "subscribed"


class RuleManager:
    """规则的发现 / 导入 / 导出 / 删除 / 订阅安装。

    只碰文件系统与 JSON；**启用状态与测试结果在 `storage.py`**（DB）。
    """

    def __init__(self, *, user_dir: Path | None = None,
                 builtin_dir: Path | None = None) -> None:
        self.user_dir = Path(user_dir) if user_dir else user_rules_dir()
        self.builtin_dir = Path(builtin_dir) if builtin_dir else builtin_rules_dir()

    # ------------------------------------------------------------------ 目录

    def ensure_dirs(self) -> None:
        self.user_dir.mkdir(parents=True, exist_ok=True)

    @property
    def subscription_dir(self) -> Path:
        return self.user_dir / SUBSCRIPTION_DIR

    def discover(self) -> list[AnimeRule]:
        """扫描内置 + 用户 + 订阅规则（坏规则跳过并保留原因）。"""
        rules: list[AnimeRule] = []
        errors: list[str] = []
        for path in sorted(self.builtin_dir.glob("*.json")):
            if path.name == INDEX_FILE:
                continue
            try:
                rules.append(load_rule(path, builtin=True,
                                       key=f"builtin:{path.stem}"))
            except RuleError as error:
                errors.append(f"{path.name}：{error}")
        self.ensure_dirs()
        for path in sorted(self.user_dir.glob("*.json")):
            if path.name == INDEX_FILE:
                continue
            try:
                rules.append(load_rule(path, key=path.stem))
            except RuleError as error:
                errors.append(f"{path.name}：{error}")
        if self.subscription_dir.is_dir():
            for folder in sorted(self.subscription_dir.iterdir()):
                if not folder.is_dir():
                    continue
                for path in sorted(folder.glob("*.json")):
                    if path.name == INDEX_FILE:
                        continue
                    try:
                        rules.append(load_rule(
                            path, key=f"sub:{folder.name}:{path.stem}",
                            source_url=self._source_of(folder.name)))
                    except RuleError as error:
                        errors.append(f"{folder.name}/{path.name}：{error}")
        self.last_errors = errors
        return rules

    def _source_of(self, name: str) -> str:
        for entry in list_subscriptions(self.user_dir):
            if entry.get("name") == name:
                return str(entry.get("url") or "")
        return ""

    # ------------------------------------------------------------------ 导入

    def import_text(self, text: str, *, name: str = "") -> list[AnimeRule]:
        """导入单条规则、规则数组或 `{"rules":[...]}` 订阅体（同名覆盖）。"""
        items = parse_rule_bundle(text)
        saved: list[AnimeRule] = []
        for item in items:
            problems = validate_rule_dict(item)
            blocking = [problem for problem in problems
                        if not problem.startswith("该规则部分源依赖")]
            if blocking and not any("灰显" in problem for problem in blocking):
                raise RuleError("；".join(blocking))
            rule = rule_from_dict(item, key=name or safe_rule_filename(
                str(item.get("name") or "rule")))
            path = self._target_path(rule.name, override=name)
            path.write_text(json.dumps(item, ensure_ascii=False, indent=2),
                            encoding="utf-8")
            rule.path = str(path)
            rule.key = path.stem
            saved.append(rule)
        return saved

    def import_file(self, path: str | Path) -> list[AnimeRule]:
        source = Path(path)
        text = source.read_text(encoding="utf-8-sig", errors="replace")
        return self.import_text(text, name=safe_rule_filename(source.stem))

    def _target_path(self, rule_name: str, *, override: str = "") -> Path:
        self.ensure_dirs()
        stem = safe_rule_filename(override or rule_name)
        target = self.user_dir / f"{stem}.json"
        index = 2
        while target.exists() and not override:
            target = self.user_dir / f"{stem} ({index}).json"
            index += 1
        return target

    # ------------------------------------------------------------------ 导出

    def export_rule(self, rule: AnimeRule, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rule_to_json(rule), encoding="utf-8")
        return target

    def export_bundle(self, rules: list[AnimeRule], path: str | Path) -> Path:
        """把多条规则打成订阅文件（`{"version":1,"rules":[...]}`）。"""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "rules": [rule_to_dict(rule) for rule in rules]}
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    # ------------------------------------------------------------------ 删除

    def delete_rule(self, rule: AnimeRule) -> bool:
        """删除用户规则文件（内置/订阅规则不接受删除，避免"删了还会回来"）。"""
        if rule.builtin or rule.key.startswith("sub:"):
            return False
        if rule.path:
            try:
                Path(rule.path).unlink(missing_ok=True)
                return True
            except OSError:
                return False
        return False

    # ------------------------------------------------------------------ 订阅

    def install_subscription(self, name: str, rules: list[dict]) -> list[AnimeRule]:
        """把订阅内容落到 `_subscribed/<name>/`（同名覆盖 = 更新）。"""
        folder = self.subscription_dir / safe_rule_filename(name)
        folder.mkdir(parents=True, exist_ok=True)
        saved: list[AnimeRule] = []
        for item in rules:
            rule_name = str(item.get("name") or "rule")
            target = folder / f"{safe_rule_filename(rule_name)}.json"
            target.write_text(json.dumps(item, ensure_ascii=False, indent=2),
                              encoding="utf-8")
            rule = rule_from_dict(item, key=f"sub:{folder.name}:{target.stem}",
                                  path=str(target), source_url="")
            saved.append(rule)
        return saved

    def subscription_checksums(self, name: str) -> dict[str, str]:
        folder = self.subscription_dir / safe_rule_filename(name)
        result: dict[str, str] = {}
        if not folder.is_dir():
            return result
        for path in sorted(folder.glob("*.json")):
            try:
                result[path.stem] = rule_checksum(load_rule(path))
            except RuleError:
                continue
        return result

    def write_index(self, name: str, text: str) -> Path:
        folder = self.subscription_dir / safe_rule_filename(name)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / INDEX_FILE
        target.write_text(text, encoding="utf-8")
        return target

    def read_index(self, name: str) -> list[dict]:
        path = self.subscription_dir / safe_rule_filename(name) / INDEX_FILE
        if not path.is_file():
            return []
        return parse_index(path.read_text(encoding="utf-8-sig", errors="replace"))

    def remove_subscription(self, name: str, *, keep_rules: bool = False) -> bool:
        folder = self.subscription_dir / safe_rule_filename(name)
        if not folder.is_dir():
            return False
        if keep_rules:
            return False
        shutil.rmtree(folder, ignore_errors=True)
        return True


# --------------------------------------------------------------------------- 订阅清单（DB 存 JSON）


def subscriptions_key() -> str:
    return "rule_subscriptions"


def list_subscriptions(user_dir: Path | None = None) -> list[dict]:
    """读取订阅列表（存 `app_settings` 的 `anime/rule_subscriptions`，JSON 数组）。"""
    from .storage import load_subscriptions

    return load_subscriptions()


def save_subscriptions(entries: list[dict]) -> None:
    from .storage import save_subscriptions as _save

    _save(entries)


__all__ = [
    "INDEX_FILE", "SUBSCRIPTION_DIR", "RuleError", "RuleManager", "RuleSource",
    "builtin_rules_dir", "list_subscriptions", "load_rule", "missing_variables",
    "parse_index", "parse_rule", "parse_rule_bundle", "parse_subscription",
    "rule_checksum", "rule_from_dict", "rule_to_dict", "rule_to_json",
    "safe_rule_filename", "save_subscriptions", "subscriptions_key", "substitute",
    "substitute_url", "user_rules_dir", "validate_rule", "validate_rule_dict",
]
