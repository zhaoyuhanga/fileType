"""乐库 · 跨源换歌匹配（`core/music/sources/matcher.py`）测试。

## 为什么这个模块必须测

它决定"**两个音源里的这首歌是不是同一首**"。乐库的「换源」「多源回退」
全靠它 —— 匹配错了的后果很具体：

- 用户点周杰伦的《稻香》，换源后放出来是别人的翻唱；
- 下载任务把 A 歌的文件存成 B 歌的名字；
- 歌单里同一首歌出现好几遍（不同源的同一首没被认出来）。

这类错误用户很难描述（"放错了""名字不对"），也最难排查。

这里测的是**判断的相对关系**，不去断言具体分值（那是把实现抄进测试）：
同名同歌手 → 高分；不同歌 → 低分；时长差很多 → 降分；未知时长 → 中性。
"""
from __future__ import annotations

import pytest

from modu_workbench.core.music.sources import matcher as M
from modu_workbench.core.music.models import RemoteTrack


def track(title: str, artist: str = "", duration_ms: int = 0, source: str = "a") -> RemoteTrack:
    """构造一个候选曲目。"""
    return RemoteTrack(
        source=source,
        remote_id=f"{source}-{title}",
        title=title,
        artist=artist,
        album="",
        duration_ms=duration_ms,
    )


# --------------------------------------------------------------------------- 归一化


@pytest.mark.parametrize(
    ("raw", "expected_contains"),
    [
        ("周杰伦 - 稻香", "稻香"),
        ("【官方】稻香 (Live)", "稻香"),
        ("ＡＢＣ", "abc"),          # 全角转半角
    ],
)
def test_normalize_strips_noise(raw: str, expected_contains: str) -> None:
    """归一化要去掉"歌手 - "前缀、方括号噪声、大小写与全角差异。"""
    normalized = M.normalize(raw)
    assert expected_contains in normalized


def test_normalize_makes_equivalent_titles_equal() -> None:
    """同一个标题的不同写法归一化后应一致（否则永远匹配不上）。"""
    variants = ["稻香", "稻香 ", " 《稻香》 ", "【HQ】稻香"]
    normalized = {M.normalize(v) for v in variants}
    assert normalized == {"稻香"}, f"这些写法没有被归一到一起：{normalized}"


def test_normalize_empty_is_safe() -> None:
    """空标题不崩。"""
    assert M.normalize("") == ""
    assert M.normalize(None) == ""  # type: ignore[arg-type]


# --------------------------------------------------------------------------- 歌手拆分


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("周杰伦/费玉清", {"周杰伦", "费玉清"}),
        ("周杰伦、方文山", {"周杰伦", "方文山"}),
        ("A feat. B", {"a", "b"}),
        ("独唱", {"独唱"}),
    ],
)
def test_artists_of_splits_collaborators(text: str, expected: set[str]) -> None:
    """合唱/合作要拆成多个歌手（否则「A & B」和「B & A」会被当成不同歌手）。"""
    assert M.artists_of(text) == expected


# --------------------------------------------------------------------------- 单项打分


def test_title_score_same_is_high_different_is_low() -> None:
    """同名高分、异名低分。"""
    assert M.title_score("稻香", "稻香") >= 0.9
    assert M.title_score("稻香", "青花瓷") <= 0.2


def test_title_score_handles_variants() -> None:
    """带版本标注的同一首歌不应被判成完全不同。"""
    assert M.title_score("稻香", "稻香 (Live)") >= 0.5


def test_artist_score_same_is_high_different_is_low() -> None:
    """同名歌手高分、不同歌手低分。"""
    assert M.artist_score("周杰伦", "周杰伦") >= 0.9
    assert M.artist_score("周杰伦", "林俊杰") <= 0.2


def test_artist_score_empty_is_neutral() -> None:
    """歌手信息缺失时给中性分，不能直接判 0（很多源不带歌手）。"""
    score = M.artist_score("", "周杰伦")
    assert 0.2 <= score <= 0.8, f"缺失歌手时分数过于极端：{score}"


