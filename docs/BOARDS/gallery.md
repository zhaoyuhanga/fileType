# 板块：墨软图库（`gallery`）

> 本地相册：浏览 / 分类 / 美化 / 本地 AI 增强
> 子页：browse / viewer / editor / enhance

## 1. 目录

**界面层（boards/gallery/）**

| 文件 | 行数 |
|---|---|
| `boards/gallery/board.py` | 1405 |
| `boards/gallery/context.py` | 23 |
| `boards/gallery/editor.py` | 641 |
| `boards/gallery/enhance.py` | 466 |
| `boards/gallery/widgets.py` | 929 |

**引擎层（core/gallery/）**

| 文件 | 行数 |
|---|---|
| `core/gallery/ai.py` | 328 |
| `core/gallery/edits.py` | 403 |
| `core/gallery/enhance.py` | 465 |
| `core/gallery/exif.py` | 207 |
| `core/gallery/hashing.py` | 76 |
| `core/gallery/library.py` | 677 |
| `core/gallery/models.py` | 308 |
| `core/gallery/storage.py` | 655 |
| `core/gallery/thumbs.py` | 219 |

依赖规则：本板块只能引用 `boards/gallery/`、`core/gallery/`、`core/platform`、`core/llm`、`ui_kit`、`services`；
跨板块引用会被 `tests/architecture/test_architecture.py` 拦下。

## 2. 数据

数据库：单库 `modu.db`（详见 `docs/DATABASE.md`）。

- `gallery_images`
- `gallery_albums`
- `gallery_album_items`
- `gallery_tags`
- `gallery_image_tags`
- `gallery_edits`
- `gallery_ai_tasks`

应用级单例：`boards/gallery/context.py`（惰性创建；`app/context.py` 做跨板块聚合）。

## 3. 界面约定

- 页面骨架：`ColumnPage` + `SectionCard` + `EmptyState`（空列表必须给下一步操作）；
- 状态与进度：板块底部 `TaskBar`（空闲自动收起进度与取消）；页面用 `_report` / `_busy` / `_idle`；
- 留白/圆角/字号一律取 `ui_kit/tokens.py` 的令牌（见 `docs/UI_GUIDE.md`）。

## 4. 测试

| 文件 | 说明 |
|---|---|
| `tests/board_gallery/test_gallery_editor.py` | 板块引擎测试 |
| `tests/board_gallery/test_gallery_enhance.py` | 板块引擎测试 |
| `tests/board_gallery/test_gallery_ui.py` | 板块界面测试 |
| `tests/board_gallery/test_image_core.py` | 板块引擎测试 |

冒烟清单见 `docs/TESTING.md` 第 5 节。

## 5. 变更须知

- 改数据结构：加迁移模块（`core/platform/migrations/`）并同步 `tests/platform/test_db_schema.py` 与 `docs/DATABASE.md`；
- 改界面：跑 `packaging/ui_snapshot.py --only gallery` 出图对比，再跑 `pytest tests/board_gallery tests/architecture -q`；
- 新增数据源：只在 `core/gallery/sources/providers/__init__.py` 注册（板块业务代码不需要改）。
