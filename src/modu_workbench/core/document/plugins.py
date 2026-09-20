"""墨软文档：插件（自定义工具）加载（需求 6.9「扩展：支持插件、工具注册」）。

设计取舍：

- **插件就是一个 Python 文件**，放在 `数据目录/document/plugins/` 下，
  必须定义 `register(api)`；`api.register_tool(ToolSpec(...))` 注册后，
  该工具会立刻出现在「权限安全」的工具列表、AI 的工具清单和调用日志里 ——
  与内置工具完全同权（同一套 `call_tool`）。
- **默认关闭**：插件是本机可执行代码，加载它等于让这段代码以应用权限运行。
  因此默认不加载，必须在「设置 → 文档」里显式勾选「启用插件目录」。
- **失败不致命**：某个插件语法错误/抛异常/没写 `register`，只记录原因，
  不影响其它插件与整个板块（`PluginRecord.error` 会显示在设置页与审计日志里）。
- **不联网、不扫描别处**：只读插件目录里的 `*.py`，并且限制单文件大小。

给插件作者的接口（`PluginApi`）：

    # plugins/我的工具.py
    def register(api):
        def handler(context, args):
            # context.ir 是当前文档（DocumentIR），args 是参数字典
            hits = context.ir.replace_text(str(args.get("old", "")), str(args.get("new", "")))
            return api.ToolResult(bool(hits), f"替换 {hits} 处")
        api.register_tool(api.ToolSpec(
            name="my_replace", label="我的替换", description="把 A 换成 B",
            parameters={"old": "原文字", "new": "新文字"}, category="edit",
            handler=handler))
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from modu_workbench.core.platform.paths import document_dir

from .tools import (
    TOOL_REGISTRY,
    ToolResult,
    ToolSpec,
    register_tool as register_document_tool,
    tool_names,
)

PLUGIN_DIR_NAME = "plugins"
PLUGIN_SUFFIX = ".py"
#: 单文件大小上限（插件应当是"小工具"，不是整套框架）
MAX_PLUGIN_BYTES = 200_000
#: 设置键：是否启用插件目录
SETTING_KEY = "enable_plugins"

EXAMPLE_NAME = "example_tool.py"
EXAMPLE_SOURCE = '''"""墨软文档示例插件：注册两个小工具。

