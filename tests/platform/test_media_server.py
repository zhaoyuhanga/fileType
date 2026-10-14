"""本地媒体流服务测试：全量 + Range + 安全边界。

关于稳健性：这两个"应当 404"的用例原先断言的是**异常消息文本**
（`assert "HTTP Error 404" in str(exc)`）。这在满载跑全量套件时会偶发失败——
本机回环连接被重置时 `urlopen` 抛的是 `URLError` 而不是 `HTTPError`，
消息里自然没有 "HTTP Error 404"，于是"服务行为正确"却被判成失败。
现在改成断言**状态码**（有响应就回 `__exit__` 校验 `status`，
没响应就重试一次后接受 `URLError`），测的仍是同一个不变量：**非媒体/无令牌必须被拒**。
"""
from __future__ import annotations

import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from modu_workbench.services.media_server import media_url


def _status_or_none(url: str, *, headers: dict | None = None,
                    attempts: int = 3) -> int | None:
    """请求一个"应当被拒"的地址，返回状态码。

    - 拿到响应（含 4xx）→ 返回状态码；
    - 抛 `HTTPError`（`urlopen` 对 4xx/5xx 的正常行为）→ 返回它的 code；
    - 连接被重置等瞬时错误 → 短暂退避后重试，最终仍失败才返回 None。
    """
    last: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            with urlopen(Request(url, headers=headers or {}), timeout=20) as response:
                return int(response.status)
        except HTTPError as error:
            return int(error.code)
        except (URLError, ConnectionError, OSError) as error:
            last = error
            if attempt < attempts:
                time.sleep(0.15 * attempt)
    assert last is not None
    return None


def test_media_server_full_and_range(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    payload = b"0123456789abcdefghij"  # 20B 伪视频
    video.write_bytes(payload)
    url = media_url(str(video))

    full = urlopen(url)
    assert full.status == 200
    assert full.read() == payload

    req = Request(url, headers={"Range": "bytes=2-9"})
    with urlopen(req) as resp:
        assert resp.status == 206
        assert resp.headers.get("Content-Range") == "bytes 2-9/20"
        assert resp.read() == payload[2:10]


def test_media_server_rejects_other_files(tmp_path: Path) -> None:
    """非媒体扩展名一律 404（不能拿它读任意本机文件）。"""
    secret = tmp_path / "secret.txt"
    secret.write_text("no", encoding="utf-8")
    url = media_url(str(secret))
    assert _status_or_none(url) == 404


def test_media_server_rejects_path_outside_token(tmp_path: Path) -> None:
    """没有令牌前缀的路径必须 404（防止同机进程枚举读取媒体文件）。"""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"0123456789")
    url = media_url(str(video))
    host = url.split("/media/", 1)[0].rsplit("/", 1)[0]
    tail = url.split("/media/", 1)[1]
    forged = f"{host}/media/{tail}"
    assert forged != url
    assert _status_or_none(forged) == 404


def test_media_server_requires_session_token(tmp_path: Path) -> None:
    """回归：同一个文件在**带令牌**时能取到、**不带令牌**时 404。"""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"0123456789")
    url = media_url(str(video))

    with urlopen(url, timeout=20) as response:
        assert response.status == 200
        assert response.read() == b"0123456789"

    host = url.split("/media/", 1)[0].rsplit("/", 1)[0]
    tail = url.split("/media/", 1)[1]
    assert _status_or_none(f"{host}/media/{tail}") == 404
