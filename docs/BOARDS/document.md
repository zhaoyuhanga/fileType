# 板块：墨软文档（`document`）

> 一站式文档工作台：查看编辑 / 格式美化 / 填充计算 / 两个文档合并 / AI 循环美化
> 子页：viewer / beautify_page / sheet_page / merge_page / loop_page / security_page

## 1. 目录

**界面层（boards/document/）**

| 文件 | 职责 |
|---|---|
| `boards/document/board.py` | 板块外壳：文档库列表 + 六个页签 + 统一任务条 |
| `boards/document/context.py` | 板块单例（存储 / 库 / AI，权限实时读设置） |
| `boards/document/viewer.py` | 查看与编辑（按能力矩阵选编辑方式：源码/段落/表格/预览） |
| `boards/document/beautify_page.py` | 格式美化参数、预览改动、应用、导出 |
| `boards/document/sheet_page.py` | 自动填充、智能填充、公式计算、统计与检查 |
| `boards/document/merge_page.py` | 两个文档合并（七种模式 + 报告 + 差异 + 导出） |
| `boards/document/loop_page.py` | AI 循环美化（目标、轮次表、每轮 diff、回滚） |
| `boards/document/security_page.py` | 脱敏、水印与追踪、AI 权限、审计与 AI 调用日志 |
| `boards/document/pdf_dialog.py` | PDF 工具箱对话框（拆分/合并/压缩/加密/水印/表单/转图片） |
| `boards/document/widgets.py` | 后台线程（`DocumentWorker`）、变更表、差异视图、版本对话框 |

**引擎层（core/document/）**

| 文件 | 职责 | 对应需求 |
|---|---|---|
| `formats.py` | 格式能力矩阵（view/edit/convert/annotate/ocr） | 6.2 |
| `models.py` | `DocumentIR` / `Block` / `TableData` / 选项与结果模型 | 全部 |
| `parser.py` | 各格式 → IR（含 OOXML 容器嗅探、LibreOffice 兜底） | 6.2 / 6.6 |
| `writer.py` | IR → txt/md/html/docx/xlsx/csv/tsv/json/pdf（水印与页眉页脚） | 6.2 / 6.3 |
| `beautify.py` | 一键美化、7 套模板、样式统一、表格、编号、目录、品牌 | 6.3 |
| `sheet.py` | 自动填充、智能填充、公式引擎、统计、检查、单位、AI 公式 | 6.4 |
| `tools.py` | 工具注册表（30+ 个真实工具，界面与 AI 共用） | 6.5 |
| `ai.py` | 场景（7 类）、工具调用协议、质量评估、权限与日志 | 6.5 |
| `merge.py` | 两个文档合并（七种模式）+ 去重/术语/冲突/报告/摘要 | 6.6 |
| `loop.py` | AI 循环美化引擎（分析→计划→工具→评分→停止） | 6.7 |
| `quality.py` | 文档体检、五维质量评分、结构化 diff | 6.7 |
| `security.py` | 脱敏规则、权限模型、水印与追踪、审计字典 | 6.8 |
| `storage.py` | `doc_*` 五张表（文档库/版本/合并报告/AI 与审计日志） | 6.8 |
| `library.py` | 业务编排（界面唯一入口） | 全部 |
| `pdf_tools.py` | PDF 页面级操作（拆分/合并/压缩/加密/旋转/水印/表单/批注/转图片/任意文档转 PNG） | 6.2 |
| `templates.py` | 本地模板库（保存/导入/导出/删除，JSON 可分享） | 6.3 / 6.11 |
| `plugins.py` | 插件加载器：插件目录里的 `register(api)` 注册自定义工具（默认关闭） | 6.9 扩展 |
| `ocr.py` | 可选 OCR（tesseract / PyMuPDF） | 6.2 |
| `media.py` | 二进制素材缓存（Word 里的图片按 sha1 落盘，IR 只存引用） | 6.2 |

依赖规则：本板块只能引用 `boards/document/`、`core/document/`、`core/platform`、`core/llm`、
`ui_kit`、`services`；跨板块引用会被 `tests/architecture/test_architecture.py` 拦下。

## 2. 数据

数据库：单库 `modu.db`（详见 `docs/DATABASE.md` 第 3.6 节）。

