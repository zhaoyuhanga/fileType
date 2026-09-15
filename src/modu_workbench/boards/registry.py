"""板块注册表：墨读·工作台当前可用板块。"""
from __future__ import annotations

from . import book_board, convert_board, music_board
from .base import BoardSpec

BOOK_BOARD = BoardSpec(
    key="book",
    title="墨读书库",
    tagline="电子书阅读 · 本地书库",
    description="导入 TXT / EPUB，自动章节解析、进度记忆、阅读主题与历史记录；内置在线书库下载（个人学习用途）。",
    icon="📚",
    phase="可用",
    page=book_board.BookBoardPage,
)

CONVERT_BOARD = BoardSpec(
    key="convert",
    title="墨读转换",
    tagline="文档 / 表格 / 图片 / 媒体 / 归档",
    description="本地离线转换：文本互转、PDF、图片、归档与查看编辑（txt/md/json/mp4）；Word/Excel、音视频能力陆续升级中。",
    icon="🔄",
    phase="基础可用",
    page=convert_board.ConvertBoardPage,
)

MUSIC_BOARD = BoardSpec(
    key="music",
    title="墨读音乐",
    tagline="在线搜索 · 本地曲库 · 歌单播放",
    description="联网搜索并下载音乐、内置播放器（顺序/循环/随机）、歌单收藏与分类、播放历史与音频格式转换。",
    icon="🎧",
    phase="可用",
    page=music_board.MusicBoardPage,
)

# 首页卡片与顶栏导航顺序
ACTIVE_BOARDS: tuple[BoardSpec, ...] = (BOOK_BOARD, CONVERT_BOARD, MUSIC_BOARD)


def get_board(key: str) -> BoardSpec | None:
    for spec in ACTIVE_BOARDS:
        if spec.key == key:
            return spec
    return None
