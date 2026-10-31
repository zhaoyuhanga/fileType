"""自研 HLS 分片下载器：并发抓取 + 单线程保序落盘 + 断点续传（方案 3.2.1）。

为什么要有它（对标结论 L5 的做法，也是"下载慢"的正解）：

| 旧实现 | 本模块 |
|---|---|
| 分片**严格串行**，每片 `requests.get` 新建连接 | `ThreadPoolExecutor` 并发（默认 6）+ **每线程一个 Session**（keep-alive） |
| 只重试 2 次，失败即整集重来 | 单分片 3 次退避重试；坏片率 > 2% 才判失败 |
| `.part` 只用于"写坏就删" | `.parts/` 目录 + `manifest.json`，**中断后跳过已完成分片继续下** |
| 无并发上限、无限速 | 并发可配 2–16 + 令牌桶限速（默认不限） |
| 只有百分比与 MB | 百分比 + 速度 + ETA + "第 M/N 片" |

关键工程点（照抄 LunaTV-EE 的"并发下载、串行落盘"）：
**分片下载并发、按序写入串行** —— 即使 8 路乱序完成，也不会把 TS 拼错。

暂停/继续用四个检查点（取清单后 / 每片开始前 / 每片写盘后 / 拼接前）等门闸，
`shutdown()` 保证线程退出且 `.parts/` 保留，因此"下几十集中途关掉、明天继续"成立。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import requests

from .hls import HlsPlaylist, parse_m3u8, segment_iv
from .models import Episode, Quality, RemoteVideo
from .sources import SourceError, describe_network_error
from modu_workbench.core.platform.logging import swallow

#: 默认并发（取 LunaTV-EE 的 6 worker 与 Kazumi "分片并发 3" 之间的折中）
DEFAULT_CONCURRENCY = 6
#: 单分片重试次数与退避（与 LunaTV-EE 的 1000ms/3 次同量级）
DEFAULT_RETRIES = 3
RETRY_BACKOFF = (0.3, 0.8, 1.5)
#: 坏片率超过这个值就判定整集失败（避免产出残片）
BAD_PART_RATIO = 0.02
#: 产物最小字节数：低于它基本是错误页/占位文件（分片本身的单文件下限见 hls 解析）
_MIN_RESULT_BYTES = 16 * 1024


@dataclass
class SegmentPlan:
    """一个待下载分片的元数据（也持久化进 manifest.json）。"""

    index: int
    url: str
    duration: float = 0.0
    expected_size: int = 0

    def to_meta(self) -> dict:
        return {
            "index": self.index,
            "url": self.url,
            "duration": self.duration,
            "expected_size": self.expected_size,
        }


@dataclass
class SegmentProgress:
    """一次进度回报。"""

    done_parts: int = 0
    total_parts: int = 0
    written_bytes: int = 0
    total_bytes: int = 0
    speed_bps: float = 0.0
    message: str = ""

    @property
    def percent(self) -> int:
        if self.total_bytes > 0:
            return min(100, int(self.written_bytes * 100 / self.total_bytes))
        if self.total_parts > 0:
            return min(100, int(self.done_parts * 100 / self.total_parts))
        return 0

    @property
    def eta_seconds(self) -> float:
        if self.speed_bps <= 0 or self.total_bytes <= 0:
            return 0.0
        remaining = max(0, self.total_bytes - self.written_bytes)
        return remaining / self.speed_bps


class RateLimiter:
    """令牌桶限速（默认不限速；`limit_bytes_per_second <= 0` 时完全跳过）。"""

    def __init__(self, limit_bytes_per_second: float = 0.0) -> None:
        self.limit = max(0.0, float(limit_bytes_per_second or 0.0))
        self._lock = threading.Lock()
        self._allowance = 0.0
        self._last = time.monotonic()

    def take(self, amount: int) -> None:
        if self.limit <= 0 or amount <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                self._allowance = min(self.limit, self._allowance + (now - self._last) * self.limit)
                self._last = now
                if self._allowance >= amount:
                    self._allowance -= amount
                    return
                deficit = amount - self._allowance
                sleep_for = min(1.0, deficit / self.limit)
            time.sleep(max(0.01, sleep_for))


class _WriteGate:
    """保序写入闸门。

    **只有专门的写线程会调用 `wait_turn`/`advance`**；下载线程把完成的分片
    放进一个字典，写线程按 `next_index` 顺序取走。这一点很关键：
    如果让"收集 future 的主循环"去等某个序号，它就会被一个慢分片卡住、
    无法继续收集其它已完成的分片（首版就在这里死锁过）。
    """

    def __init__(self, start_index: int = 0) -> None:
        self._condition = threading.Condition()
        self._next = start_index
        self._closed = False
        self._error: BaseException | None = None

    @property
    def next_index(self) -> int:
        with self._condition:
            return self._next

    def wait_turn(self, index: int, timeout: float = 300.0) -> bool:
        """等到轮到自己；返回 False 表示已关闭/出错。"""
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._next != index and not self._closed and self._error is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=min(0.25, remaining))
            return self._next == index and not self._closed and self._error is None

    def advance(self, index: int) -> None:
        with self._condition:
            if index == self._next:
                self._next += 1
            self._condition.notify_all()

    def fail(self, error: BaseException) -> None:
        with self._condition:
            self._error = error
            self._closed = True
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()


class _Paused(RuntimeError):
    """内部信号：命中了暂停门闸（不是错误，不该计入坏片）。"""


class _Cancelled(RuntimeError):
    """内部信号：用户取消。"""


@dataclass
class DownloadOutcome:
    """一次下载的结果。"""

    path: Path | None = None
    ok: bool = False
    message: str = ""
    parts_total: int = 0
    parts_done: int = 0
    bad_parts: int = 0
    bytes_written: int = 0
    elapsed: float = 0.0
    from_resume: int = 0

    @property
    def speed_bps(self) -> float:
        return self.bytes_written / self.elapsed if self.elapsed > 0 else 0.0


class SegmentDownloader:
    """并发分片下载器（详见模块 docstring）。"""

    def __init__(
        self,
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        retries: int = DEFAULT_RETRIES,
        timeout: float = 20.0,
        headers: dict | None = None,
        on_progress=None,  # noqa: ANN001  Callable[[SegmentProgress], None]
        cancel: threading.Event | None = None,
        pause_event: threading.Event | None = None,
        speed_limit_bps: float = 0.0,
        verify_size: bool = True,
        pause_timeout: float = 600.0,
    ) -> None:
        self.concurrency = max(1, min(16, int(concurrency or DEFAULT_CONCURRENCY)))
        self.retries = max(1, int(retries or DEFAULT_RETRIES))
        self.timeout = float(timeout or 20.0)
        self.headers = dict(headers or {})
        self.on_progress = on_progress
        self.cancel = cancel
        self.pause_event = pause_event
        self.pause_timeout = max(0.0, float(pause_timeout))
        self.limiter = RateLimiter(speed_limit_bps)
        self.verify_size = bool(verify_size)
        self._local = threading.local()
        self._sessions: list[requests.Session] = []
        self._sessions_lock = threading.Lock()

    # ------------------------------------------------------------------ Session

    def _session(self) -> requests.Session:
        """每线程一个 Session（requests 的 Session 非线程安全，但连接要复用）。"""
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(self.headers)
            self._local.session = session
            with self._sessions_lock:
                self._sessions.append(session)
        return session

    def close(self) -> None:
        with self._sessions_lock:
            sessions, self._sessions = self._sessions, []
        for session in sessions:
            try:
                session.close()
            except (OSError, ValueError) as error:  # 关闭失败可忽略
                swallow("释放资源", error)

    def __enter__(self) -> SegmentDownloader:
        return self

    def __exit__(self, *exc) -> None:  # noqa: ANN002
        self.close()

    # ------------------------------------------------------------------ 清单

    def fetch_playlist(self, url: str, referer: str = "") -> HlsPlaylist:
        """拉取并解析清单；主清单会挑一个变体再取一次。

        选中变体后把它的声明码率回填到媒体清单上 —— 分片下载器据此估算
        总字节数与 ETA（清单本身不含码率信息）。
        """
        text = self._get_text(url)
        playlist = parse_m3u8(text, url)
        if playlist.is_master:
            best = playlist.best_variant()
            if best is not None:
                text = self._get_text(best.url)
                playlist = parse_m3u8(text, best.url)
                playlist.bandwidth = int(getattr(best, "bandwidth", 0) or 0)
        return playlist

    def _get_text(self, url: str) -> str:
        response = self._session().get(url, timeout=self.timeout)
        response.raise_for_status()
        return response.text

    # ------------------------------------------------------------------ 下载

    def download(
        self,
        playlist: HlsPlaylist,
        dest: Path,
        *,
        resume: bool = True,
        target_suffix: str | None = None,
    ) -> DownloadOutcome:
        """下载整份清单，返回结果。

        `resume=True` 时复用 `<dest>.parts/` 里已完成的分片与 `manifest.json`。
        """
        started = time.monotonic()
        outcome = DownloadOutcome(path=Path(dest))
        target = Path(dest)
        parts_dir = target.with_suffix(target.suffix + ".parts")
        manifest_path = parts_dir / "manifest.json"

        plan = self._build_plan(playlist)
        if not plan:
            outcome.message = "m3u8 清单里没有分片（可能是直播流或链接已失效）"
            return outcome
        outcome.parts_total = len(plan)

        resumed = self._restore_progress(parts_dir, manifest_path, plan) if resume else 0
        outcome.from_resume = resumed
        # 先落 manifest 再等门闸：即使立刻暂停，`.parts/` 与 manifest 也已就位，
        # "保留分片下次继续"这句话才成立
        self._write_manifest(parts_dir, manifest_path, plan)

        # 暂停检查点①：取清单之后（允许等待 pause_timeout 秒）
        if not self._gate():
            outcome.message = "已暂停（分片已保留，可继续下载）"
            return outcome

        key = self._fetch_key(playlist)
        state = _ProgressState(total_parts=len(plan), total_bytes=self._estimate_total(plan))
        state.done_bytes = self._existing_bytes(parts_dir, plan)
        state.done_parts = resumed
        self._report(
            state,
            (
                f"分片 {resumed}/{len(plan)} 已在本地，继续下载…"
                if resumed
                else f"开始下载 {len(plan)} 个分片（并发 {self.concurrency}）"
            ),
        )

        gate = _WriteGate(start_index=resumed)
        bad: list[int] = []
        stopped = False  # 停止"补投新任务"（含正常的"全部投完"与异常）
        interrupted = False  # 真正没下完（坏片/取消/写盘失败）
        paused = False
        results: dict[int, bytes] = {}
        results_lock = threading.Condition()
        cur = [resumed]  # 写游标（只被写线程推进）
        writer_error: list[BaseException] = []

        def writer() -> None:
            """单写线程：严格按序号把已完成分片落盘（并发下载、串行落盘）。

            `results` 是**非破坏性**字典 —— 写线程只取 `cur` 那一片，
            因此乱序完成的分片会安全地排队等待，绝不会把 TS 拼错。
            """
            while True:
                with results_lock:
                    while cur[0] not in results and not stopped and cur[0] < len(plan):
                        results_lock.wait(timeout=0.2)
                    if cur[0] >= len(plan) or stopped:
                        return
                    payload = results[cur[0]]
                try:
                    self._append(parts_dir, cur[0], payload)
                except OSError as error:
                    writer_error.append(error)
                    gate.fail(error)
                    return
                state.add(len(payload), 1)
                gate.advance(cur[0])
                with results_lock:
                    cur[0] += 1
                    results_lock.notify_all()
                self._report(state)

        try:
            with ThreadPoolExecutor(
                max_workers=self.concurrency, thread_name_prefix="seg-dl"
            ) as pool:
                writer_thread = threading.Thread(target=writer, name="seg-dl-writer", daemon=True)
                writer_thread.start()
                pending: dict[Future, int] = {}
                queue = [item for item in plan if item.index >= resumed]
                cursor = 0

                def submit_more() -> None:
                    nonlocal cursor
                    while (
                        len(pending) < self.concurrency * 2 and cursor < len(queue) and not stopped
                    ):
                        item = queue[cursor]
                        cursor += 1
                        pending[pool.submit(self._fetch_part, item)] = item.index

                submit_more()
                while pending:
                    if self._cancelled():
                        stopped = True
                        interrupted = True
                    done, _ = wait(list(pending), timeout=0.2, return_when=FIRST_COMPLETED)
                    for future in done:
                        index = pending.pop(future)
                        try:
                            payload = future.result()
                        except _Paused:
                            paused = True
                            stopped = True
                            interrupted = True
                            continue
                        except _Cancelled:
                            stopped = True
                            interrupted = True
                            continue
                        except Exception as error:  # noqa: BLE001  坏片走这里
                            bad.append(index)
                            if self._should_abort(bad, plan):
                                stopped = True
                                interrupted = True
                                outcome.message = (
                                    f"坏片过多（{len(bad)}/{len(plan)}）：" f"{_short(error)}"
                                )
                            continue
                        if key is not None and payload:
                            payload = self._decrypt(payload, key, playlist, index)
                        with results_lock:
                            results[index] = payload
                            results_lock.notify_all()
                    if not stopped:
                        submit_more()
                # 全部任务已收集（无坏片时）：唤醒写线程并等它把分片落盘
                with results_lock:
                    results_lock.notify_all()
                writer_thread.join(timeout=120.0)
                with results_lock:
                    incomplete = cur[0] < len(plan)
                if incomplete:
                    # 中间缺片（坏片/取消）→ 后面的分片也没法按序落盘
                    stopped = True
                    interrupted = True
                    outcome.message = outcome.message or "部分分片未取到，已停止写入"
        finally:
            with results_lock:
                stopped = True
                results_lock.notify_all()
            gate.close()
        if writer_error:
            outcome.message = f"写盘失败：{writer_error[0]}"
            interrupted = True

        outcome.parts_done = state.done_parts
        outcome.bad_parts = len(bad)
        outcome.bytes_written = state.written_bytes
        outcome.elapsed = time.monotonic() - started
        if self._cancelled():
            outcome.message = "下载已取消"
            return outcome
        if paused:
            outcome.message = "已暂停（分片已保留，可继续下载）"
            return outcome
        if interrupted:
            outcome.message = outcome.message or "未完成"
            return outcome
        if bad and len(bad) / max(1, len(plan)) > BAD_PART_RATIO:
            outcome.message = f"坏片过多（{len(bad)}/{len(plan)}），已放弃以免产出残片"
            return outcome

        # 暂停检查点③：拼接前
        if not self._gate():
            outcome.message = "已暂停（分片已保留，可继续）"
            return outcome

        assembled = self._assemble(parts_dir, plan, target)
        if not assembled:
            outcome.message = "分片拼接失败"
            return outcome
        outcome.ok = True
        outcome.path = target
        outcome.message = f"完成：{target.name}（{target.stat().st_size / 1048576:.1f} MB）"
        self._report(state, outcome.message, force=True)
        return outcome

    # ------------------------------------------------------------------ 细节

    def _build_plan(self, playlist: HlsPlaylist) -> list[SegmentPlan]:
        durations = getattr(playlist, "segment_durations", None) or []
        expected = self._expected_size(playlist)
        plan: list[SegmentPlan] = []
        for index, url in enumerate(playlist.segments):
            duration = float(durations[index]) if index < len(durations) else 0.0
            plan.append(
                SegmentPlan(index=index, url=url, duration=duration, expected_size=expected)
            )
        return plan

    @staticmethod
    def _expected_size(playlist: HlsPlaylist) -> int:
        """按平均码率估算单片大小（进度与 ETA 用；拿不到就返回 0）。"""
        bandwidth = int(getattr(playlist, "bandwidth", 0) or 0)
        duration = float(getattr(playlist, "target_duration", 0.0) or 0.0)
        if bandwidth > 0 and duration > 0:
            return int(bandwidth * duration / 8)
        return 0

    @staticmethod
    def _estimate_total(plan: list[SegmentPlan]) -> int:
        total = sum(item.expected_size for item in plan if item.expected_size > 0)
        return total

    def _fetch_key(self, playlist: HlsPlaylist) -> bytes | None:
        if not playlist.encrypted:
            return None
        if not playlist.aes128:
            raise SourceError(
                f"该视频流使用 {playlist.key_method or '未知'} 加密，需要 ffmpeg 才能下载；"
                "请安装 ffmpeg 或改用其他源"
            )
        response = self._session().get(playlist.key_uri, timeout=self.timeout)
        response.raise_for_status()
        key = response.content
        if len(key) not in (16, 24, 32):
            raise SourceError(f"解密密钥长度异常（{len(key)} 字节），无法解密该视频流")
        return key

    @staticmethod
    def _decrypt(payload: bytes, key: bytes, playlist: HlsPlaylist, index: int) -> bytes:
        from .aes import aes128_cbc_decrypt

        try:
            return aes128_cbc_decrypt(payload, key[:16], segment_iv(playlist, index))
        except ValueError as error:
            raise SourceError(f"分片解密失败（第 {index + 1} 片）：{error}") from error

    def _fetch_part(self, item: SegmentPlan) -> bytes:
        """抓一个分片（含重试与限速）；失败抛最后一个异常。"""
        last: Exception | None = None
        for attempt in range(1, self.retries + 1):
            if self._cancelled():
                raise _Cancelled("下载已取消")
            # 暂停检查点②：每片开始前等门闸（**不等待**，立刻退出让线程池收敛）
            if not self._gate(timeout=0.0):
                if self._cancelled():
                    raise _Cancelled("下载已取消")
                raise _Paused("下载已暂停")
            try:
                response = self._session().get(item.url, timeout=self.timeout)
                response.raise_for_status()
                self.limiter.take(len(response.content))
                return response.content
            except requests.RequestException as error:
                last = error
                if attempt < self.retries:
                    time.sleep(RETRY_BACKOFF[min(attempt - 1, len(RETRY_BACKOFF) - 1)])
        host = urlsplit(item.url).hostname or ""
        raise SourceError(describe_network_error(last or Exception("未知网络错误"), host))

    def _append(self, parts_dir: Path, index: int, payload: bytes) -> None:
        parts_dir.mkdir(parents=True, exist_ok=True)
        path = parts_dir / f"{index:06d}.part"
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(payload)
        tmp.replace(path)

    def _existing_bytes(self, parts_dir: Path, plan: list[SegmentPlan]) -> int:
        total = 0
        for item in plan:
            path = parts_dir / f"{item.index:06d}.part"
            if path.is_file():
                total += path.stat().st_size
        return total

    def _restore_progress(
        self, parts_dir: Path, manifest_path: Path, plan: list[SegmentPlan]
    ) -> int:
        """断点续传：返回"从第几片开始"（前面都已存在且尺寸合理）。

        校验规则：存在 + 非空（拿到期望大小时要求 ≥ 50%）。宽度放宽是有意的 ——
        分片大小受码率波动影响，卡太死会让续传永远失效。
        """
        if not parts_dir.is_dir():
            return 0
        if manifest_path.is_file():
            try:
                meta = json.loads(manifest_path.read_text(encoding="utf-8"))
                if int(meta.get("total_parts") or 0) not in (0, len(plan)):
                    return 0  # 清单变了（集数/分片数不同）→ 不续传，避免拼错
            except Exception:  # noqa: BLE001
                return 0
        index = 0
        for item in plan:
            path = parts_dir / f"{item.index:06d}.part"
            if not path.is_file() or path.stat().st_size <= 0:
                break
            if self.verify_size and item.expected_size > 0:
                if path.stat().st_size < item.expected_size * 0.5:
                    break
            index += 1
        return index

    def _write_manifest(
        self, parts_dir: Path, manifest_path: Path, plan: list[SegmentPlan]
    ) -> None:
        try:
            parts_dir.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(
                json.dumps(
                    {
                        "total_parts": len(plan),
                        "created_at": int(time.time()),
                        "parts": [item.to_meta() for item in plan],
                    },
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _assemble(self, parts_dir: Path, plan: list[SegmentPlan], target: Path) -> bool:
        """按序拼接 `.part` 为目标文件；成功后才删除分片目录。"""
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".assembling")
        try:
            with open(tmp, "wb") as handle:
                for item in plan:
                    part = parts_dir / f"{item.index:06d}.part"
                    if not part.is_file():
                        raise SourceError(f"缺少分片 {item.index}，无法拼接")
                    with open(part, "rb") as source:
                        shutil.copyfileobj(source, handle, length=1024 * 1024)
            if tmp.stat().st_size < _MIN_RESULT_BYTES:
                tmp.unlink(missing_ok=True)
                return False
            tmp.replace(target)
        except Exception:  # noqa: BLE001
            tmp.unlink(missing_ok=True)
            return False
        shutil.rmtree(parts_dir, ignore_errors=True)
        return True

    # ------------------------------------------------------------------ 门闸与进度

    def _cancelled(self) -> bool:
        return self.cancel is not None and self.cancel.is_set()

    def _gate(self, *, timeout: float | None = None) -> bool:
        """暂停门闸（检查点用）：`pause_event` 被 set 时阻塞直到清除或取消。

        检查点①（清单之后 / 拼接之前）允许等待 `self.pause_timeout` 秒 ——
        用户点"暂停"后打算继续下载时不必重来；超时仍未恢复就干净退出并保留分片。
        检查点②（每片开始前）**不等待**：工作线程看到暂停立刻退出，
        否则线程池迟迟不收敛，UI 的"已暂停"要等很久才出现。
        """
        if self.pause_event is None:
            return not self._cancelled()
        budget = self.pause_timeout if timeout is None else float(timeout)
        deadline = time.monotonic() + max(0.0, budget)
        while self.pause_event.is_set():
            if self._cancelled():
                return False
            if time.monotonic() > deadline:
                return False
            time.sleep(0.05)
        return not self._cancelled()

    def _report(self, state: _ProgressState, message: str = "", *, force: bool = False) -> None:
        if self.on_progress is None:
            return
        now = time.monotonic()
        force = force or state.done_parts >= state.total_parts or state.done_parts <= 1
        if not force and now - state.last_report < 0.4:
            return
        state.last_report = now
        speed = state.speed()
        progress = SegmentProgress(
            done_parts=state.done_parts,
            total_parts=state.total_parts,
            written_bytes=state.written_bytes,
            total_bytes=state.total_bytes,
            speed_bps=speed,
            message=message or _progress_text(state, speed),
        )
        try:
            self.on_progress(progress)
        except Exception as error:  # noqa: BLE001  外部回调/第三方边界
            swallow("segment_downloader 中的可选步骤", error)

    def _should_abort(self, bad: list[int], plan: list[SegmentPlan]) -> bool:
        return len(bad) / max(1, len(plan)) > BAD_PART_RATIO


@dataclass
class _ProgressState:
    total_parts: int
    total_bytes: int = 0
    done_parts: int = 0
    done_bytes: int = 0
    written_bytes: int = 0
    started: float = field(default_factory=time.monotonic)
    last_report: float = 0.0

    def add(self, size: int, parts: int = 1) -> None:
        self.written_bytes += size
        self.done_bytes += size
        self.done_parts += parts

    def speed(self) -> float:
        elapsed = max(0.001, time.monotonic() - self.started)
        return self.written_bytes / elapsed


def _progress_text(state: _ProgressState, speed: float) -> str:
    total = state.total_parts
    head = f"分片 {state.done_parts}/{total}"
    size = f"{state.done_bytes / 1048576:.1f} MB"
    parts = [head, size]
    if speed > 0:
        parts.append(f"{speed / 1048576:.1f} MB/s")
    if state.total_bytes > 0:
        percent = min(100, int(state.done_bytes * 100 / state.total_bytes))
        parts.append(f"{percent}%")
        if speed > 0:
            remaining = max(0, state.total_bytes - state.done_bytes)
            eta = remaining / speed
            parts.append(f"剩余 {_format_eta(eta)}")
    return "（" + "，".join(parts) + "）"


def _format_eta(seconds: float) -> str:
    total = int(max(0, seconds))
    if total >= 3600:
        return f"{total // 3600} 小时 {(total % 3600) // 60} 分"
    if total >= 60:
        return f"{total // 60} 分 {total % 60} 秒"
    return f"{total} 秒"


def _short(error: BaseException, limit: int = 80) -> str:
    text = str(error).replace("\n", " ").strip()
    return text[:limit] + ("…" if len(text) > limit else "")


# --------------------------------------------------------------------------- 便捷入口


def build_headers(url: str = "", referer: str = "") -> dict:
    from .downloader import default_headers

    return default_headers(url, referer)


def download_hls(
    playlist_url: str,
    dest: Path,
    *,
    video: RemoteVideo | None = None,  # noqa: ARG001  兼容 downloader 的调用形状
    episode: Episode | None = None,  # noqa: ARG001
    quality: Quality | None = None,  # noqa: ARG001
    referer: str = "",
    concurrency: int = DEFAULT_CONCURRENCY,
    speed_limit_bps: float = 0.0,
    on_progress=None,  # noqa: ANN001  Callable[[SegmentProgress], None]
    cancel: threading.Event | None = None,
    pause_event: threading.Event | None = None,
    resume: bool = True,
    pause_timeout: float = 600.0,
) -> DownloadOutcome:
    """一站式：取清单 → 并发下载分片 → 拼接为一个 `.ts` 文件。

    分片本身是 MPEG-TS，因此产物是 `.ts`；要 MP4 请用 `remux_to_mp4()`
    做一次 `-c copy` 重封装（不重编码）。
    """
    headers = build_headers(playlist_url, referer)
    downloader = SegmentDownloader(
        concurrency=concurrency,
        headers=headers,
        on_progress=on_progress,
        cancel=cancel,
        pause_event=pause_event,
        speed_limit_bps=speed_limit_bps,
        pause_timeout=pause_timeout,
    )
    try:
        playlist = downloader.fetch_playlist(playlist_url, referer=referer)
        if not playlist.segments:
            return DownloadOutcome(message="m3u8 清单里没有分片（可能是直播流）")
        target = Path(dest).with_suffix(".ts")
        return downloader.download(playlist, target, resume=resume)
    finally:
        downloader.close()


def remux_to_mp4(ts_path: Path, *, ffmpeg: str, target: Path | None = None) -> Path | None:
    """把拼接好的 `.ts` 用 ffmpeg `-c copy` 重封装为 MP4（不重编码）。

    这就是方案 3.2.2 的"按需 remux"：不做转码，只换容器。
    """
    source = Path(ts_path)
    if not source.is_file():
        return None
    output = Path(target) if target else source.with_suffix(".mp4")
    args = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output),
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
            timeout=600,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not output.is_file():
        output.unlink(missing_ok=True)
        return None
    return output


__all__ = [
    "BAD_PART_RATIO",
    "DEFAULT_CONCURRENCY",
    "DEFAULT_RETRIES",
    "DownloadOutcome",
    "RateLimiter",
    "SegmentDownloader",
    "SegmentPlan",
    "SegmentProgress",
    "build_headers",
    "download_hls",
    "remux_to_mp4",
]
