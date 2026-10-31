"""墨软图库左侧分类导航树（美化版）的行为回归。

对应第四轮用户反馈：「分类树空白有点大有点丑，且点击会有蓝色标记，可以重新美化一下」。
美化本身（通栏圆角选中态、令牌配色、紧凑行高）由
`tests/architecture/test_ui_design_system.py` 的树规则 + 抓像素测试锁定；
这里只保证**行为没变**：点节点照旧筛选、计数照样正确、折叠展开照旧可用，
并锁住新增的「当前分类保持高亮」。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from modu_workbench.boards.gallery.board import BROWSE_KEY, GalleryBoardPage
from modu_workbench.boards.gallery.nav_tree import (
    BADGE_ROLE,
    GROUP_GAP,
    GROUP_ROLE,
    MUTED_ROLE,
    PAYLOAD_ROLE,
    ROW_MIN_HEIGHT,
    CategoryNavDelegate,
    CategoryNavTree,
)
from modu_workbench.core.gallery import ImageLibrary, ImageStorage
from modu_workbench.ui_kit.tokens import SPACE


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _make_library(tmp_path: Path, *, fill: bool = True) -> ImageLibrary:
    store = ImageStorage(str(tmp_path / "gallery.db"))
    lib = ImageLibrary(store, str(tmp_path / "cache"), image_dir=str(tmp_path / "images"))
    if not fill:
        return lib
    root = tmp_path / "photos"
    root.mkdir()
    rng = np.random.default_rng(11)
    for index in range(12):
        Image.fromarray(rng.integers(0, 255, (120, 160, 3), dtype="uint8")).save(
            root / f"风景{index:02d}.jpg")
    for index in range(4):
        Image.fromarray(rng.integers(0, 255, (120, 160, 3), dtype="uint8")).save(
            root / f"人像{index:02d}.jpg")
    lib.import_paths([str(root)])
    lib.create_album("旅行")
    ids = [item.id for item in lib.storage.list_images(limit=20)]
    lib.tag_images(ids[:6], "海边")
    lib.tag_images(ids[6:10], "城市夜景")
    return lib


@pytest.fixture()
def library(tmp_path: Path) -> ImageLibrary:
    return _make_library(tmp_path)


def _build_page(qapp: QApplication, library: ImageLibrary) -> GalleryBoardPage:
    widget = GalleryBoardPage(library=library)
    widget.resize(1100, 760)
    widget.show()
    qapp.processEvents()
    return widget


@pytest.fixture()
def page(qapp: QApplication, library: ImageLibrary):  # noqa: ARG001
    widget = _build_page(qapp, library)
    yield widget
    widget.shutdown()
    widget.deleteLater()
    qapp.processEvents()


def _node(page: GalleryBoardPage, prefix: str) -> QTreeWidgetItem:
    for index in range(page._side.topLevelItemCount()):
        node = page._side.topLevelItem(index)
        if node.text(0).startswith(prefix):
            return node
    raise AssertionError(f"左侧树里没有「{prefix}」分组")


def _child(node: QTreeWidgetItem, label: str) -> QTreeWidgetItem:
    for index in range(node.childCount()):
        if node.child(index).text(0) == label:
            return node.child(index)
    raise AssertionError(f"「{node.text(0)}」下没有「{label}」节点")


def _selectable(item: QTreeWidgetItem) -> bool:
    return bool(item.flags() & Qt.ItemFlag.ItemIsSelectable)


# --------------------------------------------------------------- 结构与计数


def test_side_uses_the_themed_nav_tree(page: GalleryBoardPage) -> None:
    """左侧必须是美化过的 CategoryNavTree（而不是默认 QTreeWidget）。"""
    assert isinstance(page._side, CategoryNavTree)
    assert isinstance(page._side.itemDelegate(), CategoryNavDelegate)
    assert page._side.objectName() == "gallerySide"
    assert page._side.header().isHidden()
    assert page._side.topLevelItemCount() == 5


def test_counts_live_in_badges_not_in_titles(page: GalleryBoardPage) -> None:
    """计数从标题里挪到右侧徽章：数字对齐成列，标题保持干净。"""
    root = page._side.topLevelItem(0)
    assert root.text(0) == "🖼 全部图片"
    assert root.data(0, BADGE_ROLE) == "16"

    assert _child(root, "★ 我的收藏").data(0, BADGE_ROLE) == "0"
    assert _child(root, "🆕 最近导入").data(0, BADGE_ROLE) == "7 天"

    albums = _node(page, "📁")
    assert _child(albums, "旅行").data(0, BADGE_ROLE) == "0"

    tags = _node(page, "🏷")
    assert _child(tags, "海边").data(0, BADGE_ROLE) == "6"
    assert _child(tags, "城市夜景").data(0, BADGE_ROLE) == "4"

    sources = _node(page, "📥")
    assert _child(sources, "本地导入").data(0, BADGE_ROLE) == "16"

    # 徽章数字与库里的真实数量一致（不是写死的）
    assert root.data(0, BADGE_ROLE) == str(page._library.storage.count_images())


def test_group_headers_carry_no_payload_and_are_not_selectable(page: GalleryBoardPage) -> None:
    for prefix in ("📁", "🗓", "🏷", "📥"):
        node = _node(page, prefix)
        assert node.data(0, GROUP_ROLE) is True
        assert node.data(0, PAYLOAD_ROLE) is None
        assert not _selectable(node), "分组标题不该能选中"


def test_add_node_kinds(qapp: QApplication) -> None:  # noqa: ARG001
    """三种节点：可点节点带条件、分组标题弱化、占位行淡显且不可点。"""
    tree = CategoryNavTree()
    node = tree.add_node(None, "海边", badge=6, payload={"tag": "海边"})
    assert node.data(0, PAYLOAD_ROLE) == {"tag": "海边"}
    assert node.data(0, BADGE_ROLE) == "6"
    assert node.data(0, GROUP_ROLE) is None
    assert _selectable(node)
    assert node.toolTip(0) == "海边（6）", "标题里没有计数，tooltip 要补全"

    group = tree.add_node(None, "📁 相册", group=True)
    assert group.data(0, GROUP_ROLE) is True and not _selectable(group)

    placeholder = tree.add_node(None, "还没有标签", hint="选中图片后点底栏「打标签…」")
    assert placeholder.data(0, MUTED_ROLE) is True
    assert placeholder.data(0, BADGE_ROLE) is None
    assert placeholder.toolTip(0) == "选中图片后点底栏「打标签…」"
    assert not _selectable(placeholder)


def test_empty_library_groups_give_next_step(qapp: QApplication, tmp_path: Path) -> None:
    """空图库时每个分组都要给一句「下一步做什么」，而不是留一个空分组。"""
    page = _build_page(qapp, _make_library(tmp_path, fill=False))
    try:
        for prefix in ("📁", "🗓", "🏷", "📥"):
            node = _node(page, prefix)
            assert node.childCount() == 1, f"「{prefix}」分组空了也没给说明"
            child = node.child(0)
            assert child.data(0, MUTED_ROLE) is True
            assert child.toolTip(0) and child.toolTip(0) != child.text(0), "占位行要给出下一步"
        assert page._side.topLevelItem(0).data(0, BADGE_ROLE) == "0"
    finally:
        page.shutdown()
        page.deleteLater()
        qapp.processEvents()


# --------------------------------------------------------------- 行为不变


def test_clicking_a_node_still_filters_the_grid(page: GalleryBoardPage, qapp: QApplication) -> None:
    tags = _node(page, "🏷")
    page._on_side_clicked(_child(tags, "海边"), 0)
    qapp.processEvents()
    qapp.processEvents()
    assert len(page._items) == 6
    assert "海边" in page._scope_text()
    assert page._stack.currentIndex() == 0


def test_active_node_stays_highlighted_after_tree_rebuild(
    page: GalleryBoardPage, qapp: QApplication
) -> None:
    """点完节点树会重建：高亮必须跟着回到对应节点（否则用户看不出在看哪一类）。

    重建会销毁旧节点对象，所以这里只按标签比对，不去碰旧对象（shiboken 崩溃的坑）。
    """
    tags = _node(page, "🏷")
    page._on_side_clicked(_child(tags, "城市夜景"), 0)
    qapp.processEvents()
    qapp.processEvents()

    current = page._side.currentItem()
    assert current is not None, "重建后选中态丢了"
    assert current.text(0) == "城市夜景"
    assert current.data(0, BADGE_ROLE) == "4"
    assert len(page._items) == 4


def test_default_and_all_node_are_highlighted(page: GalleryBoardPage, qapp: QApplication) -> None:
    """默认进页面就高亮「全部图片」，点回它也能恢复。"""
    assert page._side.currentItem() is not None
    assert page._side.currentItem().text(0) == "🖼 全部图片"

    tags = _node(page, "🏷")
    page._on_side_clicked(_child(tags, "海边"), 0)
    qapp.processEvents()
    qapp.processEvents()
    assert page._side.currentItem().text(0) == "海边"

    page._on_side_clicked(page._side.topLevelItem(0), 0)
    qapp.processEvents()
    qapp.processEvents()
    assert len(page._items) == 16
    assert page._side.currentItem().text(0) == "🖼 全部图片"


def test_collapse_state_survives_rebuild(page: GalleryBoardPage, qapp: QApplication) -> None:
    """折叠/展开照旧可用，而且用户折起来的组不会在重建后被强行展开。"""
    albums = _node(page, "📁")
    albums.setExpanded(False)
    qapp.processEvents()
    assert not albums.isExpanded()

    page.reload()
    qapp.processEvents()
    qapp.processEvents()
    assert _node(page, "📁").isExpanded() is False, "重建后折叠状态被丢掉了"

    _node(page, "📁").setExpanded(True)
    qapp.processEvents()
    assert _node(page, "📁").isExpanded() is True


def test_month_node_still_drives_the_timeline(page: GalleryBoardPage, qapp: QApplication) -> None:
    months = _node(page, "🗓")
    assert months.childCount() >= 1, "有拍摄时间时应列出月份"
    bucket = months.child(0).text(0)
    page._on_side_clicked(months.child(0), 0)
    qapp.processEvents()
    qapp.processEvents()
    assert len(page._items) == 16
    assert page._side.currentItem().text(0) == bucket


def test_clicking_group_header_does_nothing(page: GalleryBoardPage, qapp: QApplication) -> None:
    """分组标题没有筛选条件：点了不应该改变结果集。"""
    page._search.setText("海边")
    page._on_filter_changed()
    qapp.processEvents()
    before = len(page._items)

    page._on_side_clicked(_node(page, "📁"), 0)
    qapp.processEvents()
    assert len(page._items) == before
    assert page._search.text() == "海边"


# --------------------------------------------------------------- 外观细节


def test_rows_are_compact_and_indentation_uses_token(page: GalleryBoardPage) -> None:
    """行高/缩进比默认（36 / 20）紧凑：十几个节点才不会散在面板里。

    行高下限由控件自己兜底（`ROW_MIN_HEIGHT`），不依赖样式表是否存在。
    """
    tree = page._side
    assert tree.indentation() == SPACE["lg"]
    assert tree.indentation() < 20

    plain = tree.visualItemRect(_child(tree.topLevelItem(0), "★ 我的收藏")).height()
    assert plain == ROW_MIN_HEIGHT, f"普通行行高应为 {ROW_MIN_HEIGHT}px，实测 {plain}px"
    assert plain < 36, "必须明显比默认行高（36px）紧凑"

    # 分组标题上方多留 GROUP_GAP：分组之间才有段落感
    group = tree.visualItemRect(_node(page, "📁")).height()
    assert group == plain + GROUP_GAP


def test_tree_does_not_need_a_horizontal_scrollbar(page: GalleryBoardPage) -> None:
    """标题超长就省略（有 tooltip），不靠横向滚动条 —— 免得把底部顶掉。"""
    assert page._side.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff


def test_browse_page_still_renders(page: GalleryBoardPage, qapp: QApplication) -> None:
    page.show_page(BROWSE_KEY)
    qapp.processEvents()
    assert page._side.isVisible()
    assert page._grid.count() == 16
    page._grid.ensure_current()
    assert page._grid.current_item() is not None
