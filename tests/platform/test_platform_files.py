"""`core/platform/files.py` 的文件名净化与目标路径去重。

## 为什么单独测这一层

`safe_filename` / `unique_path` 原先在乐库、影视、图库各自的 `models.py` 与
`downloader.py` 里各抄一份（三份实现只差默认 fallback 与限长）。收敛到
`core.platform.files` 之后，**板块侧只剩薄封装**，因此这里必须把"共享实现"和
"各板块的历史默认值"两件事分别钉死：

1. 共享实现的规则与收敛前逐字一致（非法字符 → 下划线 → 合并空白 → 去首尾 → 截断）；
2. 三个板块的对外行为不变：图库限长 110、乐库/影视限长 120，默认 fallback 各是
   `track` / `video` / `image` —— 这些是用户能看到的文件名，不能因为"重构"而变。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from modu_workbench.core.platform.files import safe_filename, unique_path


# ------------------------------------------------------------------ 共享实现


def test_safe_filename_replaces_illegal_chars() -> None:
    assert safe_filename('a/b\\c:d*e?f"g<h>i|j', "file") == "a_b_c_d_e_f_g_h_i_j"


def test_safe_filename_collapses_whitespace_and_underscores() -> None:
    assert safe_filename("  a   b  ", "file") == "a b"
    assert safe_filename("a___b", "file") == "a_b"
    # 去掉首尾的空格与 `._`（Windows 上 "x." 建不出来）
    assert safe_filename("... x ...", "file") == "x"


def test_safe_filename_falls_back_when_empty() -> None:
    assert safe_filename("", "file") == "file"
    assert safe_filename("   ", "file") == "file"
    assert safe_filename("...", "file") == "file"


def test_safe_filename_respects_limit() -> None:
    assert len(safe_filename("x" * 300, "file")) == 120          # 默认上限
    assert len(safe_filename("x" * 300, "file", limit=110)) == 110
    assert len(safe_filename("x" * 300, "file", limit=5)) == 5


def test_safe_filename_keeps_cjk() -> None:
    """中文文件名不能被当成非法字符清掉（乐库/影视的曲名、片名都是中文）。"""
    assert safe_filename("周杰伦 - 晴天", "track") == "周杰伦 - 晴天"


# ------------------------------------------------------------------ 目标路径去重


def test_unique_path_creates_directory(tmp_path: Path) -> None:
    target = unique_path(tmp_path / "nested", "a.txt")
    assert target.parent.is_dir()


def test_unique_path_returns_plain_name_when_free(tmp_path: Path) -> None:
    assert unique_path(tmp_path, "a.txt").name == "a.txt"


def test_unique_path_appends_sequence_without_overwriting(tmp_path: Path) -> None:
    first = unique_path(tmp_path, "a.txt")
    first.write_text("1", encoding="utf-8")
    second = unique_path(tmp_path, "a.txt")
    assert second.name == "a (2).txt"
    second.write_text("2", encoding="utf-8")
    assert unique_path(tmp_path, "a.txt").name == "a (3).txt"
    # 原文件必须原样保留（"避免覆盖"是这个函数的唯一职责）
    assert first.read_text(encoding="utf-8") == "1"


def test_unique_path_keeps_multi_dot_stem(tmp_path: Path) -> None:
    """`s01.e02.mp4` 这类带点的名字只能加在最后一个后缀前。"""
    (tmp_path / "s01.e02.mp4").write_text("x", encoding="utf-8")
    assert unique_path(tmp_path, "s01.e02.mp4").name == "s01.e02 (2).mp4"


# ------------------------------------------------------- 各板块的默认值（行为守恒）


def test_board_safe_filename_defaults_unchanged() -> None:
    """三个板块的对外签名与限长必须与重构前一致。"""
    from modu_workbench.core.gallery.models import safe_filename as gallery_safe
    from modu_workbench.core.music.models import safe_filename as music_safe
    from modu_workbench.core.video.models import safe_filename as video_safe

    assert music_safe("   ") == "track"
    assert video_safe("   ") == "video"
    assert gallery_safe("   ") == "image"

    assert len(music_safe("x" * 300)) == 120
    assert len(video_safe("x" * 300)) == 120
    assert len(gallery_safe("x" * 300)) == 110      # 图库的历史限长：不能跟着变成 120

    assert music_safe('a/b:c*d?"e') == "a_b_c_d_e"
    assert video_safe('a/b:c*d?"e') == "a_b_c_d_e"
    assert gallery_safe('a/b:c*d?"e') == "a_b_c_d_e"


def test_downloader_unique_path_is_the_shared_one() -> None:
    """两个 downloader 对外仍暴露 `unique_path`，且指向同一份实现。"""
    from modu_workbench.core.music import downloader as music_downloader
    from modu_workbench.core.video import downloader as video_downloader

    assert music_downloader.unique_path is unique_path
    assert video_downloader.unique_path is unique_path


@pytest.mark.parametrize("name", ["a/b", "x" * 300, "   ", "周杰伦 - 晴天"])
def test_video_strip_html_delegates_to_textutil(name: str) -> None:
    """`core.video.models.strip_html` 与 `core.platform.textutil.strip_html` 必须同义。"""
    from modu_workbench.core.platform.textutil import strip_html as shared
    from modu_workbench.core.video.models import strip_html as video

    assert video(name) == shared(name)
    assert video("<p>简介&nbsp;文本</p>") == "简介 文本"
