"""墨软文档：权限与安全（MR-DOC-308）。

四件事：
1. **脱敏**（`mask_text` / `mask_document`）：上传模型之前把手机号、身份证、银行卡、
   邮箱、金额、IP、车牌、中文姓名遮掉；界面上可以先"预览命中"再决定是否发送；
2. **权限控制**（`Permissions`）：是否允许上传云端、是否允许本地处理、哪些模型可用、
   单文档大小上限 —— 由 `ai.py` 在每次调用前强制检查，而不是靠调用方自觉；
3. **水印与追踪**（`build_watermark` / `new_tracking_id`）：导出时写入页眉页脚与追踪标识；
4. **审计**（`AUDIT_ACTIONS` / `audit_line`）：AI 调用、工具调用、导出、合并等操作
   统一落库（表 `doc_audit`，由 `storage.py` 提供读写）。

默认策略是"本地优先"：默认允许本地处理、**默认不上传**、默认开启脱敏。
"""
from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from .models import Change, DocumentIR
from .writer import WatermarkOptions


# ---------------------------------------------------------------- 脱敏规则


@dataclass(frozen=True)
class MaskRule:
    key: str
    label: str
    note: str
    pattern: re.Pattern
    keep_head: int = 0
    keep_tail: int = 4
    keep_separator: bool = False

    def mask(self, value: str, *, char: str = "*") -> str:
        return _mask_keep(value, self.keep_head, self.keep_tail, char=char,
                          keep_separator=self.keep_separator)


def _mask_keep(value: str, keep_head: int, keep_tail: int, *, char: str = "*",
               keep_separator: bool = False) -> str:
    """保留头尾、遮住中间；分隔符（-、空格、@ 之后）按需保留。"""
    if not value:
        return value
    if keep_separator:
        # 只遮数字/字母，保留 - 空格等分隔符的结构
        head = value[:keep_head]
        tail = value[-keep_tail:] if keep_tail else ""
        middle = value[len(head):len(value) - len(tail)] if len(value) > len(head) + len(tail) else ""
        masked = "".join(ch if not ch.isalnum() else char for ch in middle)
        return f"{head}{masked}{tail}"
    if len(value) <= keep_head + keep_tail:
        return char * len(value)
    return value[:keep_head] + char * (len(value) - keep_head - keep_tail) + value[-keep_tail:]


def _mask_email(value: str) -> str:
    local, _, domain = value.partition("@")
    if len(local) <= 1:
        return f"*@{domain}"
    return f"{local[0]}{'*' * max(1, len(local) - 1)}@{domain}"


def _mask_amount(value: str) -> str:
    """金额：遮住所有数字但保留千分位、小数点与单位（便于核对量级）。"""
    match = re.match(r"^(?P<prefix>[^\d]*)(?P<body>[\d.,]+)(?P<suffix>.*)$", value)
    if not match:
        return "*" * len(value)
    masked = "".join("*" if char.isdigit() else char for char in match.group("body"))
    if "." in masked:
        integer, _, decimal = masked.partition(".")
        masked = f"{integer}.{decimal[:2]}"
    return f"{match.group('prefix')}{masked}{match.group('suffix')}"


MASK_RULES: dict[str, MaskRule] = {
    "phone": MaskRule(
        "phone", "手机号", "1 开头的 11 位手机号，保留前 3 后 4",
        re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), keep_head=3, keep_tail=4),
    "id_card": MaskRule(
        "id_card", "身份证号", "18 位身份证号，保留前 6 后 4",
        re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), keep_head=6, keep_tail=4),
    "bank_card": MaskRule(
        "bank_card", "银行卡号", "13-19 位数字（含空格分组），保留后 4 位",
        re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"), keep_head=0, keep_tail=4,
        keep_separator=True),
    "email": MaskRule(
        "email", "邮箱", "保留首字母与域名",
        re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), keep_head=1, keep_tail=0),
    "amount": MaskRule(
        "amount", "金额", "遮住整数部分，保留小数与单位（支持 1,200,000 这种千分位写法）",
        re.compile(r"(?<![\d.])(?:[¥$€£]\s?)?(?:\d{1,3}(?:,\d{3})+|\d{3,})(?:\.\d+)?"
                   r"(?:\s?(?:元|万元|亿元|美元|万))?(?![\d.])"),
        keep_head=0, keep_tail=0),
    "ip": MaskRule(
        "ip", "IP 地址", "保留前两段",
        re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"), keep_head=0, keep_tail=0),
    "plate": MaskRule(
        "plate", "车牌号", "保留省份与字母",
        re.compile(r"[京津冀晋蒙辽吉黑沪苏浙皖闽赣鲁豫鄂湘粤桂琼渝川贵云藏陕甘青宁新]"
                   r"[A-HJ-NP-Z][A-HJ-NP-Z0-9]{4,5}[A-HJ-NP-Z0-9挂学警港澳]"),
        keep_head=2, keep_tail=0),
    "cn_name": MaskRule(
        "cn_name", "中文姓名", "保留姓氏",
        re.compile(r"(?<![\u4e00-\u9fff])[\u4e00-\u9fff]{2,4}(?=(?:先生|女士|同学|经理|主任|老师|同志))"),
        keep_head=1, keep_tail=0),
}

