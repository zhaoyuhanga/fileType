# 板块：墨软影视（`video`）

> 多源聚合搜索 + 在线播放 + 下载入库
> 子页：search / library / history / sources / player

## 1. 目录

**界面层（boards/video/）**

| 文件 | 行数 |
|---|---|
| `boards/video/board.py` | 666 |
| `boards/video/context.py` | 36 |
| `boards/video/detail.py` | 337 |
| `boards/video/history.py` | 253 |
| `boards/video/library_page.py` | 451 |
| `boards/video/player.py` | 585 |
| `boards/video/search.py` | 432 |
| `boards/video/sources.py` | 345 |
| `boards/video/widgets.py` | 553 |

**引擎层（core/video/）**

| 文件 | 行数 |
|---|---|
| `core/video/aes.py` | 142 |
| `core/video/downloader.py` | 769 |
| `core/video/hls.py` | 259 |
| `core/video/library.py` | 417 |
| `core/video/models.py` | 425 |
| `core/video/sources/base.py` | 192 |
| `core/video/sources/http.py` | 47 |
| `core/video/sources/matcher.py` | 92 |
| `core/video/sources/providers/cms_vod.py` | 462 |
| `core/video/sources/providers/public.py` | 337 |
| `core/video/sources/registry.py` | 410 |
| `core/video/storage.py` | 532 |

依赖规则：本板块只能引用 `boards/video/`、`core/video/`、`core/platform`、`core/llm`、`ui_kit`、`services`；
跨板块引用会被 `tests/architecture/test_architecture.py` 拦下。

## 2. 数据

数据库：单库 `modu.db`（详见 `docs/DATABASE.md`）。

- `video_videos`
- `video_playlists`
- `video_playlist_items`
- `video_history`
- `video_play_records`

应用级单例：`boards/video/context.py`（惰性创建；`app/context.py` 做跨板块聚合）。

## 3. 界面约定

- 页面骨架：`ColumnPage` + `SectionCard` + `EmptyState`（空列表必须给下一步操作）；
- 状态与进度：板块底部 `TaskBar`（空闲自动收起进度与取消）；页面用 `_report` / `_busy` / `_idle`；
- 留白/圆角/字号一律取 `ui_kit/tokens.py` 的令牌（见 `docs/UI_GUIDE.md`）。

## 4. 测试

| 文件 | 说明 |
|---|---|
| `tests/board_video/test_video_aes.py` | 板块引擎测试 |
| `tests/board_video/test_video_core.py` | 板块引擎测试 |
| `tests/board_video/test_video_integration.py` | 板块引擎测试 |
| `tests/board_video/test_video_ui.py` | 板块界面测试 |
| `tests/board_video/test_video_sources.py` | 内置源清单结构自检（离线，不探测接口存活） |

冒烟清单见 `docs/TESTING.md` 第 5 节。

## 4.1 内置采集源维护

采集源清单写在 `core/video/sources/providers/__init__.py` 的 `CMS_SITES`，顺序即聚合优先级
（`registry.search_all` 按「标题+年份」去重、保留先出现的源），`DEFAULT_PROVIDER_ORDER` 必须与它一一对应。

- **加源前必须人工实测**：`GET <base>/api.php/provide/vod?ac=detail&wd=关键词` 要返回 200 + JSON + 非空 `list`，
  且 `vod_play_url` 里的地址能真的拉到 m3u8/mp4（分享页要能解析出真实地址）；
  至少测两个关键词（一个电影 + 一部剧集），只测一个关键词不足以判死。
- **排序按实测码率**：同一集的分片大小 ÷ 分片时长可用于估算码率，
  码率高、线路全的站排前面，避免「只有 540P/很模糊」的线路抢占同名结果。
- **同一上游换域名的站只留一个**：这类站共用一套 CDN 与同名分片（分片数、分片大小完全相同），
  都留着只会让搜索重复请求同名结果。
- **失效即替换**：搜索接口返回 JSON 但播放地址全 404 的站点同样算失效（用户点播必失败），直接换成实测可用的站。
- 改完清单要同步 `tests/board_video/test_video_sources.py` 的结构约束，并递增 `core/video/sources/registry.py`
  的 `SOURCES_VERSION`（否则老用户的「启用集合」会把新源默认关掉）。

## 5. 变更须知

- 改数据结构：加迁移模块（`core/platform/migrations/`）并同步 `tests/platform/test_db_schema.py` 与 `docs/DATABASE.md`；
- 改界面：跑 `packaging/ui_snapshot.py --only video` 出图对比，再跑 `pytest tests/board_video tests/architecture -q`；
- 新增数据源：只在 `core/video/sources/providers/__init__.py` 注册（板块业务代码不需要改），见 4.1。