- `doc_documents`：文档库（路径/标题/格式/分类/统计/收藏/标签）
- `doc_versions`：**版本快照**（`DocumentIR` 的 JSON，每篇默认保留 40 个）
- `doc_merge_reports`：合并差异报告与摘要
- `doc_ai_calls`：AI 调用日志（模型/角色/工具/耗时/成本/脱敏次数）
- `doc_audit`：审计日志（打开/编辑/导出/美化/合并/回滚/脱敏/OCR）

设置走 `app_settings` 的 `document/` 命名空间：`output_dir`、`template`、`font_cn`、`body_size`、
`brand_color`、`auto_number`、`build_toc`、`beautify_tables`、`max_versions`、`max_rounds`、
`quality_threshold`、`max_cost`、`require_confirm`、`allow_cloud`、`allow_local`、
`mask_before_upload`、`mask_rules`、`allowed_models`、`max_chars`、`price_per_1k`、
`watermark_text`、`watermark_footer`、`watermark_tracking`。

每个键都有人读（接线见第 10 节）：`max_versions` → 版本裁剪、`max_rounds`/`quality_threshold`/`max_cost`
→ 循环面板初值、`require_confirm` → 每轮结束弹确认、`template`/`font_cn`/`body_size`/`brand_color`/美化开关
→ 美化面板初值与工具默认、`max_chars`/`allow_*`/`mask_*` → 每次 AI 调用前实时读取。
布尔值一律用 `settings.value(key, default, type=bool)` 读：Qt 在 Windows 注册表里把 bool 存成
`"true"/"false"` 字符串，`bool("false")` 会得到 True（踩过一次，见第 10 节）。

二进制素材（Word 里的图片）落在 `数据目录/document/media/`，按内容 sha1 命名、天然去重；
IR 与版本快照里只存文件名引用，因此快照不会因为图片而膨胀。

应用级单例：`boards/document/context.py`（惰性创建；`app/context.py` 做跨板块聚合）。

## 3. 界面约定

- 页面骨架：`PageHeader` + `TaskBar`（底部统一任务条）+ `EmptyState`（空库必须给下一步操作）；
- 状态与进度：子页通过 `widgets.report/busy/note/idle` 操作任务条，不各自造状态栏；
- 重活（解析/合并/循环美化/导出）一律 `DocumentWorker`（QThread，可取消）；
- 每处修改都要**看得见**：`ChangeTable`（类型/位置/说明/来源 AI 或本地）+ `DiffBrowser`（+ − ~）；
- 留白/圆角/字号取 `ui_kit/tokens.py` 的令牌（见 `docs/UI_GUIDE.md`）。

## 4. 测试

| 文件 | 说明 |
|---|---|
| `tests/board_document/test_document_core.py` | 格式矩阵、解析、写出（含 xlsx 冻结表头/条件格式/数据验证）、美化、质量、存储与库流程 |
| `tests/board_document/test_document_sheet.py` | 公式引擎、自动填充、智能填充、排序筛选、统计、单位、AI 公式 |
| `tests/board_document/test_document_merge.py` | 七种合并模式、表格合并、去重/术语/冲突/摘要、库集成 |
| `tests/board_document/test_document_ai_loop.py` | 工具注册表与调用、AI 权限与脱敏、循环美化停止条件与回滚 |
| `tests/board_document/test_document_pdf.py` | PDF 拆分/合并/压缩/加密/解密/水印/表单、工具注册、PDF 工具箱对话框 |
| `tests/board_document/test_document_comments.py` | 批注：文档层增删列、Word 原生批注、Excel 批注表、其它格式附录、PDF 标注读写、界面批注面板 |
| `tests/board_document/test_document_extras.py` | 本地模板库（保存/导入/导出/删除/套用）、OCR（图片与 PDF、缺失提示）、导出图片 |
| `tests/board_document/test_document_plugins.py` | 插件加载：与内置同权、默认关闭、坏插件不致命、超大文件拒绝、示例插件可跑 |
| `tests/board_document/test_document_nonfunctional.py` | 6.9 非功能：性能预算、预览保护、版本清理与回滚、快捷键与可访问名 |
| `tests/board_document/test_document_ui.py` | 板块与六个子页、查看编辑、分屏实时预览、书签、自动保存、多标签、美化、计算、合并、循环、安全、设置页 |
| `tests/board_document/test_document_acceptance.py` | 需求 6.10 的八条验收标准逐条验收（真实引擎端到端） |

