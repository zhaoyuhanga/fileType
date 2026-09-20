"""墨软文档：PDF 工具箱（拆分 / 合并 / 压缩 / 加密 / 解密 / 旋转 / 抽取 / 水印 / 表单）。

需求（6.2）明确点名了"PDF 拆分、合并、压缩、水印、加密、表单填写"，
所以这里是**真实现**而不是占位：

- 全部基于 `pypdf`（随包依赖），离线可用，不联网、不上传；
- 水印用 Qt 生成一页与目标页**同尺寸**的覆盖层再 `merge_page`，
  因此不会被缩放拉花（pypdf 的 merge 不会自动缩放，尺寸不一致就会错位）；
- 加密使用 `pypdf.constants.UserAccessPermissions` 位标志控制"允许打印/允许复制"，
  不同 pypdf 版本常量名有差异，统一用 getattr 兜底；
- 表单填写会同时设置 `/NeedAppearances`，否则部分阅读器不刷新显示；
- 每个函数都返回产物路径，失败抛 `PdfError`（中文原因，可直接展示）。

说明：文档板块的 PDF **文本层**解析走 `parser.py`；这里只做"页面级"操作。
"""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

#: A4 尺寸（点）—— 新建覆盖层时的默认页面大小
A4_WIDTH_PT = 595.0
A4_HEIGHT_PT = 842.0


class PdfError(ValueError):
    """PDF 操作失败（消息可直接展示给用户）。"""


def _require_pypdf():  # noqa: ANN202
    try:
        import pypdf
    except ImportError as error:  # pragma: no cover - pypdf 是随包依赖
        raise PdfError("缺少 pypdf 依赖，无法处理 PDF") from error
    return pypdf


@dataclass
class PdfInfo:
    """一份 PDF 的概况（界面与工具都用它）。"""

    path: str = ""
    pages: int = 0
    encrypted: bool = False
    size_bytes: int = 0
    title: str = ""
    author: str = ""
    producer: str = ""
    page_size: tuple[float, float] = (0.0, 0.0)
    form_fields: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.pages} 页", f"{self.size_bytes / 1024:.0f} KB"]
        if self.page_size[0]:
            parts.append(f"{self.page_size[0]:.0f}×{self.page_size[1]:.0f} pt")
        if self.encrypted:
            parts.append("已加密")
        if self.form_fields:
            parts.append(f"{len(self.form_fields)} 个表单域")
        if self.title:
            parts.append(f"标题：{self.title}")
        return " · ".join(parts)


# ---------------------------------------------------------------- 读取


def _open_reader(path: str | Path, password: str = "", *,
                 require_decrypted: bool = True):  # noqa: ANN202
    """打开 PDF。

    `require_decrypted=False` 用于"只看概况"的场景：加密文件也能报出
    "已加密 + 页数"，此时任何需要解密的字段都要由调用方自行容错。
    """
    pypdf = _require_pypdf()
    source = Path(path)
    if not source.is_file():
        raise PdfError(f"文件不存在：{source}")
    try:
        reader = pypdf.PdfReader(str(source))
    except Exception as error:  # noqa: BLE001
        raise PdfError(f"无法读取 PDF：{error}") from error
    if reader.is_encrypted:
        if password:
            try:
                status = reader.decrypt(password)
            except Exception as error:  # noqa: BLE001
                raise PdfError(f"无法解密：{error}") from error
            # 注意：pypdf 在密码错误时**不抛异常**，而是返回 0/NOT_DECRYPTED，
            # 不检查返回值的话后续操作会以 FileNotDecryptedError 的形式在别处炸开
            if not status:
                raise PdfError("密码不正确：无法解密这份 PDF")
        elif require_decrypted:
            raise PdfError("这份 PDF 已加密：请先填写密码（或用「解密」功能另存为不加密的副本）")
    return reader


