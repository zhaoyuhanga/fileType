"""聚合搜索的**时间预算**回归：一个死源不能把整条搜索拖住。

真实故障（用户反馈"墨软影视搜索一直搜索中，没有反馈"）的根因不是界面 bug：

- `archive.org` 从本机完全不可达，它自己的客户端要"15s × 3 次 + 退避"才放弃；
- 原来的 `search_all` 是**顺序遍历且没有单源超时** → 整条搜索被它拖住约 50 秒；
- 而且结果要等**所有**源都结束才上屏 → 界面一直只有"搜索中…"，一条都不出。

修好之后有三条硬要求，这里逐条钉住：

1. 死源不参与阻塞：总耗时必须是"预算 + 少量余量"，与死源自己的超时无关；
2. 活源的结果**按时拿到**，不能因为死源被丢掉；
3. `on_partial` 能回报进度与累积结果（界面靠它显示"已查 4/11 个源…"）。
"""
from __future__ import annotations

import threading
import time

from modu_workbench.core.video.models import RemoteVideo
from modu_workbench.core.video.sources import VideoRegistry
from modu_workbench.core.video.sources.base import VideoSource


def _remote(key: str, remote_id: str, title: str) -> RemoteVideo:
    return RemoteVideo(source=key, remote_id=remote_id, title=title)


class _Stub(VideoSource):
    """可控替身：`delay` 秒后返回，或永久阻塞（delay=None）。"""

    def __init__(self, key: str, label: str, *, delay: float = 0.0, hits: int = 1,
                 hang: bool = False, error: str = "") -> None:
        self.info = type("I", (), {"key": key, "label": label, "kind": "cms",
                                  "note": "", "homepage": ""})()
        self._delay = delay
        self._hits = hits
        self._hang = hang
        self._error = error
        self.release = threading.Event()

    def search(self, keyword, kind="all", limit=30, page=1):  # noqa: ANN001, ANN201
        if self._hang:
            # 模拟"永不返回"的源，直到测试主动放行（不 join 才不会被拖住）
            self.release.wait(timeout=30)
            return []
        if self._delay:
            time.sleep(self._delay)
        if self._error:
            from modu_workbench.core.video.sources.http import SourceError

            raise SourceError(self._error)
        return [_remote(self.info.key, f"{self.info.key}-{index}", f"片{index}")
                for index in range(self._hits)]

    def supports_keyword_search(self) -> bool:
        return True


def _registry(*sources: VideoSource) -> VideoRegistry:
    registry = VideoRegistry(list(sources))
    registry.set_order([source.info.key for source in sources])
    registry.set_enabled_keys([source.info.key for source in sources])
    return registry


def test_dead_source_does_not_block_the_whole_search() -> None:
    """核心回归：一个永不放行的源，不能让搜索超过预算。"""
    dead = _Stub("dead", "死源", hang=True)
    alive = _Stub("alive", "活源", delay=0.01, hits=3)
    registry = _registry(dead, alive)

    started = time.monotonic()
    videos, errors = registry.search_all("片", per_source_timeout=0.4)
    elapsed = time.monotonic() - started

    assert elapsed < 3.0, f"有单源超时却仍然花了 {elapsed:.1f}s（死源把整体拖住了）"
    assert [video.source for video in videos] == ["alive", "alive", "alive"], \
        "活源的结果必须照常拿到"
    assert len(videos) == 3
    assert any("死源" in error for error in errors), errors
    assert any("已跳过" in error for error in errors), "超时要说明是'跳过'，不是含糊失败"
    dead.release.set()          # 放行后台线程，避免影响其它用例


