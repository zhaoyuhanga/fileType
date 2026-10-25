"""书库在线源（`core/book/online.py`）的解析与容错测试。

## 为什么这个文件重要

`online.py` 是 485 行的**网页抓取 + HTML 解析**模块（搜索、目录、章节、整本下载、
编码识别），而它此前**一个测试都没有**。这类代码的风险和别的模块不同：

- 它面对的是**第三方站点的真实 HTML**（结构随时可能变），不能靠"约定"保证正确；
- 它的失败方式是"悄悄解析出 0 条结果"，用户看到的是"搜不到"而不是报错；
- 解析规则大量依赖正则与启发式（标题长度、排除导航词、`/book/数字` 约定）。

所以这里**不联网**，全部用固定 HTML 片段断言解析行为；网络层则用假 `_get` 注入。
凡是"站点改版就会坏"的地方，都用注释写明**当前规则是什么、为什么**，
这样下次改版时能一眼看出该改哪条。

约定（与 `tests/README.md` 一致）：不允许真的联网，不允许依赖本机设置。
"""
from __future__ import annotations

import pytest

from modu_workbench.core.book import online as online_module
from modu_workbench.core.book.online import (
    ChapterInfo,
    NetworkError,
    NovelDetail,
    NovelInfo,
    NovelSource,
)

# --------------------------------------------------------------------------- 夹具


class FakeResponse:
    """最小可用的 `requests.Response` 替身（只实现 online.py 用到的属性）。"""

    def __init__(self, text: str = "", *, status: int = 200, content: bytes | None = None,
                 content_type: str = "text/html; charset=utf-8", encoding: str = "utf-8"):
        self._text = text
        self.status_code = status
        self.content = content if content is not None else text.encode(encoding, "ignore")
        self.headers = {"Content-Type": content_type}
        self.apparent_encoding = "utf-8"


def make_source(html: str = "") -> NovelSource:
    """造一个不会真的联网的源：`_get` 直接返回给定 HTML。"""
    source = NovelSource("https://example.com")
    source._get = lambda url: html          # type: ignore[method-assign]
    return source


# --------------------------------------------------------------------------- 搜索解析


SEARCH_HTML = """
<html><body>
  <a href="/">首页</a>
  <a href="/login">登录</a>
  <a href="/book/10086/">凡人修仙传</a>
  <a href="/book/10087/">作者：忘语 斗破苍穹</a>
  <a href="/novel/20001/">诡秘之主</a>
  <a href="/book/10086/">凡人修仙传</a>          <!-- 重复项，应去重 -->
  <a href="/book/99999/">短</a>                   <!-- 太短，应排除 -->
  <a href="https://other-site.com/book/123/">别站的书</a>  <!-- 站外，应排除 -->
  <a href="/category/xuanhuan">玄幻</a>           <!-- 导航词，应排除 -->
  <a href="/book/777/">This Is English Only</a>   <!-- 无汉字，应排除 -->
</body></html>
"""


def test_search_keeps_only_book_like_links() -> None:
    """搜索只保留"站内 + 像小说 + 指向 /book|novel/数字"的链接。

    这条同时钉住了四条排除规则（顺序无关）：
    1. 站外链接不要；2. 少于 4 个汉字不要；3. 纯英文不要；
    4. 导航/分类词（首页、登录、玄幻…）不要。
    """
    items = make_source(SEARCH_HTML)._parse_search(SEARCH_HTML)
    titles = [item.title for item in items]

    # 正常项保留
    assert any("凡人修仙传" in title for title in titles)
    assert any("诡秘之主" in title for title in titles)
    # 排除项
    assert not any(title in ("首页", "登录", "玄幻") for title in titles), "导航链接被当成了书"
    assert not any("短" == title for title in titles), "过短标题应当被排除"
    assert not any("English" in title for title in titles), "纯英文链接应当被排除"
    assert not any("别站" in title for title in titles), "站外链接应当被排除"


def test_search_dedupes_by_title() -> None:
    """同一本书出现多次只保留一条（按标题去重）。"""
    items = make_source()._parse_search(SEARCH_HTML)
    titles = [item.title for item in items if "凡人" in item.title]
    assert len(titles) == 1, f"重复标题没有被去重：{titles}"