def _writer_from(reader) -> object:  # noqa: ANN001
    """把读者内容搬进一个新的 writer。

    优先用 `PdfWriter(clone_from=...)`：它会把页面真正"挂到"新 writer 上，
    否则 `page.compress_content_streams()` 这类操作会触发 pypdf 的弃用警告
    （"pages not assigned to a writer"），未来版本会直接失效。
    """
    pypdf = _require_pypdf()
    try:
        return pypdf.PdfWriter(clone_from=reader)
    except Exception:  # noqa: BLE001  老版本没有 clone_from
        writer = pypdf.PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        return writer


def _save(writer, output: str | Path) -> Path:  # noqa: ANN001
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(destination, "wb") as handle:
            writer.write(handle)
    except Exception as error:  # noqa: BLE001
        raise PdfError(f"写出 PDF 失败：{error}") from error
    return destination


def pdf_info(path: str | Path, *, password: str = "") -> PdfInfo:
    """读取 PDF 概况（页数/尺寸/元数据/表单域数量）。

    加密文件在没有密码时也能报出"已加密 + 页数"，不会因为读不了元数据就整个失败。
    """
    source = Path(path)
    info = PdfInfo(path=str(source))
    try:
        info.size_bytes = source.stat().st_size
    except OSError:
        info.size_bytes = 0
    reader = _open_reader(source, password, require_decrypted=False)
    info.encrypted = bool(reader.is_encrypted)
    try:
        info.pages = len(reader.pages)
    except Exception:  # noqa: BLE001  未解密的加密文件可能读不出页数
        info.pages = 0
    if info.encrypted and not password:
        return info
    try:
        metadata = reader.metadata or {}
        info.title = str(metadata.get("/Title") or "")
        info.author = str(metadata.get("/Author") or "")
        info.producer = str(metadata.get("/Producer") or "")
    except Exception:  # noqa: BLE001
        pass
    if info.pages:
        try:
            box = reader.pages[0].mediabox
            info.page_size = (float(box.width), float(box.height))
        except Exception:  # noqa: BLE001
            info.page_size = (A4_WIDTH_PT, A4_HEIGHT_PT)
    info.form_fields = list_form_fields(source, password=password)
    return info


# ---------------------------------------------------------------- 页码解析


def parse_pages(spec: str, total: int) -> list[int]:
    """解析页码范围：`1-3,5,8-` → [0,1,2,4,7,...]（0 基，自动去重排序）。"""
    if total <= 0:
        return []
    text = (spec or "").strip()
    if not text:
        return list(range(total))
    pages: set[int] = set()
    for chunk in re.split(r"[,，、;；\s]+", text):
        if not chunk:
            continue
        if "-" in chunk:
            start_text, _, end_text = chunk.partition("-")
            try:
                start = int(start_text) if start_text.strip() else 1
                end = int(end_text) if end_text.strip() else total
            except ValueError as error:
                raise PdfError(f"页码范围无法识别：{chunk}（示例：1-3,5,8-）") from error
            if start < 1 or end < start:
                raise PdfError(f"页码范围不合理：{chunk}（页码从 1 开始，且左小于右）")
            pages.update(range(start - 1, min(end, total)))
            continue
        try:
            index = int(chunk)
        except ValueError as error:
            raise PdfError(f"页码范围无法识别：{chunk}（示例：1-3,5,8-）") from error
        if index < 1 or index > total:
            raise PdfError(f"页码超出范围：{index}（这份 PDF 共 {total} 页）")
        pages.add(index - 1)
    if not pages:
        raise PdfError(f"没有解析出任何页码：{spec}")
    return sorted(pages)


# ---------------------------------------------------------------- 合并 / 拆分


def merge_pdfs(paths: Sequence[str | Path], output: str | Path, *,
               bookmarks: bool = True, password: str = "") -> Path:
    """把多个 PDF 合成一份（可选按文件名加书签）。"""
    files = [Path(item) for item in paths if str(item).strip()]
    if len(files) < 2:
        raise PdfError("合并至少需要两个 PDF 文件")
    pypdf = _require_pypdf()
    writer = pypdf.PdfWriter()
    for source in files:
        reader = _open_reader(source, password)
        start = len(writer.pages)
        for page in reader.pages:
            writer.add_page(page)
        if bookmarks:
            try:
                writer.add_outline_item(source.stem, start)
            except Exception:  # noqa: BLE001  书签失败不影响合并
                pass
    return _save(writer, output)