def test_partial_results_are_reported_before_the_slow_source_finishes() -> None:
    """边搜边回报：快的源先到，进度必须能看见（界面就靠它显示"在动"）。"""
    slow = _Stub("slow", "慢源", hang=True)
    fast = _Stub("fast", "快源", delay=0.01, hits=2)
    registry = _registry(slow, fast)

    seen: list[tuple[int, int, int]] = []
    started = time.monotonic()
    videos, _errors = registry.search_all(
        "片", per_source_timeout=0.5,
        on_partial=lambda done, total, found, errs: seen.append(
            (done, total, len(found))),
    )
    elapsed = time.monotonic() - started

    assert seen, "必须有部分回报，否则界面只能干等"
    # 最后一次回报应当是"全部处理完"（含被跳过的超时源）
    assert seen[-1][0] == seen[-1][1] == 2
    assert any(count >= 2 for _done, _total, count in seen), \
        f"快源的结果应当先被回报出来：{seen}"
    assert elapsed < 3.0
    assert len(videos) == 2
    slow.release.set()


def test_source_order_is_preserved_not_completion_order() -> None:
    """并发不能改变优先级语义：结果按**源顺序**归并，与顺序遍历一致。

    注意标题必须不同：同名会被"标题+年份"去重合并掉（那是另一个用例的事）。
    """
    class _Named(_Stub):
        def __init__(self, key, label, *, delay, name):  # noqa: ANN001
            super().__init__(key, label, delay=delay)
            self._name = name

        def search(self, keyword, kind="all", limit=30, page=1):  # noqa: ANN001, ANN201
            time.sleep(self._delay)
            return [_remote(self.info.key, f"{self.info.key}-1", self._name)]

    slow = _Named("a", "甲", delay=0.25, name="甲片")
    fast = _Named("b", "乙", delay=0.01, name="乙片")
    registry = _registry(slow, fast)          # 顺序：甲 在前

    videos, _errors = registry.search_all("片", per_source_timeout=3.0)
    assert [video.source for video in videos] == ["a", "b"], \
        "乙 先完成，但结果必须按源顺序排（甲 在前）"


def test_dedupe_keeps_the_higher_priority_source() -> None:
    """同名去重保留"源顺序更靠前"的那条 —— 即使它是后完成的。"""
    class _Named(_Stub):
        def __init__(self, key, label, *, delay, name):  # noqa: ANN001
            super().__init__(key, label, delay=delay)
            self._name = name

        def search(self, keyword, kind="all", limit=30, page=1):  # noqa: ANN001, ANN201
            time.sleep(self._delay)
            return [_remote(self.info.key, f"{self.info.key}-1", self._name)]

    first = _Named("a", "甲", delay=0.25, name="同名片")   # 优先级高但完成晚
    second = _Named("b", "乙", delay=0.01, name="同名片")
    registry = _registry(first, second)

    videos, _errors = registry.search_all("同名片", per_source_timeout=3.0)
    assert [video.source for video in videos] == ["a"], \
        "甲 源优先级更高，去重后应当保留甲"
    assert videos[0].remote_id == "a-1"


def test_no_timeout_keeps_old_behaviour() -> None:
    """`per_source_timeout=None` = 不限时（保留旧行为，供测试与特殊调用方使用）。"""
    class _Named(_Stub):
        def __init__(self, key, label, *, name):  # noqa: ANN001
            super().__init__(key, label, delay=0.05)
            self._name = name

        def search(self, keyword, kind="all", limit=30, page=1):  # noqa: ANN001, ANN201
            time.sleep(self._delay)
            return [_remote(self.info.key, f"{self.info.key}-{i}", f"{self._name}{i}")
                    for i in range(2)]

    registry = _registry(_Named("a", "甲", name="甲片"), _Named("b", "乙", name="乙片"))
    videos, errors = registry.search_all("片", per_source_timeout=None)
    assert len(videos) == 4 and not errors


def test_failed_source_is_recorded_and_others_still_return() -> None:
    bad = _Stub("bad", "坏源", error="连接被拒绝")
    good = _Stub("good", "好源", delay=0.01, hits=2)
    registry = _registry(bad, good)
    videos, errors = registry.search_all("片", per_source_timeout=2.0)
    assert len(videos) == 2
    assert any("坏源" in error for error in errors)
