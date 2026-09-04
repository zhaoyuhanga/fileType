"""本地媒体流服务测试：全量 + Range。"""
from __future__ import annotations

from pathlib import Path
from urllib.request import Request, urlopen

from modu_workbench.services.media_server import media_url


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
    secret = tmp_path / "secret.txt"
    secret.write_text("no", encoding="utf-8")
    url = media_url(str(secret))
    try:
        urlopen(url)
        raise AssertionError("应当拒绝非媒体文件")
    except Exception as exc:  # noqa: BLE001
        assert "HTTP Error 404" in str(exc)
