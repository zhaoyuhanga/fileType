"""静态分析挖出的「未定义名」回归测试。

## 背景

接入 ruff（`pyproject.toml` 的 `[tool.ruff]`）后，`F821 undefined-name` 一次报出
**5 个真实的运行时崩溃点**。它们有个共同点：**只在特定操作下才会触发**，
所以既有的 1190 个用例一个都没覆盖到。

本文件把其中三个能用行为验证的钉死（另两个只是类型注解，由 ruff 闸门守）。

| 缺陷 | 触发操作 | 后果 |
|---|---|---|
| `beautify_page.py` 用 `QFileDialog` 但没导入 | 文档页「导入模板 / 导出模板」 | `NameError` 崩溃 |
| `merge.py` 用 `BeautifyOptions` 但没导入 | 合并时勾「生成目录」但**不**勾美化 | `NameError` 崩溃 |
| `handler.py` 的 `except requests.RequestException` 而 `requests` 从未导入 | 媒体代理预读上游失败时 | `NameError` 把本该"降级返回空"的路径变成崩溃 |
"""
from __future__ import annotations

from pathlib import Path

import pytest


# --------------------------------------------------------------------------- 1) 文档「导入/导出模板」


def test_beautify_page_exposes_qfiledialog() -> None:
    """`beautify_page` 必须能拿到 `QFileDialog`。

    「导入模板 / 导出模板」两个按钮直接调用 `QFileDialog.getOpenFileName/…`，
    模块没导入这个名字时，点一下就 `NameError`。
    """
    from modu_workbench.boards.document import beautify_page

    assert hasattr(beautify_page, "QFileDialog"), (
        "beautify_page 没有导入 QFileDialog —— 「导入模板/导出模板」会 NameError 崩溃"
    )


# --------------------------------------------------------------------------- 2) 合并：只生成目录


def test_merge_with_toc_only_does_not_raise() -> None:
    """合并时只勾「生成目录」（不勾美化）必须能正常跑完。

    `merge_documents()` 里 `elif options.add_toc:` 分支会 `BeautifyOptions(...)`，
    而该名字曾经没有导入 —— 这条路径必崩，且只有"只勾目录"这个组合才会走到。
    """
    from modu_workbench.core.document.merge import MergeOptions, merge_documents
    from modu_workbench.core.document.models import BLOCK_HEADING, Block, DocumentIR

    main = DocumentIR(title="主文档", format_key="markdown")
    main.blocks = [
        Block(kind=BLOCK_HEADING, text="一、总体要求", level=1),
        Block(kind=BLOCK_HEADING, text="1. 具体说明", level=2),
    ]
    other = DocumentIR(title="副文档", format_key="markdown")
    other.blocks = [Block(kind=BLOCK_HEADING, text="二、附则", level=1)]

    options = MergeOptions(
        mode="append",
        add_toc=True,          # ← 只勾这个
        unify_style=False,     # ← 不勾美化
        add_summary=False,
        dedupe=False,
    )
    result = merge_documents(main, other, options)   # 曾经在这里 NameError
    assert result.ir is not None
    assert result.ir.blocks, "合并结果为空"


# --------------------------------------------------------------------------- 3) 媒体代理预读失败降级


class _BrokenResponse:
    """`iter_content` 抛 requests 网络错的假响应。

    必须抛 `requests.RequestException`（而不是内置 `ConnectionError`）：
    `_peek()` 的 except 子句捕的就是它 —— 也正是这里曾经引用了未导入的
    `requests`，导致真出错时先炸 `NameError`。
    """

    content_type = "video/mp4"

    def iter_content(self, chunk_size: int = 1024):  # noqa: ANN201
        import requests

        raise requests.RequestException("上游断开了")


def test_media_proxy_peek_returns_empty_on_network_error() -> None:
    """预读失败必须**降级返回空**，而不是抛 `NameError`。

    `_peek()` 里写的是 `except requests.RequestException:`，但模块从未导入
    `requests` —— 一旦真的抛异常，Python 求值 except 子句时先炸 `NameError`，
    把"本该优雅降级"的路径变成崩溃。
    """
    from modu_workbench.services.media_proxy import handler as handler_module

    assert hasattr(handler_module, "requests"), (
        "handler.py 没导入 requests，但 except 子句引用了它"
    )
    peek = handler_module.MediaProxyHandler._peek      # noqa: SLF001  不构造 handler
    padded = peek(object(), _BrokenResponse())         # type: ignore[arg-type]
    assert padded == b""
