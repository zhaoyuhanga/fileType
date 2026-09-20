# 数据库设计与迁移（v1.0.0）

> 结构定义在代码里：`src/modu_workbench/core/platform/migrations/v1_initial.py`。
> 本文是"给人看"的同一份设计（评审用），两者由 `tests/test_db_schema.py` 的结构快照测试绑定 ——
> 改表必须同时改测试与本文，不会悄悄漂移。

## 1. 总体决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 库数量 | **单库** `modu.db` | 备份/迁移只需复制一个文件；跨板块（历史/统计/收藏）可联表查询 |
| 隔离方式 | **板块前缀分表**（`book_`/`music_`/`video_`/`gallery_`/`doc_`/`llm_`） | 单库仍能一眼看出归属；板块删除/搬迁互不影响 |
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
# 1) core/platform/migrations/v2_xxx.py：VERSION / NOTE / apply(conn)
# 2) 在 migrations/__init__.py 的 MIGRATIONS 注册
# 3) tests/test_db_schema.py + docs/DATABASE.md 同步
```

已发布迁移：`v1_initial`（单库初始结构）、`v2_document`（墨软文档板块的 `doc_*` 五张表，
纯增量、可重复执行、不需要数据搬迁）。

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
