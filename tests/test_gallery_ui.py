"""墨软图库界面回归测试（offscreen）：缩放下标 / 筛选后翻页 / 缩略图补齐 / 左侧分类树。

对应四个用户反馈：
1. 缩放下方百分比标签不变化；
2. 搜索后网格不显示内容，且"下一张"没反应；
3. 扫描文件夹后大量缩略图是灰色占位（"很丑"），也没有入口回到全部图片；
4. 左侧需要列出有哪些图片，并能按时间 / 来源 / 标签 / 相册分类查看。
另外覆盖：点击左侧树节点时不得销毁正在处理点击的节点（shiboken 崩溃）。
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from modu_workbench.boards.gallery_board import BROWSE_KEY, VIEWER_KEY, GalleryBoardPage
from modu_workbench.boards.gallery_widgets import ITEM_ROLE
from modu_workbench.core.image import ImageLibrary, ImageStorage


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture()
def library(tmp_path: Path) -> ImageLibrary:
    store = ImageStorage(str(tmp_path / "gallery.db"))
    lib = ImageLibrary(store, str(tmp_path / "cache"), image_dir=str(tmp_path / "images"))
    root = tmp_path / "photos"
    root.mkdir()
    rng = np.random.default_rng(7)
    for index in range(12):
        Image.fromarray(rng.integers(0, 255, (120, 160, 3), dtype="uint8")).save(
            root / f"风景{index:02d}.jpg")
    for index in range(4):
        Image.fromarray(rng.integers(0, 255, (120, 160, 3), dtype="uint8")).save(
            root / f"人像{index:02d}.jpg")
    lib.import_paths([str(root)])
    return lib


@pytest.fixture()
def page(qapp: QApplication, library: ImageLibrary):  # noqa: ARG001
    widget = GalleryBoardPage(library=library)
    widget.resize(1100, 760)
    widget.show()
    qapp.processEvents()
    yield widget
    widget.shutdown()
    widget.deleteLater()
    qapp.processEvents()


def drain_thumbnails(qapp: QApplication, page: GalleryBoardPage) -> None:
    """把分批补齐的缩略图全部跑完（真实运行时由 QTimer 驱动）。"""
    grid = page._grid
    for _ in range(200):
        if len(grid._loaded) >= grid.count():
            return
        grid.fill_thumbnails_lazily()
        qapp.processEvents()
    raise AssertionError("缩略图补齐没有收敛")


# --------------------------------------------------------------------- 1 缩放标签


def test_zoom_label_follows_zoom(page: GalleryBoardPage, qapp: QApplication) -> None:
    """缩放下方的百分比标签必须随放大/缩小/适应窗口实时变化。"""
    page._grid.ensure_current()
    page._step_viewer(0)
    qapp.processEvents()
    fitted = page._viewer_zoom.text()
    assert "适应窗口" in fitted

    page._viewer.zoom_in()
    qapp.processEvents()
    zoomed = page._viewer.zoom_percent
    assert page._viewer_zoom.text() == f"缩放 {zoomed}%"
    assert page._viewer_zoom.text() != fitted

    page._viewer.zoom_out()
    qapp.processEvents()
    assert page._viewer_zoom.text() == f"缩放 {page._viewer.zoom_percent}%"

    page._viewer.reset_zoom()
    qapp.processEvents()
    assert "适应窗口" in page._viewer_zoom.text()


def test_zoom_changed_signal_emitted(page: GalleryBoardPage, qapp: QApplication) -> None:
    seen: list[int] = []
    page._viewer.zoomChanged.connect(lambda percent, _fitted: seen.append(percent))
    page._grid.ensure_current()
    page._step_viewer(0)
    qapp.processEvents()
    page._viewer.zoom_in()
    qapp.processEvents()
    assert seen, "ImageViewer.zoomChanged 未发出，标签无从更新"


# ----------------------------------------------------------- 2 搜索 / 翻页不响应


def test_search_keeps_current_row_and_next_works(page: GalleryBoardPage, qapp: QApplication) -> None:
    """搜索后网格要有内容且默认选中第一张，"下一张"确实会前进。"""
    page._search.setText("风景")
    page._on_filter_changed()
    qapp.processEvents()

    assert len(page._items) == 12
    assert page._grid.count() == 12
    assert page._grid.currentRow() == 0
    assert "搜索「风景」" in page._scope_text()

    page._step_viewer(1)
    qapp.processEvents()
    assert page._stack.currentIndex() == 1, "应切到大图页"
    first = page._viewer_info.text()
    assert "风景" in first

    page._step_viewer(1)
    qapp.processEvents()
    assert page._viewer_info.text() != first, "「下一张」没有换图"


def test_next_stays_inside_filtered_result(page: GalleryBoardPage, qapp: QApplication) -> None:
    """翻页只在当前结果集内，不会跳到被筛掉的图片。"""
    page._search.setText("人像")
    page._on_filter_changed()
    qapp.processEvents()
    assert len(page._items) == 4

    for _ in range(6):
        page._step_viewer(1)
        qapp.processEvents()
        assert "人像" in page._viewer_info.text()


def test_empty_result_does_not_break_next(page: GalleryBoardPage, qapp: QApplication) -> None:
    page._search.setText("不存在的名字")
    page._on_filter_changed()
    qapp.processEvents()
    assert page._items == []
    page._step_viewer(1)          # 不应抛异常
    qapp.processEvents()
    assert page._grid.currentRow() == -1


def test_reload_keeps_selected_item(page: GalleryBoardPage, qapp: QApplication) -> None:
    """刷新（reload）后仍保留原选中项，避免 currentRow 掉成 -1。"""
    page._grid.setCurrentRow(3)
    keep = page._grid.current_item()
    assert keep is not None
    page.reload()
    qapp.processEvents()
    assert page._grid.current_item() is not None
    assert page._grid.current_item().id == keep.id


# ------------------------------------------------------- 3 缩略图占位 / 回到全部


def test_thumbnails_all_filled_after_lazy_pass(page: GalleryBoardPage, qapp: QApplication) -> None:
    """扫描文件夹后，分批补齐必须覆盖每一行，不再残留灰色占位。"""
    assert page._grid.count() == 16
    drain_thumbnails(qapp, page)
    assert len(page._grid._loaded) == page._grid.count()


def test_side_click_returns_to_all_images(page: GalleryBoardPage, qapp: QApplication) -> None:
    """左侧「全部图片」是回到全量的入口，且会清掉之前的搜索条件。"""
    page._search.setText("风景")
    page._on_filter_changed()
    qapp.processEvents()
    assert len(page._items) == 12

    all_node = page._side.topLevelItem(0)
    assert "全部图片" in all_node.text(0)

    page._on_side_clicked(all_node, 0)
    qapp.processEvents()
    assert page._search.text() == ""
    assert len(page._items) == 16
    assert page._stack.currentIndex() == 0
    assert page._nav_buttons[BROWSE_KEY].objectName() == "navButtonActive"


# --------------------------------------------------------------- 4 左侧分类导航


def test_side_tree_lists_every_dimension(page: GalleryBoardPage, qapp: QApplication) -> None:
    labels = [
        page._side.topLevelItem(i).text(0)
        for i in range(page._side.topLevelItemCount())
    ]
    for expected in ("全部图片", "相册", "时间", "标签", "来源"):
        assert any(expected in label for label in labels), f"左侧缺少「{expected}」分组"

    root = page._side.topLevelItem(0)
    children = [root.child(i).text(0) for i in range(root.childCount())]
    assert any("我的收藏" in text for text in children)
    assert any("最近导入" in text for text in children)


def test_side_nodes_carry_filter_payloads(page: GalleryBoardPage, qapp: QApplication) -> None:
    """每个可点节点都要带筛选条件；纯分组标题不可点。"""
    for index in range(page._side.topLevelItemCount()):
        node = page._side.topLevelItem(index)
        payload = node.data(0, ITEM_ROLE)
        if "全部图片" in node.text(0):
            assert payload and payload.get("all")
        elif node.text(0).startswith(("📁", "🗓", "🏷", "📥")):
            assert payload is None, "分组标题不应携带筛选条件"


def test_click_month_node_filters_and_keeps_tree_alive(
    page: GalleryBoardPage, qapp: QApplication
) -> None:
    """点击时间节点要按月份筛选，并且不能在点击处理中把节点销毁（shiboken 崩溃）。"""
    month = None
    for index in range(page._side.topLevelItemCount()):
        node = page._side.topLevelItem(index)
        if node.text(0).startswith("🗓") and node.childCount():
            month = node.child(0)
            break
    if month is None:
        pytest.skip("没有拍摄时间数据")

    label = month.text(0)
    page._on_side_clicked(month, 0)
    # 关键回归：此刻树尚未重建，旧节点必须仍然存活
    assert month.text(0) == label

    qapp.processEvents()
    assert page._side_refresh_pending is False
    assert len(page._items) == 16
    assert "·" in page._scope_text() or page._scope_text()
    # 重建后树仍然完整可用
    assert page._side.topLevelItemCount() == 5

    page._on_side_clicked(page._side.topLevelItem(0), 0)
    qapp.processEvents()
    assert len(page._items) == 16


def test_favorite_node_filters(page: GalleryBoardPage, qapp: QApplication) -> None:
    keep = page._grid.items()[0]
    page._library.storage.set_favorite([keep.id], True)
    page._on_side_clicked(page._side.topLevelItem(0).child(0), 0)
    qapp.processEvents()
    assert page._favorite_only.isChecked()
    assert [item.id for item in page._items] == [keep.id]
    assert "仅收藏" in page._scope_text()

    page._on_side_clicked(page._side.topLevelItem(0), 0)
    qapp.processEvents()
    assert not page._favorite_only.isChecked()
    assert len(page._items) == 16


def test_recent_import_node_and_reset(page: GalleryBoardPage, qapp: QApplication) -> None:
    """「最近导入」节点带 N 天范围；用户改用其它筛选时要清掉，避免交集为空。"""
    recent = page._side.topLevelItem(0).child(1)
    assert "最近导入" in recent.text(0)

    old = int(time.time()) - 30 * 86400
    page._library.storage._conn.execute("UPDATE images SET added_at = ?", (old,))
    page._library.storage._conn.commit()

    page._on_side_clicked(recent, 0)
    qapp.processEvents()
    assert page._recent_days == 7
    assert page._items == []
    assert "最近 7 天导入" in page._scope_text()

    # 用户随后手动改筛选 → 范围被清空，结果不再为空
    page._on_filter_changed()
    qapp.processEvents()
    assert page._recent_days == 0
    assert len(page._items) == 16


def test_viewer_page_navigation_keeps_scope(page: GalleryBoardPage, qapp: QApplication) -> None:
    page._search.setText("风景")
    page._on_filter_changed()
    qapp.processEvents()
    page.show_page(VIEWER_KEY)
    assert page._stack.currentIndex() == 1
    page.show_page(BROWSE_KEY)
    assert page._stack.currentIndex() == 0
