"""墨软文档：格式能力矩阵（唯一事实来源）。

维护约定（与 `core/convert/formats.py` 同款思路）：
- 只登记**真的能用**的能力：`edit=False` 的格式在界面上不会给编辑框，
  `convert=False` 的不会出现在导出目标里 —— 登记了却做不到就是"点了没反应"；
- 扩展名别名统一放 `EXTENSION_ALIASES`（htm→html、jpeg→jpg、yml→yaml…），
  业务代码不得自己 `endswith`；
- 国产办公格式（wps/et/dps）按**容器嗅探**归类：OOXML 的按对应族处理，
  旧二进制格式交给 LibreOffice。

`render` 决定界面用哪种控件：rich（QTextBrowser）/ text（QPlainTextEdit）/
sheet（QTableWidget）/ slides（逐页文本）/ data（结构化高亮）/ image（图片预览）。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------- 渲染方式

RENDER_RICH = "rich"
RENDER_TEXT = "text"
RENDER_SHEET = "sheet"
RENDER_SLIDES = "slides"
RENDER_DATA = "data"
RENDER_IMAGE = "image"

RENDER_LABELS = {
    RENDER_RICH: "富文本",
    RENDER_TEXT: "纯文本",
    RENDER_SHEET: "表格",
    RENDER_SLIDES: "演示",
    RENDER_DATA: "结构化数据",
    RENDER_IMAGE: "图片",
}

# ---------------------------------------------------------------- 分类

CATEGORY_ORDER = (
    "document", "text", "markup", "web", "ebook", "sheet", "slides",
    "office_cn", "data", "image", "code",
)

CATEGORY_LABELS = {
    "document": "文档",
    "text": "文本",
    "markup": "标记",
    "web": "网页",
    "ebook": "电子书",
    "sheet": "表格",
    "slides": "演示",
    "office_cn": "国产办公",
    "data": "数据",
    "image": "图片/扫描",
    "code": "代码/日志",
}


def category_label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category or "其它")


# ---------------------------------------------------------------- 格式定义


@dataclass(frozen=True)
class DocFormatSpec:
    """一种文档格式的完整能力声明。"""

    key: str
    label: str
    category: str
    extensions: tuple[str, ...]
    render: str
    edit: bool = True
    convert: bool = True
    annotate: bool = False
    ocr: bool = False
    engine: str = ""
    note: str = ""

    @property
    def category_label(self) -> str:
        return category_label(self.category)

    def capability_text(self) -> str:
        """一行能力摘要（设置页/查看器提示用）。"""
        parts = ["查看"]
        parts.append("编辑" if self.edit else "只读")
        if self.annotate:
            parts.append("批注")
        if self.ocr:
            parts.append("OCR")
        if self.convert:
            parts.append("转换")
        return " · ".join(parts)


#: 全部登记格式（顺序即界面顺序：文档 → 文本 → 标记 → 网页 → 电子书 → 表格
#: → 演示 → 国产办公 → 数据 → 图片 → 代码）
FORMAT_SPECS: tuple[DocFormatSpec, ...] = (
    # ---------- 文档 ----------
    DocFormatSpec(
        "docx", "Word DOCX", "document", ("docx", "docm"), RENDER_RICH,
        engine="python-docx", note="富文本、样式、批注、修订；段落与样式可编辑",
    ),
    DocFormatSpec(
        "doc", "Word DOC", "document", ("doc",), RENDER_RICH, edit=False,
        engine="LibreOffice", note="旧版二进制格式：可查看，编辑请先另存为 DOCX",
    ),
    DocFormatSpec(
        "pdf", "PDF", "document", ("pdf",), RENDER_RICH, edit=False, convert=True,
        annotate=True, ocr=True, engine="pypdf",
        note="支持批注 / 表单填写 / 拆分合并压缩水印加密；扫描件可 OCR",
    ),
    DocFormatSpec(
        "rtf", "RTF", "document", ("rtf",), RENDER_RICH,
        engine="内置 RTF 解析", note="以文本层编辑；复杂排版建议另存为 DOCX",
    ),
    DocFormatSpec(
        "odt", "ODT", "document", ("odt",), RENDER_RICH, edit=False,
        engine="内置 content.xml 解析", note="兼容 OpenOffice；编辑请用 LibreOffice 另存为 DOCX",
    ),
    # ---------- 文本 ----------
    DocFormatSpec(
        "txt", "TXT", "text", ("txt",), RENDER_TEXT,
        engine="编码自适应", note="UTF-8/UTF-16/GB18030 自适应；支持正则替换",
    ),
    # ---------- 标记 ----------
    DocFormatSpec(
        "markdown", "Markdown", "markup", ("md", "markdown", "mdown"), RENDER_RICH,
        engine="内置解析 + markdown", note="实时预览、目录、公式与流程图代码块",
    ),
    # ---------- 网页 ----------
    DocFormatSpec(
        "html", "HTML", "web", ("html", "htm", "xhtml"), RENDER_RICH,
        engine="bs4", note="清理样式后可导出 Markdown / PDF",
    ),
    # ---------- 电子书 ----------
    DocFormatSpec(
        "epub", "EPUB", "ebook", ("epub",), RENDER_RICH, edit=False,
        engine="ebooklib", note="阅读与导出（PDF / Markdown）；正文不直接改写",
    ),
    # ---------- 表格 ----------
    DocFormatSpec(
        "xlsx", "Excel XLSX", "sheet", ("xlsx", "xlsm"), RENDER_SHEET,
        engine="openpyxl", note="公式、筛选、冻结、条件格式与数据验证",
    ),
    DocFormatSpec(
        "xls", "Excel XLS", "sheet", ("xls",), RENDER_SHEET, edit=False,
        engine="xlrd", note="旧版只读：可查看与导出，编辑请另存为 XLSX",
    ),
    DocFormatSpec(
        "ods", "ODS", "sheet", ("ods",), RENDER_SHEET, edit=False,
        engine="LibreOffice", note="兼容 OpenOffice；需要 LibreOffice 才能读",
    ),
    DocFormatSpec(
        "csv", "CSV", "sheet", ("csv",), RENDER_SHEET,
        engine="标准库 csv", note="大文件分块读取；支持自动填充与公式",
    ),
    DocFormatSpec(
        "tsv", "TSV", "sheet", ("tsv",), RENDER_SHEET,
        engine="标准库 csv", note="制表符分隔，其余同 CSV",
    ),
    # ---------- 演示 ----------
    DocFormatSpec(
        "pptx", "PowerPoint PPTX", "slides", ("pptx", "pptm"), RENDER_SLIDES,
        engine="OOXML 文本层", note="逐页查看与备注；文本层可编辑（保留版式）",
    ),
    DocFormatSpec(
        "ppt", "PowerPoint PPT", "slides", ("ppt",), RENDER_SLIDES, edit=False,
        engine="LibreOffice", note="旧版二进制：可查看与导出，编辑需另存为 PPTX",
    ),
    DocFormatSpec(
        "odp", "ODP", "slides", ("odp",), RENDER_SLIDES, edit=False,
        engine="内置 content.xml 解析", note="逐页查看与导出；编辑请用 LibreOffice",
    ),
    # ---------- 国产办公 ----------
    DocFormatSpec(
        "wps", "WPS 文字", "office_cn", ("wps",), RENDER_RICH, edit=False,
        engine="容器嗅探 / LibreOffice", note="OOXML 容器可直接读；旧格式交给 LibreOffice",
    ),
    DocFormatSpec(
        "et", "WPS 表格", "office_cn", ("et", "ett"), RENDER_SHEET, edit=False,
        engine="容器嗅探 / LibreOffice", note="兼容 WPS 生态；导出为 XLSX 后可编辑",
    ),
    DocFormatSpec(
        "dps", "WPS 演示", "office_cn", ("dps", "dpt"), RENDER_SLIDES, edit=False,
        engine="容器嗅探 / LibreOffice", note="逐页查看与导出",
    ),
    # ---------- 数据 ----------
    DocFormatSpec(
        "json", "JSON", "data", ("json", "jsonl"), RENDER_DATA,
        engine="标准库 json", note="格式化、校验（含行列定位报错）",
    ),
    DocFormatSpec(
        "xml", "XML", "data", ("xml", "xsd", "plist"), RENDER_DATA,
        engine="ElementTree", note="结构化查看与美化缩进",
    ),
    DocFormatSpec(
        "yaml", "YAML", "data", ("yaml", "yml"), RENDER_DATA,
        engine="PyYAML", note="结构化查看与格式化",
    ),
    DocFormatSpec(
        "ini", "INI", "data", ("ini", "cfg", "conf", "properties", "toml"), RENDER_DATA,
        engine="configparser", note="键值分组查看",
    ),
    # ---------- 图片 / 扫描 ----------
    DocFormatSpec(
        "png", "PNG", "image", ("png",), RENDER_IMAGE, edit=False, convert=True,
        ocr=True, engine="Pillow + OCR", note="可批注与 OCR，识别后文字可编辑",
    ),
    DocFormatSpec(
        "jpg", "JPEG", "image", ("jpg", "jpeg", "jfif"), RENDER_IMAGE, edit=False,
        convert=True, ocr=True, engine="Pillow + OCR", note="可批注与 OCR",
    ),
    DocFormatSpec(
        "bmp", "BMP", "image", ("bmp",), RENDER_IMAGE, edit=False, convert=True,
        ocr=True, engine="Pillow + OCR", note="可批注与 OCR",
    ),
    DocFormatSpec(
        "tiff", "TIFF", "image", ("tiff", "tif"), RENDER_IMAGE, edit=False,
        convert=True, ocr=True, engine="Pillow + OCR", note="多页扫描件按页 OCR",
    ),
    DocFormatSpec(
        "webp", "WebP", "image", ("webp",), RENDER_IMAGE, edit=False, convert=True,
        ocr=True, engine="Pillow + OCR", note="可批注与 OCR",
    ),
    DocFormatSpec(
        "gif", "GIF", "image", ("gif",), RENDER_IMAGE, edit=False, convert=True,
        engine="Pillow", note="动图按首帧查看",
    ),
    # ---------- 代码 / 日志 ----------
    DocFormatSpec(
        "log", "日志 LOG", "code", ("log", "out", "err"), RENDER_TEXT,
        engine="纯文本 + 高亮", note="大文件分块；支持正则替换与导出",
    ),
    DocFormatSpec(
        "sql", "SQL", "code", ("sql",), RENDER_TEXT,
        engine="Pygments", note="语法高亮、格式化与查找替换",
    ),
    DocFormatSpec(
        "code", "代码文件", "code",
        ("py", "js", "ts", "jsx", "tsx", "css", "java", "c", "h", "cpp", "hpp",
         "go", "rs", "php", "rb", "cs", "kt", "swift", "sh", "bat", "ps1", "vue", "lua"),
        RENDER_TEXT, engine="Pygments", note="语法高亮与编辑；可导出为高亮 HTML",
    ),
)

FORMAT_BY_KEY: dict[str, DocFormatSpec] = {spec.key: spec for spec in FORMAT_SPECS}

# 扩展名 → 格式 key（含别名）
EXTENSION_ALIASES: dict[str, str] = {
    "md": "markdown",
    "markdown": "markdown",
    "mdown": "markdown",
    "mkd": "markdown",
    "htm": "html",
    "xhtml": "html",
    "jpeg": "jpg",
    "jfif": "jpg",
    "tif": "tiff",
    "yml": "yaml",
    "docm": "docx",
    "xlsm": "xlsx",
    "pptm": "pptx",
    "ett": "et",
    "dpt": "dps",
    "cfg": "ini",
    "conf": "ini",
    "properties": "ini",
    "toml": "ini",
    "jsonl": "json",
    "xsd": "xml",
    "plist": "xml",
    "out": "log",
    "err": "log",
    "pyw": "code",
    "mjs": "code",
    "cjs": "code",
    "scss": "code",
    "less": "code",
    "bash": "code",
    "zsh": "code",
    "cmd": "code",
}

_EXTENSION_TO_KEY: dict[str, str] = {}
for _spec in FORMAT_SPECS:
    for _ext in _spec.extensions:
        _EXTENSION_TO_KEY[_ext] = _spec.key
_EXTENSION_TO_KEY.update(EXTENSION_ALIASES)

#: 全部可识别的扩展名（带点，供文件对话框过滤器使用）
SUPPORTED_EXTENSIONS: tuple[str, ...] = tuple(
    sorted({f".{ext}" for ext in _EXTENSION_TO_KEY})
)


def format_key_from_extension(extension: str) -> str:
    """扩展名（带不带点都行）→ 格式 key；未登记返回空串。"""
    ext = (extension or "").strip().lower().lstrip(".")
    if not ext:
        return ""
    if ext == "tar.gz":            # 归档类沿用转换板块语义，文档板块不处理
        return ""
    return _EXTENSION_TO_KEY.get(ext, "")


def format_key_for_path(path: str | Path) -> str:
    """按文件名猜格式；`.dps.gz` 这类多段后缀只取最后一段。"""
    name = Path(str(path)).name.lower()
    for ext in sorted(_EXTENSION_TO_KEY, key=len, reverse=True):
        if name.endswith("." + ext):
            return _EXTENSION_TO_KEY[ext]
    return ""


def get_spec(key: str) -> DocFormatSpec | None:
    return FORMAT_BY_KEY.get(key or "")


def spec_for_path(path: str | Path) -> DocFormatSpec | None:
    return get_spec(format_key_for_path(path))


def format_label(key: str) -> str:
    spec = get_spec(key)
    return spec.label if spec else (key.upper() if key else "未知")


def is_supported(path: str | Path) -> bool:
    return spec_for_path(path) is not None


def editable_formats() -> tuple[DocFormatSpec, ...]:
    return tuple(spec for spec in FORMAT_SPECS if spec.edit)


# ---------------------------------------------------------------- 导出目标

#: 文档板块的原生导出目标（写成 IR → 目标格式；其余交给墨软转换的动作）
EXPORT_TARGETS: tuple[tuple[str, str], ...] = (
    ("txt", "纯文本 TXT"),
    ("markdown", "Markdown"),
    ("html", "网页 HTML"),
    ("docx", "Word DOCX"),
    ("xlsx", "Excel XLSX"),
    ("csv", "CSV"),
    ("tsv", "TSV"),
    ("json", "结构化 JSON"),
    ("pdf", "PDF"),
)

EXPORT_TARGET_LABELS = dict(EXPORT_TARGETS)
EXPORT_TARGET_KEYS = tuple(key for key, _label in EXPORT_TARGETS)

#: 各导出目标的扩展名
EXPORT_EXTENSIONS = {
    "txt": ".txt",
    "markdown": ".md",
    "html": ".html",
    "docx": ".docx",
    "xlsx": ".xlsx",
    "csv": ".csv",
    "tsv": ".tsv",
    "json": ".json",
    "pdf": ".pdf",
}


def export_label(target: str) -> str:
    return EXPORT_TARGET_LABELS.get(target, target.upper())


# ---------------------------------------------------------------- 界面用矩阵


def describe_matrix() -> list[dict[str, str]]:
    """把能力矩阵整理成「一行一格式」的字典列表（设置页/帮助页展示）。"""
    rows: list[dict[str, str]] = []
    for spec in FORMAT_SPECS:
        rows.append({
            "key": spec.key,
            "category": spec.category_label,
            "label": spec.label,
            "extensions": "、".join(f".{ext}" for ext in spec.extensions[:4])
            + ("…" if len(spec.extensions) > 4 else ""),
            "render": RENDER_LABELS.get(spec.render, spec.render),
            "view": "支持",
            "edit": "支持" if spec.edit else "只读",
            "convert": "支持" if spec.convert else "—",
            "note": spec.note,
        })
    return rows


def summary_text() -> str:
    """一句话概括（板块页头/README 用）。"""
    return (
        f"覆盖 {len(FORMAT_SPECS)} 种格式 / {len(CATEGORY_ORDER)} 个族："
        + "、".join(category_label(name) for name in CATEGORY_ORDER)
    )


def extensions_for_dialog() -> str:
    """QFileDialog 过滤器字符串。"""
    patterns = " ".join(f"*{ext}" for ext in SUPPORTED_EXTENSIONS)
    return f"支持的文档 ({patterns});;所有文件 (*.*)"


__all__ = [
    "CATEGORY_LABELS",
    "CATEGORY_ORDER",
    "DocFormatSpec",
    "EXPORT_EXTENSIONS",
    "EXPORT_TARGETS",
    "EXPORT_TARGET_KEYS",
    "EXPORT_TARGET_LABELS",
    "EXTENSION_ALIASES",
    "FORMAT_BY_KEY",
    "FORMAT_SPECS",
    "RENDER_DATA",
    "RENDER_IMAGE",
    "RENDER_LABELS",
    "RENDER_RICH",
    "RENDER_SHEET",
    "RENDER_SLIDES",
    "RENDER_TEXT",
    "SUPPORTED_EXTENSIONS",
    "category_label",
    "describe_matrix",
    "editable_formats",
    "export_label",
    "extensions_for_dialog",
    "format_key_for_path",
    "format_key_from_extension",
    "format_label",
    "get_spec",
    "is_supported",
    "spec_for_path",
    "summary_text",
]