放到「设置 → 文档 → 插件目录」里，勾选「启用插件目录」后即可在
「权限安全 → 工具」与 AI 工具清单里看到它们。
"""
from __future__ import annotations


def register(api):
    """入口：插件必须定义这个函数。"""

    def upper_case(context, args):
        """把文档里英文单词统一成大写（示例：读文档 + 改文档）。"""
        hits = 0
        for block in context.ir.blocks:
            if block.is_textual and block.text:
                new_text = block.text.upper()
                if new_text != block.text:
                    block.text = new_text
                    hits += 1
        return api.ToolResult(bool(hits), f"已把 {hits} 段文字转成大写")

    api.register_tool(api.ToolSpec(
        name="example_upper", label="示例：转大写", description="把正文英文统一成大写",
        parameters={}, category="edit", handler=upper_case))

    def word_count(context, args):
        """统计字数（示例：只读工具）。"""
        text = context.ir.text()
        chars = len([ch for ch in text if not ch.isspace()])
        return api.ToolResult(True, f"当前文档 {chars} 字", {"chars": chars})

    api.register_tool(api.ToolSpec(
        name="example_word_count", label="示例：统计字数", description="统计文档非空白字符数",
        parameters={}, category="read", handler=word_count))
'''


@dataclass
class PluginApi:
    """插件可用的接口（刻意只暴露"注册工具"这一件事）。

    `ToolSpec` / `ToolResult` 是给插件构造声明的类（不带类型注解，因此不会被
    dataclass 当成字段）；`register_tool` 是真正的注册入口，注册过的名字记在 `registered`。
    """

    version: str = "1"
    registered: list[str] = field(default_factory=list)

    ToolSpec = ToolSpec
    ToolResult = ToolResult

    def register_tool(self, spec: ToolSpec) -> ToolSpec:
        """注册一个工具（与内置工具同权：AI 与界面都能调用）。"""
        result = register_document_tool(spec)
        if spec.name not in self.registered:
            self.registered.append(spec.name)
        return result

    def tool_names(self) -> list[str]:
        """当前已注册的全部工具名（含内置）。"""
        return tool_names()


@dataclass
class PluginRecord:
    """一个插件的加载结果。"""

    name: str
    path: str
    ok: bool = False
    tools: list[str] = field(default_factory=list)
    error: str = ""
    size_bytes: int = 0

    def summary(self) -> str:
        if self.ok:
            return f"{self.name}：注册 {len(self.tools)} 个工具（{'、'.join(self.tools)}）"
        return f"{self.name}：加载失败 —— {self.error}"


def plugin_dir() -> Path:
    """插件目录（不存在时创建）。"""
    path = document_dir() / PLUGIN_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def discover() -> list[Path]:
    """插件目录里的 `*.py`（按文件名排序，跳过下划线开头的文件）。"""
    return sorted(path for path in plugin_dir().glob(f"*{PLUGIN_SUFFIX}")
                  if path.is_file() and not path.name.startswith("_"))


def plugins_enabled(storage=None) -> bool:
    """是否启用插件目录（默认关闭：加载插件等于执行本机代码）。"""
    if storage is None:
        return False
    try:
        return storage.get_setting(SETTING_KEY, "0") == "1"
    except Exception:  # noqa: BLE001  配置读不出来时按"不启用"处理
        return False


def set_enabled(storage, enabled: bool) -> None:  # noqa: ANN001
    storage.set_setting(SETTING_KEY, "1" if enabled else "0")


def load_file(path: str | Path, *, api: Optional[PluginApi] = None) -> PluginRecord:
    """加载单个插件文件（任何异常都转成 `PluginRecord.error`，不向外抛）。"""
    source_path = Path(path)
    record = PluginRecord(name=source_path.stem, path=str(source_path))
    try:
        record.size_bytes = source_path.stat().st_size
    except OSError as error:
        record.error = f"读取失败：{error}"
        return record
    if record.size_bytes > MAX_PLUGIN_BYTES:
        record.error = (f"文件过大（{record.size_bytes / 1024:.0f} KB > "
                        f"{MAX_PLUGIN_BYTES / 1024:.0f} KB）：插件只应注册小工具")
        return record
    try:
        code = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        record.error = f"读取失败：{error}"
        return record

    bridge = api or PluginApi()
    namespace: dict = {
        "__name__": f"modu_doc_plugin_{source_path.stem}",
        "__file__": str(source_path),
        "__doc__": None,
    }
    try:
        exec(compile(code, str(source_path), "exec"), namespace)   # noqa: S102 插件即代码
    except Exception as error:  # noqa: BLE001  语法错误 / 顶层异常
        record.error = f"{error.__class__.__name__}：{error}"
        return record

    register = namespace.get("register")
    if not callable(register):
        record.error = "缺少 register(api) 入口函数（请参考示例插件）"
        return record
    try:
        register(bridge)
    except Exception as error:  # noqa: BLE001  注册过程出错
        record.error = f"register() 执行失败：{error}"
        return record

    record.tools = [name for name in bridge.registered if name in TOOL_REGISTRY]
    record.ok = bool(record.tools)
    if not record.ok:
        record.error = "没有注册任何工具（请在 register(api) 里调用 api.register_tool）"
    return record


def load_plugins(*, enabled: Optional[bool] = None, storage=None) -> list[PluginRecord]:
    """加载插件目录里的全部插件。

    `enabled=None` 时按设置决定；返回每个插件的加载结果（含失败原因）。
    """
    if enabled is None:
        enabled = plugins_enabled(storage)
    if not enabled:
        return []
    records = [load_file(path) for path in discover()]
    if storage is not None:
        for record in records:
            try:
                storage.add_audit("plugin", record.path, record.summary(), "local")
            except Exception:  # noqa: BLE001  审计失败不影响加载结果
                pass
    return records


def write_example_plugin(name: str = EXAMPLE_NAME, *, overwrite: bool = False) -> Path:
    """生成示例插件（界面「生成示例插件」用它，用户有个能跑起来的起点）。"""
    target = plugin_dir() / (name if name.endswith(PLUGIN_SUFFIX) else f"{name}{PLUGIN_SUFFIX}")
    if target.exists() and not overwrite:
        return target
    target.write_text(EXAMPLE_SOURCE, encoding="utf-8")
    return target


def capability_text() -> str:
    return (f"插件目录：{plugin_dir()}（默认关闭）。插件是普通 Python 文件，"
            "定义 register(api) 即可注册自定义工具，与内置工具同权；"
            "加载失败只记录原因，不影响板块其它功能。")


def status_text(storage=None) -> str:
    """设置页/板块页显示的一行状态。"""
    enabled = plugins_enabled(storage)
    files = discover()
    state = "已启用" if enabled else "未启用（默认关闭）"
    detail = f"，发现 {len(files)} 个插件文件" if files else "，目录为空"
    return f"插件：{state}{detail}｜{plugin_dir()}"


def now_stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


__all__ = [
    "EXAMPLE_NAME",
    "EXAMPLE_SOURCE",
    "MAX_PLUGIN_BYTES",
    "PLUGIN_DIR_NAME",
    "PLUGIN_SUFFIX",
    "SETTING_KEY",
    "PluginApi",
    "PluginRecord",
    "capability_text",
    "discover",
    "load_file",
    "load_plugins",
    "now_stamp",
    "plugin_dir",
    "plugins_enabled",
    "set_enabled",
    "status_text",
    "write_example_plugin",
]
