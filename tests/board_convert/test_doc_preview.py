"""DocViewer 预览渲染测试：Markdown 排版（表格/代码/引用/列表/标题）/ JSON 高亮 / Qt 文档后处理。"""
from __future__ import annotations

from PySide6.QtGui import QTextBlockFormat
from PySide6.QtWidgets import QTextBrowser

from modu_workbench.boards.convert.doc_viewer import (
    _apply_document_style,
    _qt_style_html,
    _render_json,
    _render_markdown,
    highlight_code,
)

SAMPLE = """# 标题

一段**正文**，含 `内联代码` 与 [链接](https://example.com)。

## 表格

| 格式 | 动作 |
|---|---|
| txt | md |
| pdf | txt |
| json | csv |

> 引用块

---

1. 顶层
    - 嵌套

```python
print("hi")
```
"""


def test_highlight_json_code() -> None:
    html = highlight_code('{"name": "墨软", "count": 3}', "json")
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
    html = _render_markdown("# 标题\n\n一段正文。\n\n```json\n{\"b\": 2}\n```\n\n- 甲\n- 乙\n")
    assert "<h1" in html and "标题" in html
    assert "一段正文" in html
    assert "<pre" in html            # 代码块保留
    assert "color:" in html          # 代码块语法高亮
    assert "<li>甲" in html


def test_markdown_table_is_qt_styled() -> None:
    """表格：表头加粗+底色、隔行浅底、细边框（Qt 只认这些属性）。"""
    html = _render_markdown("| 格式 | 动作 |\n|---|---|\n| txt | md |\n| pdf | txt |\n")
    assert 'cellpadding="7"' in html and 'border="1"' in html
    assert "#eef1f8" in html.lower()            # 表头底色
    assert "<b>格式</b>" in html                # 表头加粗且文字没丢
    assert "#fafbfe" in html.lower()            # 隔行底色


def test_markdown_code_inline_and_block_are_styled() -> None:
    html = _render_markdown("行内 `code` 与代码块：\n\n```python\nprint(1)\n```\n")
    assert "font-family:Consolas" in html                     # 等宽字体
    assert "background-color:#f1f3f9" in html.lower()         # 行内代码淡底
    assert "#b02f60" in html.lower()                          # 行内代码品红字
    # 代码块外面套了带底色的单元格（Qt 对 pre 背景支持不稳）
    assert "#f6f8fc" in html.lower()


def test_markdown_blockquote_hr_and_nested_list() -> None:
    html = _render_markdown("> 引用\n\n---\n\n1. 顶层\n    - 嵌套\n")
    assert "#4f6ef7" in html.lower()          # 引用块左侧色条
    assert "#f7f9fd" in html.lower()          # 引用块浅底
    assert "<hr" not in html.lower()          # <hr> 已换成细表格行
    assert "<br" in html                      # 嵌套列表前的换行（否则 Qt 会挤在一行）


def test_qt_style_html_keeps_text(qapp) -> None:  # noqa: ANN001
    """属性化改写不能丢文本（曾经把表头文字清空过）。"""
    html = _qt_style_html("<table><tr><th>格式</th><td>txt</td></tr></table>")
    assert "格式" in html and "txt" in html


def test_apply_document_style_sets_line_height_and_table(qapp) -> None:  # noqa: ANN001
    browser = QTextBrowser()
    try:
        browser.setHtml(_render_markdown(SAMPLE))
        _apply_document_style(browser)
        document = browser.document()

        # 行距：QTextDocument 不支持 CSS line-height，必须用 blockFormat 设置
        block = document.firstBlock()
        assert block.blockFormat().lineHeight() == 165
        assert block.blockFormat().lineHeightType() == QTextBlockFormat.LineHeightTypes.ProportionalHeight.value

        # 表格确实进入了文档（用于单元格内边距/边框的后处理）
        from PySide6.QtGui import QTextTable

        tables = [frame for frame in _all_frames(document.rootFrame()) if isinstance(frame, QTextTable)]
        assert tables, "文档里应当有 QTextTable（Markdown 表格）"
        assert tables[0].format().cellPadding() == 7
    finally:
        browser.deleteLater()


def _all_frames(root):  # noqa: ANN001, ANN202
    """广度遍历所有子框架（含表格内的框架）。"""
    queue = list(root.childFrames())
    while queue:
        frame = queue.pop(0)
        yield frame
        queue.extend(frame.childFrames())
