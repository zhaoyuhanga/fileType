# 数据库设计与迁移（v1.0.0）

> 结构定义在代码里：`src/modu_workbench/core/platform/migrations/v1_initial.py`。
> 本文是"给人看"的同一份设计（评审用），两者由 `tests/test_db_schema.py` 的结构快照测试绑定 ——
> 改表必须同时改测试与本文，不会悄悄漂移。

## 1. 总体决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 库数量 | **单库** `modu.db` | 备份/迁移只需复制一个文件；跨板块（历史/统计/收藏）可联表查询 |
| 隔离方式 | **板块前缀分表**（`book_`/`music_`/`video_`/`gallery_`/`doc_`/`anime_`/`llm_`） | 单库仍能一眼看出归属；板块删除/搬迁互不影响 |
| 设置 | 统一 `app_settings`，键带命名空间（`music/sources/enabled`） | 免去每个板块各建一张 key/value 表；板块读写由 `settings_namespace` 自动加前缀 |
| 结构演进 | 版本化迁移（`schema_migrations`） | 老用户升级可平滑；`CREATE TABLE IF NOT EXISTS` 无法改列 |
| 旧数据 | 首次启动自动导入 5 个旧分库，旧文件改名 `*.imported.bak` | 不删用户数据，可人工回退 |
| 时间字段 | 一律 `INTEGER`（epoch 秒） | 与 stdlib `sqlite3` + Python `time` 对齐，无时区歧义 |
| 主键 | `INTEGER PRIMARY KEY AUTOINCREMENT` | 行号稳定，便于历史/收藏长期引用 |
| 外键 | 显式声明 + `PRAGMA foreign_keys=ON` | 删除图片/视频/书时级联清理关联行 |

数据位置：`%APPDATA%\ModuWorkbench\modu.db`（`MODU_DATA_DIR` 可覆盖，媒体文件在 `music/` `video/` `gallery/`，文档板块的产出与兜底目录在 `document/`）。
连接参数：`journal_mode=WAL`、`foreign_keys=ON`，每个板块持有一条连接（`core/platform/db.SqliteStore`）。

## 2. 平台表

| 表 | 字段 | 说明 |
|---|---|---|
| `schema_migrations` | `version` PK, `applied_at`, `note` | 已应用的迁移版本（幂等执行的依据） |
| `app_settings` | `key` PK, `value`, `updated_at` | 全应用设置；键 = `<板块命名空间>/<原键>`，如 `video/sources/enabled` |

## 3. 各板块表

`*` = NOT NULL（`=x` 表示默认值）。完整列定义以迁移文件为准。

### 墨软书库（`book_`）

| 表 | 关键字段 |
|---|---|
| `book_books` | `path`(唯一) `title` `author` `format` `added_at` `last_opened_at` `last_chapter_index` `last_offset_in_chapter` `last_progress` |
| `book_history` | `book_id`→`book_books` 级联、`chapter_index` `chapter_title` `opened_at` `action`；索引 `idx_book_history_book` |

### 墨软乐库（`music_`）

| 表 | 关键字段 |
|---|---|
| `music_tracks` | `path`(唯一) `title` `artist` `album` `duration_ms` `format` `source` `remote_id` `cover_path` `category` `favorited` `play_count`；索引 `idx_music_track_artist` |
| `music_playlists` | `name`(唯一) `kind`(`playlist`/`favorite`) `created_at` |
| `music_playlist_items` | `playlist_id`+`track_id` 复合主键、`position` `added_at` |
| `music_playlist_remotes` | 歌单内"待下载"的在线曲目快照（`playlist_id` `remote_key` `source` `remote_id` `title` `artist` `duration_ms` `url` …） |
| `music_history` | `track_id` `action` `played_at`；索引 `idx_music_history_time` |

### 墨软影视（`video_`）

| 表 | 关键字段 |
|---|---|
| `video_videos` | `file_path` `title` `kind` `series_key` `season` `episode_index` `episode_label` `year` `actors` `director` `duration_ms` `format` `source` `remote_id` `quality` `favorited` `play_count`；索引 `idx_video_series` `idx_video_kind` `idx_video_time` |
| `video_playlists` | `name`(唯一) `kind`(`category`/`favorite`) `created_at` |
| `video_playlist_items` | `playlist_id`+`video_id` 复合主键、`position` |
| `video_history` | `video_id` `action` `played_at` `episode_label` `quality` `source` `title_snapshot`（删条目后历史仍可读）；索引 `idx_video_history_time` |
| `video_play_records` | 续播记录：`video_id`+`episode_label` 主键、`url` `source` `updated_at` |

