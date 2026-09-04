"""在线小说源:支持搜索、目录、章节阅读、批量下载。

默认源基于"笔趣阁"类站点(免费小说,HTML 模板统一)。
所有 API 都通过 base_url 配置 — 站点经常换域名,用户在 UI 里改就行。

调用流程:
    src = NovelSource(base_url="https://www.biququ.com")
    results = src.search("凡人修仙传")        # → list of NovelInfo
    detail = src.get_catalog(results[0].url)   # → NovelDetail(含全部 chapters)
    text = src.get_chapter(detail.chapters[0].url)  # → str
    src.download_novel(results[0].url, save_dir, on_progress=lambda n, total: ...)
"""
from __future__ import annotations

import re
import time
import html as html_mod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

try:
    from fake_useragent import UserAgent
    _ua = UserAgent()
except Exception:  # noqa: BLE001
    _ua = None


HEADERS_FALLBACK = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _get_headers() -> dict:
    if _ua is not None:
        try:
            ua = _ua.random
        except Exception:  # noqa: BLE001
            ua = HEADERS_FALLBACK["User-Agent"]
    else:
        ua = HEADERS_FALLBACK["User-Agent"]
    return {**HEADERS_FALLBACK, "User-Agent": ua}


@dataclass
class NovelInfo:
    """搜索结果里的一项。"""
    title: str
    author: str = ""
    url: str = ""
    latest_chapter: str = ""
    description: str = ""


@dataclass
class ChapterInfo:
    """目录里的一个章节。"""
    title: str
    url: str
    index: int = 0


@dataclass
class NovelDetail:
    """一本小说的完整目录。"""
    title: str
    author: str
    description: str
    url: str
    chapters: List[ChapterInfo] = field(default_factory=list)
    # 备用:00shu.la 风格的"整本 TXT 直链" — 非空时,UI 应显示"整本下载"按钮
    download_url: str = ""
    # 备用:站点的"整本下载页"URL(00shu 的 /txt/{id}/ 这种)
    txt_page_url: str = ""


@dataclass
class DownloadProgress:
    current: int = 0
    total: int = 0
    current_title: str = ""
    failed: int = 0
    done: bool = False


class NetworkError(RuntimeError):
    """网络请求错误。"""


# 通用选择器(尽量覆盖主流模板)
_CANDIDATE_TITLE_SELECTORS = [
    "h1", "h2.title", "div.bookname h1", "div.info h1",
    ".book-info h1", "#info h1",
]
_CANDIDATE_CONTENT_SELECTORS = [
    "div#content", "div.content", "div#chapter_content",
    "div.read-content", "div.showtxt", "div#chapter-content",
    "article", "div.txt", "div.book_content",
]


