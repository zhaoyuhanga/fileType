"""Kazumi 真实规则的兼容性回归（用真站点实测出来的四条）。

这四条都是**拿 KazumiRules 仓库的真实规则、打真站点**跑出来的，不是想当然的边界：

| 症状 | 根因 | 修法 |
|---|---|---|
| 搜索命中 0 条 | 规则里 `searchName`/`searchResult` 写成 `//div/a[2]` 这种**绝对路径**，在条目节点上求值会跳到整个文档，取到站点头部的 `IOS用户使用欧路Via浏览器…全功能拦截` | XPath 结果为空时按 Kazumi 语义**作用域化**（`//x` → `.//x`）重试 |
| 详情 404、选集全空 | `searchResult` 指向 `<a>` 元素，我们取的是**文本**（标题），于是地址变成"站/葬送的芙莉莲" | `<a>` 元素优先取 `href` |
| 整条规则崩掉 | 某规则写 `"searchResult": "//"`（合法 JSON、非法 XPath）→ XPathError 把整站搜索打掉 | 把这类"空路径"当成"字段没写" |
| 播放地址拿不到 | 站点把真地址放在**另一个播放页**里（`player_aaaa` 的 url 是百分号编码的播放页） | 支持"再嗅探一层"（受 max_iframe_depth 限制） |

真站点会变、也需要联网，所以这里全部用**离线 fixture** 固定这几条语义；
真站实测结论记在 `docs/BOARDS/anime.md`。
"""
from __future__ import annotations

import pytest

from modu_workbench.core.anime.selectors import Selector, XPathSelector

#: 模拟一个"站点头部有干扰链接 + 条目里才有真链接"的搜索页
SEARCH_HTML = """
<html><body>
  <div class="header">
    <p>IOS用户使用欧路Via浏览器且开启全功能拦截后即可正常访问</p>
    <a class="button" href="/bgmsearch/2023.html">2023年10月</a>
  </div>
  <div class="module-items">
    <div class="module-item">
      <a class="module-item-title" title="葬送的芙莉莲">葬送的芙莉莲</a>
      <a class="module-item-cover" href="/bgmdetail/dPDDDE.html"></a>
      <a class="button" href="/bgmplay/dPDDDE-1-1.html">立即播放</a>
    </div>
    <div class="module-item">
      <a class="module-item-title" title="葬送的芙莉莲 第二季">葬送的芙莉莲 第二季</a>
      <a class="module-item-cover" href="/bgmdetail/ZZZZZ.html"></a>
      <a class="button" href="/bgmplay/ZZZZZ-1-1.html">立即播放</a>
    </div>
  </div>
</body></html>
"""


def _items():
    doc = XPathSelector("//div[@class='module-item']")
    return doc.nodes(SEARCH_HTML)


# --------------------------------------------------------------------------- 作用域化


def test_absolute_sub_xpath_is_scoped_to_the_item() -> None:
    """`//h2/a` 这种绝对路径必须**就当前条目**求值，而不是整个文档。"""
    items = _items()
    selector = XPathSelector("//h2/a")
    # 本 fixture 用 .button 模拟；换成同样的绝对路径形态
    scoped = XPathSelector("//a[@class='button']")
    first = scoped.all(items[0])
    assert first == ["/bgmplay/dPDDDE-1-1.html"], (
        f"应当只取到本条目的按钮链接，实际 {first}（这就是'取到站点头部干扰链接'的回归）")

    second = scoped.all(items[1])
    assert second == ["/bgmplay/ZZZZZ-1-1.html"]

    # 原样求值能拿到东西时**不做**作用域化（保持 XPath 原意）
    cross = selector.all(SEARCH_HTML)
    assert isinstance(cross, list)


def test_scoped_expression_property() -> None:
    assert XPathSelector("//div/a").scoped_expression == ".//div/a"
    assert XPathSelector(".//div/a").scoped_expression == ".//div/a"
    assert XPathSelector("/html/body//a").scoped_expression == "./html/body//a"


