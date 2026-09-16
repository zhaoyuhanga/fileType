"""本地媒体 HTTP 流服务（供内嵌 <video> 播放）。

- 仅监听 127.0.0.1 随机端口，随应用懒启动（守护线程）；
- 本地文件：支持 Range 请求（拖动进度条）；
- **远程直链代理**：`/proxy/?u=...`，用于 m3u8/mp4 等地址 ——
  视频站普遍校验 Referer/UA，代理在服务端补上请求头，浏览器端就能直接播；
- **HLS 代理**：`/hls/?u=...`，抓取 m3u8 并把清单内的分片/子清单地址
  重写为继续走本代理，从而让 <video> + hls.js 能带正确请求头播放；
- 只服务本机绝对路径且扩展名在媒体白名单内的本地文件；
- URL 内含每次运行随机生成的令牌，避免同机其他进程枚举读取任意媒体文件。
"""
from __future__ import annotations

import os
import re
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

import requests

_MEDIA_EXTS = {
    ".mp4", ".mov", ".avi", ".m4a", ".mp3", ".wav", ".webm", ".mkv", ".m4v",
    ".flac", ".aac", ".ogg", ".opus", ".wma",
}
_MIME = {
    ".mp4": "video/mp4", ".m4v": "video/x-m4v", ".mov": "video/quicktime",
    ".webm": "video/webm", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
    ".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav",
    ".flac": "audio/flac", ".aac": "audio/aac", ".ogg": "audio/ogg",
    ".opus": "audio/opus", ".wma": "audio/x-ms-wma",
}

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_TOKEN = secrets.token_urlsafe(18)
_PREFIX = f"/{_TOKEN}/media/"
_PROXY_PREFIX = f"/{_TOKEN}/proxy/"
_HLS_PREFIX = f"/{_TOKEN}/hls/"

# 代理请求上游时的超时（流式响应，读超时给宽松一些）
_UPSTREAM_TIMEOUT = (10.0, 60.0)
_CHUNK = 64 * 1024

_lock = threading.Lock()
_server: ThreadingHTTPServer | None = None
_base_url: str | None = None


