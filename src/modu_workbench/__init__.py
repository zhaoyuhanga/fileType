"""墨软·工作台（Modu Workbench）—— 本地离线的一站式阅读、转换与媒体工作台。"""

__version__ = "1.1.0"
APP_NAME = "墨软·工作台"
#: 顶栏副标题 / 主窗口标题 / 首页说明都用它：**必须与 `ACTIVE_BOARDS` 的板块数一致**。
#: 曾经漏掉「文档」「动漫」（第七板块加入后标题仍写着五个板块）——
#: 因此 `tests/architecture/test_ui_design_system.py` 里有一条断言盯着"每个板块名都在副标题里"。
APP_SLOGAN = "本地离线 · 阅读 · 转换 · 音乐 · 影视 · 图库 · 文档 · 动漫"