def split_pdf(path: str | Path, output_dir: str | Path, *, mode: str = "each",
              ranges: str = "", every: int = 1, prefix: str = "",
              password: str = "") -> list[Path]:
    """拆分 PDF。

    - `mode="each"`：每页一个文件；
    - `mode="every"`：每 `every` 页一组；
    - `mode="ranges"`：按 `ranges`（如 `1-3,5,8-`）把**指定的连续段**各存一份。
    """
    source = Path(path)
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    pypdf = _require_pypdf()
    reader = _open_reader(source, password)
    total = len(reader.pages)
    stem = prefix or source.stem
    produced: list[Path] = []

    if mode == "ranges":
        segments = _parse_segments(ranges, total)
        for index, group in enumerate(segments, start=1):
            writer = pypdf.PdfWriter()
            for page_index in group:
                writer.add_page(reader.pages[page_index])
            label = _segment_label(group)
            produced.append(_save(writer, folder / f"{stem}-{label}.pdf"))
        return produced

    size = max(1, int(every)) if mode == "every" else 1
    for start in range(0, total, size):
        writer = pypdf.PdfWriter()
        for page_index in range(start, min(start + size, total)):
            writer.add_page(reader.pages[page_index])
        if size == 1:
            name = f"{stem}-第{start + 1}页.pdf"
        else:
            name = f"{stem}-第{start + 1}至{min(start + size, total)}页.pdf"
        produced.append(_save(writer, folder / name))
    return produced


def extract_pages(path: str | Path, output: str | Path, *, pages: str = "",
                  password: str = "") -> Path:
    """抽取指定页另存为新的 PDF（`pages` 为空 = 全部页面）。"""
    pypdf = _require_pypdf()
    reader = _open_reader(path, password)
    wanted = parse_pages(pages, len(reader.pages))
    writer = pypdf.PdfWriter()
    for index in wanted:
        writer.add_page(reader.pages[index])
    return _save(writer, output)


def _parse_segments(spec: str, total: int) -> list[list[int]]:
    """把 `1-3,5,8-` 解析成"连续段"列表（每段成为一个文件）。"""
    segments: list[list[int]] = []
    text = (spec or "").strip()
    if not text:
        raise PdfError("按段拆分需要填写页码段，例如 1-3,5,8-")
    for chunk in re.split(r"[,，、;；]+", text):
        if not chunk.strip():
            continue
        segments.append(parse_pages(chunk, total))
    return segments


def _segment_label(group: Sequence[int]) -> str:
    if len(group) == 1:
        return f"第{group[0] + 1}页"
    return f"第{group[0] + 1}至{group[-1] + 1}页"


# ---------------------------------------------------------------- 压缩 / 旋转


def compress_pdf(path: str | Path, output: str | Path, *, level: str = "medium",
                 password: str = "") -> Path:
    """压缩 PDF：重写内容流 + 去重相同对象 + 清理元数据。

    `level`：`light`（只重写内容流）/ `medium`（+去重）/ `strong`（+清元数据与书签）。
    """
    reader = _open_reader(path, password)
    writer = _writer_from(reader)
    for page in writer.pages:  # type: ignore[attr-defined]
        try:
            page.compress_content_streams()
        except Exception:  # noqa: BLE001  个别页面压缩失败不影响整体
            continue
    if level in ("medium", "strong"):
        # pypdf 7 把参数改名（remove_orphans→remove_unreferenced、remove_identicals→remove_duplicates），
        # 这里按"新名字 → 旧名字"依次尝试，两个大版本都能用
        for kwargs in ({"remove_duplicates": True, "remove_unreferenced": True},
                       {"remove_identicals": True, "remove_orphans": True}):
            try:
                writer.compress_identical_objects(**kwargs)  # type: ignore[attr-defined]
                break
            except TypeError:
                continue
            except Exception:  # noqa: BLE001  个别版本没有这个方法
                break
    if level == "strong":
        try:
            writer.metadata = None  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    return _save(writer, output)


