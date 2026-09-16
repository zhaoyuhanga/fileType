"""电子书解析:支持 txt / epub。

- txt: 多阶段正则识别章节 + JUNK 过滤 + fallback 切分
- epub: ebooklib spine + toc 还原章节
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set, Tuple

import chardet
from bs4 import BeautifulSoup


@dataclass
class Chapter:
    """一个章节。"""

    title: str
    content: str
    start_offset: int = 0
    paragraphs: List[str] = field(default_factory=list)


@dataclass
class Book:
    """一本书的解析结果。"""

    path: str
    title: str
    author: str = ""
    format: str = ""
    chapters: List[Chapter] = field(default_factory=list)
    full_text: str = ""
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.chapters)


# ===========================================================================
# TXT 章节识别 — 多阶段正则
# ===========================================================================

# 中文章节号关键字
_CN_NUM = "零一二三四五六七八九十百千万〇两壹贰叁肆伍陆柒捌玖拾佰仟"

# 章节关键字(出现在"第N"后面,或单独成行)
_KW = "章回节卷篇部集话"

# 特殊章名(可以单独成行)
_SPECIAL_CHAPTERS = (
    "序章", "楔子", "引子", "后记", "尾声", "番外", "外篇", "内篇",
    "终章", "结语", "前言", "序言", "写在前面", "序",
)

# 明显的伪标题 — 出现这些关键字的行直接跳过
_JUNK_KEYWORDS = (
    "求推荐", "求订阅", "求收藏", "求月票", "求月", "求票",
    "PS:", "PS：", "PS ",
    "本章完", "本章结束", "(本章完)", "（本章完）",
    "加更", "加一更", "加更奉上",
    "QQ群", "书友群", "微信公众号", "关注公众号",
    "感谢打赏", "感谢订阅", "感谢支持",
    "防盗版", "防盗章",
    "已更新", "已修改", "已发布",
)


def _is_junk(line: str) -> bool:
    """判断是否伪标题(作者留言/章节通告等)。"""
    s = line.strip()
    if not s:
        return True
    if len(s) > 60:                # 太长一定不是标题(章节标题通常 <= 30 字)
        return True
    for j in _JUNK_KEYWORDS:
        if j in s:
            return True
    return False


def _is_valid_title(s: str) -> bool:
    """标题行必须满足:不是 JUNK,且长度合理(<= 60)。"""
    if _is_junk(s):
        return False
    # 排除明显是正文对话或描述的行(常见模式)
    bad_patterns = [
        r"第[一二三四五六七八九十]回合",
        r"第[一二三四五六七八九十]场",
        r"上半场", r"下半场",
    ]
    for pat in bad_patterns:
        if re.search(pat, s):
            return False
    return True


# 章节识别正则(按优先级,每条互斥)
# 注意:每条都用 ^ 强制行首,避免误匹配正文中的"第N章..."

# 0) ===xxx=== 包裹式: ===序章 大荒=== / ===第一章 朝气蓬勃===
_RE_EQ_WRAP = re.compile(
    r"^=+\s*"
    r"(?:正文[\s·•.、:：\-—]*)?"
    r"(?:第\s*[" + _CN_NUM + r"0-9]+\s*[" + _KW + r"]"
    r"|" + "|".join(_SPECIAL_CHAPTERS) + r")"
    r".*?=+\s*$"
)

# 1) 【xxx】 包裹式: 【第一章 xxx】
_RE_BRK_WRAP = re.compile(
    r"^【\s*"
    r"(?:正文[\s·•.、:：\-—]*)?"
    r"(?:第\s*[" + _CN_NUM + r"0-9]+\s*[" + _KW + r"]"
    r"|" + "|".join(_SPECIAL_CHAPTERS) + r")"
    r".*?】\s*$"
)

# 2) 正文 第N章 / 正文 第N卷(重生系小说最常见)
_RE_ZHENGWEN = re.compile(
    r"^正文[\s·•.、:：\-—]*"
    r"第\s*[" + _CN_NUM + r"0-9]+\s*[" + _KW + r"]"
    r"[\s\S]*$"
)

# 3) 正文 序章 / 正文 楔子
_RE_ZHENGWEN_SPECIAL = re.compile(
    r"^正文[\s·•.、:：\-—]*(" + "|".join(_SPECIAL_CHAPTERS) + r")\s*[\s\S]*$"
)

# 4) 裸 第N章/回/节/卷/篇/部/集/话 xxx(后跟标题或不跟)
_RE_BARE_KW = re.compile(
    r"^第\s*[" + _CN_NUM + r"0-9]+\s*[" + _KW + r"]"
    r"[\s\S]*$"
)

# 5) 裸 序章/楔子/后记...(单独成行)
_RE_BARE_SPECIAL = re.compile(
    r"^(" + "|".join(_SPECIAL_CHAPTERS) + r")\s*$"
)

# 6) 裸 序章/楔子/后记...(后面带标题)
_RE_BARE_SPECIAL_TITLED = re.compile(
    r"^(" + "|".join(_SPECIAL_CHAPTERS) + r")\s+[\s\S]+$"
)

# 7) Chapter 1 / CHAPTER I / Chapter One
_RE_CHAPTER_EN = re.compile(
    r"^Chapter\s+[0-9IVXLCDM" + _CN_NUM + r"]+\b[\s\S]*$",
    re.IGNORECASE,
)

# 8) 分卷 第N章 (大章节套小章节)
_RE_VOL_CHAP = re.compile(
    r"^第\s*[" + _CN_NUM + r"0-9]+\s*卷"
    r"[\s\S]*?第\s*[" + _CN_NUM + r"0-9]+\s*[" + _KW + r"]"
    r"[\s\S]*$"
)

# 所有正则按优先级
_ALL_PATTERNS = [
    _RE_EQ_WRAP, _RE_BRK_WRAP,
    _RE_ZHENGWEN, _RE_ZHENGWEN_SPECIAL,
    _RE_BARE_KW, _RE_BARE_SPECIAL, _RE_BARE_SPECIAL_TITLED,
    _RE_CHAPTER_EN, _RE_VOL_CHAP,
]


def _looks_like_chapter_title(line: str) -> bool:
    """是否像是章节标题行。"""
    s = line.strip()
    if not _is_valid_title(s):
        return False
    return any(pat.match(s) for pat in _ALL_PATTERNS)


def _read_txt_text(path: str) -> str:
    """读取 txt 文件,自动探测编码(优先 GBK/GB18030,中文小说 95% 是这两种)。"""
    raw = Path(path).read_bytes()
    # BOM 优先
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace")
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le", errors="replace")
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", errors="replace")

    # 试多种编码,选中文比例最高的(GBK/GB18030 优先)
    candidates = {}
    for enc in ("gbk", "gb18030", "utf-8", "big5", "utf-16", "gb2312"):
        try:
            t = raw.decode(enc, errors="replace")
            # 中文字符比例(Unicode 范围)
            sample = t[:8000]
            chinese = sum(1 for c in sample if '\u4e00' <= c <= '\u9fff')
            # 乱码字符比例:超出 ASCII + 中文 + 标点的控制字符 / 私有区字符
            garbage = sum(1 for c in sample
                          if c in '\ufffd'  # 替换字符
                          or (0x0080 <= ord(c) <= 0x009f)  # 控制字符
                          or (0xe000 <= ord(c) <= 0xf8ff))  # 私有区
            ratio = chinese / max(1, len(sample))
            garbage_ratio = garbage / max(1, len(sample))
            score = ratio - garbage_ratio * 2  # 乱码扣分
            candidates[enc] = (t, score, ratio, garbage_ratio)
        except Exception:
            continue
    if not candidates:
        return raw.decode("utf-8", errors="replace")
    # 选评分最高的
    best_enc, (text, score, ratio, gb_ratio) = max(
        candidates.items(), key=lambda kv: kv[1][1]
    )
    return text


def _split_by_pattern(text: str) -> List[Chapter]:
    """用多阶段正则找章节标题,切分。"""
    lines = text.splitlines()
    line_offsets = [0]
    for line in lines:
        line_offsets.append(line_offsets[-1] + len(line) + 1)  # +1 for \n

    # 找出所有候选标题行
    title_indices: List[int] = []
    for idx, line in enumerate(lines):
        if _looks_like_chapter_title(line):
            title_indices.append(idx)

    if not title_indices:
        return []

    chapters: List[Chapter] = []
    for i, line_idx in enumerate(title_indices):
        next_idx = title_indices[i + 1] if i + 1 < len(title_indices) else len(lines)
        title = lines[line_idx].strip()
        # 清理包裹符号 = / 【】 让标题更干净
        title = re.sub(r"^=+\s*", "", title)
        title = re.sub(r"\s*=+\s*$", "", title)
        title = re.sub(r"^【\s*", "", title)
        title = re.sub(r"\s*】\s*$", "", title)

        body_lines = lines[line_idx + 1: next_idx]
        content = "\n".join(body_lines).strip()
        start = line_offsets[line_idx + 1] if line_idx + 1 < len(line_offsets) else 0
        chapters.append(Chapter(title=title, content=content, start_offset=start))

    # 后处理:删除"空内容章节"(通常是正文首行重复章节名造成的"假章节")
    #   - 如果某章 content 为空,把它和上一章的标题去重后保留上一章
    chapters = _dedup_empty_chapters(chapters)
    return chapters


def _normalize_title(s: str) -> str:
    """把标题归一化(去前缀/空白/标点)用于去重比较。"""
    s = re.sub(r"^正文[\s·•.、:：\-—]*", "", s)
    s = re.sub(r"^=+\s*|\s*=+$", "", s)
    s = re.sub(r"^【\s*|\s*】$", "", s)
    s = re.sub(r"[\s　]+", "", s)
    return s


def _dedup_empty_chapters(chapters: List[Chapter]) -> List[Chapter]:
    """删除"空内容章节"。

    网文常见模式:章节标题行后,正文第一行又重复一次章节名(无"正文"前缀)。
    这导致两个相邻"标题",中间夹一行空,后面才是真正的章节内容。
    我们把空内容章节 + 后续"重复章节"合并到上一章。
    """
    if not chapters:
        return chapters
    out: List[Chapter] = [chapters[0]]
    for ch in chapters[1:]:
        # 如果这章内容为空,大概率是"重复标题",跳过
        if not ch.content.strip():
            # 也尝试把它的标题作为 alias 并入上一章(去掉"正文"前缀后)
            alias = _normalize_title(ch.title)
            prev_alias = _normalize_title(out[-1].title)
            # 只有当标题不同时才追加(避免重复)
            if alias and alias != prev_alias:
                out[-1].title = f"{out[-1].title}（{ch.title}）"
            continue
        # 如果这章内容不为空,但它的标题和上一章"归一化"后相同,说明是另一种重复
        if _normalize_title(ch.title) == _normalize_title(out[-1].title):
            # 跳过(保留上一章的标题)
            continue
        out.append(ch)
    return out


def _split_by_blank_lines(text: str, min_blank: int = 2, max_chars: int = 5000) -> List[Chapter]:
    """Fallback:按连续空行分大段。

    - min_blank: 至少 N 个连续空行才算分章(默认 2)
    - max_chars: 兜底,单章不超过 N 字
    """
    lines = text.splitlines()
    line_offsets = [0]
    for line in lines:
        line_offsets.append(line_offsets[-1] + len(line) + 1)

    # 找出所有"连续 min_blank 个空行"的位置
    blank_runs: List[int] = []  # 记录每段连续空行的起始行
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            j = i
            while j < len(lines) and not lines[j].strip():
                j += 1
            run_len = j - i
            if run_len >= min_blank:
                blank_runs.append(i)
            i = j
        else:
            i += 1

    if len(blank_runs) < 2:
        return []

    chapters: List[Chapter] = []
    chap_num = 0
    seg_starts = [0] + [r + min_blank for r in blank_runs]  # 每段开始行
    seg_ends = [r for r in blank_runs] + [len(lines)]

    for start_line, end_line in zip(seg_starts, seg_ends):
        body = "\n".join(lines[start_line: end_line]).strip()
        if not body:
            continue
        # 兜底:太长就再切
        if len(body) > max_chars * 2:
            sub_lines = body.split("\n")
            cur = []
            cur_chars = 0
            for sl in sub_lines:
                cur.append(sl)
                cur_chars += len(sl)
                if cur_chars >= max_chars:
                    chap_num += 1
                    chapters.append(Chapter(
                        title=f"第{chap_num}段", content="\n".join(cur).strip(),
                        start_offset=line_offsets[start_line] if start_line < len(line_offsets) else 0,
                    ))
                    cur = []
                    cur_chars = 0
            if cur:
                chap_num += 1
                chapters.append(Chapter(
                    title=f"第{chap_num}段", content="\n".join(cur).strip(),
                    start_offset=line_offsets[start_line] if start_line < len(line_offsets) else 0,
                ))
        else:
            chap_num += 1
            # 用段落第一行非空内容做标题(限 30 字)
            first_line = next((l.strip() for l in lines[start_line: end_line] if l.strip()), "")
            title = first_line[:30] if first_line else f"第{chap_num}段"
            chapters.append(Chapter(
                title=title,
                content=body,
                start_offset=line_offsets[start_line] if start_line < len(line_offsets) else 0,
            ))

    return chapters


def _split_by_separator(text: str) -> List[Chapter]:
    """Fallback:按 "==========" / "-----------" 等分隔符切。"""
    lines = text.splitlines()
    line_offsets = [0]
    for line in lines:
        line_offsets.append(line_offsets[-1] + len(line) + 1)

    sep_re = re.compile(r"^[=\-_*]{5,}\s*$")
    boundaries = []
    for i, line in enumerate(lines):
        if sep_re.match(line):
            boundaries.append(i)

    if len(boundaries) < 2:
        return []

    chapters: List[Chapter] = []
    seg_starts = [b + 1 for b in boundaries[:-1]]
    seg_ends = [b for b in boundaries[1:]] + [len(lines)]
    for n, (s, e) in enumerate(zip(seg_starts, seg_ends), 1):
        body = "\n".join(lines[s: e]).strip()
        if not body:
            continue
        first_line = next((l.strip() for l in lines[s: e] if l.strip()), "")
        title = first_line[:30] if first_line else f"第{n}段"
        chapters.append(Chapter(
            title=title,
            content=body,
            start_offset=line_offsets[s] if s < len(line_offsets) else 0,
        ))
    return chapters


def _split_by_fixed_chars(text: str, chars_per_chap: int = 5000) -> List[Chapter]:
    """最后兜底:按固定字数切。"""
    if len(text) < chars_per_chap * 2:
        return []
    chapters: List[Chapter] = []
    n = len(text)
    pos = 0
    chap_num = 0
    while pos < n:
        end = min(pos + chars_per_chap, n)
        # 找下一个换行做边界
        nl = text.find("\n", end)
        if nl == -1 or nl >= n:
            nl = n
        chunk = text[pos: nl].strip()
        if chunk:
            chap_num += 1
            chapters.append(Chapter(
                title=f"第{chap_num}段",
                content=chunk,
                start_offset=pos,
            ))
        pos = nl + 1
    return chapters


def _split_txt_into_chapters(text: str) -> List[Chapter]:
    """多策略识别章节。

    优先级:
    1) 主正则(>= 3 章即采用)
    2) 分隔符切(>= 2 章 + 比主正则多)
    3) 连续空行切(>= 2 章 + 比主正则多)
    4) 固定字数切(>= 2 章)
    5) 整本当一章
    """
    # 1) 主正则
    chapters = _split_by_pattern(text)
    n_main = len(chapters)
    if n_main >= 3:
        return chapters

    # 2) Fallback: 分隔符
    chapters_sep = _split_by_separator(text)
    if len(chapters_sep) >= 2 and len(chapters_sep) > n_main:
        return chapters_sep

    # 3) Fallback: 连续空行
    chapters_blank = _split_by_blank_lines(text, min_blank=2)
    if len(chapters_blank) >= 2 and len(chapters_blank) > n_main:
        return chapters_blank

    # 4) Fallback: 固定字数
    if n_main == 0:
        chapters_fixed = _split_by_fixed_chars(text, chars_per_chap=5000)
        if len(chapters_fixed) >= 2:
            return chapters_fixed

    # 主正则有结果就用主正则
    if n_main > 0:
        return chapters
    # 都没有就整本当一章
    return [Chapter(title="全文", content=text.strip(), start_offset=0)]


def _guess_txt_meta(path: str, full_text: str) -> tuple[str, str]:
    """从文件名/首行尽量提取书名和作者。"""
    stem = Path(path).stem
    title, author = stem, ""
    m = re.match(r"^《(.+?)》\s*[-_—]?\s*(.+?)$", stem)
    if m:
        title, author = m.group(1).strip(), m.group(2).strip()
    else:
        m = re.match(r"^(.+?)\s*[-_—]\s*(.+?)$", stem)
        if m and len(m.group(1)) <= 60 and len(m.group(2)) <= 30:
            title, author = m.group(1).strip(), m.group(2).strip()
    return title, author


def parse_txt(path: str) -> Book:
    text = _read_txt_text(path)
    title, author = _guess_txt_meta(path, text)
    chapters = _split_txt_into_chapters(text)
    return Book(
        path=path,
        title=title,
        author=author,
        format="txt",
        chapters=chapters,
        full_text=text,
    )


# ===========================================================================
# EPUB 解析(unchanged)
# ===========================================================================

def _html_to_text(html: bytes) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    paras = []
    for p in soup.find_all(["p", "br", "div", "h1", "h2", "h3", "h4"]):
        t = p.get_text(" ", strip=True)
        if t:
            paras.append(t)
    if not paras:
        paras = [soup.get_text("\n", strip=True)]
    return "\n".join(paras)


def parse_epub(path: str) -> Book:
    from ebooklib import epub, ITEM_DOCUMENT

    book = epub.read_epub(path)
    title = (book.get_metadata("DC", "title") or [["未知书名"]])[0][0]
    creators = book.get_metadata("DC", "creator") or []
    author = creators[0][0] if creators else ""

    toc_title_by_id: dict[str, str] = {}
    for _t_id, _t_title, _t_content in _walk_toc(book.toc):
        if _t_content is not None and _t_title:
            tid = getattr(_t_content, "id", None) or getattr(_t_content, "file_name", None)
            if tid:
                toc_title_by_id[str(tid)] = _t_title

    toc_item_ids: Set[str] = set()
    for _t_id, _t_title, _t_content in _walk_toc(book.toc):
        if _t_content is not None:
            tid = getattr(_t_content, "id", None) or getattr(_t_content, "file_name", None)
            if tid:
                toc_item_ids.add(str(tid))

    chapters: List[Chapter] = []
    full_text_parts: List[str] = []
    offset = 0

    for spine_id in book.spine:
        if isinstance(spine_id, tuple):
            spine_id = spine_id[0]
        item = None
        if isinstance(spine_id, str):
            item = book.get_item_with_id(spine_id)
        else:
            item = spine_id
        if item is None:
            continue
        try:
            if item.get_type() != ITEM_DOCUMENT:
                continue
        except AttributeError:
            continue
        item_id = (getattr(item, "id", "") or "").lower()
        item_fname = (getattr(item, "file_name", "") or "").lower()
        if "nav" in item_id or "nav" in item_fname:
            continue

        content_html = item.get_content()
        text = _html_to_text(content_html)
        if not text.strip():
            continue

        item_id_str = getattr(item, "id", None) or getattr(item, "file_name", None)
        item_title = toc_title_by_id.get(str(item_id_str), "")
        if not item_title:
            soup = BeautifulSoup(content_html, "lxml")
            for tag in soup.find_all(["h1", "h2", "h3"]):
                t = tag.get_text(strip=True)
                if t:
                    item_title = t
                    break
        if not item_title:
            item_title = Path(item.get_name()).stem or f"章节{len(chapters) + 1}"

        start = offset
        full_text_parts.append(text)
        offset += len(text) + 1
        chapters.append(Chapter(title=item_title, content=text, start_offset=start))

    full_text = "\n".join(full_text_parts)
    if not chapters:
        return Book(path=path, title=title, author=author, format="epub", error="EPUB 内无章节内容")

    return Book(
        path=path,
        title=title,
        author=author,
        format="epub",
        chapters=chapters,
        full_text=full_text,
    )


def _walk_toc(toc):
    for node in toc:
        if isinstance(node, tuple):
            section, children = node
            content = getattr(section, "content", None)
            yield (getattr(section, "ebook_id", "") or "",
                   getattr(section, "title", ""), content)
            yield from _walk_toc(children)
        else:
            content = getattr(node, "content", None)
            yield (getattr(node, "ebook_id", "") or "",
                   getattr(node, "title", ""), content)


# ===========================================================================
# 入口
# ===========================================================================

def parse_book(path: str) -> Book:
    """根据后缀分发到对应解析器。"""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".txt":
            return parse_txt(path)
        if ext == ".epub":
            return parse_epub(path)
        return Book(path=path, title=os.path.basename(path), format=ext,
                    error=f"暂不支持的格式: {ext}")
    except Exception as e:  # noqa: BLE001
        return Book(path=path, title=os.path.basename(path), format=ext.lstrip("."),
                    error=f"解析失败: {e}")