#: 默认开启的脱敏规则（覆盖需求里点名的手机号/身份证/金额）
DEFAULT_MASK_RULES: tuple[str, ...] = ("phone", "id_card", "bank_card", "email", "amount")


def mask_label(key: str) -> str:
    rule = MASK_RULES.get(key)
    return rule.label if rule else key


def mask_rule_choices() -> list[tuple[str, str]]:
    return [(key, rule.label) for key, rule in MASK_RULES.items()]


def mask_text(text: str, rules: Iterable[str] | None = None, *,
              char: str = "*") -> tuple[str, dict[str, int]]:
    """按规则脱敏，返回（脱敏后的文本, 每条规则命中次数）。"""
    keys = list(rules) if rules is not None else list(DEFAULT_MASK_RULES)
    counts: dict[str, int] = {}
    result = text or ""
    for key in keys:
        rule = MASK_RULES.get(key)
        if rule is None:
            continue

        def replace(match: re.Match, _rule: MaskRule = rule) -> str:
            counts[_rule.key] = counts.get(_rule.key, 0) + 1
            if _rule.key == "email":
                return _mask_email(match.group(0))
            if _rule.key == "amount":
                return _mask_amount(match.group(0))
            if _rule.key == "ip":
                parts = match.group(0).split(".")
                return ".".join([parts[0], parts[1] or "0", char, char])
            return _rule.mask(match.group(0), char=char)

        result = rule.pattern.sub(replace, result)
    return result, counts


def scan_sensitive(text: str, rules: Iterable[str] | None = None) -> dict[str, int]:
    """只统计命中（不修改文本），用于界面上的"发送前预览"。"""
    keys = list(rules) if rules is not None else list(DEFAULT_MASK_RULES)
    counts: dict[str, int] = {}
    for key in keys:
        rule = MASK_RULES.get(key)
        if rule is None:
            continue
        hits = len(rule.pattern.findall(text or ""))
        if hits:
            counts[key] = hits
    return counts


@dataclass
class MaskReport:
    """脱敏结果：命中统计 + 变更条目。"""

    counts: dict[str, int] = field(default_factory=dict)
    changes: list[Change] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def summary(self) -> str:
        if not self.counts:
            return "没有发现需要脱敏的敏感信息"
        parts = [f"{mask_label(key)} {value} 处" for key, value in self.counts.items()]
        return "已脱敏：" + "、".join(parts)


def mask_document(ir: DocumentIR, rules: Iterable[str] | None = None, *,
                  char: str = "*") -> tuple[DocumentIR, MaskReport]:
    """对整份文档脱敏（正文 + 表格单元格）。"""
    target = ir.clone()
    report = MaskReport()
    for index, block in enumerate(target.blocks):
        if block.is_table and block.table is not None:
            for row_index, row in enumerate(block.table.rows):
                for column_index, cell in enumerate(row):
                    masked, counts = mask_text(cell, rules, char=char)
                    if masked != cell:
                        block.table.rows[row_index][column_index] = masked
                        _merge_counts(report.counts, counts)
            continue
        masked, counts = mask_text(block.text, rules, char=char)
        if masked != block.text:
            report.changes.append(Change(
                "脱敏", f"块 {index + 1}", "已遮住敏感信息", before=block.text[:40],
                after=masked[:40]))
            block.text = masked
            _merge_counts(report.counts, counts)
    return target, report


def _merge_counts(target: dict[str, int], extra: dict[str, int]) -> None:
    for key, value in extra.items():
        target[key] = target.get(key, 0) + value


# ---------------------------------------------------------------- 权限