### 墨软图库（`gallery_`）

| 表 | 关键字段 |
|---|---|
| `gallery_images` | `path`(部分唯一索引) `url` `thumb_path` `width/height` `taken_at` `added_at` `exif_json` `gps_lat/lon` `camera` `source` `sha256` `dhash` `favorited` `ai_tags` `ai_caption`；索引 `idx_gallery_images_sha/dhash/taken/added/camera/path` |
| `gallery_albums` | `name`(唯一) `cover_path` `kind`(`album`/`favorite`) |
| `gallery_album_items` | `album_id`+`image_id` 复合主键、`position` |
| `gallery_tags` / `gallery_image_tags` | 标签与多对多关系（`use_count` 计数） |
| `gallery_edits` | 非破坏性编辑步骤栈 `steps_json` |
| `gallery_ai_tasks` | AI 任务队列：`kind` `state` `progress` `result_path` `message` |

### 墨软文档（`doc_`，迁移 v2）

| 表 | 关键字段 |
|---|---|
| `doc_documents` | `path`(唯一) `title` `format` `category` `size_bytes` `blocks` `words` `pages` `favorited` `tags` `meta_json` `added_at` `opened_at` `edited_at`；索引 `idx_doc_documents_opened` |
| `doc_versions` | **版本快照**：`document_id` `path` `label` `kind`(`open`/`save`/`beautify`/`merge`/`loop`/`manual`) `round_index` `note` `ir_json`（`DocumentIR` 的 JSON）`created_at`；索引 `idx_doc_versions_doc`；每篇默认保留 40 个 |
| `doc_merge_reports` | 两个文档合并的报告：`main_path` `other_path` `mode` `output_path` `summary` `report_json` `ai_used` `created_at`；索引 `idx_doc_merge_time` |
| `doc_ai_calls` | AI 调用日志：`document_id` `kind` `role` `model` `prompt_preview` `output_preview` `tools` `ok` `detail` `duration_ms` `est_cost` `masked` `created_at`；索引 `idx_doc_ai_time` |
| `doc_audit` | 审计日志：`action`（open/edit/export/beautify/merge/split/loop/rollback/mask/ocr…）`target` `detail` `actor` `created_at`；索引 `idx_doc_audit_time` |

> 板块设置（输出目录、默认模板、AI 权限、脱敏规则、循环上限）走 `app_settings` 的 `document/` 命名空间，
> 不单独建表 —— 与其它板块一致。

### 墨软动漫（`anime_`，迁移 v3）

| 表 | 关键字段 |
|---|---|
| `anime_rules` | 规则**索引**（规则 JSON 文件是唯一真相）：`key`(唯一) `name` `version` `kind`(`xpath`/`api`) `path` `builtin` `enabled` `source_url` `test_state` `test_note` `updated_at`；文件消失时把 `path` 清空并置 `test_state='missing'`（**不删行**，保住用户的启用状态与试跑记录） |
| `anime_series` | 番剧库：`rule_key`+`remote_id`(复合唯一，同一作品在不同规则下是两行) `title` `title_norm` `cover` `year` `summary` `bangumi_id` `total_episodes` `latest_episode` `air_weekday`(-1 未知) `created_at` `updated_at`；索引 `idx_anime_series_rule` |
| `anime_favorites` | 追番：`series_id`(唯一) `note` `created_at`；`ON CONFLICT DO UPDATE` 只更新备注（重复追番是幂等的，`created_at` 不变） |
| `anime_progress` | 按集进度：`series_id`+`episode_key` 复合唯一、`episode_name` `episode_index` `position_ms` `duration_ms` `watched` `updated_at`；索引 `idx_anime_progress_time`；播放器每 5 秒上报一次，唯一约束保证幂等 |
| `anime_history` | 播放历史（**快照式**：自带 `title`/`episode_name`，番剧行删掉后仍可读）：`series_id` `rule_key` `title` `episode_name` `source` `played_at`；索引 `idx_anime_history_time` |
| `anime_danmaku` | 弹幕**索引**（弹幕本体是磁盘 xml/json）：`series_id`+`episode_key`+`provider` 复合唯一、`episode_id` `count` `path` `fetched_at` |
| `anime_download_tasks` | 下载任务：`series_id`+`episode_key` 去重、`episode_name` `rule_key` `url` `dest_path` `parts_dir` `total_parts` `done_parts` `state`(`pending`/`running`/`paused`/`failed`/`done`) `message` `updated_at`；索引 `idx_anime_download_state` |

