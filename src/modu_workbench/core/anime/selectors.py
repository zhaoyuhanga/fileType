"""选择器：XPath / CSS / JSONPath 三种求值器，统一接口。

为什么要统一：规则引擎里同一段流程（搜索列表、作品名、链接、线路、剧集）
在 XPath 模式与 API 模式下都是"从某个东西里取一组/一个值"，差异只在**求值方式**。
把差异收进这里，`engine.py` 就只有一份逻辑。

统一接口（`Selector`）：

    first(data) -> str        取第一个值（属性/text/JSON 标量）
    all(data) -> list[str]    取所有值
    nodes(data) -> list       取所有节点（XPath 元素 / JSON 对象），供链式取值

支持的选择器语法（与 Kazumi 兼容 + 我们的扩展）：

| 写法 | 含义 |
|---|---|
| `//div[@class='x']/a/@href` | XPath（属性；`text()` 取文本） |
| `css:div.item > a` | **我们的扩展**：CSS 选择器（`soupsieve`，已随 beautifulsoup4 安装） |
| `$.data.list[*]` | JSONPath 子集：`$` / `.a.b` / `[*]` / `[0]` / `['k']` / `["k"]` |
| 空选择器 | 表示"当前对象自身"（API 模式里 `episodeUrlPath` 常为空） |

**必须明确不支持**（与 Kazumi 相同，且给出可读错误而不是静默返回空）：
JSONPath 的递归 `..`、过滤 `[?(...)]`、切片 `[0:2]`、函数 `length()`。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

#: `css:` 前缀（我们的扩展）
CSS_PREFIX = "css:"


class SelectorError(ValueError):
    """选择器语法错误（含"不支持的语法"的明确说明）。"""


def looks_like_xpath(expression: str) -> bool:
    text = (expression or "").strip()
    return text.startswith("/") or text.startswith("(") or text.startswith(".")


def looks_like_jsonpath(expression: str) -> bool:
    return (expression or "").strip().startswith("$")


# --------------------------------------------------------------------------- XPath


class XPathSelector:
    """XPath 1.0（lxml）。兼容 Kazumi 要求的"以 `//` 开头"，同时更宽松。

    **以 `//` 开头的表达式在子节点上求值时会"就地作用域化"**（自动补成 `.//`）。
    这是照 Kazumi 的语义来的，也是真机踩出来的：

    Kazumi 规则里的 `searchName` / `searchResult` / `chapterResult` 普遍写成
    `//div/a[2]`、`//a` 这种**绝对路径**。XPath 规范里 `//x` 等价于
    `/descendant-or-self::node()/x`，在某个元素上求值时会跳到**整个文档**去找 ——
    于是每条搜索结果拿到的都是"全页第一个匹配"，实测表现为
    `searchName` 取到站点头部的 `IOS用户使用欧路Via浏览器…全功能拦截`、
    `searchResult` 取到空 `<a>`，标题全是垃圾、链接全空 → **搜索命中 0 条**。

    处理顺序：**先按原样求值，只有结果为空时才用作用域化表达式重试**。
    这样 `//a/@href`（确实要跨节点取）这类写法不受影响，而
    "本想取当前条目"的写法能正常工作。
    """

    def __init__(self, expression: str) -> None:
        self.expression = (expression or "").strip()

    # -- 求值 --------------------------------------------------------------

    def _evaluate(self, data: Any) -> list:
        """求值。

        **元素 + 绝对路径 → 先作用域化**（`//x` 当 `.//x` 用）。

        这条规则与 `engine._scoped()` 是同一个语义，也是真实站点逼出来的：
        XPath 规范里 `//x` 是"从文档根找"，**即使在元素上求值也一样**。
        于是规则的 `searchName: "//div/a[2]"`、`chapterResult: "//a/@href"`
        会给每条命中/每条线路都返回"全页第一个匹配" —— 实测表现为标题取到站点头部的
        `IOS用户使用欧路Via浏览器…全功能拦截`、所有线路的剧集一模一样、搜索命中 0 条。

        只有"先作用域化拿到东西"或"作用域化拿不到、原样能拿到"时才用原样结果；
        两者都空就返回空。字符串输入（整页 HTML）不受影响，仍按文档语义求值。
        """
        from lxml import etree, html

        root = self._root(data)
        if root is None:
            return []
        if self.expression in ("//", "/", ".", "./", ""):
            # 真规则里出现过 `"searchResult": "//"`（KazumiRules 的 aafun）——
            # 这是**合法 JSON 但非法 XPath**。以前会让整条规则直接抛 XPathError，
            # 一条写坏的字段把整站搜索打掉；现在当成"该字段没写"（返回空）。
            return []
        is_element = isinstance(root, (etree._Element, etree._ElementTree))  # noqa: SLF001
        expression = self.expression
        if is_element and expression.startswith("/"):
            scoped = "." + expression
            try:
                scoped_result = root.xpath(scoped)
            except etree.XPathError:
                scoped_result = None
            if scoped_result:
                return scoped_result if isinstance(scoped_result, list) else [scoped_result]
        try:
            result = root.xpath(expression)
        except etree.XPathError as error:
            raise SelectorError(f"XPath 语法错误：{self.expression}（{error}）") from error
        if not isinstance(result, list):
            result = [result]
        if result or not is_element or not expression.startswith("/"):
            return result
        scoped = self.scoped_expression
        if scoped == expression:
            return result
        try:
            retry = root.xpath(scoped)
        except etree.XPathError:
            return result
        return retry if isinstance(retry, list) else [retry]

    @property
    def scoped_expression(self) -> str:
        """`//x/y` → `.//x/y`（只在"就某个子节点"求值时有意义）。"""
        expr = self.expression
        if expr.startswith("/"):
            return "." + expr
        return expr

    @staticmethod
    def _root(data: Any):  # noqa: ANN401
        from lxml import etree, html

        if data is None:
            return None
        if isinstance(data, (etree._Element, etree._ElementTree)):  # noqa: SLF001
            return data
        if isinstance(data, str):
            text = data.strip()
            if not text:
                return None
            try:
                return html.fromstring(text)
            except Exception:  # noqa: BLE001  非 HTML（例如 JSON 文本）
                try:
                    return etree.fromstring(text.encode("utf-8"))
                except Exception:  # noqa: BLE001
                    return None
        return None

    # -- 公共接口 ----------------------------------------------------------

    def nodes(self, data: Any) -> list:  # noqa: ANN401
        return self._evaluate(data)

    def all(self, data: Any) -> list[str]:  # noqa: ANN401
        return [_stringify(item) for item in self._evaluate(data)]

    def first(self, data: Any) -> str:  # noqa: ANN401
        for item in self._evaluate(data):
            text = _stringify(item)
            if text:
                return text
        return ""

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"XPathSelector({self.expression!r})"


def _stringify(item: Any) -> str:  # noqa: ANN401
    """把 XPath 结果项转成字符串（元素取文本、属性已是字符串）。

    **`<a>` 优先取 `href`**：规则里的"结果/剧集"选择器普遍写成 `//h2/a` 这种
    **元素**形态（Kazumi 的 `searchResult` / `chapterResult` 都是这个约定），
    而它们的语义就是"链接"。若按元素取文本，拿到的是标题文字 —— 实测表现为
    hit 的地址变成 `https://站/葬送的芙莉莲`，接着详情 404、选集全空。
    显式写 `@href` / `text()` 的规则不受影响（那时返回的已经是字符串）。
    """
    if item is None:
        return ""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, (int, float)):
        return str(item)
    href = None
    getter = getattr(item, "get", None)
    if callable(getter):
        try:
            href = getter("href")
        except Exception:  # noqa: BLE001
            href = None
    if href and getattr(item, "tag", "") in ("a", "A"):
        return str(href).strip()
    text = getattr(item, "text_content", None)
    if callable(text):
        try:
            return str(text() or "").strip()
        except Exception:  # noqa: BLE001
            pass
    text = getattr(item, "text", None)
    if isinstance(text, str):
        return text.strip()
    return str(item).strip()


# --------------------------------------------------------------------------- CSS


class CssSelector:
    """CSS 选择器（我们的扩展）：`soupsieve` 求值；`css:a@href` 形态可**取属性**。"""

    def __init__(self, expression: str) -> None:
        raw = (expression or "").strip()
        # `a.item@href`：把 `@attr` 摘出来，其余部分是纯 CSS 选择器
        self.attribute = ""
        if "@" in raw:
            head, _, tail = raw.rpartition("@")
            if tail and re.fullmatch(r"[\w\-]+", tail) and head:
                raw, self.attribute = head, tail
        self.expression = raw

    def nodes(self, data: Any) -> list:  # noqa: ANN401
        from bs4 import BeautifulSoup

        if data is None:
            return []
        text = data if isinstance(data, str) else str(data)
        try:
            soup = BeautifulSoup(text, "lxml")
            return soup.select(self.expression)
        except Exception as error:  # noqa: BLE001
            raise SelectorError(f"CSS 语法错误：{self.expression}（{error}）") from error

    def all(self, data: Any) -> list[str]:  # noqa: ANN401
        found: list[str] = []
        for node in self.nodes(data):
            if self.attribute:
                value = node.get(self.attribute) if hasattr(node, "get") else None
                text = str(value).strip() if value else ""
            else:
                text = node.get_text(strip=True) if hasattr(node, "get_text") else str(node)
            if text:
                found.append(text)
        return found

    def first(self, data: Any) -> str:  # noqa: ANN401
        found = self.all(data)
        return found[0] if found else ""

    def __repr__(self) -> str:  # pragma: no cover
        return f"CssSelector({self.expression!r}, attr={self.attribute!r})"


# --------------------------------------------------------------------------- JSONPath


_TOKEN = re.compile(r"""
    (?P<dot>\.(?P<name>[A-Za-z_][\w\-]*))          # .name
    |(?P<bracket>\[(?P<inner>[^\]]*)\])             # [0] [*] ['k'] ["k"] [?(...)]
