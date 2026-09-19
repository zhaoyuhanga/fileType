"""电子书族：EPUB → 纯文本，以及纯文本 → 可读的 EPUB。

为什么要按 spine 抽正文（而不是 manifest）：

- EPUB 的 ``content.opf`` 里 manifest 是「文件清单」，spine 才是「阅读顺序」。
  按 manifest 抽会把前言、附录、版权页的顺序打乱，读者看到的正文是乱的；
  所以这里严格按 spine 遍历，spine 缺失时才退化成按 manifest 顺序取文档。
- ``nav.xhtml``（EPUB3 目录）和 ``toc.ncx``（EPUB2 目录）里也全是文字。
  坑在于：ebooklib 里 ``EpubNav`` 继承自 ``EpubHtml``，``get_type()`` 返回的正是
  ``ITEM_DOCUMENT``，只按类型过滤会把整份目录文字混进正文。因此必须再按
  「是不是 EpubNav / 文件名是不是 nav.xhtml / properties 里有没有 nav」过滤一次。

写 EPUB 时的取舍：

- 正文按**空行**切段，每段包 ``<p>``，段内换行写成 ``<br/>``。
  这样 ``write_epub`` 写出来的书再被 ``epub_to_text`` 读回来，文本原样一致（可往返）；
  段首尾空白会被规范化（HTML 本来也不保留行首空格）。
- 标题只写进元数据与 ``<head><title>``，**不往正文里塞 <h1>**，
  否则「txt → epub → txt」会凭空多出一行标题，往返就不相等了。
- 单个 XHTML 文件有体积上限，超长文本按段落切成多个章节，避免一个几 MB 的
  xhtml 把阅读器卡死。
"""
from __future__ import annotations

import hashlib
import html as html_mod
import re
from pathlib import Path
from typing import Any

# 需要变成「段落级换行」的标签；br / hr 单独处理
_BLOCK_TAGS = (
    "p", "div", "section", "article", "aside", "main", "header", "footer",
    "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "dl", "dt", "dd",
    "table", "thead", "tbody", "tfoot", "tr", "td", "th", "blockquote", "pre",
    "figure", "figcaption", "form", "fieldset", "address",
)
# 目录类文档：即使被 ebooklib 当成 ITEM_DOCUMENT，也不能当正文
_NAVIGATION_NAMES = {"nav.xhtml", "nav.html", "toc.xhtml", "toc.html", "toc.ncx", "nav.xhtm"}
# 单个章节 XHTML 的段落字符上限
_MAX_CHAPTER_CHARS = 20_000
_PARA_SPLIT_PATTERN = re.compile(r"\n[ \t]*\n+")

# ebooklib 惰性导入缓存：(ebooklib 模块, epub 模块)
_EBOOKLIB: Any = None
_EPUB: Any = None


# ---------------------------------------------------------------- EPUB → 文本

def epub_to_text(source: str | Path) -> str:
    """抽取 EPUB 正文纯文本（按 spine 顺序，段落之间空一行）。

    加密（DRM）/ 损坏的 epub 会抛中文 ``ValueError``，不会漏出 zipfile 的英文异常。
    """
    ebooklib, epub = _load_ebooklib()
    path = Path(source)
    if not path.exists():
        raise ValueError(f"EPUB 文件不存在：{path}")
    if path.stat().st_size <= 0:
        raise ValueError(f"EPUB 文件是空的：{path}")

    try:
        book = epub.read_epub(str(path))
    except Exception as error:  # noqa: BLE001 - ebooklib 会抛 zipfile/KeyError/lxml 等多种异常
        raise ValueError(
            f"EPUB 打开失败（{path.name}）：{error}。"
            "常见原因：文件损坏、不是 EPUB（改名而来）、被 DRM/密码保护，或缺少 META-INF/container.xml。"
        ) from error

    parts: list[str] = []
    for item in _spine_documents(book, ebooklib):
        body = html_to_text(_item_html(item))
        if body:
            parts.append(body)
    text = "\n\n".join(parts).strip()
    if not text:
        raise ValueError(
            f"这个 EPUB 里没有可提取的正文文本（{path.name}）："
            "可能整本都是图片（扫描版）、正文被加密，或者 spine 里没有正文文档。"
        )
    return text


