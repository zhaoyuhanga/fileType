"""表格读写：xlsx(openpyxl) / xls(xlrd) / csv → csv/html/txt/pdf。"""
from __future__ import annotations

import csv
import html as html_mod
import io
from pathlib import Path

from .pdf_out import render_text_pdf


def read_sheet_rows(path: Path) -> list[list[str]]:
    """读取首个工作表为二维字符串列表。"""
    ext = path.suffix.lower()
    if ext == ".csv":
        with open(path, encoding="utf-8-sig", newline="") as fp:
            return [[(c or "") for c in row] for row in csv.reader(fp)]
    if ext in (".xlsx", ".xlsm"):
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        sheet = wb.worksheets[0]
        rows: list[list[str]] = []
        for row in sheet.iter_rows(values_only=True):
            rows.append(["" if cell is None else str(cell) for cell in row])
        wb.close()
        return rows
    if ext == ".xls":
        import xlrd

        book = xlrd.open_workbook(str(path))
        sheet = book.sheet_by_index(0)
        return [[sheet.cell_value(r, c) if sheet.cell_value(r, c) is not None else ""
                 for c in range(sheet.ncols)] for r in range(sheet.nrows)]
    raise ValueError("仅支持 XLSX / XLS / CSV 表格文件")


def _to_csv_text(rows: list[list[str]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerows(rows)
    return buffer.getvalue()


def _to_html(rows: list[list[str]], title: str) -> str:
    body = "\n".join(
        "<tr>" + "".join(f"<td>{html_mod.escape(c)}</td>" for c in row) + "</tr>" for row in rows
    )
    return (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<title>{html_mod.escape(title)}</title></head>"
        f"<body><table border='1' cellpadding='6' cellspacing='0'>{body}</table></body></html>"
    )


def convert_sheet(path: Path, target: str, output: Path, title: str) -> None:
    rows = read_sheet_rows(path)
    if target == "csv":
        output.write_text(_to_csv_text(rows), encoding="utf-8")
        return
    if target == "txt":
        text = "\n".join("\t".join(row) for row in rows)
        output.write_text(text, encoding="utf-8")
        return
    if target == "html":
        output.write_text(_to_html(rows, title), encoding="utf-8")
        return
    if target == "pdf":
        text = "\n".join("\t".join(row) for row in rows)
        render_text_pdf(text, output, title=title)
        return
    raise ValueError(f"不支持的表格目标：{target}")