@dataclass
class Permissions:
    """AI 与文档处理的权限开关（默认本地优先、默认不上传）。"""

    allow_cloud: bool = False            # 允许把文档内容发到云端模型
    allow_local: bool = True             # 允许调用本地模型（Ollama 等）
    mask_before_upload: bool = True      # 上传前自动脱敏
    allowed_models: tuple[str, ...] = ()  # 允许使用的模型名（空 = 不限制）
    max_chars: int = 20000               # 单次送给模型的最大字符数
    mask_rules: tuple[str, ...] = DEFAULT_MASK_RULES

    def check(self, profile_label: str, *, is_local: bool, payload: str) -> Optional[str]:
        """返回拒绝原因（None = 允许）。"""
        if is_local and not self.allow_local:
            return f"本地模型处理已被关闭：{profile_label}（可在「设置 → 文档」里开启）"
        if not is_local and not self.allow_cloud:
            return f"云端模型上传已被关闭：{profile_label}（默认只做本地处理）"
        if self.allowed_models and profile_label not in self.allowed_models:
            return f"模型 {profile_label} 不在允许清单里"
        if len(payload) > self.max_chars:
            return (f"内容过长（{len(payload)} 字 > 上限 {self.max_chars} 字）："
                    "请先选中片段或调高上限")
        return None

    def to_dict(self) -> dict:
        return {
            "allow_cloud": self.allow_cloud,
            "allow_local": self.allow_local,
            "mask_before_upload": self.mask_before_upload,
            "allowed_models": list(self.allowed_models),
            "max_chars": self.max_chars,
            "mask_rules": list(self.mask_rules),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Permissions":
        return cls(
            allow_cloud=bool(data.get("allow_cloud", False)),
            allow_local=bool(data.get("allow_local", True)),
            mask_before_upload=bool(data.get("mask_before_upload", True)),
            allowed_models=tuple(data.get("allowed_models") or ()),
            max_chars=int(data.get("max_chars") or 20000),
            mask_rules=tuple(data.get("mask_rules") or DEFAULT_MASK_RULES),
        )


LOCAL_PROVIDER_KEYS = ("ollama", "custom_local", "lmstudio", "vllm")


def is_local_profile(profile) -> bool:  # noqa: ANN001
    """判断一份大模型配置是不是"本地模型"（本地地址或本地服务商预设）。"""
    provider = str(getattr(profile, "provider", "") or "").lower()
    if provider in LOCAL_PROVIDER_KEYS:
        return True
    endpoint = str(getattr(profile, "base_url", "") or "")
    return any(host in endpoint for host in ("127.0.0.1", "localhost", "0.0.0.0", "::1"))


def _setting_bool(getter: Callable[..., object], key: str, default: bool) -> bool:
    """读一个布尔设置。

    **坑**：`QSettings` 在 Windows 注册表里把 bool 写成字符串 `"true"/"false"`，
    直接 `bool(value)` 会把 `"false"` 当成 True（"关掉自动保存/人工确认"就失效了）。
    因此优先用 `type=bool` 让 Qt 自己转换，取不到再退回手工判断。
    """
    try:
        return bool(getter(key, default, type=bool))
    except TypeError:                       # 普通 dict / lambda 形态的 getter
        value = getter(key, default)
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def permissions_from_settings(getter: Callable[[str, object], object]) -> Permissions:
    """从设置读取权限（`getter` 形如 `settings.value`）。"""
    return Permissions(
        allow_cloud=_setting_bool(getter, "document/allow_cloud", False),
        allow_local=_setting_bool(getter, "document/allow_local", True),
        mask_before_upload=_setting_bool(getter, "document/mask_before_upload", True),
        allowed_models=tuple(
            item.strip() for item in str(getter("document/allowed_models", "") or "").split(",")
            if item.strip()),
        max_chars=int(getter("document/max_chars", 20000) or 20000),
        mask_rules=tuple(
            item.strip() for item in str(
                getter("document/mask_rules", ",".join(DEFAULT_MASK_RULES)) or "").split(",")
            if item.strip()) or DEFAULT_MASK_RULES,
    )


# ---------------------------------------------------------------- 水印与追踪


def new_tracking_id(prefix: str = "MD") -> str:
    """生成可追溯的导出标识（水印里带它就能查到是谁导出的哪一版）。"""
    stamp = time.strftime("%Y%m%d")
    return f"{prefix}-{stamp}-{secrets.token_hex(3).upper()}"


def build_watermark(text: str = "", *, footer: str = "", tracking_id: str = "",
                    enabled: Optional[bool] = None) -> WatermarkOptions:
    """组装导出水印参数（默认：只要填了内容就启用）。"""
    option = WatermarkOptions(text=text, footer=footer, tracking_id=tracking_id)
    option.enabled = bool(text or footer or tracking_id) if enabled is None else bool(enabled)
    return option


def describe_watermark(watermark: WatermarkOptions) -> str:
    if not watermark.enabled:
        return "未启用导出水印"
    return f"导出水印：{watermark.line or '（空）'}"


# ---------------------------------------------------------------- 审计


AUDIT_ACTIONS: dict[str, str] = {
    "open": "打开文档",
    "edit": "编辑保存",
    "export": "导出文件",
    "beautify": "格式美化",
    "merge": "文档合并",
    "split": "文档拆分",
    "loop": "AI 循环美化",
    "ai_call": "AI 调用",
    "tool": "工具调用",
    "rollback": "版本回滚",
    "mask": "脱敏",
    "ocr": "OCR 识别",
}


def audit_label(action: str) -> str:
    return AUDIT_ACTIONS.get(action, action)


def audit_line(record: dict) -> str:
    """把一条审计记录渲染成一行（界面表格/日志导出用）。"""
    created = record.get("created_at")
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(created or 0)))
    action = audit_label(str(record.get("action") or ""))
    target = str(record.get("target") or "")
    detail = str(record.get("detail") or "")
    return f"{stamp} · {action} · {target}" + (f" · {detail}" if detail else "")


__all__ = [
    "AUDIT_ACTIONS",
    "DEFAULT_MASK_RULES",
    "LOCAL_PROVIDER_KEYS",
    "MASK_RULES",
    "MaskReport",
    "MaskRule",
    "Permissions",
    "audit_label",
    "audit_line",
    "build_watermark",
    "describe_watermark",
    "is_local_profile",
    "mask_document",
    "mask_label",
    "mask_rule_choices",
    "mask_text",
    "new_tracking_id",
    "permissions_from_settings",
    "scan_sensitive",
]