def _spine_documents(book: Any, ebooklib: Any) -> list[Any]:
    """按 spine 顺序取正文文档；spine 为空时退化为 manifest 顺序。"""
    documents: list[Any] = []
    seen: set[str] = set()
    for entry in getattr(book, "spine", []) or []:
        reference = entry[0] if isinstance(entry, (tuple, list)) and entry else entry
        item = book.get_item_with_id(reference) if isinstance(reference, str) else reference
        if item is None or not _is_body_document(item):
            continue
        key = _item_key(item)
        if key in seen:
            continue
        seen.add(key)
        documents.append(item)

    if documents:
        return documents

    # 少数电子书没写/写坏了 spine，只能退回 manifest 顺序；至少保证有正文可读
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        if not _is_body_document(item):
            continue
        key = _item_key(item)
        if key in seen:
            continue
        seen.add(key)
        documents.append(item)
    return documents


def _is_body_document(item: Any) -> bool:
    if item.get_type() != _EBOOKLIB.ITEM_DOCUMENT:
        return False
    return not _is_navigation(item)


def _is_navigation(item: Any) -> bool:
    """目录文档判定：EpubNav 类型 / nav.xhtml 这类文件名 / properties 带 nav。"""
    if isinstance(item, _EPUB.EpubNav):
        return True
    name = (item.get_name() or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    if name in _NAVIGATION_NAMES:
        return True
    properties = getattr(item, "properties", None) or []
    return "nav" in properties


def _item_key(item: Any) -> str:
    return str(item.get_id() or item.get_name() or id(item))


def _item_html(item: Any) -> str:
    """取章节 XHTML 文本；bytes 内容按 UTF-8 解码（解不开的部分替换，不整章丢弃）。"""
    content = item.get_content()
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    return content or ""


def html_to_text(html_text: str) -> str:
    """XHTML → 纯文本：块级标签之间空一行，段内文本保持连续。

    做法是先给每个块级标签前后各插一个 ``\\n\\n`` 文本节点再 ``get_text()``：
    这样 ``<p>Hello <b>world</b></p>`` 仍是「Hello world」（不会被拆成三行），
    而相邻段落之间会真的空一行。最后把空行压成一个空行、去掉行首尾空白。
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError as error:  # pragma: no cover - 依赖缺失分支
        raise ValueError(
            "解析 EPUB 正文需要 beautifulsoup4：请执行 pip install beautifulsoup4 后重试"
        ) from error

    soup = BeautifulSoup(html_text or "", "html.parser")
    for tag in soup.find_all(("script", "style", "title")):
        tag.decompose()
    head = soup.find("head")
    if head is not None:
        head.decompose()
    for br in soup.find_all(("br", "hr")):
        br.replace_with("\n")
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_before("\n\n")
        tag.insert_after("\n\n")
    return _normalize_paragraphs(soup.get_text())


def _normalize_paragraphs(raw: str) -> str:
    """统一换行 + 逐行去空白 + 连续空行压成一个（段落之间恰好空一行）。"""
    unified = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    result: list[str] = []
    for line in unified.split("\n"):
        stripped = line.strip()
        if stripped:
            result.append(stripped)
        elif result and result[-1] != "":
            result.append("")
    while result and result[-1] == "":
        result.pop()
    return "\n".join(result)


# ---------------------------------------------------------------- 文本 → EPUB

def write_epub(text: str, output: str | Path, title: str = "") -> None:
    """纯文本 → 可读 EPUB（UTF-8，段落化，带目录与 NCX）。"""
    ebooklib, epub = _load_ebooklib()
    blocks = _split_paragraphs(text)
    if not blocks:
        raise ValueError("没有可写入 EPUB 的文本内容（源文件是空的）")

    book_title = (title or "").strip() or "未命名文档"
    book = epub.EpubBook()
    # 用内容哈希当书籍 id：同一份文本重复转换得到同一个 id，便于查重与增量处理
    identifier = hashlib.sha1(f"{book_title}\n{text}".encode("utf-8")).hexdigest()[:20]
    book.set_identifier(f"modu-{identifier}")
    book.set_title(book_title)
    book.set_language("zh-CN")
    book.add_author("墨软·工作台")

    chunks = _chunk_paragraphs(blocks)
    chapters: list[Any] = []
    for index, chunk in enumerate(chunks, start=1):
        chapter_title = book_title if len(chunks) == 1 else f"{book_title}（{index}/{len(chunks)}）"
        chapter = epub.EpubHtml(title=chapter_title, file_name=f"text_{index:03d}.xhtml", lang="zh-CN")
        # 只放 body 片段：EbookLib 会用自带 chapter 模板包成完整 XHTML。
        # 坑：如果这里自己拼一份带 `<?xml version="1.0" encoding="utf-8"?>` 的整文档，
        # lxml 解析 unicode + encoding 声明会直接抛错（epub 里的正文会变成空文档），
        # 所以「自己拼整文档」是帮倒忙。
        chapter.content = _body_html(chunk)
        book.add_item(chapter)
        chapters.append(chapter)

    book.toc = tuple(chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    # 'nav' 是 EpubNav 的默认 id；spine 里放对象时 ebooklib 会自己取 idref
    book.spine = ["nav", *chapters]

    try:
        epub.write_epub(str(output), book)
    except Exception as error:  # noqa: BLE001 - 磁盘/权限/编码问题统一转成中文提示
        raise ValueError(f"EPUB 写入失败（{output}）：{error}") from error


def _split_paragraphs(text: str) -> list[str]:
    """文本 → 段落列表：空行分段，段内换行保留。"""
    unified = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not unified.strip():
        return []
    return [block.strip("\n") for block in _PARA_SPLIT_PATTERN.split(unified) if block.strip()]


def _chunk_paragraphs(blocks: list[str]) -> list[list[str]]:
    """按字符预算把段落分组，避免生成一个超大 XHTML。"""
    chunks: list[list[str]] = []
    current: list[str] = []
    size = 0
    for block in blocks:
        if current and size + len(block) > _MAX_CHAPTER_CHARS:
            chunks.append(current)
            current, size = [], 0
        current.append(block)
        size += len(block) + 2
    if current:
        chunks.append(current)
    return chunks


def _body_html(blocks: list[str]) -> str:
    """段落列表 → XHTML body 片段（EbookLib 负责补 html/head/body 骨架）。"""
    return "\n".join(f"<p>{_paragraph_html(block)}</p>" for block in blocks)


def _paragraph_html(block: str) -> str:
    """段落 → HTML：先转义再换行，保证 ``<`` 之类不会被当成标签。"""
    return html_mod.escape(block).replace("\n", "<br/>")


# ---------------------------------------------------------------- 依赖

def _load_ebooklib() -> tuple[Any, Any]:
    """惰性导入 ebooklib 并缓存；缺依赖时给中文安装提示。"""
    global _EBOOKLIB, _EPUB
    if _EPUB is None:
        try:
            import ebooklib
            from ebooklib import epub
        except ImportError as error:  # pragma: no cover - 依赖缺失分支
            raise ValueError(
                "EPUB 读写需要 EbookLib：请执行 pip install EbookLib（或 pip install -r requirements.txt）后重试"
            ) from error
        _EBOOKLIB, _EPUB = ebooklib, epub
    return _EBOOKLIB, _EPUB