def _upstream_headers(referer: str = "", extra: str = "") -> dict:
    headers = {"User-Agent": _UA, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    if extra:
        for line in extra.split("|"):
            key, _, value = line.partition("=")
            if key.strip() and value.strip():
                headers[key.strip()] = value.strip()
    return headers


def _upstream_get(url: str, headers: dict, *, attempts: int = 3) -> requests.Response | None:
    """带退避重试的取流请求。

    第三方影视站点抖动明显（连接被重置、瞬时 SSL EOF），
    单次失败就返回 502 会让 ffmpeg/hls.js 直接放弃该分片，
    因此这里重试；仍失败才返回 None 由调用方报错。
    """
    last: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            response = requests.get(url, headers=headers, stream=True,
                                    timeout=_UPSTREAM_TIMEOUT, allow_redirects=True)
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            last = error
            if attempt < attempts:
                time.sleep(0.6 * attempt)
    if last is not None and os.environ.get("MODU_MEDIA_LOG"):
        print(f"[media] upstream failed after {attempts} attempts: {url[:100]} -> {last}",
              file=sys.stderr)
    return None


def _rewrite_key_uri(line: str, base_url: str, referer: str, extra: str) -> str:
    """改写 `#EXT-X-KEY` 里的 URI，让密钥也走本机代理。

    AES-128 加密流必须取到密钥才能解密；若密钥 URI 仍指向原站，
    浏览器会因跨域/缺少 Referer 而失败（hls.js 报 `keyLoadError`）。
    """
    marker = 'URI="'
    head, sep, tail = line.partition(marker)
    if not sep:
        return line
    uri, sep2, rest = tail.partition('"')
    if not uri:
        return line
    from urllib.parse import urljoin

    absolute = uri if uri.lower().startswith(("http://", "https://")) else urljoin(base_url, uri)
    return f"{head}{marker}{_origin()}{_proxy_path(absolute, referer, extra)}{sep2}{rest}"


def _rewrite_playlist(text: str, base_url: str, referer: str, extra: str) -> str:
    """把 m3u8 里所有 URI 重写为继续走本机代理（保留请求头）。

    既包括普通行（分片 / 子清单），也包括 `#EXT-X-KEY` 的密钥 URI。
    """
    out_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            out_lines.append(line)
            continue
        if stripped.startswith("#"):
            if stripped.startswith("#EXT-X-KEY") and 'URI="' in stripped:
                out_lines.append(_rewrite_key_uri(stripped, base_url, referer, extra))
            else:
                out_lines.append(line)
            continue
        # 非注释行 = URI（分片或子清单）
        absolute = stripped
        if not absolute.lower().startswith(("http://", "https://")):
            from urllib.parse import urljoin

            absolute = urljoin(base_url, stripped)
        out_lines.append(_origin() + _hls_path(absolute, referer, extra))
    return "\n".join(out_lines)


def _origin() -> str:
    """本机流服务的源（scheme://host:port）。

    清单内改写出的 URI 一律用绝对地址最稳妥：hls.js 依赖清单地址解析相对路径，
    播放器与 ffmpeg 也都不接受相对路径。
    """
    base = ensure_server()          # http://127.0.0.1:port/<token>/media/
    return base.split("/media/", 1)[0].rsplit("/", 1)[0]


def _hls_path(target: str, referer: str = "", extra: str = "") -> str:
    """构造 HLS 代理路径（**不含** origin，由调用方补全，避免重复拼接）。"""
    ensure_server()                 # 保证服务已就绪
    params = {"u": target}
    if referer:
        params["r"] = referer
    if extra:
        params["h"] = extra
    return f"{_HLS_PREFIX}{_name_hint(target)}?{urlencode(params)}"


def _proxy_path(target: str, referer: str = "", extra: str = "") -> str:
    """构造直通代理路径（**不含** origin，由调用方补全）。"""
    ensure_server()
    params = {"u": target}
    if referer:
        params["r"] = referer
    if extra:
        params["h"] = extra
    return f"{_PROXY_PREFIX}{_name_hint(target)}?{urlencode(params)}"


def _name_hint(target: str) -> str:
    """取出目标 URL 的文件名，让代理路径以真实扩展名结尾。

    必要性：ffmpeg 的 HLS 解复用器按 URL 的扩展名判断分片类型，
    形如 `/proxy/?u=...` 的地址会被判为「不在 allowed_segment_extensions」
    而直接拒绝（报 Invalid data）。因此代理地址必须以 `.ts` 这类扩展名收尾。
    服务端只按路径前缀分发，附加的文件名不影响处理。
    """
    try:
        path = urlsplit(target).path
    except ValueError:
        return ""
    name = path.rsplit("/", 1)[-1]
    if "." not in name or len(name) > 64:
        return ""
    # 只保留安全字符，避免把奇怪内容带进 URL
    return re.sub(r"[^A-Za-z0-9._-]", "", name)


def wrap_playlist(text: str, base_url: str, referer: str = "", headers: dict | None = None) -> str:
    """把 m3u8 内容改写为「全部经本机代理」的形式（绝对地址）。

    供 ffmpeg 下载与调试使用：请求头由本机流服务在服务端补，
    因此 ffmpeg 不需要任何 HTTP 选项（跨版本稳定）。
    """
    extra = "|".join(f"{key}={value}" for key, value in (headers or {}).items() if value)
    return _rewrite_playlist(text, base_url, referer, extra)


def _make_handler() -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):  # type: ignore[no-redef]
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch(head_only=False)

        def do_HEAD(self) -> None:  # noqa: N802
            self._dispatch(head_only=True)

        # ---------- 分发 ----------

        def _dispatch(self, *, head_only: bool) -> None:
            path = unquote(urlsplit(self.path).path)
            if path.startswith(_HLS_PREFIX):
                self._serve_hls(head_only=head_only)
            elif path.startswith(_PROXY_PREFIX):
                self._serve_proxy(head_only=head_only)
            else:
                self._serve_local(head_only=head_only)

        # ---------- 错误响应 ----------

        def _fail(self, code: int, message: str = "") -> None:
            """显式给正文长度，避免 HTTP/1.1 keep-alive 下客户端等待。"""
            body = (message or "").encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if body:
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

        # ---------- 本地文件（Range） ----------

        def _serve_local(self, head_only: bool = False) -> None:
            try:
                path = unquote(urlsplit(self.path).path)
                if not path.startswith(_PREFIX):
                    self._fail(404, "not found")
                    return
                file_path = Path(path[len(_PREFIX):])
                ext = file_path.suffix.lower()
                if ext not in _MEDIA_EXTS or not file_path.is_file():
                    self._fail(404, "not found")
                    return
                size = file_path.stat().st_size
                start, end, status = 0, size - 1, 200
                range_header = self.headers.get("Range")
                if range_header and range_header.startswith("bytes="):
                    parts = range_header[6:].split("-", 1)
                    if parts[0].strip().isdigit():
                        start = int(parts[0])
                        if len(parts) == 2 and parts[1].strip().isdigit():
                            end = int(parts[1])
                        status = 206
                    elif len(parts) == 2 and parts[1].strip().isdigit() and not parts[0].strip():
                        suffix = int(parts[1])
                        start = max(0, size - suffix)
                        status = 206
                end = min(end, size - 1)
                if start > end or start >= size:
                    self._fail(416, "range not satisfiable")
                    return
                length = end - start + 1
                self.send_response(status)
                self.send_header("Content-Type", _MIME.get(ext, "application/octet-stream"))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(length))
                if status == 206:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if head_only:
                    return
                with open(file_path, "rb") as fh:
                    fh.seek(start)
                    remaining = length
                    while remaining > 0:
                        data = fh.read(min(_CHUNK, remaining))
                        if not data:
                            break
                        self.wfile.write(data)
                        remaining -= len(data)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        # ---------- 远程直链代理 ----------

        def _serve_proxy(self, head_only: bool = False) -> None:
            query = parse_qs(urlsplit(self.path).query)
            target = (query.get("u") or [""])[0]
            referer = (query.get("r") or [""])[0]
            extra = (query.get("h") or [""])[0]
            if not target.lower().startswith(("http://", "https://")):
                self._fail(400, "missing target url")
                return
            headers = _upstream_headers(referer, extra)
            range_header = self.headers.get("Range")
            if range_header:
                headers["Range"] = range_header
            upstream = _upstream_get(target, headers)
            if upstream is None:
                self._fail(502, "upstream request failed")
                return
            with upstream:
                if upstream.status_code >= 400:
                    self._fail(upstream.status_code, "upstream error")
                    return
                content_type = upstream.headers.get("Content-Type", "application/octet-stream")

                # 关键：HTTP/1.1 下响应必须能界定长度。真实源站常用 chunked 且不带
                # Content-Length，若原样转发，客户端（ffmpeg / hls.js / QMediaPlayer）
                # 就永远等不到「响应结束」——表现为下载卡 0 字节、播放 fragLoadError。
                # 因此统一先读全量、显式给出 Content-Length。
                try:
                    body = upstream.content
                except requests.RequestException:
                    self._fail(502, "upstream read failed")
                    return

                # 目标其实是清单（如主清单里的子清单）时，同样要改写内部地址，
                # 否则相对路径会以「本机代理」为基准解析成 404。
                if b"#EXTM3U" in body[:512]:
                    body = _rewrite_playlist(
                        body.decode("utf-8", "replace"), upstream.url or target, referer, extra
                    ).encode("utf-8")
                    content_type = "application/vnd.apple.mpegurl"

                self.send_response(upstream.status_code)
                self.send_header("Content-Type", content_type)
                self.send_header("Accept-Ranges", upstream.headers.get("Accept-Ranges", "bytes"))
                self.send_header("Content-Length", str(len(body)))
                if upstream.headers.get("Content-Range"):
                    self.send_header("Content-Range", upstream.headers["Content-Range"])
                # hls.js 运行在 QWebEngine 页面里（源与本服务不同），分片必须带 CORS 头，
                # 否则清单能加载、每个分片却被浏览器拦掉（表现为 fragLoadError）
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range, Accept-Ranges")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if head_only:
                    return
                try:
                    self.wfile.write(body)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, requests.RequestException):
                    pass

        # ---------- HLS 清单代理 ----------

        def _serve_hls(self, head_only: bool = False) -> None:
            query = parse_qs(urlsplit(self.path).query)
            target = (query.get("u") or [""])[0]
            referer = (query.get("r") or [""])[0]
            extra = (query.get("h") or [""])[0]
            if not target.lower().startswith(("http://", "https://")):
                self._fail(400, "missing target url")
                return
            headers = _upstream_headers(referer, extra)
            upstream = _upstream_get(target, headers)
            if upstream is None:
                # 清单也可能被当作直链（极端情况）→ 退回直通代理
                self._serve_proxy(head_only=head_only)
                return
            try:
                text = upstream.text
                base = upstream.url or target
            finally:
                upstream.close()
            # 是清单就重写；不是（比如拿到的其实是媒体文件）就原样透传
            if "#EXTM3U" in text[:512]:
                body = _rewrite_playlist(text, base, referer, extra).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if not head_only:
                    try:
                        self.wfile.write(body)
                        # 必须 flush：http.server 的 wfile 有 64KB 缓冲，
                        # 媒体清单常达数百 KB，不 flush 客户端只会收到前 64KB 的截断内容
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        pass
                return
            self._serve_proxy(head_only=head_only)

        def log_message(self, fmt, *args) -> None:  # noqa: ANN002
            # MODU_MEDIA_LOG=1 时打印访问日志（排查播放/下载取流问题用）
            if os.environ.get("MODU_MEDIA_LOG"):
                print("[media] " + (fmt % args), file=sys.stderr)
            return

    return Handler


