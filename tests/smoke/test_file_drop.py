"""拖拽导入测试。

## 背景

接入前全仓 `dragEnterEvent`/`dropEvent` 是 **0 处** —— 一个以文件为核心的桌面
应用却不能把文件拖进来，只能一层层点「选择文件」。这是"功能打磨"里最容易被
用户第一眼发现的缺口。

实现是 `ui_kit/dnd.py` 的 `FileDropController`：挂在板块根控件上的事件过滤器，
不侵入任何板块的类结构。本文件覆盖两层：

1. **控制器语义**（裸 `QWidget` + 假 MIME）：后缀白名单、目录放行、去重、
   上限、路径规范化、处理函数抛异常时不外泄；
2. **四个板块真的挂上了**：书库 / 图库 / 影视 / 转换，且拖入的路径确实进了
   各自的导入入口。

> 事件用 `controller.eventFilter(widget, event)` 直接驱动，而不是
> `widget.event(...)`：合成的拖放事件经过 QWidget 事件分发时容易踩到平台层的
> 生命周期问题（实测会让进程直接崩掉），直连过滤器既稳定又足够覆盖逻辑。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QWidget

from modu_workbench.ui_kit.dnd import MAX_DROPPED_ITEMS, FileDropController, accepts_drag

_HOSTS: list[QWidget] = []


# --------------------------------------------------------------------------- 工具


#: 合成事件用的 QMimeData 必须**保住 Python 引用**：
#: 构造完事件后若只留局部变量，GC 会收走它，事件里就只剩一个悬空的 QObject
#: 包装（调用时 AttributeError: 'QObject' object has no attribute 'hasUrls'）。
_MIMES: list[QMimeData] = []


def _mime(paths: list[Path]) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    _MIMES.append(mime)
    return mime


def _enter_event(paths: list[Path]) -> QDragEnterEvent:
    return QDragEnterEvent(
        QPoint(5, 5),
        Qt.DropAction.CopyAction,
        _mime(paths),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _drop_event(paths: list[Path]) -> QDropEvent:
    return QDropEvent(
        QPoint(5, 5),
        Qt.DropAction.CopyAction,
        _mime(paths),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _norm(paths: list[Path]) -> list[str]:
    """拖放层对外给出的是规范化后的路径（Windows 上是反斜杠）。"""
    return [str(Path(path)) for path in paths]


def _toaster(app: QApplication):  # noqa: ANN001, ANN202
    from modu_workbench.ui_kit.toast import Toaster

    host = QWidget()
    host.resize(200, 100)
    _HOSTS.append(host)          # 宿主必须在测试期间存活
    return Toaster(host)


class _Widget(QWidget):
    """被拖放的目标控件。"""


@pytest.fixture(autouse=True)
def _drop_cleanup(qapp: QApplication):
    """用例结束后关掉自己造的控件。

    **刻意不调用 `sendPostedEvents(DeferredDelete)`**：它是全局的，会把
    *其它测试*里排队但尚未处理的删除一并执行 —— 那些测试可能仍持有
    Python 引用，控件被提前销毁后就成了悬空包装，最终在解释器退出时崩溃
    （现象：全部用例通过、进程却以 0xC0000409 退出）。
    这里只 `close()` + 交给 session 级清理统一收尾。
    """
    yield
    for host in list(_HOSTS):
        host.close()
        host.deleteLater()
    _HOSTS.clear()
    for mime in list(_MIMES):
        mime.deleteLater()
    _MIMES.clear()
    qapp.processEvents()


def test_accepts_matching_suffix_and_rejects_other(qapp: QApplication, tmp_path: Path) -> None:
    """后缀白名单生效：.txt 接受，.exe 拒绝；目录一律接受。"""
    txt = tmp_path / "a.txt"
    txt.write_text("x", encoding="utf-8")
    exe = tmp_path / "a.exe"
    exe.write_bytes(b"MZ")

    controller = FileDropController(_Widget(), lambda _p: None, suffixes=(".txt",))
    assert controller.accepts_path(str(txt)) is True
    assert controller.accepts_path(str(exe)) is False
    assert controller.accepts_path(str(tmp_path)) is True, "目录应一律接受"


def test_no_suffix_filter_accepts_anything(qapp: QApplication, tmp_path: Path) -> None:
    """不传 suffixes 时接受任意文件（转换板块就是这样：按格式自己判断）。"""
    weird = tmp_path / "a.weird"
    weird.write_bytes(b"x")
    controller = FileDropController(_Widget(), lambda _p: None)
    assert controller.accepts_path(str(weird)) is True


def test_paths_are_normalized(qapp: QApplication, tmp_path: Path) -> None:
    """`QUrl` 给的是正斜杠，交给 handler 的必须是程序内部统一的写法。

    否则同一个文件的两种写法会被当成两条 —— 去重、查重、同一路径判断全失效。
    """
    target = tmp_path / "a.txt"
    target.write_text("x", encoding="utf-8")

    received: list[list[str]] = []
    widget = _Widget()
    controller = FileDropController(widget, received.append, suffixes=(".txt",))
    controller.eventFilter(widget, _drop_event([target]))

    assert received, "handler 没被调用"
    assert received[0] == _norm([target]), f"路径没有被规范化：{received[0]!r}"


def test_collect_dedupes_and_caps(qapp: QApplication, tmp_path: Path) -> None:
    """同一路径重复只算一次；条目数有上限（防手滑拖进超大目录）。"""
    files = []
    for index in range(MAX_DROPPED_ITEMS + 20):
        path = tmp_path / f"f{index}.txt"
        path.write_text("x", encoding="utf-8")
        files.append(path)

    controller = FileDropController(_Widget(), lambda _p: None, suffixes=(".txt",))
    assert len(controller.collect(_mime(files))) == MAX_DROPPED_ITEMS, "没有截断到上限"
    picked = controller.collect(_mime([files[0], files[0], files[1]]))
    assert picked == _norm([files[0], files[1]]), "没去重"


def test_drag_enter_accepts_then_drop_calls_handler(qapp: QApplication, tmp_path: Path) -> None:
    """拖入被接受 → 松手调用 handler，且拿到的是本地路径。"""
    good = tmp_path / "book.txt"
    good.write_text("x", encoding="utf-8")
    received: list[list[str]] = []

    widget = _Widget()
    controller = FileDropController(widget, received.append, suffixes=(".txt",))

    enter = _enter_event([good])
    assert controller.eventFilter(widget, enter) is True
    assert enter.isAccepted(), "拖入合法文件应被接受"

    drop = _drop_event([good])
    controller.eventFilter(widget, drop)
    assert received == [_norm([good])], f"handler 没收到路径：{received}"


def test_drop_with_only_rejected_files_is_ignored(qapp: QApplication, tmp_path: Path) -> None:
    """全是不可接受的文件时不调用 handler，也不崩。"""
    bad = tmp_path / "a.exe"
    bad.write_bytes(b"MZ")
    received: list[list[str]] = []

    widget = _Widget()
    controller = FileDropController(widget, received.append, suffixes=(".txt",))
    drop = _drop_event([bad])
    controller.eventFilter(widget, drop)
    assert received == []
    assert not drop.isAccepted()


def test_handler_exception_does_not_escape(qapp: QApplication, tmp_path: Path) -> None:
    """导入函数抛异常时必须被吞掉并留痕，不能把界面搞崩。"""
    good = tmp_path / "a.txt"
    good.write_text("x", encoding="utf-8")

    def boom(_paths: list[str]) -> None:
        raise RuntimeError("故意失败")

    widget = _Widget()
    controller = FileDropController(widget, boom, suffixes=(".txt",))
    drop = _drop_event([good])
    controller.eventFilter(widget, drop)          # 不抛即通过
    assert drop.isAccepted(), "路径本身合法，事件仍应被接受"


# --------------------------------------------------------------------------- 四个板块


def test_book_shelf_accepts_drag(qapp: QApplication, tmp_path: Path) -> None:
    """书库：挂上拖拽，且拖入 .txt 会走到导入入口。"""
    from modu_workbench.boards.book.shelf import ShelfView
    from modu_workbench.core.book import Library, Storage

    library = Library(Storage(str(tmp_path / "book.db")))
    shelf = ShelfView(library, _toaster(qapp))
    try:
        assert accepts_drag(shelf), "书库没有启用拖放"
        called: list[list[str]] = []
        shelf._import_paths = called.append                    # noqa: SLF001  拦截真实导入

        book = tmp_path / "sample.txt"
        book.write_text("第一章\n\n正文。", encoding="utf-8")
        shelf._drop.eventFilter(shelf, _drop_event([book]))    # noqa: SLF001
        assert called == [_norm([book])], f"书库拖入没有进入导入入口：{called}"
    finally:
        shelf.close()
        shelf.deleteLater()


def test_gallery_board_accepts_drag(qapp: QApplication, tmp_path: Path) -> None:
    """图库：挂上拖拽，图片会被接受，且来源标为「拖拽」。"""
    from modu_workbench.boards.gallery.board import GalleryBoardPage
    from modu_workbench.core.gallery import ImageLibrary, ImageStorage

    library = ImageLibrary(
        ImageStorage(str(tmp_path / "g.db")),
        str(tmp_path / "cache"),
        image_dir=str(tmp_path / "images"),
    )
    board = GalleryBoardPage(library=library)
    try:
        assert accepts_drag(board), "图库没有启用拖放"
        called: list[tuple[list[str], str]] = []
        board._start_import = lambda paths, source: called.append((paths, source))  # noqa: SLF001

        photo = tmp_path / "p.jpg"
        photo.write_bytes(b"\xff\xd8\xff")
        board._drop.eventFilter(board, _drop_event([photo]))    # noqa: SLF001
        assert called and called[0][0] == _norm([photo])
        assert called[0][1] == "拖拽", "来源应标为拖拽，便于提示与统计"
    finally:
        board.close()
        board.deleteLater()


def test_convert_board_accepts_drag(qapp: QApplication, tmp_path: Path) -> None:
    """转换：挂上拖拽，拖入文件会进转换列表。"""
    from modu_workbench.boards.convert.board import ConvertBoardPage

    page = ConvertBoardPage(output_dir=str(tmp_path / "out"))
    try:
        assert accepts_drag(page), "转换板块没有启用拖放"
        doc = tmp_path / "a.md"
        doc.write_text("# 标题", encoding="utf-8")
        page._drop.eventFilter(page, _drop_event([doc]))        # noqa: SLF001

        paths = [row["path"] for row in page._rows]             # noqa: SLF001
        assert str(Path(doc)) in paths, f"拖入的文件没进列表：{paths}"
    finally:
        page.close()
        page.deleteLater()


def test_video_library_accepts_drag(qapp: QApplication, tmp_path: Path) -> None:
    """影视：挂上拖拽，视频文件会走 _import_paths。"""
    from modu_workbench.boards.video.library_page import VideoLibraryPage
    from modu_workbench.core.video import VideoLibrary, VideoStorage

    storage = VideoStorage(str(tmp_path / "v.db"))
    page = None
    try:
        library = VideoLibrary(storage, tmp_path / "files")
        page = VideoLibraryPage(library, storage, _toaster(qapp))
        assert accepts_drag(page), "影视板块没有启用拖放"

        received: list[list[str]] = []
        page._import_paths = received.append                    # noqa: SLF001
        video = tmp_path / "movie.mp4"
        video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        page._drop.eventFilter(page, _drop_event([video]))      # noqa: SLF001
        assert received == [_norm([video])], f"影视拖入没有进入导入入口：{received}"
    finally:
        if page is not None:
            page.close()
            page.deleteLater()
            storage.close()