AI 相关测试全部用替身模型（monkeypatch `OpenAiCompatClient.chat`），不联网、不需要 Key。
冒烟清单见 `docs/TESTING.md` 第 5 节。

## 5. 变更须知

- **新增格式**：只改 `core/document/formats.py`（登记能力）+ `parser.py` 的解析分支；
  写出目标要同时加到 `EXPORT_TARGETS` 与 `writer.py`，否则界面会列出做不到的目标。
- **新增工具**：在 `core/document/tools.py` 注册（`ToolSpec` + handler），AI 与界面同时获得该能力；
  工具的失败必须返回 `ToolResult(False, 中文原因)`，不要抛异常打断整条流程。
- **数据结构**：加迁移模块并同步 `tests/platform/test_db_schema.py` 与 `docs/DATABASE.md`；
  版本快照的字段变化要让 `DocumentIR.to_dict/from_dict` 保持向后兼容（老快照仍能回滚）。
- **改界面**：跑 `pytest tests/board_document tests/architecture -q`；
  截图对比用 `python packaging/ui_snapshot.py --only document`。
- **权限/脱敏**：只改 `security.py` 的规则表与 `Permissions`；AI 调用统一从 `DocumentAi._chat` 走，
  不要绕过（绕过就等于绕过了脱敏与审计）。
- **设置项**：新增设置键时**必须同时接上读取方**（`test_document_ui.py` 的
  `test_*_takes_settings_defaults` 就是这个用途），否则界面承诺了却不生效；
  需要按设置变化实时生效的读取器由 `context.py` 注入（参考 `version_keep_provider`、`permissions_provider`）。
- **PDF 页面操作**：全部放 `pdf_tools.py`，不要在界面里直接调 pypdf ——
  那里的容错（加密返回值检查、clone_from、参数改名兼容）是踩过坑的。
- **保存语义**：`DocumentLibrary.save` 默认 `overwrite=True`（保存自己打开的文档就是替换原文件），
  防覆盖加序号只用于 `export`/转换输出；改这个默认值前先想清楚"用户点保存却发现多出一个 (1) 文件"。

## 6. 需求对照与验收

需求编号 → 落点（详细验收见 `tests/board_document/test_document_acceptance.py`）：

| 需求 | 落点 |
|---|---|
| 6.2 格式查看与编辑 | `formats.py`（33 种格式能力矩阵）+ `parser.py`/`writer.py` + `viewer.py`（源码/段落/单元格编辑、大纲、查找替换、分屏与实时预览、书签、**批注**、自动保存、打印、版本历史）；**PDF 页面级操作**见 `pdf_tools.py` 与「PDF 工具」对话框（拆分/合并/压缩/加密/解密/旋转/水印/表单/批注/转图片）；**OCR**（图片与扫描 PDF → 可编辑文本）与**导出图片**（任意文档 → PNG：PDF 直接渲染、Office 经 LibreOffice 转 PDF） |
| 6.3 格式美化 | `beautify.py`（7 套模板、样式统一、表格美化、编号、目录、品牌与页眉页脚、页码）+ `templates.py`（本地模板库：保存/导入/导出/删除）+ `beautify_page.py`（先预览后应用） |
| 6.4 自动填充与计算 | `sheet.py`（序列/日期/工作日/月份/星期/中文序号/文本序号/公式填充、智能填充、公式引擎、统计/透视/图表、排序筛选、单位换算、AI 公式）+ `sheet_page.py` |
| 6.5 AI 模型配置与工具调用 | `core/llm`（多配置 + 降级）+ `ai.py`（7 场景、JSON 工具协议、质量评估、权限与日志）+ `tools.py`（44 个工具，界面与 AI 共用） |
| 6.6 两个文档合并 | `merge.py`（七种模式、去重、术语、风格、过渡段、目录、摘要、冲突、差异报告）+ `merge_page.py` |
| 6.7 AI 循环美化 | `loop.py`（分析→计划→工具→评分→停止，六种停止条件）+ `loop_page.py`（轮次表、每轮 diff、回滚） |
| 6.8 权限与安全 | `security.py`（脱敏规则、权限模型、水印与追踪、审计）+ `security_page.py` |
| 6.9 非功能 | 性能：见下表（大文档解析/写出都有实测预算测试）；可靠：自动保存 + 版本快照（按上限清理）+ 崩溃后回滚；扩展：格式表/工具表/模板表都是数据，**插件目录**可注册自定义工具；易用：AI 侧边（页签）+ 统一任务条 + **全键盘快捷键**；可访问：关键控件都有可访问名，快捷键有独立说明窗 |
| 6.10 验收标准 | `test_document_acceptance.py` 八条逐条断言 |
| 6.11 优先级 | P0（查看编辑、美化、填充计算、AI 配置与工具、两个文档合并）与 P1（循环美化、跨格式合并、OCR、差异报告、PDF 工具箱）均已实现；模板市场、多人协作、插件生态属 P2，未做（见下） |
| 6.12 待确认问题 | 见下节的处理决定 |

