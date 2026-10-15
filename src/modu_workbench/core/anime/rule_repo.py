"""Kazumi 官方规则仓库的一键导入（`Predidit/KazumiRules`）。

## 为什么需要这个模块

用户反馈："墨软动漫还是没有规则，能默认一套导入 Kazumi 官方规则仓库、创建文件自动导入"。
随包的 `rules_builtin/` 只有两条指向 `127.0.0.1` / `example.com` 的**离线示例**，
导入后搜索是 0 条 —— 空着手进动漫板块确实没法用。

**但不做"把别人的规则打进安装包"**，两个原因：

1. **规则是有版权的第三方内容**，仓库里是 MIT 的代码、规则本身不是（README 明确说
   规则由社区贡献、指向第三方站点）。把 16 条真实规则复制进我们的发行物再分发，
   不合适；
2. 这类规则**寿命以周计**（站点换域名/改版），打包进去很快就全是失效的，
   用户拿到手就是"全是坏规则"，比没有更糟。

所以做成**一键从上游拉取并落地成本地文件**：内容始终是最新的，也不再分发别人的东西。
导入后规则就是普通本地规则文件（`数据目录/anime/rules/_subscribed/KazumiRules/*.json`），
用户可以自己改、单独删、禁用。

## 多源回退（真机实测的必要设计）

本机实测：`raw.githubusercontent.com` 与 `cdn.jsdelivr.net` 都是
`WinError 10054 远程主机强迫关闭了一个现有的连接`，**只有 `api.github.com` 通**。
（反过来在某些网络里 raw 通、api 被限流。）因此这里按顺序试多个源，
并把**第一次成功的源记下来**给后续请求用，避免每条规则都先撞两次墙。

## 诚实的边界（写在这里，也要出现在界面上）

这批规则**全部标注 `useWebview: true`**。实测 16 条：搜索与选集 11 条可用，
但**播放地址 0 条能静态解析**（站点 JS 算的或私有加密）。也就是导入后
"能搜到番、能看到剧集列表、能追番挂弹幕"，但**点播放大概率打不开**。
这不是导入功能的问题，是没有浏览器内核这条边界的直接结果。
"""
from __future__ import annotations

import base64
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Iterable

from modu_workbench.core.anime.rules import safe_rule_filename
from modu_workbench.core.platform.http import HttpClient, SourceError

#: 上游仓库（用于展示与校验）
REPO = "Predidit/KazumiRules"
REPO_URL = f"https://github.com/{REPO}"
#: 订阅在本地落地时用的名字
SUBSCRIPTION_NAME = "KazumiRules"
#: 单条规则的下载并发（别把 api.github.com 打限流）
DOWNLOAD_WORKERS = 4

#: 多个下载源，按顺序回退。`{name}` 会被替换成规则名（不带 .json）。
#: 最后一项走 GitHub API（本机实测唯一可用的那个），代价是响应更大、
#: 未认证时受 60 次/小时的限流，所以放在最后而不是最前。
SOURCES: tuple[dict, str] = (
    {
        "key": "raw",
        "label": "GitHub 原始文件",
        "index": f"https://raw.githubusercontent.com/{REPO}/main/index.json",
        "rule": f"https://raw.githubusercontent.com/{REPO}/main/{{name}}.json",
        "kind": "json",
    },
    {
        "key": "jsdelivr",
        "label": "jsDelivr CDN",
        "index": f"https://cdn.jsdelivr.net/gh/{REPO}@main/index.json",
        "rule": f"https://cdn.jsdelivr.net/gh/{REPO}@main/{{name}}.json",
        "kind": "json",
    },
    {
        "key": "api",
        "label": "GitHub API",
        "index": f"https://api.github.com/repos/{REPO}/contents/index.json",
        "rule": f"https://api.github.com/repos/{REPO}/contents/{{name}}.json",
        "kind": "api",
    },
)


#: 上次成功的下载源（本机实测前两个源必失败，记住它能省 16 秒/次）
_preferred: dict | None = None


