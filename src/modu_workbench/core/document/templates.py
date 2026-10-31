"""墨软文档：本地模板库（美化模板的保存 / 导入 / 导出 / 删除）。

需求 6.11 把「模板市场」列为 P1。离线工作台做不了联网市场，但**模板真正有用的部分是
"把一套排版参数存下来、随时套用、能互相分享"** —— 这里就做这个：

- 用户模板存成 JSON（`数据目录/document/templates/<名字>.json`），可读可改、可版本管理；
- 内置模板（`beautify.TEMPLATES`）与用户模板在界面上合并列出（用户模板带「我的」前缀）；
- 导出即"分享模板"，导入即"使用别人的模板" —— 不需要账号与网络，也不会因为站点下线而失效。

JSON 结构（`version` 便于以后兼容旧文件）：

    {"version": 1, "name": "公司公文", "note": "内部规范", "builtin": "gov",
     "template": "gov", "options": {...BeautifyOptions.to_dict()...}}
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from modu_workbench.core.platform.paths import document_dir

from .beautify import TEMPLATES, template_options
from .models import BeautifyOptions

TEMPLATE_VERSION = 1
USER_PREFIX = "我的模板："
_SAFE_NAME = re.compile(r'[\\/:*?"<>|\s]+')


class TemplateError(ValueError):
    """模板读写失败（消息可直接展示）。"""


def template_dir() -> Path:
    path = document_dir() / "templates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_template_name(name: str) -> str:
    cleaned = _SAFE_NAME.sub("_", (name or "").strip()).strip("_")
    return cleaned[:60] or f"模板-{time.strftime('%Y%m%d-%H%M%S')}"


@dataclass
class UserTemplate:
    """一个用户模板（磁盘上的 JSON）。"""

    name: str
    path: str
    note: str = ""
    base: str = "general"
    options: Optional[BeautifyOptions] = None

    @property
    def label(self) -> str:
        return f"{USER_PREFIX}{self.name}"

    def summary(self) -> str:
        options = self.options or BeautifyOptions()
        return (f"{options.font_cn} {options.body_size:g}pt · 行距 {options.line_spacing:g}"
                + ("· 自动编号" if options.auto_number else "")
                + ("· 目录" if options.build_toc else "")
                + (f"｜{self.note}" if self.note else ""))


def options_from_dict(data: dict, base: str = "general") -> BeautifyOptions:
    """从 JSON 字典还原参数（`heading_sizes` 的键在 JSON 里会变成字符串）。"""
    options = template_options(base)
    sizes = data.get("heading_sizes") or {}
    for key, value in (data or {}).items():
        if key == "heading_sizes":
            continue
        if hasattr(options, key):
            setattr(options, key, value)
    if sizes:
        options.heading_sizes = {int(key): float(value) for key, value in sizes.items()}
    return options


def save_template(name: str, options: BeautifyOptions, *, note: str = "",
                  base: str = "") -> Path:
    """保存（或覆盖）一个用户模板。"""
    safe = safe_template_name(name)
    payload = {
        "version": TEMPLATE_VERSION,
        "name": safe,
        "note": note,
        "template": options.template or base or "general",
        "saved_at": int(time.time()),
        "options": options.to_dict(),
    }
    target = template_dir() / f"{safe}.json"
    try:
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as error:
        raise TemplateError(f"保存模板失败：{error}") from error
    return target


def load_template(source: str | Path) -> UserTemplate:
    """读取模板（可以给名字、文件名或完整路径）。"""
    path = Path(source)
    if not path.is_file():
        candidate = template_dir() / f"{safe_template_name(str(source))}.json"
        if candidate.is_file():
            path = candidate
        else:
            raise TemplateError(f"模板不存在：{source}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise TemplateError(f"模板文件不是有效 JSON：{path.name}（{error}）") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("options"), dict):
        raise TemplateError(f"模板文件结构不正确：{path.name}（缺少 options）")
    base = str(payload.get("template") or "general")
    return UserTemplate(name=str(payload.get("name") or path.stem), path=str(path),
                        note=str(payload.get("note") or ""), base=base,
                        options=options_from_dict(payload["options"], base))


def list_user_templates() -> list[UserTemplate]:
    """列出全部用户模板（读不出来的只列名字，不抛异常）。"""
    templates: list[UserTemplate] = []
    for path in sorted(template_dir().glob("*.json")):
        try:
            templates.append(load_template(path))
        except TemplateError:
            templates.append(UserTemplate(name=path.stem, path=str(path), note="（文件损坏）"))
    return templates


def delete_template(source: str | Path) -> bool:
    path = Path(source)
    if not path.is_file():
        candidate = template_dir() / f"{safe_template_name(str(source))}.json"
        path = candidate if candidate.is_file() else path
    if not path.is_file():
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


def export_template(source: str | Path, destination: str | Path) -> Path:
    """把模板导出到任意位置（"分享模板"）。"""
    template = load_template(source)
    target = Path(destination)
    if target.is_dir():
        target = target / f"{safe_template_name(template.name)}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(Path(template.path).read_text(encoding="utf-8"), encoding="utf-8")
    except OSError as error:
        raise TemplateError(f"导出模板失败：{error}") from error
    return target


def import_template(source: str | Path, *, name: str = "") -> UserTemplate:
    """导入一个模板文件（可改名字），返回落库后的模板。"""
    incoming = load_template(source)
    final_name = safe_template_name(name or incoming.name)
    path = save_template(final_name, incoming.options or BeautifyOptions(),
                         note=incoming.note, base=incoming.base)
    saved = load_template(path)
    saved.name = final_name
    return saved


def choices() -> list[tuple[str, str]]:
    """给界面用的下拉项：`(value, label)`。

    - 内置模板：value = 模板 key；
    - 用户模板：value = `user:<文件路径>`，界面上加「我的模板：」前缀。
    """
    items: list[tuple[str, str]] = [
        (key, spec.label) for key, spec in TEMPLATES.items()
    ]
    items.extend((f"user:{template.path}", template.label)
                 for template in list_user_templates())
    return items


def token_for_user_template(path: str | Path) -> str:
    return f"user:{path}"


def resolve_choice(value: str) -> tuple[BeautifyOptions, str]:
    """把下拉项解析成（参数, 展示名）。`user:` 前缀读 JSON，其它按内置模板。"""
    if value.startswith("user:"):
        template = load_template(value[len("user:"):])
        options = template.options or BeautifyOptions()
        options.template = template.base or options.template
        return options, template.label
    return template_options(value or "general"), TEMPLATES.get(
        value or "general", TEMPLATES["general"]).label


def capability_text() -> str:
    return (f"内置 {len(TEMPLATES)} 套模板 + 本地模板库（保存/导入/导出/删除，"
            "JSON 可分享、可版本管理；不依赖联网模板市场）")


__all__ = [
    "TEMPLATE_VERSION",
    "USER_PREFIX",
    "TemplateError",
    "UserTemplate",
    "capability_text",
    "choices",
    "delete_template",
    "export_template",
    "import_template",
    "list_user_templates",
    "load_template",
    "options_from_dict",
    "resolve_choice",
    "safe_template_name",
    "save_template",
    "template_dir",
    "token_for_user_template",
]
