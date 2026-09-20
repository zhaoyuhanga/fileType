"""墨软文档：插件（自定义工具）加载测试（需求 6.9「扩展」）。

插件的关键是"默认关闭、失败不致命、与内置工具同权" —— 三条都在这里锁住。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from modu_workbench.core.document import (
    TOOL_REGISTRY,
    DocumentLibrary,
    DocumentStorage,
    call_tool,
    parse_markdown,
    tool_names,
)
from modu_workbench.core.document import plugins as document_plugins


@pytest.fixture()
def plugin_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把插件目录指到临时目录（不动用户数据目录）。"""
    monkeypatch.setenv("MODU_DATA_DIR", str(tmp_path / "data"))
    folder = document_plugins.plugin_dir()
    return folder


@pytest.fixture()
def library(tmp_path: Path) -> DocumentLibrary:
    return DocumentLibrary(DocumentStorage(tmp_path / "modu.db"), output_dir=tmp_path / "out")


def _write_plugin(folder: Path, name: str, source: str) -> Path:
    path = folder / name
    path.write_text(source, encoding="utf-8")
    return path


GOOD_PLUGIN = '''
def register(api):
    def shift(context, args):
        hits = context.ir.replace_text(str(args.get("old", "")), str(args.get("new", "")))
        return api.ToolResult(bool(hits), f"插件替换 {hits} 处")

    api.register_tool(api.ToolSpec(
        name="plugin_shift", label="插件：替换", description="把 A 换成 B",
        parameters={"old": "原文字", "new": "新文字"}, category="edit", handler=shift))
'''


def test_plugins_disabled_by_default(plugin_dir: Path, library: DocumentLibrary) -> None:
    _write_plugin(plugin_dir, "好的插件.py", GOOD_PLUGIN)
    assert document_plugins.plugins_enabled(library.storage) is False
    assert document_plugins.load_plugins(storage=library.storage) == []
    assert "plugin_shift" not in tool_names()

    document_plugins.set_enabled(library.storage, True)
    assert document_plugins.plugins_enabled(library.storage) is True


def test_plugin_tool_is_registered_and_usable(plugin_dir: Path,
                                              library: DocumentLibrary) -> None:
    _write_plugin(plugin_dir, "好的插件.py", GOOD_PLUGIN)
    records = library.load_plugins(enabled=True)
    assert len(records) == 1 and records[0].ok is True
    assert records[0].tools == ["plugin_shift"]
    assert "plugin_shift" in tool_names()
    assert TOOL_REGISTRY["plugin_shift"].category == "edit"

    document = parse_markdown("# 标题\n\n旧词在这里。", title="文档")
    context = library.tool_context(document)
    result = call_tool("plugin_shift", context, {"old": "旧词", "new": "新词"})
    assert result.ok and "插件替换 1 处" in result.summary
    assert "新词" in context.ir.text()

    # 加载结果写进审计，便于追溯"这份文档被哪个插件改过"
    assert any(record["action"] == "plugin" for record in library.audit_records())
    assert any(record["action"] == "tool" and "插件：替换" in record["detail"]
               for record in library.audit_records())
    TOOL_REGISTRY.pop("plugin_shift", None)


def test_broken_plugin_does_not_break_others(plugin_dir: Path,
                                             library: DocumentLibrary) -> None:
    _write_plugin(plugin_dir, "语法错误.py", "def register(api):\n    return 1 +\n")
    _write_plugin(plugin_dir, "运行时错误.py", "def register(api):\n    this is not python\n")
    _write_plugin(plugin_dir, "没有入口.py", "value = 1\n")
    _write_plugin(plugin_dir, "注册时炸.py",
                  "def register(api):\n    raise RuntimeError('故意失败')\n")
    _write_plugin(plugin_dir, "没注册工具.py", "def register(api):\n    pass\n")
    _write_plugin(plugin_dir, "_跳过.py", GOOD_PLUGIN)
    _write_plugin(plugin_dir, "好的插件.py", GOOD_PLUGIN)

    records = {record.name: record for record in library.load_plugins(enabled=True)}
    assert set(records) == {"语法错误", "运行时错误", "没有入口", "注册时炸", "没注册工具",
                            "好的插件"}
    assert records["好的插件"].ok is True
    assert "SyntaxError" in records["语法错误"].error
    assert "执行失败" in records["运行时错误"].error
    assert "register(api)" in records["没有入口"].error
    assert "故意失败" in records["注册时炸"].error
    assert "没有注册任何工具" in records["没注册工具"].error
    assert "跳过" not in records, "下划线开头的文件应当被跳过"
    assert all(record.summary() for record in records.values())
    TOOL_REGISTRY.pop("plugin_shift", None)


def test_oversized_plugin_is_rejected(plugin_dir: Path, library: DocumentLibrary) -> None:
    big = plugin_dir / "巨大插件.py"
    big.write_text("# " + "x" * (document_plugins.MAX_PLUGIN_BYTES + 10), encoding="utf-8")
    records = library.load_plugins(enabled=True)
    assert len(records) == 1 and records[0].ok is False
    assert "过大" in records[0].error


def test_example_plugin_runs(plugin_dir: Path, library: DocumentLibrary) -> None:
    path = document_plugins.write_example_plugin()
    assert path.is_file() and path.name == document_plugins.EXAMPLE_NAME
    assert document_plugins.write_example_plugin() == path, "重复调用不应覆盖用户改过的文件"

    records = library.load_plugins(enabled=True)
    assert records and records[0].ok
    assert set(records[0].tools) == {"example_upper", "example_word_count"}

    document = parse_markdown("# 标题\n\nhello world。", title="文档")
    context = library.tool_context(document)
    assert call_tool("example_word_count", context, {}).ok
    assert call_tool("example_upper", context, {}).ok
    assert "HELLO WORLD" in context.ir.text()
    for name in ("example_upper", "example_word_count"):
        TOOL_REGISTRY.pop(name, None)


def test_plugin_helpers(plugin_dir: Path, library: DocumentLibrary) -> None:
    assert document_plugins.discover() == []
    assert "未启用" in document_plugins.status_text(library.storage)
    assert "插件目录" in document_plugins.capability_text()
    assert str(plugin_dir) in document_plugins.capability_text()

    _write_plugin(plugin_dir, "一个插件.py", GOOD_PLUGIN)
    files = document_plugins.discover()
    assert [path.name for path in files] == ["一个插件.py"]
    assert "发现 1 个" in document_plugins.status_text(library.storage)

    document_plugins.set_enabled(library.storage, True)
    assert "已启用" in document_plugins.status_text(library.storage)
    assert library.plugin_status() == document_plugins.status_text(library.storage)


def test_plugin_loader_survives_broken_storage(library: DocumentLibrary,
                                               plugin_dir: Path) -> None:
    """存储读不出设置时按"不启用"处理，不抛异常。"""

    class Broken:
        def get_setting(self, key: str, default: str = "") -> str:
            raise RuntimeError("数据库坏了")

    assert document_plugins.plugins_enabled(Broken()) is False
    assert document_plugins.load_plugins(storage=Broken()) == []
