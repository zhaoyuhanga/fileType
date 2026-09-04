"""在线书库页：粘 URL 整本下载（00shu 直链优先，其余按目录章节下载）。

下载在后台 QThread 中进行；完成后可一键导入书架。
书源抓取仅供个人学习与试读，默认带合规开关。
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from modu_workbench.core.reader import Library
from modu_workbench.core.reader.online import NovelSource
from modu_workbench.ui_kit.toast import Toaster

DEFAULT_SOURCE_BASE = "https://www.00shu.la"
_DOWN_PAGE_RE = re.compile(r"/txt/(\d+)/?", re.IGNORECASE)


class _DownloadWorker(QThread):
    progressed = Signal(int, int, str)   # current, total, current_title
    completed = Signal(str)              # 保存路径
    failed = Signal(str)

    def __init__(self, novel_url: str, save_dir: str, parent=None):
        super().__init__(parent)
        self._url = novel_url
        self._save_dir = Path(save_dir)

    def run(self) -> None:  # noqa: D102
        source = NovelSource(base_url=DEFAULT_SOURCE_BASE)
        try:
            save_dir = self._save_dir
            save_dir.mkdir(parents=True, exist_ok=True)

            direct = _DOWN_PAGE_RE.search(self._url)
            if direct:
                # 00shu 整本直链：先抓书名再拉整本 txt
                page_html = source._get(self._url)  # noqa: SLF001
                soup = BeautifulSoup(page_html, "html.parser")
                h1 = soup.find("h1")
                title = h1.get_text(strip=True) if h1 else "novel"
                safe = re.sub(r'[\\/:*?"<>|]', "_", title).strip() or "novel"
                save_path = save_dir / f"{safe}.txt"
                book_id = direct.group(1)
                parsed = urlsplit(DEFAULT_SOURCE_BASE)
                download_url = f"{parsed.scheme}://down.{parsed.netloc}/modules/article/txtarticle.php?id={book_id}"
                source.download_full_txt(download_url, save_path, on_progress=self._on_full_progress)
            else:
                path = source.download_novel(self._url, save_dir, on_progress=self._on_novel_progress)
                save_path = path
            self.completed.emit(str(save_path))
        except Exception as error:  # noqa: BLE001
            self.failed.emit(f"下载失败：{error}")

    def _on_novel_progress(self, prog) -> None:  # noqa: ANN001
        self.progressed.emit(prog.current, max(prog.total, 1), prog.current_title or "")

    def _on_full_progress(self, written: int, total: int) -> None:
        self.progressed.emit(written, max(total, 1), "整本 txt")


class OnlineDownloadPage(QWidget):
    """在线书库页：URL 输入 → 后台下载 → 完成自动入库。"""

    def __init__(self, library: Library, toaster: Toaster, parent: QWidget | None = None):
        super().__init__(parent)
        self._library = library
        self._toaster = toaster
        self._worker: _DownloadWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(10)

        title = QLabel("在线书库（按需下载）")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "支持两类来源：粘贴书站目录页 URL（按章节下载），或 00shu 类站点整本 TXT 直链页（快速下载）。"
        )
        subtitle.setObjectName("pageSub")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        form = QVBoxLayout()
        form.setSpacing(8)

        url_row = QHBoxLayout()
        url_label = QLabel("书籍 URL")
        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText("示例：https://www.00shu.la/txt/53054/ 或书站目录页地址")
        self._url_input.textChanged.connect(self._update_ui)
        url_row.addWidget(url_label)
        url_row.addWidget(self._url_input, 1)
        form.addLayout(url_row)

        dir_row = QHBoxLayout()
        dir_label = QLabel("保存到")
        self._dir_input = QLineEdit(str(default_download_dir()))
        browse = QPushButton("选择…")
        browse.clicked.connect(self._pick_dir)
        dir_row.addWidget(dir_label)
        dir_row.addWidget(self._dir_input, 1)
        dir_row.addWidget(browse)
        form.addLayout(dir_row)
        layout.addLayout(form)

        self._compliance = QCheckBox("仅用于个人学习与试读，遵守目标站点条款与版权要求（默认开启）")
        self._compliance.setChecked(True)
        self._compliance.toggled.connect(self._update_ui)
        layout.addWidget(self._compliance)

        actions = QHBoxLayout()
        self._start_button = QPushButton("开始下载")
        self._start_button.clicked.connect(self._start_download)
        self._open_button = QPushButton("打开保存目录")
        self._open_button.clicked.connect(self._open_dir)
        self._open_button.setEnabled(False)
        actions.addWidget(self._start_button)
        actions.addWidget(self._open_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        layout.addWidget(self._progress)

        self._status = QLabel("就绪。粘贴 URL 后开始下载。")
        self._status.setObjectName("readerStatus")
        layout.addWidget(self._status)

        layout.addStretch(1)

        disclaimer = QLabel(
            "⚠ 在线抓取仅用于个人学习、试读与备份自有内容；请勿传播受版权保护的作品。"
        )
        disclaimer.setObjectName("readerStatus")
        disclaimer.setWordWrap(True)
        layout.addWidget(disclaimer)

        self._update_ui()

    # ---------- 交互 ----------

    def _pick_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择下载保存目录", self._dir_input.text())
        if folder:
            self._dir_input.setText(folder)

    def _update_ui(self) -> None:
        enabled = self._compliance.isChecked() and self._worker is None
        self._url_input.setEnabled(enabled)
        self._dir_input.setEnabled(enabled)
        self._start_button.setEnabled(enabled and bool(self._url_input.text().strip()))

    def _start_download(self) -> None:
        url = self._url_input.text().strip()
        if not url.startswith(("http://", "https://")):
            self._toaster.error("请输入完整的 http(s) 书籍 URL")
            return
        if not self._compliance.isChecked():
            return

        self._worker = _DownloadWorker(url, self._dir_input.text().strip() or str(default_download_dir()))
        self._worker.progressed.connect(self._on_progress)
        self._worker.completed.connect(self._on_completed)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_worker_finished)

        self._progress.setValue(0)
        self._status.setText("正在连接书站…")
        self._start_button.setText("下载中…")
        self._update_ui()
        self._worker.start()

    def _on_progress(self, current: int, total: int, detail: str) -> None:
        percent = min(100, int(current * 100 / total)) if total else 0
        self._progress.setValue(percent)
        self._status.setText(f"{percent}% · {detail or '下载中'}")

    def _on_completed(self, save_path: str) -> None:
        self._progress.setValue(100)
        self._status.setText(f"已保存：{save_path}")
        self._open_button.setEnabled(True)
        try:
            self._library.import_path(save_path)
            self._toaster.success("已下载并导入书架")
        except Exception as error:  # noqa: BLE001
            self._toaster.info(f"已下载：{save_path}（自动导入失败：{error}，可手动导入）")

    def _on_failed(self, message: str) -> None:
        self._status.setText(message)
        self._toaster.error(message)

    def _on_worker_finished(self) -> None:
        self._worker = None
        self._start_button.setText("开始下载")
        self._update_ui()

    def _open_dir(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(self._dir_input.text().strip()))


def default_download_dir() -> Path:
    base = Path.home() / "Downloads" / "墨读书库下载"
    base.mkdir(parents=True, exist_ok=True)
    return base