def rotate_pdf(path: str | Path, output: str | Path, *, angle: int = 90,
               pages: str = "", password: str = "") -> Path:
    """旋转页面（90 的倍数）；`pages` 为空 = 全部页面。"""
    if angle % 90 != 0:
        raise PdfError("旋转角度必须是 90 的倍数（90 / 180 / 270）")
    reader = _open_reader(path, password)
    writer = _writer_from(reader)
    wanted = set(parse_pages(pages, len(reader.pages)))
    for index, page in enumerate(writer.pages):  # type: ignore[attr-defined]
        if index in wanted:
            try:
                page.rotate(angle)
            except Exception:  # noqa: BLE001
                page.rotate(-360 + angle % 360)
    return _save(writer, output)


# ---------------------------------------------------------------- 加密 / 解密


def _permission_flags(*, allow_printing: bool, allow_copying: bool, allow_modifying: bool):  # noqa: ANN202
    """构造 pypdf 的权限位（不同版本常量名不同，用 getattr 兜底）。"""
    from pypdf.constants import UserAccessPermissions as UAP

    flags = getattr(UAP, "PRINT", None)
    printing = getattr(UAP, "PRINT_TO_REPRESENTATION", None)
    copying = getattr(UAP, "EXTRACT", None) or getattr(UAP, "EXTRACT_TEXT_AND_GRAPHICS", None)
    modifying = getattr(UAP, "MODIFY", None)
    value = 0
    if allow_printing:
        for item in (flags, printing):
            if item is not None:
                value |= int(item)
    if allow_copying and copying is not None:
        value |= int(copying)
    if allow_modifying and modifying is not None:
        value |= int(modifying)
    return value


def encrypt_pdf(path: str | Path, output: str | Path, *, user_password: str,
                owner_password: str = "", allow_printing: bool = True,
                allow_copying: bool = False, allow_modifying: bool = False,
                password: str = "") -> Path:
    """加密 PDF（AES-128/RC4 由 pypdf 决定；权限位控制打印/复制/修改）。"""
    if not user_password.strip():
        raise PdfError("请设置打开密码（为空等于没加密）")
    reader = _open_reader(path, password)
    writer = _writer_from(reader)
    try:
        writer.encrypt(  # type: ignore[attr-defined]
            user_password=user_password,
            owner_password=owner_password or user_password,
            permissions_flag=_permission_flags(
                allow_printing=allow_printing, allow_copying=allow_copying,
                allow_modifying=allow_modifying),
        )
    except TypeError:
        # 老版本签名只有位置参数
        writer.encrypt(user_password, owner_password or user_password)  # type: ignore[attr-defined]
    return _save(writer, output)


def decrypt_pdf(path: str | Path, output: str | Path, *, password: str) -> Path:
    """解密另存为不加密副本（不知道密码会明确报错）。"""
    if not password.strip():
        raise PdfError("请输入这份 PDF 的打开密码")
    reader = _open_reader(path, password)
    writer = _writer_from(reader)
    return _save(writer, output)


# ---------------------------------------------------------------- 水印


