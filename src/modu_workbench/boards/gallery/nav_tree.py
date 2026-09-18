"""图库左侧分类导航树：整行圆角选中态 + 右侧徽章，替代 QTreeWidget 的默认外观。

用户反馈「分类树空白有点大有点丑，且点击会有蓝色标记，可以重新美化一下」。
拆开来是三层原因，都在这里（配合 `ui_kit/theme.py` 的树规则）修掉：

1. **默认蓝标记**：Fusion/QSS 会把「分支（缩进）列」按系统高亮色画成一个方块，
   内容列则被 QSS 画成圆角块 —— 两段拼起来就是「左边一块宝蓝、右边一块浅紫」的接缝。
   实测（Qt 6.11，离屏渲染逐像素对比）：
   - `QTreeView::branch:selected { background: transparent; }` **不生效**
     （transparent 被当成"没写这条规则"，继续退回系统蓝）；
   - `background: <具体颜色>` 生效，但分支列是**方块**，与圆角内容块之间仍有接缝；
   - 正解是让控件根本不给分支列上色（QSS `show-decoration-selected: 0`），
     整行底色改由 `drawRow()` 自己画一个**通栏圆角块**，选中/悬停都是一整块。
2. **留白过大**：默认行高 36px、每级缩进 20px，十来个节点散在面板里显得空。
   行高由 QSS 令牌收紧到 26px；缩进**只能写代码** —— QSS 里没有 `indentation`
   属性（实测报 "Unknown property indentation"），写了等于没写。
3. **计数不成列**：原来把数量内联在标题里（`标签（6）`），右半边空着、数字也对不齐。
   改成右侧胶囊徽章（`BADGE_ROLE`），与 `ui_kit.components.chip` 同款。

颜色全部取自 `ui_kit/tokens.py`（QSS 由同一套令牌生成），不新增调色板。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from modu_workbench.ui_kit.tokens import FONT, SPACE, TOKENS

#: 筛选条件（与 `widgets.ITEM_ROLE` 同值，同一个 UserRole 槽位）
PAYLOAD_ROLE = Qt.ItemDataRole.UserRole
#: 右侧胶囊徽章的文案（计数 / 周期），`None` 表示不画徽章
BADGE_ROLE = Qt.ItemDataRole.UserRole + 10
#: 纯分组标题（相册 / 时间 / 标签 / 来源）：不可点，弱化显示
GROUP_ROLE = Qt.ItemDataRole.UserRole + 11
#: 占位说明行（「还没有相册」）：不可点，比正文更淡
MUTED_ROLE = Qt.ItemDataRole.UserRole + 12

ROW_INSET = SPACE["xs"]        # 4：整行底色左右各内缩一点，与面板边框留出呼吸
ROW_MIN_HEIGHT = 26            # 行高下限（与 theme.py 树规则的 22+2×2 对齐）
GROUP_GAP = SPACE["sm"]        # 8：分组标题上方留白，把上一组和这一组分隔开
BADGE_HEIGHT = 20              # 胶囊高：比行高小 6px，上下各有 3px 呼吸
BADGE_PAD_X = SPACE["sm"]      # 8：胶囊左右内边距
BADGE_MIN_WIDTH = 28           # 一位数计数也保持等宽，数字才能对齐成一列
BADGE_GAP = SPACE["sm"]        # 8：胶囊与标题之间的最小间距


class CategoryNavDelegate(QStyledItemDelegate):
    """画每一行：左侧标题 + 右侧徽章，键盘焦点用 1px 强调色描边。

    行底色不在这里画（`QTreeWidget` 的整行通栏块由 `CategoryNavTree.drawRow()`
    负责，那边才拿得到包含分支列的整行矩形）；这里只负责内容。
    """

    def __init__(self, tree: "CategoryNavTree") -> None:
        super().__init__(tree)
        self._tree = tree

    def sizeHint(self, option, index):  # noqa: ANN001, ANN201, N802
        size = super().sizeHint(option, index)
        # 行高下限写在这里而不是只靠 QSS：没有样式表时基础 sizeHint 只有 13px
        # （实测），行会塌成一条线；有样式表时 QSS 的 min-height 也是同一个值。
        height = max(size.height(), ROW_MIN_HEIGHT)
        if index.data(GROUP_ROLE):
            height += GROUP_GAP
        size.setHeight(height)
        return size

    def paint(self, painter, option, index) -> None:  # noqa: ANN001, N802
        t = TOKENS
        group = bool(index.data(GROUP_ROLE))
        muted = bool(index.data(MUTED_ROLE))
        selected = self._tree.is_selected(index)

        # 先让样式（= QSS）画它负责的背景/分支；文本清空后由下面自己画
        # （标题左对齐 + 右侧计数胶囊，默认委托只会把整段文字画在左边）
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        opt.state &= ~QStyle.StateFlag.State_HasFocus      # 去掉 Fusion 的虚线焦点框
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

        row = QRectF(option.rect)
        if group:
            row.setTop(row.top() + GROUP_GAP)              # 多出来的高度留在标题上方

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        font = QFont(option.font)
        if group:
            font.setPixelSize(FONT["small"])
            font.setBold(True)
        painter.setFont(font)

        left = row.left() + SPACE["sm"]
        right = row.right() - ROW_INSET - SPACE["xs"]
        badge = self._badge_rect(painter, row, right, index)
        if badge is not None:
            right = badge.left() - BADGE_GAP

        text_rect = QRectF(left, row.top(), max(10.0, right - left), row.height())
        if group or muted:
            painter.setPen(QColor(t.text_dim if group else t.text_faint))
        else:
            painter.setPen(QColor(t.accent_strong if selected else t.text))
        label = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight,
                                             int(text_rect.width())),
        )
        if badge is not None:
            self._paint_badge(painter, badge, index, selected)
        painter.restore()

    def _badge_rect(self, painter: QPainter, row: QRectF, right: float,
                    index) -> QRectF | None:  # noqa: ANN001
        text = str(index.data(BADGE_ROLE) or "")
        if not text:
            return None
        width = max(BADGE_MIN_WIDTH,
                    painter.fontMetrics().horizontalAdvance(text) + 2 * BADGE_PAD_X)
        return QRectF(right - width, row.center().y() - BADGE_HEIGHT / 2, width, BADGE_HEIGHT)

    def _paint_badge(self, painter: QPainter, rect: QRectF, index,  # noqa: ANN001
                     selected: bool) -> None:
        t = TOKENS
        painter.setPen(Qt.PenStyle.NoPen)
        # 选中行的底已经是 accent_soft，徽章换成 surface 才看得出是一枚胶囊
        painter.setBrush(QColor(t.surface if selected else t.surface_2))
        painter.drawRoundedRect(rect, BADGE_HEIGHT / 2, BADGE_HEIGHT / 2)

        font = QFont(painter.font())
        font.setPixelSize(FONT["caption"])
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(t.accent_strong if selected else t.text_dim))
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter),
                         str(index.data(BADGE_ROLE) or ""))


class CategoryNavTree(QTreeWidget):
    """图库左侧分类导航树：紧凑行高、按令牌缩进、整行圆角选中态。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hover = None
        self.setObjectName("gallerySide")
        self.setHeaderHidden(True)
        # 缩进只能代码设置：QSS 没有 indentation 属性（写了报 Unknown property）
        self.setIndentation(SPACE["lg"])
        # 分组标题比普通行高 GROUP_GAP，所以不能开 uniformRowHeights
        self.setUniformRowHeights(False)
        self.setAnimated(False)
        self.setMouseTracking(True)
        # 标题超长就省略（悬停有 tooltip），避免出现横向滚动条把底部顶掉
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setItemDelegate(CategoryNavDelegate(self))

    # ------------------------------------------------------------ 节点

    def add_node(self, parent: QTreeWidgetItem | None, label: str, *, badge=None,
                 payload: dict | None = None, group: bool = False,
                 hint: str = "") -> QTreeWidgetItem:
        """新增节点。

        `label` 是可见标题（**不含**计数，计数走右侧徽章），
        `badge` 是徽章文案（计数 / 「7 天」），`payload` 是点击后要应用的筛选条件，
        `group=True` 表示纯分组标题（不可点、弱化显示）。
        """
        item = QTreeWidgetItem([label])
        if badge is not None:
            item.setData(0, BADGE_ROLE, str(badge))
        if group:
            item.setData(0, GROUP_ROLE, True)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        elif payload is not None:
            item.setData(0, PAYLOAD_ROLE, payload)
        else:
            item.setData(0, MUTED_ROLE, True)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)       # 占位说明行不可点
        if not hint:
            hint = f"{label}（{badge}）" if badge is not None else label
        item.setToolTip(0, hint)
        if parent is None:
            self.addTopLevelItem(item)
        else:
            parent.addChild(item)
        return item

    # ------------------------------------------------------------ 状态

    def is_selected(self, index) -> bool:  # noqa: ANN001
        model = self.selectionModel()
        return bool(model is not None and model.isSelected(index))

    # ------------------------------------------------------------ 行绘制

    def drawRow(self, painter, option, index) -> None:  # noqa: ANN001, N802
        """整行统一底色：默认实现会把选中态拆成「分支列方块 + 内容列圆角块」。"""
        tokens = TOKENS
        group = bool(index.data(GROUP_ROLE))
        selected = self.is_selected(index)
        hovered = self._hover is not None and self._hover == index

        opt = QStyleOptionViewItem(option)
        if not group and (selected or hovered):
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pill = QRectF(opt.rect).adjusted(ROW_INSET, 1, -ROW_INSET, -1)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(tokens.accent_soft if selected else tokens.surface_hover))
            painter.drawRoundedRect(pill, tokens.radius_sm, tokens.radius_sm)
            painter.restore()
        # 清掉状态，QSS 的 ::item:selected / :hover 就不会再叠一层色块
        opt.state &= ~QStyle.StateFlag.State_Selected
        opt.state &= ~QStyle.StateFlag.State_MouseOver
        opt.state &= ~QStyle.StateFlag.State_HasFocus
        super().drawRow(painter, opt, index)
        # 键盘焦点：1px 强调色描边取代默认虚线框（焦点可见，但不刺眼）
        if not group and self.hasFocus() and self.currentIndex() == index:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(tokens.accent), 1))
            painter.drawRoundedRect(
                QRectF(opt.rect).adjusted(ROW_INSET + 0.5, 0.5, -ROW_INSET - 0.5, -0.5),
                tokens.radius_sm, tokens.radius_sm)
            painter.restore()

    # ------------------------------------------------------------ 悬停

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001, N802
        index = self.indexAt(event.position().toPoint())
        self._set_hover(index if index.isValid() else None)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: ANN001, N802
        self._set_hover(None)
        super().leaveEvent(event)

    def viewportEvent(self, event) -> bool:  # noqa: ANN001, N802
        # 光标挪到滚动条/边框上时，控件本身没收到 Leave：这里补一次，
        # 否则悬停底色会一直留在那一行上
        if event.type() == QEvent.Type.Leave:
            self._set_hover(None)
        return super().viewportEvent(event)

    def _set_hover(self, index) -> None:  # noqa: ANN001
        if index != self._hover:
            self._hover = index
            self.viewport().update()


__all__ = [
    "BADGE_ROLE",
    "GROUP_GAP",
    "GROUP_ROLE",
    "MUTED_ROLE",
    "PAYLOAD_ROLE",
    "ROW_INSET",
    "ROW_MIN_HEIGHT",
    "CategoryNavDelegate",
    "CategoryNavTree",
]