def test_search_extracts_author_from_neighbour_text() -> None:
    """作者从链接所在父节点的「作者：xxx」里提取。"""
    # 标题必须 ≥4 个汉字才会被当作书（`_parse_search` 的真实规则）
    html = """<html><body>
      <div class="item"><a href="/book/1/">测试书名甲</a> 作者：张三</div>
    </body></html>"""
    items = make_source()._parse_search(html)
    assert len(items) == 1, f"应当解析出 1 条，实际 {[i.title for i in items]}"
    assert items[0].author == "张三"
    assert items[0].title == "测试书名甲"


def test_search_ignores_titles_shorter_than_four_chars() -> None:
    """标题少于 4 个汉字不当作书 —— 这条规则专为过滤"首页/登录/更多"这类导航项。"""
    html = """<html><body>
      <a href="/book/1/">三字名</a>
      <a href="/book/2/">四个汉字</a>
    </body></html>"""
    titles = [item.title for item in make_source()._parse_search(html)]
    assert "三字名" not in titles
    assert "四个汉字" in titles


def test_search_caps_result_count() -> None:
    """结果上限 30 条（防止大站点一次返回上千条把界面卡住）。"""
    html = "<html><body>" + "".join(
        f'<a href="/book/{i}/">测试书名编号{i:03d}</a>' for i in range(50)
    ) + "</body></html>"
    items = make_source()._parse_search(html)
    assert len(items) == 30


def test_search_returns_empty_on_blank_keyword() -> None:
    """空关键词直接返回空，不发请求。"""
    source = NovelSource("https://example.com")

    def boom(url: str) -> str:  # noqa: ANN001
        raise AssertionError("空关键词不应该发起请求")

    source._get = boom          # type: ignore[method-assign]
    assert source.search("   ") == []


def test_search_falls_through_broken_endpoints() -> None:
    """某个搜索端点报错时，应继续试下一个端点，而不是整体失败。

    很多站点的搜索路径各不相同，代码里准备了 7 个候选（`_search_urls`）。
    """
    source = NovelSource("https://example.com")
    calls: list[str] = []

    def fake_get(url: str) -> str:
        calls.append(url)
        if len(calls) < 3:
            raise NetworkError("HTTP 404")
        return SEARCH_HTML

    source._get = fake_get     # type: ignore[method-assign]
    items = source.search("测试")
    assert items, "前两个端点失败后应当继续尝试并拿到结果"
    assert len(calls) >= 3


def test_search_returns_empty_when_all_endpoints_fail() -> None:
    """全部端点都失败时返回空列表（界面显示"没搜到"，不抛异常）。"""
    source = NovelSource("https://example.com")

    def always_fail(url: str) -> str:  # noqa: ANN001
        raise NetworkError("boom")

    source._get = always_fail   # type: ignore[method-assign]
    assert source.search("测试") == []


# --------------------------------------------------------------------------- 编码识别


@pytest.mark.parametrize(
    ("apparent", "content_type", "expected"),
    [
        ("GB2312", "text/html", "gbk"),        # 中文小说站最常见
        ("gb18030", "text/html", "gbk"),
        ("UTF-8", "text/html", "utf-8"),
        ("ascii", "text/html; charset=gbk", "gbk"),
        ("ascii", "text/html", "gb18030"),     # 兜底：国内站多点 GB 系
    ],
)
def test_detect_encoding(apparent: str, content_type: str, expected: str) -> None:
    """编码识别优先级：apparent_encoding → Content-Type → 兜底 gb18030。

    乱码是书库最典型的用户投诉（"打开是方块字"），所以这条必须钉住。
    """
    response = FakeResponse(content_type=content_type)
    response.apparent_encoding = apparent
    assert NovelSource._detect_encoding(response) == expected


def test_detect_encoding_prefers_utf8_for_bom() -> None:
    """带 UTF-8 BOM 的内容按 utf-8-sig 解（否则首字符会显示成 ''）。"""
    response = FakeResponse(content=b"\xef\xbb\xbfhello")
    response.apparent_encoding = "ascii"
    assert NovelSource._detect_encoding(response) == "utf-8-sig"


# --------------------------------------------------------------------------- URL 拼装


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("/book/1/", "https://example.com/book/1/"),
        ("book/1/", "https://example.com/book/1/"),
        ("https://cdn.example.org/x", "https://cdn.example.org/x"),
        ("", "https://example.com"),
    ],
)
def test_make_url(relative: str, expected: str) -> None:
    """相对路径补全为绝对地址；绝对地址原样返回。"""
    assert make_source()._make_url(relative) == expected


