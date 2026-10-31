"""设置闸门：`QSettings.value()` 的**高危读取**必须显式声明 `type=`。

## 为什么要这道闸门

`QSettings` 存取**不做类型保持**：`setValue(key, False)` 之后 `value(key)`
读回来是字符串 `'false'`，而 `'false'` 在 Python 里是**真值**。

真实踩过的坑（`boards/video/widgets.py::video_auto_switch_pref`）：
用户在设置页关掉「卡顿自动换源」，程序读到的却是 `'false'` → `bool('false') == True`
→ 自动换源又被打开。**设置页显示"关闭"，实际行为是"开启"** ——
这种 bug 用户很难描述清楚。

## 判定规则（按风险分级，不是一刀切）

| 情况 | 判定 | 理由 |
|---|---|---|
| `int(...)` / `float(...)` / `str(...)` 包住 | ✅ 允许 | 显式转换，类型确定 |
| 布尔用途（赋给 `setChecked`、`bool(...)`、`if`）且**没写 type** | ❌ 报错 | `'false'` 是真值 —— 就是上面那个坑 |
| 无任何转换、直接拿去用 | ❌ 报错 | 拿到什么类型全看 Qt |
| 键名是变量 | 跳过 | 静态无法判断，交给 code review |

`doc/`、`video/` 等设置页里大量 `int(self._settings.value(..., 16) or 16)`
属于第一类，是合规的 —— 本闸门**不会**要求它们全加 `type=`（那是噪音）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "modu_workbench"

VALUE_CALL = re.compile(r'\.value\(\s*["\']([^"\']+)["\']')
#: 显式数值/字符串转换（这些能安全兜住 QSettings 的字符串返回）
SAFE_WRAP = re.compile(r"\b(int|float|str)\s*\(")
#: 布尔语境：说明"读到字符串 'false' 会变成真"是真问题
BOOL_CONTEXT = re.compile(r"setChecked|\.setEnabled|\bbool\s*\(|\bif\s+|\bnot\s+|isChecked\b")


def _scan() -> tuple[list[str], list[str]]:
    """返回 (高危, 布尔风险) 两组问题。"""
    high_risk: list[str] = []
    bool_risk: list[str] = []
    for path in SRC.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        relative = str(path.relative_to(SRC)).replace("\\", "/")
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines, 1):
            match = VALUE_CALL.search(line)
            if not match:
                continue
            if "type=" in line:
                continue
            # 转换常写成跨行：`int(\n    settings.value(...)\n)`。
            # 因此把"本行 + 前两行"合成一个窗口再判断有没有显式转换。
            window = " ".join(lines[max(0, index - 3): index])
            has_safe = bool(SAFE_WRAP.search(line)) or bool(SAFE_WRAP.search(window))
            has_bool_ctx = bool(BOOL_CONTEXT.search(line))

            # 另一种合规写法：先赋给变量，紧接着在 try 里转换
            #     value = settings.value("k", 120)
            #     try: return int(value)
            #     except (TypeError, ValueError): return 120
            assigned = re.match(r"\s*(\w+)\s*=\s*.*\.value\(", line)
            if assigned and not has_safe:
                var = assigned.group(1)
                following = " ".join(lines[index: index + 6])
                if re.search(rf"\b(int|float|str)\s*\(\s*{re.escape(var)}\s*\)", following):
                    has_safe = True
            where = f"{relative}:{index}  {match.group(1)}"

            if has_bool_ctx:
                # 布尔语境即使外层有 int()/str() 也危险：比如 `if value(...) or 默认`
                bool_risk.append(f"{where}  → {line.strip()[:74]}")
            elif not has_safe:
                high_risk.append(f"{where}  → {line.strip()[:74]}")
    return high_risk, bool_risk


def test_untyped_settings_reads_have_explicit_conversion() -> None:
    """没写 `type=` 的读取，至少要有 `int()/float()/str()` 明确转换。"""
    high_risk, _ = _scan()
    assert high_risk == [], (
        "以下设置读取既没声明 type，也没有显式数值/字符串转换：\n  "
        + "\n  ".join(high_risk)
    )


def test_bool_settings_declare_type() -> None:
    """用于布尔判断的设置**必须**写 `type=bool`（否则 False 会读成真值）。"""
    _, bool_risk = _scan()
    assert bool_risk == [], (
        "以下设置用在布尔语境却没声明 type —— 存 False 会读回 'false'（真值），"
        "导致「设置里关了、实际还是开着」：\n  " + "\n  ".join(bool_risk)
    )


def test_bool_setting_roundtrip() -> None:
    """真机验证 Qt 的行为：存 False 再读必须还是 False。"""
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])  # noqa: F841
    settings = QSettings("ModuWorkbench", "gate-probe")
    try:
        settings.setValue("probe/enabled", False)
        settings.sync()
        assert settings.value("probe/enabled", True, type=bool) is False

        raw = settings.value("probe/enabled")
        assert not isinstance(raw, bool), "Qt 行为变了（现在保持 bool），本闸门可放宽"
        assert bool(raw) is True, "不声明 type 时 falsy 会变成真值 —— 这正是要防的坑"

        settings.setValue("probe/enabled", True)
        settings.sync()
        assert settings.value("probe/enabled", False, type=bool) is True
    finally:
        settings.remove("probe/enabled")