## 7. 需求 6.12「待确认问题」的处理决定

| 问题 | 决定与理由 |
|---|---|
| PPT 深度编辑还是仅查看与导出？ | **文本层可编辑 + 逐页与备注查看 + 导出 PDF/图片路径**：pptx 允许改文字与备注（保留版式），ppt/odp/dps 只读并提示用 LibreOffice 另存为 PPTX。不做版式级编辑（拖拽/母版）—— 那是 PowerPoint 的活，这里做只会做成"点了没反应" |
| 合并是否必须保留原始修订记录？ | **可选，默认保留**（`MergeOptions.keep_revisions`）：来自副文档的块标 `revision=inserted`，对比合并额外标【新增】/【修订】；关闭后是"干净合并"。原始文件的批注/修订（Word 修订模式）**不解析**，只有导出后的内容差异可追溯 |
| 循环美化最大轮次 / 成本上限 / 人工确认？ | 默认 **最大 3 轮、质量阈值 0.9、成本不限制、不必人工确认**，都可改；`require_confirm` 打开后每轮由界面确认；成本按"字符数 / 4 × 单价"估算（单价在「文档 → 权限安全」填，未填则不计成本） |
| 是否支持本地模型完全离线运行？ | **支持**：权限模型默认"允许本地、禁止云端"，`is_local_profile` 按服务商与地址判定（Ollama / 127.0.0.1…），本地模型不脱敏直发；未配置任何模型时美化/合并/计算/循环（本地规则）全部照常工作 |
| 合并后是否必须生成差异报告和摘要？ | **默认都生成**（`add_summary`/`diff` 始终记录），摘要可关；差异报告是每轮循环与每次合并的固定产物（`doc_merge_reports` + 每轮 `RoundReport.diff`），便于回滚与审计 |

## 8. 需求 6.9「非功能需求」落点

| 要求 | 实现 | 验证 |
|---|---|---|
| 性能：10MB 文档秒级打开、大文件分块加载 | 表格按 `MAX_TABLE_ROWS`（2 万行）截断并告警；**预览保护**：块数 > `PREVIEW_BLOCK_LIMIT`(800) 或字符 > 40 万时只渲染前 800 块并提示"保存/导出仍是全文"；解析/美化/写出都是单遍流式处理 | `test_document_nonfunctional.py`：1.46 MB Markdown 解析 **0.11s**、20k 行 CSV **0.03s**、导出 Word 1.57s（预算取实测 3~5 倍，只挡回归） |
| 兼容：主流 Office / WPS / PDF / Markdown | 33 种格式 + OOXML 容器嗅探 + LibreOffice 兜底（ODS/旧版 DOC·PPT/WPS 旧格式） | 格式矩阵不变量测试 + 各格式解析测试 |
| 可靠：自动保存、崩溃恢复、版本回滚 | 编辑内容按设置间隔自动写回原文件（不刷快照）；打开/保存/美化/合并/循环每轮都留 `DocumentIR` 快照，按设置里的「版本保留」（`document/max_versions`，默认 40）清理；回滚只改内存，不覆盖磁盘原文件 | 自动保存与版本清理/回滚测试 |
| 扩展：插件、工具注册、模型接入 | 工具是数据（`ToolSpec` + `register_tool`）；**插件目录**里的 Python 文件定义 `register(api)` 即可注册自定义工具（与内置同权，默认关闭、失败只记原因）；模型接入复用 `core/llm` 多配置 | `test_document_plugins.py`（7 条，含坏插件不致命、超大文件拒绝、示例插件可跑） |
| 易用：AI 侧边栏、快捷指令、模板市场 | 右侧六页签（含 AI 循环美化）、统一任务条；**窗口级快捷键**（Ctrl+O/Shift+O/回车/S/Shift+S/P/W/L/F、F5、Ctrl+Shift+P/R、Ctrl+Delete、Alt+1..6）并有「⌨ 快捷键」说明窗；模板以**本地模板库**替代联网市场 | 快捷键绑定与触发测试 |
| 可访问：键盘操作、高对比度、屏幕阅读器基础 | 全部常用操作可键盘完成；关键控件设置 `accessibleName`/`accessibleDescription`；配色走统一令牌（高对比度可整体换主题） | 可访问名非空断言 |

