"""影视播放偏好：音量与倍速重启后保留。"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from modu_workbench.core.platform.paths import SETTINGS_APP, SETTINGS_ORG
from modu_workbench.ui_kit.media.player import (
    VIDEO_SPEED_KEY,
    VIDEO_VOLUME_KEY,
    VideoPlayerDialog,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _clean_prefs():
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    for key in (VIDEO_VOLUME_KEY, VIDEO_SPEED_KEY):
        settings.remove(key)
    settings.sync()
    yield
    for key in (VIDEO_VOLUME_KEY, VIDEO_SPEED_KEY):
        settings.remove(key)
    settings.sync()


def _open() -> VideoPlayerDialog:
    return VideoPlayerDialog(title="测试片", url="")


def test_volume_and_speed_are_restored(qapp: QApplication) -> None:
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    settings.setValue(VIDEO_VOLUME_KEY, 35)
    settings.setValue(VIDEO_SPEED_KEY, 1.5)
    settings.sync()

    dialog = _open()
    try:
        assert dialog._volume.value() == 35  # noqa: SLF001
        assert float(dialog._speed_box.currentData()) == 1.5  # noqa: SLF001
    finally:
        dialog.close()


def test_changes_are_persisted(qapp: QApplication) -> None:
    dialog = _open()
    try:
        dialog._volume.setValue(50)  # noqa: SLF001
        dialog._speed_box.setCurrentIndex(dialog._speed_box.findData(2.0))  # noqa: SLF001
        settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        assert int(settings.value(VIDEO_VOLUME_KEY)) == 50
        assert float(settings.value(VIDEO_SPEED_KEY)) == 2.0
    finally:
        dialog.close()

    second = _open()
    try:
        assert second._volume.value() == 50  # noqa: SLF001
        assert float(second._speed_box.currentData()) == 2.0  # noqa: SLF001
    finally:
        second.close()


def test_video_import_can_be_cancelled(tmp_path: Path) -> None:
    """导入大影片时点「取消」应在下一个文件前停下，已入库的保留。"""
    from modu_workbench.core.video import VideoLibrary, VideoStorage

    storage = VideoStorage(str(tmp_path / "video.db"))
    library = VideoLibrary(storage, tmp_path / "files")
    paths = []
    for index in range(8):
        path = tmp_path / f"片子{index}.mp4"
        path.write_bytes(b"\x47" + b"\x00" * 63)
        paths.append(str(path))

    state = {"cancelled": False}

    def on_progress(done: int, total: int, message: str) -> None:
        if done >= 1:
            state["cancelled"] = True

    videos = library.import_paths(
        paths, on_progress=on_progress, cancel=lambda: state["cancelled"]
    )
    try:
        assert 1 <= len(videos) < 8
        assert len(storage.list_videos()) == len(videos)
    finally:
        storage.close()


def test_cancel_current_reaches_video_import(qapp: QApplication, tmp_path: Path) -> None:
    from PySide6.QtWidgets import QWidget

    from modu_workbench.boards.video.library_page import VideoLibraryPage
    from modu_workbench.core.video import VideoLibrary, VideoStorage
    from modu_workbench.ui_kit.toast import Toaster

    storage = VideoStorage(str(tmp_path / "video.db"))
    library = VideoLibrary(storage, tmp_path / "files")
    host = QWidget()
    page = VideoLibraryPage(library, storage, Toaster(host))
    try:
        calls: list[bool] = []

        class DummyWorker:
            def isRunning(self) -> bool:
                return True

            def cancel(self) -> None:
                calls.append(True)

            def wait(self, _ms: int) -> bool:
                return True

        page._import_worker = DummyWorker()  # type: ignore[assignment]  # noqa: SLF001
        assert page.cancel_current() is True
        assert calls == [True]
    finally:
        page.shutdown()
        storage.close()