def test_base_url_trailing_slash_is_stripped() -> None:
    """`base_url` 末尾的斜杠会被去掉（否则会拼出 `//book/1`）。"""
    assert NovelSource("https://example.com/").base_url == "https://example.com"


# --------------------------------------------------------------------------- HTTP 错误


def test_http_error_becomes_network_error_with_url() -> None:
    """HTTP 4xx/5xx 抛 `NetworkError`，且消息里**带 URL**（便于用户判断是哪个源挂了）。"""
    source = NovelSource("https://example.com")

    class Session:
        headers: dict = {}

        def get(self, url, timeout=None):  # noqa: ANN001
            return FakeResponse(status=404)

    source._session = Session()   # type: ignore[assignment]
    with pytest.raises(NetworkError) as info:
        source._get("https://example.com/book/1/")
    assert "404" in str(info.value)
    assert "book/1" in str(info.value)


# --------------------------------------------------------------------------- 目录解析


CATALOG_HTML = """
<html><body>
  <h1>示例小说</h1>
  <div class="info">作者：李四</div>
  <div id="list">
    <a href="/book/1/100.html">第一章 起</a>
    <a href="/book/1/101.html">第二章 承</a>
    <a href="/book/1/102.html">第三章 转</a>
    <a href="/book/1/103.html">第四章 合</a>
  </div>
</body></html>
"""


def test_get_catalog_parses_chapters_in_order() -> None:
    """目录按页面顺序给出章节，序号从 0 递增（阅读进度依赖这个顺序）。"""
    source = make_source(CATALOG_HTML)
    detail = source.get_catalog("https://example.com/book/1/")

    assert isinstance(detail, NovelDetail)
    assert len(detail.chapters) >= 4
    indices = [chapter.index for chapter in detail.chapters]
    assert indices == sorted(indices), "章节序号必须单调递增"
    assert indices[0] == 0
    assert detail.chapters[0].title.startswith("第一章")


def test_get_catalog_returns_detail_type_even_without_chapters() -> None:
    """解析不出章节时也要返回 `NovelDetail`（而不是 None/抛异常）。

    界面依赖这个契约：拿不到目录就提示"该页没有可读章节"。
    """
    source = make_source("<html><body><p>空页面</p></body></html>")
    detail = source.get_catalog("https://example.com/book/2/")
    assert isinstance(detail, NovelDetail)
    assert detail.chapters == []


# --------------------------------------------------------------------------- 正文清理


def test_clean_text_collapses_whitespace_and_unescapes() -> None:
    """`_clean_text` 压缩空白并还原 HTML 实体（`&amp;` → `&`）。"""
    text = NovelSource._clean_text("  李四  &amp;  王五\n\t合著 ")
    assert text == "李四 & 王五 合著"


def test_clean_content_drops_consecutive_blank_lines() -> None:
    """正文连续空行压成一个（章节页常有大量空行，留着会把阅读区撑得很难看）。"""
    raw = "第一段\n\n\n\n第二段\n\n\n"
    cleaned = NovelSource._clean_content(raw)
    assert "\n\n\n" not in cleaned
    assert "第一段" in cleaned and "第二段" in cleaned


def test_clean_content_keeps_normal_paragraph_breaks() -> None:
    """正常段落之间的单个空行必须保留（这是正文排版本身）。"""
    raw = "第一段\n\n第二段"
    assert NovelSource._clean_content(raw) == "第一段\n\n第二段"


# --------------------------------------------------------------------------- 数据契约


def test_dataclasses_have_expected_defaults() -> None:
    """数据结构字段顺序/默认值被界面与下载逻辑依赖，改动要有意识。"""
    info = NovelInfo(title="t", author="a", url="u")
    assert info.latest_chapter == "" and info.description == ""

    chapter = ChapterInfo(title="c", url="u", index=3)
    assert chapter.index == 3

    detail = NovelDetail(title="t", author="a", description="d", url="u")
    assert detail.chapters == []
    assert detail.download_url == "" and detail.txt_page_url == ""


def test_download_progress_defaults() -> None:
    progress = online_module.DownloadProgress(current=0, total=10)
    assert progress.failed == 0
    assert progress.done is False
    assert progress.current_title == ""