def watermark_pdf(path: str | Path, output: str | Path, *, text: str, footer: str = "",
                  tracking_id: str = "", password: str = "") -> Path:
    """给每一页加浅灰水印文字与页脚（覆盖层与页面同尺寸，不会被缩放拉花）。"""
    line = " · ".join(item for item in (text.strip(), footer.strip(),
                                        f"标识：{tracking_id.strip()}" if tracking_id.strip() else "")
                      if item)
    if not line:
        raise PdfError("请填写水印文字或页脚内容")
    pypdf = _require_pypdf()
    reader = _open_reader(path, password)
    # 先把页面挂到新 writer 上再合并覆盖层：直接改 reader 的页面会触发
    # pypdf 的 "pages not assigned to a writer" 弃用警告（且被官方标记为不可靠）
    writer = _writer_from(reader)
    cache: dict[tuple[float, float], object] = {}
    with tempfile.TemporaryDirectory(prefix="modu-pdf-wm-") as folder:
        for page in writer.pages:  # type: ignore[attr-defined]
            width, height = _page_size(page)
            key = (round(width, 1), round(height, 1))
            overlay_path = cache.get(key)
            if overlay_path is None:
                overlay_path = Path(folder) / f"overlay-{len(cache)}.pdf"
                render_overlay_pdf(line, overlay_path, width=width, height=height)
                cache[key] = overlay_path
            overlay_reader = pypdf.PdfReader(str(overlay_path))
            page.merge_page(overlay_reader.pages[0], over=True)
        return _save(writer, output)


def _page_size(page) -> tuple[float, float]:  # noqa: ANN001
    try:
        box = page.mediabox
        return float(box.width), float(box.height)
    except Exception:  # noqa: BLE001
        return A4_WIDTH_PT, A4_HEIGHT_PT


def render_overlay_pdf(line: str, output: str | Path, *, width: float, height: float) -> Path:
    """用 Qt 生成一页"水印覆盖层"PDF（同尺寸，居中浅灰文字 + 底部页脚）。"""
    from PySide6.QtCore import QCoreApplication, QSizeF
    from PySide6.QtGui import QFont, QPageSize, QTextDocument
    from PySide6.QtPrintSupport import QPrinter

    if QCoreApplication.instance() is None:
        raise PdfError("生成 PDF 水印需要先创建 QApplication（请在图形界面进程内调用）")
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(destination))
    printer.setPageSize(QPageSize(QSizeF(max(72.0, width), max(72.0, height)),
                                  QPageSize.Unit.Point, "custom", QPageSize.SizeMatchPolicy.ExactMatch))
    document = QTextDocument()
    document.setDefaultFont(QFont("Microsoft YaHei", 14))
    font_size = max(12, min(int(height / 22), 40))
    escaped = _escape(line)
    document.setHtml(
        "<div style='text-align:center;margin-top:%dpt;color:#c9cede;font-size:%dpt;"
        "font-weight:bold'>%s</div>"
        "<div style='text-align:center;color:#b9c0d2;font-size:10pt'>%s</div>"
        % (int(height / 2.6), font_size, escaped, escaped))
    document.print_(printer)
    return destination


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# ---------------------------------------------------------------- 表单


def list_form_fields(path: str | Path, *, password: str = "") -> list[dict]:
    """列出 AcroForm 表单域（名称/类型/当前值/可选项）。"""
    reader = _open_reader(path, password)
    try:
        fields = reader.get_fields() or {}
    except Exception:  # noqa: BLE001  没有表单或结构异常
        return []
    result: list[dict] = []
    for name, field in fields.items():
        entry = {"name": str(name)}
        try:
            entry["type"] = str(field.get("/FT") or "")
            entry["value"] = str(field.get("/V") or "")
            states = field.get("/_States_")
            if states:
                entry["options"] = [str(item) for item in states]
        except Exception:  # noqa: BLE001
            pass
        result.append(entry)
    return result


def fill_form(path: str | Path, output: str | Path, values: dict, *,
              password: str = "", flatten: bool = False) -> Path:
    """填写 PDF 表单字段（`values` = {字段名: 值}）。"""
    if not values:
        raise PdfError("没有要填写的字段")
    pypdf = _require_pypdf()
    reader = _open_reader(path, password)
    fields = {str(item.get("name")) for item in list_form_fields(path, password=password)}
    if not fields:
        raise PdfError("这份 PDF 没有可填写的表单域（AcroForm）")
    unknown = [name for name in values if name not in fields]
    if unknown and len(unknown) == len(values):
        raise PdfError("字段名都不匹配，可用字段：" + "、".join(sorted(fields)[:10]))
    writer = pypdf.PdfWriter(clone_from=reader)
    for page in writer.pages:
        try:
            writer.update_page_form_field_values(page, values, auto_regenerate=False)
        except Exception:  # noqa: BLE001  字段不一定在每一页上
            continue
    try:
        from pypdf.generic import BooleanObject, NameObject

        root = writer._root_object  # noqa: SLF001
        if "/AcroForm" in root:
            root["/AcroForm"][NameObject("/NeedAppearances")] = BooleanObject(True)
    except Exception:  # noqa: BLE001  个别版本结构不同，忽略
        pass
    if flatten:
        for page in writer.pages:
            try:
                page.flatten()
            except Exception:  # noqa: BLE001
                continue
    return _save(writer, output)


