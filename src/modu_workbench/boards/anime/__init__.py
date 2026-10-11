"""墨软动漫板块（第七大板块）：规则驱动采集 / 番剧库 / 时间表 / 规则 / 历史。

与第六板块「墨软影视」的边界（详见 `docs/VIDEO_ANIME_PLAN.md` 4.0）：

- 内容来源是**规则驱动的番剧站采集**（XPath / JSON API 规则），不是苹果 CMS 接口；
- 核心资产是**规则库 + 追番 + 弹幕 + Bangumi 元数据**；
- 播放器/代理/分片下载**复用共享层**（`ui_kit/media/`、`services/media_proxy/`），
  因此本包不 import `boards.video`，也不 import `core.video`。
"""
