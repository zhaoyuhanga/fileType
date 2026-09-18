# 界面设计规范（UI Guide，v1.0.0）

> 令牌唯一来源：`src/modu_workbench/ui_kit/tokens.py`
> QSS 生成：`src/modu_workbench/ui_kit/theme.py`
> 组件库：`src/modu_workbench/ui_kit/components/`
> 规范由测试锁定：`tests/architecture/test_ui_design_system.py`（改令牌/QSS 会被测试拦住）

## 1. 三条硬性规则

1. **无棱角**：所有可见容器与控件的圆角 ≥ 10px（`radius_sm`），胶囊状元素用 `radius_pill`；
   QSS 里不允许出现 `border-radius: 0`（测试会检查）。
2. **只按刻度取值**：间距只能用 `SPACE` 刻度，字号只能用 `FONT` 刻度；
   不允许在页面里随手写 `setContentsMargins(13, 7, 13, 7)` 这种数字。
3. **不留大块空白**：列表/网格为空必须显示 `EmptyState`（图标 + 标题 + 说明 + 主操作）；
   卡片墙最后一行如果有剩余宽度，让最后一张卡跨列铺满（首页就是这么做的）。

## 2. 令牌

| 类别 | 令牌 | 取值 |
|---|---|---|
| 间距 | `SPACE` | 0 / 4 / 8 / 12 / 16 / 24 / 32 / 48 |
| 页面留白 | `PAGE_MARGIN` = 24，`SECTION_GAP` = 16，`CARD_PADDING` = 16，`ROW_GAP` = 8 | — |
| 字号 | `FONT` | 11 / 12 / 13 / 14 / 16 / 20 / 28 |
| 行高 | `LINE_HEIGHT` | 1.6 |
| 圆角 | `radius_sm` / `radius` / `radius_lg` / `radius_pill` | 10 / 12 / 16 / 999 |
| 控件尺寸 | `control_height` / `row_height` / `header_height` | 34 / 36 / 40 |
| 颜色 | `shell_bg` `surface` `surface_2` `surface_hover` `border` `border_strong` `text_hi` `text` `text_dim` `text_faint` `accent(+hover/strong/soft)` `success(+bg)` `danger(+bg)` `warn(+bg)` `info(+bg)` | 浅色 `LIGHT` / 深色 `DARK` |

颜色**只表达语义**：页面底色用 `shell_bg`，卡片用 `surface`，次级面用 `surface_2`，
文字按重要性用 `text_hi → text → text_dim → text_faint`，强调用 `accent`。
禁止在页面里写死 `#ff0000` 这类颜色（阅读正文主题除外）。

## 3. 组件库

| 组件 | 用途 | 约定 |
|---|---|---|
| `ColumnPage` | 标准页面骨架 | 页边距 24、区块间距 16；`add()` 直接塞控件即可逐步迁移 |
| `PageHeader` | 标题 + 副标题 + 右侧操作 | 每页一个，别在页面里自己拼标题 |
| `SectionCard` | 白色圆角区块 | 内边距 16；`title` / `hint` / `actions` 可选 |
| `EmptyState` | 空列表占位 | 必须带"下一步做什么"的说明或按钮 |
| `Toolbar` | 搜索 + 筛选 + 主操作 | 固定在内容区上方，间距 8 |
| `chip` / `hint_label` / `divider` | 徽章 / 说明文字 / 分隔线 | 徽章胶囊形，说明文字小号灰 |
| `primary_button` / `ghost_button` / `link_button` | 三种按钮层级 | 一个页面最多一个主按钮 |

## 4. 页面模板

```python
from modu_workbench.ui_kit.components import ColumnPage, EmptyState, SectionCard, primary_button

class XxxPage(QWidget):
    def __init__(self, ...):
        super().__init__()
        self._page = ColumnPage("标题", "一句话定位", actions=[primary_button("新建")])
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._page)

        self._list_card = SectionCard("列表", actions=[chip("0 项")])
        self._page.add(self._list_card)
        self._empty = EmptyState("📄", "还没有内容", "导入或新建后显示在这里")
```

列表页的刷新逻辑：有数据 → 显示表格/网格；无数据 → 用 `EmptyState` 换掉列表控件
（不要在表格里留空白行）。

## 5. 控件必须逐类覆盖（"为什么我的界面有点丑"）

