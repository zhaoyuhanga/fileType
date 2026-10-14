"""真实 Kazumi 规则的兼容性回归：`bodyType` 与标量类型保真。

这两条都是**用真规则实测出来**的：

1. KazumiRules 里的 `xfdmnext` 用 `"bodyType": "json"` + 自定义 `Authorization` 头，
   `sorani` 用 `"enabled": true` / `"sortDesc": false` / `"page": 1` 这类**非字符串**字段。
   早先的实现"body 是 dict 就按 json 发、其余一律 `str()`"，前者靠运气对，
   后者会把 `false` 发成 `"false"`、`1` 发成 `"1"` —— 严格校验类型的接口会 400 或行为异常。
2. 缺失 `bodyType` 时必须保持旧行为（body 优先、退回 query），否则会破坏既有规则。

测试全部离线：注入替身 requests，直接检查我们**实际发出的 kwargs**。
"""
from __future__ import annotations

import pytest

from modu_workbench.core.anime.engine import AnimeEngine, _post_body_kwargs, _template_dict
from modu_workbench.core.anime.rules import rule_from_dict


# --------------------------------------------------------------------------- 纯函数


def test_post_body_kwargs_honours_body_type_json() -> None:
    assert _post_body_kwargs("json", {"q": "x"}, {}) == {"json": {"q": "x"}}


def test_post_body_kwargs_honours_body_type_form() -> None:
    """`form` 必须走表单编码（`data=`），而不是 JSON。"""
    assert _post_body_kwargs("form", {"q": "x"}, {}) == {"data": {"q": "x"}}
    assert _post_body_kwargs("x-www-form-urlencoded", {"q": "x"}, {}) == {"data": {"q": "x"}}


def test_post_body_kwargs_honours_body_type_text() -> None:
    """`text` 要发原始文本；body 是 dict 时序列化成 JSON 文本。"""
    assert _post_body_kwargs("text", '{"q": "x"}', {}) == {"data": '{"q": "x"}'}
    assert _post_body_kwargs("raw", {"q": "x"}, {}) == {"data": '{"q": "x"}'}


def test_post_body_kwargs_without_body_type_keeps_legacy_behaviour() -> None:
    """未写 `bodyType`：body 是 dict 就按 json，否则退回 query（旧行为，不能变）。"""
    assert _post_body_kwargs("", {"q": "x"}, {"k": "v"}) == {"json": {"q": "x"}}
    assert _post_body_kwargs("", None, {"k": "v"}) == {"data": {"k": "v"}}
    assert _post_body_kwargs("", None, {}) == {}


def test_template_dict_keeps_scalar_types() -> None:
    """没有占位符的 bool/int/float 必须保原类型（真规则里很常见）。"""
    rendered = _template_dict(
        {"enabled": True, "sortDesc": False, "page": 1, "size": 50, "ratio": 1.5,
         "keyword": "@keyword"},
        {"keyword": "芙莉莲"},
    )
    assert rendered["enabled"] is True
    assert rendered["sortDesc"] is False
    assert rendered["page"] == 1 and isinstance(rendered["page"], int)
    assert rendered["size"] == 50
    assert rendered["ratio"] == 1.5
    assert rendered["keyword"] == "芙莉莲"


def test_template_dict_substitutes_only_strings_with_placeholder() -> None:
    """带占位符的字符串照常替换；纯字符串不替换也不改动。"""
    rendered = _template_dict({"path": "/api/@source/x", "plain": "abc", "k": "@keyword"},
                              {"source": "9", "keyword": "w"})
    assert rendered["path"] == "/api/9/x"
    assert rendered["plain"] == "abc"
    assert rendered["k"] == "w"


# --------------------------------------------------------------------------- 端到端（替身 requests）


class _FakeResponse:
    """最小的 requests.Response 替身（引擎的 POST 路径要 .json() / .text）。"""

    def __init__(self, payload) -> None:  # noqa: ANN001
        self._payload = payload

    def json(self):  # noqa: ANN201
        return self._payload

    @property
    def text(self) -> str:
        import json as _json

        return _json.dumps(self._payload, ensure_ascii=False)


class _RecordingHttp:
    """记录我们实际发出的 kwargs，并返回一份可被 listPath 解析的 JSON。"""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str, dict]] = []
        self.headers = {"User-Agent": "test"}

    def get(self, url: str, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(("GET", url, kwargs))
        return _FakeResponse(self.payload)

    def post(self, url: str, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(("POST", url, kwargs))
        return _FakeResponse(self.payload)

    def get_json(self, url: str, **kwargs):  # noqa: ANN003, ANN201
        if kwargs.get("method") == "POST":
            kwargs.pop("method", None)
        self.calls.append(("GET", url, kwargs))
        return self.payload

    def get_text(self, url: str, **kwargs) -> str:  # noqa: ANN003
        kwargs.pop("method", None)
        self.calls.append(("GET", url, kwargs))
        return ""


def _api_rule(request: dict) -> object:
    return rule_from_dict({
        "api": "8", "type": "anime", "name": "录制站", "version": "1.0",
        "baseURL": "https://example.com/",
        "searchMode": "api", "chapterMode": "api",
        "searchApiConfig": {
            "request": request,
            "listPath": "$.list[*]", "namePath": "$.name", "sourcePath": "$.id",
        },
        "chapterApiConfig": {
            "request": {"method": "GET", "url": "https://example.com/detail/@source"},
            "format": "flat", "episodesPath": "$.eps[*]",
            "episodeNamePath": "$.name", "episodeUrlPath": "$.url",
        },
    }, key="rec")


@pytest.mark.parametrize("body_type,expected_key", [("json", "json"), ("form", "data"),
                                                    ("text", "data")])
def test_search_sends_body_per_body_type(body_type: str, expected_key: str) -> None:
    http = _RecordingHttp({"list": [{"name": "片", "id": 7}]})
    rule = _api_rule({
        "method": "POST", "url": "https://example.com/search",
        "headers": {"Authorization": "Bearer k"},
        "bodyType": body_type, "body": {"search_term": "@keyword"},
    })
    engine = AnimeEngine([rule], http_factory=lambda _rule: http)
    hits = engine.search_rule(rule, "芙莉莲")

    assert [h.title for h in hits] == ["片"]
    assert http.calls, "应当发出了一次请求"
    method, url, kwargs = http.calls[-1]
    assert method == "POST", f"应当用 POST 发搜索：{method}"
    assert expected_key in kwargs, f"bodyType={body_type} 应用 {expected_key}= 发送：{kwargs}"
    assert kwargs["headers"]["Authorization"] == "Bearer k", "自定义头不能被丢掉"


def test_boolean_and_int_fields_are_not_stringified() -> None:
    """真规则（如 sorani）里的 `enabled: true` / `page: 1` 必须原样发出。"""
    http = _RecordingHttp({"data": {"records": [{"title": "片", "id": 3}]}})
    rule = _api_rule({
        "method": "GET", "url": "https://example.com/api/video",
        "query": {"page": 1, "enabled": True, "sortDesc": False, "keyword": "@keyword"},
    })
    engine = AnimeEngine([rule], http_factory=lambda _rule: http)
    engine.search_rule(rule, "片")

    _method, _url, kwargs = http.calls[-1]
    params = kwargs.get("params") or {}
    assert params.get("enabled") is True
    assert params.get("sortDesc") is False
    assert params.get("page") == 1 and isinstance(params.get("page"), int)
    assert params.get("keyword") == "片"