@dataclass
class ImportResult:
    """一次导入的结果（界面直接拿它显示，别让用户猜）。"""

    installed: list[str] = field(default_factory=list)      # 成功落地的规则名
    failed: list[tuple[str, str]] = field(default_factory=list)  # (规则名, 原因)
    skipped: list[str] = field(default_factory=list)        # 本地已最新、跳过的规则名
    source: str = ""                                        # 实际用上的下载源
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.installed) or bool(self.skipped)

    def summary(self) -> str:
        parts = [f"已导入 {len(self.installed)} 条规则"]
        if self.skipped:
            parts.append(f"已是最新 {len(self.skipped)} 条")
        if self.failed:
            parts.append(f"失败 {len(self.failed)} 条")
        if self.source:
            parts.append(f"来源：{self.source}")
        return "；".join(parts)


class RuleRepoError(SourceError):
    """取规则仓库失败（所有下载源都试过）。"""


def _http(referer: str = "") -> HttpClient:
    import os

    headers = {"Accept": "application/json, text/plain, */*",
               "User-Agent": "ModuWorkbench/1.1 (+https://github.com/zhaoyuhanga/fileType)"}
    # 可选令牌：未认证的 api.github.com 只有 60 次/小时，全量导入一次约 18 次，
    # 频繁更新就会撞 403。设置 GITHUB_TOKEN 可提到 5000 次/小时（不做界面，够用）。
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if referer:
        headers["Referer"] = referer
    # 单个源 8 秒、最多 2 次：多源回退下"快点失败换下一个"比"死磕一个"更划算
    return HttpClient(headers=headers, timeout=8.0, attempts=2, backoff=0.4,
                      hint="可在规则页用「导入」手动指定其它地址")


def _decode_source(text: str, kind: str) -> str:
    """把响应体变成规则 JSON 文本。

    `api.github.com` 返回的是 `{"content": "<base64>", "encoding": "base64"}` 包装，
    其它源直接就是文件内容。这里统一解开，调用方不用关心用的是哪个源。
    """
    if kind != "api":
        return text
    try:
        payload = json.loads(text)
    except ValueError as error:
        raise RuleRepoError(f"GitHub API 返回的不是 JSON：{error}") from error
    if isinstance(payload, dict) and payload.get("encoding") == "base64" and payload.get("content"):
        try:
            raw = base64.b64decode(payload["content"])
        except Exception as error:  # noqa: BLE001
            raise RuleRepoError(f"GitHub API 内容的 base64 解码失败：{error}") from error
        return raw.decode("utf-8", "replace")
    if isinstance(payload, dict) and payload.get("message"):
        raise RuleRepoError(str(payload["message"]))
    raise RuleRepoError("GitHub API 响应里没有 content 字段")


def _fetch(source: dict, url: str) -> str:
    client = _http(referer=REPO_URL)
    try:
        text = client.get_text(url)
    except Exception as error:  # noqa: BLE001
        # 403 在 `api.github.com` 上基本只有一种原因：未认证限流 60 次/小时。
        # 本机实测撞到过（调试期间请求太多）。必须把它翻译成用户能行动的说明 ——
        # "403 Forbidden" 只会让人以为被封了。
        message = str(error)
        if "403" in message and "api.github.com" in url:
            raise RuleRepoError(
                "GitHub API 限流（未认证时 60 次/小时）。"
                "请稍后重试；若需要频繁更新，可设置环境变量 GITHUB_TOKEN 提高额度"
            ) from error
        raise
    if not text:
        raise RuleRepoError(f"{source['label']} 返回空内容")
    decoded = _decode_source(text, str(source.get("kind") or "json"))
    if not decoded.lstrip().startswith(("{", "[")):
        raise RuleRepoError(f"{source['label']} 返回的不是规则内容（可能被网络劫持/拦截）")
    return decoded


def pick_source() -> tuple[dict, str, str]:
    """按顺序探测下载源，返回 `(源, index 文本, 人类可读来源名)`。

    探测顺序即 `SOURCES`；第一个能拿到合法 index 的源胜出。
    **成功的源会被记住**（`_preferred`）：本机实测前两个源必失败，
    不记住的话每次导入都要先撞两次墙（实测探测一次要 16.7 秒）。
    """
    global _preferred
    order: list[dict] = []
    if _preferred is not None:
        order.append(_preferred)
    order.extend(source for source in SOURCES if source is not _preferred)

    failures: list[str] = []
    for source in order:
        try:
            text = _fetch(source, source["index"])
            json.loads(text)          # index 必须是合法 JSON，否则不算成功
            _preferred = source
            return source, text, str(source["label"])
        except Exception as error:  # noqa: BLE001  换下一个源
            failures.append(f"{source['label']}：{error}")
            if source is _preferred:  # 记住的源也挂了 → 下次从头试
                _preferred = None
    raise RuleRepoError("取规则清单失败（所有下载源都不可用）：" + "；".join(failures))


