"""转换板块批量流程打磨：跳过/进度/重试/输出目录/删源文件/覆盖策略。"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from modu_workbench.boards.convert import board as board_module
from modu_workbench.boards.convert.board import (
    ConvertBoardPage,
    collect_folder_files,
    configured_output_dir,
)
from modu_workbench.core.convert.engine import ConversionResult, run_conversion
from modu_workbench.core.convert.registry import common_actions, get_action
from modu_workbench.core.platform.paths import SETTINGS_APP, SETTINGS_ORG


@pytest.fixture()
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture()
def out_dir(tmp_path: Path) -> Path:
    path = tmp_path / "out"
    path.mkdir()
    return path


@pytest.fixture(autouse=True)
def _clean_convert_settings():
    """用例之间不共享输出目录/开关（QSettings 在会话内是同一个 INI）。"""
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    for key in (
        "convert/output_dir",
        "convert/no_overwrite",
        "convert/keep_original",
    ):
        settings.remove(key)
    settings.sync()
    yield
    for key in (
        "convert/output_dir",
        "convert/no_overwrite",
        "convert/keep_original",
    ):
        settings.remove(key)
    settings.sync()


def _txt(tmp_path: Path, name: str, text: str = "内容") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _wait(page: ConvertBoardPage) -> None:
    worker = page._worker  # noqa: SLF001
    if worker is None:
        return
    loop = QEventLoop()
    worker.finished.connect(loop.quit)
    from PySide6.QtCore import QTimer

    QTimer.singleShot(15_000, loop.quit)  # 防止信号先于连接发出时把用例挂死
    loop.exec()
    assert page._worker is None, "转换线程未在超时内结束"  # noqa: SLF001


def test_skipped_and_unchecked_rows_never_get_stuck_in_queue(
    qapp: QApplication, out_dir: Path, tmp_path: Path
) -> None:
    """回归：以前 `_start` 把整张表重置成「排队中」，未勾选/被跳过的行永远停在排队中。"""
    page = ConvertBoardPage(output_dir=str(out_dir))
    try:
        good = _txt(tmp_path, "好.txt")
        other = _txt(tmp_path, "不勾选.txt")
        image = tmp_path / "图.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        page._append_paths([str(good), str(other), str(image)])  # noqa: SLF001
        page._rows[1]["checked"] = False  # noqa: SLF001  第二行不参与本批

        page._selected_action = get_action("txt-to-html")  # noqa: SLF001
        page._start()  # noqa: SLF001
        _wait(page)

        statuses = [row["status"] for row in page._rows]  # noqa: SLF001
        assert statuses[0] == "成功"
        assert statuses[1] == "待转换", "未勾选的文件不该被标成排队中"
        assert statuses[2] == "跳过", "不适用该动作的文件应保持「跳过」而不是排队中"
        assert "跳过 1" in page._task_bar.text  # noqa: SLF001
    finally:
        page.close()


def test_progress_denominator_counts_only_current_batch(
    qapp: QApplication, out_dir: Path
) -> None:
    page = ConvertBoardPage(output_dir=str(out_dir))
    try:
        page._batch_paths = ["a", "b", "c"]  # noqa: SLF001
        page._batch_done = 1  # noqa: SLF001
        page._on_started("b")  # noqa: SLF001
        assert "2/3" in page._task_bar.text  # noqa: SLF001
        assert page._task_bar._progress.value() == 33  # noqa: SLF001
    finally:
        page.close()


def test_retry_only_reruns_failed_files(
    qapp: QApplication, out_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = _txt(tmp_path, "bad.txt")
    good = _txt(tmp_path, "good.txt")
    page = ConvertBoardPage(output_dir=str(out_dir))
    try:
        page._append_paths([str(bad), str(good)])  # noqa: SLF001
        page._selected_action = get_action("txt-to-html")  # noqa: SLF001

        calls: list[str] = []
        failed_once: set[str] = set()

        def fake(action, path, out, cancel=None, overwrite=False):
            name = Path(path).name
            calls.append(name)
            if name == "bad.txt" and name not in failed_once:
                failed_once.add(name)
                return ConversionResult(action.id, path, "failed", message="模拟失败")
            return ConversionResult(
                action.id, path, "succeeded",
                output_path=str(Path(out) / f"{Path(path).stem}.html"),
                target_format="html",
            )

        monkeypatch.setattr(board_module.convert_engine, "run_conversion", fake)
        page._start()  # noqa: SLF001
        _wait(page)
        assert page._rows[0]["status"] == "失败"  # noqa: SLF001
        assert page._rows[1]["status"] == "成功"  # noqa: SLF001
        assert page._retry_button.isEnabled()  # noqa: SLF001
        assert "1" in page._retry_button.text()  # noqa: SLF001

        calls.clear()
        page._retry_button.click()  # noqa: SLF001
        _wait(page)
        assert calls == ["bad.txt"], "重试只应跑失败的那一个文件"
        assert page._rows[0]["status"] == "成功"  # noqa: SLF001
        assert not page._retry_button.isEnabled()  # noqa: SLF001
    finally:
        page.close()


def test_output_dir_is_persisted_and_restored(
    qapp: QApplication, tmp_path: Path
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    page = ConvertBoardPage(output_dir=str(first))
    try:
        page._set_output_dir(str(second))  # noqa: SLF001
        stored = QSettings(SETTINGS_ORG, SETTINGS_APP).value("convert/output_dir", "", type=str)
        assert Path(stored) == second
    finally:
        page.close()

    assert Path(configured_output_dir()) == second
    page2 = ConvertBoardPage()
    try:
        assert Path(page2._output_dir) == second  # noqa: SLF001
        assert page2._out_input.text() == str(second)  # noqa: SLF001
    finally:
        page2.close()


def test_folder_import_ignores_unknown_formats(tmp_path: Path) -> None:
    folder = tmp_path / "book"
    folder.mkdir()
    _txt(folder, "第一章.txt")
    (folder / "封面.bin").write_bytes(b"\x00\x01")
    (folder / "无扩展名").write_bytes(b"x")

    files, ignored = collect_folder_files(folder)
    names = sorted(Path(path).name for path in files)
    assert names == ["第一章.txt"]
    assert ignored == 2, "未知格式与无扩展名文件应被计数忽略，而不是塞进列表变成「未知」行"


def test_overwrite_policy_replaces_instead_of_adding_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    source = _txt(tmp_path, "同名.txt", "第一版")
    action = get_action("txt-to-html")

    first = run_conversion(action, source, out)
    assert first.status == "succeeded"
    source.write_text("第二版", encoding="utf-8")
    second = run_conversion(action, source, out)
    assert Path(second.output_path).name == "同名 (2).html", "默认策略必须防覆盖"

    source.write_text("第三版", encoding="utf-8")
    replaced = run_conversion(action, source, out, overwrite=True)
    assert Path(replaced.output_path).name == "同名.html", "允许覆盖时不该再加序号"
    assert "第三版" in Path(replaced.output_path).read_text(encoding="utf-8")
    assert not (out / "同名 (3).html").exists()


def test_source_removal_only_after_success_and_only_when_confirmed(
    qapp: QApplication, out_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    settings.setValue("convert/keep_original", False)

    source = _txt(tmp_path, "待删.txt")
    page = ConvertBoardPage(output_dir=str(out_dir))
    try:
        page._append_paths([str(source)])  # noqa: SLF001
        page._selected_action = get_action("txt-to-html")  # noqa: SLF001

        monkeypatch.setattr(
            QMessageBox, "warning",
            lambda *args, **kwargs: QMessageBox.StandardButton.No,
        )
        page._start()  # noqa: SLF001
        _wait(page)
        assert source.exists(), "用户在确认框选「否」时不能删源文件"

        monkeypatch.setattr(
            QMessageBox, "warning",
            lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
        )
        page._start()  # noqa: SLF001
        _wait(page)
        assert not source.exists(), "确认后应删除转换成功的源文件"
        assert page._rows[0]["status"] == "成功"  # noqa: SLF001
    finally:
        page.close()


def test_engine_actions_for_txt_still_include_html() -> None:
    """守住本文件里用的动作 id（注册表改名时不至于让上面几条测试误报）。"""
    assert any(action.id == "txt-to-html" for action in common_actions(["txt"]))
