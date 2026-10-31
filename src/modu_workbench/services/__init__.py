"""本地服务与外部只读服务客户端（横切能力，各板块共用）。

- `media_proxy/`：本机媒体代理（影视播放/下载的分片、缓存、防盗链）**唯一实现**；
- `dandanplay.py`：弹弹play 开放弹幕网络的只读客户端（搜索/匹配/拉取/缓存）。

v2.0.0 起 `dandanplay` 从 `core/anime/danmaku.py` 迁到这里：弹幕能力属于
「墨软影视」，不再挂在已移除的动漫板块下。
原来的 `media_server.py` 转发壳已删除（同名函数曾在新旧两处各一份），
调用方统一 `from modu_workbench.services import media_proxy`。
"""