def reset_preferred_source() -> None:
    """忘掉记住的下载源（测试与"网络环境变了"时用）。"""
    global _preferred
    _preferred = None


def fetch_index(*, source: dict | None = None) -> list[dict]:
    """取官方仓库的规则清单（`index.json`），返回 `[{name, version, ...}, ...]`。"""
    if source is not None:
        return _parse_index(_fetch(source, source["index"]))
    _source, text, _label = pick_source()
    return _parse_index(text)


def _parse_index(text: str) -> list[dict]:
    try:
        payload = json.loads(text)
    except ValueError as error:
        raise RuleRepoError(f"规则清单不是合法 JSON：{error}") from error
    if isinstance(payload, dict):                      # 有些镜像会包一层
        payload = payload.get("rules") or payload.get("data") or []
    if not isinstance(payload, list):
        raise RuleRepoError("规则清单格式不认识（期望一个数组）")
    items: list[dict] = []
    for entry in payload:
        if isinstance(entry, str):
            items.append({"name": entry, "version": ""})
        elif isinstance(entry, dict) and entry.get("name"):
            items.append(entry)
    return items


def fetch_rules(names: Iterable[str], *, source: dict | None = None,
                on_progress: Callable[[int, int, str], None] | None = None,
                workers: int = DOWNLOAD_WORKERS) -> tuple[list[dict], ImportResult]:
    """并发下载指定规则，返回 `(规则字典列表, 结果摘要)`。

    `on_progress(done, total, name)` 每完成一条调一次（工作线程内调用）。
    单条失败不影响其它条 —— 记进 `ImportResult.failed`，界面照实显示。
    """
    wanted = [str(name).strip() for name in names if str(name).strip()]
    result = ImportResult()
    if not wanted:
        return [], result

    if source is None:
        source, _text, label = pick_source()
        result.source = label
    else:
        result.source = str(source.get("label") or source.get("key") or "")

    template = str(source["rule"])
    kind = str(source.get("kind") or "json")
    rules: list[dict] = []
    done = 0

    def _one(name: str) -> tuple[str, dict | None, str]:
        url = template.replace("{name}", name)
        try:
            text = _fetch(source, url)
            payload = json.loads(text)
            if not isinstance(payload, dict):
                return name, None, "规则内容不是 JSON 对象"
            payload.setdefault("name", name)
            return name, payload, ""
        except Exception as error:  # noqa: BLE001  单条失败不影响其它条
            return name, None, str(error)[:160]

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(wanted))),
                            thread_name_prefix="kazumi-rules") as pool:
        futures = [pool.submit(_one, name) for name in wanted]
        for future in as_completed(futures):
            name, payload, error = future.result()
            if payload is not None:
                rules.append(payload)
                result.installed.append(name)
            else:
                result.failed.append((name, error))
            done += 1
            if on_progress is not None:
                try:
                    on_progress(done, len(wanted), name)
                except Exception:  # noqa: BLE001  进度回调不能影响导入
                    pass

    # 顺序按上游清单走，便于用户对照
    order = {name: index for index, name in enumerate(wanted)}
    rules.sort(key=lambda item: order.get(str(item.get("name")), 999))
    result.installed.sort(key=lambda name: order.get(name, 999))
    if result.installed:
        result.warnings.append(
            "这批上游规则多数标注 useWebview：能搜到番、能看到剧集列表，"
            "但播放地址依赖网页脚本，静态解析多半拿不到 —— 取不到时请用「用外部播放器打开」")
    return rules, result