## 9. 已知能力边界（如实说明，避免"看起来能其实不能"）

| 能力 | 现状 | 说明 |
|---|---|---|
| 数学公式 / Mermaid 流程图 | 原样保留代码块并预览为等宽文本 | Qt 单栈下不引入 MathJax/Mermaid 渲染引擎（会重新引入 QtWebEngine，与 v1.0.0 的"去 WebEngine"决策冲突） |
| 演示文稿导出图片 | 经 LibreOffice 转 PDF 再渲染 PNG（`pdf_tools.document_to_images`），或用「导出为图片」按钮 | 需要本机 LibreOffice + PyMuPDF；两者缺一时给出安装提示，不做假渲染 |
| 表格条件格式 / 数据验证的**保留** | 只读取统计（`meta` 里记数量），导出时按规则**新写** | IR 不保存 Excel 的条件格式/验证表达式，重建不可靠；因此提供"导出时写入"而不是假装保留 |
| Word 批注 / 修订（Track Changes） | 批注**写出**支持（python-docx ≥ 1.2 写 Word 原生批注）；修订**按「接受全部修订」读文字**并给提示；**读取别人文档里的批注内容不支持** | 我们自己的批注随 IR 走：Word 原生批注、Excel「批注」工作表、HTML/MD/TXT 附录；PDF 用真实 `/FreeText` 标注。别人文档里的批注内容读不出来，需要 Word/WPS 处理 |
| OCR | 需要本机 tesseract（`MODU_TESSERACT`），扫描 PDF 另需 PyMuPDF | 缺失时给出安装提示，不静默失败；OCR 结果作为"未保存的可编辑文本"打开，避免覆盖原扫描件 |
| Word 里的**图表 / SmartArt** | 只取文字与表格；**图片**（含表格里的）、**文本框**（退化为引用块）、**修订插入的文字**都已保留 | 图表/SmartArt 里的文字会被读成普通段落，图形本身不重建；页眉页脚收进 `metadata`（不当正文块） |
| 修订与批注语义 | 修订按「接受全部修订」后的结果读取（删除的内容不保留），并在 `warnings` 里说明 | 需要逐条接受/拒绝修订请用 Word/WPS；我们自己的批注随 IR 往返（见上一行） |
| 设置项「最大时间上限」 | 只在循环面板里设，没进设置页 | 需要限时就地在循环面板里填秒数 |
| 多人协作 / 实时协同 / 联网模板市场 / 插件生态 | 未实现（P2）；模板分享用**本地模板库**（JSON 导入导出）替代 | 本板块是本地单机工作台，插件注册机制见 `formats.py` / `tools.py` / `TEMPLATES` 的扩展点 |

## 10. 设置接线与数据安全（本轮修复）

自查发现一批"界面已经承诺、底层没接线"的设置项，以及几个会造成重复/误覆盖/丢内容的问题，均已修复并配了回归测试：

