# 全量优化报告（v2.2.0 基线 → 未发版）

> 配套文档：项目规则 [`AGENTS.md`](../AGENTS.md)、提示词体系
> [`AI_OPTIMIZATION_PLAYBOOK.md`](AI_OPTIMIZATION_PLAYBOOK.md)。
> 本报告记录**这次到底改了什么、凭什么说没改坏、还有什么没做以及为什么**。

## 0. 一句话结论

两轮下来，在**行为守恒**（公开接口、错误类型、调用时序、用户可见文案、QSS 字节序列、
界面控件树全部不变）的前提下，把仓库里最贵的结构性债务处理掉了：平台层重复实现、
831 行的自检函数、页面里复制 33 份的状态助手、714 行的 QSS 单函数、566 行的工具注册函数、
以及 5 个 150~263 行的界面装配函数。**最长函数从 831 行降到 205 行**。
全量 **1665 条用例**（1 条按环境跳过）通过，`ruff` 干净，打包自检 **17/17** 与优化前逐项一致；
剩下的 106 个 ≥60 行函数给出了按风险排序的施工清单 —— 见第 6 节（哪些**故意不做**与为什么）
与第 7 节（下一轮清单）。

## 1. 验收方法与证据（可复现）

| 证据 | 手段 | 结果 |
|---|---|---|
| 既有行为 | `pytest tests -q`（1665 条；含 14 个架构闸门、UI 离屏用例） | 通过（1 条按环境跳过；新增 27 条） |
| 静态检查 | `ruff check src tests`（E4/E7/E9/F/W） | 干净 |
| 打包可用性 | `MODU_CHECK_DEPS` 自检 17 项 | 17/17 `true`，**键与顺序与重构前逐字一致** |
| 样式零变化 | `sha256(app_qss(t))`，浅色/深色各一份 | LIGHT `8e2b96ea…`、DARK `34892000…`，**前后完全相同** |
| 公共接口 | `__all__` 与导出名逐一保留（平台层是"新增"，板块层是"薄封装"） | 无删除、无签名变化 |
| 架构边界 | `tests/architecture/test_architecture.py` | 通过（未新增任何跨层引用） |

复现命令：

```powershell
$env:QT_QPA_PLATFORM="offscreen"; .venv\Scripts\python.exe -m pytest tests -q
.venv\Scripts\python.exe -m ruff check src tests
$env:MODU_CHECK_DEPS="$env:TEMP\check.json"; .venv\Scripts\python.exe -m modu_workbench
```

## 2. 代码改动明细（6 个优化单元）

### 单元 1 · 平台层：三处"跨板块重复实现"收敛

**问题**：同一个能力在多个板块各抄一份，而且抄得**不完全一样**——