Qt Widgets 的默认外观来自 **QStyle（我们用 Fusion）**，QSS 只会改写**你写到的那部分**：
漏掉的控件或子控件会继续用 Fusion 绘制，于是同一屏里出现两套视觉 —— 这就是"样式有点丑"的根源。
（QML/Qt Quick Controls 之所以看起来现代，是因为它自带 Material/Basic 样式表；
换成 QML 等于把整套 Widgets UI 重写一遍，性价比不划算，QSS 补齐完全够用。）

每个"弹出类 / 复合控件"都必须写全这几条，`tests/architecture/test_ui_design_system.py`
里的 `test_qss_covers_popup_controls` / `test_combo_popup_uses_working_selectors` 会逐条检查：

| 控件 | 必须覆盖 | 不写的后果 |
|---|---|---|
| `QComboBox` | `::drop-down`（去边框/底色）、`::down-arrow`（**只能给 image**）、`padding-right` 预留箭头位 | 右侧多一个灰色按钮+竖分隔线；长文本压到箭头上；用 `border` 拼三角形会渲染成**小方块** |
| `QComboBox QAbstractItemView` | 背景、`border: none`（容器已有原生边框）、`padding`、`outline: 0` | 出现双层边 + 当前项一圈虚线框 |
| `QComboBox::item` / `:hover` / `:selected` | 文字色、hover 底色、选中底色 | 列表项沿用系统蓝高亮，与主题不搭 |
| `QSpinBox` | `::up-button`/`::down-button`（去边框）、`::up-arrow`/`::down-arrow` | 右侧是 2010 风格的立体箭头按钮 |
| `QCheckBox` / `QRadioButton` | `::indicator` 尺寸/圆角/边框 + `:checked` 的勾图/圆点 | 默认指示器又小又灰，勾选态是系统蓝 |
| `QMenu` | `::item`（内边距/圆角）、`::item:selected`、`::separator` | 右键菜单项贴边、选中是一整条系统蓝 |
| `QTreeView` | `show-decoration-selected: 0`、`::item`（行高/圆角/hover/selected）、`::branch`（箭头图标） | 分支列出现一块系统蓝方块（见下节） |
| `QTabWidget` | `::pane` + `QTabBar::tab` / `:selected` | 选项卡是立体凸起的老样式 |

### 下拉列表的选择器坑（Qt 6.11 实测，务必照抄）

这三条是踩出来的，写错会**又丑又卡**，测试已把它们钉死：

1. ❌ `QComboBox QAbstractItemView::item { … }`（后代选择器 + 子控件）**不匹配**，写了等于没写，
   列表项继续用 Fusion 默认外观；
2. ❌ `QComboBox::item { padding: … }` / `min-height` —— 会触发几何爆炸：
   实测行高算成 **1900px**、弹出层从 130px 涨到 **792px**（点一下就像卡死）；
3. ❌ `QComboBox QAbstractItemView { selection-background-color: … }` 对弹窗**无效**，
   选中态只能写在 `QComboBox::item:selected`。

✅ 正确写法（现在的主题就是这样）：

```css
QComboBox QAbstractItemView { background: …; border: none; padding: 4px; outline: 0; }
QComboBox::item { color: …; }
QComboBox::item:hover { background: …; }
QComboBox::item:selected { background: …; color: …; }
```

**也不要用**"弹出时改 window flags + `WA_TranslucentBackground`"那套去凑圆角：
在已创建的弹出窗口上改标志会**重建原生窗口**（Windows 上就是"点一下卡一下"），
全局事件过滤器还会让每个事件都回调进 Python。弹出层保留系统原生边框即可 —— 与 `QMenu` 一致。

箭头/勾选/圆点这类小图标不引二进制资源，而是在 `theme._icon_urls()` 里
**用 QPainter 现画成 PNG** 缓存到 `<数据目录>/cache/ui/`（颜色随主题，深浅色各自一份），
再以 `image: url(...)` 写进 QSS。`app_qss()` 在没有 GUI 应用时（纯单元测试）会跳过生成，
此时相关 `image:` 规则为空 —— 但控件几何（内边距/行高/圆角）依然生效。

### 分类树的坑（Qt 6.11 实测，图库左侧导航）

