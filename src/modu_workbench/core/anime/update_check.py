"""追番更新检查：比对"规则详情页的剧集列表"与库里记录的总集数/末集名。

设计原则（与 `docs/VIDEO_ANIME_PLAN.md` 4.7 对齐）：

- **纯函数式**：本模块不知道规则引擎、不碰 DB、不碰 Qt。唯一的网络入口是调用方
  注入的 `detail_fetcher(series) -> SeriesDetail | None`，所以单测可以完全离线跑。
- **并发 2**：一次对几十部番剧逐个请求详情页会被站点限流（也拖慢整个 UI），
  并发 2 是"够快但不至于被封"的折中；更高的并发留给用户手动调（本函数收参数）。
- **失败不影响其他条目**：单部番剧抛异常只记进 `SeriesUpdate.error` 并计入 `failed`，
  绝不能把整次检查打断（否则第 3 部番剧的 403 会让后面 27 部永远查不到更新）。
- **绝不把"减少"当新闻**：站点调整集数（合并/下架）时 `new_total < old_total`，
  此时报告 0 条新增，只更新末集名（否则用户会看到"更新了 -2 集"这种荒谬提示）。

比对规则（本模块的核心，D 阶段的差异化能力）：

1. `new_total > old_total`：新增 = 新详情里的**最后 `new_total - old_total` 集**（按线路/集序）；
   若库里 `total_episodes` 是 0（从未记录过集数），则视为"首次记录"，新增判空
   —— 第一次追番就报 12 集"新集"会直接淹没真正的新集提醒。
2. `new_total == old_total` 但末集名变了：只报那一集（站点替换片源/改名的情况）。
3. 其它情况：没有新增。
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from .models import AnimeSeries, SeriesDetail, normalize_title

__all__ = ["SeriesUpdate", "UpdateReport", "check_updates"]

#: 进度回调：`on_progress(已完成数, 总数, 可读消息)`
ProgressFn = Callable[[int, int, str], None]
#: 详情抓取器：由调用方注入（`library.py` 里包一层引擎），失败返回 None
DetailFetcher = Callable[[AnimeSeries], Optional[SeriesDetail]]


@dataclass
class SeriesUpdate:
    """一部番剧的更新检查结果。"""

    series: AnimeSeries
    new_episodes: list[str] = field(default_factory=list)   # 新增集名（按顺序）
    old_total: int = 0
    new_total: int = 0
    latest_episode: str = ""
    error: str = ""

    @property
    def has_new(self) -> bool:
        return bool(self.new_episodes)

    @property
    def message(self) -> str:
        """给用户看的一句话（UI 角标/tooltip 直接用它，别再各自拼字符串）。"""
        if self.error:
            return f"检查失败：{self.error}"
        if self.new_episodes:
            return f"新增 {len(self.new_episodes)} 集：{self.new_episodes[-1]}"
        return "暂无更新"


@dataclass
class UpdateReport:
    """一次"检查全部追番"的汇总。"""

    items: list[SeriesUpdate] = field(default_factory=list)
    checked: int = 0
    failed: int = 0

    @property
    def with_new(self) -> list[SeriesUpdate]:
        return [item for item in self.items if item.has_new]

    @property
    def new_count(self) -> int:
        """有新集的**番剧数量**（不是集数）—— nav 角标显示的是"3 部有新集"。"""
        return len(self.with_new)


def _text(value) -> str:  # noqa: ANN001
    return "" if value is None else str(value)


def _episode_number(episode) -> int:  # noqa: ANN001  AnimeEpisode
    """取一集的集号：优先用解析结果 `number`，没有就从名字里现解析。

    为什么不能只靠"列表末尾 N 项"：第二个线路常常是**另一个站点/另一个顺序**的片源，
    线路内顺序未必与更新顺序一致（实测里出现过"第 4 集排在最后"），
    只按位置切会把老集当成新集推给用户。
    """
    number = int(getattr(episode, "number", 0) or 0)
    if number:
        return number
    from .models import parse_episode_number

    return parse_episode_number(getattr(episode, "name", ""))


def _episodes_of(detail: SeriesDetail) -> list:
    """把详情页的线路/剧集摊平成列表（保留站点顺序，供"按集号找不到"时兜底）。"""
    episodes: list = []
    for road in detail.roads or []:
        for episode in road.episodes or []:
            if (getattr(episode, "name", "") or "").strip():
                episodes.append(episode)
    return episodes


def _episode_names(episodes: list) -> list[str]:
    return [(getattr(episode, "name", "") or "").strip() for episode in episodes]


def _episode_count(detail: SeriesDetail) -> int:
    """新详情的集数。

    优先用**真实抓到的剧集条数**：引擎解析了详情页，它列的才是能播的集；
    `series.total_episodes` 只在"没有剧集列表"（API 模式只给总数）时兜底。
    反过来（站点声称 12 集、实际只列出 8 集）时必须以 8 为准，
    否则"总集数"永远追不上，更新角标会一直挂着。
    """
    counted = len(_episodes_of(detail))
    if counted:
        return counted
    declared = int(detail.series.total_episodes or 0) if detail.series else 0
    return max(0, declared)


def _pick_new(episodes: list, count: int, old_total: int, latest: str) -> list[str]:
    """挑出"新增的那几集"的名字。

    ① 能全部解析出集号时，取**集号 > old_total** 的集并按集号升序 —— 最准的口径：
       第二个线路的顺序常常与更新顺序不同（实测里出现过"第 4 集排在列表最后"），
       按位置切会把老集当成新集推给用户；
    ② 集号解析不出来时（"正片 / OVA / 特别篇"这类命名）退回"列表最后 count 项"。

    **刻意不做"末集名去重"**：`series.latest_episode` 每次检查都会被写成最新一集，
    拿它去过滤会把真正的新集吃掉（"从 4 集涨到 6 集"时被砍成只报第 05 集，
    因为我们刚把第 06 集记成了 latest_episode）。判断新旧只能靠**集号/数量**，
    名字只用于展示。真正需要过滤的是"整体重编号"，那由调用方用
    `len(added) <= new_total - old_total` 兜住（见 `_compare`）。
    """
    numbered = [_episode_number(episode) for episode in episodes]
    if numbered and all(number > 0 for number in numbered):
        order = sorted(zip(numbered, _episode_names(episodes)), key=lambda item: item[0])
        return [name for number, name in order if number > old_total]
    return _episode_names(episodes)[-count:] if count > 0 else []


def _integer(value, default: int = 0) -> int:  # noqa: ANN001
    """安全转 int（库里/对象里的字段可能是脏字符串，别让比较直接 TypeError）。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _compare(series: AnimeSeries, detail: SeriesDetail) -> SeriesUpdate:
    """按模块 docstring 的三条规则算出一部番剧的更新。"""
    old_total = max(0, _integer(series.total_episodes))
    new_total = _episode_count(detail)
    episodes = _episodes_of(detail)
    latest = (_episode_names(episodes)[-1] if episodes
              else (_text(detail.series.latest_episode) if detail.series else ""))

    if old_total <= 0:
        # 首次记录集数：不算"新集"（否则第一次追番就弹 12 集）
        return SeriesUpdate(series=series, new_episodes=[], old_total=old_total,
                            new_total=new_total, latest_episode=latest)

    if new_total > old_total:
        if not episodes:
            # 只有总数没有剧集列表：只能确认"变多了"，给一条占位说明而不是编集名
            return SeriesUpdate(series=series, new_episodes=[f"新增 {new_total - old_total} 集"],
                                old_total=old_total, new_total=new_total, latest_episode=latest)
        added = _pick_new(episodes, new_total - old_total, old_total, series.latest_episode)
        # 新增集数不可能超过"总集数增量"：超了说明站点做了整体重编号
        # （旧的第 6/7 集被重排成新的第 1/2 集），这种情况一律不报，避免误报刷屏。
        if len(added) > new_total - old_total:
            added = []
        return SeriesUpdate(series=series, new_episodes=list(added), old_total=old_total,
                            new_total=new_total, latest_episode=latest)

    if new_total == old_total and latest and normalize_title(latest) != normalize_title(
            series.latest_episode):
        # 集数没变但末集名字变了：站点换源/改名，报这一集（不报"删了一集"）
        return SeriesUpdate(series=series, new_episodes=[latest], old_total=old_total,
                            new_total=new_total, latest_episode=latest)

    return SeriesUpdate(series=series, new_episodes=[], old_total=old_total,
                        new_total=new_total, latest_episode=latest)


