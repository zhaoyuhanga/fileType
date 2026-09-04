"""DocViewer 预览渲染测试：Markdown 排版 / JSON 与代码块语法高亮。"""
from __future__ import annotations

from modu_workbench.boards.doc_viewer import _render_json, _render_markdown, highlight_code


def test_highlight_json_code() -> None:
    html = highlight_code('{"name": "墨读", "count": 3}', "json")
    assert html.startswith("<pre")
    assert "color:" in html  # 内联 token 颜色（Pygments）


def test_json_preview_highlights_and_warns() -> None:
    ok = _render_json('{"a": 1}')
    assert "<pre" in ok
    assert "color:" in ok
    bad = _render_json('{"a": }')
    assert "JSON 无效" in bad
    assert "第 1 行" in bad


def test_markdown_preview_formats_and_highlights() -> None:
    source = "# 标题\n\n一段正文。\n\n```json\n{\"b\": 2}\n```\n\n- 甲\n- 乙\n"
    html = _render_markdown(source)
    assert "<h1>标题</h1>" in html
    assert "一段正文" in html
    assert "<pre" in html  # 代码块保留
    assert "color:" in html  # 代码块高亮
    assert "<li>甲</li>" in html or "<li>甲" in html
