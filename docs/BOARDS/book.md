# 板块：墨软书库（`book`）

> 本地 TXT / EPUB 阅读器 + 在线书库下载
> 子页：`shelf`（书架）/ `online`（在线搜索下载）/ `reader`（阅读器）

## 1. 目录与规模

**界面层（`boards/book/`，927 行）**

| 文件 | 行数 | 职责 |
|---|---|---|
| `boards/book/board.py` | 123 | 板块入口：`QStackedWidget` 装三个子页 + 顶栏切换 |
| `boards/book/shelf.py` | 236 | 书架列表、导入、删除、打开 |
| `boards/book/reader.py` | 283 | 阅读器：章节切换、阅读主题、进度保存 |
| `boards/book/online.py` | 263 | 在线搜索页 + 下载任务（`QThread`） |
| `boards/book/context.py` | 22 | 本板块单例（懒创建） |

**引擎层（`core/book/`，1,442 行）**

| 文件 | 行数 | 职责 |
|---|---|---|
| `core/book/parser.py` | 624 | TXT / EPUB 解析：编码识别、章节标题识别、正文清洗 |
| `core/book/online.py` | 535 | **在线书源**：搜索 / 目录 / 章节 / 整本下载（第三方站点 HTML 解析） |
| `core/book/storage.py` | 191 | `book_books` / `book_history` 两张表的读写 |
| `core/book/library.py` | 68 | 业务编排（界面唯一入口） |
| `core/book/files.py` | 24 | 文件扫描：只认 `.txt` / `.epub`，递归 + 去重 |

依赖规则：本板块只能引用 `boards/book/`、`core/book/`、`core/platform`、`core/llm`、`ui_kit`、`services`；
跨板块引用会被 `tests/architecture/test_architecture.py` 拦下。

> ⚠️ **没有** `core/book/sources/` 或 `core/book/sources/providers/` 目录（旧文档写错过）。
> 在线书源是**单一实现** `NovelSource`，通过 `base_url` 参数适配不同站点，
> 而不是像影视/乐库那样有可插拔的 provider 注册表。

## 2. 支持的格式与诚实边界

| 格式 | 支持 | 边界 |
|---|---|---|
| `.txt` | ✅ | 编码自动识别（UTF-8 / UTF-8-BOM / GBK / GB18030）；章节靠标题正则分级识别 |
| `.epub` | ✅ | 用 `ebooklib` 解析，按 spine 顺序取正文 |
| 其它 | ❌ | 不认 PDF / MOBI / AZW3；`.pdf` 请走「墨软转换」或「墨软文档」 |

**TXT 章节识别的真实规则**（`core/book/parser.py`，测试见 `test_reader_parser.py`）：

- 识别 `第X章` / `第X回` / `正文 第X章` 等写法，中文数字与阿拉伯数字都支持；
- 完全识别不出章节时，**退化为"整本一章"**（而不是报错）——至少能读；
- 会过滤明显的垃圾行（网址、广告短语），规则保守：宁可多留一行，也不吃掉正文。

**诚实的限制**：靠样式排版（而非标题文字）分章的书会被识别成一章；
这类书建议先用「墨软转换」处理，或在阅读器里靠滚动位置记忆代替章节跳转。

## 3. 数据

数据库：单库 `modu.db`（详见 `docs/DATABASE.md`）。

| 表 | 内容 | 关键字段 |
|---|---|---|
| `book_books` | 书库主表，每本书一条 | `path`(**UNIQUE**，按路径去重)、`title`、`author`、`format`、`last_chapter_index`、`last_offset_in_chapter`、`last_progress`(0~1)、`last_opened_at` |
| `book_history` | 打开/换章/进度记录 | `book_id`、`chapter_index`、`chapter_title`、`opened_at`(秒级)、`action`(`open`/`chapter`/`progress`) |

设计要点：

- **按路径去重，不按书名**：同名的两本书放在不同目录中是两条记录；
- **阅读位置存在 `book_books` 上**（每本一条，UPSERT），不是流水表 —— 书架直接读它显示进度条；
- 删书**级联清历史**（`delete_book` 内先删 `book_history`），否则会留下点不开的僵尸条目；
- 历史排序 `ORDER BY opened_at DESC, id DESC`：`opened_at` 是秒级，同一秒打开两本书时
  单靠它排序不确定（v2.2.0 修，见下文「变更须知」）。

应用级单例：`boards/book/context.py`（惰性创建；`app/context.py` 做跨板块聚合）。