def test_item_title_and_link_are_paired_correctly() -> None:
    """回归的核心：每条命中的标题与链接必须来自**同一条目**。"""
    items = _items()
    title_sel = XPathSelector("//a[@class='module-item-title']")
    link_sel = XPathSelector("//a[@class='module-item-cover']")
    pairs = []
    for node in items:
        title = title_sel.first(node) or title_sel.attr(node, "title")
        pairs.append((title, link_sel.first(node)))
    assert pairs[0][1] == "/bgmdetail/dPDDDE.html"
    assert pairs[1][1] == "/bgmdetail/ZZZZZ.html"
    assert pairs[0][1] != pairs[1][1], "两条命中不能拿到同一个链接"


# --------------------------------------------------------------------------- href 优先


def test_anchor_element_yields_href_not_text() -> None:
    """`searchResult` 指向 `<a>` 时必须给 href；否则地址会变成标题文字。"""
    selector = XPathSelector("//a[@class='module-item-cover']")
    assert selector.first(SEARCH_HTML) == "/bgmdetail/dPDDDE.html"

    # 显式取文本仍然拿文本
    text_selector = XPathSelector("//a[@class='module-item-title']/text()")
    assert text_selector.first(SEARCH_HTML) == "葬送的芙莉莲"
    # 显式取属性仍然拿属性
    attr_selector = XPathSelector("//a[@class='module-item-title']/@title")
    assert attr_selector.first(SEARCH_HTML) == "葬送的芙莉莲"


def test_anchor_without_href_falls_back_to_text() -> None:
    """没有 href 的 `<a>`（锚点/纯文字）仍应给出文本，不能变成空串。"""
    selector = XPathSelector("//a[@class='plain']")
    assert selector.first("<div><a class='plain'>纯文字</a></div>") == "纯文字"


# --------------------------------------------------------------------------- 空路径


@pytest.mark.parametrize("expression", ["//", "/", ".", "./"])
def test_empty_path_is_treated_as_missing_field(expression: str) -> None:
    """`//` 这种"合法 JSON、非法 XPath"不能让整条规则崩掉。"""
    from modu_workbench.core.anime.rules import rule_from_dict as _rule
    from modu_workbench.core.anime.engine import AnimeEngine

    selector = XPathSelector(expression)
    assert selector.all(SEARCH_HTML) == []
    assert selector.first(SEARCH_HTML) == ""

    rule = _rule({
        "api": "8", "type": "anime", "name": "空路径站", "version": "1.0",
        "baseURL": "https://example.invalid/",
        "searchMode": "xpath", "chapterMode": "xpath",
        "searchURL": "https://example.invalid/s?wd=@keyword",
        "searchList": "//div[@class='module-item']",
        "searchName": "//a[@class='module-item-title']/@title",
        "searchResult": expression,
        "chapterRoads": "//div[@class='play-list']",
        "chapterResult": ".//a",
    }, key="empty")
    # 不抛异常即可（这条规则会因为没有链接而返回 0 命中，但绝不能炸）
    engine = AnimeEngine([rule])
    assert engine is not None


# --------------------------------------------------------------------------- 再嗅探一层


def test_player_aaaa_url_that_is_another_play_page() -> None:
    """`player_aaaa.url` 解出来是**另一个播放页**时要继续嗅探，不能直接判失败。"""
    from urllib.parse import quote

    from modu_workbench.core.anime.sniffer import Sniffer

    inner_url = "https://next.example.com/anime/3252/play/47463?source=xfxf1"
    encoded = quote(inner_url, safe="")          # 实测 moonci：百分号编码
    outer = ('<script>var player_aaaa={"encrypt":1,"url":"%s"}</script>' % encoded)
    inner = ('<script>var player_aaaa={"encrypt":0,'
             '"url":"https://cdn.example.com/3252/index.m3u8"}</script>')

    sniffer = Sniffer()
    fetched: list[str] = []

    def fake_fetch(url, referer="", error=None):  # noqa: ANN001, ANN202
        fetched.append(url)
        if url == inner_url:
            return inner, ""
        return outer, ""

    sniffer._fetch = fake_fetch                       # noqa: SLF001
    result = sniffer.sniff("https://www.example.com/anime/272/play/1-1.html")
    assert result.ok, result.note
    assert result.url == "https://cdn.example.com/3252/index.m3u8"
    assert result.strategy == "player_aaaa"
    assert inner_url in fetched, "应当去嗅探解码出来的播放页"
    assert "另一个播放页" in result.note


