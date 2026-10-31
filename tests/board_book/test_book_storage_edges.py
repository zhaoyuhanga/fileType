"""书库文件扫描（`core/book/files.py`）与存储层（`core/book/storage.py`）的边界测试。

这两个模块此前只有"正常路径"被测（导入一本书、读一章、删掉级联），
**边界条件一个都没有**：不存在的路径、大小写扩展名、重复导入、同名不同路径、
阅读位置、历史记录顺序、删书级联。恰恰是这些边界会在用户手里出事。

> 写这些用例时我按"猜"的签名写错过一次（`save_position` 实际要 4 个参数、
> 位置不存在单独的 getter，而是 `BookRecord.last_*` 字段）——
> 说明**没有测试保护时连 API 形状都容易记错**，这本身就是补测试的理由。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from modu_workbench.core.book.files import SUPPORTED_BOOK_EXTENSIONS, walk_book_files
from modu_workbench.core.book.storage import Storage

# --------------------------------------------------------------------------- 文件扫描


def test_supported_extensions_are_txt_and_epub() -> None:
    """书库只认 TXT / EPUB —— 加格式要同时改解析器，这条防止只改一半。"""
    assert set(SUPPORTED_BOOK_EXTENSIONS) == {".txt", ".epub"}


def test_walk_collects_supported_files_recursively(tmp_path: Path) -> None:
    """递归收集子目录里的书，非书文件（PDF/图片）忽略。"""
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "b.epub").write_bytes(b"x")
    (tmp_path / "c.pdf").write_text("x", encoding="utf-8")
    (tmp_path / "img.jpg").write_bytes(b"x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "d.txt").write_text("x", encoding="utf-8")

    found = walk_book_files([str(tmp_path)])
    names = sorted(Path(item).name for item in found)
    assert names == ["a.txt", "b.epub", "d.txt"]


def test_walk_is_case_insensitive_for_extensions(tmp_path: Path) -> None:
    """`.TXT` / `.EPUB` 也要认（Windows 用户常被大写扩展名坑到）。"""
    (tmp_path / "A.TXT").write_text("x", encoding="utf-8")
    (tmp_path / "B.EPUB").write_bytes(b"x")
    assert len(walk_book_files([str(tmp_path)])) == 2


def test_walk_dedupes_overlapping_inputs(tmp_path: Path) -> None:
    """同一个文件既直接传入、又通过父目录扫到时，只出现一次。"""
    book = tmp_path / "a.txt"
    book.write_text("x", encoding="utf-8")
    assert len(walk_book_files([str(book), str(tmp_path)])) == 1


def test_walk_ignores_missing_paths(tmp_path: Path) -> None:
    """不存在的路径静默跳过（用户可能拖进已删除的文件）。"""
    assert walk_book_files([str(tmp_path / "nope.txt")]) == []
    assert walk_book_files([]) == []


def test_walk_ignores_unsupported_file_passed_directly(tmp_path: Path) -> None:
    """直接传入一个 PDF 也不会被收进书库。"""
    pdf = tmp_path / "x.pdf"
    pdf.write_text("x", encoding="utf-8")
    assert walk_book_files([str(pdf)]) == []


# --------------------------------------------------------------------------- 存储层边界


@pytest.fixture()
def storage(tmp_path: Path) -> Storage:
    store = Storage(str(tmp_path / "library.db"))
    yield store
    store.close()


def test_upsert_returns_same_id_for_same_path(storage: Storage, tmp_path: Path) -> None:
    """同一路径重复导入返回同一个 id（书架不会出现两本一样的书）。"""
    path = str(tmp_path / "same.txt")
    assert storage.upsert_book(path, "标题", "作者", "txt") == \
        storage.upsert_book(path, "标题", "作者", "txt")


def test_upsert_updates_metadata_in_place(storage: Storage, tmp_path: Path) -> None:
    """重复导入时更新标题/作者（书改名后元数据要跟着变）。"""
    path = str(tmp_path / "a.txt")
    book_id = storage.upsert_book(path, "旧标题", "旧作者", "txt")
    storage.upsert_book(path, "新标题", "新作者", "txt")
    record = storage.get_book(book_id)
    assert record is not None and record.title == "新标题"


def test_different_paths_are_different_books(storage: Storage, tmp_path: Path) -> None:
    """同名但不同目录的两本书是两条记录（按路径去重，不是按书名）。"""
    first = storage.upsert_book(str(tmp_path / "x" / "同名.txt"), "同名", "甲", "txt")
    second = storage.upsert_book(str(tmp_path / "y" / "同名.txt"), "同名", "甲", "txt")
    assert first != second


def test_save_position_roundtrip(storage: Storage, tmp_path: Path) -> None:
    """阅读位置（章节 + 章内偏移 + 进度）能完整存取 —— 进度记忆是本板块核心价值。"""
    book_id = storage.upsert_book(str(tmp_path / "a.txt"), "标题", "作者", "txt")
    storage.save_position(book_id, 7, 1200, 0.42)

    record = storage.get_book(book_id)
    assert record is not None
    assert record.last_chapter_index == 7
    assert record.last_offset_in_chapter == 1200
    assert record.last_progress == pytest.approx(0.42)
    assert record.last_opened_at, "保存位置时应同时更新最后打开时间"


def test_position_defaults_for_unread_book(storage: Storage, tmp_path: Path) -> None:
    """没读过的书位置是 0 / 0.0，而不是 None（界面直接拿来用，不必判空）。"""
    book_id = storage.upsert_book(str(tmp_path / "b.txt"), "标题", "作者", "txt")
    record = storage.get_book(book_id)
    assert record is not None
    assert record.last_chapter_index == 0
    assert record.last_offset_in_chapter == 0
    assert record.last_progress == 0.0
    assert record.last_opened_at is None


def test_save_position_on_unknown_book_does_not_raise(storage: Storage) -> None:
    """给不存在的书写位置不炸（书被删后界面可能还会补一次保存）。"""
    storage.save_position(999_999, 1, 0, 0.1)      # 不应抛异常


def test_delete_book_is_by_id_and_idempotent(storage: Storage, tmp_path: Path) -> None:
    """删书接口叫 `delete_book`，且重复删除不报错（双击/重试很常见）。"""
    book_id = storage.upsert_book(str(tmp_path / "a.txt"), "标题", "作者", "txt")
    storage.delete_book(book_id)
    assert storage.get_book(book_id) is None
    storage.delete_book(book_id)                   # 再删一次也不该炸


def test_deleting_book_cascades_history(storage: Storage, tmp_path: Path) -> None:
    """删书要级联清历史，否则历史里会留一堆点不开的僵尸条目。"""
    book_id = storage.upsert_book(str(tmp_path / "a.txt"), "标题", "作者", "txt")
    storage.add_history(book_id, 1, "第一章")
    storage.delete_book(book_id)
    assert storage.list_history() == []


def test_history_is_newest_first(storage: Storage, tmp_path: Path) -> None:
    """历史按打开时间倒序（界面打开就是最近在读的那本）。"""
    first = storage.upsert_book(str(tmp_path / "1.txt"), "第一本", "作者", "txt")
    second = storage.upsert_book(str(tmp_path / "2.txt"), "第二本", "作者", "txt")
    storage.add_history(first, 1, "第一章")
    storage.add_history(second, 2, "第二章")

    entries = storage.list_history()
    assert len(entries) >= 2
    assert entries[0].book_title == "第二本"
    assert entries[0].book_id == second


def test_history_carries_book_title_snapshot(storage: Storage, tmp_path: Path) -> None:
    """历史条目自带书名（`list_history` 做了 join），界面不必逐条回表。"""
    book_id = storage.upsert_book(str(tmp_path / "a.txt"), "历史里的书名", "作者", "txt")
    storage.add_history(book_id, 1, "第一章")
    entries = storage.list_history()
    assert entries and entries[0].book_title == "历史里的书名"
    assert entries[0].chapter_title == "第一章"


def test_history_filter_by_book(storage: Storage, tmp_path: Path) -> None:
    """可以只看某一本书的历史（阅读器侧栏用）。"""
    first = storage.upsert_book(str(tmp_path / "1.txt"), "甲书", "作者", "txt")
    second = storage.upsert_book(str(tmp_path / "2.txt"), "乙书", "作者", "txt")
    storage.add_history(first, 1, "甲章")
    storage.add_history(second, 1, "乙章")

    only_first = storage.list_history(book_id=first)
    assert len(only_first) == 1
    assert only_first[0].book_title == "甲书"


def test_history_records_action_kind(storage: Storage, tmp_path: Path) -> None:
    """历史记录动作类型（open / chapter / progress），界面据此区分展示。"""
    book_id = storage.upsert_book(str(tmp_path / "a.txt"), "标题", "作者", "txt")
    storage.add_history(book_id, 0, "第一章", action="open")
    storage.add_history(book_id, 3, "第四章", action="chapter")
    actions = [entry.action for entry in storage.list_history(book_id=book_id)]
    assert "open" in actions and "chapter" in actions


def test_clear_history_can_target_one_book(storage: Storage, tmp_path: Path) -> None:
    """清历史可以只清一本（`clear_history(book_id=...)`）。"""
    first = storage.upsert_book(str(tmp_path / "1.txt"), "甲书", "作者", "txt")
    second = storage.upsert_book(str(tmp_path / "2.txt"), "乙书", "作者", "txt")
    storage.add_history(first, 1, "甲章")
    storage.add_history(second, 1, "乙章")

    storage.clear_history(book_id=first)

    remaining = storage.list_history()
    assert all(entry.book_id == second for entry in remaining)


def test_books_are_listed_in_stable_order(storage: Storage, tmp_path: Path) -> None:
    """书单顺序稳定（界面靠它渲染书架，顺序抖动会让人以为书乱序了）。"""
    for index in range(3):
        storage.upsert_book(str(tmp_path / f"{index}.txt"), f"书{index}", "作者", "txt")
    first = [record.id for record in storage.list_books()]
    second = [record.id for record in storage.list_books()]
    assert first == second
    assert len(first) == 3


def test_get_book_by_path(storage: Storage, tmp_path: Path) -> None:
    """按路径查书（重复导入前先查是否已在库中）。"""
    path = str(tmp_path / "a.txt")
    book_id = storage.upsert_book(path, "标题", "作者", "txt")
    record = storage.get_book_by_path(path)
    assert record is not None and record.id == book_id
    assert storage.get_book_by_path(str(tmp_path / "nope.txt")) is None