class NovelSource:
    """通用小说源 — 通过 base_url 配置。"""

    def __init__(self, base_url: str, timeout: float = 15.0):
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(_get_headers())

    # ---------- HTTP ----------
    def _get(self, url: str) -> str:
        try:
            r = self._session.get(url, timeout=self.timeout)
        except requests.RequestException as e:
            raise NetworkError(f"请求失败:{url}\n{e}") from e
        r.encoding = self._detect_encoding(r)
        if r.status_code >= 400:
            raise NetworkError(f"HTTP {r.status_code}: {url}")
        return r.text

    @staticmethod
    def _detect_encoding(r: requests.Response) -> str:
        # 优先 GBK(中文小说网多),再 utf-8
        enc = (r.apparent_encoding or "").lower()
        if "gb" in enc or "gbk" in enc:
            return "gbk"
        if "utf-8" in enc or "utf8" in enc:
            return "utf-8"
        # 看 content 头部
        ct = r.headers.get("Content-Type", "")
        if "gbk" in ct.lower():
            return "gbk"
        # 试一下 GBK(BOM / high-byte 模式)
        b = r.content[:4096]
        if b.startswith(b"\xef\xbb\xbf"):
            return "utf-8-sig"
        # 简单启发:多数中文小说站用 gb18030
        return "gb18030"

    def _make_url(self, rel: str) -> str:
        if not rel:
            return self.base_url
        if rel.startswith(("http://", "https://")):
            return rel
        return urljoin(self.base_url + "/", rel.lstrip("/"))

    # ---------- 搜索 ----------
    def search(self, keyword: str) -> List[NovelInfo]:
        """搜索。

        通用策略:先尝试 base_url/search.htm?keyword=xxx,
        解析所有 <a> 链接里"看起来像小说"的项。
        """
        if not keyword.strip():
            return []
        # 尝试多个常见搜索 URL
        results: List[NovelInfo] = []
        tried = 0
        for url in self._search_urls(keyword):
            tried += 1
            try:
                html = self._get(url)
            except NetworkError:
                continue
            try:
                items = self._parse_search(html)
            except Exception:  # noqa: BLE001
                items = []
            if items:
                results = items
                break
        return results

    def _search_urls(self, keyword: str) -> List[str]:
        kw = requests.utils.quote(keyword)
        # 兼容多个常见搜索 endpoint,第一个能解析的就用
        return [
            f"{self.base_url}/search.htm?keyword={kw}",
            f"{self.base_url}/search?q={kw}",
            f"{self.base_url}/s/?q={kw}",
            f"{self.base_url}/search.html?keyword={kw}",
            f"{self.base_url}/search/{kw}",
            f"{self.base_url}/search?keyword={kw}",
            f"{self.base_url}/modules/article/search.php?searchkey={kw}",
        ]

    def _parse_search(self, html: str) -> List[NovelInfo]:
        """通用解析:扫所有指向站点本域的链接,提取像小说的项。"""
        soup = BeautifulSoup(html, "lxml")
        results: List[NovelInfo] = []
        seen_titles: set[str] = set()
        for a in soup.find_all("a"):
            href = a.get("href", "")
            text = a.get_text(strip=True)
            if not href or not text:
                continue
            # 必须是站内链接
            if not (href.startswith("/") or self.base_url in href):
                continue
            # 必须看起来像小说标题(>= 4 个汉字)
            clean = re.sub(r"\s+", "", text)
            if len(clean) < 4 or len(clean) > 50:
                continue
            if not re.search(r"[\u4e00-\u9fff]", clean):
                continue
            # 排除导航/分类链接
            if any(k in text for k in [
                "首页", "登录", "注册", "搜索", "书架", "分类", "玄幻", "都市", "修真",
                "言情", "科幻", "历史", "军事", "更多", "全部", "完本", "排行榜",
            ]):
                continue
            # 必须指向 /book/xxx 或 /novel/xxx(很多笔趣阁类站点约定)
            if not re.search(r"/(book|novel|books|read)/\d+", href):
                continue
            title = clean
            if title in seen_titles:
                continue
            seen_titles.add(title)

            # 尝试拿作者(看父/兄弟节点)
            author = ""
            parent_text = a.parent.get_text(" ", strip=True) if a.parent else ""
            m = re.search(r"作者[:：\s]*([^\s/]+)", parent_text)
            if m and m.group(1) != title:
                author = m.group(1).strip()

            # 最新章节(下一行常有)
            next_text = ""
            nxt = a.find_next("a") or a.find_next("span")
            if nxt:
                next_text = nxt.get_text(strip=True)

            results.append(NovelInfo(
                title=title, author=author,
                url=self._make_url(href), latest_chapter=next_text,
            ))
        return results[:30]

    # ---------- 目录 ----------
    def get_catalog(self, novel_url: str) -> NovelDetail:
        """取小说目录:标题 + 作者 + 全部章节。

        智能识别:
        - 00shu.la 的 /txt/{id}/  → 整本 TXT 下载页(无章节,提供 download_url)
        - 常规目录页 → 解析章节列表
        """
        # ---------- 00shu.la 整本下载页(/txt/{id}/)特殊处理 ----------
        m = re.match(
            r"^(https?://[^/]+)/txt/(\d+)/?$",
            novel_url.rstrip("/") + "/",
        )
        if m:
            return self._parse_00shu_download_page(m.group(1), m.group(2), novel_url)

        html = self._get(novel_url)
        soup = BeautifulSoup(html, "lxml")  # type: ignore

        detail = NovelDetail(
            title="", author="", description="", url=novel_url,
        )
        # 标题
        for sel in _CANDIDATE_TITLE_SELECTORS:
            el = soup.select_one(sel)
            if el and el.get_text(strip=True):
                detail.title = self._clean_text(el.get_text(" ", strip=True))
                break
        # 作者(可能在 meta)
        meta_author = soup.find("meta", attrs={"name": "author"})
        if meta_author and meta_author.get("content"):
            detail.author = meta_author["content"].strip()
        # 兜底:从正文找"作者: xxx"
        if not detail.author:
            text = soup.get_text("\n", strip=True)
            m = re.search(r"作\s*者[:：]\s*([^\s\n]+)", text)
            if m:
                detail.author = m.group(1).strip()
        # 描述
        for sel in ["div.intro", "p.intro", "div.description", "#bookintro", "div.summary"]:
            el = soup.select_one(sel)
            if el and el.get_text(strip=True):
                detail.description = self._clean_text(el.get_text(" ", strip=True))
                break

        # 章节列表:找所有 /book/xxx/NUMBER.html 形式的链接
        seen: set[str] = set()
        chapters: List[ChapterInfo] = []
        # 一些站把章节放在 #list dl dd a / #chapterlist dd a
        for wrap_sel in ["#list", "#chapterlist", "div.list", "div.chapterlist", "dl"]:
            wrap = soup.select(wrap_sel)
            for w in wrap:
                for a in w.find_all("a"):
                    href = a.get("href", "")
                    text = a.get_text(strip=True)
                    if not href or not text:
                        continue
                    if not (href.startswith("/") or self.base_url in href):
                        continue
                    full = self._make_url(href)
                    if full in seen:
                        continue
                    seen.add(full)
                    chapters.append(ChapterInfo(
                        title=self._clean_text(text), url=full, index=len(chapters),
                    ))
        # 兜底:全文档所有指向 /book/NUMBER.html 的 <a>
        if not chapters:
            for a in soup.find_all("a"):
                href = a.get("href", "")
                text = a.get_text(strip=True)
                if not href or not text:
                    continue
                if not re.search(r"/\d+\.html?$|/read/\d+/\d+", href):
                    continue
                full = self._make_url(href)
                if full in seen:
                    continue
                if len(text) > 60 or len(text) < 2:
                    continue
                seen.add(full)
                chapters.append(ChapterInfo(
                    title=self._clean_text(text), url=full, index=len(chapters),
                ))

        detail.chapters = chapters
        if not detail.title:
            detail.title = soup.title.get_text(strip=True) if soup.title else "未知书名"
        return detail

    # ---------- 章节 ----------
    def get_chapter(self, chapter_url: str) -> tuple[str, str]:
        """返回 (章节标题, 纯文本内容)。"""
        html = self._get(chapter_url)
        soup = BeautifulSoup(html, "lxml")
        # 标题
        title = ""
        for sel in ["h1", ".title", ".chapter_title", "div.bookname h1", "h2"]:
            el = soup.select_one(sel)
            if el:
                t = el.get_text(strip=True)
                if t and 2 <= len(t) <= 80:
                    title = self._clean_text(t)
                    break
        if not title and soup.title:
            title = self._clean_text(soup.title.get_text(strip=True))
        # 内容
        content_el = None
        for sel in _CANDIDATE_CONTENT_SELECTORS:
            el = soup.select_one(sel)
            if el and el.get_text(strip=True):
                content_el = el
                break
        if content_el is None:
            content_el = soup.body or soup
        raw = content_el.get_text("\n", strip=True)
        raw = self._clean_content(raw)
        return title, raw

    # ---------- 下载 ----------
    def download_novel(
        self,
        novel_url: str,
        save_dir: str | Path,
        on_progress: Optional[Callable[[DownloadProgress], None]] = None,
        max_chapters: int = 5000,
        min_delay_sec: float = 0.05,
    ) -> Path:
        """下载整本小说为 txt。"""
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        # 拿目录
        detail = self.get_catalog(novel_url)
        total = min(len(detail.chapters), max_chapters)
        prog = DownloadProgress(current=0, total=total)
        if on_progress:
            on_progress(prog)

        # 输出文件名
        safe_title = re.sub(r"[\\/:*?\"<>|]", "_", detail.title).strip() or "novel"
        out_path = save_dir / f"{safe_title}.txt"

        try:
            with open(out_path, "w", encoding="utf-8") as fp:
                fp.write(f"{detail.title}\n\n")
                if detail.author:
                    fp.write(f"作者: {detail.author}\n\n")
                if detail.description:
                    fp.write(f"简介: {detail.description}\n\n")
                fp.write(f"共 {total} 章 · 来源: {self.base_url}\n")
                fp.write("=" * 60 + "\n\n")
                for i, ch in enumerate(detail.chapters[:total], start=1):
                    prog.current = i
                    prog.current_title = ch.title
                    if on_progress:
                        on_progress(prog)
                    try:
                        title, body = self.get_chapter(ch.url)
                        if not title:
                            title = ch.title
                        fp.write(f"\n\n\n第 {i:04d} 章 {title}\n")
                        fp.write("-" * 40 + "\n")
                        fp.write(body)
                        fp.write("\n")
                    except Exception as e:  # noqa: BLE001
                        prog.failed += 1
                        fp.write(f"\n\n\n第 {i:04d} 章 {ch.title}\n")
                        fp.write(f"[下载失败: {e}]\n")
                    # 礼貌 delay,避免被封
                    if min_delay_sec > 0:
                        time.sleep(min_delay_sec)
        finally:
            prog.done = True
            if on_progress:
                on_progress(prog)
        return out_path

    # ---------- helpers ----------
    @staticmethod
    def _clean_text(s: str) -> str:
        s = re.sub(r"\s+", " ", s).strip()
        return html_mod.unescape(s)

    @staticmethod
    def _clean_content(raw: str) -> str:
        """清理章节正文:去广告/页脚/重复空行。"""
        lines = [ln.rstrip() for ln in raw.splitlines()]
        out: List[str] = []
        prev_blank = False
        for line in lines:
            stripped = line.strip()
            if not stripped:
                if prev_blank:
                    continue
                prev_blank = True
                out.append("")
                continue
            prev_blank = False
            # 明显的非正文行
            if any(skip in stripped for skip in [
                "本章未完", "请收藏", "下一页", "上一章", "返回目录",
                "加入书签", "本章错误", "本章未完请点击",
                "本章完", "看最快更新", "天才一秒记住",
                "首发网址", "本文章节", "温馨提示",
            ]):
                continue
            out.append(stripped)
        return "\n".join(out).strip()

    # ---------- 00shu.la 整本下载页 ----------
    def _parse_00shu_download_page(self, base: str, book_id: str, page_url: str) -> NovelDetail:
        """解析 00shu.la 的 /txt/{id}/ 整本下载页。

        返回的 NovelDetail:
        - chapters: 空列表(无章节列表可解析)
        - download_url: 整本直链(可直接下载)
        - title/author/description: 从 /txt/{id}/ 页面提取(只 1 次网络请求)
        """
        download_url = f"https://down.00shu.la/modules/article/txtarticle.php?id={book_id}"
        detail = NovelDetail(
            title="", author="", description="",
            url=page_url, chapters=[],
            download_url=download_url,
            txt_page_url=page_url,
        )
        try:
            html = self._get(page_url)
            soup = BeautifulSoup(html, "lxml")
            # title: <h1>吞噬星空2起源大陆TXT下载</h1>
            for sel in ["h1", "title"]:
                el = soup.select_one(sel)
                if el:
                    t = el.get_text(" ", strip=True)
                    if t:
                        t = re.sub(r"TXT(全集)?下载", "", t)
                        t = re.sub(r"_\d+小说网?$", "", t)
                        t = re.sub(r"\s+", " ", t).strip()
                        if t:
                            detail.title = t
                            break
            # 描述(优先 og:description,其次 meta description)
            for sel in [
                "meta[property='og:description']",
                "meta[name='description']",
                "meta[name='keywords']",
            ]:
                el = soup.select_one(sel)
                if el and el.get("content"):
                    txt = el["content"].strip()
                    # keywords 经常是"X小说下载,X小说TXT",清理一下
                    txt = re.sub(r"TXT(全集)?下载", "", txt)
                    txt = re.sub(r"下载分享推荐给你的朋友!?", "", txt)
                    txt = re.sub(r"\s+", " ", txt).strip()
                    if txt and len(txt) > 4:
                        detail.description = txt
                        break
            # 作者:从下载页一般没明确作者(00shu 不显示),用书名兜底
            if not detail.author:
                detail.author = ""  # 显示"作者未知"即可
        except Exception:  # noqa: BLE001
            if not detail.title:
                detail.title = f"未知书名({book_id})"
        return detail

    def download_full_txt(self, download_url: str, save_path: str | Path,
                          on_progress: Optional[Callable[[int, int], None]] = None) -> Path:
        """直接下载 00shu 的整本 txt(走 download_url 直链)。"""
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with self._session.get(download_url, timeout=120, stream=True) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length", 0))
            written = 0
            with open(save_path, "wb") as fp:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    fp.write(chunk)
                    written += len(chunk)
                    if on_progress:
                        on_progress(written, total)
        return save_path
