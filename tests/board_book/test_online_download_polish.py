"""在线书库下载：可取消、失败不留半本书、同名不覆盖。"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from modu_workbench.boards.book.online import OnlineDownloadPage
from modu_workbench.core.book.online import (
    ChapterInfo,
    DownloadCancelled,
    NovelDetail,
    NovelSource,
    unique_download_path,
)
from modu_workbench.ui_kit.toast import Toaster


def _source(chapters: int = 5) -> NovelSource:
    source = NovelSource("https://example.com")
    detail = NovelDetail(
        title="测试书", author="作者", description="简介", url="https://example.com/book/1",
        chapters=[ChapterInfo(title=f"第{i}章", url=f"https://example.com/ch/{i}", index=i)
                  for i in range(chapters)],
    )
    source.get_catalog = lambda _url: detail          # type: ignore[method-assign]
    source.get_chapter = lambda _url: ("章节名", "正文内容")  # type: ignore[method-assign]
    return source


def test_cancel_leaves_no_file_behind(tmp_path: Path) -> None:
    source = _source()
    with pytest.raises(DownloadCancelled):
        source.download_novel(
            "https://example.com/book/1", tmp_path,
            cancel=lambda: True, min_delay_sec=0,
        )
    assert list(tmp_path.iterdir()) == [], "取消后不该留下成品或 .part 半截文件"


def test_cancel_midway_removes_partial_file(tmp_path: Path) -> None:
    source = _source()
    calls = {"n": 0}

    def cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    with pytest.raises(DownloadCancelled):
        source.download_novel(
            "https://example.com/book/1", tmp_path,
            cancel=cancel, min_delay_sec=0,
        )
    assert list(tmp_path.iterdir()) == []


def test_same_title_download_never_overwrites(tmp_path: Path) -> None:
    source = _source(chapters=2)
    first = source.download_novel("https://example.com/book/1", tmp_path, min_delay_sec=0)
    first.write_text("手工修订版", encoding="utf-8")

    second = source.download_novel("https://example.com/book/1", tmp_path, min_delay_sec=0)
    assert second.name == "测试书 (2).txt"
    assert first.read_text(encoding="utf-8") == "手工修订版", "旧下载不能被静默覆盖"
    assert second.exists()


def test_unique_download_path_skips_existing(tmp_path: Path) -> None:
    base = tmp_path / "书.txt"
    base.write_text("x", encoding="utf-8")
    assert unique_download_path(base).name == "书 (2).txt"
    (tmp_path / "书 (2).txt").write_text("x", encoding="utf-8")
    assert unique_download_path(base).name == "书 (3).txt"


class _FakeStreamResponse:
    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks
        self.headers = {"Content-Length": str(sum(len(chunk) for chunk in chunks))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int = 65536):
        yield from self._chunks


class _FakeSession:
    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    def get(self, *_args, **_kwargs):
        return _FakeStreamResponse(self._chunks)


def test_full_txt_cancel_removes_part_file(tmp_path: Path) -> None:
    source = NovelSource("https://example.com")
    source._session = _FakeSession([b"AAAA", b"BBBB"])  # type: ignore[assignment]
    target = tmp_path / "整本.txt"
    seen = {"n": 0}

    def cancel() -> bool:
        seen["n"] += 1
        return seen["n"] > 1

    with pytest.raises(DownloadCancelled):
        source.download_full_txt("https://down.example.com/x.txt", target, cancel=cancel)
    assert not target.exists()
    assert not target.with_name("整本.txt.part").exists()


def test_taskbar_cancel_routes_to_download_worker(qapp: QApplication, tmp_path: Path) -> None:
    from PySide6.QtWidgets import QWidget

    from modu_workbench.core.book import Library, Storage

    library = Library(Storage(str(tmp_path / "lib.db")))
    host = QWidget()
    page = OnlineDownloadPage(library, Toaster(host))
    try:
        cancelled: list[bool] = []

        class DummyWorker:
            def cancel(self) -> None:
                cancelled.append(True)

            def wait(self, _ms: int) -> bool:  # noqa: D401
                return True

        page._worker = DummyWorker()  # type: ignore[assignment]  # noqa: SLF001
        assert page.cancel_download() is True
        assert cancelled == [True]
        page.shutdown()  # 收尾不应因 Dummy 而崩
    finally:
        page.close()
        library.storage.close()
