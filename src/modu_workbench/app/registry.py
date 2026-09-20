"""板块注册表：墨软·工作台当前可用板块。"""
from __future__ import annotations

from ..boards.book import board as book_board
from ..boards.convert import board as convert_board
from ..boards.document import board as document_board
from ..boards.gallery import board as gallery_board
from ..boards.music import board as music_board
from ..boards.video import board as video_board
from ..boards.base import BoardSpec

BOOK_BOARD = BoardSpec(
    key="book",
    title="墨软书库",
    tagline="电子书阅读 · 本地书库",
    description="导入 TXT / EPUB，自动章节解析、进度记忆、阅读主题与历史记录；内置在线书库下载（个人学习用途）。",
    icon="📚",
    phase="可用",
    page=book_board.BookBoardPage,
)

CONVERT_BOARD = BoardSpec(
    key="convert",
    title="墨软转换",
    tagline="文本 / 文档 / 表格 / 图片 / 音视频 / 字幕 / 电子书 / 归档",
    description=(
        "本地离线转换：68 种格式、592 个动作 —— 文本与数据互转（txt/md/json/xml/yaml/ini/csv）、"
        "文档与表格（docx/xlsx/odt/rtf/pdf）、图片（含图片转 PDF）、音视频、字幕（srt/vtt）、"
        "电子书（epub）与压缩解压（zip/tar/gz/bz2/xz），并可查看编辑与批量处理。"
        "音视频依赖随包 ffmpeg、Office 高保真依赖 LibreOffice，缺依赖的动作会置灰并说明原因。"
    ),
    icon="🔄",
    phase="可用",
    page=convert_board.ConvertBoardPage,
)

DOCUMENT_BOARD = BoardSpec(
    key="document",
    title="墨软文档",
    tagline="查看编辑 · 格式美化 · 填充计算 · 文档合并 · AI 循环美化",
    description=(
        "一站式文档工作台：Word/PDF/Excel/Markdown/TXT/HTML/EPUB/PPT/CSV/JSON 等格式的查看与编辑；"
        "一键与模板美化（公文/报告/论文/合同/简历/会议纪要）；自动填充、智能填充、公式计算与统计；"
        "配置 AI 文本模型后可用 7 类场景与 30+ 文档工具，并把两个文档合并（同格式与跨格式）；"
        "AI 循环美化按「分析→计划→工具→评分」多轮优化，每轮可预览、可回滚、有日志。"
    ),
    icon="📄",
    phase="可用",
    page=document_board.DocumentBoardPage,
)

MUSIC_BOARD = BoardSpec(
    key="music",
    title="墨软乐库",
    tagline="在线搜索 · 本地曲库 · 歌单播放",
    description="联网搜索并下载音乐、内置播放器（顺序/循环/随机）、歌单收藏与分类、播放历史与音频格式转换。",
    icon="🎧",
    phase="可用",
    page=music_board.MusicBoardPage,
)

VIDEO_BOARD = BoardSpec(
    key="video",
    title="墨软影视",
    tagline="在线搜索 · 本地播放 · 分类收藏",
    description=(
        "联网搜索电影/电视剧/动漫并下载到本地；内置播放器（多清晰度、倍速、续播）、"
        "分类与收藏、播放历史，以及视频格式转换。多数据源可切换、支持换源重试。"
    ),
    icon="🎬",
    phase="可用",
    page=video_board.VideoBoardPage,
)

GALLERY_BOARD = BoardSpec(
    key="gallery",
    title="墨软图库",
    tagline="本地相册 · 分类收藏 · 美化与增强",
    description=(
        "读取本地图片并生成缩略图，网格/瀑布流/时间轴浏览；自动与手动分类、标签、收藏、"
        "重复识别；批量导入与网址/剪贴板收集；裁剪滤镜调节文字马赛克等美化（可撤销），"
        "以及本地增强（一键增强/超分/降噪/去模糊/抠图/消除）。"
    ),
    icon="🖼",
    phase="可用",
    page=gallery_board.GalleryBoardPage,
)

# 首页卡片与顶栏导航顺序：前五个板块顺序固定（历史约定），
# 第六大板块「墨软文档」追加在末尾，避免影响既有板块的入口位置。
ACTIVE_BOARDS: tuple[BoardSpec, ...] = (
    BOOK_BOARD, CONVERT_BOARD, MUSIC_BOARD, VIDEO_BOARD, GALLERY_BOARD, DOCUMENT_BOARD,
)


def get_board(key: str) -> BoardSpec | None:
    for spec in ACTIVE_BOARDS:
        if spec.key == key:
            return spec
    return None