# ---------------------------------------------------------------- 批注


def list_annotations(path: str | Path, *, password: str = "") -> list[dict]:
    """列出 PDF 里已有的批注（`/Annots` 里的文字类标注）。"""
    reader = _open_reader(path, password)
    result: list[dict] = []
    for page_index, page in enumerate(reader.pages, start=1):
        try:
            annotations = page.get("/Annots") or []
        except Exception:  # noqa: BLE001
            continue
        for item in annotations:
            try:
                obj = item.get_object()
                subtype = str(obj.get("/Subtype") or "")
                if subtype not in ("/Text", "/FreeText", "/Highlight", "/Popup"):
                    continue
                text = str(obj.get("/Contents") or obj.get("/T") or "").strip()
                if not text:
                    continue
                result.append({
                    "page": page_index,
                    "type": subtype.lstrip("/"),
                    "text": text,
                    "author": str(obj.get("/T") or ""),
                })
            except Exception:  # noqa: BLE001  单条损坏不影响其余
                continue
    return result


def annotate_pdf(path: str | Path, output: str | Path,
                 notes: Sequence[dict | tuple], *, password: str = "",
                 author: str = "墨软文档") -> Path:
    """给 PDF 加**真实批注**（`pypdf.annotations.FreeText`）。

    `notes` 支持两种写法：`{"page": 1, "text": "..."}` 或 `(页码, 文字)`（页码从 1 开始）。
    同一页有多条时按顺序向下排开，避免叠在一起。
    """
    items: list[tuple[int, str]] = []
    for note in notes:
        if isinstance(note, dict):
            page = int(note.get("page") or 1)
            text = str(note.get("text") or "").strip()
        else:
            page, text = int(note[0]), str(note[1]).strip()
        if text:
            items.append((page, text))
    if not items:
        raise PdfError("没有要写入的批注（请提供页面与文字）")

    from pypdf.annotations import FreeText

    reader = _open_reader(path, password)
    writer = _writer_from(reader)
    total = len(writer.pages)                      # type: ignore[attr-defined]
    per_page: dict[int, int] = {}
    for page_number, text in items:
        if page_number < 1 or page_number > total:
            raise PdfError(f"页码超出范围：{page_number}（这份 PDF 共 {total} 页）")
        offset = per_page.get(page_number, 0)
        per_page[page_number] = offset + 1
        width, height = _page_size(writer.pages[page_number - 1])   # type: ignore[attr-defined]
        top = max(24.0, height - 48.0 - offset * 34.0)
        rectangle = (max(8.0, width - 240.0), top - 28.0,
                     max(80.0, width - 12.0), top)
        writer.add_annotation(                        # type: ignore[attr-defined]
            page_number=page_number - 1,
            annotation=FreeText(text=text, rect=rectangle, font_size="10pt",
                                font_color="1f2430", border_color="1452C4",
                                background_color="eef1f8"),
        )
    return _save(writer, output)


# ---------------------------------------------------------------- 文本与图片

def extract_text(path: str | Path, *, pages: str = "", password: str = "") -> str:
    """抽取文本层（`pages` 为空 = 全部页）。"""
    reader = _open_reader(path, password)
    wanted = set(parse_pages(pages, len(reader.pages)))
    chunks: list[str] = []
    for index, page in enumerate(reader.pages):
        if index not in wanted:
            continue
        try:
            chunks.append((page.extract_text() or "").strip())
        except Exception:  # noqa: BLE001  单页失败不影响其余页
            continue
    return "\n\n".join(chunk for chunk in chunks if chunk)


