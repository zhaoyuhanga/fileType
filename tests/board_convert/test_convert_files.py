"""文件扫描与导入扩展名测试（原桥接测试；v1.0.0 移除内嵌前端后保留通用部分）。"""
from __future__ import annotations

import json
from pathlib import Path

from modu_workbench.core.platform.files import SUPPORTED_IMPORT_EXTENSIONS, scan_paths

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---- 文件扫描 ----

def test_scan_paths_recurses_filters_and_dedupes(tmp_path: Path) -> None:
    (tmp_path / "skip.exe").write_bytes(b"MZ")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.md").write_text("# b", encoding="utf-8")
    (sub / "c.mp4").write_bytes(b"0000")

    # 同时传入目录、已包含的文件（去重）、不存在的路径（忽略）
    found = scan_paths(
        [str(tmp_path), str(tmp_path / "a.txt"), str(tmp_path / "missing.txt")]
    )

    assert found == [str(tmp_path / "a.txt"), str(sub / "b.md"), str(sub / "c.mp4")]
    assert all(Path(p).suffix.lower() in SUPPORTED_IMPORT_EXTENSIONS for p in found)


def test_scan_paths_rejects_unsupported_single_file(tmp_path: Path) -> None:
    binary = tmp_path / "tool.exe"
    binary.write_bytes(b"MZ")
    assert scan_paths([str(binary)]) == []
    assert scan_paths([]) == []


# ---- 批量任务：任何异常都必须回传 done ----

def output_exists(path: str | None) -> bool:
    return bool(path) and Path(path).exists()
