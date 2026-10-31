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

### 1.1 支持的格式（v1.0.4 起）

格式与动作都是**数据**：`core/convert/formats.py` 定义"有哪些格式"，
`core/convert/registry.py` 用 `_pair_actions(...)` 批量生成动作。新增格式只需改这两个文件，
**不要在业务代码里写 if/else 判断后缀**。

| 族 | 输入格式 | 可输出到 | 实现 |
|---|---|---|---|
| 文本 | txt / md / html | txt / md / html / pdf | `engine._run_text` |
| 数据 | json / xml / ini / yaml | txt / md / html / pdf / json / xml / yaml / ini / csv | `data_io.py` |
| 文档 | docx / doc / odt / rtf | txt / md / html / pdf（pdf 优先 LibreOffice 高保真） | `office_io.py` |
| 表格 | xlsx / xls / ods / csv / tsv | csv / tsv / xlsx / md / html / txt / pdf | `sheet_io.py` |
| 图片 | jpg / png / webp / bmp / gif / tiff / ico / tga / pcx / ppm（+ 只读 psd / dds / jp2） | 上述可写图片格式 + **pdf** | `image_io.py`（Pillow） |
| 音视频 | mp4 / mov / avi / mkv / webm / flv / wmv / m4v / mpg / mpeg / ts / 3gp / ogv；m4a / mp3 / wav / flac / aac / ogg / opus / wma / m4b / aiff / amr / ac3 | 同族互转 + 视频提取音频 | `media_io.py`（ffmpeg） |
| 字幕 | srt / vtt | srt / vtt / txt | `subtitle_io.py` |
| 电子书 | epub | txt / md / html / pdf | `ebook_io.py`（ebooklib） |
| 归档 | zip / tar / tar.gz / tar.bz2 / tar.xz / gz / bz2 / xz / rar | 压缩到 zip / tar / tar.* / gz / bz2 / xz；各自解压 | `archive_io.py`（标准库） |

维护约定：

- **登记即承诺**：进了 `*_FORMATS` 的格式必须有可用动作，`tests/board_convert/test_convert_format_coverage.py`
  会检查（含动作 id 唯一、目标有扩展名映射、每个格式至少一个动作）；
- **只读格式**（psd / dds / jp2）：只能作为源，不进 `TARGET_EXTENSION`；
- **单文件压缩保留源文件全名**：`报告.txt` → `报告.txt.gz`（bz2/xz 没有文件名字段，
  只有这样才能把原名带回去）；tar 系列仍是 `报告.tar.gz`（条目名即原名）；
- **缺依赖给可操作提示**：ods 需要 LibreOffice、amr 需要 ffmpeg 带 amrnb 编码器，
  引擎抛的是中文原因，不是英文堆栈。

## 2. 数据

数据库：单库 `modu.db`（详见 `docs/DATABASE.md`）。

（该板块没有独立数据表）

应用级单例：`boards/convert/context.py`（惰性创建；`app/context.py` 做跨板块聚合）。

## 3. 界面约定

- 页面骨架：`ColumnPage` + `SectionCard` + `EmptyState`（空列表必须给下一步操作）；
- 状态与进度：板块底部 `TaskBar`（空闲自动收起进度与取消）；页面用 `_report` / `_busy` / `_idle`；
- 留白/圆角/字号一律取 `ui_kit/tokens.py` 的令牌（见 `docs/UI_GUIDE.md`）。

### 输出目录与产物（v1.0.3 起）

- **默认输出目录必须走 `QStandardPaths.DocumentsLocation`**（`default_output_dir()`），
  不能写死 `Path.home()/"Documents"`：中文 Windows 上「文档」常被 OneDrive 重定向，
  写死会新建一个用户不会去看的目录，表现就是"转换成功但输出目录没数据"；
- **产物必须校验**：`core/convert/engine.py` 的 `_ensure_output_exists` 要求文件存在且非空、
  解压目录非空 —— 否则一律报失败并说明原因，绝不允许"没产出也算成功"；
- **界面要让输出可见**：任务条完成后写明「输出目录：<完整路径>」，工具栏有「打开输出目录」，
  双击表格「输出」列可在资源管理器里定位产物。「输出」列只显示文件名，完整路径进 tooltip
  （长路径会把窄列撑得看不清，见 v1.0.3 反馈）。

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