def to_images(path: str | Path, output_dir: str | Path, *, pages: str = "",
              dpi: int = 150) -> list[Path]:
    """PDF → PNG（扫描件 OCR 的前置步骤）；需要 PyMuPDF，缺失时给可操作提示。"""
    try:
        import fitz  # PyMuPDF
    except Exception as error:  # noqa: BLE001
        raise PdfError(
            "PDF 转图片需要 PyMuPDF（pip install pymupdf）："
            "装好后可把扫描件转成图片再做 OCR，或用「墨软转换」的 PDF → 图片") from error
    reader_pages = _open_reader(path)
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    produced: list[Path] = []
    wanted = parse_pages(pages, len(reader_pages.pages))
    document = fitz.open(str(path))
    try:
        for index in wanted:
            page = document.load_page(index)
            pixmap = page.get_pixmap(dpi=max(72, int(dpi)))
            target = folder / f"{Path(path).stem}-第{index + 1}页.png"
            pixmap.save(str(target))
            produced.append(target)
    finally:
        try:
            document.close()
        except Exception:  # noqa: BLE001
            pass
    return produced


def document_to_images(path: str | Path, output_dir: str | Path, *, pages: str = "",
                       dpi: int = 150) -> list[Path]:
    """把**任意受支持文档**导出为 PNG（PDF 直接渲染；Office 先经 LibreOffice 转 PDF）。

    这是需求 6.2「演示文稿导出 PDF/图片」的落地点：PPTX/PPT/ODP 先由 LibreOffice 转成
    PDF（保真度最高），再逐页渲染成 PNG；缺 LibreOffice 或 PyMuPDF 时给出可操作提示。
    """
    source = Path(path)
    if not source.is_file():
        raise PdfError(f"文件不存在：{source}")
    suffix = source.suffix.lower()
    if suffix == ".pdf":
        return to_images(source, output_dir, pages=pages, dpi=dpi)
    from . import formats as fmt

    spec = fmt.spec_for_path(source)
    if spec is not None and spec.render == fmt.RENDER_IMAGE:
        raise PdfError("该文件本身就是图片：直接打开即可，无需导出图片")
    from modu_workbench.core.convert import office_io

    if office_io.find_soffice() is None:
        raise PdfError(
            "把 Office 文档导出为图片需要 LibreOffice（soffice）：安装后可一键导出，"
            "或改用「导出 PDF」再自行截图")
    generated = office_io.soffice_convert(source, target="pdf")
    if generated is None:
        raise PdfError(f"LibreOffice 无法转换这份文档：{source.name}")
    try:
        return to_images(generated, output_dir, pages=pages, dpi=dpi)
    finally:
        try:
            generated.unlink(missing_ok=True)
        except OSError:
            pass


def capability_text() -> str:
    return ("PDF 工具：拆分（每页/每 N 页/按段）、合并（可加书签）、抽取指定页、压缩（轻/中/强）、"
            "加密与解密（可控制打印/复制/修改权限）、旋转、水印与追踪标识、表单域列出与填写、"
            "批注（读取与写入 FreeText 标注）、文本层抽取、PDF → PNG（需 PyMuPDF）、"
            "任意文档 → PNG（Office 文档经 LibreOffice 转 PDF 再渲染）")


__all__ = [
    "A4_HEIGHT_PT",
    "A4_WIDTH_PT",
    "PdfError",
    "PdfInfo",
    "annotate_pdf",
    "capability_text",
    "compress_pdf",
    "decrypt_pdf",
    "document_to_images",
    "encrypt_pdf",
    "extract_pages",
    "extract_text",
    "fill_form",
    "list_annotations",
    "list_form_fields",
    "merge_pdfs",
    "parse_pages",
    "pdf_info",
    "render_overlay_pdf",
    "rotate_pdf",
    "split_pdf",
    "to_images",
    "watermark_pdf",
]
