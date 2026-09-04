"""Python ⇄ React(main 分支) 桥：把 window.formatFlow 能力映射到 Python 实现。

- 转换：用本仓库 core/convert 引擎按“文件扩展名 + 目标”执行（兼容 main 分支 actionId）；
- 批量：后台线程逐文件执行，经 QWebChannel 信号推送 jobs:event 并最终回传结果数组；
- 文档：readDoc / saveDoc / saveDocAs 复用 core/convert.text_io 与文件对话框。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog

from modu_workbench.core.convert import engine as convert_engine
from modu_workbench.core.convert.formats import format_from_extension
from modu_workbench.core.convert.registry import get_action
from modu_workbench.core.convert.text_io import read_text_smart, write_text
from modu_workbench.services import config

SUPPORTED_IMPORT_EXTENSIONS = (
    ".txt", ".md", ".html", ".json", ".docx", ".doc", ".xlsx", ".xls", ".csv",
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".mp4", ".mov", ".avi",
    ".m4a", ".mp3", ".wav", ".zip", ".tar", ".rar",
)


def file_item_json(file_path: str, index: int) -> dict:
    path = Path(file_path)
    ext = path.suffix.lower()
    fmt = format_from_extension(ext)
    if fmt == "unknown":
        if ext == ".jpeg":
            fmt = "jpg"
    return {
        "id": f"m{index}-{path.name}",
        "path": str(path),
        "name": path.name,
        "extension": ext.lstrip("."),
        "format": fmt,
        "category": _category(fmt),
        "sizeBytes": path.stat().st_size if path.exists() else 0,
        "selected": True,
        "status": "queued",
        "progress": 0,
    }


def _category(fmt: str) -> str:
    if fmt in ("jpg", "png", "webp", "bmp", "gif"):
        return "image"
    if fmt in ("mp4", "mov", "avi"):
        return "video"
    if fmt in ("m4a", "mp3", "wav"):
        return "audio"
    if fmt in ("zip", "rar", "tar"):
        return "archive"
    if fmt == "unknown":
        return "unknown"
    return "document"


class _BatchWorker(QThread):
    event_signal = Signal(str)   # JSON 事件 {fileId,status,...}
    done_signal = Signal(str)    # JSON 结果数组

    def __init__(self, bridge: "WebBridge", batch_id: str, resolve_action_id: str, files, output_dir: str):
        super().__init__()
        self._bridge = bridge
        self._batch = batch_id
        self._files = files
        self._output = output_dir
        self._action_id = resolve_action_id

    def run(self) -> None:  # noqa: D102
        results: list[dict] = []
        for file in self._files:
            if self._bridge.is_cancelled(self._batch):
                outcome = {"fileId": file["id"], "status": "cancelled", "message": "已取消"}
                results.append(outcome)
                self.event_signal.emit(json.dumps(outcome))
                continue
            self.event_signal.emit(json.dumps({"fileId": file["id"], "status": "running"}))
            result = resolve_and_run(self._action_id, file, self._output, self._bridge.cancel_event(self._batch))
            outcome = {
                "fileId": file["id"],
                "status": result.status,
                "targetFormat": result.target_format,
                "outputPath": result.output_path,
                "message": result.message,
            }
            results.append(outcome)
            self.event_signal.emit(json.dumps(outcome))
        self.done_signal.emit(json.dumps(results))


def resolve_and_run(action_id: str, file: dict, output_dir: str, cancel) -> convert_engine.ConversionResult:
    """兼容 main 分支的 actionId：按文件扩展名 + 目标格式在 Python 引擎中找对应动作。"""
    path = file["path"]
    if action_id in ("compress-to-zip", "compress-to-tar", "zip-extract", "tar-extract", "rar-extract"):
        action = get_action(action_id)
    else:
        source = format_from_extension(Path(path).suffix)
        if action_id.endswith("-to-markdown"):
            target = "markdown"
        elif action_id.endswith("-to-txt"):
            target = "txt"
        elif action_id.endswith("-to-html"):
            target = "html"
        elif action_id.endswith("-to-pdf"):
            target = "pdf"
        elif action_id.endswith("-to-csv"):
            target = "csv"
        else:
            target = action_id.rsplit("-to-", 1)[-1] if "-to-" in action_id else ""
        action = get_action(f"{source}-to-{target}") if target else None
    if action is None:
        return convert_engine.ConversionResult(
            action_id, path, "failed", message="该转换组合当前暂未提供（墨读引擎内）。"
        )
    return convert_engine.run_conversion(action, path, output_dir, cancel=cancel)


class WebBridge(QObject):
    """暴露给前端（QWebChannel 'bridge' 对象）的方法。"""

    resultReady = Signal(str, str)   # requestId, resultJson
    eventReady = Signal(str, str)    # kind, payloadJson

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pending: dict[str, str] = {}
        self._batch_requests: dict[str, str] = {}
        self._workers: dict[str, _BatchWorker] = {}
        self._cancel_flags: dict[str, threading.Event] = {}

    # ---- 通用 RPC ----

    @Slot(str, str, str)
    def invoke(self, request_id: str, method: str, args_json: str) -> None:
        try:
            args = json.loads(args_json) if args_json else []
            handler = getattr(self, f"handle_{method}", None)
            if handler is None:
                self._emit_result(request_id, {"error": f"unknown method {method}"})
                return
            result = handler(*args)
            if isinstance(result, dict) and result.get("_async"):
                batch_id = result["_async"]
                self._pending[request_id] = batch_id
                self._batch_requests[batch_id] = request_id
                return
            self._emit_result(request_id, result)
        except Exception as error:  # noqa: BLE001
            self._emit_result(request_id, {"error": str(error)})

    def _emit_result(self, request_id: str, value) -> None:
        self.resultReady.emit(request_id, json.dumps(value, ensure_ascii=False))

    def _finish_batch(self, batch_id: str, payload: str) -> None:
        request_id = self._batch_requests.pop(batch_id, None)
        self._pending.pop(request_id, None) if request_id else None
        if request_id:
            self._emit_result(request_id, json.loads(payload))

    # ---- 桥能力实现 ----

    def handle_importPaths(self, paths: list[str]) -> list[dict]:
        files = config.walk_book_files(paths)  # 复用递归扫描
        if not files:
            files = []
        collected: list[str] = []
        for raw in paths:
            p = Path(raw)
            if p.is_dir():
                for child in p.rglob("*"):
                    if child.is_file() and child.suffix.lower() in SUPPORTED_IMPORT_EXTENSIONS:
                        collected.append(str(child))
            elif p.is_file() and p.suffix.lower() in SUPPORTED_IMPORT_EXTENSIONS:
                collected.append(str(p))
        collected = list(dict.fromkeys(collected + files))
        return [file_item_json(p, i) for i, p in enumerate(collected)]

    def handle_pickFiles(self) -> list[dict]:
        paths, _ = QFileDialog.getOpenFileNames(None, "选择要转换的文件", "", "支持文件 (*.*)")
        return self.handle_importPaths(paths)

    def handle_pickFolders(self) -> list[dict]:
        folder = QFileDialog.getExistingDirectory(None, "选择文件夹（递归）")
        return self.handle_importPaths([folder]) if folder else []

    def handle_pickOutputDirectory(self) -> str:
        folder = QFileDialog.getExistingDirectory(None, "选择输出目录")
        return folder or ""

    def handle_getDefaultOutputDir(self) -> str:
        from modu_workbench.boards.convert_board import default_output_dir

        return str(default_output_dir())

    def handle_openOutputDirectory(self, directory: str) -> str:
        target = directory or self.handle_getDefaultOutputDir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(target))
        return ""

    def handle_getEngineStatus(self) -> list[dict]:
        from modu_workbench.core.convert.media_io import find_ffmpeg
        from modu_workbench.core.convert.office_io import find_soffice

        return [
            {"name": "内置转换核心", "available": True, "details": "文本/图片/归档/文档/表格"},
            {"name": "ffmpeg", "available": bool(find_ffmpeg()), "executable": find_ffmpeg()},
            {"name": "LibreOffice", "available": bool(find_soffice()), "executable": find_soffice()},
        ]

    def handle_openMedia(self, file_path: str) -> dict:
        """用 Python 原生播放器打开本地媒体（嵌入式前端 mp4 预览用）。"""
        from modu_workbench.services.media_player import MediaPlayerDialog

        dialog = MediaPlayerDialog(file_path)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()
        return {}

    def handle_mediaUrl(self, file_path: str) -> str:
        """返回本地 HTTP 流地址，供 Web 视图内嵌 <video> 播放。"""
        from modu_workbench.services.media_server import media_url

        return media_url(file_path)

    def handle_startJobs(self, batch_id: str, action_id: str, files: list[dict], output_dir: str) -> dict:
        worker = _BatchWorker(self, batch_id, action_id, files, output_dir)
        worker.event_signal.connect(lambda payload: self.eventReady.emit("job", payload))
        worker.done_signal.connect(lambda payload: self._finish_batch(batch_id, payload))
        self._workers[batch_id] = worker
        self._cancel_flags[batch_id] = threading.Event()
        worker.finished.connect(lambda: self._cleanup(batch_id, worker))
        worker.start()
        return {"_async": batch_id}

    def _cleanup(self, batch_id: str, worker: _BatchWorker) -> None:
        self._workers.pop(batch_id, None)
        self._cancel_flags.pop(batch_id, None)

    @Slot(str)
    def cancelJobs(self, batch_id: str) -> None:
        flag = self._cancel_flags.get(batch_id)
        if flag:
            flag.set()

    def is_cancelled(self, batch_id: str) -> bool:
        flag = self._cancel_flags.get(batch_id)
        return bool(flag and flag.is_set())

    def cancel_event(self, batch_id: str) -> threading.Event | None:
        return self._cancel_flags.get(batch_id)

    # ---- 文档读写 ----

    def handle_readDoc(self, file_path: str) -> dict:
        path = Path(file_path)
        ext = path.suffix.lower()
        info = path.stat()
        item = file_item_json(file_path, 0)
        if ext == ".mp4":
            return {"ok": True, "doc": {**item, "kind": "media", "category": "video"}}
        content = read_text_smart(path)
        return {"ok": True, "doc": {**item, "kind": "text", "content": content, "encoding": "UTF-8", "sizeBytes": info.st_size}}

    def handle_saveDoc(self, file_path: str, content: str) -> dict:
        try:
            write_text(file_path, content)
            return {"ok": True, "path": file_path}
        except Exception as error:  # noqa: BLE001
            return {"ok": False, "error": str(error)}

    def handle_saveDocAs(self, source_path: str, suggested_name: str, content) -> dict:
        target, _ = QFileDialog.getSaveFileName(None, "另存为", suggested_name or os.path.basename(source_path))
        if not target:
            return {"ok": False, "canceled": True}
        try:
            if isinstance(content, str):
                write_text(target, content)
            else:
                import shutil

                shutil.copyfile(source_path, target)
            return {"ok": True, "path": target}
        except Exception as error:  # noqa: BLE001
            return {"ok": False, "error": str(error)}
