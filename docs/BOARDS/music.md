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
| `core/music/sources/providers/kuwo.py` | 175 |
| `core/music/sources/providers/netease.py` | 196 |
| `core/music/sources/quality.py` | 150 |
| `core/music/sources/registry.py` | 292 |
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
| `tests/board_music/test_music_quality.py` | 试听/片段过滤：判定规则、排序、酷我解析、换源拿完整版、界面隐藏 |

冒烟清单见 `docs/TESTING.md` 第 5 节。

## 4.1 试听/片段过滤（`core/music/sources/quality.py`）

酷我等"完整曲目音源"会把同一首歌的**片段/串烧/铃声**一起返回（实测 20 条里 6~7 条只有
10~42 秒），点下载还常遇到平台限制（"当前歌曲只能在酷我手机端播放"）。处理原则：

- 判定纯本地：时长 `< min_full_seconds()`（默认 45，可配置）、标题含"片段/铃声/试听"、
  音源标注、试听型音源（iTunes）；**"Demo/伴奏/现场"不算片段**；
- 排序：`sort_full_first()` 让完整曲目在前（跨音源 `search_all` 统一处理）；
- 隐藏：界面默认隐藏 `should_hide()` 为真的条目（只隐藏"混在完整音源里的片段"，
  试听型音源的结果保留，因为它名字里就写着试听）；
- 下载：`downloader._download_resolved` 对片段**直接拒绝且不发请求**，交给
  `download_track` 的换源逻辑去找完整版；换源匹配 `registry.resolve` 优先挑完整版本。
- 阈值来源：设置 → 乐库（`boards/music/widgets.py` 的 `apply_quality_prefs()` 在页面初始化时推给核心）。

## 5. 变更须知

- 改数据结构：加迁移模块（`core/platform/migrations/`）并同步 `tests/platform/test_db_schema.py` 与 `docs/DATABASE.md`；
- 改界面：跑 `packaging/ui_snapshot.py --only music` 出图对比，再跑 `pytest tests/board_music tests/architecture -q`；
- 新增数据源：只在 `core/music/sources/providers/__init__.py` 注册（板块业务代码不需要改）。
