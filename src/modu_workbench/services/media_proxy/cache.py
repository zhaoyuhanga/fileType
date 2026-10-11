"""分片磁盘缓存：LRU + 配额 + Range 语义。

对应方案里的 V1-c / V1-d / V1-e / V1-l：

- **键**：默认 `sha1(url + '|' + range)`；调用方拿到"内容身份"（episode_key + 序号）
  时优先用它 —— 源站常给 URL 挂时间戳/签名（`?t=…&sign=…`），纯 URL 键会让
  "同一个分片"永远不命中（LunaTV-EE 用 `douban_id` 作键正是这个原因）。
- **值**：`<cache_dir>/<前2位>/<hash>.bin` + 同名 `.meta`（JSON 元数据）。
- **淘汰**：按 `last_access` 升序删除，直到总占用 ≤ 配额；启动时只扫一次目录。
- **Range**：带 `Range` 的请求独立成键（`|bytes=100-199`），只在 206 时缓存该区间；
  区间不匹配时**回源补齐**而不是拼半截（避免"半截缓存"坏播放）。

线程安全：所有公开方法都持锁；文件写入走 `tmp → replace` 原子替换。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import MAX_SEGMENT_BYTES, ProxyConfig, get_config


def cache_key(identity: str, byte_range: str = "") -> str:
    """缓存键：内容身份（或 URL）+ Range 的 sha1。

    `identity` 由调用方给出：有 episode 上下文时用
    `f"{episode_key}#{segment_index}"`，否则退回目标 URL。
    """
    payload = f"{identity}|{byte_range or ''}".encode("utf-8", "replace")
    return hashlib.sha1(payload).hexdigest()


@dataclass
class CacheEntry:
    """一条缓存记录（内存索引；`.meta` 是它的持久化形态）。"""

    key: str
    path: Path
    size: int
    content_type: str = ""
    status: int = 200
    content_range: str = ""
    url: str = ""
    created_at: float = 0.0
    last_access: float = 0.0

    def to_meta(self) -> dict:
        return {
            "key": self.key,
            "size": self.size,
            "content_type": self.content_type,
            "status": self.status,
            "content_range": self.content_range,
            "url": self.url,
            "created_at": self.created_at,
        }


class SegmentCache:
    """磁盘 LRU 缓存。"""

    def __init__(self, root: Path, quota_bytes: int, *, max_entry_bytes: int = 64 * 1024 * 1024):
        self.root = Path(root)
        self.quota_bytes = max(1, int(quota_bytes))
        self.max_entry_bytes = max(1, int(max_entry_bytes))
        self._lock = threading.RLock()
        self._entries: dict[str, CacheEntry] = {}
        self._total = 0
        self._scan()

    # ------------------------------------------------------------------ 内部

    def _scan(self) -> None:
        """启动时扫一遍缓存目录，重建内存索引（顺带清掉孤儿 .tmp）。"""
        self.root.mkdir(parents=True, exist_ok=True)
        for meta_path in self.root.glob("*/*.meta"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                blob = meta_path.with_suffix(".bin")
                if not blob.is_file():
                    meta_path.unlink(missing_ok=True)
                    continue
                size = int(meta.get("size") or blob.stat().st_size)
                entry = CacheEntry(
                    key=str(meta.get("key") or meta_path.stem),
                    path=blob,
                    size=size,
                    content_type=str(meta.get("content_type") or ""),
                    status=int(meta.get("status") or 200),
                    content_range=str(meta.get("content_range") or ""),
                    url=str(meta.get("url") or ""),
                    created_at=float(meta.get("created_at") or blob.stat().st_mtime),
                    last_access=blob.stat().st_mtime,
                )
                self._entries[entry.key] = entry
                self._total += entry.size
            except Exception:  # noqa: BLE001  单条坏元数据不影响整体缓存
                continue
        for tmp in self.root.glob("*/*.tmp"):
            tmp.unlink(missing_ok=True)
        with self._lock:
            self._evict_locked()

    def _path_for(self, key: str) -> Path:
        bucket = self.root / key[:2]
        bucket.mkdir(parents=True, exist_ok=True)
        return bucket / f"{key}.bin"

    def _evict_locked(self) -> None:
        if self._total <= self.quota_bytes:
            return
        ordered = sorted(self._entries.values(), key=lambda item: item.last_access)
        for entry in ordered:
            if self._total <= self.quota_bytes:
                break
            self._drop_locked(entry.key)

    def _drop_locked(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry is None:
            return
        self._total -= entry.size
        try:
            entry.path.unlink(missing_ok=True)
            entry.path.with_suffix(".meta").unlink(missing_ok=True)
        except OSError:
            pass

    # ------------------------------------------------------------------ 读

    def get(self, key: str) -> CacheEntry | None:
        """命中则刷新访问时间并返回条目；未命中返回 None。"""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if not entry.path.is_file():
                self._drop_locked(key)
                return None
            entry.last_access = time.time()
            return entry

    def read(self, entry: CacheEntry) -> bytes:
        try:
            return entry.path.read_bytes()
        except OSError:
            with self._lock:
                self._drop_locked(entry.key)
            return b""

    # ------------------------------------------------------------------ 写

    def put(self, key: str, data: bytes, *, content_type: str = "", status: int = 200,
            content_range: str = "", url: str = "") -> CacheEntry | None:
        """写入一条缓存；超过单条上限或超配额时返回 None（调用方照常转发）。"""
        if not data or len(data) > self.max_entry_bytes:
            return None
        with self._lock:
            target = self._path_for(key)
            tmp = target.with_suffix(".bin.tmp")
            try:
                tmp.write_bytes(data)
                tmp.replace(target)
            except OSError:
                tmp.unlink(missing_ok=True)
                return None
            now = time.time()
            # 同键覆盖：先扣掉旧体积
            old = self._entries.get(key)
            if old is not None:
                self._total -= old.size
            entry = CacheEntry(
                key=key, path=target, size=len(data), content_type=content_type,
                status=status, content_range=content_range, url=url,
                created_at=old.created_at if old is not None else now, last_access=now,
            )
            self._entries[key] = entry
            self._total += entry.size
            try:
                target.with_suffix(".meta").write_text(
                    json.dumps(entry.to_meta(), ensure_ascii=False), encoding="utf-8"
                )
            except OSError:
                pass
            self._evict_locked()
            return entry

    # ------------------------------------------------------------------ 维护

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return self._total

    def count(self) -> int:
        with self._lock:
            return len(self._entries)

    def stats(self) -> dict:
        with self._lock:
            return {
                "count": len(self._entries),
                "bytes": self._total,
                "quota_bytes": self.quota_bytes,
                "root": str(self.root),
            }

    def clear(self, *, keep_dir: bool = True) -> int:
        """清空缓存，返回清理的字节数。"""
        with self._lock:
            removed = self._total
            for key in list(self._entries):
                self._drop_locked(key)
            self._entries.clear()
            self._total = 0
        if not keep_dir:
            try:
                for child in self.root.glob("*"):
                    if child.is_dir():
                        for item in child.glob("*"):
                            item.unlink(missing_ok=True)
                        child.rmdir()
            except OSError:
                pass
        return removed


_cache: SegmentCache | None = None
_cache_lock = threading.Lock()
_cache_root: Path | None = None


def get_cache(config: ProxyConfig | None = None) -> SegmentCache:
    """进程级缓存单例（换目录时自动重建）。"""
    global _cache, _cache_root
    cfg = config or get_config()
    with _cache_lock:
        if _cache is None or _cache_root != cfg.cache_dir:
            _cache = SegmentCache(cfg.cache_dir, cfg.cache_quota_bytes,
                                  max_entry_bytes=MAX_SEGMENT_BYTES)
            _cache_root = cfg.cache_dir
        return _cache


def reset_cache() -> None:
    """仅供测试：丢弃单例（下次按当前配置重建）。"""
    global _cache, _cache_root
    with _cache_lock:
        _cache = None
        _cache_root = None


__all__ = ["CacheEntry", "SegmentCache", "cache_key", "get_cache", "reset_cache"]
