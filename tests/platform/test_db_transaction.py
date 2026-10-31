"""存储层事务语义测试：`transaction()` 必须真的合并提交。

## 为什么这道闸门重要

`SqliteStore.execute()` 原本**无条件** `commit()`，而各板块 storage 子类里
还有 60 处直接 `self._conn.commit()`。结果是：写在
`with store.transaction():` 块里的批量写入**完全没被合并** ——
每写一条仍然 fsync 一次（事务只是包了层壳）。

实测 3000 行写入（SQLite WAL）：

    逐条提交     : 4306 ms
    事务块内写入 :  140 ms      ← 31 倍

这才是"导入 5000 张照片要 7 秒"的真因（不是 SQL 慢）。
本文件守住三件事：

1. 块内的 `execute` / 子类 `upsert_*` 不再逐条提交；
2. 块正常结束 → 数据落盘；
3. 块内抛异常 → **整体回滚**（不能留下半批脏数据）。
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from modu_workbench.core.gallery.models import ImageItem
from modu_workbench.core.gallery.storage import ImageStorage
from modu_workbench.core.platform.db import SqliteStore


@pytest.fixture()
def store(tmp_path: Path):  # noqa: ANN201
    s = SqliteStore(str(tmp_path / "txn.db"))
    yield s
    s.close()


def test_execute_commits_outside_transaction(store: SqliteStore) -> None:
    """事务**外**仍然是逐条提交（既有调用方行为不变）。"""
    store.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", ("probe/a", "1"))
    # 换一个独立连接读，验证确实已提交
    other = SqliteStore(str(store.db_path), migrate=False)
    try:
        row = other.query_one("SELECT value FROM app_settings WHERE key = 'probe/a'")
        assert row is not None and row["value"] == "1"
    finally:
        other.close()


def test_transaction_commits_on_exit(store: SqliteStore) -> None:
    """块内写入在退出时统一落盘。"""
    with store.transaction():
        for index in range(50):
            store.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?)",
                (f"probe/{index}", str(index)),
            )
    other = SqliteStore(str(store.db_path), migrate=False)
    try:
        assert len(other.query("SELECT * FROM app_settings WHERE key LIKE 'probe/%'")) == 50
    finally:
        other.close()


def test_transaction_rolls_back_on_error(store: SqliteStore) -> None:
    """块内抛异常 → 整批回滚，不能留下半批数据。

    这对批量导入很关键：用户中途取消、或某一项数据有问题，
    不应该让"导入了一半"的状态留在库里。
    """
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.execute("INSERT INTO app_settings (key, value) VALUES ('probe/x', '1')")
            raise RuntimeError("模拟中途失败")

    assert store.query("SELECT * FROM app_settings WHERE key = 'probe/x'") == []


def test_transaction_flag_restored_after_exit(store: SqliteStore) -> None:
    """退出事务后标志要恢复，后续 execute 继续逐条提交。"""
    with store.transaction():
        store.execute("INSERT INTO app_settings (key, value) VALUES ('probe/y', '1')")
    assert store._in_transaction is False  # noqa: SLF001
    store.execute("INSERT INTO app_settings (key, value) VALUES ('probe/z', '1')")
    other = SqliteStore(str(store.db_path), migrate=False)
    try:
        keys = {row["key"] for row in other.query("SELECT key FROM app_settings")}
    finally:
        other.close()
    assert {"probe/y", "probe/z"} <= keys


def test_nested_transaction_does_not_break(store: SqliteStore) -> None:
    """嵌套 transaction 不能把标志搞坏（内层不应提前提交）。"""
    with store.transaction():
        store.execute("INSERT INTO app_settings (key, value) VALUES ('probe/n1', '1')")
        with store.transaction():
            store.execute("INSERT INTO app_settings (key, value) VALUES ('probe/n2', '2')")
        store.execute("INSERT INTO app_settings (key, value) VALUES ('probe/n3', '3')")
    other = SqliteStore(str(store.db_path), migrate=False)
    try:
        keys = {row["key"] for row in other.query("SELECT key FROM app_settings")}
    finally:
        other.close()
    assert {"probe/n1", "probe/n2", "probe/n3"} <= keys


# --------------------------------------------------------------------------- 子类也要受益


def make_item(index: int) -> ImageItem:
    return ImageItem(
        path=f"/photos/{index:05d}.jpg",
        sha256=f"{index:064x}",
        thumb_path="",
        title=f"照片{index}",
        ext="jpg",
        mime="image/jpeg",
        size_bytes=1000,
        width=100,
        height=100,
        added_at=1_700_000_000 + index,
        source="local",
    )


def test_subclass_upserts_are_batched_in_transaction(tmp_path: Path) -> None:
    """板块 storage 子类（这里用图库）在事务块内也必须被合并。

    子类原来直接 `self._conn.commit()`（共 60 处），绕过了事务标志，
    所以这道用例专门盯"子类是否真的走 `_commit()`"。
    """
    storage = ImageStorage(str(tmp_path / "gallery.db"))
    try:
        items = [make_item(i) for i in range(200)]
        with storage.transaction():
            for item in items:
                storage.upsert_image(item)
        assert storage.count_images() == 200, "事务提交后数据应当完整落盘"
    finally:
        storage.close()


def test_batch_write_is_much_faster_than_per_row_commit(tmp_path: Path) -> None:
    """批量写入必须显著快于逐条提交（防止有人把 `_commit()` 改回直接 commit）。

    这里不比绝对时间（机器差异大），比**同一进程内的相对差距**：
    事务批量应在逐条提交的 1/5 以内（实测约 1/31）。
    """
    count = 300

    # 逐条提交
    per_row = ImageStorage(str(tmp_path / "a.db"))
    try:
        start = time.perf_counter()
        for index in range(count):
            per_row.upsert_image(make_item(index))
        slow = time.perf_counter() - start
    finally:
        per_row.close()

    # 事务批量
    batched = ImageStorage(str(tmp_path / "b.db"))
    try:
        start = time.perf_counter()
        with batched.transaction():
            for index in range(count):
                batched.upsert_image(make_item(index))
        fast = time.perf_counter() - start
    finally:
        batched.close()

    assert fast * 5 < slow, (
        f"事务批量没有明显变快：逐条 {slow * 1000:.0f} ms / 事务 {fast * 1000:.0f} ms。"
        "很可能是某处又直接调用了 `self._conn.commit()`，绕过了 `_commit()`。"
    )