def import_from_repo(manager, *, names: Iterable[str] | None = None,  # noqa: ANN001
                     on_progress: Callable[[int, int, str], None] | None = None,
                     skip_unchanged: bool = True,
                     ) -> ImportResult:
    """把官方规则导入 `RuleManager`（落地成 `_subscribed/KazumiRules/*.json`）。

    `names=None` 表示"清单里的全部规则"。

    **`skip_unchanged=True`（默认）会把"上游版本没变"的规则跳过。**

    这不是优化洁癖，是必须的：走 `api.github.com` 时未认证只有 **60 次/小时**，
    而一次全量导入要 1 次清单 + 17 次文件下载 —— 更新一次就吃掉三分之一的配额，
    用户再点两次就撞 `403 rate limit exceeded`（本机实测撞到过）。
    跳过后"原样再导入一次"只剩 **1 个请求**。
    上游版本比本地新（或本地缺失）时才真正下载。取不到清单时不会静默当作"都变了"，
    而是照常全量下载 —— 那才是旧行为。
    """
    source, index_text, label = pick_source()
    entries = _parse_index(index_text)
    if not entries:
        raise RuleRepoError("规则清单是空的")
    upstream_version = {str(entry["name"]): str(entry.get("version") or "") for entry in entries}
    if names is None:
        wanted = [str(entry["name"]) for entry in entries]
    else:
        wanted = [str(name) for name in names]
        known = set(upstream_version)
        missing = [name for name in wanted if name not in known]
        if missing:
            raise RuleRepoError("清单里没有这些规则：" + "、".join(missing))

    skipped: list[str] = []
    if skip_unchanged:
        keep: list[str] = []
        for name in wanted:
            local = _local_version(manager, name)
            newer = upstream_version.get(name) or ""
            if local and newer and local == newer:
                skipped.append(name)
                continue
            keep.append(name)
        wanted = keep

    rules, result = fetch_rules(wanted, source=source, on_progress=on_progress)
    result.source = label
    result.skipped = skipped
    if skipped:
        result.warnings.append(
            f"{len(skipped)} 条本地已是最新，已跳过下载（省上游请求配额）")
    if rules:
        manager.install_subscription(SUBSCRIPTION_NAME, rules)
        # 清单也落盘，便于之后"更新"和界面显示条目数
        manager.write_index(SUBSCRIPTION_NAME, index_text)
    return result


def _local_version(manager, name: str) -> str:  # noqa: ANN001
    """本地已装规则的 version（没有该文件返回空）。"""
    path = manager.subscription_dir / SUBSCRIPTION_NAME / f"{safe_rule_filename(name)}.json"
    if not path.is_file():
        return ""
    try:
        return str(json.loads(path.read_text(encoding="utf-8-sig")).get("version") or "")
    except Exception:  # noqa: BLE001  坏文件当成"没有本地版本"，会重新下载
        return ""


def installed_rule_names(manager) -> list[str]:  # noqa: ANN001
    """本地已导入的官方规则名（用于界面显示"已导入 N 条"）。

    必须排除 `index.json` —— 它是清单不是规则，算进去会让"17 条规则"显示成 18。
    """
    from modu_workbench.core.anime.rules import INDEX_FILE

    folder = manager.subscription_dir / SUBSCRIPTION_NAME
    if not folder.is_dir():
        return []
    return sorted(path.stem for path in folder.glob("*.json") if path.name != INDEX_FILE)


def outdated(manager) -> list[tuple[str, str, str]]:  # noqa: ANN001
    """本地规则与上游清单的版本差异：`[(规则名, 本地版本, 上游版本), ...]`。

    只比 `version` 字段（上游靠它标注改版）。读不到本地清单就返回空 ——
    这里**不主动联网**：每次进规则页都打一次上游既慢又没必要，
    真要更新让用户点「导入/更新」。
    """
    # 注意：`RuleManager.read_index()` 返回的是**已解析的 list**，不是文本 ——
    # 早先这里又套了一次 `_parse_index()`，直接 TypeError。
    entries = manager.read_index(SUBSCRIPTION_NAME)
    if not isinstance(entries, list):
        entries = _parse_index(entries or "[]")
    if not entries:
        return []
    upstream = {str(entry["name"]): str(entry.get("version") or "") for entry in entries}
    result: list[tuple[str, str, str]] = []
    folder = manager.subscription_dir / SUBSCRIPTION_NAME
    if not folder.is_dir():
        return []
    for path in sorted(folder.glob("*.json")):
        name = path.stem
        try:
            local = str(json.loads(path.read_text(encoding="utf-8-sig")).get("version") or "")
        except Exception:  # noqa: BLE001
            continue
        if name in upstream and upstream[name] and upstream[name] != local:
            result.append((name, local, upstream[name]))
    return result


__all__ = [
    "DOWNLOAD_WORKERS",
    "ImportResult",
    "REPO",
    "REPO_URL",
    "RuleRepoError",
    "SOURCES",
    "SUBSCRIPTION_NAME",
    "fetch_index",
    "fetch_rules",
    "import_from_repo",
    "installed_rule_names",
    "outdated",
    "pick_source",
    "reset_preferred_source",
]