| 名字 | 重复情况 | 差异 |
|---|---|---|
| `safe_filename` | 乐库 `core/music/models.py`、影视 `core/video/models.py`、图库 `core/gallery/models.py` 各一份 | 默认 fallback（`track`/`video`/`image`）、限长（120/120/**110**） |
| `unique_path` | 乐库与影视的 `downloader.py` 各一份 | 逐字相同 |
| `strip_html` | `core/platform/textutil.py` 与 `core/video/models.py` 各一份 | 逐字相同 |

**为什么必须收敛**：板块之间不允许互相 import，所以三份实现只能靠人工同步。
改一处忘一处的结果是"乐库能存、影视崩"这类只在特定板块复现的差异；
而 `safe_filename` 的**限长差异（110 vs 120）是真实存在的历史行为**，
统一成 120 会让图库文件名截断位置变化 —— 这是必须保住的细节。

**改法**：实现进 `core/platform/files.py`（板块无关的共享层），带 `limit` 参数；
三个板块各自保留**签名与默认值不变的薄封装**（`_safe_filename(name, fallback, 110/120)`），
`unique_path` 直接由模块顶部导入绑定，`from core.music.downloader import unique_path`
照旧可用。`core/platform/__init__.py` 的 `__all__` 同步登记。

**证明**：新增 `tests/platform/test_platform_files.py` **15 例**，其中
`test_board_safe_filename_defaults_unchanged` 专门钉住"图库仍是 110、乐库/影视仍是 120、
三个 fallback 不变"，`test_downloader_unique_path_is_the_shared_one` 断言两个
downloader 暴露的是**同一个函数对象**。

### 单元 2 · `app/selfcheck.py`：831 行的 `run_checks` 按域拆分

**问题**：一个函数 831 行，里面嵌着 17 个 `check_*` 内部函数。改一项要在 800 多行里翻，
而且**任何一项都没法被单独调用或单独测试**。

**改法**：按"验的是谁"拆成 4 个子模块，本文件只剩编排：

| 模块 | 覆盖 | 行数 |
|---|---|---|
| `app/selfcheck_convert.py` | Markdown/JSON 渲染、PDF 导出、QtMultimedia 播放 | 147 |
| `app/selfcheck_platform.py` | 单 Qt 栈、ffmpeg/ffprobe 定位、品牌图标 | 111 |
| `app/selfcheck_media.py` | 乐库/影视/图库内核 + 影视播放内核 | 333 |
| `app/selfcheck_document.py` | 文档 IR 全链路、PDF 打印引擎、大模型路由降级 | 290 |
| `app/selfcheck.py`（编排） | `_CHECKS` 数据表 + 逐条执行 + 写 JSON + 退出码 | **168** |

`_CHECKS` 从"17 行 `record(...)` 调用"变成**一张数据表**（键 + 函数 + 顺序），
新增一项检查 = 写一个 `check_*` + 表里加一行。

**证明**：`MODU_CHECK_DEPS` 自检输出的 JSON **键、顺序、值全部与重构前一致**（17/17 `true`）。
这是本轮最强的证据 —— 它覆盖渲染、转换、播放、六个板块的内核与打包资源。

### 单元 3 · 乐库/影视：页面里复制 33 份的"任务条状态助手"收敛为混入

**问题**：`_busy` / `_report` / `_idle` / `_set_progress` / `_note_fallback` 五个方法
在乐库 5 个页面里**逐字复制了 5 遍**（25 份），影视还有 8 份（`_note_fallback` ×3、
`_busy` ×2、`VideoLibraryPage` 的另外 3 个）。总共 **33 份重复实现**。

**改法**：新增 `ui_kit/components/task_status.py::TaskStatusMixin`（纯 Python、不依赖 Qt），
页面改成 `class MusicSearchPage(TaskStatusMixin, QWidget)`。
**只删"逐字相同"的方法**：影视的 `VideoSearchPage._report`（不写 `_note_fallback`）、
`VideoSearchPage._idle`、`VideoHistoryPage._report`（直接 `idle`）、以及
`_set_tail_stretch`（三份的布局属性不同：`self._page.body` vs `self._page_widget.body`）
**全部原样保留** —— 差异即行为，不动。

**连带收窄了架构闸门**：`tests/architecture/test_ui_page_conventions.py` 原本断言
"调用状态助手的类必须自己定义它"，现在放行**显式继承 `TaskStatusMixin`** 的类。
放行边界写得很窄（只认这一个基类名），并新增
`test_registered_mixins_really_provide_helpers` 防止"登记了却没实现"的空登记。

**证明**：新增 `tests/architecture/test_ui_task_status.py` **11 例**（假宿主、无 Qt），
钉住三条容易改坏的语义：`_report` 无计数时只记文字、`_idle("")` 不覆盖已有文字、
`_set_progress` 只改数值不改显隐。乐库/影视全部 UI 用例通过。

### 单元 4 · `ui_kit/theme.py`：714 行的 `app_qss` 按控件族拆分

**问题**：`app_qss` 是一个 **714 行的 f-string**，24 个 `/* ---------- */` 分节全挤在一起。
想改表格行高要在 700 行里滚到第 470 行，还容易改到隔壁的滚动条。

**改法**：按控件族拆成 5 个模块，`app_qss` 只做"算一次图标 → 按固定顺序拼接"：

| 模块 | 覆盖 |
|---|---|
| `theme_qss_base.py` | 页面底色/文字、按钮、顶栏导航、面板/卡片、徽章、分组卡片 |
| `theme_qss_forms.py` | 输入框、下拉框与弹出列表、数字输入、勾选/单选、右键菜单 |
| `theme_qss_views.py` | 树、选项卡、表格、滚动条/分割条/提示、状态标签 |
| `theme_qss_boards.py` | 阅读正文留白、文档查看器、播放器小窗、转换动作、乐库播放条 |
| `theme_qss_shell.py` | 页面骨架、区块卡片、空状态、工具条/次要按钮 |

拼接方式是 `"\n" + "\n".join(...) + "\n"`，**与原来单个 f-string 的字节序列完全一致**。

**证明**：`sha256(app_qss(t))` 前后完全相同（LIGHT/DARK 两份）：
`18221` 字符 / `8e2b96ea928f1d95` / `348920006fad4bf9`。
这比"看截图挺像"强得多 —— 它连空格与换行都证明没变。

### 单元 5 · 规范一致性：`except Exception: pass` 清零

**问题**：`docs/ARCHITECTURE.md` 从 v2.1.0 起规定"失败可忽略的位置不允许再写
`except Exception: pass`"，但全仓还有 5 处没换（AST 扫描：只认"当前只剩一条 `pass`"的处理器）。

**改法**：

| 位置 | 处置 |
|---|---|
| `core/platform/http.py:55`（certifi 缺失） | 高频路径 → `debug("加载 certifi 证书", error)`（默认级别不刷警告） |
| `core/platform/http.py:60`（OpenSSL 默认路径不可用） | 同上 → `debug("查询系统证书路径", error)` |
| `boards/gallery/board.py`（记住每页张数） | → `swallow("记住每页张数", error)` |
| `boards/gallery/board.py`（记住导入目录） | → `swallow("记住导入目录", error)` |
| `app/main.py:18`（日志系统自身装配失败） | **刻意保留 `pass`** 并写明理由：出问题的正是日志系统，`swallow()` 会再走同一条失败路径 |

**证明**：`tests/architecture`、`tests/board_gallery`、`tests/test_platform_http.py` 全通过；
AST 复扫后 `except Exception/BaseException: pass` 从 5 处降为 **1 处（且是已注明的唯一例外）**。

### 单元 6 · 架构闸门与测试的同步收紧

分开记一笔：这次**没有**为了让测试变绿而放松任何断言，唯一的"放松"是
`test_helper_calls_are_defined` 放行显式混入（见单元 3），同时补了一条更严格的
`test_registered_mixins_really_provide_helpers` 作为补偿。其余闸门一字未改。

### 单元 7 · 工具注册表分组（第二轮）

`core/document/tools.py::_register_all` 原本是 **566 行**、51 个 `register_tool(ToolSpec(...))`
平铺。按"原文件里连续同类"切成 9 个 `_register_*_tools()`：

| 分组 | 数量 | 类别 |
|---|---|---|
| `_register_read_tools` | 2 | read |
| `_register_write_tools` | 2 | write |
| `_register_edit_tools` | 13 | edit |
| `_register_sheet_tools` | 6 | sheet |
| `_register_ai_tools` | 8 | ai |
| `_register_merge_tools` | 3 | merge |
| `_register_pdf_tools` | 13 | pdf |
| `_register_write_tools_2` | 1 | write（`export_images`，**刻意留在 PDF 组之后**） |
| `_register_secure_tools` | 3 | secure |

⚠️ 这里有个坑：按"类别字典"归类会把末尾那个 `write` 类的 `export_images` 提到前面去，
而注册顺序就是 `TOOL_ORDER`（= AI 工具目录与界面的展示顺序），会让用户在眼前看到工具重排。
所以切分必须按**原文件里的连续同类**来切，不能按类别聚合。

**证明**：`TOOL_REGISTRY` / `TOOL_ORDER` / `TOOL_CATEGORIES` / `catalog_lines()` /
`tools_in_category()` 全量序列化后，与改前**逐字一致**（51 个工具的 name/label/desc/
参数表/类别/needs_ai/handler 名/签名全部相同）。

### 单元 8 · 界面层 5 个巨型装配函数按分节拆开（第二轮）

| 位置 | 之前 | 拆成 |
|---|---|---|
| `ui_kit/settings/video.py::__init__` | 263 行 | `_build_basics` / `_build_sources` / `_build_playback` / `_build_shortcuts` / `_build_cache` / `_build_danmaku` / `_build_footer` |
| `ui_kit/settings/document.py::__init__` | 207 行 | `_build_output` / `_build_style` / `_build_loop` / `_build_permissions` / `_build_capability` / `_build_plugins` / `_build_media_cache` |
| `ui_kit/settings/llm.py::__init__` | 173 行 | `_build_kind_list` / `_build_profile_list` / `_build_profile_form`（左中右三栏） |
| `ui_kit/settings/gallery.py::__init__` | 110 行 | `_build_dirs` / `_build_preferences` / `_build_ai` |
| `boards/convert/board.py::__init__` | 219 行 | `_build_header` / `_build_toolbar` / `_build_file_panel` / `_build_action_panel` / `_wire_up` |
| `ui_kit/media/player.py::__init__` + `_build_ui` | 85 + 153 行 | `_init_state` + `_build_header` / `_build_transport` / `_build_options` |

**证明（本轮的通用手法，见第 4 节"结构不变量"）**：把文件切回 HEAD 版本构造页面、导出
"控件数 / 布局条目数 / 实例属性集合 / 全部可见文案 / 全部 placeholder"，换回工作区版本
再导一次，**逐字段比对全部一致**：

```
DocumentSettingsPage   OK  widgets=96  layout_items=23  attrs=30
GallerySettingsPage    OK  widgets=33  layout_items=5   attrs=12
LlmSettingsPage        OK  widgets=61  layout_items=3   attrs=24
VideoSettingsPage      OK  widgets=151 layout_items=25  attrs=39
ConvertBoardPage       OK  children=103  attrs=33  表头/芯片/列数一致
VideoPlayerDialog      OK  children=81   attrs=70  清晰度/倍速/进度范围一致
```

**这个探针抓到了一个真 bug**（也就是为什么"测试绿"不够）：把分节搬进 `_build_*` 时，
`video.py` 的 `_build_basics` 与 `gallery.py` 的 `__init__` 各多留了一次
`QVBoxLayout(self)`，于是第二份 layout 成了**孤儿**——控件都挂在它上面，界面直接少一半，
而所有用例仍然全绿，只在控制台留一行
`QLayout: Attempting to add QLayout ... which already has a layout`。
单元 8 的 6 个文件都是按探针结果修过才收工的。

## 3. 量化对比（口径：`src/**.py`，`splitlines()` 计行）

两轮合并看：

| 指标 | 起点（HEAD） | 第一轮后 | 第二轮后 | 说明 |
|---|---|---|---|---|
| 文件数 | 210 | 220 | 220 | 新增 10 个模块（自检 4 + QSS 5 + 混入 1） |
| 总行数 | 69,776 | 69,930 | 70,142 | +366 行，**全是** docstring / 编排代码 / 薄封装 |
| 最长文件 | 2,057（`core/document/sheet.py`） | 同左 | 同左 | 未动（见第 6 节） |
| 文件 > 500 行 | 46 | 42 | 44 | 两个设置页因新增方法 docstring 略过 500 行线（内容不变） |
| 最长函数 | **831**（`selfcheck.run_checks`） | 566（`tools._register_all`） | **205**（`beautify_page.__init__`） | 降 **75%** |
| 函数 ≥ 60 行 | 102 | 105 | 106 | 拆大函数的同时，QSS 段等声明式长函数进了统计 |
| 逐字重复的实现 | 33 份状态助手 + 5 份平台工具 | 0 | 0 | 收敛为 1 个混入 + 共享层 |
| 最长函数 | **831**（`selfcheck.run_checks`） | **566**（`document/tools._register_all`） | 最长函数降 32%；原先的 831/714 两个"巨无霸"都拆掉了 |
| 函数 ≥ 60 行 | 102 | 105 | +3：QSS 由 1 个 714 行函数变为 5 个 ≤189 行函数（声明式字符串，已注明例外） |
| 逐字重复的实现 | 33 份状态助手 + 5 份平台工具 | 0 | 收敛为 1 个混入 + 共享层 |

## 4. 全量审计结果（这轮"看到了但没动"的部分）

用 AST 全仓扫描（不只靠肉眼）得到的事实（**第二轮后**）：

- **106 个函数 ≥ 60 行**，前 10 名：

  | 行数 | 位置 | 性质 |
  |---|---|---|
  | 205 | `boards/document/beautify_page.py::__init__` | Qt 装配（左右两栏，可继续拆） |
  | 202 | `boards/gallery/enhance.py::__init__` | Qt 装配（一堆滑块） |
  | 189 | `ui_kit/theme_qss_base.py::qss_base` | QSS 字符串（声明式数据，非逻辑） |
  | 189 | `core/video/segment_downloader.py::download` | 业务算法（**必须先补测试**） |
  | 187 | `boards/document/loop_page.py::__init__` | Qt 装配 |
  | 179 | `core/gallery/edits.py::apply_step` | 业务算法（非破坏性编辑的步骤重放） |
  | 178 | `boards/music/search.py::__init__` | Qt 装配 |
  | 177 | `boards/document/sheet_page.py::__init__` | Qt 装配 |
  | 164 | `app/selfcheck_document.py::check_document_core` | 自检（顺序执行的验证步骤） |
  | 160 | `ui_kit/theme_qss_forms.py::qss_forms` | QSS 字符串（声明式数据） |

- **44 个文件 > 500 行**，前 6 名：`document/sheet.py` 2057、`document/tools.py` 1838、
  `gallery/board.py` 1754、`ui_kit/media/player.py` 1640、`document/parser.py` 1598、
  `document/viewer.py` 1279。
- **重复实现**（AST 精确比对 8~60 行的函数体）：除已收敛的以外，仅剩
  `boards/music/widgets.py::show_menu` 与 `boards/video/widgets.py::show_menu`
  （12 行；两个板块之间**不能**互相引用，收敛要进 `ui_kit`，收益小于风险），
  以及 `core/gallery/library.py::_unique_target`（与 `unique_path` 契约不同：
  收整路径、1000 次用尽后回退到 sha1 命名 —— 合并会改行为，**故意不动**）。
- **规范扫描**：全仓 `except Exception: pass` 从 **5 处 → 1 处**（仅剩 `app/main.py` 日志
  自身装配失败，已在代码里写明为什么这是唯一不能留痕的位置）；
  `except <具体类型>: pass` 40 处，属于"先收窄类型再忽略"，符合约定（宽捕获才强制 `swallow`）。
- **TODO/FIXME**：`src/` 下 0 处（唯一命中是一个中文注释里的词，不是待办标记）。

## 5. 文档交付

| 文件 | 作用 | 面向 |
|---|---|---|
| [`AGENTS.md`](../AGENTS.md) | 项目级规则文件（长期约束层）：技术栈、命令、架构红线、行为守恒铁律、异常写法、UI/测试/提交规范、本仓库专属的坑 | AI 每次对话自动读；人也能当天书查 |
| [`docs/AI_OPTIMIZATION_PLAYBOOK.md`](AI_OPTIMIZATION_PLAYBOOK.md) | 提示词体系（任务层）：两层分工、建基线、四个场景提示词、三种"行为不变"的硬证据、执行纪律、推荐施工顺序 | 每次发起优化任务前复制取用 |
| [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) | 已同步结构变化（theme 拆分、selfcheck 拆分、TaskStatusMixin、平台层新工具、工具注册分组） | 架构说明 |
| [`CHANGELOG.md`](../CHANGELOG.md) | 两条「未发版」记录：第 52 条（第一轮）、第 53 条（第二轮），均含"之前/现在/验证" | 发版说明 |

## 6. 为什么有些"优化"这轮故意不做

**"全量优化"不等于"把所有长函数都拆一遍"。** 下面这些按要求应当做、但本轮刻意不做，
原因是**风险与证据不匹配** —— 要么没有测试兜底，要么拆了反而更难懂：

| 暂不做的项 | 原因 | 启动条件 |
|---|---|---|
| `core/document/sheet.py`（2057 行，公式引擎） | 公式解析器有"每次求值前保存、结束后恢复 token 流"的跨函数状态约定；拆函数时若跟着函数边界切状态，会出现**只有嵌套公式才触发**的"缺少 )"，常规用例全绿 | 先补嵌套公式用例（3 层以上、跨表、数组公式），再按"场景四→场景三"施工 |
| `document/parser.py`、`writer.py` | 格式矩阵密集、每个分支对应一种真实文件；拆错的代价是"某种格式静默坏掉" | 先为每种格式补往返用例（parse→write→parse 幂等） |
| `boards/music/widgets.py` / `boards/video/widgets.py` 的 `show_menu` | 12 行 ×2；跨板块不能互引，收敛要进 `ui_kit/media`，收益小于耦合风险 | 等第三个板块也需要同一个菜单时再做 |
| `core/gallery/library.py::_unique_target` | 与 `unique_path` **契约不同**（收整路径 / 回退 sha1 命名），合并即改行为 | 若确认要统一，属行为变更，走 CHANGELOG + 迁移说明 |
| 全仓 import 排序（`I001`） | 会把上百个文件重排，副作用顺序风险大于收益（`pyproject.toml` 已写明理由） | 不做 |

## 7. 下一轮施工清单（第二轮的已完成项已划掉）

每条都可以直接套 `docs/AI_OPTIMIZATION_PLAYBOOK.md` 的场景提示词执行。
**第二轮已完成**：`tools._register_all` 分组、4 个设置页 `__init__`、转换板块 `__init__`、
播放器 `__init__`/`_build_ui`（见单元 7、8）。

1. ~~**[低] `tools._register_all` 按类别分组**~~ ✅ 已完成（单元 7）
2. ~~**[低] 4 个设置页的巨型 `__init__`**~~ ✅ 已完成（单元 8）
3. **[低] 剩下的板块页装配函数**（都可用单元 8 的结构探针证明）：
   `boards/document/beautify_page.py::__init__`（205）、`boards/gallery/enhance.py::__init__`（202）、
   `boards/document/loop_page.py::__init__`（187）、`boards/music/search.py::__init__`（178）、
   `boards/document/sheet_page.py::__init__`（177）、`boards/video/library_page.py::__init__`（152）、
   `boards/video/board.py::__init__`（133）、`boards/music/library_page.py::__init__`（127）、
   `boards/music/playlist.py::__init__`（122）、`boards/video/sources.py::__init__`（120）、
   `boards/document/merge_page.py::__init__`（150）、
   `boards/gallery/board.py::_build_toolbar/_build_bottom`（140/102）、
   `boards/video/detail.py::_build_ui`（102）、`boards/convert/doc_viewer.py::_qt_style_html`（86）。
   只做"分节 + 抽方法"，**不动信号连接顺序**；每个文件做完都跑一次结构探针。
4. **[中] `core/video/segment_downloader.py::download`（189 行）、`core/gallery/edits.py::apply_step`（179 行）**
   先补"分片重试/取消""编辑步骤重放"用例，再把纯逻辑（URL/Range 计算、步骤参数校验）
   抽成可独立测试的函数；`download` 里的网络/ffmpeg 调用留在原处。
   `apply_step` 若是一长串 `if step.kind == ...`，可改成**分发表**（新增步骤 = 加一个函数 + 一条表项），
   但必须保证未知 kind 的报错文案与顺序不变。
5. **[中] `boards/book/*`、`core/book/online.py`（533 行）、`core/music/sources/providers/*`**
   带宽/解析类逻辑，历史上有过"排序不稳定"的事故（见 `docs/TESTING.md` 第 5 节），
   动之前先看 `tests/board_book/` 里已有的 500+ 行用例。
6. **[高] `core/document/sheet.py` 公式引擎、`parser.py`/`writer.py`**
   见第 6 节的启动条件。这是全仓逻辑密度最高的三处，值得单独一轮。

## 8. 给下一个人的四条提醒

1. **快照比对 > 肉眼审查**。这套代码里最容易骗人的是"看起来没变"：
   QSS 少一个空格、`_report` 少一次 `_note_fallback`、自检少一项 —— 都不会报错。
   能算哈希就算哈希，能存 JSON 就存 JSON。
2. **界面拆分必须做"结构探针"**。测试全绿 + 界面看着正常，仍可能有一整个孤儿布局
   （单元 8 就踩到了两次）。做法：同一个类在 HEAD 与工作区各构造一次，导出
   "控件数 / 布局条目数 / 实例属性集合 / 全部可见文案 / 全部 placeholder" 再逐字段比对。
   实现见本仓库的临时脚本思路（`findChildren(QWidget)` + `vars(obj)`），十行就能写完。
3. **差异即行为**。影视那三份 `_report` 看起来"应该统一"，但它们各自对应
   "有没有右上角小标签""结束时要不要直接 idle"的真实差异。**重复代码里藏着差异，
   收敛前先把差异找出来写进方案**，而不是先删再修。
4. **测试是设计的一部分**。本轮动过的唯一测试
   （`test_ui_page_conventions.py`）不是为了变绿，而是因为**设计确实变了**
   （助手从"每个类自己定义"变成"共享混入提供"），并且同时补了一条更严的补偿断言。
   如果有一天你发现自己在改断言让它通过，请先停下来问"设计是不是真的该变"。