## 4. 在线书源（`core/book/online.py`）

**设计**：一个 `NovelSource(base_url)` 类，通过**多候选搜索端点 + 启发式 HTML 解析**
适配"笔趣阁类"站点，而不是为每个站点写适配器。

| 能力 | 实现方式 | 现状 |
|---|---|---|
| 搜索 | 依次尝试 7 个常见搜索路径（`_search_urls`），第一个能解析出结果的就用 | 站点改版会失效 |
| 搜索解析 | 扫全部 `<a>`，保留"站内 + ≥4 汉字 + 不含导航词 + 指向 `/book|novel/数字`"的项 | 启发式，见下 |
| 目录 | 正则提取章节链接，`index` 从 0 递增 | — |
| 正文 | 取正文容器、去广告行、压缩连续空行 | — |
| 整本下载 | `00shu.la` 的 `/txt/{id}/` 页面走特殊解析，直接拿 TXT 下载地址 | 单站点特例 |

**解析规则的边界（改版时改这里）**：

```python
# 标题：4~50 个字符、必须含汉字
if len(clean) < 4 or len(clean) > 50: continue
# 链接：必须是站内、且形如 /book/123/ 或 /novel/123/
if not re.search(r"/(book|novel|books|read)/\d+", href): continue
# 排除导航词（首页/登录/玄幻/排行榜…）
```

**合规**：设置里有合规开关；本板块定位是"按需下载**用户有权访问的内容**"的工具，
不做批量抓取。使用范围由使用者自行确认。

## 5. 界面约定

- 页面骨架：`ColumnPage` + `SectionCard` + `EmptyState`（空列表必须给下一步操作）；
- 状态与进度：板块底部 `TaskBar`（空闲自动收起进度与取消）；页面用 `_report` / `_busy` / `_idle`；
- 阅读主题（米白/纯白/薄荷/灰灰/夜间）作用于**阅读正文 HTML 内联样式**，
  不影响全局 QSS —— 见 `docs/UI_GUIDE.md`；
- 留白/圆角/字号一律取 `ui_kit/tokens.py` 的令牌。

## 6. 测试

| 文件 | 用例 | 覆盖 |
|---|---|---|
| `tests/board_book/test_reader_parser.py` | 13 | TXT 章节识别（中文数字/阿拉伯数字/正文前缀/垃圾行过滤）、GBK 编码、识别不出时的退化、EPUB 往返 |
| `tests/board_book/test_reader_core.py` | 3 | 存储去重、导入/列表/读取/历史、删书级联 |
| **`tests/board_book/test_online_source.py`** | **20** | **在线源：搜索解析/去重/排除规则/端点回退/编码识别/URL 拼装/HTTP 错误/目录/正文清洗** |
| **`tests/board_book/test_book_storage_edges.py`** | **21** | **边界：大小写扩展名/重复导入/同名不同路径/位置往返/未知书不炸/删书级联/历史顺序与过滤/清历史** |
| `tests/board_book/test_book_ui.py` | 5 | 书架/阅读/子页切换/合规门 |

合计 **62 个用例**（v2.2.0 之前只有 21 个，`online.py` 485 行**零测试**）。

> 补测试时**发现并修掉一个真实缺陷**：`list_history` 只按秒级 `opened_at` 排序，
> 同一秒打开两本书时顺序不确定（用户快速切换书籍会看到历史乱序）。

冒烟清单见 `docs/TESTING.md` 第 5 节。

## 7. 变更须知

- **改数据结构**：加迁移模块（`core/platform/migrations/`，已发布的不许改），
  同步 `tests/platform/test_db_schema.py` 与 `docs/DATABASE.md`；
- **改界面**：跑 `packaging/ui_snapshot.py --only book` 出图对比，
  再跑 `pytest tests/board_book tests/architecture -q`；
- **改在线源解析规则**（站点改版时）：只动 `core/book/online.py` 的
  `_parse_search` / `get_catalog` / `_clean_content`，
  并**同时补一条用真实 HTML 片段的用例**（`test_online_source.py`）——
  这个模块的失败方式是"悄悄解析出 0 条"，没有测试就发现不了；
- **加新格式**（如 MOBI）：要在 `core/book/files.py` 的 `SUPPORTED_BOOK_EXTENSIONS`
  与 `core/book/parser.py` 的 `parse_book` 分发处**一起**改，`test_supported_extensions_are_txt_and_epub`
  会提醒你只改了一半。
