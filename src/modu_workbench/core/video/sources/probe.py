"""视频源单源测速：延迟 + 首个分片吞吐（v3-e 的可测试内核）。

把「测什么、怎么算」从注册表里拆出来，好处有两个：

1. `VideoRegistry.measure_sources` 只负责并发调度与结果落库，逻辑薄；
2. 首片解析、速率换算这些**纯函数**可以完全离线测试
   （见 tests/board_video/test_video_speed.py）。

数值口径（与 docs/VIDEO_PLAN.md 3.3.5 / L4 对齐）：

- 整体超时 3–4 秒（默认 `PROBE_TIMEOUT = 3.5`），到点就放弃，不让一个慢源拖住整批；
- 速度 = `首块字节 / 耗时`，单位 **Mbit/s**（1 Byte = 8 bit）。算分用 MB/s
  （参考线 8 MB/s），换算只允许出现在 `SourceHealth.last_speed_mbytes` 里；
- "提前停止" = 拿到元数据 + 1 个分片就走：这里只取首块（`SEGMENT_BYTES`），
  分片常常有好几 MB，下完它会让 20 个源的测速从几秒变成半分钟。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..hls import parse_m3u8, resolve_url

#: 单源整体超时（秒）。计划要求 3–4s：比它长会让「测 20 个源」变成一分钟。
PROBE_TIMEOUT = 3.5

#: 单次网络调用的**下限**：剩余预算再少也给它 0.25s，否则会退化成必然失败。
MIN_PHASE_TIMEOUT = 0.25

#: 首块下载字节数。够算速率即可，不必下完整个分片（L4 的"提前停止"）。
SEGMENT_BYTES = 262144

#: 测速用的固定关键词：要短、常见、各采集站都搜得到结果（避免"测了个空"）。
DEFAULT_PROBE_KEYWORD = "电影"

#: 每个源最多看多少条搜索结果（只用来挑第一条，多了纯浪费时间）。
PROBE_SEARCH_LIMIT = 5


@dataclass
class ProbeResult:
    """单源测速结果。

    | 字段 | 含义 |
    |---|---|
    | `ok` | 至少拿到一个可用信号（延迟或吞吐）|
    | `latency_ms` | 搜索请求耗时（毫秒）|
    | `mbit_per_s` | 首个分片吞吐，**Mbit/s**（0 = 没测到）|
    | `height` | 首条结果的分辨率高度（0 = 未知）|
    | `resolution` | 画质标签原文（如 "1080P"），界面直接显示 |
    | `error` | 失败/降级说明；`ok=True` 时也可能有（只是"没测到速度"）|
    | `segments` | 实际取到的分片数。正常恒为 0（没取到）或 1（提前停止），
                    测试用它断言"没有去要第二个分片" |
    """

    key: str = ""
    ok: bool = False
    latency_ms: float = 0.0
    mbit_per_s: float = 0.0
    height: int = 0
    resolution: str = ""
    error: str = ""
    segments: int = 0
    elapsed: float = 0.0
    extra: dict = field(default_factory=dict)

    @property
    def speed_mbps(self) -> float:
        """吞吐（Mbit/s）的别名 —— 与 `mbit_per_s` 同值，避免单位歧义。"""
        return self.mbit_per_s

    @property
    def speed_mbytes(self) -> float:
        """吞吐（MB/s），界面展示用。"""
        return self.mbit_per_s / 8.0


def phase_timeout(deadline: float, now: float | None = None) -> float:
    """算本次网络调用还能用多久（不短于 `MIN_PHASE_TIMEOUT`）。"""
    current = time.monotonic() if now is None else now
    return max(MIN_PHASE_TIMEOUT, deadline - current)


def hls_first_segment(playlist_text: str, base_url: str) -> str:
    """从 m3u8 里取**首个媒体分片**的绝对地址（取不到返回 ""）。

    - 媒体清单（`#EXTINF` + 分片行）→ 返回第一个分片；
    - 主清单（只有 `#EXT-X-STREAM-INF`，分片在子清单里）→ 返回 ""。

    只认媒体清单是有意的：再往下钻一层要多一次网络往返，而测速预算只有 3–4 秒，
    多一跳就多一次超时风险；主清单测不到速度时延迟分照样有效。
    """
    if not playlist_text:
        return ""
    playlist = parse_m3u8(playlist_text, base_url)
    if not playlist.segments:
        return ""
    # `HlsPlaylist.segments` 已经是绝对地址（parse_m3u8 内部做了 resolve_url）
    target = str(playlist.segments[0] or "")
    if not target:
        return ""
    if target.startswith(("http://", "https://")):
        return target
    return resolve_url(base_url, target)


def _call_with_timeout(function, *args, timeout: float, **kwargs):
    """带超时调用；替身只实现旧签名（不收 `timeout`）时自动退回。

    真实 `HttpClient.get/get_text/request` 都收 `timeout`；而测试替身常常只写
    `get_text(url, **kwargs)`。退回一次比要求所有替身升级更省事。
    """
    try:
        return function(*args, timeout=timeout, **kwargs)
    except TypeError:
        return function(*args, **kwargs)


def probe_source(provider, keyword: str = DEFAULT_PROBE_KEYWORD, *, http=None,  # noqa: ANN001
                 timeout: float = PROBE_TIMEOUT, manager=None, cancel=None) -> ProbeResult:
    """测一个源：搜索（延迟）→ 首集地址 → 首个分片（吞吐）。

    **任何失败都写进返回值，绝不抛异常** —— 它跑在并发批次里，抛出去会毁掉整批测速。
    `cancel` 是 `threading.Event`，只在阶段之间检查（网络调用本身由 `timeout` 兜住）。
    `manager` 是调用方（注册表 / 界面线程）的 `ThreadPoolExecutor`，保留给将来把分片
    下载再并发一层；当前实现按预算串行取一个分片，不排队也不多占线程。
    """
    result = ProbeResult(key=str(getattr(provider, "key", "") or ""))
    client = http if http is not None else getattr(provider, "http", None)
    started = time.monotonic()
    deadline = started + max(MIN_PHASE_TIMEOUT, float(timeout))
    if client is None:
        result.error = "该源没有 HTTP 客户端，无法测速"
        result.elapsed = time.monotonic() - started
        return result
    try:
        result.error = _probe_inner(provider, keyword, client, deadline, result, cancel)
    except Exception as error:  # noqa: BLE001  一个坏源不能毁掉整批测速
        result.error = str(error)[:200] or error.__class__.__name__
    result.elapsed = time.monotonic() - started
    result.ok = bool(result.latency_ms > 0 or result.mbit_per_s > 0)
    return result


def _probe_inner(provider, keyword: str, client, deadline: float,  # noqa: ANN001
                 result: ProbeResult, cancel) -> str:  # noqa: ANN001
    """真正干活的分支：返回错误说明（空串 = 一切顺利）。"""
    # ---- 阶段 1：搜索（这就是延迟样本）----
    phase_started = time.monotonic()
    videos = provider.search(keyword, kind="all", limit=PROBE_SEARCH_LIMIT)
    result.latency_ms = max(0.0, (time.monotonic() - phase_started) * 1000.0)

    first = videos[0] if videos else None
    if first is None:
        return "搜索没有返回结果，无法定位分片"
    result.resolution = _resolution_text(first)
    result.height = _resolution_height(first)

    if _cancelled(cancel):
        return "已取消"

    url = _episode_url(provider, first)
    if not url:
        # 边界情况（直链源、结果不带剧集的源）：只测到延迟就收工，
        # 不为了凑速度分再拉一次详情 —— 那会吃掉 3–4 秒预算的一大块。
        return "首条结果没有分集地址（只测到延迟）"

    referer = _referer_for(provider, first, url)

    # ---- 阶段 2：HEAD 探活（失败静默：很多站点对 HEAD 返 405/403）----
    _head(client, url, referer=referer, timeout=phase_timeout(deadline))
    if _cancelled(cancel):
        return "已取消"

    # ---- 阶段 3：首个分片吞吐 ----
    mbit, note = _measure_first_segment(
        provider, client, url, referer=referer,
        timeout=phase_timeout(deadline), cancel=cancel,
    )
    if mbit > 0:
        result.mbit_per_s = mbit
        result.segments = 1        # 提前停止：只取了这一个分片
    return note


def _resolution_text(video) -> str:  # noqa: ANN001
    """首条结果的画质标签：优先分集画质（源给的线路名），其次条目备注。"""
    for episode in (getattr(video, "episodes", None) or []):
        for quality in (getattr(episode, "qualities", None) or []):
            label = str(getattr(quality, "label", "") or "").strip()
            if label:
                return label
    return str(getattr(video, "remarks", "") or "").strip()


def _resolution_height(video) -> int:  # noqa: ANN001
    """把画质标签折成高度（"1080P"→1080、"4K"→2160）；认不出返回 0。"""
    text = _resolution_text(video)
    if not text:
        return 0
    upper = text.upper()
    if "4K" in upper or "2160" in upper:
        return 2160
    if "2K" in upper or "1440" in upper:
        return 1440
    digits = ""
    for char in upper:
        if char.isdigit():
            digits += char
        elif digits:
            break
    return int(digits) if digits else 0


def _episode_url(provider, video) -> str:  # noqa: ANN001
    """首集地址：搜索结果自带剧集就直接用，否则返回 ""（不主动拉详情）。"""
    episodes = getattr(video, "episodes", None) or []
    if not episodes:
        return ""
    try:
        return str(provider.play_url(video, episodes[0], None) or "")
    except Exception:  # noqa: BLE001  解析不出地址不是测速的错，只是没得测
        return ""


def _referer_for(provider, video, url: str) -> str:  # noqa: ANN001
    """分片请求要带的 Referer：优先源自己给的下载头，其次结果里记的 Referer。

    注意**不要**退回 `guess_referer(分片地址)`：分片常在另一个 CDN 域名上，
    拿分片域名当 Referer 只会稳定吃 403；宁可不带。
    """
    try:
        headers = provider.download_headers(url) or {}
    except Exception:  # noqa: BLE001
        headers = {}
    referer = str(headers.get("Referer") or "")
    if referer:
        return referer
    extra = getattr(video, "extra", None) or {}
    if isinstance(extra, dict):
        return str(extra.get("referer") or extra.get("Referer") or "")
    return ""


def _head(client, url: str, *, referer: str, timeout: float) -> None:
    """HEAD 探一下地址是否可达；任何失败都静默（不支持 HEAD 的站点很常见）。

    按**关键字**传 method/url：`requests` 里 `method` 是 keyword-only 参数，
    位置传参在这个签名上迟早出错（真踩过：把 url 当成了 method）。
    """
    if not url:
        return
    headers = {"Referer": referer} if referer else {}
    try:
        _call_with_timeout(client.request, method="HEAD", url=url,
                           headers=headers, timeout=timeout)
    except Exception:  # noqa: BLE001
        pass


def _measure_first_segment(provider, client, episode_url: str, *, referer: str,  # noqa: ANN001
                           timeout: float, cancel) -> tuple[float, str]:  # noqa: ANN001
    """取首个分片，返回 (Mbit/s, 说明)。失败时速度为 0、说明写进第二个元素。"""
    if not episode_url:
        return 0.0, "没有可测的分片地址"
    headers = {"Referer": referer} if referer else {}
    try:
        text = _call_with_timeout(client.get_text, episode_url, headers=headers, timeout=timeout)
    except Exception as error:  # noqa: BLE001
        return 0.0, f"取清单失败：{error}"[:200]
    segment = hls_first_segment(text, episode_url)
    if not segment:
        return 0.0, "不是 HLS 清单或没有分片（只测到延迟）"
    if _cancelled(cancel):
        return 0.0, "已取消"
    started = time.monotonic()
    try:
        payload = _call_with_timeout(
            client.get, segment,
            headers={**headers, "Range": f"bytes=0-{SEGMENT_BYTES - 1}"},
            timeout=timeout,
        )
    except Exception as error:  # noqa: BLE001
        return 0.0, f"取分片失败：{error}"[:200]
    return transfer_rate(payload, time.monotonic() - started), ""


def transfer_rate(payload, elapsed: float) -> float:  # noqa: ANN001
    """`首块字节 / 耗时` → Mbit/s。

    耗时下限 10ms：本机环回（或测试替身）能在 1ms 内返回 256 KiB，算出来就是
    2000 Mbit/s 这种**物理上不可能**的数字；真实网络下界一般也就在 10ms 量级，
    所以钳一下不算失真，反而避免把局域网缓存命中吹成"20 Gbps 的源"。
    """
    span = max(float(elapsed or 0.0), 0.01)
    return max(0.0, payload_size(payload) * 8.0 / span / 1_000_000.0)


def payload_size(payload) -> int:  # noqa: ANN001
    """响应体字节数（requests 的 `.content` / 测试替身的 `.raw`）。

    `content` 存在但为空 **不直接返回 0**：要落回 `text`/`raw`，
    否则替身里"只给 text"的响应会被算成 0 字节（速度分白丢）。
    """
    content = getattr(payload, "content", None)
    if isinstance(content, (bytes, bytearray)) and content:
        return len(content)
    raw = getattr(payload, "raw", None)
    if isinstance(raw, (bytes, bytearray)) and raw:
        return len(raw)
    text = getattr(payload, "text", None)
    if isinstance(text, str) and text:
        return len(text.encode("utf-8"))
    return 0


def _cancelled(cancel) -> bool:  # noqa: ANN001
    return bool(cancel is not None and cancel.is_set())


__all__ = [
    "DEFAULT_PROBE_KEYWORD",
    "MIN_PHASE_TIMEOUT",
    "PROBE_SEARCH_LIMIT",
    "PROBE_TIMEOUT",
    "SEGMENT_BYTES",
    "ProbeResult",
    "hls_first_segment",
    "payload_size",
    "phase_timeout",
    "probe_source",
    "transfer_rate",
]
