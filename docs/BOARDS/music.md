# 板块：墨软乐库（`music`）

> 在线搜索下载 + 本地曲库 + 常驻播放条
> 子页：search / library / playlist / convert / history

## 1. 目录

**界面层（boards/music/）**

| 文件 | 行数 |
|---|---|
| `boards/music/board.py` | 381 |
| `boards/music/context.py` | 46 |
| `boards/music/history.py` | 163 |
| `boards/music/library_page.py` | 603 |
| `boards/music/playlist.py` | 455 |
| `boards/music/search.py` | 522 |
| `boards/music/widgets.py` | 433 |

**引擎层（core/music/）**

| 文件 | 行数 |
|---|---|
| `core/music/downloader.py` | 316 |
| `core/music/library.py` | 240 |
| `core/music/models.py` | 154 |
| `core/music/player.py` | 365 |
| `core/music/sources/base.py` | 138 |
| `core/music/sources/http.py` | 46 |
| `core/music/sources/matcher.py` | 95 |
| `core/music/sources/providers/archive_org.py` | 85 |
| `core/music/sources/providers/audius.py` | 77 |
| `core/music/sources/providers/ccmixter.py` | 75 |
| `core/music/sources/providers/direct.py` | 32 |
| `core/music/sources/providers/itunes.py` | 70 |
| `core/music/sources/providers/jamendo.py` | 75 |
| `core/music/sources/providers/kuwo.py` | 122 |
| `core/music/sources/providers/netease.py` | 196 |
| `core/music/sources/registry.py` | 286 |
| `core/music/storage.py` | 436 |

依赖规则：本板块只能引用 `boards/music/`、`core/music/`、`core/platform`、`core/llm`、`ui_kit`、`services`；
跨板块引用会被 `tests/architecture/test_architecture.py` 拦下。

## 2. 数据

数据库：单库 `modu.db`（详见 `docs/DATABASE.md`）。

- `music_tracks`
- `music_playlists`
- `music_playlist_items`
- `music_history`
- `music_playlist_remotes`

应用级单例：`boards/music/context.py`（惰性创建；`app/context.py` 做跨板块聚合）。

## 3. 界面约定

- 页面骨架：`ColumnPage` + `SectionCard` + `EmptyState`（空列表必须给下一步操作）；
- 状态与进度：板块底部 `TaskBar`（空闲自动收起进度与取消）；页面用 `_report` / `_busy` / `_idle`；
- 留白/圆角/字号一律取 `ui_kit/tokens.py` 的令牌（见 `docs/UI_GUIDE.md`）。

## 4. 测试

| 文件 | 说明 |
|---|---|
| `tests/board_music/test_music_core.py` | 板块引擎测试 |
| `tests/board_music/test_music_ui.py` | 板块界面测试 |

冒烟清单见 `docs/TESTING.md` 第 5 节。

## 5. 变更须知

- 改数据结构：加迁移模块（`core/platform/migrations/`）并同步 `tests/platform/test_db_schema.py` 与 `docs/DATABASE.md`；
- 改界面：跑 `packaging/ui_snapshot.py --only music` 出图对比，再跑 `pytest tests/board_music tests/architecture -q`；
- 新增数据源：只在 `core/music/sources/providers/__init__.py` 注册（板块业务代码不需要改）。