用户反馈过：「分类树空白有点大有点丑，且点击会有蓝色标记」。原因是三件事叠在一起，
逐像素对比离屏渲染后得到下面几条结论，`test_ui_design_system.py` 里的
`test_tree_rules_are_token_driven_and_kill_system_blue` /
`test_tree_selection_is_tinted_not_system_blue` 已把它们钉死：

1. ❌ **蓝色方块**来自「分支（缩进）列」：`show-decoration-selected: 1` 时 Fusion 会按
   系统高亮色把整列画成一个**方块**，而内容列由 QSS 画成圆角块 —— 拼起来就是
   「左边一块宝蓝、右边一块浅紫」的接缝（抓到的像素是 Fusion 的 `#308cc6`）。
2. ❌ `QTreeView::branch:selected { background: transparent; }` **不生效**：QSS 把
   `transparent` 当成"没写过这条规则"，继续退回系统高亮色（给具体颜色才生效，
   但分支列仍是方块、仍有接缝）。正解是 `show-decoration-selected: 0`，让控件
   根本不给分支列上色，整行底色改由控件自绘一个**通栏圆角块**
   （`boards/gallery/nav_tree.py` 的 `CategoryNavTree.drawRow()`）。
   自绘时要把 `State_Selected` / `State_MouseOver` 从 option 里清掉，
   否则 QSS 的 `::item:selected` 会在同一行再叠一层色块。
3. ❌ QSS 里**没有** `indentation` 属性：写了会报 `Unknown property indentation`，
   缩进只能代码设置（`setIndentation(SPACE["lg"])`）。默认缩进 20px + 默认行高
   36px（`min-height: 30` + `padding: 3px`）会让十来个节点散在面板里，很空。
4. ❌ 不要指望 `QTreeWidgetItem` 的默认 `sizeHint`：**没有样式表时它只有 13px**
   （实测），行会塌成一条线。导航树在 `CategoryNavDelegate.sizeHint()` 里
   自己兜了行高下限（26px），QSS 的 `min-height: 22 + padding: 2×2 = 26` 与它对齐。
5. ✅ 计数不要拼在标题里（`标签（6）`，右侧空一片、数字对不齐）：存到
   `BADGE_ROLE`，由委托画成右侧胶囊；标题超长就省略 + tooltip，这样也不需要
   横向滚动条（`ScrollBarAlwaysOff`）。
6. ✅ 键盘焦点仍然要可见：清掉 `State_HasFocus`（去掉 Fusion 虚线框）后，
   由控件自己画 1px 强调色圆角描边。

### 布局硬约束

- **不要用 QLabel 直接显示长路径**：QLabel 的 `minimumSizeHint` 会按整段文本算宽度，
  一条 `C:\Users\...\AppData\Roaming\ModuWorkbench\music` 能把设置页最小宽度顶到 900px+，
  于是设置窗口出现横向滚动条（`music` / `video` / `llm` 三页都踩过）。
  改用**只读 `QLineEdit`**（可横向滚动、最小宽度很小），完整路径放 tooltip。
- **复选框标签别写成长句子**：状态、接口地址这类细节放 tooltip，标签只留"名称（能力）"。
- 结果：`test_settings_pages_fit_default_width` 保证七个设置页在 980×700 下都没有横向滚动条。

## 6. 验收流程

```powershell
# 1) 跑规范测试
.venv\Scripts\python.exe -m pytest tests/architecture tests/smoke -q

# 2) 渲染截图（docs/ui/board-<key>.png），人眼过一遍排版
.venv\Scripts\python.exe packaging\ui_snapshot.py            # 全部板块
.venv\Scripts\python.exe packaging\ui_snapshot.py --only video
```

截图检查清单：
- 有没有直角？卡片/输入框/按钮/下拉是否统一圆角；
- **下拉框**：箭头是干净的 V 形？长文本有没有压到箭头？展开后的列表行高、hover、选中是否跟主题一致？
- **分类树**（图库左侧）：选中是整行一块主题浅紫胶囊（**不能有系统蓝方块**）？行高够紧凑、缩进不散？
  计数在右侧对齐成一列？折叠过的分组有没有被重建强行展开？
- 有没有某一块留白明显过大（例如一行只有一张卡、右侧空一半）；
- 空列表页是否给出了下一步操作；
- 同屏控件高度/间距是否一致（按钮、输入框、下拉是否齐平）；
- 设置窗口在默认尺寸下有没有横向滚动条。
