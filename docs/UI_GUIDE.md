# 界面设计规范（UI Guide，v1.0.0）

> 令牌唯一来源：`src/modu_workbench/ui_kit/tokens.py`
> QSS 生成：`src/modu_workbench/ui_kit/theme.py`
> 组件库：`src/modu_workbench/ui_kit/components/`
> 规范由测试锁定：`tests/test_ui_design_system.py`（改令牌/组件会被测试拦住）

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

## 5. 验收流程

```powershell
# 1) 跑规范测试
.venv\Scripts\python.exe -m pytest tests/test_ui_design_system.py -q

# 2) 渲染截图（docs/ui/board-<key>.png），人眼过一遍排版
.venv\Scripts\python.exe packaging\ui_snapshot.py            # 全部板块
.venv\Scripts\python.exe packaging\ui_snapshot.py --only video
```

截图检查清单：
- 有没有直角？卡片/输入框/按钮是否统一圆角；
- 有没有某一块留白明显过大（例如一行只有一张卡、右侧空一半）；
- 空列表页是否给出了下一步操作；
- 同屏控件高度/间距是否一致（按钮、输入框、下拉是否齐平）。
