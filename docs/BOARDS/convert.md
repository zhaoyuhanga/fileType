# 板块：墨软转换（`convert`）

> 本地离线格式转换：文本 / 文档 / 表格 / 图片 / 媒体 / 归档
> 子页：board（单一页面）

## 1. 目录

**界面层（boards/convert/）**

| 文件 | 行数 |
|---|---|
| `boards/convert/board.py` | 436 |
| `boards/convert/doc_viewer.py` | 500 |

**引擎层（core/convert/）**

| 文件 | 行数 |
|---|---|
| `core/convert/archive_io.py` | 97 |
| `core/convert/engine.py` | 211 |
| `core/convert/formats.py` | 56 |
| `core/convert/image_io.py` | 39 |
| `core/convert/media_io.py` | 136 |
| `core/convert/office_io.py` | 115 |
| `core/convert/pdf_out.py` | 33 |
| `core/convert/registry.py` | 152 |
| `core/convert/sheet_io.py` | 72 |
| `core/convert/text_io.py` | 50 |

依赖规则：本板块只能引用 `boards/convert/`、`core/convert/`、`core/platform`、`core/llm`、`ui_kit`、`services`；
跨板块引用会被 `tests/architecture/test_architecture.py` 拦下。

## 2. 数据

数据库：单库 `modu.db`（详见 `docs/DATABASE.md`）。

（该板块没有独立数据表）

应用级单例：`boards/convert/context.py`（惰性创建；`app/context.py` 做跨板块聚合）。

## 3. 界面约定

- 页面骨架：`ColumnPage` + `SectionCard` + `EmptyState`（空列表必须给下一步操作）；
- 状态与进度：板块底部 `TaskBar`（空闲自动收起进度与取消）；页面用 `_report` / `_busy` / `_idle`；
- 留白/圆角/字号一律取 `ui_kit/tokens.py` 的令牌（见 `docs/UI_GUIDE.md`）。

## 4. 测试

| 文件 | 说明 |
|---|---|
| `tests/board_convert/test_convert_advanced.py` | 板块引擎测试 |
| `tests/board_convert/test_convert_core.py` | 板块引擎测试 |
| `tests/board_convert/test_convert_files.py` | 板块引擎测试 |
| `tests/board_convert/test_convert_ui.py` | 板块界面测试 |
| `tests/board_convert/test_doc_preview.py` | 板块引擎测试 |

冒烟清单见 `docs/TESTING.md` 第 5 节。

## 5. 变更须知

- 改数据结构：加迁移模块（`core/platform/migrations/`）并同步 `tests/platform/test_db_schema.py` 与 `docs/DATABASE.md`；
- 改界面：跑 `packaging/ui_snapshot.py --only convert` 出图对比，再跑 `pytest tests/board_convert tests/architecture -q`；
- 新增数据源：只在 `core/convert/sources/providers/__init__.py` 注册（板块业务代码不需要改）。
