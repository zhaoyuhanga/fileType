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

冒烟清单见 `docs/TESTING.md` 第 5 节。

## 5. 变更须知

- 改数据结构：加迁移模块（`core/platform/migrations/`）并同步 `tests/platform/test_db_schema.py` 与 `docs/DATABASE.md`；
- 改界面：跑 `packaging/ui_snapshot.py --only video` 出图对比，再跑 `pytest tests/board_video tests/architecture -q`；
- 新增数据源：只在 `core/video/sources/providers/__init__.py` 注册（板块业务代码不需要改）。
