"""墨软文档：OCR（可选能力，缺失时必须给出可操作的提示而不是静默失败）。

设计取舍：
- 引擎用**外部 tesseract**（PATH 或 `MODU_TESSERACT` 指定），不随包分发 ——
  语言包体积大、许可证独立，随包会让安装包再涨几十 MB；
- 图片 OCR 只需 tesseract；PDF 扫描件 OCR 还需要 PyMuPDF（把页面渲染成位图），
  两者缺一都返回空结果，由调用方把原因写进界面提示；
- 不联网、不上传：OCR 全程在本机完成。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

DEFAULT_LANG = "chi_sim+eng"
#: 单次 OCR 的页数上限（扫描件动辄几百页，避免一次点下去卡住界面）
MAX_PDF_PAGES = 60


def find_tesseract() -> str | None:
    """定位 tesseract：环境变量 → PATH → Windows 常见安装位置。"""
    override = os.environ.get("MODU_TESSERACT")
    if override:
        return override if Path(override).is_file() else None
    on_path = shutil.which("tesseract")
    if on_path:
        return on_path
    if os.name == "nt":
        for candidate in (
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tesseract-OCR/tesseract.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Tesseract-OCR/tesseract.exe",
            Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Tesseract-OCR/tesseract.exe",
        ):
            if candidate.is_file():
                return str(candidate)
    return None


def available() -> bool:
    return find_tesseract() is not None


def pdf_render_available() -> bool:
    """PDF → 位图是否可用（PyMuPDF）。"""
    try:
        import fitz  # noqa: F401  PyMuPDF

        return True
    except Exception:  # noqa: BLE001
        return False


def ocr_image(path: str | Path, *, lang: str = "", timeout: int = 180) -> str:
    """图片 → 文字；引擎缺失或识别失败时返回空串（调用方负责提示原因）。"""
    engine = find_tesseract()
    if engine is None or not Path(path).is_file():
        return ""
    command = [engine, str(path), "stdout", "-l", lang or DEFAULT_LANG]
    try:
        proc = subprocess.run(
            command, capture_output=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.decode("utf-8", "ignore").strip()


def ocr_pdf_pages(path: str | Path, *, lang: str = "", max_pages: int = MAX_PDF_PAGES) -> list[tuple[int, str]]:
    """扫描 PDF → [(页码, 文字)]；缺少 PyMuPDF 或 tesseract 时返回空列表。"""
    if not available() or not pdf_render_available():
        return []
    try:
        import fitz
    except Exception:  # noqa: BLE001
        return []

    results: list[tuple[int, str]] = []
    try:
        document = fitz.open(str(path))
    except Exception:  # noqa: BLE001
        return []
    try:
        with tempfile.TemporaryDirectory(prefix="modu-ocr-") as folder:
            for page_index in range(min(document.page_count, max_pages)):
                page = document.load_page(page_index)
                pixmap = page.get_pixmap(dpi=200)
                image_path = Path(folder) / f"page-{page_index + 1}.png"
                pixmap.save(str(image_path))
                text = ocr_image(image_path, lang=lang)
                if text:
                    results.append((page_index + 1, text))
    except Exception:  # noqa: BLE001
        return results
    finally:
        try:
            document.close()
        except Exception:  # noqa: BLE001
            pass
    return results


def describe() -> str:
    """一行状态说明（设置页/提示条用）。"""
    engine = find_tesseract()
    if engine is None:
        return "未检测到 tesseract：扫描件/图片 OCR 不可用（设置 MODU_TESSERACT 或安装 Tesseract-OCR）"
    parts = [f"tesseract：{engine}"]
    parts.append("PDF 扫描件可直接识别" if pdf_render_available() else "PDF 需先转图片（缺 PyMuPDF）")
    return "；".join(parts)


__all__ = [
    "DEFAULT_LANG",
    "MAX_PDF_PAGES",
    "available",
    "describe",
    "find_tesseract",
    "ocr_image",
    "ocr_pdf_pages",
    "pdf_render_available",
]