def check_updates(
    series_list: Iterable[AnimeSeries],
    *,
    detail_fetcher: DetailFetcher,
    max_workers: int = 2,
    cancel: threading.Event | None = None,
    on_progress: ProgressFn | None = None,
) -> UpdateReport:
    """并发检查追番更新，返回汇总报告。

    - `detail_fetcher` 返回 None（规则不可用/站点改版/网络失败）时按"检查失败"计数，
      但**不写新集**：拿不到详情就断言"没有更新"是错的，UI 需要能区分两者；
    - `cancel` 为 `threading.Event`，已 set 时不再派发新任务（在飞的请求让它自然结束，
      强行杀掉线程只会让 requests 连接池处于半死状态）；
    - `on_progress(done, total, message)` 会在**工作线程**里被调用，Qt 侧必须
      经信号转到主线程（这是调用方责任，写在这里免得踩坑）。
    """
    items = [item for item in series_list if isinstance(item, AnimeSeries)]
    report = UpdateReport(items=[], checked=0, failed=0)
    total = len(items)
    if not items:
        if on_progress:
            on_progress(0, 0, "没有需要检查的番剧")
        return report

    workers = max(1, min(8, int(max_workers or 1)))
    lock = threading.Lock()
    counter = {"done": 0}

    def work(series: AnimeSeries) -> SeriesUpdate | None:
        if cancel is not None and cancel.is_set():
            return None            # 已取消：直接不产出结果（计入"没查到"会误导用户）
        try:
            detail = detail_fetcher(series)
        except Exception as error:  # noqa: BLE001  单条失败绝不上抛（否则整轮检查中断）
            return SeriesUpdate(series=series, error=str(error) or error.__class__.__name__,
                                old_total=int(series.total_episodes or 0))
        if detail is None:
            return SeriesUpdate(series=series, error="没有拿到详情（规则不可用或站点无响应）",
                                old_total=int(series.total_episodes or 0))
        try:
            return _compare(series, detail)
        except Exception as error:  # noqa: BLE001  比对本身出问题也算这一条失败
            return SeriesUpdate(series=series, error=f"比对失败：{error}",
                                old_total=int(series.total_episodes or 0))

    results: list[SeriesUpdate] = []
    if workers == 1 or total == 1:
        for series in items:
            if cancel is not None and cancel.is_set():
                break
            update = work(series)
            if update is None:
                break
            results.append(update)
            _report_progress(lock, counter, total, on_progress, update)
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="anime-update") as pool:
            futures = {pool.submit(work, series): series for series in items}
            for future in as_completed(futures):
                update = future.result()
                if update is None:
                    continue
                results.append(update)
                _report_progress(lock, counter, total, on_progress, update)

    # 结果顺序与入参一致（并发完成顺序是乱的，UI 列表与测试都依赖稳定顺序）。
    # 不用 id() 当键：库里的行会被包装成**新对象**，Python 的 id 不可靠；
    # 用 (rule_key, remote_id) 这组唯一键，重复项按出现次序兜底。
    order: dict[tuple[str, str], int] = {}
    for index, series in enumerate(items):
        order.setdefault((series.rule_key, series.remote_id), index)
    results.sort(key=lambda item: order.get(
        (item.series.rule_key, item.series.remote_id), len(items)))
    report.items = results
    report.checked = len(results)
    report.failed = sum(1 for item in results if item.error)
    if on_progress:
        on_progress(len(results), total,
                    f"检查完成：{report.new_count} 部有新集"
                    + (f"，{report.failed} 部失败" if report.failed else ""))
    return report


def _report_progress(lock: threading.Lock, counter: dict, total: int,
                     on_progress: ProgressFn | None, update: SeriesUpdate) -> None:
    if on_progress is None:
        return
    with lock:
        counter["done"] += 1
        done = counter["done"]
    title = update.series.title or update.series.remote_id or "未命名番剧"
    on_progress(done, total, f"[{done}/{total}] {title}：{update.message}")