""", re.VERBOSE)

_UNSUPPORTED = (
    (re.compile(r"\.\."), "递归下降 `..`"),
    (re.compile(r"\[\s*\?"), "过滤表达式 `[?(...)]`"),
    (re.compile(r"\[\s*-?\d*\s*:\s*-?\d*\s*\]"), "数组切片 `[start:end]`"),
    (re.compile(r"\blength\s*\("), "函数 `length()`"),
)


@dataclass
class _Step:
    kind: str            # "key" | "index" | "wildcard"
    value: Any = None


class JsonPathSelector:
    """JSONPath 子集：`$` / `.a.b` / `[*]` / `[0]` / `['k']`（与 Kazumi 相同范围）。"""

    def __init__(self, expression: str) -> None:
        self.expression = (expression or "").strip()
        self.steps = self._parse(self.expression)

    @staticmethod
    def _parse(expression: str) -> list[_Step]:
        text = expression.strip()
        if text in ("", "$"):
            return []
        if not text.startswith("$"):
            raise SelectorError(f"JSONPath 必须以 $ 开头：{expression}")
        for pattern, label in _UNSUPPORTED:
            if pattern.search(text):
                raise SelectorError(f"不支持的 JSONPath 语法：{label}（{expression}）")
        steps: list[_Step] = []
        position = 1
        while position < len(text):
            match = _TOKEN.match(text, position)
            if match is None:
                raise SelectorError(f"JSONPath 语法错误（位置 {position}）：{expression}")
            position = match.end()
            if match.group("dot"):
                steps.append(_Step("key", match.group("name")))
                continue
            inner = (match.group("inner") or "").strip()
            if inner == "*":
                steps.append(_Step("wildcard"))
            elif inner.isdigit() or (inner.startswith("-") and inner[1:].isdigit()):
                steps.append(_Step("index", int(inner)))
            elif (inner.startswith("'") and inner.endswith("'")) or \
                    (inner.startswith('"') and inner.endswith('"')):
                steps.append(_Step("key", inner[1:-1]))
            else:
                raise SelectorError(f"不支持的 JSONPath 下标：[{inner}]（{expression}）")
        return steps

    def nodes(self, data: Any) -> list:  # noqa: ANN401
        current: list[Any] = [data]
        for step in self.steps:
            nxt: list[Any] = []
            for item in current:
                if step.kind == "key":
                    if isinstance(item, dict) and step.value in item:
                        nxt.append(item[step.value])
                elif step.kind == "index":
                    if isinstance(item, (list, tuple)) and -len(item) <= step.value < len(item):
                        nxt.append(item[step.value])
                    elif isinstance(item, dict):
                        nxt.append(item)
                elif step.kind == "wildcard":
                    if isinstance(item, dict):
                        nxt.extend(item.values())
                    elif isinstance(item, (list, tuple)):
                        nxt.extend(item)
            current = nxt
        return current

    def all(self, data: Any) -> list[str]:  # noqa: ANN401
        found: list[str] = []
        for item in self.nodes(data):
            text = _json_scalar(item)
            if text != "":
                found.append(text)
        return found

    def first(self, data: Any) -> str:  # noqa: ANN401
        found = self.all(data)
        return found[0] if found else ""

    def __repr__(self) -> str:  # pragma: no cover
        return f"JsonPathSelector({self.expression!r})"


def _json_scalar(item: Any) -> str:  # noqa: ANN401
    if item is None or isinstance(item, (dict, list, tuple)):
        return ""
    if isinstance(item, bool):
        return "true" if item else "false"
    if isinstance(item, (int, float)):
        return str(item)
    return str(item).strip()


# --------------------------------------------------------------------------- 门面


class Selector:
    """按表达式自动选择求值器（`css:` 前缀 / `$` 开头 / 其余按 XPath）。"""

    def __init__(self, expression: str, *, allow_json: bool = True) -> None:
        self.expression = (expression or "").strip()
        self._delegate: Any = None
        if not self.expression:
            self._delegate = None                      # 空 = 当前对象自身
        elif self.expression.lower().startswith(CSS_PREFIX):
            self._delegate = CssSelector(self.expression[len(CSS_PREFIX):])
        elif looks_like_jsonpath(self.expression):
            if not allow_json:
                raise SelectorError(f"该字段不支持 JSONPath：{self.expression}")
            self._delegate = JsonPathSelector(self.expression)
        elif looks_like_xpath(self.expression):
            self._delegate = XPathSelector(self.expression)
        else:
            # 既不是 XPath 也不是 JSONPath：按 CSS 处理更符合直觉（`div.item > a`）
            self._delegate = CssSelector(self.expression)

    @property
    def is_identity(self) -> bool:
        return self._delegate is None

    @property
    def kind(self) -> str:
        if self._delegate is None:
            return "identity"
        return type(self._delegate).__name__.replace("Selector", "").lower()

    def nodes(self, data: Any) -> list:  # noqa: ANN401
        if self._delegate is None:
            return [] if data is None else [data]
        return self._delegate.nodes(data)

    def all(self, data: Any) -> list[str]:  # noqa: ANN401
        if self._delegate is None:
            return [] if data is None else [str(data)]
        return self._delegate.all(data)

    def first(self, data: Any) -> str:  # noqa: ANN401
        if self._delegate is None:
            return "" if data is None else str(data)
        return self._delegate.first(data)

    def attr(self, data: Any, name: str) -> str:  # noqa: ANN401
        """取节点属性（XPath/CSS 都支持；JSON 模式下取同名字段）。"""
        if not name:
            return self.first(data)
        if isinstance(self._delegate, JsonPathSelector):
            return self.first(data)
        for node in self.nodes(data):
            value = None
            if hasattr(node, "get"):
                try:
                    value = node.get(name)
                except Exception:  # noqa: BLE001
                    value = None
            if value:
                return str(value).strip()
        return ""

    def __repr__(self) -> str:  # pragma: no cover
        return f"Selector({self.expression!r}, kind={self.kind})"


#: 后向兼容别名：XPath 求值（旧写法）
def xpath_first(expression: str, data: Any) -> str:  # noqa: ANN401
    return XPathSelector(expression).first(data)


def xpath_all(expression: str, data: Any) -> list[str]:  # noqa: ANN401
    return XPathSelector(expression).all(data)


def json_first(expression: str, data: Any) -> str:  # noqa: ANN401
    return JsonPathSelector(expression).first(data)


def json_all(expression: str, data: Any) -> list[str]:  # noqa: ANN401
    return JsonPathSelector(expression).all(data)


def parse_json(text: str) -> Any:  # noqa: ANN401
    """宽松解析 JSON（容忍前后杂字符；失败返回 None）。"""
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = min([index for index in (text.find("{"), text.find("[")) if index >= 0] or [-1])
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:index + 1])
                except json.JSONDecodeError:
                    return None
    return None


def validate_selector(expression: str) -> str:
    """校验选择器语法；返回空串表示合法，否则返回可读错误。"""
    try:
        Selector(expression)
    except SelectorError as error:
        return str(error)
    except Exception as error:  # noqa: BLE001
        return f"选择器无法解析：{error}"
    return ""


def iter_media_urls(value: Any) -> Iterable[str]:  # noqa: ANN401
    """递归找出任意结构里像媒体地址的字符串（嗅探用）。"""
    if isinstance(value, str):
        if looks_like_media(value):
            yield value
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from iter_media_urls(item)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from iter_media_urls(item)


_MEDIA_SUFFIX = re.compile(r"\.(m3u8|mp4|flv|mkv|ts|mpd|webm|mov)(\?|#|$)", re.IGNORECASE)


def looks_like_media(url: str) -> bool:
    """是否像媒体地址（不看域名，只看路径后缀/协议）。"""
    text = (url or "").strip()
    if not text or text.startswith("data:"):
        return False
    if not text.lower().startswith(("http://", "https://", "/", "rtmp://")):
        return False
    return bool(_MEDIA_SUFFIX.search(text))


__all__ = [
    "CSS_PREFIX", "CssSelector", "JsonPathSelector", "Selector", "SelectorError",
    "XPathSelector", "iter_media_urls", "json_all", "json_first", "looks_like_media",
    "looks_like_jsonpath", "looks_like_xpath", "parse_json", "validate_selector",
    "xpath_all", "xpath_first",
]