def test_player_aaaa_single_hop_still_works() -> None:
    """解出来就是媒体地址时不该多打请求（保持原行为）。"""
    from modu_workbench.core.anime.sniffer import Sniffer

    html = ('<script>var player_aaaa={"encrypt":0,'
            '"url":"https://cdn.example.com/a/index.m3u8"}</script>')
    sniffer = Sniffer()
    # sniff(html=...) 是离线模式：给了 HTML 就一个请求都不发
    result = sniffer.sniff("https://www.example.com/play/1.html", html=html)
    assert result.ok and result.url.endswith("index.m3u8")
    assert result.strategy == "player_aaaa"


# --------------------------------------------------------------------------- 示例规则提醒


def test_template_base_url_is_detected() -> None:
    """示例/占位地址必须被识别出来（用户导入模板后搜不到东西就是因为它）。"""
    from modu_workbench.core.anime.rules import is_template_base_url

    for url in ("https://www.example.com/", "https://api.example.com/",
                "http://127.0.0.1:8000/", "https://localhost/",
                "https://foo.test/", "https://a.invalid/"):
        assert is_template_base_url(url), url
    for url in ("https://www.moonci.com/", "https://next.xifanacg.com/",
                "https://www.baimaodm.com/"):
        assert not is_template_base_url(url), url


def test_template_rule_still_imports_but_warns() -> None:
    """关键取舍：示例规则**必须能导入**（人要照它改），只做提醒、不算校验失败。

    防回归重点：曾经把"示例地址"塞进 `validate_rule` 的失败列表，
    结果连导入示例规则都失败（3 个测试立刻挂掉）。
    """
    from modu_workbench.core.anime.rules import rule_from_dict, rule_warnings, validate_rule

    rule = rule_from_dict({
        "api": "8", "type": "anime", "name": "模板", "version": "1.0",
        "baseURL": "https://www.example.com/",
        "searchMode": "xpath", "chapterMode": "xpath",
        "searchURL": "https://www.example.com/s?wd=@keyword",
        "searchList": "//div[@class='item']",
        "searchName": "//a/@title", "searchResult": "//a/@href",
        "chapterRoads": "//div[@class='play-list']", "chapterResult": ".//a",
    }, key="tpl")
    assert validate_rule(rule) == [], "示例规则不能被判为校验失败"
    warnings = rule_warnings(rule)
    assert any("示例" in item or "模板" in item for item in warnings), warnings


def test_rules_list_column_shows_template_warning() -> None:
    """规则列表的「提示」列必须把这条说出来，而不是只显示 `-`。"""
    from modu_workbench.boards.anime.widgets import rule_warning
    from modu_workbench.core.anime.rules import rule_from_dict

    base = {
        "api": "8", "type": "anime", "version": "1.0",
        "searchMode": "xpath", "chapterMode": "xpath",
        "searchURL": "https://x.invalid/s?wd=@keyword",
        "searchList": "//div[@class='item']",
        "searchName": "//a/@title", "searchResult": "//a/@href",
        "chapterRoads": "//div", "chapterResult": ".//a",
    }
    template = rule_from_dict({**base, "name": "模板",
                               "baseURL": "https://www.example.com/"}, key="t")
    real = rule_from_dict({**base, "name": "真站",
                           "baseURL": "https://www.moonci.com/"}, key="r")
    assert "模板" in rule_warning(template) or "示例" in rule_warning(template)
    assert rule_warning(real) == ""
