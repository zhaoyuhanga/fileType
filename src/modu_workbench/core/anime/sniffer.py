"""播放页静态嗅探：不执行页面 JS，只用文本 + 一次 HTTP 抓取找出真实媒体地址。

**能力边界（必须先说清楚，别让用户以为"规则写对了就一定能看"）**：
我们**没有浏览器内核、不执行页面 JS**。所以：

- 苹果 CMS v10（`player_aaaa` 里的 JSON）、把地址写死在 HTML/JSON 里的自研播放器、
  播放页套 iframe 的站点 —— 这些能静态提取，可以支持；
- 用 JS 动态拼接/解密（自定义加密、WebAssembly、`eval` 混淆）、或需要过站点验证的源 ——
  **无法支持**。Kazumi 的维护者在 issue #914 里也说明这类问题基本只能靠注入脚本解决，
  而我们正是没有这个能力。嗅探失败时给出的 `note` 会明确说出"试了哪些策略、下一步怎么办"，
  而不是笼统的"解析失败"。

策略顺序是**从便宜/可靠到投机**（命中即返回，出错不抛出）：

    direct       地址本身就是媒体文件（最可靠，零额外请求）
    player_aaaa  页面里的苹果 CMS 播放器 JSON
    player_api   `/player/?...` 这类播放器接口（请求它并按 JSON 扫描）
    json_scan    页面其实是 JSON（接口返回 / SPA 预置数据）
    regex        原始文本里的媒体字面量 + 常见 JS 变量赋值
    iframe       抓 `<iframe src>` 递归（最多 `max_iframe_depth` 层）

所有公开方法都**不抛网络异常**：失败返回 `SniffedMedia(url="", strategy="failed",
note=...)`，`note` 是可直接展示给用户的中文说明。
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from ..platform.http import HttpClient, host_of, looks_like_media_url
from .models import SniffedMedia
from .selectors import iter_media_urls, looks_like_media, parse_json

#: 嗅探策略名（`SniffedMedia.strategy` 的取值，试跑面板直接显示给用户）
STRATEGIES = ("direct", "player_aaaa", "player_api", "json_scan", "regex", "iframe", "failed")

#: 媒体地址里的裸字符（写在一起便于阅读；`\\` 也要收，因为 JSON 转义里长这样）
_URL_CHARS = r"A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%\\|"

_MEDIA_LITERAL = re.compile(
    r"(?:https?:)?(?:\\?/){2}[" + _URL_CHARS + r"]*?\.(?:m3u8|mp4|flv|mkv|mpd)"
    r"(?:[" + _URL_CHARS + r"]*)?",
    re.IGNORECASE,
)

#: `url: "..."` / `"url":"..."` / `src: '...'` 这类 JS/JSON 变量赋值（值可能是编码过的）
_WRAPPED_LITERAL = re.compile(
    r"""(?ix)
    (?:url|source|src|file|video_?url|play_?url|link|video)      # 键名（JSON 与 JS 都常见）
    \s*[:=]\s*
    (?:[rubf]{0,2})                                             # JS 前缀 r/b/f
    (?P<q>["'])(?P<value>.+?)(?P=q)                             # 引号里的值
    """
)

#: `atob('...')` / `unescape('...')` / `decodeURIComponent('...')`
_WRAPPER_CALL = re.compile(
    r"(?is)\b(?:atob|unescape|decodeURIComponent|decodeURI)\s*\(\s*([\"'])(.+?)\1\s*\)"
)

#: 苹果 CMS v10 的播放器脚本：等号两侧空格、单引号、`&quot;` 都要容忍
_PLAYER_ASSIGN = re.compile(r"(?is)player_aaaa\s*=\s*\{")

_IFRAME_SRC = re.compile(
    r"""(?is)<iframe[^>]*?\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))"""
)

#: `/player/?...id=xxx` 这类播放器接口。**必须有查询串**（否则 `/player/x.js` 会误命中），
#: 相对地址（`/player/?id=1`）与协议相对地址（`//host/player/?id=1`）都要收。
_PLAYER_ENDPOINT = re.compile(
    r"(?i)(?:https?:)?(?://)?[^\s\"'<>]*?/player/[^\s\"'<>]*?\?"
    r"[^\s\"'<>]*(?:id|url|vid|key|link)=[^\s\"'<>\"&]+[^\s\"'<>]*"
)

#: "没传 html"与"传了空 html"是两件事：前者要抓页面，后者必须**离线**（测试与试跑面板依赖）
_MISSING = object()

#: 尾部常见的"句子标点"，多半不属于地址（HTML 里 `"url":"x.m3u8",`）
_TRAILING = "。，、；：！？）】》\"'>,;)]}。"


# --------------------------------------------------------------------------- 字面量解码


def _unescape_js(text: str) -> str:
    r"""把 JS/JSON 特有的转义还原：`\/`、`\u002F`、`\x2F`（含大写十六进制）。

    规则 JSON 里的播放地址常被站点写成 `https:\/\/host\/index.m3u8`，
    如果不在匹配前还原，正则和 `looks_like_media()` 都会漏掉。
    """
    if not text:
        return ""
    value = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)
    value = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), value)
    return value.replace("\\/", "/")


def _strip_quotes(text: str) -> str:
    value = (text or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1].strip()
    return value


def _base64_decode(value: str) -> str:
    """尽量按 base64 解码；解不出来或解出来不是可读文本就返回空串。"""
    text = re.sub(r"\s+", "", value or "")
    if len(text) < 8:
        return ""
    text = text.replace("-", "+").replace("_", "/")
    text += "=" * (-len(text) % 4)
    try:
        raw = base64.b64decode(text, validate=False)
    except (binascii.Error, ValueError):
        return ""
    for encoding in ("utf-8", "gbk"):
        try:
            decoded = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        # 解出来必须"整串都是可打印字符"，否则多半是二进制被误判成 base64
        if decoded and all(char.isprintable() or char in "\r\n\t" for char in decoded):
            return decoded.strip()
    return ""


def _is_encoded_media(value: str) -> bool:
    """判断一个值是否需要（且可能）反编码：`aHR0cHM6Ly8` = `https://`。"""
    text = _strip_quotes(value)
    header = re.match(r"^(?:[A-Za-z0-9+/]{4}){2,}", text)
    if header and "aHR0c" in text[:32]:
        return True
    return "\\u" in text or "\\/" in text or "%2F" in text.upper()


def decode_media_literal(value: str) -> str:
    r"""解码一个"可能是媒体地址的字符串字面量"。

    依次处理（每一步都可能失败，失败就保留上一步结果）：

    1. 反引号/单双引号包裹、`atob('...')`/`unescape('...')` 包装；
    2. JS 转义 `\/`、`\uXXXX`、`\xXX`；
    3. 百分号编码（`%2F`）；
    4. base64（含 URL-safe 变体）——只在"看起来像 base64"时尝试。

    解不出**媒体地址**时返回空串，调用方据此跳过（不要拿半截字符串去播放）。
    """
    text = (value or "").strip()
    if not text:
        return ""

    # atob/unescape 包装：先取出里面的字符串
    for _ in range(3):
        match = _WRAPPER_CALL.search(text)
        if not match:
            break
        text = match.group(2)

    text = _strip_quotes(text)
    text = _unescape_js(text)

    if re.search(r"%[0-9a-fA-F]{2}", text):
        from urllib.parse import unquote

        text = unquote(text)

    # base64：解出来的内容里必须能再看到一层转义或直接的 `http`
    if _is_encoded_media(text):
        decoded = _base64_decode(text)
        if decoded:
            text = _unescape_js(decoded)

    text = text.strip().strip("\"'").strip()
    # 解码结果必须是"像媒体地址的东西"：否则调用方拿到一段解出来的 JS 垃圾，
    # 再往下传就会变成一个莫名其妙的播放失败。
    return text if looks_like_media(text) else ""


def _clean_url(candidate: str) -> str:
    """补全 `//host/...` 协议相对地址，去掉尾部标点。"""
    text = (candidate or "").strip().rstrip(_TRAILING)
    if text.startswith("//"):
        text = "https:" + text
    return text


def _fix_endpoint(text: str) -> str:
    """播放器接口地址补全：只补协议相对的 `//`，**绝不**给站内相对路径补域名。"""
    if text.startswith("//"):
        return "https:" + text
    return text


def find_media_urls(text: str) -> list[str]:
    """从任意文本里找出媒体地址（正则兜底策略）。

    做两遍：先匹配原始文本里的完整地址，再解开 `\\/` / `\\u002F` 转义后匹配一遍
    （站点把地址塞在 JSON 字符串里时全是转义斜杠），最后对 `atob()`/变量赋值里的
    值做一次解码尝试。结果**保序去重**——顺序就是页面里出现的顺序，便于人工核对。
    """
    if not text:
        return []

    found: list[str] = []

    def collect(source: str) -> None:
        for match in _MEDIA_LITERAL.finditer(source):
            candidate = _clean_url(_unescape_js(match.group(0)))
            if looks_like_media(candidate):
                found.append(candidate)

    collect(text)
    unescaped = _unescape_js(text)
    if unescaped != text:
        collect(unescaped)

    # 变量赋值 / atob / unescape：值可能是编码过的
    for match in _WRAPPED_LITERAL.finditer(text):
        decoded = decode_media_literal(match.group("value"))
        if looks_like_media(decoded):
            found.append(decoded)
    for match in _WRAPPER_CALL.finditer(text):
        decoded = decode_media_literal(match.group(2))
        if looks_like_media(decoded):
            found.append(decoded)

    unique: list[str] = []
    for item in found:
        if item not in unique:
            unique.append(item)
    return unique


def extract_iframes(text: str) -> list[str]:
    """取出 `<iframe src="...">`（保序去重；`about:blank`/`javascript:` 一律丢弃）。"""
    if not text:
        return []
    found: list[str] = []
    for match in _IFRAME_SRC.finditer(text):
        value = (match.group(1) or match.group(2) or match.group(3) or "").strip()
        if not value:
            continue
        scheme = urlsplit(value).scheme.lower()
        if scheme in ("javascript", "data", "about"):
            continue
        if value not in found:
            found.append(value)
    return found


def extract_player_endpoints(text: str) -> list[str]:
    """找出页面里的 `/player/?...id=xxx` 接口地址（保序去重）。

    站内相对地址（`/player/?id=1`）**原样返回**，由调用方用 `urljoin` 补成绝对地址——
    这里并不知道页面在哪，硬补域名只会补错。
    """
    if not text:
        return []
    found: list[str] = []
    for match in _PLAYER_ENDPOINT.finditer(text):
        value = _fix_endpoint(_clean_url(match.group(0)))
        if value and value not in found:
            found.append(value)
    return found


# --------------------------------------------------------------------------- 苹果 CMS


def _clean_json_text(text: str) -> str:
    """把"像 JSON 但其实是 JS 对象字面量"的文本清洗成可 `json.loads` 的样子。

    站点常见三种毛病：`&quot;` 实体、单引号、尾随逗号。**注释用引号感知扫描去掉**，
    不能用正则（`//` 会出现在 `https://` 里，正则一删就把地址剪断了）。
    """
    value = (text or "").replace("&quot;", '"').replace("&#34;", '"')
    value = value.replace("&amp;", "&")

    output: list[str] = []
    index = 0
    length = len(value)
    in_string = False
    quote = ""
    while index < length:
        char = value[index]
        if in_string:
            if char == "\\" and index + 1 < length:
                output.append(char)
                output.append(value[index + 1])
                index += 2
                continue
            if char == quote:
                in_string = False
            output.append(char)
            index += 1
            continue
        if char in "\"'":
            in_string = True
            quote = char
            output.append(char)
            index += 1
            continue
        if char == "/" and index + 1 < length and value[index + 1] in "/*":
            in_string = True          # 临时借用：注释里的引号不算字符串
            quote = "\x00"            # 哨兵，永远不会等于任何真实字符
            if value[index + 1] == "/":
                index = value.find("\n", index)
                index = length if index < 0 else index
            else:
                end = value.find("*/", index + 2)
                index = length if end < 0 else end + 2
            in_string = False
            output.append(" ")
            continue
        output.append(char)
        index += 1

    cleaned = "".join(output)
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)     # 尾随逗号
    cleaned = _single_to_double_quotes(cleaned)
    return cleaned


def _single_to_double_quotes(text: str) -> str:
    """把 JS 对象里的单引号键/值换成双引号（已含双引号的片段跳过）。"""
    output: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char != "'":
            output.append(char)
            index += 1
            continue
        end = index + 1
        body: list[str] = []
        while end < length:
            if text[end] == "\\" and end + 1 < length:
                body.append(text[end:end + 2])
                end += 2
                continue
            if text[end] == "'":
                break
            body.append(text[end])
            end += 1
        if end >= length:                      # 没闭合：原样保留，交给解析器报错
            output.append(text[index:])
            break
        chunk = "".join(body).replace('"', '\\"')
        output.append(f'"{chunk}"')
        index = end + 1
    return "".join(output)


def extract_player_aaaa(text: str) -> dict | None:
    """提取苹果 CMS v10 播放器脚本里的 `player_aaaa` 对象。

    容忍 `player_aaaa = {...}`（等号两侧空格）、单引号、`&quot;` 实体、
    注释与尾随逗号。解析不出来返回 `None`（调用方继续走更投机的策略）。
    """
    if not text:
        return None
    match = _PLAYER_ASSIGN.search(text)
    if match is None:
        return None

    start = match.end() - 1                    # 指向 `{`
    depth = 0
    in_string = False
    quote = ""
    end = -1
    index = start
    length = len(text)
    while index < length:
        char = text[index]
        if in_string:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                in_string = False
            index += 1
            continue
        if char in "\"'":
            in_string = True
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
        index += 1
    if end < 0:
        return None

    body = text[start:end]
    for candidate in (body, _clean_json_text(body)):
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            data = None
        if isinstance(data, dict):
            return data
    return None


def _player_payload(data: dict) -> tuple[str, str] | None:
    """从 `player_aaaa` 对象里取出地址并解码，返回 `(地址, 说明)`。

    `encrypt` 为 `1`/`base64`/`true` 时 `url` 是 base64（苹果 CMS 的 `base64` 选项）；
    其余情况也可能是转义过的，统一交给 `decode_media_literal` 处理。
    **只认 `url` 键**：`link_next`/`link_pre` 是"下一集/上一集"页面地址，
    把它当成本集播放地址是真实踩过的坑（会跳到相邻集）。
    """
    raw = data.get("url")
    if not isinstance(raw, str) or not raw.strip():
        return None
    value = raw.strip()

    encrypt_text = str(data.get("encrypt", "")).strip().lower()
    encoded = encrypt_text in ("1", "base64", "true")

    decoded = _clean_url(value)
    how = "明文"
    if encoded or _is_encoded_media(value):
        candidate = decode_media_literal(value)
        if candidate and looks_like_media(candidate):
            decoded, how = _clean_url(candidate), "base64 解码"
    if not looks_like_media(decoded) and looks_like_media_url(decoded):
        how = "明文"
    if not (looks_like_media(decoded) or looks_like_media_url(decoded)):
        return None
    return decoded, how


# --------------------------------------------------------------------------- 嗅探器


class Sniffer:
    """播放页静态嗅探器。

    `http` 可注入（测试用），默认按 `referer`/`user_agent` 构造 `HttpClient`。
    """

    def __init__(self, *, http=None, referer: str = "", user_agent: str = "",
                 max_iframe_depth: int = 3, timeout: float = 20.0) -> None:
        self.referer = referer or ""
        self.user_agent = user_agent or ""
        self.max_iframe_depth = max(0, int(max_iframe_depth))
        self.timeout = timeout
        headers: dict[str, str] = {}
        if self.user_agent:
            headers["User-Agent"] = self.user_agent
        if self.referer:
            headers["Referer"] = self.referer
        self.http = http if http is not None else HttpClient(
            headers=headers, timeout=timeout, attempts=2, hint="可在浏览器中打开该页确认地址")

    # ------------------------------------------------------------------ 入口

    def sniff(self, url: str, *, html=_MISSING) -> SniffedMedia:
        """嗅探播放地址。

        给了 `html`（**哪怕是空串**）就表示"离线模式"：绝不发请求。
        没给 `html` 时才会去抓 `url`。空串与"没给"必须区分开——试跑面板会传
        抓好的 HTML，单元测试则靠传空串来保证一个包都不发。
        """
        offline = html is not _MISSING
        return self._sniff(str(url or ""), "" if html is _MISSING else str(html or ""),
                           0, [], offline=offline)

    def sniff_url(self, url: str) -> SniffedMedia:
        """抓取 `url` 并嗅探（等价于不传 `html` 的 `sniff()`）。"""
        return self._sniff(str(url or ""), "", 0, [], offline=False)

    # ------------------------------------------------------------------ 内部

    def _sniff(self, url: str, html: str, depth: int, visited: list[str], *,
               offline: bool) -> SniffedMedia:
        failures: list[str] = []

        # 文本：能用已知 HTML 就不抓；否则抓一次全文（JSON 页面也走这里）
        text = html
        if offline:
            referer = self.referer
        else:
            text, error = self._fetch(url)
            if error:
                return SniffedMedia(url="", strategy="failed",
                                    note=self._network_note(url, error, depth))
            referer = self.referer or self._origin(url)

        # 1) 地址本身就是媒体文件（最可靠）
        for candidate in self._as_list(url, text):
            if looks_like_media(candidate):
                return SniffedMedia(url=candidate, strategy="direct",
                                    referer=self.referer or self._origin(url), note="")

        # 2) 苹果 CMS v10 的 player_aaaa
        result = self._try_player_aaaa(text, referer)
        if result is not None:
            return result
        failures.append("player_aaaa")

        # 3) /player/ 播放器接口
        result = self._try_player_api(url, text, depth, visited)
        if result is not None:
            return result
        failures.append("player_api")

        # 4) 页面其实是 JSON
        result = self._try_json_scan(text)
        if result is not None:
            return result
        failures.append("json_scan")

        # 5) 正则兜底（JS 变量、base64、转义地址）
        result = self._try_regex(text, url, referer)
        if result is not None:
            return result
        failures.append("regex")

        # 6) iframe 递归
        if depth < self.max_iframe_depth:
            result = self._try_iframes(url, text, depth, visited)
            if result is not None:
                return result
        failures.append("iframe")

        return SniffedMedia(url="", strategy="failed", referer=referer,
                            note=self._failure_note(url, depth, failures))

    def _as_list(self, url: str, text: str) -> list[str]:
        """参与"直链判定"的候选：传入的地址 + 文本本身（可能是纯地址响应）。"""
        candidates = [str(url or "").strip()]
        stripped = (text or "").strip()
        if stripped and "\n" not in stripped and len(stripped) <= 2048:
            candidates.append(_strip_quotes(stripped))
        return candidates

    def _try_player_aaaa(self, text: str, referer: str) -> SniffedMedia | None:
        if "player_aaaa" not in text:
            return None
        payload = _player_payload(extract_player_aaaa(text) or {})
        if payload is None:
            return None
        decoded, how = payload
        return SniffedMedia(url=decoded, strategy="player_aaaa", referer=referer,
                            note=f"苹果 CMS player_aaaa 的 url 字段（{how}）")

    def _try_player_api(self, url: str, text: str, depth: int,
                        visited: list[str]) -> SniffedMedia | None:
        for endpoint in extract_player_endpoints(text)[:4]:
            target = urljoin(url, _unescape_js(endpoint))
            if not target or target in visited:
                continue
            body, error = self._fetch(target, referer=url)
            if error or not body:
                continue
            result = self._scan_payload(body, "player_api", referer=url,
                                        note="请求页面里的 /player/ 接口并按 JSON 解析")
            if result is not None:
                return result
        return None

    def _try_json_scan(self, text: str) -> SniffedMedia | None:
        data = parse_json(text)
        if data is None:
            return None
        for candidate in iter_media_urls(data):
            fixed = _clean_url(_unescape_js(str(candidate)))
            if looks_like_media_url(fixed) or looks_like_media(fixed):
                return SniffedMedia(url=fixed, strategy="json_scan", referer=self.referer,
                                    note="页面返回的就是 JSON（递归找媒体字符串）")
        return None

    def _try_regex(self, text: str, url: str, referer: str) -> SniffedMedia | None:
        for candidate in find_media_urls(text):
            fixed = _clean_url(candidate)
            if not fixed:
                continue
            if looks_like_media(fixed) or looks_like_media_url(fixed):
                how = "解码 base64/atob" if _is_encoded_media(candidate) else "原文/转义还原"
                return SniffedMedia(url=fixed, strategy="regex", referer=referer,
                                    note=f"正则命中媒体字面量（{how}）")
        return None

    def _try_iframes(self, url: str, text: str, depth: int,
                     visited: list[str]) -> SniffedMedia | None:
        for source in extract_iframes(text)[:6]:
            target = urljoin(url, source)
            if not target or target in visited or target == url:
                continue
            visited.append(target)
            body, error = self._fetch(target)
            if error or not body:
                continue
            inner = self._sniff(target, body, depth + 1, visited, offline=True)
            if inner.ok:
                note = f"iframe 第 {depth + 1} 层：{source}"
                if inner.note:
                    note = f"{note}（{inner.note}）"
                return SniffedMedia(url=inner.url, strategy="iframe",
                                    referer=inner.referer or url, note=note)
        return None

    def _scan_payload(self, body: str, strategy: str, *, referer: str, note: str):
        """对一段"可能是 JSON 也可能是 HTML"的响应做扫描（player_api 用）。"""
        data = parse_json(body)
        if data is not None:
            for candidate in iter_media_urls(data):
                fixed = _clean_url(_unescape_js(str(candidate)))
                if looks_like_media(fixed) or looks_like_media_url(fixed):
                    return SniffedMedia(url=fixed, strategy=strategy, referer=referer, note=note)
        for candidate in find_media_urls(body):
            fixed = _clean_url(candidate)
            if looks_like_media(fixed) or looks_like_media_url(fixed):
                return SniffedMedia(url=fixed, strategy=strategy, referer=referer, note=note)
        return None

    # ------------------------------------------------------------------ 网络

    def _fetch(self, url: str, *, referer: str = "") -> tuple[str, str]:
        """抓取文本；返回 `(正文, 可读错误)`，**绝不抛异常**（requests 异常也不外泄）。"""
        if not url:
            return "", "地址为空"
        try:
            kwargs = {"referer": referer} if referer else {}
            return self.http.get_text(url, **kwargs), ""
        except Exception as error:  # noqa: BLE001  网络/解析异常一律转成可读说明
            return "", str(error) or error.__class__.__name__

    def _network_note(self, url: str, error: str, depth: int) -> str:
        where = "iframe 子页面" if depth else "播放页"
        return (f"抓取{where}失败（{host_of(url) or url}）：{error}。"
                "可稍后重试；若浏览器里能正常打开，说明该站对非浏览器请求做了限制。")

    def _failure_note(self, url: str, depth: int, failures: list[str]) -> str:
        tried = "、".join(failures) if failures else "（无）"
        head = "页面里没有可静态提取的媒体地址"
        if depth:
            head = f"第 {depth + 1} 层页面里没有可静态提取的媒体地址"
        return (f"{head}：已尝试 {tried}。该站可能依赖 JS 渲染（我们无浏览器内核），"
                "可尝试在浏览器中打开该页并复制地址后手动播放。")

    @staticmethod
    def _origin(url: str) -> str:
        parts = urlsplit(url or "")
        if not parts.scheme or not parts.netloc:
            return ""
        return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


__all__ = [
    "STRATEGIES",
    "Sniffer",
    "decode_media_literal",
    "extract_iframes",
    "extract_player_aaaa",
    "extract_player_endpoints",
    "find_media_urls",
]