> 动漫**刻意不建外键**：番剧行的 `remote_id` 来自站点（换规则会重建），级联删除会在"清规则索引"
> 时连带删掉用户追番与进度；清理统一走 `AnimeStorage.delete_series()`（显式删追番/进度/弹幕索引，
> 保留历史与下载任务）。
>
> 板块设置（启用的规则、订阅清单 `anime/rule_subscriptions`）走 `app_settings` 的 `anime/` 命名空间。
> 追番进度导出/导入为 JSON（`AnimeStorage.export_payload()`/`import_payload()`），
> 只导出**用户资产**（番剧 + 追番 + 进度 + 启用规则 + 订阅），不导出历史与下载任务
> （后者绑定本机文件路径，导到别的机器只会变成失败任务）。

### 大模型（`llm_`）

| 表 | 关键字段 |
|---|---|
| `llm_profiles` | `kind`(`text`/`image`/`video`) `provider` `base_url` `api_key` `model` `vision_model` `temperature` `max_tokens` `priority` `enabled` |
| `llm_calls` | 调用记录：`kind` `profile_id` `ok` `detail` `created_at`（界面展示降级链） |

## 4. 迁移与旧数据导入

```python
# 结构迁移：开库时自动执行（幂等）
from modu_workbench.core.platform.db import SqliteStore
store = SqliteStore(app_db_path())          # 自动 migrate 到最新版本

# 新增一次结构变更
# 1) core/platform/migrations/v3_xxx.py：VERSION / NOTE / apply(conn)
# 2) 在 migrations/__init__.py 的 MIGRATIONS 注册
# 3) tests/platform/test_db_schema.py + docs/DATABASE.md 同步
```

已发布迁移：`v1_initial`（单库初始结构）、`v2_document`（墨软文档板块的 `doc_*` 五张表，
纯增量、可重复执行、不需要数据搬迁）、`v3_anime`（墨软动漫板块的 `anime_*` 七张表 + 四个索引，
同样是纯增量：`core/anime/*` 的规则文件、弹幕缓存都在文件系统里，DB 只放索引与用户资产）。

> **v1.1.0 没有新增表/迁移**：影视板块这一版新增的运行时数据都**不落库** ——
> 分片磁盘缓存在文件系统里（`数据目录/video/cache/<sha1 前2位>/<sha1>.bin` + 同名 `.meta`，
> 设置页「缓存→清空分片缓存」即删除，随用随重建），
> 新增的播放/下载参数走 `app_settings` 的 `video/*` 键（无需 DDL）。
> 计划里的 `video_download_tasks` / `video_play_events` 两张表属于后续阶段，届时再加迁移。

旧分库导入（`core/platform/legacy.py`，首次启动执行一次）：

1. `%APPDATA%\WinEBook\library.db`（Electron 时代书库）先复制为待导入的 `library.db`；
2. 依次把 `library.db` / `music.db` / `video.db` / `gallery.db` / `llm.db` 的业务表
   按**列名对齐**导入对应前缀表（旧库缺列时自动补 `0`/`''`，避免 NOT NULL 失败）；
3. 旧 `settings` / `llm_settings` 表并入 `app_settings`（加板块命名空间前缀）；
4. 导入完成后旧文件改名 `*.imported.bak`，并在 `app_settings` 记 `platform/legacy_imported=1`。

失败策略：单个旧库失败只记日志、不阻断启动；`tests/test_db_migration.py` 用真实旧结构样本演练全流程。

## 5. 维护约定

- 业务表一律带板块前缀；索引名一律 `idx_<板块>_…`（SQLite 索引名全局唯一，重名会静默失败）。
- 时间字段用 `INTEGER`（epoch 秒），字段名以 `_at` 结尾；新增表必须有 `created_at` 或等价时间列。
- 去重键写进约束（`UNIQUE`/复合主键），不要只靠代码判断。
- 结构变更走迁移文件，**已发布的迁移模块不得修改**。
- 备份 = 复制 `modu.db`（WAL 模式下同时复制 `-wal`/`-shm`，或先关闭应用）。