def ensure_server() -> str:
    """启动本地流服务（幂等），返回 base URL（本地文件前缀）。"""
    global _server, _base_url
    if _base_url:
        return _base_url
    with _lock:
        if _base_url:
            return _base_url
        handler = _make_handler()
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        port = server.server_address[1]
        _server = server
        _base_url = f"http://127.0.0.1:{port}{_PREFIX}"
        thread = threading.Thread(target=server.serve_forever, name="modu-media-server", daemon=True)
        thread.start()
        return _base_url


def media_url(file_path: str) -> str:
    """返回可在 <video src> 中使用的本地流地址。"""
    base = ensure_server()
    return base + quote(str(Path(file_path)), safe="")


def is_local_url(url: str) -> bool:
    """是否已经是本机流服务地址（避免重复代理）。"""
    return url.startswith("http://127.0.0.1:") and (_PREFIX in url or _PROXY_PREFIX in url or _HLS_PREFIX in url)


def stream_url(url: str, referer: str = "", headers: dict | None = None) -> str:
    """把远程地址包装成带 Referer/UA 的本机播放地址（绝对 URL）。

    已经是本机地址时原样返回；空地址返回空串。
    """
    if not url:
        return ""
    if is_local_url(url):
        return url
    extra = "|".join(f"{key}={value}" for key, value in (headers or {}).items() if value)
    return _origin() + _proxy_path(url, referer, extra)


def hls_url(url: str, referer: str = "", headers: dict | None = None) -> str:
    """把 m3u8 地址包装成经过本机清单重写的地址（适合 hls.js 播放，绝对 URL）。"""
    if not url:
        return ""
    extra = "|".join(f"{key}={value}" for key, value in (headers or {}).items() if value)
    return _origin() + _hls_path(url, referer, extra)


def local_base_url() -> str:
    """本机流服务前缀（调试用）。"""
    return ensure_server()
