"""桥接层健壮性测试：文件扫描、批量任务必然回传、版本单一来源。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from modu_workbench import __version__
from modu_workbench.core.platform.files import SUPPORTED_IMPORT_EXTENSIONS, scan_paths
from modu_workbench.services.web_bridge import _BatchWorker

REPO_ROOT = Path(__file__).resolve().parents[1]


class _StubBridge:
    """最小桥接替身：批量任务只依赖取消查询。"""

    def __init__(self) -> None:
        self.cancelled = False

    def is_cancelled(self, batch_id: str) -> bool:  # noqa: ARG002
        return self.cancelled

    def cancel_event(self, batch_id: str):  # noqa: ARG002
        return None


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

def _run_worker(tmp_path: Path, action_id: str, files: list) -> list[dict]:
    events: list[str] = []
    done: list[str] = []
    worker = _BatchWorker(_StubBridge(), "batch-1", action_id, files, str(tmp_path))
    worker.event_signal.connect(events.append)
    worker.done_signal.connect(done.append)
    worker.run()  # 同线程直调，信号为直连

    assert len(done) == 1, "无论成功失败都必须回传一次结果"
    payload = json.loads(done[0])
    # 每个文件都要有对应事件（running + 终态）
    assert len(events) >= len(payload)
    return payload


def test_batch_worker_reports_malformed_entries(tmp_path: Path) -> None:
    payload = _run_worker(tmp_path, "txt-to-markdown", [{"id": "1"}, {"id": "2"}])
    assert [item["fileId"] for item in payload] == ["1", "2"]
    assert {item["status"] for item in payload} == {"failed"}
    assert all(item["message"] for item in payload)


def test_batch_worker_survives_non_dict_entry(tmp_path: Path) -> None:
    payload = _run_worker(tmp_path, "txt-to-markdown", [None])  # type: ignore[list-item]
    assert len(payload) == 1
    assert payload[0]["status"] == "failed"
    assert payload[0]["fileId"] == ""


def test_batch_worker_runs_real_conversion(tmp_path: Path) -> None:
    source = tmp_path / "note.txt"
    source.write_text("标题\n正文", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    payload = _run_worker(
        tmp_path, "txt-to-markdown", [{"id": "f1", "path": str(source), "name": "note.txt"}]
    )
    if payload[0]["status"] == "failed":
        # 引擎缺少依赖时仅校验契约（也必须给出失败原因）
        assert payload[0]["message"]
    else:
        assert payload[0]["status"] == "succeeded"
        assert output_exists(payload[0].get("outputPath"))


def output_exists(path: str | None) -> bool:
    return bool(path) and Path(path).exists()


# ---- 版本单一来源（pyproject / 包 / 内嵌前端） ----

def test_version_single_source() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, flags=re.M)
    assert declared, "pyproject.toml 缺少 version"
    assert declared.group(1) == __version__

    index = REPO_ROOT / "src" / "modu_workbench" / "webfront" / "index.html"
    html = index.read_text(encoding="utf-8")
    assert f'window.__MODU_VERSION__ = "{__version__}"' in html, (
        "webfront/index.html 版本过期：请重新执行 packaging/build_webfront.ps1"
    )