| 修复 | 落点 | 测试 |
|---|---|---|
| 带图 Word：**图片不再丢** | `core/document/media.py`（图片按内容 sha1 落 `数据目录/document/media/`，块上只留引用）+ `parser._docx_images`（DrawingML/VML、表格内图片）+ `writer._docx_image` 重新嵌入；HTML/PDF 转 data URI 自包含，TXT/MD/XLSX 留占位并提示 | `test_docx_images_are_parsed_into_blocks`、`test_docx_images_survive_save_and_export`、`test_docx_image_missing_cache_keeps_placeholder`、`test_docx_image_in_other_targets`、`test_beautify_keeps_images` |
| 「版本保留」真的生效（过去写死 40） | `storage.DocumentStorage.version_keep_provider` + `boards/document/context.py::_read_version_keep`（裁剪时实时读设置） | `test_version_keep_follows_setting`、`test_context_version_keep_follows_settings` |
| 「AI 循环美化默认值」进循环面板（过去只有硬编码初值） | `boards/document/loop_page.py::_apply_settings_defaults` | `test_loop_panel_takes_settings_defaults` |
| 「每轮结束人工确认」真的会停下来问 | `loop_page.LoopWorker.ask_confirm/answer_confirm`（工作线程发信号 + 等答复，弹框始终在主线程；点「停止」也会解锁） | `test_loop_confirm_setting_is_wired` |
| 「默认美化参数」进美化面板（过去只影响工具/循环一侧） | `boards/document/beautify_page.py::_apply_settings_defaults`（只覆盖用户保存过的键，模板默认不被冲掉） | `test_beautify_panel_takes_settings_defaults` |
| 布尔设置读法（`bool("false") is True` 的坑） | 全部开关改 `settings.value(key, default, type=bool)`；`security._setting_bool` 兼容 `"true"/"false"` 字符串。**影响面**：自动保存关不掉、云端上传/脱敏开关反着来、人工确认弹框关不掉 | `test_permissions_read_windows_bool_strings`、`test_bool_settings_read_from_windows_string_form` |
| 文档标题不再重复（`ir.title` 与首个标题同名时） | `writer._title_already_in_blocks()`（HTML/PDF/DOCX 共用） | `test_write_does_not_repeat_document_title`、`test_write_keeps_title_when_it_differs_from_first_heading` |
| 合并结果不覆盖主文件 | `merge_page._apply()` 断掉 `ir.path`（原路径存进 `metadata`），保存走「另存为」；无路径文档单占一个标签 | `test_merge_apply_never_overwrites_main_file` |
| 另存为之后 Ctrl+S 不再重复弹路径 | `library.save()` 保存成功即更新 `ir.path`；落到别的文件上时按新路径重新登记（新文件独立进文档库） | `test_save_remembers_new_path`、`test_save_as_registers_new_document` |
| PDF「打印」不再盲打默认打印机 | 统一先弹 `QPrintDialog`；PDF 用 `viewer._PdfPages`（PyMuPDF → **QtPdf**）逐页按打印机分辨率渲染，页范围/份数/打印机都生效；其它格式仍走 HTML 排版 | `test_pdf_printing_renders_pages`（含只打印第 1 页） |
| Word **文本框 / 修订插入 / 超链接**里的文字不再消失 | `_docx_paragraph_text()` 按文档顺序取全部 `w:t`（跳过 `w:del` 删除内容与文本框），不再用只看直接子级的 `paragraph.text`；文本框退化为引用块并记 warning；页眉页脚收进 `metadata` | `test_docx_textbox_and_revision_text_is_not_lost` |
| 打开文件再保存不再"凭空多一行标题" | 解析时记 `metadata["title_source"]`（`filename` / `content`）；写出只在标题是文档自己的时候才写可见标题行 | `test_filename_title_is_not_invented_as_visible_title` |
| Markdown / HTML 里的图片也进素材缓存 | `_image_block()`：`data:` URI 与本地相对路径（按 `base_dir` 解析）都收进缓存；远程 URL 原样保留，导出 HTML 写成 `<img src>`、导出 Markdown 写成 `![alt](url)` | `test_markdown_images_become_blocks`、`test_html_data_uri_image_is_kept` |
| 素材缓存可见可清 | `media.cache_stats()` / `media.clear_cache()` + 设置页「素材缓存」区（大小、打开目录、清空，清空前弹确认） | `test_media_cache_stats_and_clear`；设置页横向不滚动的既有断言 |

顺带：`workbench.spec` / `workbench_mac.spec` 不再裁掉 `PySide6.QtPdf` 与 `Qt6Pdf.dll`（打印 PDF 要用），
并新增源码/打包自检项 `pdf_print_engine`（共 **16 项**）。
两个设置区的标题已标注"（进入板块时生效）"，与实现一致。

素材缓存的取舍（如实说明）：`数据目录/document/media/` **只增不减**（同内容只存一份，靠 sha1 去重）。
磁盘紧张时可以在「设置 → 文档 → 素材缓存」里点「清空素材缓存」：
已打开文档的图片会退化成 `[图片] 名字` 占位并给提示，不会报错；重新打开源文件会自动补回来。

新增格式/写出目标时注意：**标题行只在 `metadata["title_source"] != "filename"` 时写出**
（`writer._title_already_in_blocks`）。解析侧用 `parse_document` 会自动打这个标记，
直接用 `parse_markdown(text, title=...)` 构造的 IR 视为"文档自己的标题"，会照常写出。


