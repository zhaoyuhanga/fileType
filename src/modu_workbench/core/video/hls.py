"""HLS(m3u8) 解析：主清单取多清晰度，媒体清单取分片，供播放与下载共用。

第三方采集站的播放地址几乎都是 m3u8，因此这里需要处理三件事：
1. **多清晰度**：主清单（EXT-X-STREAM-INF）里的每个 variant 就是一个清晰度选项，
   直接对应用户诉求第 7 条「清晰度多选项」；
2. **分片下载**：媒体清单里的 EXTINF 分片列表，按顺序抓回来再合流成单个文件；
3. **加密兜底**：遇到 AES-128 加密（EXT-X-KEY）时明确报错，
   而不是下载出一堆无法播放的碎片（合流交给 ffmpeg，它能处理密钥）。
"""
from __future__ import annotations

import binascii
import re
from dataclasses import dataclass, field
from typing import List
from urllib.parse import urljoin

# 无画质标注时用的占位标签
QUALITY_FALLBACK = "原画"


@dataclass
class HlsVariant:
    """主清单里的一个清晰度选项。"""

    url: str
    label: str = ""
    bandwidth: int = 0
    width: int = 0
    height: int = 0

    @property
    def display(self) -> str:
        if self.label:
            return self.label
        if self.height:
            return f"{self.height}P"
        if self.bandwidth:
            return f"{self.bandwidth // 1000} kbps"
        return QUALITY_FALLBACK

    @property
    def rank(self) -> int:
        return self.height or (self.bandwidth // 1000 if self.bandwidth else 0)


@dataclass
class HlsPlaylist:
    """解析结果：要么是主清单（有 variants），要么是媒体清单（有 segments）。"""

    is_master: bool = False
    is_media: bool = False
    variants: List[HlsVariant] = field(default_factory=list)
    segments: List[str] = field(default_factory=list)
    target_duration: float = 0.0
    encrypted: bool = False
    # AES-128 分片加密信息（METHOD=AES-128 时有效）：
    # 有 key_uri 就能用标准库自行解密，无需 ffmpeg；SAMPLE-AES 等则必须交给 ffmpeg
    key_method: str = ""
    key_uri: str = ""
    key_iv: bytes | None = None
    live: bool = False
    headers: dict = field(default_factory=dict)

    @property
    def aes128(self) -> bool:
        """是否为标准 AES-128 加密（可自行解密）。"""
        return self.key_method.upper() == "AES-128" and bool(self.key_uri)

    @property
    def duration_seconds(self) -> float:
        return self.target_duration * len(self.segments)

    def best_variant(self) -> HlsVariant | None:
        if not self.variants:
            return None
        return max(self.variants, key=lambda item: item.rank)


def _attrs_of(line: str) -> dict:
    """解析 `#EXT-X-STREAM-INF:` 后面的 KEY=VALUE 列表（值可能带引号）。"""
    result: dict = {}
    payload = line.split(":", 1)[1] if ":" in line else ""
    for match in re.finditer(r'([A-Z0-9\-]+)=("[^"]*"|[^,]*)', payload):
        key = match.group(1)
        value = match.group(2).strip('"')
        result[key] = value
    return result


def quality_label(height: int, name: str = "") -> str:
    """按分辨率高度给出常见清晰度标签。"""
    if height >= 2000:
        return "4K"
    if height >= 1400:
        return "2K"
    if height >= 1000:
        return "1080P"
    if height >= 700:
        return "720P"
    if height >= 500:
        return "540P"
    if height >= 400:
        return "480P"
    if height > 0:
        return f"{height}P"
    return name or QUALITY_FALLBACK


def parse_m3u8(text: str, base_url: str) -> HlsPlaylist:
    """解析 m3u8 文本；相对地址会用 base_url 补全为绝对地址。"""
    playlist = HlsPlaylist(headers={})
    lines = [line.strip() for line in (text or "").splitlines()]
    if not any(line.startswith("#EXTM3U") for line in lines[:5]):
        # 不是合法清单：交由上层判定为「拿到的不是 m3u8」
        return playlist

    pending_variant: dict | None = None
    pending_inf: float = 0.0
    has_endlist = "#EXT-X-ENDLIST" in text
    saw_media_sequence = False
    inf_durations: list[float] = []
    declared_target = 0.0

    for line in lines:
        if not line:
            continue
        if line.startswith("#EXT-X-STREAM-INF"):
            pending_variant = _attrs_of(line)
            continue
        if line.startswith("#EXT-X-MEDIA-SEQUENCE"):
            saw_media_sequence = True
            continue
        if line.startswith("#EXT-X-STREAM") or line.startswith("#EXT-X-I-FRAME-STREAM-INF"):
            continue
        if line.startswith("#EXT-X-KEY"):
            attrs = _attrs_of(line)
            method = str(attrs.get("METHOD", "NONE")).upper()
            if method and method != "NONE":
                playlist.encrypted = True
                playlist.key_method = method
                uri = str(attrs.get("URI") or "")
                if uri:
                    playlist.key_uri = urljoin(base_url, uri) if base_url else uri
                iv_hex = str(attrs.get("IV") or "").strip()
                if iv_hex.lower().startswith("0x"):
                    iv_hex = iv_hex[2:]
                if iv_hex:
                    try:
                        playlist.key_iv = binascii.unhexlify(iv_hex)
                    except (binascii.Error, ValueError):
                        playlist.key_iv = None
            continue
        if line.startswith("#EXT-X-TARGETDURATION"):
            try:
                declared_target = float(line.split(":", 1)[1].strip())
            except (IndexError, ValueError):
                declared_target = 0.0
            continue
        if line.startswith("#EXTINF"):
            payload = line.split(":", 1)[1] if ":" in line else ""
            try:
                pending_inf = float(payload.split(",")[0].strip())
            except (ValueError, IndexError):
                pending_inf = 0.0
            continue
        if line.startswith("#"):
            continue

        # 非 # 开头的行就是 URI
        absolute = urljoin(base_url, line) if base_url else line
        if pending_variant is not None:
            try:
                bandwidth = int(pending_variant.get("BANDWIDTH") or 0)
            except ValueError:
                bandwidth = 0
            try:
                width = int(pending_variant.get("RESOLUTION", "0x0").split("x")[0])
                height = int(pending_variant.get("RESOLUTION", "0x0").split("x")[-1])
            except (ValueError, IndexError):
                width = height = 0
            label = pending_variant.get("NAME") or quality_label(height)
            playlist.variants.append(HlsVariant(
                url=absolute, label=label, bandwidth=bandwidth, width=width, height=height,
            ))
            pending_variant = None
            continue

        playlist.segments.append(absolute)
        if pending_inf:
            inf_durations.append(pending_inf)
        pending_inf = 0.0

    playlist.is_master = bool(playlist.variants)
    playlist.is_media = bool(playlist.segments)
    # 分片时长优先用 EXTINF 的最大值（贴合 TARGETDURATION 语义）；
    # 没有 EXTINF 时才退回清单声明的 TARGETDURATION
    if inf_durations:
        playlist.target_duration = max(inf_durations)
    else:
        playlist.target_duration = declared_target
    # 无法点播的直播流：有媒体序列但清单没有结束标记
    playlist.live = (not playlist.is_master) and saw_media_sequence and not has_endlist
    return playlist


def is_m3u8_url(url: str) -> bool:
    lowered = (url or "").lower().split("?", 1)[0]
    return lowered.endswith(".m3u8")


def segment_iv(playlist: HlsPlaylist, index: int) -> bytes:
    """分片解密用的 IV。

    规范：清单显式给了 EXT-X-KEY 的 IV 就用它；否则用「媒体序列号」作为大端整数
    补成 16 字节。本实现按分片序号从 0 开始（EXT-X-MEDIA-SEQUENCE 默认为 0）。
    """
    if playlist.key_iv is not None:
        return playlist.key_iv
    return index.to_bytes(16, "big")


def aes128_decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    """用标准库实现解密一个 HLS AES-128 分片（CBC + PKCS#7 去填充）。"""
    from .aes import aes128_cbc_decrypt

    return aes128_cbc_decrypt(data, key, iv)


def resolve_url(base_url: str, target: str) -> str:
    """把清单里的相对地址补全为绝对地址。"""
    if not target:
        return ""
    if target.lower().startswith(("http://", "https://")):
        return target
    return urljoin(base_url, target)


def list_qualities(m3u8_url: str, *, http=None, referer: str = "") -> list[HlsVariant]:  # noqa: ANN001
    """拉取 m3u8 主清单，返回可选清晰度列表（失败时返回空列表）。

    供界面「清晰度多选项」下拉使用：主清单有 variant 就能让用户挑，
    没有 variant（单清晰度）时由调用方回退到源自带的标签。
    """
    if not m3u8_url:
        return []
    # 走 headers 传 Referer，而不是自定义 kwarg —— 这样测试替身也只需实现 requests 风格接口
    kwargs: dict = {"headers": {"Referer": referer}} if referer else {}
    try:
        if http is not None:
            text = http.get_text(m3u8_url, **kwargs)
        else:
            from .sources.http import HttpClient

            text = HttpClient().get_text(m3u8_url, **kwargs)
    except Exception:  # noqa: BLE001  清晰度探测失败不影响播放
        return []
    playlist = parse_m3u8(text, m3u8_url)
    return playlist.variants
