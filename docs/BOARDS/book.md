# 板块：墨软书库（`book`）

> 本地 TXT / EPUB 阅读器 + 在线书库下载
> 子页：shelf / online / reader

## 1. 目录

**界面层（boards/book/）**

| 文件 | 行数 |
|---|---|
| `boards/book/board.py` | 123 |
| `boards/book/context.py` | 22 |
| `boards/book/online.py` | 257 |
| `boards/book/reader.py` | 271 |
| `boards/book/shelf.py` | 236 |

**引擎层（core/book/）**

| 文件 | 行数 |
|---|---|
| `core/book/files.py` | 24 |
| `core/book/library.py` | 69 |
| `core/book/online.py` | 535 |
| `core/book/parser.py` | 625 |
| `core/book/storage.py` | 190 |

依赖规则：本板块只能引用 `boards/book/`、`core/book/`、`core/platform`、`core/llm`、`ui_kit`、`services`；
跨板块引用会被 `tests/architecture/test_architecture.py` 拦下。

## 2. 数据

数据库：单库 `modu.db`（详见 `docs/DATABASE.md`）。

- `book_books`
- `book_history`

应用级单例：`boards/book/context.py`（惰性创建；`app/context.py` 做跨板块聚合）。

## 3. 界面约定

- 页面骨架：`ColumnPage` + `SectionCard` + `EmptyState`（空列表必须给下一步操作）；
- 状态与进度：板块底部 `TaskBar`（空闲自动收起进度与取消）；页面用 `_report` / `_busy` / `_idle`；
- 留白/圆角/字号一律取 `ui_kit/tokens.py` 的令牌（见 `docs/UI_GUIDE.md`）。

## 4. 测试

| 文件 | 说明 |
|---|---|
| `tests/board_book/test_book_ui.py` | 板块界面测试 |
| `tests/board_book/test_reader_core.py` | 板块引擎测试 |
| `tests/board_book/test_reader_parser.py` | 板块引擎测试 |

冒烟清单见 `docs/TESTING.md` 第 5 节。

## 5. 变更须知

- 改数据结构：加迁移模块（`core/platform/migrations/`）并同步 `tests/platform/test_db_schema.py` 与 `docs/DATABASE.md`；
- 改界面：跑 `packaging/ui_snapshot.py --only book` 出图对比，再跑 `pytest tests/board_book tests/architecture -q`；
- 新增数据源：只在 `core/book/sources/providers/__init__.py` 注册（板块业务代码不需要改）。