@pytest.mark.parametrize(
    ("left", "right", "low", "high"),
    [
        (200_000, 200_000, 0.95, 1.0),      # 完全一致
        (200_000, 201_000, 0.8, 1.0),       # 差 1 秒
        (200_000, 260_000, 0.0, 0.3),       # 差 1 分钟 → 大概率不是同一首
        (0, 200_000, 0.4, 0.6),             # 时长未知 → 中性
    ],
)
def test_duration_score(left: int, right: int, low: float, high: float) -> None:
    """时长接近给高分，差很多给低分，未知给中性（不能当"完全不符"）。"""
    score = M.duration_score(left, right)
    assert low <= score <= high, f"duration_score({left},{right}) = {score}，期望 {low}~{high}"


# --------------------------------------------------------------------------- 综合匹配


def test_match_score_prefers_identical_track() -> None:
    """同一首歌（同名同歌手同时长）总分应很高。"""
    score = M.match_score(track("稻香", "周杰伦", 200_000), track("稻香", "周杰伦", 200_000))
    assert score >= 0.9, f"同一首歌得分过低：{score}"


def test_match_score_rejects_different_song() -> None:
    """完全不同的歌总分应很低（不能因为同源就拉高）。"""
    score = M.match_score(track("稻香", "周杰伦", 200_000), track("青花瓷", "林俊杰", 200_000))
    assert score <= 0.4, f"不同的歌得分过高（会换错源）：{score}"


def test_match_score_same_title_different_artist_is_middling() -> None:
    """同名不同歌手（常见于翻唱）应处于中间地带，不该直接判为同一首。"""
    same = M.match_score(track("稻香", "周杰伦"), track("稻香", "周杰伦"))
    covered = M.match_score(track("稻香", "周杰伦"), track("稻香", "某翻唱歌手"))
    assert covered < same, "翻唱不应与原作者同分"


def test_best_match_picks_the_right_candidate() -> None:
    """多个候选中应挑出真正对应的那首。"""
    original = track("稻香", "周杰伦", 200_000)
    candidates = [
        track("青花瓷", "周杰伦", 210_000, source="b"),
        track("稻香", "周杰伦", 201_000, source="c"),
        track("晴天", "周杰伦", 199_000, source="d"),
    ]
    best = M.best_match(original, candidates)
    assert best is not None
    assert best.source == "c", f"选错了候选：{best.source}"


def test_best_match_returns_none_when_nothing_close() -> None:
    """没有足够接近的候选时必须返回 None —— 宁可"没换到"也不能换错。"""
    original = track("稻香", "周杰伦", 200_000)
    candidates = [
        track("青花瓷", "林俊杰", 300_000, source="b"),
        track("完全无关", "陌生歌手", 100_000, source="c"),
    ]
    assert M.best_match(original, candidates) is None


def test_best_match_empty_candidates() -> None:
    """没有候选时不崩，返回 None。"""
    assert M.best_match(track("稻香"), []) is None
    assert M.best_match(track("稻香"), None) is None  # type: ignore[arg-type]


def test_best_match_threshold_is_respected() -> None:
    """`threshold` 生效：调高阈值后原本能匹配的也应该匹配不上。"""
    original = track("稻香", "周杰伦", 200_000)
    candidates = [track("稻香", "", 200_000, source="b")]   # 缺歌手，分数中等

    lenient = M.best_match(original, candidates, threshold=0.1)
    strict = M.best_match(original, candidates, threshold=0.99)
    assert lenient is not None, "低阈值下应当匹配上"
    assert strict is None, "高阈值下不应匹配"


def test_best_match_prefers_higher_score_among_many() -> None:
    """多个都能过阈值时，应选分数最高的那个。"""
    original = track("稻香", "周杰伦", 200_000)
    candidates = [
        track("稻香", "", 200_000, source="exact-title"),
        track("稻香", "周杰伦", 200_000, source="exact-all"),
    ]
    best = M.best_match(original, candidates)
    assert best is not None and best.source == "exact-all"
