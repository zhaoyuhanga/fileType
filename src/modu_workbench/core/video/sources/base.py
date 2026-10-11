"""影视源基类与元信息：所有视频来源都实现同一套接口。

新增一个源只需三步（不动任何既有业务代码）：
1. 继承 `VideoSource`，实现 `search()`；按需实现 `detail()` / `play_url()`；
2. 在 `providers/__init__.py` 的 `build_default_providers()` 里注册一个实例；
3. 完成 —— 注册表自动获得「聚合搜索 / 熔断降级 / 换源重试 / 配置持久化」能力。

约定：
- `search()` 返回 `RemoteVideo` 列表（episodes 可以为空，先给结果，详情再拉）；
- `detail()` 补齐 `episodes`（分集 + 画质）；
- `play_url()` 返回最终可播放地址（默认直接取剧集 url）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List

from ..models import Episode, Quality, RemoteVideo
from .http import HttpClient, SourceError

# 延迟滑动窗口长度：10 个样本足够看出「这个源现在是不是在抖」，
# 又不至于把几分钟前的拥塞一直算进 P50 里。
LATENCY_WINDOW = 10

# 打分公式（借自 LunaTV-EE，见 docs/VIDEO_ANIME_PLAN.md 3.3.5 / L4）：
#     score = 分辨率 40% + 速度 40% + 延迟 20%
SCORE_WEIGHT_RESOLUTION = 0.4
SCORE_WEIGHT_SPEED = 0.4
SCORE_WEIGHT_LATENCY = 0.2

# 分辨率归一到 0–1。**不做线性映射**：4K 对 1080p 的观感差距远小于 1080p 对 480p，
# 按比例给分会让「4K 但慢得要死」的源永远排第一，而它恰恰是最不能用的那种。
RESOLUTION_SCORES: dict[int, float] = {2160: 1.0, 1440: 0.85, 1080: 0.75, 720: 0.5, 480: 0.3}

# 速度参考线：8 MB/s 记满分（覆盖 4K 原盘的常见码率），再快也不加分。
SPEED_REFERENCE_MBPS = 8.0

# 延迟参考线：3000ms 记 0 分。超过这个值的源基本等于不可用。
LATENCY_REFERENCE_MS = 3000.0

# 权重加成系数：权重 50 为中性（加成 1.0），每高 1 分 +0.5%
WEIGHT_BONUS_STEP = 0.005


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)


def resolution_score(height: int) -> float:
    """把分辨率高度归一到 0–1（2160→1.0 / 1080→0.75 / 720→0.5 / 480→0.3）。

    低于 480 的按 480 以下线性衰减（480 以下每 100 行约 -0.06），
    未知分辨率（0）给 0.4 —— 与「比 720p 略差」的保守印象一致，
    不能给 0，否则所有拿不到 RESOLUTION 的源都会被判死刑。
    """
    try:
        height = int(height or 0)
    except (TypeError, ValueError):
        height = 0
    if height <= 0:
        return 0.4
    if height in RESOLUTION_SCORES:
        return RESOLUTION_SCORES[height]
    if height > 2160:
        return 1.0
    # 取不超过它的最近档位，档位之间线性插值（1440 这种非常见值也能拿到合理分数）
    lower = max((key for key in RESOLUTION_SCORES if key <= height), default=0)
    upper = min((key for key in RESOLUTION_SCORES if key >= height), default=0)
    if not lower:
        return _clamp01(RESOLUTION_SCORES[480] * (height / 480.0))
    if not upper or upper == lower:
        return RESOLUTION_SCORES[lower]
    span = upper - lower
    ratio = (height - lower) / span
    low_score, high_score = RESOLUTION_SCORES[lower], RESOLUTION_SCORES[upper]
    return low_score + (high_score - low_score) * ratio


def resolution_score_from_label(label: str) -> float:
    """从源给的画质标签（"1080P" / "4K" / "原画"）估分辨率分。"""
    text = (label or "").strip().upper()
    if not text:
        return 0.4
    if "4K" in text or "2160" in text:
        return 1.0
    if "2K" in text or "1440" in text:
        return 0.85
    digits = ""
    for char in text:
        if char.isdigit():
            digits += char
        elif digits:
            break
    if digits:
        return resolution_score(int(digits))
    return 0.4


@dataclass
class SourceHealth:
    """视频源健康度：连续失败自动降级（熔断），成功即恢复。

    测速相关字段（v3-e，见 docs/VIDEO_ANIME_PLAN.md 3.3.5）：

    - `latency_samples`：最近 `LATENCY_WINDOW` 次请求耗时的滑动窗口（秒），
      对外只暴露 P50，避免一次网络抖动就把整个源判死；
    - `last_speed_mbps`：最近一次测得的下行吞吐，**单位是 Mbit/s**（1 Byte = 8 bit）。
      界面必须带上单位（"x.x MB/s" 展示时先除以 8），否则 8 与 1 谁快都说不清；
      内部计算用的参考线 `SPEED_REFERENCE_MBPS = 8.0` 是 **MB/s**，两者别混。
    """

    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    degraded_until: float = 0.0
    last_error: str = ""
    last_ok_at: float = 0.0
    # 滑动窗口 + 最近一次测速结果（默认值让 `SourceHealth()` 保持无参可构造）
    latency_samples: List[float] = field(default_factory=list)
    # 内部字段：单位 Mbit/s，经下面的 `last_speed_mbps` 属性读写（属性带清洗与非负钳制）
    _last_speed_mbps: float = 0.0
    measured_at: float = 0.0
    measured_height: int = 0

    def is_degraded(self, now: float) -> bool:
        return self.degraded_until > now

    def as_text(self, now: float) -> str:
        if self.is_degraded(now):
            return "临时降级"
        if self.consecutive_failures:
            return "不稳定"
        return "可用"

    # ---------- 延迟（滑动 P50） ----------

    def note_latency(self, seconds: float) -> None:
        """记一次请求耗时（秒）；窗口满后丢最旧的样本。"""
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            return
        if value < 0 or value != value:      # 负值 / NaN 直接丢弃，别污染 P50
            return
        self.latency_samples.append(value)
        if len(self.latency_samples) > LATENCY_WINDOW:
            del self.latency_samples[:-LATENCY_WINDOW]

    def percentile(self, ratio: float = 0.5) -> float:
        """滑动窗口的分位数，返回**毫秒**；没有样本时返回 0.0。

        线性插值（与 `statistics.quantiles` 的 inclusive 口径一致）：
        窗口里只有 1 个样本时 P50 就是它本身，而不是报错。
        """
        samples = sorted(self.latency_samples)
        if not samples:
            return 0.0
        if len(samples) == 1:
            return samples[0] * 1000.0
        position = _clamp01(float(ratio)) * (len(samples) - 1)
        lower = int(position)
        upper = min(lower + 1, len(samples) - 1)
        value = samples[lower] + (samples[upper] - samples[lower]) * (position - lower)
        return value * 1000.0

    @property
    def latency_ms(self) -> float:
        """滑动 P50 延迟（毫秒）；没有样本时为 0.0（界面据此显示「未测速」）。"""
        return self.percentile(0.5)

    # ---------- 速度 ----------

    @property
    def last_speed_mbps(self) -> float:
        """最近一次吞吐（Mbit/s）。别直接显示这个数：界面按 MB/s 展示（见 docstring）。"""
        return self._last_speed_mbps

    @last_speed_mbps.setter
    def last_speed_mbps(self, value: float) -> None:
        try:
            self._last_speed_mbps = max(0.0, float(value))
        except (TypeError, ValueError):
            self._last_speed_mbps = 0.0

    @property
    def last_speed_mbytes(self) -> float:
        """最近一次吞吐（MB/s）——**界面展示用这个**。"""
        return self._last_speed_mbps / 8.0

    def speed_score(self) -> float:
        """速度项得分（0–1）：以 8 MB/s 为满分参考线。"""
        if self._last_speed_mbps <= 0:
            return 0.0
        return _clamp01(self.last_speed_mbytes / SPEED_REFERENCE_MBPS)

    # ---------- 记录（供注册表调用） ----------

    def note_success(self, seconds: float | None = None, *, speed_mbps: float = 0.0,
                     height: int = 0) -> None:
        """记一次成功（可选耗时 / 吞吐 / 分辨率），并清除熔断状态。

        这是注册表 `record_success` 的底层实现；`seconds` 给了就顺手进延迟窗口，
        因此「搜索/解析成功后记录耗时」（计划 3.3.5 第一条）不需要额外调用。
        """
        self.successes += 1
        self.consecutive_failures = 0
        self.degraded_until = 0.0
        self.last_error = ""
        self.last_ok_at = time.time()
        if seconds is not None:
            self.note_latency(seconds)
        if speed_mbps:
            # 调用方给的是 Mbit/s（与 `last_speed_mbps` 同单位）
            self.last_speed_mbps = speed_mbps
            self.measured_at = time.time()
        if height:
            self.measured_height = int(height)

    def note_failure(self, error: Exception | str = "") -> None:
        """记一次失败：累计失败次数，连续失败达阈值后由注册表置降级时间。"""
        self.failures += 1
        self.consecutive_failures += 1
        self.last_error = str(error)[:200]

    # ---------- 打分（选源 / 选线路用，不是调缓冲档位用） ----------

    def health_score(self, *, weight: float = 50.0, height: int = 0) -> float:
        """综合评分：`分辨率 40% + 速度 40% + 延迟 20%`，再乘权重加成。

        ⚠️ **这个分数用来挑「用哪个源 / 哪条线路」，不是用来决定播放器的缓冲档位。**
        对标项目（LunaTV-EE）的 README 曾把测速结果说成"推荐缓冲模式"，
        但代码里没有任何路径把测速写回 `playerBufferMode` —— 我们不要复刻这个误读：
        缓冲档位由用户在设置里显式选择（省流/均衡/高质）。

        `weight` 是 0–100 的用户权重（顺序越靠前越高，见注册表的 `_weights`）；
        加成 = `1 + (weight - 50) * 0.005`，即权重 100 的源拿 1.25 倍、权重 0 拿 0.75 倍。
        分辨率与速度都拿不到时（没测过速）分数会很低，排序时自然沉底。
        """
        height = int(height or self.measured_height or 0)
        latency_component = 1.0 - _clamp01(self.percentile(0.5) / LATENCY_REFERENCE_MS)
        score = (
            SCORE_WEIGHT_RESOLUTION * resolution_score(height)
            + SCORE_WEIGHT_SPEED * self.speed_score()
            + SCORE_WEIGHT_LATENCY * latency_component
        )
        return score * weight_bonus(weight)

    def speed_text(self) -> str:
        """一行摘要（源设置里的「测速」列）：延迟 / 速度 / 评分。"""
        if not self.latency_samples and self._last_speed_mbps <= 0:
            return "未测速"
        latency = f"{self.latency_ms:.0f} ms" if self.latency_samples else "延迟 --"
        speed = f"{self.last_speed_mbytes:.1f} MB/s" if self._last_speed_mbps > 0 else "速度 --"
        return f"{latency} / {speed} / 评分 {self.health_score():.2f}"


def weight_bonus(weight: float) -> float:
    """权重加成：`1 + (weight - 50) * 0.005`（50 为中性）。"""
    try:
        value = float(weight)
    except (TypeError, ValueError):
        value = 50.0
    return 1.0 + (value - 50.0) * WEIGHT_BONUS_STEP

# 视频源能力类型
KIND_FULL = "full"                # 完整内容（免费可直接播放/下载）
KIND_PREVIEW = "preview"          # 仅预告/试看片段
KIND_CC = "cc"                    # 自由授权（公共领域 / CC，可自由下载）
KIND_DIRECT = "direct"            # 直链（用户自己提供的地址）
KIND_CUSTOM = "custom"            # 用户自定义（自行添加的采集接口）
KIND_EXPERIMENTAL = "experimental"

KIND_LABELS = {
    KIND_FULL: "完整内容",
    KIND_PREVIEW: "预览片段",
    KIND_CC: "自由授权",
    KIND_DIRECT: "直链",
    KIND_CUSTOM: "自定义",
    KIND_EXPERIMENTAL: "实验性",
}


@dataclass(frozen=True)
class SourceInfo:
    key: str
    label: str
    note: str
    kind: str = KIND_FULL
    needs_key: bool = False
    homepage: str = ""
    # 是否支持把内容下载到本地（预览片段源/部分受限源为 False）
    downloadable: bool = True
    # 是否可在设置里增删改（用户自定义源）
    editable: bool = False

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)


@dataclass
class ResolvedPlay:
    """播放地址解析结果（可能来自跨源兜底）。"""

    url: str
    source: str
    video: RemoteVideo
    episode: Episode | None = None
    quality: Quality | None = None
    switched_from: str = ""
    note: str = ""
    is_hls: bool = False
    referer: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def switched(self) -> bool:
        return bool(self.switched_from)

    @property
    def quality_label(self) -> str:
        return self.quality.label if self.quality else ""

    @property
    def episode_label(self) -> str:
        return self.episode.name if self.episode else ""


class VideoSource:
    """视频源基类：默认实现直链播放，子类按需覆盖。"""

    info: SourceInfo

    def __init__(self, *, http: HttpClient | None = None, key: str = ""):
        self.http = http or HttpClient(headers=self.default_headers())
        self._key = key or ""

    # ---------- 元信息 ----------

    @property
    def key(self) -> str:
        return self.info.key

    @property
    def label(self) -> str:
        return self.info.label

    def default_headers(self) -> dict:
        return {}

    def available(self) -> bool:
        return True

    def unavailable_reason(self) -> str:
        return ""

    def set_credential(self, value: str) -> None:
        """可选：需要密钥/自定义地址的源在此接收设置里的值。"""
        self._key = (value or "").strip()

    def reset_credential(self) -> None:
        """恢复默认凭据/接口地址（内置源在自己的类里重写）。"""
        self.set_credential("")

    @property
    def credential(self) -> str:
        return self._key

    # ---------- 能力 ----------

    def search(self, keyword: str, kind: str = "all", limit: int = 30,
               page: int = 1) -> List[RemoteVideo]:
        raise NotImplementedError

    def detail(self, video: RemoteVideo) -> RemoteVideo:
        """补齐分集与画质；默认原样返回（源本身已在搜索结果里带上剧集）。"""
        return video

    def play_url(self, video: RemoteVideo, episode: Episode,
                 quality: Quality | None = None) -> str:
        """返回最终播放/下载地址。"""
        if quality is not None and quality.url:
            return quality.url
        if episode is not None and episode.url:
            return episode.url
        raise SourceError("该条目没有可用的播放地址")

    def supports_download(self) -> bool:
        return bool(self.info.downloadable)

    def supports_keyword_search(self) -> bool:
        """是否参与「关键词聚合搜索」。

        直链类源只能凭一个地址检索，参与关键词聚合搜索只会每次都报一个
        「请输入 http(s):// 开头的地址」，因此由子类声明为 False 并在聚合搜索里跳过
        （用户直接粘贴地址时仍会走它，见 `VideoRegistry.search_all`）。
        """
        return True

    def download_headers(self, url: str = "") -> dict:
        """下载分片时需要附带的请求头（Referer 等）。"""
        from .http import guess_referer

        referer = guess_referer(url) if url else ""
        return {"Referer": referer} if referer else {}

    def probe(self, keyword: str = "电影", *, http=None, timeout: float = 3.5,  # noqa: ANN001
              manager=None, cancel=None) -> "ProbeResult":
        """测一次速度与延迟（v3-e，源设置里的「测速」按钮用）。

        流程（对齐 LunaTV-EE 的 L4，见 docs/VIDEO_ANIME_PLAN.md 3.3.5）：
        1. 用固定关键词搜索一次 → 搜索耗时即**延迟**样本；
        2. 取第一条结果的首集地址（结果里没带剧集就只算延迟，不再多发一次详情请求，
           这一条是为「3–4 秒整体超时」让路的取舍）；
        3. `HEAD` 探一下这个地址（失败不算错，很多站点禁止 HEAD），
           再取 m3u8 的**首个分片**，用 `bytes / elapsed` 估算吞吐。
           拿到一个分片就**提前停止** —— 测速不需要下完整个分片。

        只做「取首个分片」而不做「下完整个分片」是有意的：一个分片可能好几 MB，
        下完它会让 20 个源的测速从几秒变成半分钟，而带宽估算用首块已经够准。

        任何失败都只体现在返回值的 `error` 上，**绝不抛异常**（一个坏源不能毁掉整批测速）。
        `cancel` 是 `threading.Event`，在阶段之间检查；`manager` 用于把计时/下载放进
        传入的 `ThreadPoolExecutor`（供测试替换，生产上留空即可）。
        """
        from .probe import probe_source

        return probe_source(self, keyword, http=http, timeout=timeout,
                            manager=manager, cancel=cancel)

    # ---------- 工具 ----------

    def require_available(self) -> None:
        if not self.available():
            raise SourceError(self.unavailable_reason() or f"{self.label} 当前不可用")

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<{type(self).__name__} key={self.key!r}>"
