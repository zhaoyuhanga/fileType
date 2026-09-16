"""设计令牌（Design Tokens）——界面唯一事实来源。

规则（v1.0.0 设计规范，`docs/UI_GUIDE.md` 有完整说明）：

- **无直角**：所有可见容器/控件圆角 ≥ 10px（`radius_sm`），胶囊用 `radius_pill`；
- **间距刻度**：4 / 8 / 12 / 16 / 24 / 32 / 48，页面外边距 24、卡片内边距 16、控件间距 8~12；
- **字号刻度**：11 / 12 / 13 / 14 / 16 / 20 / 28，行高 1.6；
- **颜色只表达语义**：底色/表面/边框/文字/强调/成功/危险/警示/信息，
  浅色与深色主题共用同一套字段（`LIGHT` / `DARK`）。

本模块**不依赖 Qt**，方便测试与复用；QSS 生成见 `ui_kit/theme.py`，
布局辅助见 `ui_kit/components/layout.py`。
"""
from __future__ import annotations

from dataclasses import dataclass, fields, replace

# ---------------------------------------------------------------- 间距 / 圆角 / 字号

#: 间距刻度（px）：页面 24、区块之间 16~24、卡片内 16、控件之间 8~12
SPACE = {
    "none": 0,
    "xs": 4,
    "sm": 8,
    "md": 12,
    "lg": 16,
    "xl": 24,
    "xxl": 32,
    "huge": 48,
}

#: 页面级留白（所有板块统一，避免"有的页 12px、有的页 32px"）
PAGE_MARGIN = SPACE["xl"]          # 24
SECTION_GAP = SPACE["lg"]          # 16
CARD_PADDING = SPACE["lg"]         # 16
ROW_GAP = SPACE["sm"]              # 8

#: 字号刻度
FONT = {
    "caption": 11,
    "small": 12,
    "body": 13,
    "body_lg": 14,
    "subtitle": 16,
    "title": 20,
    "display": 28,
}

#: 行高倍数（正文可读性）
LINE_HEIGHT = 1.6


@dataclass(frozen=True)
class ThemeTokens:
    """一套主题的全部令牌（颜色 + 圆角；间距/字号见模块级常量）。"""

    name: str = "light"
    dark: bool = False

    # 外壳与表面
    shell_bg: str = "#eef1f7"
    surface: str = "#ffffff"
    surface_2: str = "#f6f8fc"
    surface_hover: str = "#f3f6fd"
    border: str = "#e3e8f2"
    border_strong: str = "#cdd6e8"
    # 文字
    text_hi: str = "#141b2e"
    text: str = "#2e3a52"
    text_dim: str = "#66728a"
    text_faint: str = "#97a2b8"
    # 强调与语义
    accent: str = "#4f5bd5"
    accent_hover: str = "#3e48bf"
    accent_strong: str = "#3a44ad"
    accent_soft: str = "#eef0fe"
    success: str = "#159a55"
    success_bg: str = "#e2f5ea"
    danger: str = "#dd4b4f"
    danger_bg: str = "#fdeaea"
    warn: str = "#b9790a"
    warn_bg: str = "#fbf1dd"
    info: str = "#2f6fdb"
    info_bg: str = "#e8f0fd"

    # 圆角（统一"无直角"：最小 10）
    radius_sm: int = 10
    radius: int = 12
    radius_lg: int = 16
    radius_pill: int = 999

    # 间距（保留在令牌里，便于按主题微调；默认取 SPACE 刻度）
    space_xs: int = SPACE["xs"]
    space_sm: int = SPACE["sm"]
    space_md: int = SPACE["md"]
    space_lg: int = SPACE["lg"]
    space_xl: int = SPACE["xl"]

    # 控件尺寸（统一行高，避免同屏控件高低不齐）
    control_height: int = 34
    row_height: int = 36
    header_height: int = 40

    def with_colors(self, **changes: str) -> "ThemeTokens":
        """基于当前令牌换色（深色主题/阅读主题用）。"""
        return replace(self, **changes)


#: 深色主题（同一套语义字段；阅读正文另有独立主题）
DARK = ThemeTokens(
    name="dark",
    dark=True,
    shell_bg="#14161d",
    surface="#1c1f28",
    surface_2="#232733",
    surface_hover="#262b38",
    border="#2c313d",
    border_strong="#3a4150",
    text_hi="#f2f4f9",
    text="#d8dce6",
    text_dim="#9aa3b4",
    text_faint="#6d7688",
    accent="#7b86ff",
    accent_hover="#8f99ff",
    accent_strong="#6a74e8",
    accent_soft="#252a44",
    success="#3fbf7f",
    success_bg="#1d2f26",
    danger="#ef6b6f",
    danger_bg="#33222a",
    warn="#d6a13c",
    warn_bg="#33291a",
    info="#5b93ee",
    info_bg="#1e2a3f",
)

#: 浅色主题（默认）
LIGHT = ThemeTokens()

TOKENS = LIGHT

#: 令牌字段名（文档与测试用）
TOKEN_FIELDS = tuple(field.name for field in fields(ThemeTokens))


def ensure_token(name: str) -> ThemeTokens:
    """按名字取主题令牌（未知名字回退浅色）。"""
    return {"light": LIGHT, "dark": DARK}.get(name, LIGHT)


__all__ = [
    "CARD_PADDING",
    "DARK",
    "FONT",
    "LIGHT",
    "LINE_HEIGHT",
    "PAGE_MARGIN",
    "ROW_GAP",
    "SECTION_GAP",
    "SPACE",
    "TOKENS",
    "TOKEN_FIELDS",
    "ThemeTokens",
    "ensure_token",
]
