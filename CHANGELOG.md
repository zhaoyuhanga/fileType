# 更新日志（CHANGELOG）

本项目遵循语义化版本；版本号单一来源为 `src/modu_workbench/__init__.py` 的 `__version__`
（与 `pyproject.toml` 保持一致，见 `tests/architecture/test_version.py`）。

## 未发版 — 代码优化与缺陷修复
### 28. CI 唯一失败用例的真因：预览渲染依赖真实 QSettings

上一节修了退出崩溃，CI 仍红。靠临时加的"失败日志推分支"机制终于拿到真实报错：

```
FAILED tests/board_document/test_document_nonfunctional.py::
       test_viewer_preview_is_capped_for_huge_documents
AssertionError: assert 0 >= 800
  where 0 = ''.count('第')      # _preview.toPlainText() 是空串
```

#### 根因

`DocViewer._refresh()` 里：

```python
show_editor  = editing and mode in (MODE_SOURCE, MODE_PARAGRAPH)
show_preview = (not show_editor) or (self._split_button.isChecked() and ...)
```

`markdown` 属于 `SOURCE_FORMATS`，所以载入后「编辑」按钮被勾上 → `show_editor` 为真
→ **是否渲染预览完全取决于「分栏」开关**；而分栏的初始值来自
`app_settings().value("document/split_view", False)`。

于是：

| 环境 | 注册表里的 `document/split_view` | 结果 |
|---|---|---|
| 开发机 | `true`（历史残留） | 预览有内容 → **通过** |
| GitHub Actions | 不存在（全新） | 预览为空 → **`0 >= 800` 失败** |

**这正好违反项目自己定的测试规则**：不允许依赖本机 QSettings。

#### 修法

用例里注入内存版 settings（`_MemorySettings`）并显式打开分栏，
断言变成"预览确实受上限保护"，而不是"这台机器的注册表恰好开着分栏"。
验证方式：把注册表里的 `document/split_view` **临时删掉**（模拟 CI 空注册表），
用例仍 `exit=0`，之后恢复原值。

#### 顺带保留下来的诊断机制

`.github/workflows/build.yml` 里加了一段 `if: failure()` 的步骤：
把「安装依赖 / 架构闸门 / 全量测试」三步的输出 tee 到同一日志，
失败时把尾部 500 行 + `python -VV` + `pip freeze` 推到 `ci-logs` 分支。

这在拿不到 CI 日志（GitHub API 403 限流、无 gh CLI/token）时是唯一能自查的办法，
**正是它定位了本节和上一节的根因**。保留着，只在失败时触发。

### 验证

- 全新 venv（PySide6 6.12）：全量 **exit=0**；
- 本地 venv（PySide6 6.11）：全量 **exit=0**；
- 空注册表模拟：目标用例 **exit=0**。


### 27. CI 一直红，真因是「测试全过但进程退出时崩溃」（PySide6 6.12）

上一节以为是设置页宽度问题，修完 CI 仍红。这轮把它查到底了。

#### 怎么确认的

GitHub API 403 限流、无 gh CLI/token，读不到 CI 日志。改用三条线索交叉定位：

1. 抓 Actions 页面 HTML 统计：4 次运行全失败，时长 4m49s ~ 6m54s
   —— **比 `build` 作业（含 109MB ffmpeg 下载 + PyInstaller）应该短得多**，
   所以失败在 `test` 作业，`build` 根本没轮到；
2. 本地"干净克隆 + 现有 venv"能过，但那不是 CI 环境；
3. **建全新 venv 忠实复现**：`pip install -e ".[dev,online]"` 装出来是
   **PySide6 6.12.0 / pytest 9.2.0**（本地是 6.11）——一跑就复现：

```
100% 通过（无 FAILED）
exit=-1073740791      ← 0xC0000409，解释器退出时崩溃
```

**全部用例通过，但进程退出时崩了**，CI 因此判失败。

#### 二分定位

| 范围 | 退出码 |
|---|---|
| `tests/board_gallery`（我上轮加的 ThumbnailWorker） | 0 ✅ |
| `tests/board_book` | 0 ✅ |
| `tests/board_music/test_music_import_thread.py`（我上轮新加） | 0 ✅ |
| **`tests/board_music/test_music_ui.py`** | **0xC0000409** ❌ |
| **`tests/smoke/test_cross_board_link.py`** | **0xC0000005** ❌ |

两个都不是这轮新写的用例，而是**既有测试在新版 PySide6 上暴露的退出崩溃**：
测试里创建的顶层窗口 / 媒体对象没有在解释器销毁前释放。

#### 修复

`tests/conftest.py` 加 session 级 autouse 夹具 `_qt_shutdown_cleanup`：
会话结束时按「关闭顶层窗口 → deleteLater → 处理事件 → quit」收尾，
让 Qt 走正常析构路径。**这是测试基础设施的通用修复**，不是给某个用例打补丁。

修复后两个 venv 全量均 `exit=0`（PySide6 6.11 与 6.12 都过）。

#### 教训

「本地全绿」这句话在没有复现 CI 的依赖版本前不成立。CI 的价值恰恰在于：
它用的是**最新依赖**，能照出本地被锁住的版本掩盖的问题。

### 验证

- 全新 venv（PySide6 6.12）：全量 **exit=0**，2.1 分钟；
- 本地 venv（PySide6 6.11）：全量 **exit=0**。


### 26. CI 首次运行就抓到一个真 bug：长路径把设置页顶出横向滚动

补完 CI 后第一次跑，`main` 上就挂了：

```
FAILED tests/smoke/test_cross_board_link.py::test_settings_pages_fit_default_width
AssertionError: general 页需要横向滚动（最大 1020px）
```

而这条用例**在本机一直是绿的**。典型的"本机能过、CI 挂"。

#### 怎么定位的

GitHub API 被限流（403）、页面内容截断，拿不到日志。改用**在干净克隆里复现**：

```
git clone --depth 1 file:///D:/work/fileType <tmp>
PYTHONPATH=<tmp>/src pytest -q        # 复现成功
```

再在克隆里打印每页的 `minimumSizeHint().width()` 和最宽标签，一眼看到：

```
general 页最宽 label 1452px:
  ffmpeg：C:\Users\me\AppData\Local\Microsoft\WinGet\Packages\
          Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\...\ffmpeg.exe
```

#### 根因

`general.py` 的 `_pickable()` 早就给**数据库路径**插了零宽断点（U+200B），
理由是"QLabel 即使 `setWordWrap` 也按最长不可断行算最小宽度"。但**外部工具那一段
（ffmpeg / ffprobe / LibreOffice）漏了**，只开了 `setWordWrap`。

于是：开发机装了 WinGet 版 ffmpeg → 路径 1452px → 980px 窗口横向滚动 768px；
CI 上没装 ffmpeg → 路径不同 → 恰好不触发。**这条测试的结果取决于机器上路径长短。**

#### 修复

- 抽出 `breakable_text()`（先放在 `general.py`，发现 `convert` 页同样问题后
  提到 `ui_kit/settings/base.py` 共用）；
- 两层断点：`\ / _ - .` 之后插零宽空格 + **每 24 个字符强制一个兜底断点**
  （`Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe` 这种五十多字符、
  只有 `.` `_` 的片段，光靠分隔符不够）；
- `general` 与 `convert` 两页的所有长路径都走它，完整原文放 tooltip。

效果：本机 8 个设置页横向滚动**全部 0px**（原来 general 768px、convert 780px）。

#### 新增两条**不依赖环境**的回归用例

原来那条测试之所以会骗人，是因为它读的是"当前机器上真实路径"。新增：

- `test_general_page_survives_very_long_tool_paths`：monkeypatch 成 120+ 字符的
  超长路径，断言无横向滚动 —— 任何机器上都可复现；
- `test_breakable_inserts_zero_width_breaks`：断点存在、可见文字不变、
  最长不可断片段 ≤ 30 字符。

### 验证

- **干净克隆**（模拟 CI 环境）全量测试：**通过**；
- 本机全量测试：**通过**；8 个设置页横向滚动全为 0。


### 25. 补 CI（`.github/workflows/build.yml`）—— SignPath 免费签名的前置条件

上一轮确认了 SignPath 的资质要求里，本项目唯一的硬缺口是
「**二进制必须能从公开仓库可验证地构建**」。仓库已确认公开，这轮把 CI 补上。

#### 工作流做什么

| 作业 | 步骤 |
|---|---|
| `test` | 安装依赖 → 架构闸门（先跑快的） → 全量测试 |
| `build` | 安装依赖 → **现下 ffmpeg**（不入库，单文件近 100MB） → PyInstaller 打包 → **无界面产物自检** → 压缩 → SHA256 → 上传产物 |

关键设计取舍：

- **不跑 GUI 冒烟**（需要交互式桌面，CI 上不可靠），改用产物自检
  `MODU_CHECK_DEPS` + `QT_QPA_PLATFORM=offscreen` —— 已实测无界面下能出
  完整结果（17 项全通过），既验证了二进制能启动、又对 CI 友好；
- ffmpeg 由 CI 现下（`.gitignore` 排除，不能入库）；
- 预留了 SignPath 签名作业（注释状态），获批后填 secrets 即可。

#### 写工作流时踩到并修掉的两个坑

1. **产物名用了 `github.ref_name`** —— PR 构建时它是 `123/merge`，
   带斜杠会生成非法文件名。改用 `github.sha`；
2. **`Set-Content -Encoding utf8NoBOM`** —— 只有 PowerShell 7+ 支持，
   而项目约定是"无 BOM"。改用 `.NET` 的
   `[System.IO.File]::WriteAllLines(..., UTF8Encoding($false))`（与
   `build_app.ps1` 保持一致），跨 PS 版本稳定。

#### 本地逐步骤验证过（不是写完就交）

| 步骤 | 验证结果 |
|---|---|
| YAML 解析 | 通过（jobs/触发器等结构正确） |
| ffmpeg 下载地址 | 109.5 MB 下载成功，包内 `bin/ffmpeg.exe` + `ffprobe.exe` 路径正确 |
| `pip install -e ".[online]"` | extras 可解析 |
| `python -m PyInstaller workbench.spec` | 打包成功（41 秒） |
| 无界面产物自检 | **17 项全通过** |
| `Compress-Archive` | 152.7 MB 压缩包生成 |
| SHA256 写入 | 无 BOM，格式与 `build_app.ps1` 一致 |

#### 文档同步

- `docs/SIGNING.md`：对照表里 CI 与仓库公开两项改为 ✅，落地步骤第 2 步标记完成，
  顶部写明「资质类条件全部满足，下一步只剩写 Code signing policy + 提交申请」；
- `README.md`：加构建徽章。

### 验证

- 全量 `pytest tests -q`：**通过**；
- 工作流每个步骤本地实测可用（见上表）；
- 提交后 GitHub Actions 会自动跑第一次构建。


### 24. 新增 `docs/SIGNING.md`：个人开发者怎么免费拿到代码签名

上一轮做完签名支持后留了个尾巴：「个人开发者可看 SignPath.io 这类开源免费签名」
—— 那是一句没核实过的口头建议。这轮把它落实成可执行文档。

#### 核实到的关键事实

- **SignPath Foundation**：对 OSS 项目**免费**，但做法不是"给你发证书"，而是
  证书签发给基金会、项目在受控流水线里借用（私钥在其 HSM，不落本地）。
  逐条读了[官方条款](https://signpath.org/terms)；
- **2023-06-01 起 CA/B Forum 要求私钥必须在 HSM**：这解释了为什么"免费签名"
  只剩云服务一条路，也解释了为什么拿不到可随意复制的 `.pfx`；
- **自签名证书治不了病**：实测签名成功但状态是 `UnknownError`
  （根证书不受信），SAC 与 SmartScreen 都不认。

#### 文档内容

`docs/SIGNING.md`：

1. 三条路径对比（SignPath / Certum / Azure Trusted Signing），并**明确标注**
   哪些是我核实过的、哪些"需自行核实"（Certum 与 Azure 的个人开发者资格、
   当期价格变动频繁，不照抄）；
2. **本项目现状 vs SignPath 要求**的逐条对照表 —— 结论是证件类要求（MIT、
   无专有组件、已发布、有文档、版本元数据）**全部满足**，真正的缺口是
   **没有 CI**（SignPath 要求"从源码可验证地构建"）和**仓库公开状态待确认**；
3. 按依赖顺序的 7 步落地清单（先补 CI，再申请）；
4. 可直接使用的 **Code signing policy** 模板（SignPath 强制要求写在首页）。

#### 诚实标注的三处不确定

- 仓库是否公开：本机到 GitHub 的连接当时不通，**没有验证**，文档里标"待确认"；
- Certum 免费档的当期规则与有效期：页面未给出明确条款，标"需自行核实"；
- Azure Trusted Signing 的个人开发者资格与地区限制：同上，标"需自行核实"。

文档已从 `README.md` 与 `TROUBLESHOOTING.md` 双向链接。

### 验证

- 全量 `pytest tests -q`：**通过**（文档改动不触发任何闸门）；
- 未改动任何代码，只新增/链接文档。


### 23. 把「做好了但点不到」的能力接上界面：图片评分 + 按相机筛选

上一轮的只读分析列出了一批"实现完整、但没有 UI 入口"的方法。
这轮接了其中**用户最能直接受益**的两个。

#### A. 图片评分（右键「⭐ 评分」）

`ImageStorage.set_rating()` 早就实现了，详情面板也一直在显示
「评分 ★★★」，但**界面上没有任何打分入口** —— 星级永远是空的。

现在右键菜单加了「⭐ 评分」子菜单（5→0 星，0 为"清除评分"，当前星级有标注），
支持多选批量打分。评分的实际用途是**筛废片**：1~2 星标记待删、5 星标记精选。

配套加了筛选栏的「全部评分」下拉（`min_rating`），语义是"不低于所选星级"。

#### B. 按相机筛选（筛选栏「全部相机」）

`list_cameras()` 按 EXIF 机型聚合计数，之前没有任何调用点。
现在筛选栏有「全部相机」下拉（显示机型与张数），可与其他条件叠加。
**没有 EXIF 机型时会自动隐藏**这个下拉，不占空白位置。

同时把两个新筛选条件写进了"范围提示"（如「3 星以上 · 相机「Canon R5」」），
用户能一眼看出当前在看哪个子集。

#### 没有接的

`set_album_cover` **未接线**，原因很具体：数据库有 `cover_path` 且能写入，
但界面上**没有任何地方显示相册封面** —— 要接线得先做相册卡片（展示层）。
已更新 `docs/ARCHITECTURE.md` 的清单说明这个前置条件。

**新增闸门** `tests/board_gallery/test_gallery_rating_camera.py`（11 用例）：
评分写入/批量/越界钳位（99→5、-3→0）/清除、`min_rating` 的"不低于"语义、
相机聚合与空机型过滤、按相机筛选、两条件叠加。

### 验证

- 全量 `pytest tests -q`：**通过**，**1190 个用例**（此前 1179）、2.73 分钟；
- 图库用例 139 → **150**；
- 图库页构造验证通过（评分下拉 6 项、相机下拉无 EXIF 时隐藏）。


### 22. 文档板块三个 UI 页补测试（1364 行零覆盖 → 344 个用例），并修一个真实缺陷

`sheets_page` / `security_page` / `merge_page` 此前**零测试引用**，
是当时全项目最大的测试空白。三个页面都是 GUI 面板，测试挑的是
**不依赖完整交互也能验证的逻辑**。

| 页面 | 行数 | 新增用例 | 测什么 |
|---|---|---|---|
| `sheet_page.py` | 622 | 13 | 取列值（列名不存在/行列不齐）、当前表格定位、换文档后状态清理、公式计算与非法公式 |
| `security_page.py` | 421 | 13 | 默认规则是否覆盖五类 PII、未开文档/未选规则是否拦住、脱敏后不残留原件、**不就地改原文档**、权限保存与非法价格兜底 |
| `merge_page.py` | 321 | 15 | 术语替换解析的全套边界、`unify_terms` 的启用条件、模式切换、**主副文档同文件的拦截** |

#### 写测试时抓到的真实缺陷：无表头表格会把首行数据当列名

`SheetPanel.set_ir()` 原来无条件用 `table.header_row()` 填列名下拉框：

```python
self._fill_columns([name for name in table.header_row()])   # 不管有没有表头
```

无表头的表（例如没有标题行的 CSV）**首行其实是数据**，于是：

1. 下拉框把「第一行数据」显示成列名；
2. 用户选中它后，`_column_values()` 返回的 `body_rows()` **又包含这一行**；
3. 统计/填充结果**多算一行**，而界面看起来一切正常。

代码里本来就有为无表头准备的分支（`_fill_columns([])` 会显示「（无表头）」），
只是没接上。改为 `if table.header else []` 后该场景消失。
闸门 `test_headerless_table_shows_no_column_names` 守住（验证过改回会失败）。

#### 顺带发现：`TableData.column_index` 的 docstring 与实现不符

docstring 写「表头缺失时按 `列1/列2` 兜底」，但实现只搜索 `header_row()`，
**没有这个兜底**（真正的兜底在 `sheet.py` 的公式引擎里，那是另一处）。
这属于文档误导，未改代码，记在此处供后续澄清。

### 验证

- 全量 `pytest tests -q`：**通过**，**1179 个用例**（此前 1138）、2.38 分钟；
- 文档板块用例 303 → **344**。


### 16. 修「书架显示为空」——读库失败被静默吞掉

`ShelfView.reload()` 原来是：

```python
try:
    self._records = self._library.list_books()
except Exception:
    self._records = []      # ← 静默
```

数据库被占用 / 损坏 / 没权限时，用户看到的是**空书架**——第一反应是"我的书全没了"，
这是最容易被误认为数据丢失的场景。

现在：`swallow()` 留痕 + 把原因记进 `_load_error`，界面显示
「书架读取失败，原因：database is locked」并提示点「刷新」重试，
而不是假装"没有书"。有用例 `test_reload_failure_is_not_silent` 守住
（验证过：改回静默写法会立刻失败）。

### 17. 书库批量导入：后台化 + 不再因一个文件中断整批

`ShelfView._import_paths()` 三个问题一起修：

| 问题 | 原来 | 现在 |
|---|---|---|
| UI 冻结 | UI 线程同步循环（50MB TXT 约 256ms，100 本十几秒） | `BookImportWorker(QThread)`，`_import()` 立即返回 |
| 一批全断 | 只 `except ValueError`，`PermissionError` 会抛出去，后面的书全不导入 | 增补 `except OSError`，记入失败列表后**继续下一本** |
| 失败不记名 | 只说"N 个文件无法解析" | 列出前 3 个文件名 + 总数 |

另加重复点击保护（"正在导入中，请等这一批完成"）与 worker 引用持有
（局部变量被 GC 而线程还在跑是 PySide6 经典崩溃）。

### 18. 影视本地导入：后台化（原来会冻结数分钟）

`VideoLibraryPage._import_local()` 原来在 UI 线程调 `import_and_copy()`：

```python
self._toaster.info(f"正在导入 {len(files)} 个文件…")   # 这行都来不及渲染
videos = self._library.import_and_copy(files)          # ← 复制 GB 级文件
```

`import_and_copy` 会把外部文件**复制进影视库目录**——导入 3 个 2GB 影片
就是复制 6GB，机械盘上要几分钟，界面完全无响应。

新增 `boards/video/widgets.py::ImportWorker`（与图库/乐库/书库同一套模式），
并把 `on_progress` 接到任务栏，用户能看到进度。

### 19. 缩略图缓存：同秒编辑会永久显示旧图（修）

`_fingerprint()` 用的是 `int(stat.st_mtime)`，**秒级截断**。实测复现：

| 场景 | 修改前 | 修改后 |
|---|---|---|
| 同一秒内覆盖（且文件大小相同） | 显示**旧图** | 正确显示新图 |
| 隔 1 秒以上覆盖 | 正确 | 正确 |

而且踩中后是**永久**的：指纹不再变化，那张图会一直显示旧内容。
用户感受是"编辑了但没生效"。

改用 `st_mtime_ns`（纳秒），并保留秒级回退以兼容不支持的平台。

### 20. `dhash_file` 解码降采样（导入再快 1.5 倍）

`dhash_image` 只用得到 9×8 个像素，但 `dhash_file` 会**完整解码**原图——
4000×3000 要先解出 1200 万像素再扔掉 99.99%。实测单张 **42 ms**，
是导入成本里最大的一项（比生成缩略图的 26 ms 还高）。

加 `draft()` 让 JPEG 在解码阶段降采样：**42.2 ms → 4.3 ms（9.8 倍）**。

**改之前先验证了哈希兼容性**（这是能否改的前提）：20 张 4000×3000 照片，
18 张哈希完全一致、2 张相差 **1 位**。去重判定阈值是汉明距离 4~6，
差 1 位不影响判定 —— 新旧版本导入的同一张照片仍会被认成重复。

导入端到端：**121 ms/张 → 57.8 ms/张**（本轮起点至今累计 2.1 倍）。

### 21. 记录「已知未接线的能力」

扫描发现若干实现完整但没有界面入口的方法（`set_rating`、`set_album_cover`、
`list_cameras`、`load_all_thumbnails` 等）。**没有删**——它们是将来要用的能力，
删了是损失；但留着容易让人以为功能已可用。

改为在 `docs/ARCHITECTURE.md` 增加「已知未接线的能力」清单，
逐条写明"现状是什么、为什么还没接"，并说明处理原则（接 UI 优先用这些，
确实不需要时再删并同步删清单）。

顺带核实：`export_current_danmaku` **不是**死代码 ——
它是 Ctrl+D 已接入功能的公开包装（供菜单/测试调用）。

### 验证

- 全量 `pytest tests -q`：**通过**，**1138 个用例**（此前 1120）、2.07 分钟；
- 新增测试：`test_gallery_exif.py`（12 用例，此前 exif 零覆盖）、
  `test_book_import_thread.py`（6 用例，含 reload 与导入线程）；
- 两处闸门都验证过有效性：改回旧写法会立刻失败。


### 15. 图库缩略图：导入快 1.6 倍，切换尺寸不再卡

`thumbnails` 这条路径上有两个独立的性能问题，都是实测才发现的。

#### A. 解码阶段就降采样（单张 40.9 ms → 4.3 ms）

`make_thumbnail` 原来是"完整解码 4000x3000 → 再缩到 320"。JPEG 支持在**解码阶段**
降采样（`Image.draft()`），只要在 `load()` 之前调用：

| 4000x3000 JPG → 320 | 耗时 |
|---|---|
| 完整解码再缩（旧） | 40.9 ms |
| 先 draft（新） | **4.3 ms** ← 9.4 倍 |

**这不是降质量**：draft 只选不小于目标尺寸的 DCT 缩放档（1/1、1/2、1/4、1/8），
最终仍用 LANCZOS 缩到目标大小，肉眼无差别。

`open_oriented` 增加了**可选**参数 `draft_size`，只有生成缩略图时才传 ——
**编辑/预览必须不传**，那些场景要原始分辨率（有专门用例守住这点，
否则用户会基于一张 500 宽的模糊图做裁剪）。

端到端：导入 20 张 4000x3000 从 **2420 ms → 1501 ms（121 → 75 ms/张）**。

#### B. 滚动时不再现场生成（原来一批 40 张卡 2.7 秒）

`_load_row()` 原来同步调用 `thumbnail()`，缓存未命中就**当场解码**。
`fill_thumbnails_lazily` 每批 40 张 → 主线程一次卡 **2.7 秒**。
导入 500 张后"补缩略图"过程会让界面持续卡顿几十秒。

改法：

- `ThumbnailCache` 增加 `get_cached()`（只查不算）与 `needs_generation()`（一次 stat）；
- `_load_row()` 改为**只取已有缓存**，未命中的先显示占位、交给后台线程；
- 新增 `ThumbnailWorker(QThread)`：后台生成、`produced(row, path)` 回主线程 setIcon
  （`QPixmap` 只能在主线程创建，所以只回传路径）；带去重集合，快速滚动不重复排队。

#### 一个被证伪的假设

我原以为"缩略图是滚动到才生成"。实测发现**导入阶段就已经生成**（这也是导入
121 ms/张的主因），所以普通滚动并不卡。真正会触发大量现场生成的场景是
**拖动"缩略图尺寸"滑块**（换尺寸 → 全部重生成）。无论如何，改成后台生成后
这两种场景都不会再冻界面。

**新增闸门** `tests/board_gallery/test_gallery_thumbs.py`（7 用例）：
`get_cached` 绝不生成（并断言耗时 < 50 ms）、已生成能取到、
`needs_generation` 如实、**带 draft 的生成必须比完整解码快**、输出尺寸不变、
降采样后图像仍有内容、**编辑路径必须拿原始分辨率**。


### 13. 修正上一轮引入的隐患：长事务会独占锁（改成分批提交）

上一轮把三个批量导入包进**一个事务**，速度是快了，但埋了个雷：
`transaction()` 全程持有 `_lock` 与 SQLite 写锁，导入 5000 张照片要 20 秒以上，
这期间主线程的任何查询都得等 —— 界面看起来像死了。

实测 4000 行写入（另开一个读线程持续查询）：

| 方式 | 耗时 | 读线程最大延迟 |
|---|---|---|
| 逐条提交 | 5779 ms | 5.6 ms |
| **整个导入一个事务** | 241 ms | **236.3 ms** ← 锁被独占 |
| **分批 200** | **269 ms** | **16.9 ms** ← 最佳 |

改成分批：速度几乎不变（269 vs 241 ms），**读延迟降 14 倍**。
基类新增 `BATCH_SIZE = 200` 并写清了这个权衡的实测数据；
图库/影视/乐库三个 `import_paths` 都改成分批提交。

### 14. 乐库批量导入会冻住界面（导入 1000 首要等 5~15 秒）

图库早就有 `ImportWorker(QThread)` 在后台导入，**乐库一直漏了**：

```python
def _import(self, paths):
    files = scan_audio_files(paths)
    tracks = self._library.import_paths(files)   # ← UI 线程同步执行
```

导入 1000 首要读 1000 次元数据、算时长、写库，全程卡住界面。
用户的典型反应是"以为卡死了，再点一次"→ 于是导入两遍。

新增 `boards/music/widgets.py::ImportWorker`（照图库同一套范式：
`progressed` / `finishedImport` / `failed` 信号 + `cancel()`），
`_import()` 改为启动 worker 并立即返回；完成/失败在主线程刷新界面。

另外补了两处防护：

- **重复点击保护**：已有导入在跑时提示"正在导入中，请等这一批完成"；
- **worker 必须持引用**（`self._import_worker`）—— 局部变量会被 GC 回收，
  而线程还在跑，是 PySide6 里经典的崩溃来源。

**新增闸门** `tests/board_music/test_music_import_thread.py`（5 用例）：
导入跑在非主线程、进度逐条回传、失败通过信号报告不崩、可取消、
`_import()` 立即返回且保存了 worker 引用。
验证过有效性：把 `_import` 改回同步调用，用例立刻失败并报"阻塞了 X 秒"。


### 11. 批量导入慢的真因：`transaction()` 其实没生效（31 倍提速）

这轮换了个方法：不看代码，**跑真实使用路径的规模测试**（大文件、大库、批量导入），
看用户实际会等多久。

#### 怎么发现的

模拟"导入 5000 张照片"，写入耗时 **7.3 秒**（1.45 ms/张）。SQL 本身不慢，
于是量了一下 commit 的开销 —— 2000 行写入：

| 方式 | 耗时 |
|---|---|
| 逐条提交（现状） | **2753 ms** |
| 单事务 | 58 ms ← **47 倍** |
| `executemany` | 54 ms ← 51 倍 |

#### 根因有两层

1. `SqliteStore.execute()` 里是**无条件** `self._conn.commit()` ——
   即使写在 `with store.transaction():` 块里，每写一条仍然各自 fsync 一次。
   **`transaction()` 只是包了层壳，没有任何批量效果。**
2. 各板块 storage 子类里还有 **60 处**直接 `self._conn.commit()`
   （gallery 24 / music 16 / video 15 / book 5），完全绕过基类的标志。

#### 改法

- `SqliteStore` 增加 `_commit()`：事务块内跳过，块外照常提交；
- `transaction()` 用 `_in_transaction` 标志真正抑制块内提交，退出时统一提交，
  异常则整体回滚（不留半批脏数据）；
- 60 处 `self._conn.commit()` 全部改走 `_commit()`；
- 三个批量导入路径（图库 / 影视 / 乐库 `import_paths`）包进事务。

效果（3000 行，同一进程实测）：

```
逐条写入 : 4306 ms
事务写入 :  140 ms      ← 31 倍
```

**块外行为完全不变**（仍逐条提交），既有调用方不受影响 —— 全量 1108 用例验证。

#### 顺带修掉的静默失败

图库批量导入里 `except Exception: result.failed += 1` 完全没有留痕 ——
用户只看到"失败 N 张"，不知道是哪张、为什么。已加 `swallow("导入单张图片", error)`。

### 12. 规模测试的其它结果（都健康，无需改动）

| 场景 | 结果 |
|---|---|
| 24 MB / 500 章 TXT 解析 | 256 ms |
| 书库 500 本写入 | 689 ms；列表 1 ms；按路径查 200 次 2 ms |
| 图库 5000 张列表全量 | 32 ms；分页 2 ms；按 sha256 查 500 次 5 ms |
| 影视 5000 条列表全量 | 34 ms；按剧集查 100 次 7 ms |
| 文档 800 块解析/体检/差异 | 4 / 6 / 2 ms |
| 图库相似查找（2000 张库） | 12.7 ms/次（全表算 hamming，可接受） |

**新增闸门** `tests/platform/test_db_transaction.py`（7 用例）：
事务外照常提交、块内合并、异常回滚、标志复位、嵌套安全、
**子类 upsert 也被批量**、以及一条相对性能断言 —— 若有人把 `_commit()` 改回
直接 commit，这条会立刻失败并提示"很可能是某处又直接调用了 `self._conn.commit()`"。


### 10. 继续补薄板块：乐库 / 图库（1057 → 1101 个用例）

按"代码量 : 用例数"重新排序，发现真正偏薄的不是转换（41.6 行/用例，已经不错），
而是**乐库（80.6）与图库（79.8）**。两者各有若干核心模块零测试引用：

| 模块 | 行数 | 性质 | 新增用例 |
|---|---|---|---|
| `core/music/sources/matcher.py` | 约 95 | **跨源换歌匹配**（判断两个源是不是同一首） | 25 |
| `core/gallery/edits.py` | 416 | 图像编辑步骤（裁剪/旋转/滤镜/调色/马赛克/边框） | 30 |

`matcher.py` 值得测的理由很具体：它错了会导致**换源后放错歌、下载文件存错名字**，
而用户很难描述（只会说"放错了"）。

### 补测时又抓到两个缺陷

**A. 中文书名号没被归一化**

`normalize` 剥掉了 `（）()[]【】`，却没处理 `《》〈〉「」『』`：

    normalize("《稻香》") → "《稻香》"     # 和 "稻香" 永远匹配不上
    title_score("稻香", "《稻香》") → 低分   # 同一首歌被判成不同

歌名写成《稻香》在中文音乐站非常常见，于是同一首歌在不同源之间认不出来。
已把书名号/引号加入标点剥离列表。

**B. `best_match(original, None)` 抛 TypeError**

换源时"没有可用候选源"是正常情况（全部源被禁用 / 都在降级），
调用方会传 `None`，而实现直接 `for candidate in candidates:` → 崩。
一个源都没配好时，整个换源流程会被打断。已改为 `candidates or ()`。

### 另外改进：未识别的编辑步骤不再完全静默

`apply_step` 对不认识的步骤名是 `return image`（静默丢弃）。
**不抛异常是对的**（一抛整个编辑栈就崩、用户之前所有改动全丢），
但完全静默会让"点了没反应"无从排查 —— 典型来源是版本升级后撤销栈里的旧步骤名。
现在会用 `debug()` 留痕（`MODU_LOG_LEVEL=debug` 可见）。

### 验证

- 全量 `pytest tests -q`：**通过**，**1101 个用例**（此前 1057）、1.88 分钟；
- 乐库用例 78 → 103；图库用例 93 → 123；
- 两个 spec 的模块清单未变，无需重新核对打包清单。


### 8. 文档板块补测试：235 → 332 个用例（三个核心模块此前零覆盖）

文档板块占全项目 31% 代码量，却是测试最薄的地方（17k 行代码只有 235 个用例）。
扫描发现 **3 个核心模块零测试引用**：

| 模块 | 行数 | 性质 | 新增用例 |
|---|---|---|---|
| `core/document/security.py` | 383 | **隐私脱敏**（身份证/手机号/银行卡/邮箱） | 29 |
| `core/document/quality.py` | 338 | 五维质量评分 + 差异对比 | 30 |
| `core/document/parser.py` | 1598 | 格式解析总入口 | 29 |

**补测试时抓到两个真实缺陷**（都已修）：

#### 缺陷 A：金额脱敏误伤普通数字（过度脱敏）

金额规则原来是裸的 `\d{3,}`，匹配任何 3 位以上数字 —— 实测：

```
「2024 年」   → 「**** 年」
「2024年1月」 → 「****年1月」
```

文档里的年份、编号、版本号全被毁掉。这比漏脱更隐蔽：用户以为"只是打码了"，
其实内容已经不可用。改成**必须带金额特征**才打码（货币符号 / 千分位 / 小数 / 金额单位），
并加了 6 个"普通数字不该动"+5 个"真金额仍要打码"的参数化用例。

#### 缺陷 B：目标关键词不切分，目标匹配形同虚设

`goal_keywords` 用 `re.findall(r"[\u4e00-\u9fff]{2,}")` 抓连续中文，而中文没有空格，
正则**贪婪匹配整句**：

```
goal_keywords("请帮我优化一下这段文字的正式程度")
→ ['请帮我优化一下这段文字的正式程度']      # 一个词都没切
```

于是"目标匹配度"退化成拿整句去正文找子串，几乎永远匹配不上、分数恒低。
改成**用停用词当分隔符**切分（零依赖），并把停用词表从 22 个补到 45 个：

```
"请帮我优化一下这段文字的正式程度" → ['文字', '正式']
"让公文更规范"                   → ['公文', '规范']
```

### 9. 文档体检：目标匹配度不再给出误导性的 100 分

`heuristic_score(ir, goal)` 的目标匹配度依赖 goal，但界面的「文档体检」
调用的是 `heuristic_score(ir)`（不带 goal）—— 而 `goal_match` 没有目标时是 **1.0**。
用户看到"目标匹配度 100"会以为文档完全符合要求，其实**这项根本没评估过**。

目标输入原来只存在于「循环美化」页，而体检是更常用的入口。现在体检前会问一句
"这次体检有具体目标吗（可留空）"：

- 留空 → 这一维显示「**未评估**」，总分只按实际评估过的**四维**算平均；
- 填了 → 正常评估，并在说明里列出未覆盖的关键词。

### 验证

- 全量 `pytest tests -q`：**通过**，**1057 个用例**（此前 1016）、2.03 分钟；
- 文档板块用例 **235 → 332**；
- 源码自检 **17/17**；六板块冒烟 **全部 true**；
- 打包：onedir **381.8 MB**、安装包已生成。
  **本轮 GUI 冒烟被 Smart App Control 拦了 8 分钟仍未放行**，
  构建本身成功（模块核验 40 项全命中），改用源码跑同一套自检 + 六板块冒烟做了等价验证。


### 5. 设置改了不生效（第二轮：建机制，而不只是修单点）

8 个设置页全都有 `save()`，但**只有 `video.py` 有一处刷新相关代码**，
其余都是"写进 QSettings 就结束"——没有任何通知机制。用户改了下载目录/开关，
已经打开的板块仍拿着旧值，感受是"改了没用"。

在 `ui_kit/settings/base.py` 建了**模块级广播**：

- 设置对话框保存完，对**每个 `save()` 返回 True 的页**发 `notify_settings_changed(page_key)`；
- 板块用 `subscribe_settings_changed(callback, owner=self)` 订阅，`owner` 销毁时自动退订。

写测试时**当场暴露了两个实现缺陷**（都已修）：

| 缺陷 | 症状 | 原因 |
|---|---|---|
| 退订永远失效 | `unsubscribe(...)` 之后回调还在 | `obj.method is obj.method` 为 `False`，bound method 每次访问都新建。改用 `__self__` 取"持有人"做身份 |
| owner 销毁后仍回调 | 会打到已删除控件上 | `destroyed` 是排队信号，测试里要 `sendPostedEvents(DeferredDelete)` 才分发；实现本身正确，用例需要等对时机 |

另外确认了一个**不该改的地方**：影视板块的下载目录、源启用状态都在
**使用时按需重读**（`video_download_dir_pref()` 每次下载都调），
`on_shown` 也已刷新源提示——所以我最初加的订阅是多余的，**已撤销**。
这正是这轮的一个教训：先确认"真的不生效"，再动手。

**新增闸门** `tests/architecture/test_settings_broadcast.py`（5 个用例）：
订阅可达、退订有效、订阅方抛异常不中断广播、owner 销毁自动退订、
对话框只广播**真的改了**的页。

### 6. 输入框缺少占位提示（可访问性）

76 个输入框里有 17 个既无 placeholder 也无预填内容，其中 5 处是**真的空白框**：

| 位置 | 用户看到的 | 补的提示 |
|---|---|---|
| 大模型「模型名」 | 空白，不知道该填什么 | `如 deepseek-chat / gpt-4o-mini / qwen-plus` |
| 大模型「多模态模型名」 | 空白 | `留空表示不启用图片能力` |
| 图库「默认导入目录」 | 空白，不知道留空会怎样 | `留空则在导入时每次询问` |
| 文档「插件目录」（只读） | 空白，像坏了 | `未启用插件目录` |
| 影视「下载目录」 | 空白 | `留空则用默认下载目录` |

其余 12 处预填了真实路径，不需要占位提示。

### 7. 核对后仍未改动的项

- 335 处 `raise` 中文消息：抽查后确认真正的技术性异常都已说清"是什么 + 可选值"
  （如"不支持的单文件压缩算法：lz4（可用：gz / bz2 / xz）"），不该为了统一而改写；
- 性能：启动 37 ms / 25 模块、各板块首次打开 16–217 ms，**已无高价值优化空间**
  （首次进入的 217 ms 是一次性导入成本，分摊到用户主动点击是可接受的）；
- 裸 `except:` 0 处；对话框缺标题 0 处。



> 这一轮**没有加功能**，是对全仓做了一次定向扫描（静默失败 / 设置类型 / 线程安全 / 死代码），
> 只改真问题。每条都有"怎么发现的"和"验证方法"。

### 1. 书打不开时只显示空白页（用户完全不知道发生了什么）

`ReaderView._load_book` 原来是：

```python
try:
    _, parsed = self._library.load_for_reading(self._record_id)
except Exception:
    parsed = None          # ← 静默吞掉
```

后果：文件被删/改名/权限不足时，用户点开一本书只看到**一片空白**，
既不知道书没了，也不知道该做什么。外层 `_open_reader` 的 `except` 也抓不到
（异常已经在内层被吃掉），所以那圈"无法打开这本书"的提示**永远不会出现**。

改法：

- 新增 `load_failed` 信号，携带**可执行的原因**（新增 `_describe_load_error`：
  `FileNotFoundError` → "原文件找不到了，请放回原位置或重新导入"、
  编码错误 → "先用转换板块转成 UTF-8 TXT"、`KeyError` → "记录已不存在，请刷新书架"）；
- 板块层接住信号：弹 Toaster + **退回书架**（空白阅读页对用户等于"卡住了"）；
- 同时 `swallow("载入书籍正文", error)` 留痕。

回归用例 `test_reader_reports_load_failure_instead_of_showing_blank`，
并**验证过它有效**：把代码改回静默写法，用例立刻失败并指出原因。

### 2. 设置存 `False` 会读成真值（"设置里关了，实际还开着"）

`QSettings` **不做类型保持**：`setValue(k, False)` 之后 `value(k)` 读回字符串 `'false'`，
而 `'false'` 在 Python 里是**真值**。实测：

```
写入 False 后无 type 读: 'false' str → 真值判断: True
指定 type=bool 读      : False
```

受影响位置：

| 位置 | 原写法 | 后果 |
|---|---|---|
| `boards/video/widgets.py` `video_auto_switch_pref` | `value(k, True)` + 外层字符串比较兜底 | 靠 `str(v).lower() not in ("false",...)` 侥幸正确，**任何新取值都会悄悄变成 True** |
| `ui_kit/media/player.py` 字幕字号 | `value(k, 22)` + 外层 `int()` | 同样靠外层兜住，写法不统一 |
| `boards/document/security_page.py` `document/price_per_1k` | `value(k, 0.0)` **无任何转换** | `"" if not price` 对字符串 `'0.0'` 判成"有值"，与"未设置"混淆 |

全部改为显式 `type=bool` / `type=int` / `type=float`。

**新增闸门** `tests/architecture/test_settings_types.py`（3 个用例），按风险分级：

- 布尔语境（`setChecked` / `bool()` / `if`）必须写 `type=bool`；
- 无任何转换的读取必须写 `type=`；
- 有 `int()/float()/str()` 外层转换（含跨行、"先赋值后转换"写法）**不强制**加 `type=`
  —— 一刀切会把 34 处合规代码误判成问题，那是噪音；
- 另有一条真机用例验证 Qt 的这个行为本身（哪天 Qt 改了会先提醒）。

### 3. 死设置键：写了没人读

`gallery/cache_cleared` 与 `video/cache_cleared_mb` 只被 `setValue` 写入，
全仓**没有任何地方读取**——纯属往注册表里堆垃圾。已删。

删除时顺手把"清了多少"变成**用户可见的反馈**（原来清完缓存界面毫无变化，
会让人以为没生效）：图库改为在缓存标签显示"已清理 X｜当前 Y"，
影视把清理量拼进缓存用量标签；顺便修掉"清了 0 字节却显示已清理 -"的文案。

### 4. 其它核对结果（**未改动**，说明为什么不改）

| 检查项 | 结果 |
|---|---|
| 24 个 `QThread` 子类 | 生命周期都有 `parent=self`，信号回传走 `Signal`，**未发现线程安全问题** |
| `SqliteStore` | 有 `__enter__`/`__exit__` + `RLock`，连接管理规范 |
| `except Exception` 宽捕获 318 处 | 静默 `pass` 已是 **0**；剩余都是"边界层"（启动兜底/UI 事件统一提示/可选能力降级），逐个看过，**不该改窄** |
| `dandanplay.py` 6 处返回空 | 已有 `last_note` 记录原因（扫描脚本没认出这种写法） |
| `video/ffmpeg_direct` 等 4 个只读不写键 | 有意的"隐藏开关 + 默认值"，不是漏写 |

### 验证

- 全量 `pytest tests -q`：**通过**（新增 4 个用例：3 个设置闸门 + 1 个书库回归）；
- 源码自检 **17/17**；六板块冒烟 **全部 true**；GUI 冒烟通过；
- 打包 v2.2.0（代码已含本轮修复）：onedir **381.8 MB** / 安装包 **152.6 MB**，
  exe 模块核验 **40 项全命中**；
- 装完真跑：dist 产物板块冒烟八键全 true、能力自检 **17/17**、
  真实启动标题含 `v2.2.0`。
  （安装包本轮被 Smart App Control 拦截，改用同一份 dist 产物验证，内容等价。）

## v2.2.0 — 性能与体积优化（启动 · 数据库 · 解密 · 包体）

> 这一版**没有新功能**，全是实测出来的性能问题。每一条都有改动前后的数字。

### 1. 启动：181 → 24 个模块（找到 v2.0.1 懒加载失效的真因）

v2.0.1 号称"板块页按需构造"，但 `AppShell.__init__` 里写了：

```python
self._factories = {spec.key: spec.page for spec in ACTIVE_BOARDS}   # ← 这里就把六个板块全导入了
```

`BoardSpec.page` 是**懒解析属性**，一取就 `importlib.import_module(...)`。
所以"按需构造"只做到了"按需实例化"，**导入**在构造主壳时就全发生了：

| 指标 | 改前 | 改后 |
|---|---|---|
| 启动加载的本项目模块 | **181 / 205** | **24** |
| `requests` 是否在启动时加载 | 是（白付 95 ms） | **否** |
| 首次进入某个板块 | 0（已预载） | 230 ms（一次性，用户主动点击时） |

改法：只保存 `BoardSpec` 本身，`go_page()` 时才取 `.page`。
另把 `core/platform/__init__.py` 的 `http` 成员改成 PEP 562 懒转发
（`requests` 自己的导入成本就有 95 ms：urllib3 63 ms + ssl 5 ms）。

**新增闸门** `tests/architecture/test_startup_budget.py`：主壳构造完成时，
加载模块数必须 ≤ 60、不得导入任何板块模块/板块内核、不得导入 `requests`。

### 2. 数据库：补三个索引，大库热查询 **6343×**

`v1_initial` 只给"列表页排序"建了索引，**按标识反查**的热路径没建。
20 万行 `video_videos` 实测：

| | 耗时 | 计划 |
|---|---|---|
| 改前 | 21.296 ms/次 | `SCAN`（全表） |
| 改后 | **0.003 ms/次** | `SEARCH ... USING COVERING INDEX` |

新增迁移 `v4_indexes`：

- `idx_video_remote(source, remote_id, episode_index)` —— 增量入库去重（每集都查）
- `idx_video_title_ep(title, episode_index)` —— 换源/匹配
- `idx_gallery_images_url(url)` —— 图库按链接反查

两个连带修正：

- **版本号用 4 不用 3**：v3 是撤掉的动漫迁移的号，从 v1.1.0 升级的库里
  `schema_migrations` 已有 `version=3`，新迁移若叫 3 会被"已应用就跳过"，
  索引永远建不上（有专门用例守住）；
- **索引只建在存在的表上**：迁移不假设前序迁移一定建好了表，缺表就跳过 ——
  否则裁剪库/老库会因 `no such table` 启动失败（这个坑是测试用例当场抓出来的）。

另外一个被证伪的"优化"：第一版写的是**部分索引**（`WHERE remote_id <> ''`），
看着更小更省，但实测查询计划**依然是 SCAN** —— 部分索引只有在查询条件能静态
蕴含索引条件时才会被使用，而调用方写的是 `WHERE remote_id = ?`。已改回普通索引。

**新增闸门** `tests/platform/test_db_indexes.py`：用 `EXPLAIN QUERY PLAN` 钉住
"必须走索引"，并覆盖 v1.1.0 老库升级与缺表容错。

### 3. AES 解密：37.7 KB/s → **0.74 MB/s**（约 20 倍）

排查慢测试时发现的**真实产品缺陷**（不只是测试慢）。`core/video/aes.py` 是纯 Python
实现（零依赖，这个设计是对的），但 `InvMixColumns` 每个分组调 16 次 `_mul`、
每次 8 轮移位：

```
cProfile：720 万次 _mul 调用，AES 解密 200KB 用 9.5 秒
→ 折算 37.7 KB/s：一个 2MB 的 HLS 分片要解密 54 秒，用户会以为软件卡死
```

改法：GF(2⁸) 的乘数在这条路径上只有 9/11/13/14 四个固定值，预先算成 256 项查表
（表由 `_mul` 生成，所以与参考实现永远一致，不存在手抄表抄错）。结果：
**37.7 KB/s → 0.74 MB/s**，FIPS-197 / NIST SP 800-38A 向量与逐字节对照全通过。

**新增闸门**：`test_video_aes.py::test_aes_throughput_is_not_pathological`
（下限 0.2 MB/s，防止有人改回逐位移位）。

### 4. 包体：388.4 → **381.8 MB**（−6.6 MB）

丢掉 Qt 自带的多语言翻译包（124 个 `.qm`）。依据：全仓 grep **没有任何 QTranslator**，
所有界面文案都是我们自己写的中文，留着多语言包纯属死重量。

同时核实并**否掉**了一个"可疑重复"：`libcrypto-3-x64.dll`（6.8 MB）与
`libcrypto-3.dll`（5.0 MB）看似重复，查导入表后确认前者被 `libssl-3-x64.dll`（Qt 网络栈）
使用、后者被 `_ssl.pyd`/`_hashlib.pyd`（Python 标准库）使用 —— **两个都在用，不能删**。

### 5. 测试套件：2.3 → **1.8 分钟**

三个用例原来在"等网络/等解密"，全部改为确定性断言：

| 用例 | 改前 | 改后 | 原因 |
|---|---|---|---|
| `test_best_variant_url_locks_the_sharpest_child_playlist` | 9.94 s | <0.01 s | 断言里 `http=None` 会真的 new 一个 HttpClient 去请求 `cdn/a.mp4`，等超时+重试 |
| `test_download_hls_decrypts_aes128_without_ffmpeg` | 14.00 s | 2.81 s | 纯 Python AES + 测试辅助加密实现（后者也改查表） |
| `test_ai_analyze_by_metadata_needs_no_upload` | 16.24 s | <0.01 s | 故意发真请求等失败；改为 patch `chat` 并**断言 prompt 里真的有元数据**（比原来更严格） |

顺带修掉一个测试隔离问题：`_download_speed_limit_bps()` 读的是**真实 QSettings 注册表**，
本机设过限速就会让下载用例按限速慢慢"等"（0.2 s → 10.5 s），测试现在显式钉死为不限速。

### 6. 杂项

- `.gitignore` 收掉工作区里另一套工具（workbuddy）的产物，`git status` 不再长期脏着
  （只忽略、不删文件）；
- 打包脚本的 GUI 冒烟把"系统策略拦截"与"真实崩溃"分开处理：前者只警告
  （等策略放行后重试 5 次 × 45 秒），后者直接 `Fail`。

### 验证

- 全量 `pytest tests -q`：**通过**，耗时 **1.8 分钟**（此前 2.3）；新增 3 个闸门文件；
- 打包 v2.2.0：onedir **381.8 MB** / 安装包 **152.6 MB**（此前 388.3 / 154.5）；
- 装完真跑（干净目录覆盖安装）：板块冒烟八个键**全部 true**、能力自检 **17/17**、
  真实启动标题含 `v2.2.0`、首页 **6 张板块卡**。

## v2.1.1 — 修复「安装后启动失败」

> **v2.1.0 的安装包是坏的**：装完点图标弹
> `Unhandled exception in script / No module named 'modu_workbench.boards.book.board'`。
> 这是我的责任 —— 两个缺陷叠加，而且**四道既有检查全部放过了它**。

### 缺陷 1：板块页的懒导入没进包（v2.0.1 引入，潜伏两个版本）

v2.0.1 为提速把板块页改成「字符串 + `importlib`」懒导入：

```python
BoardSpec(page_target="modu_workbench.boards.book.board:BookBoardPage")
# boards/base.py:  importlib.import_module(module_name)
```

**PyInstaller 的静态分析看不见字符串里的模块名。** 当时 `workbench.spec` /
`workbench_mac.spec` 里已有 `boards.convert.board`、`boards.music.board` 等，
唯独漏了：

| 漏掉的模块 | 后果 |
|---|---|
| `modu_workbench.boards.book.board` | **首屏点「墨软书库」直接崩**（就是这个报错） |
| `boards.book.context` | 书库单例初始化失败 |
| `boards.music.context` / `boards.video.context` / `boards.gallery.context` | 对应板块打开即崩 |
| `core.llm.context` | 设置页/大模型相关入口崩 |
| `ui_kit.theme_icons` / `app.selfcheck` | 主题图标、自检在冻结环境里找不到 |

已把 **14 个动态导入目标**逐个补进两个 spec 的 `hiddenimports`。

### 缺陷 2：`theme.py` ↔ `theme_icons.py` 模块级循环导入（v2.1.0 引入）

拆 `theme.py` 时把图标生成挪走，两边**模块级互相 import**：

- `theme.py` → `from .theme_icons import _icon_urls, _image_rule`
- `theme_icons.py` → `from .theme import ThemeTokens`

源码单测里 `theme` 总是先被导入，所以 1154 个测试全绿；**PyInstaller 冻结后
`theme_icons` 先被加载** → `ImportError: cannot import name '_icon_urls' from
partially initialized module`。修法：`theme_icons` 改用 `if TYPE_CHECKING:`，
运行期不再依赖 `theme`。

### 为什么四道检查都没抓到（每条都补了闸门）

| 检查 | 为什么漏 | 补的闸门 |
|---|---|---|
| 1154 个 pytest | 源码导入顺序与冻结后不同；板块页只在**点进去**时才导入 | `tests/architecture/test_no_import_cycles.py`（模块级导入图找环）+ 反向导入顺序的子进程用例 |
| `$required` 模块核验（40 项） | 它只在 exe 字节流里找**名字**，注释/字符串里的名字也算命中 | `tests/architecture/test_dynamic_imports_bundled.py`（动态导入目标 vs spec hiddenimports 逐项对齐 + 两个 spec 必须一致） |
| 打包后 `MODU_CHECK_DEPS` 自检（17 项） | 它在创建 `QApplication` **之前**就 return 了，主题组装/板块构造碰不到 | 新增**启动级冒烟** `MODU_SMOKE_BOARDS`：建主壳 + 逐个打开六个板块，写 JSON |
| 我新加的「GUI 冒烟」 | 只判断"窗口标题非空"，而启动崩溃时 PyInstaller 弹的 `Unhandled exception in script` **也是一个有标题的窗口** | 标题必须含「墨软」；出现崩溃框即失败，并**读出对话框正文**写进构建日志 |

现在的构建顺序是：静态闸门（环 / 动态导入）→ 打包 → 模块核验 40 项 →
能力自检 17 项 → **板块冒烟（六板块逐个打开）** → **GUI 冒烟（认标题、认崩溃框）** → 安装包。
任一步不过直接 `Fail`，不会再产出"装完起不来"的包。

### 验证（这一轮是**装完真跑**，不是只看自检）

- 已安装副本（`%LOCALAPPDATA%\Programs\墨软工作台`，388.4 MB）：
  - `MODU_SMOKE_BOARDS`：`theme_qss / shell / book / convert / music / video / gallery / document` **全部 true**；
  - `MODU_CHECK_DEPS`：**17/17**；
  - 真实启动：窗口标题 `墨软·工作台 · 本地离线 · … v2.1.1`，首页 **6 张板块卡**；
- 全量 `pytest tests -q`：通过（新增 2 个闸门文件 + 5 个用例）；
- 打包：onedir **388.4 MB** / 安装包 **154.5 MB**，版本资源 2.1.1。

## v2.1.0 — 单体拆分 + 异常精确化 + 文档收敛

> 这一版**有行为变更**，所以是次版本号而不是补丁号：
> 部分"失败被静默吞掉"的位置现在会**收窄异常类型并留痕**（原来 `except Exception: pass`）。
> 功能与界面不变，但**排查问题时的可观测性变了** —— 以前"什么都没发生"的地方，
> 现在 stderr 里会有一行 `[WARNING] modu: 忽略失败（保存阅读进度）sqlite3.OperationalError: ...`。

### 1. 拆掉三个大单体（合计 2177 行 → 6 个文件，公开 API 一个没动）

| 原文件 | 行数 | 拆成 | 拆分依据 |
|---|---|---|---|
| `app/main.py` | 884 | `main.py`（59）+ `app/selfcheck.py`（853） | 入口只负责"起 Qt + 开主壳"；17 项打包自检生命周期完全不同（只在 `MODU_CHECK_DEPS` 跑一次） |
| `ui_kit/theme.py` | 831 | `theme.py`（798）+ `ui_kit/theme_icons.py`（103） | 图标是"给 QSS 的 `image:` 现画 PNG"，与色板/QSS 组装无关；改图标不该碰 700 行样式串 |
| `services/media_proxy/server.py` | 586 | `fetch.py`（97）+ `handler.py`（432）+ `server.py`（122） | 取流与缓存（`fetch`）／HTTP 路由与流式转发（`handler`）／起停服务与拼地址（`server`）是三件事；`playlist` 的预热只需 `fetch`，不必 import 整个 handler |

**为什么"不影响单元模块"**：拆完每个原模块仍导出原来那些名字
（`server.py` 继续 re-export `fetch_to_cache` / `looks_like_playlist`，
`theme.py` 继续 re-export `_icon_urls` / `_image_rule`），
调用方与 1154 个测试**一行都不用改**；`__init__.py` 的 `__all__` 一个字没删。

顺带修掉两处"拆分暴露出来的旧问题"：

- `selfcheck.py` 的写结果分支原来是 `return 2 if f"write_error={error}" else 2`
  —— **永远返回 2**，且把 `error` 丢掉。现在收窄成 `OSError` 并打印原因；
- `services/media_proxy/handler.py` 里 `_allow_private()` 与新增的 `allow_private()`
  重复定义（拆分切块时露出来的），已合并为一个。

### 2. 异常精确化：69 处"静默吞"变成"精确捕获 + 留痕"

- 新增 `core/platform/logging.py`（标准库 `logging`，**零依赖**）：
  `swallow(scope, error)` 记录"被有意忽略的失败"，`debug(...)` 供高频路径用
  （`MODU_LOG_LEVEL=debug` 才输出）；
- **21 处收窄成具体类型**：关文件/关连接 → `(OSError, ValueError)` / `sqlite3.Error`、
  读设置 → `(ImportError, RuntimeError, TypeError, ValueError)`、
  读 PDF 页数/页面尺寸 → `(PdfError, OSError, ValueError, TypeError, KeyError/IndexError)`；
- **剩余 48 处保留宽捕获但不再静默**：都是"第三方回调 / Qt 槽 / 外部边界"，
  那里确实可能抛任何异常，**继续吞是对的，但必须留痕**（`swallow(...)`）；
- 判定原则写进 `docs/ARCHITECTURE.md`：「能确定类型的收窄；不能确定的留宽但留痕；
  高频路径用 `debug`」—— 并给出正反例代码。

> 诚实说明：这 69 处里，**67 处的"可吞"是我逐条看上下文确认的**（关资源、存进度、
> 记审计、可选诊断回调），另 2 处（读 PDF 页数/尺寸）保留了兜底值只是补了留痕。
> 我没有把宽捕获全部改窄 —— 对外部回调改窄会导致"回调抛了别的类型 → 整个搜索崩"，
> 那是把可观测性问题换成了稳定性问题。

### 3. 文档收敛：删 5 份、合并 1 份、刷新 6 份

**删**（都在 git 历史里，需要用 `git show` 取）：

| 删除 | 体积 | 为什么删 |
|---|---|---|
| `docs/research/kazumi_research_report.md` | 120 KB | 已移除的第七板块「动漫」的取证材料。文档在讲"如何实现一个已删功能"，只会误导后来者 |
| `docs/research/kazumi_extras_report_v2.md` | 107 KB | 同上 |
| `docs/research/kazumi_danmaku_timetable_report.md` | 50 KB | 同上（其中仍有效的"弹弹play 只读接口"事实已落在 `services/dandanplay.py` 与 `docs/BOARDS/video.md`） |
| `docs/research/kazumi_issue_mining_report.md` | 32 KB | 同上 |
| `docs/REFACTOR_PLAN.md` | 10 KB | v1.0.0 重构评审稿，结论已全部落地并**合并进** `docs/ARCHITECTURE.md`（依赖规则现在是那里的「依赖规则」一节，代码与测试的引用已同步改指过去） |

**新增**：`docs/research/README.md` 重写 —— 只剩 1 份仍在生效的 MoonTV 调研，
并给出**判断一份调研该不该留的标准**：「它描述的东西现在还有代码吗？没有就写进
CHANGELOG、材料删掉」。

**刷新**：

- `docs/ARCHITECTURE.md`：六个板块现状 / 依赖规则（含 3 个"看起来像死代码但必须存在"的例外）/
  视觉与主题分层 / **忽略失败的统一写法**；
- `docs/RELEASE.md`：自检实现位置改指 `app/selfcheck.py`、产物体积更新到 v2.1.0 实测；
- `docs/TESTING.md`、`docs/DATABASE.md`、`docs/UI_GUIDE.md`：标题去掉写死的 v1.0.0
  （每次发版都要改标题是纯粹的维护负债），并补 v2.1.0 新增闸门；
- `docs/VIDEO_PLAN.md`：状态改为"已实施并稳定 + 设计记录"，指向已删调研的段落改为
  "材料已删，依据看 git 历史"；
- `README.md`：文档索引补 `VIDEO_PLAN` / `research/README`，删掉 `REFACTOR_PLAN` 行。

### 4. 顺手修掉一个偶发失败的单测

`test_video_proxy.py::test_proxy_prefetch_warms_cache` 原来只等
`warmup_pending()` 排空 —— 但预热任务**失败**时它也会排空，于是断言偶发失败
（全量套件负载高时，假源站更容易超时）。改成等**真正想要的条件**
（缓存里已有 ≥3 片且队列排空），15 秒兜底。

### 5. 验证

- 全量 `pytest tests -q`：**通过**；
- 源码自检 **17/17**；打包产物自检 **17/17**；exe 模块核验 **40 项全命中**（v2.1.0 把拆分出的 4 个模块也纳入核验）；
- 打包 v2.1.0：onedir **388.3 MB** / 安装包 **154.5 MB**（版本资源 2.1.0）；
- 闭环：静默安装（388.4 MB）→ 已安装产物自检 **17/17** → 卸载**本轮被 Smart App Control 拦下**
  （退出码 2；同一份 `installer.nsi` 在 v2.0.0 / v2.0.1 是干净通过的，脚本逻辑未改，
  诊断方法与手动清理步骤见 `docs/RELEASE.md` 第 4 节）。

## v2.0.1 — 代码质量与启动性能优化（无行为变更）

> 纯优化版本：不改用户可见功能，只让**启动更快、代码更少**。
> 为什么不并进 v2.0.0：`v2.0.0` 已经打了 tag 并推送，同一个版本号出现两份不同二进制
> 比"多一个小版本"更糟。

### 启动性能

**启动快了一个数量级**（只算我们的开销）：

| 项目 | 优化前 | 优化后 |
|---|---|---|
| `import app.registry` | 364 ms | **5 ms** |
| 主壳 `AppShell()` 构造 | 154 ms | **4 ms** |
| 首次进某个板块 | 0（已预构造） | **11 ms** |

做法：

- **板块页按需构造**：主壳不再在 `__init__` 里把六个板块的页面全建出来（用户一次只看一个，
  没进过的板块不该先付建表格/卡片/播放器的钱）；
- **板块模块按需导入**：`BoardSpec.page` 从 `Type[QWidget]` 改成
  `page_target="包.模块:类名"` + 懒解析属性。旧写法逼着 `app/registry.py` 在模块顶层
  `import` 六个 `board` 模块及其整个 core 层，而用户此刻只想看到首页。
  `spec.page` 仍是页面类，`isinstance(page, spec.page)` 不受影响。

**删掉的多余代码**：

| 项 | 说明 |
|---|---|
| `services/media_server.py`（122 行） | v1.1.0 起只是 `media_proxy` 的转发壳，同名函数在两处各一份。已删除，调用方直接 `from modu_workbench.services import media_proxy`；测试文件同步更名为 `test_media_proxy_urls.py` |
| `boards/video/player.py`（39 行） | 播放器上移到 `ui_kit/media/` 后的转发壳（理由随动漫板块一并消失）。已删除 |
| 9 个无人引用的公开函数 | `pixmap_to_qimage`、`all_track_ids`、`default_db_path`、`audio_target_count`、`reset_app_db`、`document_db_path`、`is_m3u8_url`、`resolution_score_from_label`、`best_matches` |
| 43 处未使用的导入 | 逐个核对后删除（`Qt`/`QComboBox`/`field`/`chardet`/`os`/`Path`/`Tuple`/`Callable` 等） |
| `chardet` 依赖 | 声明了但全仓库从未使用，已从 `pyproject.toml` 移除 |

**两道新闸门（都是本轮踩出来才加的）**：

- `test_module_contracts.py::test_dunder_all_resolves`：所有模块 `__all__` 里的名字必须真实存在
  （`boards/document/__init__.py` 曾声明 `__all__ = ["board"]` 却没导入 `board`）；
- `test_module_contracts.py::test_public_facades_still_export_their_names`：
  门面模块（`ui_kit.theme` / `core.video` / `ui_kit.media` / `services.dandanplay` /
  `core.platform.textutil`）必须仍能取到对外名字 —— 批量清理"未使用导入"时
  `from PySide6.QtCore import Qt, Signal, QThread` 被整行删掉（只想删 `Qt`），
  导入期立刻 `NameError`；这条闸门与 `__all__` 检查一起把这类破坏钉住；
- `tests/smoke/test_shell_lazy_pages.py`：启动只构造首页、首次访问才建页、重复进入复用实例。

**一个"看起来像死代码、其实是架构必需"的例子（已写进注释）**：
`services/app_context.py` 优化时被删过一次，架构闸门立刻报
`boards.convert -> modu_workbench.app.context` 违规 —— 转换板块要把产物导入书库，
而板块之间不允许互相依赖，`services` 是唯一合规的中介。它已恢复并在模块注释里写明
"别当死代码删"。

### 验证

- 全量 `pytest tests -q`：**全部通过**（新增 3 道闸门：`__all__` 可解析、门面再导出、
  主壳懒构造）；
- 源码自检 **17/17**、打包产物自检 **17/17**、exe 模块核验 **40 项全命中**（v2.1.0 把拆分出的 4 个模块也纳入核验）；
- 打包 v2.0.1：onedir **388.3 MB** / 安装包 **154.4 MB**（版本资源 2.0.1）；
- 闭环：静默安装 → 已安装产物自检 17/17 → 静默卸载后目录/注册表/快捷方式全清，
  用户数据保留。

## v2.0.0 — 定版：移除第七板块「墨软动漫」

> **为什么不升 1.2 而是 2.0**：整块移除一个已发布的功能（板块入口、设置页、
> 7 张数据表、规则引擎、追番/弹幕/时间表）属于**破坏性变更**，按语义化版本必须走大版本号。
> 同时这一版把"说得到但做不到"的部分收回，文档与界面口径统一到"六个板块"。

### 移除：墨软动漫（第七板块）

**判断依据（不是拍脑袋，是实测）**：那套「规则驱动番剧采集」的可用性瓶颈不在我们的解析器，
而在源站本身。v1.1.0 后期拿 KazumiRules 官方仓库**全量 16 条真实规则逐条打真站点**：

| 阶段 | 结果 |
|---|---|
| 搜索 | 11 / 16 可用 |
| 详情 / 选集 | 11 / 16 可用 |
| **取到播放地址** | **0 / 16** |

16 条规则**全部标注 `useWebview: true`**：播放地址要么由站点网页 JS 现算，要么是站点私有加密
（实测 `encrypt=3` 这类自定义密文）。**没有浏览器内核**（v1.0.0 起的单栈决策）意味着这类站点
从根本上播不出来 —— 用户看到的是"能搜到番、能看到剧集列表、点播放打不开"。
这不是再修几处嗅探能解决的，而是架构边界；继续维护它只会持续产生"看起来能用但实际不能用"的落差。

**替代路径**：动漫类内容用「墨软影视」的类型筛选（电影 / 电视剧 / 动漫 / 综艺）搜索与播放；
影视板块的聚合源是结构化接口（苹果 CMS），播放地址可静态解析，因此**是真的能播**。

### 具体改了什么

**删除**

- `boards/anime/`（10 个模块）、`core/anime/`（13 个模块 + 2 条示例规则）、
  `ui_kit/settings/anime.py`、`ui_kit/media/rule_sources.py`（把规则包装成影视源，仅动漫用）；
- `core/platform/migrations/v3_anime.py`（7 张 `anime_*` 表 + 4 个索引）；
- `tests/board_anime/`（13 个测试文件）、`docs/BOARDS/anime.md`；
- 板块注册、设置页条目、`app/context.py` 的三个动漫单例、`paths.py` 的动漫目录函数、
  `main.py` 的 `anime_core` 自检项、两个 spec 与 `build_app.ps1` 的动漫 hiddenimports。

**迁移（不破坏老库）**

- `MIGRATIONS` 收回到 `v1_initial` + `v2_document`，`LATEST_VERSION = 2`；
- **不写"删表"迁移**：从 v1.1.0 升级的库里 `schema_migrations.version=3` 与 7 张 `anime_*` 表
  原样保留（不再被读写），既不静默销毁用户当年追的番/进度，也让"降级回 v1.1.0"仍可用；
  想回收空间的用户可按 `docs/DATABASE.md` 的 `DROP TABLE` 片段自行清理；
- `schema_version > LATEST_VERSION` 是**合法状态**（`apply_migrations` 只跳过已应用版本）。

**保留 / 迁移的能力（重要的"不连带损失"）**

- **弹幕**没有随板块消失：弹弹play 只读客户端从 `core/anime/danmaku.py` 迁到
  `services/dandanplay.py`，由「墨软影视」使用；设置项仍在「设置 → 影视 → 弹幕」，
  且**保留读取旧键 `anime/danmaku_*` 的兜底**（老用户升级后不会突然"弹幕用不了"）；
- 集数解析 / 标题归一化从 `core/anime/models.py` 迁到 `core/platform/textutil.py`
  （共享层），语义与正则**逐字保留** —— 归一化结果参与弹幕缓存文件名哈希，
  改了会让老缓存全部失配。

**文案与文档一致性（此前真的错过）**

- 副标题从「…图库 · 文档 · 动漫」收回「…图库 · 文档」，主窗口标题/首页说明同步；
- 新增架构闸门 `test_removed_anime_board_leaves_no_trace`：目录必须已删除、
  且源码里不得再 import `boards.anime` / `core.anime`（注释里的历史说明不算）；
- `test_app_slogan_mentions_every_active_board` 增加反向断言：
  **已移除的板块名不得再出现在副标题里**；
- `docs/VIDEO_ANIME_PLAN.md` → `docs/VIDEO_PLAN.md`（收窄为影视方案，动漫部分改为
  "历史参考"说明并保留调研报告作为决策留痕）；`README` / `docs/DATABASE.md` /
  `docs/TESTING.md` / `docs/RELEASE.md` 同步。

**测试**

- 删除 13 个动漫测试文件；其余板块测试全量通过；
- `test_db_schema` 新增断言：全新库**不得**再创建 `anime_*` 表；
- 架构闸门全部通过（板块清单、spec hiddenimports 一致性、BOM、版本单一来源）。

### 升级须知（从 v1.1.0）

1. 直接覆盖安装即可，用户数据（书库/乐库/影视/图库/文档/大模型）不受影响；
2. 旧的 `anime_*` 表与 `%APPDATA%\ModuWorkbench\anime\` 目录**不会被读取**，可自行删除；
3. 曾在「设置 → 动漫 → 弹幕」填过弹弹play 凭证的用户无需重填（影视板块会读旧键）；
4. 若你确实需要动漫板块，请留在 v1.1.0 —— v2.0.0 之后的代码不再包含它。

## v1.1.0 — 使用反馈修复（影视搜索"一直搜索中" · 动漫一键导入规则库）

两条来自实际使用的反馈，都定位到根因并修掉，另有 4 个在排查过程中挖出的真实缺陷。

### 影视：搜索"一直搜索中，没有反馈"

**根因不是界面**：13 个内置源里 `archive.org` 从本机完全不可达，它自己的客户端要
"15s × 3 次 + 退避"才放弃（实测单源 >45 秒）；而 `search_all` 是**顺序遍历且无单源超时**，
整条搜索被它拖住约 50 秒；结果又要等**所有**源结束才上屏 —— 于是界面只有"搜索中…"。

修法：并发跑（8 线程）+ **单源 9 秒预算** + **`shutdown(wait=False)`**（这点是必须的：
`with ThreadPoolExecutor` 的 `__exit__` 会 `shutdown(wait=True)`，把卡住的源**又 join 回来** ——
实测 9 秒判完却又等了 84 秒，总耗时 93.8s，用户端照样卡死）+ **边搜边显示**。
结果：**93.8s → 9.0s**，第一个源 **0.7 秒**就有结果上屏。
顺序语义不变（按源顺序归并，不是完成先后），`sort_by_speed` 与去重行为与改前一致。

### 动漫：没有规则 → 一键导入官方规则库（含首次自动）

新增 `core/anime/rule_repo.py` + 规则页「⤓ 导入官方规则库」按钮，
并**首次进规则页且一条可用规则都没有时自动导入一次**（内置示例只是模板，不算数）。

**不做"随包内置别人的规则"**：规则是第三方内容（仓库 MIT 的是代码，规则本身不是），
且寿命以周计 —— 打包进去用户拿到的是"一堆坏规则"。改为从上游拉取后落地成本地文件，
用户可自己改/删/禁用。

多源回退是**真机实测逼出来的**：本机 `raw.githubusercontent.com` 与 `cdn.jsdelivr.net`
都是 `WinError 10054`，只有 `api.github.com` 通，所以按 raw → jsDelivr → GitHub API
顺序试，并记住成功的源（否则每次先撞两次墙，实测探测一次 16.7 秒）。
一次性标记文件防止"删光规则后每次进页面都联网重拉"。实测全部 17 条约 28 秒导入完成。

### 排查中挖出的 4 个真实缺陷（动漫规则兼容性）

用 KazumiRules **全量 16 条真实规则**逐条打真站点时发现：

| 症状 | 根因 | 修法 |
|---|---|---|
| 搜索命中 0 条 | 子选择器写成 `//div/a[2]` 绝对路径，XPath 里 `//x` **在元素上求值也从文档根开始** → 每条命中都取到全页第一个匹配（实测取到站点头部的 `IOS用户使用欧路Via浏览器…全功能拦截`） | 元素上遇绝对路径先作用域化（`//x` → `.//x`） |
| 详情 404、选集全空 | `searchResult: "//h2/a"` 指向 `<a>` 元素，我们取的是**文本**（标题），地址变成 `https://站/葬送的芙莉莲` | `<a>` 优先取 `href` |
| 整条规则崩掉 | 某规则写 `"searchResult": "//"` —— 合法 JSON、**非法 XPath** | 空路径当成"字段没写" |
| 播放地址拿不到（可救的） | `player_aaaa.url` 解出来是**另一个播放页**（`encrypt=1` + 百分号编码） | 支持"再嗅探一层" |

另修 `bodyType` 未读（`form`/`text` 会发错 Content-Type）与
query/body 里 bool/int 被字符串化（`"enabled": true` → `"true"`，严格校验的接口会 400）。

### 诚实边界（未变）

KazumiRules 这批 16 条**全部 `useWebview: true`**：搜索 11/16、详情选集 11/16 可用，
但**取到播放地址 0/16**（站点 JS 算或私有加密）。没有浏览器内核这条边界是硬约束 ——
能播与否取决于源站，不是规则写得对不对。文档与界面都这么写。

## v1.1.0 — 第六大板块「墨软影视」播放/下载链路重写（A 阶段：共享层）

这一版**只为解决三个用户感知最强的症状**：帧率低、播放卡、下载慢。
根因排查（全部带 `文件:行` 证据）见 `docs/VIDEO_PLAN.md` 第 1.2 节
（该文档原名 `VIDEO_ANIME_PLAN.md`，v2.0.0 移除动漫板块后改名）——
**三个症状的根因都在我们自己的链路上，不是"源不好"**。

### 症状 → 根因 → 修法

| 症状 | 原来的根因 | 现在 |
|---|---|---|
| **帧率低** | 全仓没有任何硬解/后端配置（`QT_FFMPEG_DECODING_HW_DEVICE_TYPES`/`QT_MEDIA_BACKEND` 零命中），1080p H.264 全靠软解，CPU 打满 | `ui_kit/media/decode.py`：启动时（早于任何 QtMultimedia 使用）注入 `QT_MEDIA_BACKEND=ffmpeg` + `QT_FFMPEG_DECODING_HW_DEVICE_TYPES=d3d11va,d3d12va,dxva2`；设置页可关硬解/可回退纹理转换 |
| **播放卡**（网络） | 代理每分片 `requests.get` 新开 TLS、`upstream.content` 全量缓冲、所有响应 `no-store`、失败还带 3 次 0.6s 退避 | `services/media_proxy/`：每主机一个 `Session`（连接池 8/16）+ `iter_content` 流式转发 + 分片磁盘 LRU 缓存（配额/预取/清单短缓存/预热）+ 分层超时与重试（分片失败 ≤150ms 快速 502） |
| **播放卡**（选路） | `library._lock_best_variant()` 永远锁最高码率（`max(rank)`） | `core/video/abr.py`：`BandwidthMeter`（P50 滑动窗口）+ `pick_variant`（策略 `auto/highest/1080p/720p/lowest`）+ 卡顿降档最多 2 次后换源 |
| **起播卡死** | `player._check_native_started()` 等 20s 只改一行文字 | 三段自愈 3s 降档 / 8s 换源 / 20s 建议先下后播（`stallDetected(reason)` 信号），判定从"看 mediaStatus"改为"看 position 是否前进" |
| **下载慢** | `download_many` 逐条串行；分片严格串行 + 每片新连接；无断点续传；走 ffmpeg 时分片仍过 Python 代理 | `core/video/segment_downloader.py`：并发 6（可配 2–16）+ **每线程一个 Session** + **单写线程保序落盘** + `.parts` 断点续传 + 坏片重试（3 次退避）+ 令牌桶限速 + 速度/ETA；`download_many` 集间并发 2；ffmpeg 路径**优先直连**（探测到 `-headers` 就跳过本机代理），并补齐 `-reconnect*` 等按版本探测的网络参数 |

### 新增/搬移的模块

| 模块 | 说明 |
|---|---|
| `services/media_proxy/{server,upstream,cache,playlist,config,stats,state}.py` | 代理实现（旧 `services/media_server.py` 变成**同名同签名转发壳**） |
| `core/video/abr.py` | 带宽估算 + 档位选择 + 降/升档控制 |
| `core/video/segment_downloader.py` | 并发分片下载器（续传/重试/限速/合并/remux） |
| `ui_kit/media/{surface,player,danmaku,subtitle,decode}.py` | 共享媒体层：视频面、播放器窗口、弹幕轨道与性能分级、外挂字幕、硬解注入 |

**为什么播放器搬到 `ui_kit/media/`**：架构闸门禁止板块之间互相引用，而第七大板块
「墨软动漫」必须复用同一个播放器 —— 共享层是唯一合规落点（方案 4.0）。
`boards/video/player.py` 保留为转发壳，旧调用方与旧测试不受影响。

### 渲染与体验

- 渲染路径从 `QVideoWidget`（原生窗口，**无法叠加任何东西**）换成
  `QGraphicsView + QGraphicsVideoItem`：这是字幕/弹幕/诊断浮层的前提；
  `video/render_path=widget` 可一键回退，`auto` 时构造失败自动回退；
- 弹幕：性能分级（借 LunaTV-EE 公式 → 同屏 25/40/60）、轨道分配器（不重叠、放不下就丢）、
  关键词 + `/正则/` 屏蔽（只读弹弹play，默认关闭）；
- 字幕：外挂 `.srt/.vtt` 解析 + 同名自动匹配（内嵌字幕轨 Qt 无 API，**不做**，文档如实标注）；
- 自动连播（3 秒倒计时可取消）+ `F12` 播放诊断（后端/硬解/代理命中率/卡顿次数，可复制导出）；
- 新快捷键：`N`/`P` 上下一集、`D` 弹幕、`[`/`]` 调速、`Ctrl+S` 复制诊断。

### 设置项（新增）

`video/media_backend`、`video/hw_decode`、`video/hw_texture_conversion`、`video/hw_profile_mismatch`、
`video/render_path`、`video/quality_policy`、`video/auto_downgrade`、`video/auto_upgrade`、
`video/auto_next`、`video/buffer_mode`（省流/均衡/高质）、`video/cache_quota_mb`、
`video/prefetch_segments`、`video/proxy_concurrency`、`video/segment_concurrency`、
`video/download_parallel`、`video/download_speed_limit_mbps`、`video/segment_concurrency_enabled`、
`video/ffmpeg_direct`、`video/danmaku_*`。默认值都偏向"接近现状但不卡"。

### 性能预算（`tests/board_video/test_video_nonfunctional.py`）

代理吞吐 ≥ 60 MB/s、缓存命中 ≥ 60 MB/s 且**不回源**、分片下载 ≥ 40 MB/s、
代理首字节 < 120 ms、1000 条弹幕轨道分配 < 50 ms、2000 行清单改写 < 200 ms。

### 测试

新增 4 个测试文件（`test_video_proxy.py`、`test_video_segment_downloader.py`、
`test_video_abr.py`、`test_video_media_shared.py`）+ 1 个性能预算文件；
`pytest tests/board_video tests/architecture` 全绿。旧用例中"锁最高清晰度"那条改为
显式使用 `quality_policy=highest`（回归安全阀），并新增自动策略用例。

### 兼容性与边界（诚实标注）

- 旧 API（`media_server.*`、`VideoPlayerDialog`）全部保留，行为默认不变，只是更快；
- **硬解是进程级环境变量**：改设置后需重开播放窗口（必要时重启应用）才生效；
- 代理默认**拒绝访问环回/私网目标**（SSRF 收口）：本机 fixture 需 `MODU_MEDIA_ALLOW_PRIVATE=1`；
  缓存目录可用 `MODU_VIDEO_CACHE_DIR` 覆盖；
- 代理只做"**逐源开关 + Referer 三段回退**"，**不做**服务端转码（对标项目也都没有）。

## v1.1.0 — 第七大板块「墨软动漫」（规则驱动采集 + 追番 + 弹幕）

新增第七大板块，与影视板块**定位不重叠、数据不干扰、共享同一套播放/代理/下载**。

### 新增能力

| 能力 | 落地 |
|---|---|
| **规则驱动的番剧采集** | `core/anime/{rules,selectors,engine,sniffer}.py`：字段与 Kazumi 规则一一对应（便于直接吃 KazumiRules 仓库的 API 规则）、同时支持 **XPath / CSS / JSONPath 子集**、`@keyword`/`@source`/`@roadIndex`/`@episodeIndex` 等占位符、`api` 等级门控（超版灰显）、`deprecated` 灰显、`useWebview` 明确提示"可能解析失败" |
| **播放页静态嗅探** | `sniffer.py`：苹果 CMS `player_aaaa` → `/player/` 接口 → JSON 扫描 → 正则（含 `atob`/`unescape`/`\uXXXX` 解码）→ iframe 递归（≤3 层）；失败给**可读原因** |
| **多规则搜索与合并** | 搜索页**边搜边出**（每条规则完成即插卡），同一归一化标题的多规则结果合并成一张卡（可展开选来源） |
| **番剧库 / 追番 / 继续观看** | `anime_series`/`anime_favorites`/`anime_progress`/`anime_history` 四张表 + **更新提醒**（`update_check.py` 并发 2、可取消，正确处理"总集数变多"）—— 这是对标项目 Kazumi **还没做**的差异项 |
| **规则管理** | 规则页：启停 / 导入（文件·剪贴板·URL·批量）/ 导出 / 编辑 / 删除 / **试跑面板**（逐步显示 搜索 URL → 命中 N 条 → 作品名与链接 → 详情线路数与集数 → 第一集解析结果与嗅探策略，失败给出具体原因）/ 订阅（拉取→逐条校验→**校验失败保留旧版本**） |
| **弹幕（只读）** | `core/anime/danmaku.py`：弹弹play 官方接口 + 签名鉴权 + 标题/集数匹配 + XML 缓存（TTL 6h/72h）+ 本地 XML 导入导出；渲染用共享层（性能分级 60/40/25 + 轨道分配 + `/正则/` 屏蔽） |
| **Bangumi 元数据** | `bangumi.py`：只读、约 1 req/s 限速、内存 TTL 缓存；拿不到降级为规则数据（如实标注"元数据是可选项"） |
| **下载** | 复用共享下载器（并发 6 + `.parts` 断点续传 + 限速），`anime_download_tasks` 记录任务，支持整季下载（集间并发 2） |
| **数据可携带** | 追番/进度/设置的 **JSON 导出/导入**（不做 WebDAV —— Kazumi 的 WebDAV 是缺陷最密集的功能） |

### 新增结构（迁移 `v3_anime`，纯增量）

七张表：`anime_rules` / `anime_series` / `anime_favorites` / `anime_progress` /
`anime_history` / `anime_danmaku` / `anime_download_tasks`（索引 4 个，全部 `idx_anime_` 前缀）。

### 新增共享层（两个板块共用，架构闸门要求）

`ui_kit/media/{player,danmaku,danmaku_bridge,rule_sources,subtitle,surface,decode}.py` ——
播放器与弹幕桥、规则源适配器都住在共享层，因为**板块之间不得互相引用**。
`core/anime/providers.py` 保留为转发壳。

### 与影视板块同时补齐的收尾项

- **V3-e 源测速与排序**：`SourceHealth` 增加延迟 P50 与实测速度，打分公式
  `分辨率 40% + 速度 40% + 延迟 20%` × 权重加成（借 LunaTV-EE），源设置里可**测速**并可选排序（默认关，保持行为可预期）；
- **V3-f 图片代理**：搜索/详情/番剧库/历史的海报统一走 `media_proxy.image_url()`，防盗链 403 不再破图；
- **V2-c 暂停/继续 + 失败重试**：下载工具栏加「暂停下载/继续下载」与「重试失败项」，
  暂停后已下载分片保留在 `.parts/`，继续即续传；
- **V3-c 弹幕取数**：影视板块播放器接上弹弹play（只读）+ 本地 XML 导入导出（弹幕按钮右键菜单）；
- **P2 广告分片跳过**（实验性、默认关）：只跳"时长明显短于中位且与前一 jump 片不同目录"的分片，最多 3 片，并明确提示跳了哪些；
- **D-b 离线画质增强**：`core/video/enhance.py` + 「我的视频 → 增强」入口（放大档
  `scale=1920:1080:flags=lanczos,unsharp=5:5:0.8`，重编码档保持分辨率压码率）。
  **这不是 Anime4K 实时超分** —— 实时超分需要 libmpv 播放内核 + 着色器管线，与单栈决策冲突；
  我们做的是下载完成后的一次性 ffmpeg 处理，只对本地文件生效、可取消、有真 ffmpeg 的端到端测试。

### 测试

新增 `tests/board_anime/` 九个测试文件（约 3 900 行），覆盖规则/选择器/嗅探/引擎/存储/
更新检查/弹幕/Bangumi/规则源适配/界面；`tests/board_video/test_video_danmaku.py` 覆盖影视侧弹幕接线；
`tests/platform/test_db_schema.py` 守住 `anime_*` 七张表；`app/main.py` 新增自检 `anime_core`
（18 项自检全部通过）。

### 修掉的两个真实缺陷（集成测试暴露）

1. `engine.resolve(detail, road, episode)` 的形参错位：把 `road` 当成了剧集，
   导致 `_episode_url` 永远返回空串、解析必然报"该集没有可用的播放页地址"；
2. 设置页长路径标签把整页最小宽度顶到 750px 以上（`QLabel` 开了 wordWrap 也按最长
   不可断行算），默认窗口下出现横向滚动条 —— 现在在路径分隔符后插入零宽断点。

### 明确不做（如实标注）

不内置浏览器内核/不执行页面 JS（**纯 JS 渲染站点无法支持**）、不做实时 Anime4K 超分
（改离线 ffmpeg 增强）、**不发送弹幕**、不做 DLNA/一起看、不做 WebDAV 同步、不做自动过验证码。

### 打包实测（安装 → 自检 → 卸载闭环）

`packaging/build_app.ps1 -Installer` 全程跑通：onedir **388.5 MB** / 安装包 **154.7 MB**，
exe 字节流模块核验 **48 项**命中；静默安装到 `%LOCALAPPDATA%\Programs\墨软工作台`（388.6 MB，
随包 `ffmpeg`/`ffprobe` 在位）→ **已安装产物自检 18/18 通过** → 静默卸载后安装目录、
注册表项、快捷方式全部清干净，而 `%APPDATA%\ModuWorkbench\modu.db` 用户数据保留。

顺带加固了一处**会误报通过**的自检：`ffmpeg_tools` 原先"定位不到 ffmpeg 也算通过"，
而冻结运行时会回退到系统 PATH，于是随包丢失也照样报 true。现在冻结运行时必须
`_MEIPASS/tools/ffmpeg` 存在**且**解析出的 ffmpeg 来自包内；反向验证（藏起随包 ffmpeg）
会得到退出码 1 + `ffmpeg_tools=false`。详见 `docs/RELEASE.md` 第 7 节。

## v1.0.5 — 第六大板块：墨软文档（含三批自查修复）

新增第六大板块「墨软文档」（一站式文档工作台），并新增 `doc_*` 五张表（迁移 `v2_document`，
纯增量、可重复执行）。**不改动任何既有板块的代码、数据与界面**：新板块以一个独立包接入
（`boards/document/` + `core/document/`），在 `ACTIVE_BOARDS` 中追加在末尾，首页与顶栏自动出现。

> 本版是"先做出来、再自查补齐"的一版：初版交付后连着做了三批自查修复
> （设置接线与导出/合并安全 → 图片不丢/人工确认/打印/布尔设置 → Word 文字不丢/标题不凭空造/
> Markdown·HTML 图片/素材缓存管理），并在 v1.0.5 把版本号、安装包与文档一并收尾。

### 新增能力（对应需求 6.2 ~ 6.8）

| 需求 | 落地 |
|---|---|
| 6.2 格式查看编辑 | **33 种格式**的能力矩阵（`core/document/formats.py`）：DOCX/DOC/PDF/RTF/ODT、TXT、MD、HTML、EPUB、XLSX/XLS/ODS/CSV/TSV、PPTX/PPT/ODP、WPS/ET/DPS、JSON/XML/YAML/INI、PNG/JPG/…、LOG/SQL/代码。查看 + 源码/段落/单元格编辑 + 只读预览 + 大纲/查找替换（正则）/文档体检/版本历史；**多标签**（同时开多份、保留未保存编辑）、**分屏与 Markdown 实时预览**、**书签**、**批注**（Word 原生批注 / Excel 批注表 / 其它格式附录 / PDF 真实标注）、**自动保存**、**打印**、**OCR**（图片与扫描件 PDF → 可编辑文本）、**导出图片**（任意文档 → PNG）；**PDF 工具箱**（拆分/合并/压缩/加密/解密/旋转/逐页水印/表单列出与填写/批注读写/转图片） |
| 6.3 格式美化 | 7 套模板（通用/公文/报告/论文/合同/简历/会议纪要）+ 样式统一 + 表格美化 + 多级自动编号 + 目录（Word TOC 域）+ 品牌色/页眉页脚/页码；**先预览每一处改动再应用** |
| 6.4 自动填充与计算 | 序列/日期/工作日/月份/星期/中文序号/文本序号/公式填充；**智能填充**按示例反推规则（拆姓名、提取号码/邮箱/金额、归类）；**Excel 子集公式引擎**（40+ 函数、跨表引用、数组公式、循环引用检测）；统计/分组/透视/图表数据/单位换算/AI 生成公式；**表格排序与筛选**（含还原）与导出 Excel 的冻结表头/条件格式/数据验证 |
| 6.5 AI 模型与工具调用 | 复用「大模型」多配置与降级；7 类场景（润色/摘要/翻译/扩写/缩写/纠错/改写）；**51 个工具**（读取/解析/写入/替换/排版/计算/表格/OCR/翻译/导出/合并/拆分/对比/摘要/脱敏/水印/批注/PDF 页面操作/模板库），AI 与界面共用同一套实现；调用日志（模型/角色/工具/耗时/成本/脱敏次数），工具调用同时写审计 |
| 6.6 两个文档合并（P0） | 七种模式（追加/章节/表格按行·列·主键/主从/对比）+ 同格式与跨格式 + 语义去重 + 术语统一 + 风格统一 + 过渡段 + 目录 + 摘要 + 冲突扫描 + 差异报告 + 版本快照；**无大模型也能合并**（本地规则），报告里标注哪些内容由 AI 生成或修改 |
| 6.7 AI 循环美化 | 分析 → 计划 → 工具调用 → 五维评分（格式/语言/可读性/事实/目标）→ 未达标继续；停止条件：质量阈值/最大轮次/无可改进项/用户停止/成本/时间；每轮 diff、工具日志、版本快照，可回滚到任意一轮 |
| 6.8 权限与安全 | 默认本地优先（不允许云端上传）；上传前脱敏手机号/身份证/银行卡/邮箱/金额/IP/车牌/姓名；导出水印与追踪标识；审计日志（打开/编辑/导出/美化/合并/回滚/脱敏/OCR/批注/工具调用） |
| 6.11 优先级（P1 项） | 循环美化、跨格式合并、OCR（图片与扫描 PDF）、差异报告、**PDF 工具箱**、**本地模板库**（保存/导入/导出/删除，JSON 可分享）均已实现；P2（多人协作/实时协同/插件生态/联网模板市场）未做，见 `docs/BOARDS/document.md` 第 8 节 |
| 6.9 非功能 | **性能**：表格上限截断 + 大文档预览保护（>800 块或 >40 万字只渲染前 800 块，保存/导出仍是全文），实测 1.5 MB Markdown 解析 0.1s、2 万行 CSV 0.03s、导出 Word 1.6s（有预算测试挡回归）；**可靠**：自动保存 + 版本快照按上限清理 + 回滚只改内存；**扩展**：**插件目录**（`register(api)` 注册自定义工具，与内置同权，默认关闭、失败不致命）+ 工具/格式/模板都是数据；**易用**：13 个窗口级快捷键 + `Alt+1~6` 切页签 + 「⌨ 快捷键」说明窗；**可访问**：关键控件设 `accessibleName`/`accessibleDescription` |

### 实现要点（踩过的坑）

- **统一中间结构（IR）**：新增一种格式只需扩 `parser.py`，美化/合并/计算/循环全部复用；
- **公式引擎的解析状态必须每层保存恢复** —— 单元格里也是公式时会递归求值，
  不保存就会把外层公式的 token 流冲掉（表现为莫名其妙的"缺少 )"，第一次实现就踩到了）；
- **自动编号要跳过文档标题**，否则"年度报告"会变成"1 年度报告"；标题被跳过后章节号从 1 起，
  下级标题得到 `1.1` 而不是 `0.1`；
- **合并过程中的冲突不能被覆盖**：合并模式记录的问题（找不到章节、表格模式无表可并）与
  两稿冲突必须**累加**进报告；
- **合并的「生成目录」不能只靠模板**：模板默认值（"通用"）本身不含目录，
  直接按模板美化会把 `add_toc` 吞掉，必须显式覆盖；
- **AI 文本工具必须真的写回文档**：逐段调用模型并替换正文（保留标题与表格结构），
  每段留一条 Change，每轮只处理前 N 段并提示"还有 N 段，再跑一轮即可覆盖"；
  工具内部漏了常量导入会被 `call_tool` 兜成"失败"，表现为"勾了 AI 却什么都没发生"——
  验收测试专门盯这一条；
- **权限实时读取**：`DocumentAi` 通过 `permissions_provider` 每次调用前读设置，
  设置页改完立即生效，不必重启或重建板块单例；
- **保存与导出的覆盖语义不同**：`DocumentLibrary.save` 默认覆盖原文件
  （否则用户点"保存"会得到一个 `(1)` 副本），防覆盖加序号只用于导出/转换输出；
- **pypdf 的三个坑**：密码错误时 `decrypt()` 返回 0 而不是抛异常（必须检查返回值）；
  页面要 `clone_from` 挂到新 writer 后再改（否则报 "pages not assigned to a writer" 弃用警告）；
  `compress_identical_objects` 的参数在 7.x 改名（新旧参数名都要兼容）；
- **水印覆盖层必须与页面同尺寸**：pypdf 的 `merge_page` 不会自动缩放，
  尺寸不一致会错位，所以用 Qt 按页尺寸生成覆盖层并缓存；
- 只读工作簿读取（openpyxl `read_only=True`）没有 `freeze_panes` 等属性，必须 `getattr` 兜底；
- **测试里的 `deleteLater()` 不会自动执行**：`processEvents()` 默认不派发 DeferredDelete，
  页面因此越积越多，`setStyleSheet` 每次都要重新 polish 所有存活控件 ——
  界面测试从 216 秒降到 18 秒就是显式派发 DeferredDelete 换来的；
- **打包脚本必须保留 UTF-8 BOM**：Windows PowerShell 5.1（文档推荐的 `powershell -File`）
  不认没有 BOM 的 UTF-8 脚本，中文按 ANSI 解码后引号被吃掉，直接报"Missing closing ')'" ——
  实测踩过一次（编辑器保存丢 BOM 即复现），现在 `test_spec_integrity.py` 专门锁住这两个 `.ps1` 与 `.nsi`；
- **插件加载的"注册钩子"不能提前撤**：先给 `api.register_tool` 打补丁、`exec` 完就恢复，
  会导致随后调用的 `register(api)` 拿到原始实现、注册结果全丢（表现为"插件加载成功但没注册任何工具"）；
  改成 `PluginApi.register_tool` 是真正的实例方法（内部调模块级 `register_tool`）后就没有这个时序坑；
- **大文档要有预览保护**：万级块整篇渲染成 HTML 会让界面卡住，因此预览只渲染前 800 块
  并明确提示"保存/导出仍是全文"，而不是让用户以为文档被截断了。

### 修复：设置项接线与导出/合并的数据安全（本轮）

自查发现一类"界面已经承诺、底层没接线"的问题，以及两个会造成重复/误覆盖的行为问题，已全部修掉：

| 问题 | 原因 | 修复 |
|---|---|---|
| 设置页「版本保留」无效 | `document/max_versions` 只被设置页自己读写，`prune_versions` 用写死的常量 40 | `DocumentStorage.version_keep_provider` 注入读取器（`boards/document/context.py`），裁剪时实时取设置值；非法值退回常量 |
| 设置页「AI 循环美化默认值」无效 | `loop_page` 用硬编码初值 3 / 0.9 / 0 | 面板构造时读 `document/max_rounds`、`quality_threshold`、`max_cost`（只覆盖用户保存过的键） |
| 设置页「默认美化参数」到不了美化面板 | `context.default_options()` 只喂给 `library`，面板初值一律取模板默认 | `BeautifyPanel._apply_settings_defaults()`：先按设置的默认模板套模板参数，再用设置里**显式保存过**的字体/字号/品牌色/开关覆盖（没保存过的项保持模板默认，避免把"公文体"的字体字号冲成通用值） |
| 标题重复 | `ir.title` 与首个 H1 同名（文件名当标题是常态）时，HTML/PDF/DOCX 既写标题行又写标题块 | `writer._title_already_in_blocks()`：同名时不再单独输出标题；不同名照旧写 |
| 合并结果会覆盖主文件 | 「应用到文档」后 `ir.path` 仍指向主文档，Ctrl+S / 自动保存直接替换原稿 | `MergePanel._apply()` 断掉路径（原路径记进 `metadata["merge_source_path"]`），保存时走「另存为」；无路径文档在板块里单独占一个标签，切走再切回不会取回合并前的旧内容 |
| 另存为后每次 Ctrl+S 都再弹路径 | `DocumentLibrary.save` 写完后不更新 `ir.path` | 保存成功即把 `ir.path` 指向落盘文件，新文件同时进文档库 |

回归测试：`test_write_does_not_repeat_document_title`、`test_write_keeps_title_when_it_differs_from_first_heading`、
`test_version_keep_follows_setting`、`test_save_remembers_new_path`（core）、
`test_loop_panel_takes_settings_defaults`、`test_beautify_panel_takes_settings_defaults`、
`test_merge_apply_never_overwrites_main_file`、`test_context_version_keep_follows_settings`（ui）。

仍未修（已在 `docs/BOARDS/document.md` 第 9 节如实标注）：Word 里的图表/文本框/SmartArt 会退化成其中的文字。

### 修复（第二批）：图片不再丢、人工确认接通、打印可选打印机、布尔设置读法

承上一批继续清"承诺了但没接上/会丢内容"的问题：

| 问题 | 原因 | 修复 |
|---|---|---|
| **带图 Word 保存后图片丢失**（静默数据丢失） | `_parse_docx` 段落没文字就 `continue`，`word/media/` 里的图片既不进 IR 也不回写 | 新增 `core/document/media.py`：图片按内容 sha1 落到 `数据目录/document/media/`，块上只留引用（**版本快照因此不膨胀**）；解析认 DrawingML 与老式 VML、含表格单元格里的图片；写出用 `add_picture` 重新嵌入并保留原尺寸；HTML/PDF 转 data URI 自包含，TXT/MD/XLSX 留 `[图片] …` 占位并在 `WriteResult.notes` 里说明；素材缺失时留占位文字 + 提示，不再静默丢 |
| 设置页「每轮结束人工确认」无效 | 引擎侧支持 `confirm` 回调，但界面从不传 | `LoopWorker.ask_confirm/answer_confirm`：工作线程发信号、主线程弹 `QMessageBox`、工作线程等答复（超时 1 小时；点「停止」/关闭板块都会解锁），面板 `options()` 反映设置值 |
| 非 HTML 文档「打印」盲打默认打印机 | PDF 直接 `os.startfile(path, "print")`，不弹对话框、不能选打印机 | 统一先弹 `QPrintDialog`；PDF 用 `viewer._PdfPages` 逐页按打印机分辨率渲染（PyMuPDF → **QtPdf** 兜底），页范围/份数/打印机都生效；两个 spec 不再裁掉 `PySide6.QtPdf` 与 `Qt6Pdf.dll`，并新增自检项 `pdf_print_engine`（自检 15 → **16 项**） |
| **布尔设置读法错误**：`bool("false") is True` | Qt 在 Windows 注册表里把 bool 存成 `"true"/"false"` 字符串，代码里到处 `bool(settings.value(...))` | 全部改 `settings.value(key, default, type=bool)`；`security._setting_bool` 兼容字符串 getter。**实际影响**：「自动保存」关不掉、「允许云端上传」取消勾选仍生效、「上传前脱敏」关不掉、人工确认弹框关不掉——权限与安全开关是最不该出错的一类设置 |

回归测试：`test_docx_images_are_parsed_into_blocks`、`test_docx_images_survive_save_and_export`、
`test_docx_image_missing_cache_keeps_placeholder`、`test_docx_image_in_other_targets`、
`test_beautify_keeps_images`、`test_loop_confirm_setting_is_wired`、`test_pdf_printing_renders_pages`、
`test_permissions_read_windows_bool_strings`、`test_bool_settings_read_from_windows_string_form`
（另外 `app/main.py` 的 `document_core` 自检也加上了"带图 Word 往返 + 字符串布尔"两项）。

### 完善（第三批）：Word 文字不再静默丢、标题不再凭空造、图片覆盖 Markdown/HTML、素材缓存可管理

| 问题 | 原因 | 修复 |
|---|---|---|
| Word 里**文本框 / 修订插入 / 超链接**的文字整段消失 | 一直用 `paragraph.text`，而它只拼接**直接子级** `w:r`——`w:ins`（修订插入）、`w:hyperlink`、`w:smartTag` 里的文字拿不到 | 新增 `_docx_paragraph_text()`：按文档顺序取全部 `w:t`、跳过 `w:del` 的删除内容与 `w:txbxContent`；文本框另按引用块保留（IR 装不下浮动位置）并记 warning；页眉页脚文字收进 `metadata`；文档含修订时明确提示"按接受全部修订读取" |
| 打开文件再保存会**凭空多一行标题** | 解析时 `title` 取自文件名，写出时又把 `title` 当可见标题行写入 → 保存一次多一行 | 解析时记 `metadata["title_source"]`（`filename` / `content`）；`writer._title_already_in_blocks()` 只在标题是"文档自己的"时才写标题行 |
| Markdown / HTML 里的图片没有进素材体系 | Markdown 解析不认 `![]()`（当普通文字），HTML 的 data URI/相对路径只留字符串、保存后变占位 | 新增 `_image_block()`：`data:image/...;base64` 与相对路径（按 `base_dir`）都收进素材缓存；远程 URL 原样保留——导出 HTML 写 `<img src>`、导出 Markdown 写 `![alt](url)`；`DocumentIR.text()` 里的图片统一成 `[图片] 名字`（AI 上下文与 TXT 一致） |
| 素材缓存看不见也清不掉 | 只增不减 | `media.cache_stats()` / `media.clear_cache()` + 设置页「素材缓存」区（大小 / 打开目录 / 清空，清空前弹确认） |

回归测试：`test_docx_textbox_and_revision_text_is_not_lost`、
`test_filename_title_is_not_invented_as_visible_title`、`test_markdown_images_become_blocks`、
`test_html_data_uri_image_is_kept`、`test_media_cache_stats_and_clear`。

### 测试

- `tests/board_document/test_document_core.py`：格式矩阵不变量、解析（md/txt/csv/json/xlsx/html/docx/html）、
  写出（8 种目标 + 防覆盖 + PDF）、**图片素材往返（Word/Markdown/HTML + 缓存清理）**、
  **文本框/修订/超链接文字不丢**、**文件名标题不写成正文**、
  美化（模板/编号/目录/表格/幂等）、体检与评分、存储与库流程；
- `tests/board_document/test_document_sheet.py`：公式引擎（含数组、跨表、循环引用、错误码）、
  自动填充（含工作日与公式平移）、智能填充（拆名/提取/归类/补零）、统计/透视/图表、单位换算、AI 公式解析；
- `tests/board_document/test_document_merge.py`：七种模式、表格三种合并、去重/术语/样式/冲突/摘要、
  AI 回调与失败回退、跨格式合并、库集成与报告落库；
- `tests/board_document/test_document_ai_loop.py`：工具注册表与真实改写、本地兜底、
  AI 权限/脱敏/长度限制/日志、工具调用智能体、循环美化的四种停止条件与回滚；
- `tests/board_document/test_document_pdf.py`：PDF 概况/页码解析/拆分三种模式/抽取/合并（含书签）/
  压缩/旋转/加密解密（含错误密码与未解密拦截）/逐页水印/表单列出与填写/工具端到端/PDF 工具箱对话框；
- `tests/board_document/test_document_ui.py`：板块与六个子页、打开/编辑/查找替换/版本回滚、
  分屏与实时预览、书签、自动保存、多标签（切换保留未保存编辑、关闭清空）、美化应用与导出、
  公式与填充、合并预览与应用、循环美化（离线）、脱敏与水印与审计、设置页；
- `tests/board_document/test_document_acceptance.py`：需求 6.10 的**八条验收标准**逐条端到端验收；
- `tests/board_document/test_document_comments.py`：批注（Word 原生 / Excel 批注表 / PDF 标注 / 附录汇总）；
- `tests/board_document/test_document_extras.py`：本地模板库、OCR、导出图片；
- `tests/board_document/test_document_plugins.py`：插件加载器（同权、默认关闭、坏插件不致命）；
- `tests/board_document/test_document_nonfunctional.py`：6.9 性能预算、预览保护、版本清理与回滚、
  快捷键与可访问名。

同步更新：`tests/architecture/test_architecture.py`（新增板块 key）、
`tests/platform/test_db_schema.py`（`doc_*` 表与索引前缀）、
`tests/smoke/test_cross_board_link.py`（设置页清单）、两个 PyInstaller spec、
`packaging/build_app.ps1`（模块核验 + 自检项）、`app/main.py`（新增 `document_core` 与
`pdf_print_engine` 自检，共 16 项）、
`docs/DATABASE.md`、`docs/ARCHITECTURE.md`、`docs/BOARDS/document.md`、`docs/TESTING.md`、`README.md`。

### 发布产物（v1.0.5，本机构建）

（大小与 SHA256 以最近一次构建为准，重打包命令：
`powershell -ExecutionPolicy Bypass -File packaging\build_app.ps1 -SkipDeps -Installer`。）

| 产物 | 大小 | SHA256 |
|---|---|---|
| `dist/ModuWorkbench/ModuWorkbench.exe`（onedir 启动器） | 13.19 MB | `98D190E734FF09E90E5FD921B5706B57FA17FC39CFAA20BAFE4D707E22A81AB3` |
| `dist/墨软工作台-Setup-1.0.5.exe`（NSIS 安装包） | 152.1 MB | `A12E1AE9DD62920FA8B840BE6049E6DC2D6D8EB582A305EA33F9BA41AF41422C` |

- onedir 目录合计约 **379.1 MB**（上一版 374.4 MB；+4.6 MB 来自 **QtPdf**（`Qt6Pdf.dll` + `QtPdf.pyd`，
  文档板块打印 PDF 逐页渲染要用，见第二批修复）+ 新增的 `core/document` 与 `ui_kit/settings/document`）；
- 构建三道自检全绿：源码自检 **16/16**、打包产物模块核验 **28/28**（含文档板块 9 个模块 + 插件加载器）、
  打包后运行自检 **16/16**（`document_core` 覆盖 PDF 拆分/合并/压缩、批注、PDF 标注、
  本地模板库、**插件加载**、排序筛选、Excel 冻结表头/条件格式/数据验证、工具注册表与循环美化、
  **带图 Word 往返**、**布尔设置读字符串**；`pdf_print_engine` 覆盖 QtPdf 渲染 PDF 页面）；
- `FileVersion` / `ProductVersion` = **1.0.5**（exe 与安装包都是；安装包另在
  `HKCU\...\Uninstall\墨软工作台` 写 `DisplayVersion`）；
- 三批修复都已随这次打包进入产物（设置项接线、标题去重、合并结果另存为、图片保留、人工确认、
  打印对话框、布尔设置读法、Word 文本框/修订文字、Markdown/HTML 图片、素材缓存管理），
  做法与验证见上面三节；全量测试 `pytest tests -q` 通过（1 项按环境跳过）；
- **安装包实测走了一遍完整闭环**（不是只看文件生成）：
  `Setup-1.0.5.exe /S /D=<临时目录>` 静默安装 → 装出 379.1 MB、`ModuWorkbench.exe` 与 `Uninstall.exe` 都在、
  注册表 `DisplayVersion=1.0.5`、桌面与开始菜单快捷方式都在 → 跑安装后的 exe 自检 **16/16**、
  GUI 进程能正常起来并稳定存活 → `Uninstall.exe /S` 静默卸载后目录、快捷方式、注册表键全部清干净；
- 本机没有 `makensis` 在 PATH 里（`winget` 在这台机器上不可用：只有 App Execution Alias 空壳），
  实际用的是 `packaging\build_installer.ps1` 既有的兜底路径 —— electron-builder 缓存里的
  `nsis-3.0.4.1\makensis.exe`（`v3.04`），无需额外安装；
- 顺手清理：删除未再引用的 `.webfront-build/`（旧内嵌前端构建缓存 72 MB）、
  PyInstaller 工作目录 `build/`、各 `__pycache__` 与 `.pytest_cache`（合计释放约 640 MB），
  并删掉已被 1.0.5 取代的旧安装包 `dist/墨软工作台-Setup-1.0.4.exe`（149.1 MB，加入文档板块**之前**那一版）；
  另修复 `.gitignore` 里一行历史遗留的乱码注释（该文件此前不是合法 UTF-8）。

## v1.0.4 — 墨软转换：把所有能转的格式都补上

反馈：「墨软转换，所有已知可以转换的文件格式都添加进来」。格式与动作都是**数据**
（`formats.py` 定义格式、`registry.py` 批量生成动作），所以这次是扩表 + 补齐实现。

支持格式从 **24 种扩到 68 种**，动作从 **约 200 条扩到 592 条**。新增能力一览：

| 族 | 新增 |
|---|---|
| 图片 | **图片 → PDF**（动图逐帧多页）；TIFF / ICO / TGA / PCX / PPM 互转；只读 PSD / DDS / JP2 也能转出 |
| 表格 | **CSV / TSV → XLSX**（以前只能单向读出）、**表格 → Markdown**、ODS 输入（走 LibreOffice）、TSV 输入输出 |
| 数据 | xml / ini / yaml ↔ json / csv / txt / md / html / pdf（**新增 PyYAML 依赖**） |
| 文档 | ODT 文本抽取（**纯标准库读 content.xml，不依赖 LibreOffice**）、RTF 文本抽取 |
| 字幕 | srt ↔ vtt、字幕 → 纯文本 |
| 电子书 | epub → txt / md / html / pdf；txt / md / html → epub |
| 音视频 | 视频补 MKV / WEBM / FLV / WMV / M4V / MPG / MPEG / TS / 3GP / OGV；音频补 M4B / AIFF / AMR / AC3；视频提取音频扩到 mp3 / wav / m4a / aac |
| 归档 | tar.gz / tar.bz2 / tar.xz / gz / bz2 / xz 压缩与解压（标准库，解压带路径穿越防护） |

### 关键实现细节（都踩过坑）

- **ICO 的尺寸会漂**：Pillow 默认会写出一整套 16/24/32… 方形尺寸，32×24 的图转出来变成 24×18。
  现在显式指定单一尺寸（方形、≤256）；
- **PDF 渲染没有 QApplication 会直接崩进程**（实测 0xC0000409，不是抛异常）：
  `pdf_out.render_text_pdf` 现在先检查并抛出可读的中文错误，测试也补上了 `qapp` 夹具；
- **单文件压缩保留源文件全名**：`报告.txt` → `报告.txt.gz`（bz2/xz 格式本身没有文件名字段，
  只有这样才能把原名带回去）；gz 另外把名字写进 RFC 1952 头的 FNAME
  —— 注意标准库 gzip 会**静默丢弃非 Latin-1 文件名**（中文必丢），因此按 UTF-8 字节写入、读取端反向解码；
- **动作 id 必须唯一**：tar / tar.gz 曾共用 `tar-extract`，并集去重后会导致其中一个文件被"跳过"；
  现在每个归档格式一个独立 id，并有测试锁住"id 唯一、目标有扩展名、每个格式至少一个动作"。

### 测试

- 新增 `tests/board_convert/test_convert_format_coverage.py`：注册表不变量（id 唯一、
  每个登记格式都有动作、目标都有扩展名映射、扩展名别名往返）；
- 新增 `tests/board_convert/test_convert_formats_ext.py`：图片新格式 + 图片转 PDF（含动图多页）、
  CSV↔XLSX、表格转 Markdown、ODT/RTF 文本抽取；
- 新增 `tests/board_convert/test_convert_archive_ext.py`（26 条）：gz/bz2/xz 往返、
  gz 中文名进头、解压不覆盖、tar 四种变体、`../` 路径穿越拦截；
- 数据/字幕/电子书三族另有专项测试（见 `tests/board_convert/`）。

### 发布产物（v1.0.4，本机构建）

| 产物 | 大小 | SHA256 |
|---|---|---|
| `dist/墨软工作台-Setup-1.0.4.exe`（NSIS 安装包） | 151.3 MB | `5C5E17053CA4F48E7C2608BAA9CD9E292CA864DCE5A0027ACD1C1E779F2387E8` |
| `dist/ModuWorkbench/ModuWorkbench.exe`（onedir 启动器） | 11.5 MB | `0FAE1D30B1425DCC941588690499369A0251AA3C08937D2EB92DEAB252A48809` |

- onedir 目录合计约 382.9 MB（比 v1.0.3 多约 0.4 MB，来自新增的 PyYAML）；
- 构建三道自检全部通过：源码自检 14/14、exe 关键字模块 15/15、打包产物自检全 `true`；
- 已确认 `yaml` 与 `core.convert.{data_io,subtitle_io,ebook_io}` 真的进了包（在 exe 字节流里检索到）；
- 两个产物文件属性里 `FileVersion` / `ProductVersion` = **1.0.4**。

### 缺依赖的动作改为「置灰 + 说明原因」

有些格式依赖外部能力，不是每台机器都有：**ODS / 旧版 DOC 需要 LibreOffice**、
**音视频需要 ffmpeg**（`amr` 还要求 ffmpeg 带 `libopencore_amrnb` 编码器）、
**RAR 解压需要 unrar/7z**、**yaml 需要 PyYAML**。

以前这些动作照样列出来，用户点了才拿到报错。现在新增 `core/convert/capabilities.py` 做能力探测：

- 缺依赖的动作**置灰**，悬停写明缺什么（例如「需要系统提供 unrar / 7z 才能解压 RAR」）；
- 面板提示里写明「其中 N 个因缺少依赖已置灰」；全部不可用时直接说明第一个原因；
- **默认选中的改成第一个可用动作** —— 否则用户一进来点「开始转换」就是注定失败的；
- 探测逻辑独立成模块，测试用替身覆盖（不依赖本机到底装了什么）：
  `tests/board_convert/test_convert_capabilities.py` 8 条，含一条界面契约测试
  （按钮禁用 + tooltip 带原因 + 默认选中可用动作）。

### 动作面板：分类芯片 + 搜索框（同批产物里）

格式铺开后动作数量从几十涨到几百，单个 mp4 就有 24 个、混选可达 50 个，只能滚动找。面板顶部因此加了两样东西：

- **分类芯片**：全部 / 文档 / 图片 / 音频 / 视频 / 数据 / 字幕 / 电子书 / 归档，芯片上带该族动作数量，
  当前格式用不到的族自动禁用；
- **搜索框**：按动作名收窄（例如「转 PDF」「压缩」「epub」），与分类筛选串联生效；
  筛选无结果时提示「换个分类或清空搜索」，而不是误报「没有可用动作」。

实现要点：芯片只建一次、点击时只更新计数与可用状态 —— 若在点击信号处理中销毁发送者按钮，
会踩 shiboken「Destroyed while thread is running」式的崩溃（图库侧树节点踩过同样的坑）；
搜索框同理（只重排动作按钮，不重建输入框），因此 `textChanged` 里直接重建是安全的。

## v1.0.3 — 使用反馈修复（影视下载/清晰度 · 转换输出 · 图库导航 · 更多片源）

针对第二轮五条使用反馈的修复。图 1 的报错（`Error initializing the muxer for …\ffmpeg.exe`）
正是下面第一条。

### 墨软影视：所有走 ffmpeg 的下载必然失败（图 1 的根因）

`_ffmpeg_download` 组出来的参数表本身就以 ffmpeg 路径开头，而 `_run_ffmpeg_download`
又拼了一次（`[ffmpeg, *args, …]`）—— 命令行里 ffmpeg 路径出现两次，ffmpeg 把多出来的那个
当成**输出文件**，于是报「Error initializing the muxer for …\_internal\tools\ffmpeg\ffmpeg.exe:
Invalid argument」，用户看到「下载完成：成功 0，失败 1」。

因为这是唯一的 ffmpeg 调用点，**采集站的 HLS 剧集 100% 下载失败**，
只有直链 mp4（走 requests 流式下载，不经过 ffmpeg）能成功 —— 这与反馈"archive 能下、360 不能下"完全吻合。

- 修法：参数表不再包含程序名，`-progress/-nostats` 挪到全局参数区（输出文件之前）；
- 回归测试 `test_ffmpeg_download_command_passes_binary_exactly_once`：断言 ffmpeg 路径只出现一次、
  输出文件是最后一个参数（已验证"把 bug 改回去测试立刻失败"）；
- 新增**真实端到端测试** `tests/board_video/test_video_download_e2e.py`：用 ffmpeg 造一段真 HLS、
  本地 HTTP 提供，跑生产下载路径并断言产出可识别的 MP4 —— 只替身 `Popen` 的单测看不出真 ffmpeg 的反应，
  这次就是漏在这里（ffmpeg 缺失时该测试自动跳过）。

### 墨软影视：只有 540P、很模糊、像"没有清晰度可选"

本机直连实测：cms_360 搜《流浪地球》返回的 m3u8 **主清单只有 1 个变体 1280×538 / 706 kbps**
—— 不是选择器坏了，是那个源本身只有这个画质。围绕"看不清又没办法"做了四件事：

- **默认锁最高清晰度**：解析播放地址时，若源自己声明了多个带地址的清晰度就直接取最高的；
  否则探测 m3u8 主清单并锁定**最高变体**（短超时，失败即原样返回，绝不让"锦上添花"拖慢或搞坏播放）。
  以前把主清单直接交给播放器/ffmpeg，"选哪一路"不可控，还会在播放中切换码率造成抖动；
- **把话说清楚**：清晰度下拉下方新增提示行，该源最高只有 X（Y kbps）时明确写出来；
  下拉按清晰度倒序，第 0 项就是最高；
- **新增「🔍 换源找高清」**：一键去其他数据源找同一部片、同一集，逐个探测主清单，
  挑分辨率最高的候选后就地切换（剧集/清晰度全部重建，之后播放与下载都走新源）。
  核心逻辑 `VideoLibrary.find_higher_quality` 可离线测试；`current_height` 保证"没有更清晰的就不乱切"；
- **下载同样先锁最高变体**（`_prefer_best_variant`），避免"明明有 1080P 却下出 540P"。

### 墨软影视：左侧片名列加宽

`configure_table` 以前把富余宽度全给最后一列，片名列用默认宽度（长片名被截）
现在第 0 列（片名）拿剩余宽度、其余列按内容自适应、最小列宽兜底，
搜索 / 我的视频 / 播放历史 / 源设置四处一起生效。

### 墨软转换：转换成功但输出目录没有数据

- **默认输出目录改走系统「文档」**（`QStandardPaths.DocumentsLocation`）：中文 Windows 上
  「文档」常被 OneDrive 重定向到 `%USERPROFILE%\OneDrive\文档`，而以前写死
  `Path.home()/"Documents"`，会新建一个用户根本不会去看的目录 —— 这是头号原因；
- **强制校验产物**：转换函数不抛异常但没产出文件 / 产出 0 字节文件时，以前一律显示"成功"。
  现在统一把关（文件存在且非空、解压目录非空），失败时直接说明原因；
- **输出位置可见可及**：任务条完成后写明「输出目录：<完整路径>」，工具栏新增「打开输出目录」，
  双击「输出」单元格即可在资源管理器里定位产物。

### 墨软转换：文件名与「详情/输出」挤在一起看不清

- 表格列宽重排：文件名列拿剩余宽度，格式/状态/查看按内容自适应，最小列宽兜底；
- 「详情/输出」改名「输出」并**只显示文件名**，完整路径进 tooltip（长路径不再撑爆窄列）；
- 行高统一 30px、超长文本中间省略。

## v1.0.3 — 更多可用片源（候选逐条实测后才进清单）

反馈：「可以去 GitHub 找一些开源的更好的视频源加进来」。

### 内置采集源：新增 4 个（全部本机实测）、下线 1 个

| 源 | 实测证据 |
|---|---|
| ✅ **爱坤资源** `ikunzyapi.com` | 200 JSON；《庆余年》36 集；主清单 declared **1920×1080**，子清单 `5000kb`（实测 ≈1742 kbps），分片 200 `video/mp2t` |
| ✅ **最大资源** `zuidazy.me` | 子清单 **`2000k_1080`**；第三方监控 100% uptime / 122,136 条 |
| ✅ **光速资源** `api.guangsuapi.com` | declared 1920×1080、2 线路、监控 speed=fast（实测 ≈893 kbps，标签偏乐观，已写进注释） |
| ✅ **魔都资源** `mdzyapi.com` | 主清单含 3840×1608 条目；监控 50% uptime、响应 15s，故排在最后 |
| ❌ **量子资源 `cms_lziapi` 已下线** | 搜索仍返回 JSON，但 **0/5 集可播**：`/share/` 页与直链 m3u8 在 lzcdn2/lzcdn28/lzcdn10/lz-cdn **全部 404**（直连与代理结果一致，已排除代理误判） |

- **顺序改按实测码率排**（爱坤 5000kb > 电影天堂 3000k > 非凡/最大/如意/U酷 2000k > 光速 ≈900k >
  360 706kb）：聚合搜索按「标题+年份」去重、保留先出现的源，所以码率最低的 360 从第 1 位降到第 8 位，
  不再抢占同名结果 —— 这是"只有 540P"的另一半解药；
- **去重纪律**（写进 `docs/BOARDS/video.md` §4.1）：红牛 / 豪华×2 / 虎牙 / 速播 / 金鹰 / 飘零
  与已内置源**分片逐字节相同**（同一上游换域名），一律不加；极速(451 kbps)、猫眼(410 kbps) 因画质低拒绝；
  爱奇艺资源因"首个分片返回 image/jpeg"拒绝；暴风/heimuer 播放全挂；Cloudflare 挑战页 / SSL 校验失败 /
  XML-RSS / 成人内容站一律排除（共 19 类候选，逐条留证）；
- **`SOURCES_VERSION` 2 → 3**：老用户磁盘配置里没有新 key，不递增版本号新源会被默认关掉
  ——用户看到的就是"源根本没加进去"；
- 新增离线测试 `tests/board_video/test_video_sources.py`：key 唯一且符合 `cms_[a-z0-9]+`、四个字段齐全、
  禁止重复接口地址、`CMS_SITES` 与 `DEFAULT_PROVIDER_ORDER` 一一对应且顺序一致、
  `CMS_SITE_BY_KEY` 覆盖，以及**把 `requests` 换成"一调用就炸"后 `build_default_providers()`
  仍能构造全部源**（锁死"构造期零网络请求"）。

### 公共版权源：archive 不再只给"解说类"

- 根因：`mediatype:movies AND (<关键词>)` **没有馆藏限制**，把 tvarchive / opensource_movies 的
  解说类短视频一起搜了进来；
- 改为优先 `collection:(feature_films OR silent_films OR classic_cartoons)`，
  无结果时退回宽检索（收紧但不会变废）；
- 实测：搜 "sherlock holmes" 现在返回的是《Sherlock Holmes and the Secret Weapon (1943)》
  《A Study In Scarlet (1933)》《House of Dracula (1945)》等**整部长片**。

## v1.0.3 — 图库分类导航树重做（空白大 / 丑 / 点击有蓝色标记）

反馈：「图库的树状图空白有点大有点丑，点击会有蓝色标记」。逐像素实测后确认这不是"调个颜色"的事：

### 根因

- **蓝色标记**：`show-decoration-selected` 为 1 时，Fusion 把**分支（缩进）列**按系统高亮色画成方块
  （抓到像素 `#308cc6`），而内容列由 QSS 画成圆角块 → 「左蓝右紫」中间一条接缝；
- 顺带踩实三条坑并写进注释：`QTreeView::branch:selected { background: transparent }` **不生效**；
  QSS **没有** `indentation` 属性（只能代码里设）；`QTreeWidgetItem` 默认 `sizeHint` 只有 13px
  （没有样式表时行会塌）；
- **空白大**：行高 36px + 每级缩进 20px，计数内联在标题里（`标签（6）`）导致右侧空一片、数字不对齐；
- 附带发现：每次点节点都重建整棵树 → 选中态立刻消失，用户看不出自己在看哪一类。

### 改法

- 新增 `boards/gallery/nav_tree.py`：`CategoryNavTree` 用 `drawRow()` 自绘**通栏圆角胶囊**
  （覆盖分支列，一整块无接缝），并在交给样式的 option 里清掉 `Selected/MouseOver/HasFocus` 避免二次上色；
  键盘焦点改画 1px `accent` 圆角描边（焦点仍可见、不再有虚线框）；
- 选中 = `accent_soft` 底 + `accent_strong` 字，悬停 = `surface_hover`，计数改为右侧胶囊徽章
  （`BADGE_ROLE`），**颜色全部取自 `ui_kit/tokens.py`，没有新增色值**；
- 行高 26（委托兜底，不依赖样式表）、缩进 `SPACE["lg"]=16`、分组标题上方多 `SPACE["sm"]`；
- `_refresh_side()` 重建后**恢复当前分类高亮**并保留折叠状态；空分组给「下一步」提示；
- 行为不变：点节点照样筛选、计数与库一致、展开/折叠可用。

### 同批收尾（反馈"剩下的两点"后补）

- **树面板底部空白**：树下方挂一行常驻提示 —— 空库给"导入后会自动按「相册 / 时间 / 标签 / 来源」分组"，
  有图给"共 N 张 · 点节点筛选，右键图片可加入相册/打标签"。底部不再是一片空荡的树；
- **选中与悬停分不清**：选中行加 **3px 左侧强调条**（`accent`），与"鼠标划过"的浅色悬停彻底分开。
  关键实现细节：它必须画在**委托之后** —— 委托的 `initStyleOption` 会把 `State_Selected` 找回来，
  QSS 的 `::item:selected` 底色会盖住竖条（实测只剩最左 1px）。这条既写进了注释，
  也被像素测试锁住（反证过：把竖条挪回委托之前，测试立刻失败）。

### 测试

`tests/architecture/test_ui_design_system.py` 新增 3 条，其中一条**抓像素**断言
（缩进列不得是 palette highlight、胶囊必须覆盖缩进列、焦点描边可见）。
已反证：把 `show-decoration-selected` 改回 1，测试立刻报
「缩进列出现了系统高亮蓝：#308cc6」。另有 `tests/board_gallery/test_gallery_nav_tree.py`（15 条）
覆盖徽章计数、分组标题不可点、空库四组的下一步提示、点击筛选、重建后高亮保持、折叠状态保留等。

## v1.0.3 — 文档与打包配置校正

发版后做了一次「文档/配置 vs 磁盘实况」的核查：只改描述与打包配置，不涉及运行时行为。

### README 与仓库实况对齐

- **头行版本与产物**：仍写 `v1.0.0（定版）` 与 `dist\墨软工作台-Setup-0.3.0.exe` → 更正为 v1.0.2 与真实产物路径；
- **合并历史段**：仍称「React 渲染进程作为内嵌前端、由 QtWebEngine 加载、经 QWebChannel 桥接」——
  这些在 v1.0.0 已全部移除，改为历史归档说明；
- **影视播放器**：「m3u8 或原生不支持的地址自动切到内嵌网页内核（hls.js）」不成立
  （`boards/video/player.py` 自 v1.0.0 起只有原生解码）→ 改为 QtMultimedia + 本机流服务代理；
- **影视格式转换**：列表里的 `gif` 不在 `core/video/models.VIDEO_TARGETS` 里 → 删掉
  （gif 只属于转换板块的目标集）；
- **转换简介**：写「六类」却只列了五项 → 补上「文本」；
- **数据库**：「大模型配置存 `llm.db`」「书库 `library.db`、曲库 `music.db`」→ 统一为单库 `modu.db`；
- **删除整节「重建内嵌前端」**（`build_webfront.ps1|sh`、`.webfront-build\`、`vite build` 均已不存在）；
- **打包自检**：从「共 15 项（含 WebEngine 导入与渲染 / webfront 产物）」更正为 **14 项**，
  与 `app/main.py` 的 `record(...)` 一一对应；
- **目录结构树**：按磁盘实况整体重生成（旧树还是 v1.0.0 之前的平铺布局）；
- **里程碑 M5**：标注为「前端与内联预览已移除（v1.0.0 起 Qt 单栈），NSIS 安装包保留」。

### 打包配置校正（macOS spec 已不可用）

`workbench_mac.spec` 仍在打包已删除的 `src/modu_workbench/webfront`（macOS 打包会直接报错），
隐藏导入里还留着已删的 `boards.convert.web`，`CFBundleShortVersionString` 停在 `0.3.0`：

- 去掉 webfront 数据条目与 `convert.web`；
- 隐藏导入与 Windows spec 对齐（补齐乐库音源层/播放器、视频源层子模块、大模型、
  各分板块设置页、`pypdf` 与 markdown/pygments 扩展），并排除 QtWebEngine 相关打包；
- `CFBundleShortVersionString` 同步为 `1.0.2`。

### 新增测试

`tests/architecture/test_spec_integrity.py`：静态解析两个 spec 的 `Analysis(...)`，
断言 `datas` 路径存在、`modu_workbench.*` 隐藏导入可解析、两个 spec 的自研模块清单一致
——macOS spec 的那处漂移正是被它抓出来的（写测试时它当场还抓出 Windows/mac 清单不一致）。

### 版本资源（exe 与安装包在属性里能看出版本了）

此前 `ModuWorkbench.exe` 与安装包都**没有版本资源**——资源管理器「属性 → 详细信息」里
产品名/版本号一片空白，只看文件名分不出装的是哪一版：

- 新增 `packaging/version_info.txt`（PyInstaller 的 `VSVersionInfo`），
  `workbench.spec` 的 `EXE(...)` 挂上 `version="packaging/version_info.txt"`，
  启动器 exe 现在带 `ProductName 墨软·工作台` / `FileVersion 1.0.2` / `OriginalFilename` 等；
- `packaging/installer.nsi` 增加 `VIProductVersion "${APP_VERSION}.0"`（NSIS 要求四段式）
  与 `VIAddVersionKey`（ProductName / CompanyName / FileDescription / FileVersion /
  ProductVersion / LegalCopyright），安装包同样可见；
- **版本仍是单一来源**：安装包来自 `build_installer.ps1` 的 `/DAPP_VERSION`，
  exe 版本资源与 `__version__` 的一致性由 `tests/architecture/test_version.py` 新增的两项测试守住
  （`filevers`/`prodvers`/`FileVersion`/`ProductVersion` 全部比对，且禁止 NSIS 里写死版本号）。

### 仓库清理与重新打包

v1.0.2 首次发版后做了一次彻底清理（全部是 `.gitignore` 覆盖、可再生的构建产物，未触碰任何源文件）：

| 清理项 | 体积 | 说明 |
|---|---|---|
| `node_modules/` | 953.7 MB | 旧 Electron 工程的依赖，仓库根已无 `package.json`（仅 `archive/electron-formatflow/` 有），与当前 Python 单栈无关 |
| `release/` | 329.1 MB | electron-builder 遗留（`win-unpacked/`、`latest.yml`、`万能格式转换器 Setup 0.1.9.exe`） |
| `dist/` 旧产物 | 834.9 MB | `墨软工作台-Setup-1.0.0.exe` / `-1.0.1.exe`（301 MB）与 `dist\main`、`dist\renderer`、`dist\shared` 旧 Electron 目录 |
| `build/` | 37.6 MB | PyInstaller 中转缓存（打包脚本本就带 `--clean`） |
| `.pytest_cache/`、各 `__pycache__/` | — | 可再生缓存 |

合计释放 **2.18 GB**；随后全量重新打包（`packaging\build_app.ps1 -Installer -SkipDeps`），
三道自检再次全部通过（源码 14/14、exe 关键字模块 15/15、打包产物自检全 `true`）。
清理后 `dist\` 只剩本次产物，合计 533.5 MB。下表已更新为**重新打包后**的最终校验值。

### 发布产物（v1.0.3，本机构建）

| 产物 | 大小 | SHA256 |
|---|---|---|
| `dist/墨软工作台-Setup-1.0.3.exe`（NSIS 安装包） | 151.0 MB | `E98DC5A2317B29BCAF2DFAF34D91C752D6A242EA1A4BE41D0A6C01996F3E7D74` |
| `dist/ModuWorkbench/ModuWorkbench.exe`（onedir 启动器） | 11.3 MB | `793A9DC8307FD90096CA959B0A3290022F6CAF51BC40A1EA9182B24E669A2F42` |

- onedir 目录合计约 382.5 MB（随包 ffmpeg/ffprobe 约 196 MB）；
- 构建三道自检全部通过：源码自检 **14/14**、exe 关键字模块 **15/15**、打包产物自检全 `true`；
- 两个产物的文件属性里可见 `FileVersion` / `ProductVersion` = **1.0.3**、`ProductName` = 墨软·工作台；
- 打包前已清掉 `dist/` 里的旧版本安装包，目录里只保留当前版本产物；
- 图库收尾（底部提示 + 选中强调条）在这版产物里，属于本次**重新打包**后的最终校验值。

## v1.0.2 — 控件样式补齐（尤其是下拉框）

反馈："前端页面的样式有点丑，尤其下拉框的样式是真丑" + "点击下拉框会卡死一会"。

### 第一轮：补齐控件规则（已修正，见下方"第二轮"）

**根因**：界面是 Qt Widgets，默认外观由 **QStyle（Fusion）** 绘制，QSS 只改写"写到的控件 + 子控件"。
旧 QSS 只覆盖了按钮/输入框/表格/滚动条，**弹出类控件一条规则都没写**，于是它们继续按 Fusion 画。

改法：箭头/勾选/圆点用 QPainter **现画成 PNG**（随主题色，缓存到 `<数据目录>/cache/ui`），
QSS 以 `image: url(...)` 引用；并补齐 数字框 / 勾选与单选 / 右键菜单 / 树 / 选项卡 的规则。

### 第二轮：修正下拉框（选择器写错 + 去掉了会卡的弹出层 hack）

用**真实中文字体 + 像素取色**逐个选择器做了对照实验（这是关键，之前只看截图看不出问题）：

| 写法 | 实测结果 |
|---|---|
| `QComboBox QAbstractItemView::item {…}`（旧写法） | ❌ **不匹配** → 列表项一直是 Fusion 默认（第 1 轮"补齐"其实没生效，白改） |
| `QComboBox QAbstractItemView { selection-background-color }` | ❌ 不生效 |
| `QComboBox::item { padding / min-height }` | ⚠️ **几何爆炸**：行高算成 **1900px**、弹出层 130px→**792px** |
| `QComboBox::item:selected { background }` | ✅ 生效 |
| `QComboBox QAbstractItemView { border/padding/outline }` | ✅ 生效（view 级没问题） |

- **下拉列表改成正确写法**：view 级只留背景/`border: none`/`padding: 4px`/`outline: 0`，
  列表项用 `QComboBox::item` / `:hover` / `:selected`，**绝不给 `::item` 加 padding**；
- **删掉"弹出时改 window flags + 半透明"的 hack**（上一轮加的）：在已创建的弹出窗口上改标志
  会**重建原生窗口**——这正是"点一下卡一下"的原因；而且那个全局事件过滤器会让
  **每个控件、每个事件**都回调进 Python，等于给整个界面加税。现在弹出层保留系统原生边框，
  与 `QMenu` 一致：一层边、不闪烁、不卡顿。
- 顺带修掉设置页横向滚动（`music` 1082→474、`video` 1012→418、`llm` 788→762）：
  长文件路径改用只读 QLineEdit、音源复选项标签缩短（细节进 tooltip）。

**测试**：`test_combo_popup_uses_working_selectors` 把上面四条实测结论钉死
（禁止旧选择器、禁止给 `::item` 加 padding、禁止再装弹出层处理器/改 window flags）；
另有 `test_qss_covers_popup_controls`、`test_runtime_icons_are_generated`、
`test_settings_pages_fit_default_width`。`docs/UI_GUIDE.md` 增加"下拉列表的选择器坑"专节。

## v1.0.2 — 在线曲目质量过滤（酷我试听/片段）

针对「酷我源头有很多试听、下载还提示只能在手机端播放」的反馈。

### 识别：哪些结果不算"能完整听的歌"

新增 `core/music/sources/quality.py`，纯本地规则（实测酷我接口的 PAY / isdownload /
svip_preview / payInfo 字段在片段与完整曲目上**完全一致**，只有时长能区分）：

- 时长短于阈值（默认 **45 秒**，可配置 15~600 秒）：实测酷我 20 条结果里平均 6~7 条是
  10~42 秒的同名片段/串烧/铃声（"邓紫棋"一次搜索就有 7/20）；
- 标题含"片段 / 试听 / 铃声 / 彩铃 / 抢先听 / 预告"等字样（"Demo / 伴奏 / 现场"**不算**，那是正常版本）；
- 音源自己标注的试听标记，以及"试听型音源"（iTunes 30 秒片段）。
  试听型音源的结果**标注但默认不隐藏**——它的音源名里就写着"试听"，用户心里有数。

### 排序与过滤

- **完整曲目统一排在前面**（`sort_full_first`，组内保持音源相关度顺序），跨音源搜索同样生效；
- 搜索页新增 **「隐藏试听/片段（推荐）」**，默认勾选：酷我这类"混在完整音源里的片段"
  直接不显示，状态栏写明「已隐藏 N 条试听/片段（曲名…）」；
  取消勾选可以看到全部结果，试听条目**标黄**并在时长列注明「00:22 · 试听」、悬停写明原因；
- 设置 → 乐库新增 **「隐藏试听/片段」** 与 **「完整曲目最短时长」**（秒），改完立即生效。

### 下载：不再白下 10 秒，也不再说不出原因

- 下载/试听时若判定为片段，**连下都不下**，直接把原因交给"自动换源"去别的音源找完整版
  （关掉自动换源则明确报「时长仅 22 秒（试听/片段条目）」）；
- 换源匹配时**优先挑完整版本**，不再选中同名片段；
- 酷我解析不出直链时，把接口返回的**原文**带给用户（例如「酷我限制：当前歌曲只能在酷我手机端播放」），
  并额外尝试 128k / convert_url2 等参数包装与 JSON 包装的直链；
- **修掉一个真 bug**：`SourceError` 继承自 `RuntimeError`，而 `download_track` 里
  `except RuntimeError`（本意是"用户取消"）写在了 `except SourceError` **前面** ——
  所有音源错误都被它吞掉并直接抛出，**下载阶段的"失败自动换源"从来没有真正生效过**。
  这也正是"下载受限曲目只报错、不会去别的音源找完整版"的原因。
- 顺带：关键词不是 http(s) 地址时不再把「音频直链」音源算进跨源搜索，
  免得每次搜索都多一条"请输入以 http(s) 开头的音频直链"的假错误。

新增测试 `tests/board_music/test_music_quality.py`（18 项）：判定规则、阈值可配置、
稳定排序、酷我字段解析与受限文案透传、跨音源排序、片段拒绝下载不发网络请求、
换源拿完整版、搜索页隐藏/显示与偏好持久化。

### 发布产物（v1.0.2，本机构建）

| 产物 | 大小 | SHA256 |
|---|---|---|
| `dist/墨软工作台-Setup-1.0.2.exe`（NSIS 安装包） | 151.0 MB | `B64FFF6247C8A6008FC227A99C1436A200AE35D75BE08A8C8BC4A8636B811BFD` |
| `dist/ModuWorkbench/ModuWorkbench.exe`（onedir 启动器） | 11.3 MB | `8C547096782353E19010C58AB2546E7EA7BF3B661BA80192FEAB0A7C7001784A` |

- onedir 目录合计约 382.5 MB（随包 ffmpeg/ffprobe 约 196 MB）；
- 以上为**清理旧产物并加上版本资源后重新打包**的最终校验值（首次发版产物已随 `dist\` 清理一并删除）；
- 两个产物的文件属性里均可见 `FileVersion` / `ProductVersion` = **1.0.2**、`ProductName` = 墨软·工作台（见上一节）；
- 构建三道自检**全部通过**：源码自检 **14/14**、exe 关键字模块 **15/15**、
  打包产物自检全部为 `true`（含 `video_core` / `gallery_core` / `llm_core`）——
  本次产物未被「智能应用控制」拦截，与 v1.0.1 时不同；
- 版本号 1.0.1 → **1.0.2**（`__init__.py` / `pyproject.toml` 单一来源，安装包版本由
  `packaging/build_installer.ps1` 从包内读取后传给 makensis）；
- 未签名 exe/安装包在开启「智能应用控制」的机器上仍可能被系统拦截：需关闭该功能或签名后验证安装包。

## v1.0.1 — 使用反馈修复

针对「影视搜索没反馈 / 音乐音源失效 / 首页空白多 / 转换点不动」四类反馈的修复。

### 墨软影视：搜索无任何反馈（已修复）

- **根因**：搜索页的状态助手依赖 `_fallback_status`，而该属性从未创建 —— 点「搜索」立刻抛
  `AttributeError`，界面因此完全没有反应（`compileall` 检查不到，原有测试也只直接调 `_on_results()`）。
- **修复**：补齐属性；状态助手统一走 `_note_fallback()`（缺失时静默），
  并把「调用助手必须定义助手」写进 `tests/architecture/test_ui_page_conventions.py`。
- **新增回归测试** `tests/smoke/test_board_entry_points.py`：直接点「搜索」（替身线程）验证
  状态文字与结果进表，覆盖"点按钮"这条真实路径。

### 墨软乐库：音源失效与"只有 11 秒试听"

- **网易云音乐**：搜索端点 `/api/search/get/web` 已改为返回**加密字符串**，
  旧代码按其是对象解析 → `AttributeError`（即"没有数据源了"）。改用仍返回明文 JSON 的
  `/api/search/get`（歌曲/歌手/歌单三种检索均实测可用）；专辑检索拿不到曲目时自动退回歌曲检索。
- **ccMixter**：`tags=` 参数会让接口返回 SQL 报错的 HTML 页（JSON 解析失败）。
  改用 `search=`，并去掉会导致空结果的 `lic=open`。
- **试听片段校验**：部分曲目（酷我 VIP、iTunes 等）只返回 11~30 秒试听，
  以前会当成"下载成功"。现在下载后比对实际时长与标称时长，明显偏短则删除文件并抛出
  「该音源只提供 N 秒试听片段（完整曲目约 M 秒），可能是 VIP/付费曲目，已自动尝试其他音源」，
  由注册表自动换源。
- 说明：iTunes 源本身是 30 秒试听（`KIND_PREVIEW`），界面上标注为试听源；Jamendo 需自填 client_id。

### 首页：全屏时下方大片空白（已修复）

- 板块卡片改为竖向可扩展，板块区吃掉窗口多余高度；提示卡保持在底部 —— 全屏（1920×1080）
  下不再是"顶部两行卡片 + 半屏空白"（截图：`docs/ui/board-home-fullscreen.png`）。

### 墨软转换：PDF 没有动作、md 点了没反馈（已修复）

- **JSON 此前没有任何可转换动作** → 新增 JSON 转 TXT（美化）/ CSV（对象数组）/ PDF。
- **PDF 此前只能作为输出**，加进来没有动作 → 注册 PDF 为输入格式，新增
  PDF 提取文本（TXT / Markdown），文本抽取用 `pypdf`（实测从 189KB 的真实 PDF 抽出 4065 字）。
- **勾选多种格式时"共同动作"为空** → 动作面板空、按钮不可点，看起来"点了没反应"。
  现在退化为**各格式动作的并集**，运行时只转换动作适用的文件，不适用的行写明
  「跳过：该动作不适用于此格式」。
- **转换过程无反馈** → 板块接入统一任务条：显示「正在转换（i/n）：文件名」与
  「转换完成：成功 N，失败 M，跳过 K」，不再只有表格里的小字状态。
- **逐个格式动作核对**（新增测试 `test_convert_actions_for_each_format`）：
  txt / md / json / html / csv / png / mp4 / zip / pdf 均有可用动作。

### 墨软转换：Markdown 预览排版升级（对标 MarkText）✅

`QTextBrowser` 只支持 HTML4 + CSS 2.1 子集（`line-height`/`border-radius`/`padding` 基本被忽略），
所以改成「HTML 属性化 + 文档后处理」两条腿：

- **表格**：表头加粗 + 浅底、隔行浅底、细边框、单元格内边距 7px、列宽铺满（`QTextTableFormat`）；
- **标题**：h1~h6 明确字号/字重/颜色，h1/h2 下方加发丝分隔线；
- **行内代码**：等宽字体 + 淡底 + 品红字；**代码块**：包进带底色的单元格（Qt 对 `pre` 背景支持不稳），
  保留 Pygments 语法高亮；
- **引用块**：左侧 4px 色条 + 浅底（Qt 不认 `border-left`，用双格表格实现）；
- **分隔线**：`<hr>` 换成 1px 高的表格行（颜色可控）；
- **列表**：嵌套列表前补 `<br>`（否则 Qt 会把子列表挤在同一行）；
- **正文排版**：默认字体（微软雅黑 UI）、行距 165%（CSS 表达不了，用 `QTextBlockFormat` 设置）、
  段间距统一；
- 对照截图：`docs/ui/md-preview-before.png`（旧渲染：表格无边框/无底色、代码块无背景、引用只是缩进）
  与 `docs/ui/md-preview-after.png`（新渲染）。
- 新增测试 `tests/board_convert/test_doc_preview.py`：表头加粗与底色、隔行底色、等宽字体、
  代码块底色、引用色条、`<hr>` 已替换、嵌套列表换行、文本不丢失、行距 165% 与表格内边距 7px。

### 发布产物（v1.0.1，本机构建）

| 产物 | 大小 | SHA256 |
|---|---|---|
| `dist/墨软工作台-Setup-1.0.1.exe`（NSIS 安装包） | 151.0 MB | `3AA1446A51BDF270F2B0CAFFFB1F69B2FBA98129A7057397778DDA5E8F924687` |
| `dist/ModuWorkbench/ModuWorkbench.exe`（onedir 启动器） | 11.3 MB | `C17B5F4ABC9EDE857B9C20ABCDDFF7455637900D38D0D21C75F9433158D2CA95` |

- onedir 目录合计约 383 MB（随包 ffmpeg/ffprobe 约 196 MB）；
- 构建三道自检：源码自检 **14/14**、exe 关键字模块 **15/15**、
  打包产物自检（本机构建时被 Smart App Control 拦截，未能执行；上一版产物实跑为 14/14，
  其中唯一失败项是本次已修正的过时断言）。
- 未签名 exe/安装包在开启「智能应用控制」的机器上会被系统拦截：需关闭该功能或签名后验证安装包。

### 版本

- 版本号 1.0.0 → **1.0.1**（`__init__.py` / `pyproject.toml` 单一来源）；
  新增依赖 `pypdf>=5.0`（PDF 文本抽取），打包 spec 已加 hiddenimport。

## v1.0.0 — 架构重构与定版（墨软·工作台）

本版是**重构定版**：不改产品功能定位，把工程结构、数据层、界面规范与前端技术栈统一收口，
为后续加板块/加源提供稳定底座。六个阶段（P1~P6）全部落地，每个阶段都跑通全量测试并单独提交。

### 定版要点（一句话版）

- **目录即边界**：五大板块各自成包（`boards/<板块>/`），依赖规则由测试强制，板块之间互不影响；
- **公共层收口**：`core/platform`（路径/HTTP/媒体工具/文件扫描/单库），消除板块互相借代码；
- **数据层统一**：单库 `modu.db` + 版本化迁移 + 旧分库自动导入（旧文件保留备份）；
- **界面统一**：设计令牌 + 组件库（页面骨架/卡片/空状态/任务条），圆角≥10px、留白有刻度、空列表有引导；
- **前端统一**：Qt 单栈，移除内嵌 React 与 QtWebEngine（包体积 731MB → 389MB）；
- **测试与文档**：测试按板块/层级分层 + 架构闸门；文档补齐 ARCHITECTURE/DATABASE/UI_GUIDE/TESTING/RELEASE/BOARDS。

### 升级须知（从 v0.3.x）

1. 首次启动自动把 `library.db` / `music.db` / `video.db` / `gallery.db` / `llm.db`
   合并进 `modu.db`，旧文件改名为 `*.imported.bak`（不删除，可人工回退）；
2. 设置项从各库的 `settings` 表迁到 `app_settings`（键带板块命名空间，如 `video/sources/enabled`）；
3. 影视内置源清单随版本更新（失效源下线、新增可用源），自填的接口地址保留；
4. 界面整体重排：功能位置与流程不变，布局/配色/空状态按新设计规范统一。

> 目标、目录设计与阶段验收见 `docs/REFACTOR_PLAN.md`；表结构见 `docs/DATABASE.md`；
> 界面规范见 `docs/UI_GUIDE.md`；测试与发版见 `docs/TESTING.md` / `docs/RELEASE.md`。

### P1 目录与包边界重构

- **五大板块各自成包**：`boards/home|book|convert|music|video|gallery/`，
  板块入口固定 `board.py`，同板块子页按功能命名（`search.py` / `detail.py` / `player.py` /
  `widgets.py` / `library_page.py` / `history.py` / `sources.py`），前缀式平铺文件全部取消。
- **应用骨架独立**：`main.py`→`app/main.py`、`app_shell.py`→`app/shell.py`、
  `boards/registry.py`→`app/registry.py`。
- **引擎包名对齐板块**：`core/reader`→`core/book`、`core/image`→`core/gallery`。
- **新增 `tests/test_architecture.py`**：用依赖规则（AST 解析 import）锁死
  「板块之间互不影响」，并显式登记待解耦清单（P2 清空）。
- 全部用 `git mv` 迁移以保留历史；全量 pytest 通过。

### P2 依赖解耦（core/platform 公共层）

- 新增 `core/platform/` 作为**唯一允许被所有板块依赖**的公共层：
  - `paths.py`：数据目录与文件布局（原 `services/config.py`，书库专用的扫描函数移入 `core/book/files.py`）；
  - `http.py`：统一 HTTP 客户端（UA/超时/退避重试/错误翻译）——音源与视频源此前各写一份，
    现在合并为唯一实现，板块只用 `hint` 定制错误提示；
  - `media.py`：ffmpeg/ffprobe 定位（原 `core/convert/media_io.py`）与媒体时长探测
    （原 `core/music/library.py`）；`media_io` 保留同名再导出以兼容旧引用；
  - `files.py`：本地文件扫描（原 `services/file_scan.py`）。
- **应用级上下文拆分**：单例回到各自板块（`boards/<板块>/context.py`），
  大模型单例放 `core.llm.context`；`app/context.py` 做按名字懒加载的聚合，
  `services/app_context.py` 退化为懒转发兼容层。板块内部改用 `from . import context as app_context`。
- 依赖方向修正：影视不再依赖乐库（时长探测统一到 `core/platform/media`）、
  图库与大模型不再依赖影视的 HTTP 模块。
- `tests/test_architecture.py`：待解耦清单只剩设置页一项（P4 处理），
  并明确 `core.convert` 为共享转换引擎（可被调用，但不得反向依赖板块）。
- 全量 pytest + 应用依赖自检（15 项）通过。

### P3 单库化与结构迁移

- **单一数据库 `modu.db`**：原先 5 个独立库（library/music/video/gallery/llm）合并为一个库，
  业务表统一加板块前缀（`book_` / `music_` / `video_` / `gallery_` / `llm_`），
  索引名同样带前缀（SQLite 索引名全局唯一，重名会静默失败）。
- **统一设置表** `app_settings(key, value, updated_at)`：板块通过 `settings_namespace` 自动加前缀
  （如 `music/sources/enabled`），各板块不再各建一张 settings 表。
- **版本化迁移**：新增 `core/platform/db.py`（连接/WAL/外键/事务/设置）与
  `core/platform/migrations/`（`schema_migrations` 记录版本，开库自动升级，幂等）。
- **旧数据自动导入**：`core/platform/legacy.py` 首次启动把 5 个旧分库按列名对齐导入，
  旧库缺列自动补默认值，板块设置并入 `app_settings`，旧文件改名 `*.imported.bak`（不删除）。
- 各板块存储类改为继承 `SqliteStore`（自身 DDL / 连接 / 设置实现全部删除）；
  启动入口 `app/bootstrap.py` 统一做「建库 + 迁移 + 导入」。
- 文档：新增 `docs/DATABASE.md`（表结构、字段、迁移与备份约定）。
- 测试：新增 `tests/test_db_schema.py`（结构快照 / 前缀规则 / 索引唯一 / 外键指向 / 迁移幂等 /
  设置命名空间）与 `tests/test_db_migration.py`（用真实旧结构样本演练迁移）；全量 pytest 通过。

### P4 界面设计系统（进行中）

#### P4a 令牌 + 组件库 + 首页

- **设计令牌独立成文件** `ui_kit/tokens.py`：间距刻度 4/8/12/16/24/32/48、
  字号 11–28、圆角**最小 10px（全局无直角）**、控件高度/行高统一；
  颜色只表达语义，浅色 `LIGHT` 与深色 `DARK` 共用同一套字段。
- **组件库** `ui_kit/components/`：`ColumnPage`（页边距 24、区块间距 16）、`PageHeader`、
  `SectionCard`、`EmptyState`、`Toolbar`、`TaskBar`、`chip`/`divider`/三种按钮层级 ——
  页面不再各写各的留白。
- **首页重排**：板块卡改响应式栅格（2~4 列按窗口重排），卡片等高、末行跨列铺满。
- **验收工具** `packaging/ui_snapshot.py`：离屏渲染 `docs/ui/board-*.png`；
  规范文档 `docs/UI_GUIDE.md`；测试 `tests/test_ui_design_system.py`。

#### P4b 墨软影视板块重排

- 搜索页：页头 + 「搜索」「搜索结果」两张区块卡；结果为空显示 `EmptyState`
  （不再留一张空表格与半屏空白）；批量操作行按需出现；**去掉页面内重复的进度条与状态栏**。
- 我的视频页：页头（含"打开影视库目录"）+ 列表卡（筛选行 + 表格/空状态 + 条目操作 + 转换行），
  计数改走卡片徽章。
- 播放历史页：页头（含"刷新"）+ 历史卡（筛选 + 表格/空状态 + 操作行）。
- **统一任务条** `TaskBar`：全板块只有一条状态栏，进度与取消按钮仅在任务进行中显示
  （此前"两条状态条 + 常驻 0% 进度条 + 常驻取消按钮"）。
- 保留 `_status` / `_progress` / `_count` 等既有内部名（兼容属性），全套 UI 测试无需改写即通过。
- 截图：`docs/ui/board-video.png` 由"空表格 + 半屏空白 + 双状态栏"变为
  "区块卡片 + 空状态引导 + 单任务条"。

#### P4c 墨软乐库板块（统一任务条 + 空状态）

- 板块新增**共享任务条**（位于播放条上方）：五个子页的搜索/导入/下载/转换状态与进度统一汇总，
  空闲时自动收起进度与取消按钮；任务条的「取消」直接取消当前子页正在跑的任务。
- 各子页状态/进度接入统一入口（`_report` 纯信息不显示进度条、`_busy` 进行中、`_set_progress` 只在任务中更新），
  删掉页内重复的「取消下载」按钮。
- 搜索页：结果为空显示 `EmptyState`（🎵 + 下一步指引），批量操作行只在有结果时出现，
  不再留一张空表格与一排禁用按钮。
- 主题统一：老页面头使用的 `#pageSub` 也纳入新规范（字号/颜色与 `#pageSubtitle` 一致）。
- 新增 `tests/test_ui_page_conventions.py`（静态约定测试）：
  ① 调用 `_report/_busy/_idle/_set_progress` 的类必须定义它们；
  ② 每个 `@property` 后必须紧跟方法定义（防止脚本插入位置错误）；
  ③ 定义只读 `_status` 属性的类不得再给它赋值；④ 接入任务条的页面必须声明 `task_bar` 参数。
  这三条正是本轮实际踩到的坑，现在由测试兜住。

#### P4d 墨软图库板块（统一任务条 + 空图库引导）

- 图库板块页内的进度条与状态标签替换为**共享任务条**；页内「取消任务」按钮隐藏（取消统一由任务条提供），
  任务条的取消直接取消导入/收集/AI 任务。
- 缩略图网格外套一层容器：**图库为空时显示引导**（🖼 + 下一步操作），不再是一块空白大灰框。
- `_set_progress` 语义统一为「只更新进度数值，是否显示由 report/busy/idle 决定」，
  避免空闲时冒出 0% 进度条，同时保持既有测试对进度值的断言成立。
- 截图工具新增 `--sub`（渲染板块子页，如 `--only music --sub search,library,playlist,history`）。

#### P4e 墨软书库板块（统一任务条 + 空书架引导）

- 板块新增**共享任务条**：在线书库的下载进度与状态统一汇总；页内自建的进度条与状态标签移除
  （保留 `_status` / `_progress` 兼容引用）。
- 书架空态改用组件库 `EmptyState`（📚 +「导入文件 / 导入文件夹」指引；搜索无结果时提示换关键词）。
- 书库板块补 `show_page(shelf|online)`，与其他板块一致的子页切换入口（截图/自动化复用）。

> 注：转换板块（P4f）与「前端统一为 Qt」（P5）改造同一批文件，**合并执行**：
> 直接以原生 Qt 重写转换板块与文档预览，避免先改 Web 版再重写一遍。

### P5 前端统一为 Qt 单栈（含 P4f 转换板块）

- **转换板块**：永远使用原生 Qt 实现（文件表格 + 动作面板 + 批量转换 + 加入书架），
  删除「可用则加载 React 内嵌前端」的分支——该分支此前只在非无头环境生效，等于线上走的是未测试路径。
- **文档预览**：统一 `QTextBrowser`（Markdown/JSON/HTML/TXT 由 `QTextDocument` 渲染），
  不再优先 QtWebEngine。
- **影视播放**：移除 hls.js 网页兜底（QtWebEngine 的 Chromium 不含 H.264/AAC，兜底本就无效），
  只保留 QtMultimedia 原生内核；`player_available()` 不再探测 QtWebEngine。
- **删除**：`boards/convert/web.py`、`services/web_bridge.py`、`services/web_prepare.py`、
  `services/webfront.py`、`services/bridge_shim.js`、`src/modu_workbench/webfront/`、
  `packaging/build_webfront.ps1|sh`。
- **打包**：spec 移除 WebEngine/webfront 条目并把 `PySide6.QtWebEngine*` 加入 EXCLUDES；
  实测包体积 **731MB → 389MB**（其中随包 ffmpeg 196MB），QtWebEngine 已不在包内。
- **自检**：`webengine_import`/`webengine_render`/`webfront_present` 三项替换为
  `single_qt_stack`（AST 扫描确认无 QtWebEngine import 且无内嵌前端产物）。
- **测试**：`test_web_bridge.py` → `tests/test_convert_files.py`（保留文件扫描用例），
  版本单一来源测试独立为 `tests/test_version.py` 并改为校验"安装包版本由打包脚本注入"。

### P6 测试与文档规范

- **测试目录分层**（`git mv` 保留历史，30 个文件）：
  `tests/architecture/`（架构与规范闸门）、`tests/platform/`（单库与迁移）、
  `tests/board_<板块>/`（六个板块）、`tests/smoke/`（主壳与跨板块联动）、`tests/helpers/`（辅助脚本）；
  里程碑式命名 `test_m0_smoke` / `test_m4_link` 改为 `test_shell_smoke` / `test_cross_board_link`。
- `pyproject.toml` 的 `pythonpath` 增加 `tests`（辅助脚本按模块名导入）；
  移动后修正了仓库根相对路径（`parents[1]` → `parents[2]`）。
- **新增规范文档**：`docs/TESTING.md`（分层/约定/命令/每板块冒烟清单）、
  `docs/RELEASE.md`（发版检查、打包三道自检、环境限制、数据兼容）、
  `tests/README.md`（目录说明），并按板块生成 `docs/BOARDS/{book,convert,music,video,gallery}.md`
  （目录清单、数据表、界面约定、测试与变更须知——文件清单与表名由脚本从代码提取，避免过期）。
- README 增加文档索引表。

### 发布产物（本机构建，2026-09-17）

| 产物 | 大小 | SHA256 |
|---|---|---|
| `dist/墨软工作台-Setup-1.0.0.exe`（NSIS 安装包） | 150.2 MB | `E0F11BBADD1C8C58535E5E8DA5AF1752A86CD8BB711F8174DF94462BF2AEB017` |
| `dist/ModuWorkbench/ModuWorkbench.exe`（onedir 启动器） | 10.5 MB | `1CAF6F6278AE81914BA2552224BDA2F7E81F27B4DC651529126CC4A7CD1D9E6B` |

- onedir 目录合计 **371 MB**（其中随包 ffmpeg/ffprobe 196 MB，其余 175 MB）；
- 相比 v0.3.x 的 731 MB 减少 **约 360 MB**：主要是移除 QtWebEngine（约 340 MB）
  与再精简 Qml/Quick/Pdf/VirtualKeyboard（约 18 MB，已用二进制导入表核实无依赖）；
- 打包流程三道自检全部通过：源码自检 13/13、exe 关键字模块核验 15/15、
  **打包产物自检 13/13（含 `multimedia_playback`——真实播放一段音频，验证精简后音视频后端仍可用）**；
- 清理死代码：`services/media_player.py`（旧内嵌前端 mp4 预览用的对话框，P5 后已无引用）。
- 未签名 exe 在开启「智能应用控制（Smart App Control）」的机器上会被系统拦截（本机即如此），
  因此**安装包的安装/卸载未在本机实际执行**；需关闭该功能或对 exe 签名后验证。

## v0.3.1 — 墨软影视修复版

### 修复：影视板块「播放和下载都失败」

- **采集站「分享页」地址没有解析（主要原因）**：非凡、电影天堂、U酷等站点返回的分集地址
  不是直链，而是 `https://vip.xxx.com/share/<hash>` 这样的 HTML 播放页（真实 m3u8 写在页面脚本里）。
  旧实现把这页 HTML 当成视频地址交给播放器和 ffmpeg，于是**这一类条目播放和下载同时失败**。
  现在 `CmsVodSource.play_url()` 会抓取播放页取出真实地址（按集缓存，同一集只抓一次），
  并把播放页所在站点作为下载 Referer（部分 CDN 会校验）。
  另外支持 `https:\/\/` 转义写法与站点根相对路径（`/2024/x/index.m3u8?sign=…`）。
- **失效采集源替换为实测可用的接口**：黑木耳（`json.heimuer.xyz` 404）、
  卧龙（返回 HTML）、天涯（返回「暂不支持搜索」）已经失效 —— 留在默认列表里会让每次搜索都报错、
  并拖慢聚合搜索。现在的默认源（均实测返回结果）：
  360资源、量子资源、如意资源、非凡资源、电影天堂、U酷资源。
  新增源清单版本（设置项 `sources/version`）：升级后旧的启用集合会回到新清单默认值
  （避免新源默认处于停用状态），用户自己填写的采集接口地址始终保留。
- **播放器内换清晰度/线路**：如果该线路给的是播放页地址，改为走解析链路拿真实地址，
  不再把页面地址直接塞进播放器。
- **聚合搜索不再被「视频直链」源刷噪音错误**：该源只能按地址检索，
  现在只有关键词本身就是 http(s) 地址时才参与聚合（粘贴地址播放的行为不变）。
- **源设置体验**：「测试全部」一键逐个源跑真实搜索，并给出「全部不可用 → 本机网络/代理问题」
  的结论；「恢复默认顺序」现在会同时还原内置接口地址（此前空值被忽略，改了域名就再也回不去）。
- **打包配置**：`workbench.spec` / `workbench_mac.spec` 里已删除的
  `ui_kit.settings_dialog` 隐藏导入改为 `ui_kit.settings` 包（消除打包期的 hidden import 报错）。

### 测试

- `tests/test_video_core.py` 新增：分享页解析（相对地址/转义写法/m3u8 优先）、按集缓存、
  直链不额外请求、播放页无地址时报可读错误、解析后 Referer 取播放页站点、
  内置源清单与顺序表一致、源清单版本升级迁移、直链源不参与关键词聚合搜索。

## v0.3.0 — 墨软·工作台 定版

首个五板块齐备、可离线分发的定版：**墨软书库 / 墨软转换 / 墨软乐库 / 墨软影视 / 墨软图库**。

### 新增：墨软图库（第五大板块）

桌面端本地相册（不做手机/小程序端），覆盖 PRD 的展示、分类、收集、导入、美化与 AI 优化。

- **图库展示**：网格（2/3/4 列可调）、瀑布流、时间轴（按年/月筛选）；缩略图**懒加载 + 磁盘缓存**，
  滚动只读几十 KB 的小图，万张图也流畅；大图查看支持缩放/拖拽/双击复位/上一张下一张/幻灯片。
- **分类管理**：自定义相册（删除相册不删原图）、收藏夹（与相册成员双向同步）、
  多标签体系（可重命名/合并/删除）、**本地规则自动分类**（横竖图/分辨率/时段/截图等），
  以及**感知哈希重复识别**（相同文件距离 0、缩放变体距离仍很小）。
- **导入与本地读取**：文件/文件夹批量导入、递归扫描、增量导入（按路径）、
  **内容级去重**（sha256，同图换名也不重复入库）、EXIF 读取（相机/光圈快门/ISO/GPS/方向）、
  导入进度与取消、失败不影响其余。
- **图片收集**：网址直链收集（校验 Content-Type）、剪贴板收集、截图目录一键扫描，
  来源标记（本地/文件夹/网址/剪贴板/截图）可用于筛选。
- **图片美化**：裁剪（自由框选 + 1:1/4:3/16:9/3:4 预设）、90° 旋转与任意角度、水平/垂直翻转、
  9 组滤镜（可调强度）、亮度/对比度/饱和度/锐化/色温、文字（可选颜色）、涂鸦与橡皮、
  马赛克/模糊、边框；**非破坏性步骤栈**支持撤销/重做/重置，按住可看原图；
  保存默认另存为新图并入库，覆盖原图需二次确认。
- **AI 图片优化（本地算法，numpy + Pillow，不联网、不上传）**：
  一键增强、超分辨率（2x/3x/4x）、锐化、降噪（边缘保留平滑）、去模糊、自动白平衡、去雾、
  人像柔化、背景移除、AI 消除、老照片修复、风格化（动漫/油画/水彩/黑白/暖冷调）；
  每项都给出**保真度标注**（本地算法 / 近似效果）与前后**画质指标对比**
  （清晰度/噪点/对比/亮度），结果另存为新图、原图不动。
- **DeepSeek 集成（仅文本能力，已在界面明确说明）**：生成图片描述与标签、
  自然语言检索拆关键词、按描述推荐修图参数。**DeepSeek 无法生成或编辑图片**，
  因此超分/抠图/去噪/消除由上面的本地算法完成，不做成"点了没反应"的按钮。
  默认**关闭**"允许上传图片到云端"，关闭时只发送文件名/EXIF 等元数据。

### 修复：墨软图库首轮使用反馈（4 项）

- **缩放下方的百分比不变**：大图查看的缩放比例原来只在换图时刷新，滚轮/按钮缩放后文案不变。
  现在 `ImageViewer` 缩放时发出 `zoomChanged`，标签实时显示「缩放 125%」/「适应窗口（当前显示 176%）」。
- **搜索后不显示、"下一张"没反应**：网格替换数据后 `currentRow` 变成 -1（无当前项），
  大图页与快捷键都拿不到"正在看哪张"。现在替换数据时**优先恢复原选中项、否则默认选中第一张**，
  并提供 `ensure_current()` 兜底；"上一张/下一张"改为在**当前筛选结果集内**翻页，不再乱跳。
- **扫描文件夹后缩略图是灰色占位、且回不到全部图片**：懒加载只覆盖可见区，未滚动到的位置一直是
  占位色，观感很差。现在替换数据后由 `fill_thumbnails_lazily()` **分批补齐全部缩略图**（不卡界面）；
  左侧「全部图片」是随时回到全量的入口，点击会自动清空搜索框与各项筛选。
- **左侧看不到"有哪些图片、怎么分类看"**：左侧改为**分类导航树**，列出
  「全部图片（含我的收藏、最近导入 7 天）／相册／时间（按年月）／标签／来源」，节点自带数量，
  点击即筛选；状态栏用「标签『风景』 · 共 30 张」这样的**范围文案**告诉用户当前在看哪个集合。
  切换节点会清掉其它筛选；"最近 N 天"这类范围筛选在用户手动改条件时自动失效，避免叠加成空结果。
- 顺带修掉点击左侧树节点的崩溃：重建树会把**正在处理点击事件的那个节点**销毁，
  PySide6 随后访问它就抛 `libshiboken: Internal C++ object (QTreeWidgetItem) already deleted`。
  现在树的重建**延后到点击处理结束**（`QTimer.singleShot(0, ...)`，带重入保护）。

### 新增：大模型能力中心（单独一块，多类型 + 多配置 + 优先级降级）

设置里新增独立的「🤖 大模型」页（需求：模型配置要能单独列出来一块）：

- **按类型管理**：文字大模型 / 图片大模型（多模态理解）/ 视频大模型（预留），
  每个类型各自的候选列表互不干扰；
- **同类型多份配置**：列表从上到下就是**调用优先级**，可上移/下移、单独启用或停用；
- **异常自动降级**：调用时按优先级依次尝试，任何一份失败（鉴权 401 / 余额 402 /
  限流 429 / 超时 / 模型不存在 / 连接被拒）都会**自动换下一份**，
  全部失败才报错，错误里按顺序列出每一次的失败原因；调用记录可在页面上回看；
- **服务商预设**：DeepSeek / OpenAI / 通义千问（DashScope 兼容）/ 智谱 GLM / Kimi /
  硅基流动 / **本地 Ollama** / 自定义 OpenAI 兼容接口，选服务商自动带出接口地址与模型名；
- **配置独立存放**：`%APPDATA%\ModuWorkbench\llm.db`，所有板块共用；
  首次打开会自动把旧版「图库 → DeepSeek」的 Key 迁移成一份文字配置 + 一份图片配置；
- **真正接入调用**：图库的「AI 描述/标签」走图片类型（失败自动退到文字类型，只发元数据）、
  「自然语言检索 / 推荐修图参数」走文字类型，全部经路由器按优先级降级；
  图库设置页不再重复填 Key，改为显示当前可用数量并可一键跳到「大模型」页。

### 修复：设置页与图库第三轮反馈（4 项）

- **设置项被顶出窗口、点不到保存/关闭**：设置页内容现在套在滚动区里，
  「保存 / 关闭」用分隔线固定在底部 —— 图库/大模型这类长页面在小窗口下也能滚动查看；
- **AI 优化的风格选择太丑**：风格从"挤在参数行里的窄下拉框"改为**可视化风格卡片**
  （动漫/油画/水彩/黑白/鲜艳/暖阳/冷调，点一下选中并显示说明），
  参数区改成独立卡片、每个参数一行，不需要参数的算法（一键增强等）直接收起参数区；
- **300+ 张图滚动卡顿、鼠标划过很乱**：图库网格**分页**（默认 120 张/页，可选 60/120/240/480
  或"全部（不分页）"），只把当前页交给网格，缩略图当页即可补齐，滚动不再出现"半灰半图"；
  底部新增分页条（页码/上一页/下一页/跳转，支持 PgUp/PgDn 快捷键），
  换筛选条件自动回到第 1 页，上一张/下一张跨页时会自动翻页；
  同时清掉了主题里 `QListWidget::item` 的默认底色，避免悬停时出现两层色块；
- **网格缩略图卡片**（上一轮）继续沿用，页面切换时会滚回顶部。

### 修复：墨软图库第二轮使用反馈（4 项）

- **网格排版（"名字在图片重叠"）**：改用自定义 delegate 画缩略图卡片 ——
  图片区恒为 `thumb×thumb`、文件名单独占一条 22px 的信息条，两者几何上不可能重叠；
  文件名过长时中间省略（保留扩展名），另加收藏角标、选中/悬停高亮，
  网格底色改为浅灰以衬托白色卡片（原来白卡压在白底上，糊成一片）。
- **大图缩放（"缩放和下一张后的缩放不一致"）**：缩放状态从"系数 + 是否自适应"改为
  **模式 + 百分比**：手动缩放后翻到上一张/下一张**保持同一比例**；
  从「适应窗口」按 +/− 时先把当前的适应百分比换算出来再乘系数，
  所以是连续的（此前 39% 一点就跳到 125%）。
- **大图页面留白太多**：查看大图时收起"导入/整理"底栏（那些按钮只对网格选中项有意义），
  图片可用高度增加近一倍；右侧新增**详情面板**（文件名/尺寸/大小/拍摄时间/相机/标签/AI 描述），
  竖图两侧的空白不再白留；图片加 1px 细边并铺浅色画布，看得出"相框"边界。
- **右键「编辑美化」没反应**：两个原因都修了 ——
  1）`gallery_editor.py` 漏了 `from PIL import Image`，大图走缩放预览分支时
  `Image.Resampling.LANCZOS` 抛 `NameError`（打包后的窗口程序没有控制台，异常完全不可见，
  表现就是"点了没反应"）；2）右键不再依赖"上一次的选中项"，**光标落在哪张就以哪张为目标**，
  此前没有任何选中项时菜单根本不弹、有选中项时又作用在别的图片上。
- **AI 优化的百分比一直是 0%**：优化/编辑是在对话框里跑的，结束后没有回写底栏，
  于是进度条永远停在 0%。现在完成时会写回进度与结果摘要
  （「AI 优化完成：生成 N 张新图；原图未改动」/「已保存编辑结果：xxx」）。
- 新增回归测试 `tests/test_gallery_editor.py`（编辑器能打开大图、操作链、另存、覆盖需确认）
  与 6 项界面测试（卡片几何、缩放跨图一致、底栏收放、右键命中、进度回写）。

### 新增：设置按板块分区

- 统一设置对话框改为**左侧板块分页**（通用 / 书库 / 转换 / 乐库 / 影视 / 图库）；
- **各板块顶栏新增「⚙ 板块设置」**，直接打开本板块那一页，不再需要在一堆混在一起的
  配置里找；单个板块设置页构造失败时用占位页显示原因，不会拖垮整个窗口。

### 增强：软件内播放（墨软影视）

- 播放改为**原生 QtMultimedia 优先**：实测 QtWebEngine 内置 Chromium 的
  `canPlayType('video/mp4; codecs="avc1..."')` 返回空、`<video>` 报 `code=4`，
  即**不含 H.264/AAC 专有编解码器**，而影视源几乎全是 H.264 —— 网页内核无法解码这些片源。
- 新增纯标准库 AES-128-CBC 解密（FIPS-197 / NIST SP 800-38A 向量验证），
  加密 HLS 分片不再依赖 ffmpeg。
- 修复代理响应的**长度定界**（真实源站常用 chunked 且无 Content-Length，
  原样转发会让客户端永远等不到响应结束 → 播放 fragLoadError、下载卡 0 字节）。
- ffmpeg 随包分发；子进程统一 UTF-8 编码（中文文件名曾让 ffprobe 静默失败）。

### 新增：墨软影视（第四大板块）

- **在线搜索与下载**：跨源聚合搜索电影 / 电视剧 / 动漫（可再筛综艺 / 纪录片），
  结果按「片名 + 年份」去重合并；双击条目打开「选集 / 详情」，可「在线播放」「下载选中集」
  「下载全部集」；也可直接粘贴 m3u8 / mp4 直链播放或下载。
- **多数据源可切换**（`core/video/sources/` 独立成包，全部免费、本地直连、无需登录）：

  | 数据源 | 能力 | 说明 |
  |---|---|---|
  | 360资源 / 黑木耳 / 非凡影视 / 卧龙资源 / 天涯资源 | 采集源 | 苹果CMS( maccms V10 )协议，覆盖电影/剧集/动漫/综艺 |
  | Internet Archive | 自由授权 | 公共领域电影/老动画/纪录片，可自由下载与离线观看 |
  | Wikimedia Commons | 自由授权 | 自由授权的影片与纪录片段 |
  | 视频直链 | 直链 | 自备 m3u8 / mp4 地址 |
  | 自定义采集源 | 自定义 | 填入任意苹果CMS协议接口 |

  三重容错：HTTP 退避重试 → 连续失败熔断降级（聚合搜索自动跳过）→ 解析/下载失败时按
  「片名 + 年份 + 主演」跨源匹配**同一部片、同一集**继续尝试。
- **源设置**（板块内「🧩 源设置」）：启用/停用、调整优先级、修改采集接口地址（站点换域名无需等版本更新）、
  一键测试连通性；停用的源不参与搜索与自动换源。
- **多清晰度播放器**：优先读取 m3u8 主清单里的真实分辨率（4K / 1080P / 720P …），
  解析不到时回退线路画质标签；播放中可切换清晰度（尽量保留进度）、上一集/下一集、
  倍速 0.5x–2x、音量/静音、全屏与快捷键（空格 / ←→ / F11）、断点续播。
  解码优先用原生 QtMultimedia，m3u8 或原生不支持的地址自动切到内嵌网页内核（hls.js）。
- **下载**：HLS 分片合流（有 ffmpeg 时输出 MP4，无 ffmpeg 时退化为 `.ts`）与直链下载，
  支持进度、取消与逐条失败原因；下载产物做容器嗅探，避免把错误页当成影片入库。
- **分类与收藏 / 我的视频**：新建分类、归类、一键收藏；按类型/分类/关键词筛选，
  区分「本地」与「在线」条目；支持导入本地影片与在文件夹中显示。
- **播放历史**：记录每次播放与下载（集数 / 画质 / 实际使用的源），双击即可重新解析继续观看。
- **视频格式转换**：mp4 / mkv / mov / avi / webm / flv / ts / gif 互转与从视频提取音频
  （mp3 / m4a / wav）；优先流复制，容器不兼容时自动回退重编码。
- **本地流服务增强**（`services/media_server.py`）：新增远程直链代理与 HLS 清单代理，
  由服务端补 Referer/UA —— 视频站普遍校验请求头，这样浏览器端与原生解码器都能直接播放。
  代理地址返回**绝对 URL**（`QMediaPlayer` 与 `<video>` 都不接受相对路径）。

### 修复（联调阶段实测发现）

- **首页少一个板块（第四板块不显示）**：PyInstaller 复用旧分析缓存会漏打包新增包。
  打包脚本默认 `--clean`，并新增「打包后校验影视模块是否真的进了 exe」的步骤。
- `providers/__init__.py` 与 `cms_vod.default_headers` 的相对导入多了一层（`..sources.base` → `..base`），
  会导致影视模块整体无法导入。
- `VideoPlayerDialog` 构造顺序错误：原生内核在音量滑块创建前就读取了 `self._volume`。
- `VideoRegistry(providers=...)` 默认启用集写死内置源 key，注入自定义源时会被静默过滤掉。
- `stream_url` / `hls_url` 返回相对路径，播放内核无法加载（改为绝对 URL）。
- `parse_m3u8` 的分片时长：`TARGETDURATION` 会覆盖 `EXTINF`，改为优先取 `EXTINF` 最大值。
- `VideoBoardPage` 依赖注入存在自相矛盾状态（库用内置源、播放走另一个源），
  现在以 `library` 为唯一事实来源，播放解析必定与界面展示的源一致。
- `VideoDetailDialog` 关闭时未收尾后台线程，会把仍在运行的 `QThread` 交还给垃圾回收，
  触发「QThread: Destroyed while thread is still running」并**终止进程**（测试中表现为崩溃）。
  现在先等待、必要时强杀，绝不留下运行中的线程；清晰度探测超时也收紧为 4 秒单次尝试。
- **AES-128 加密的 HLS 不再需要 ffmpeg**：此前没装 ffmpeg 时下载加密流会直接失败
  （「该视频流已加密（AES-128），需要 ffmpeg」）。现在内置纯标准库的 AES-128-CBC 解密
  （`core/video/aes.py`，正确性由 FIPS-197 与 NIST SP 800-38A 官方向量验证），
  无 ffmpeg 也能下载加密流；SAMPLE-AES 等仍会明确提示需要 ffmpeg。
- **ffmpeg 随包分发**：`tools/ffmpeg/{ffmpeg,ffprobe}.exe` 打进 `_MEIPASS/tools/ffmpeg`，
  用户无需自行安装即可转换音视频、把 HLS 下载为 MP4；定位优先级
  `MODU_FFMPEG` → 随包目录 → PATH（`ffprobe` 亦统一走同一套逻辑，不再各自实现）。
- ffmpeg 9.x 兼容：`-user_agent` / `-headers` 在 9.x 会报 "Option not found"，
  改为让 ffmpeg 读取「经本机代理改写后的本地 m3u8」，请求头由流服务在服务端补，
  不再依赖 ffmpeg 的 HTTP 选项（跨版本稳定）。
  同时为本地 m3u8 显式放行协议（`-protocol_whitelist`），否则新版本 ffmpeg
  会拒绝「本地清单引用 http 分片」（`Protocol 'http' not on whitelist`）。
- **在线播放加密流失败（hls.js 报 `networkError / keyLoadError`）**：
  清单代理漏了 `#EXT-X-KEY` 的密钥 URI 改写，浏览器跨域取密钥被拒。
  现在密钥同样走本机代理（下载路径早已改写，播放路径此前遗漏）。
- **下载加密流失败（ffmpeg 报 `not in allowed_segment_extensions`）**：
  ffmpeg 的 HLS 解复用器按 URL 扩展名判断分片类型，而代理地址形如
  `/proxy/?u=...` 不以 `.ts` 结尾，导致每个分片都被拒绝。
  现在代理地址会带上原始文件名（`/proxy/seg0.ts?u=...`），服务端仍按路径前缀分发。
- **播放 `fragLoadError` / 下载一直 0 字节（根因）**：本机代理转发的响应
  **既没有 `Content-Length` 也没有 `Transfer-Encoding`**（真实源站常用 chunked），
  客户端永远等不到「响应结束」，于是 hls.js 报分片加载失败、ffmpeg 卡在 0 字节。
  现在代理统一读全量并显式给出 `Content-Length`（分片另补 `Content-Range`）。
- **子清单相对路径 404**：主清单指向的子清单经 `/proxy/` 取回时未改写内部地址，
  相对分片路径会以「本机代理」为基准解析而 404（ffmpeg 报 `Failed to open segment 0`）。
  现在 `/proxy/` 与 `/hls/` 一样会识别清单并改写。
- **网页内核根本播不了这些片源**：实测 QtWebEngine 内置 Chromium
  `canPlayType('video/mp4; codecs="avc1..."')` 返回空、`<video>` 报 `code=4`，
  即**不含 H.264/AAC 等专有编解码器**，而影视源几乎全是 H.264。
  播放已改为**原生 QtMultimedia 优先**（FFmpeg 后端可解 H.264/AAC），
  HLS 不再自动切网页内核（切过去只会得到 `bufferAppendError`）。
- **播放起播慢被误判为卡死**：HLS 需先缓冲若干分片，远端站点慢时可能要 20~30 秒；
  现在状态栏会显示「正在缓冲…已等待约 N 秒」，且不再提前判失败。
- **上游抖动导致分片 502**：第三方站点连接重置/瞬时 SSL EOF 很常见，
  代理现在带退避重试（3 次），避免单个分片失败就让 ffmpeg/hls.js 放弃。
- **下载进度一直显示 0 MB**：`-c copy` 写 MP4 时 ffmpeg 会先缓冲再落盘，
  按输出文件大小报进度必然长时间为 0。现在改用 ffmpeg 的 `-progress` 输出，
  按「已获取时长 / 总时长」给出百分比。
- **中文文件名导致 ffprobe 崩溃**：Windows 下 `text=True` 默认按 GBK 解码，
  而 ffprobe 输出是 UTF-8，会让读取线程抛 `UnicodeDecodeError`（时长探测静默失败）。
  已为所有子进程调用显式指定 `encoding="utf-8", errors="replace"`。

### 新增：墨软乐库（第三大板块）

- **在线搜索与下载**：按歌曲 / 歌手 / 专辑 / 类型检索，结果可勾选批量下载，或只选中一行单条下载；
  右键菜单支持「试听这首 / 下载这首 / 加入歌单」。
- **多音源容错**（`core/music/sources/` 独立成包，全部免费、本地直连、无需登录）：

  | 音源 | 能力 | 密钥 |
  |---|---|---|
  | 网易云音乐 | 完整曲目（公开 Web 接口） | 否 |
  | 酷我音乐 | 完整曲目（检索 + antiserver 直链） | 否 |
  | Audius | 自由授权完整曲目 | 否 |
  | Internet Archive | 公共领域 / CC 音频 | 否 |
  | ccMixter | CC 音乐社区 | 否 |
  | iTunes 试听 | 30 秒片段 + 完整元数据 | 否 |
  | Jamendo | CC 完整曲目 | 免费 client_id |
  | 音频直链 | 自建 / 已授权地址 | 否 |

  三重容错：HTTP 退避重试 → 连续失败熔断降级（聚合搜索自动跳过）→ 解析/下载失败时按
  「曲名 + 歌手 + 时长」跨源匹配同一首歌继续下载，下载后仍校验文件确为音频。
- **内置播放器**：底部常驻播放条（切板块不中断），顺序 / 列表循环 / 单曲循环 / 随机，
  音量与进度可调；在线试听复用同一播放条，失败会给出具体原因并自动跳下一首。
- **曲库与歌单**：本地导入（含时长自动补全）、收藏与分类、歌单（含「待下载」在线曲目）、
  播放历史；歌单内支持循环 / 随机播放。
- **音频格式转换**：mp3 / m4a / wav / flac / aac / ogg / opus / wma 互转（依赖 ffmpeg）。

### 新增：墨软转换内嵌前端与图形

- 转换板块复用 main 分支 React 界面（Vite 产物 → `src/modu_workbench/webfront/`，
  QtWebEngine + QWebChannel 桥接 Python 引擎），标题统一为「墨软转换」；
  转换核心、文档读写、媒体流全部在 Python 侧实现。
- mp4 内联预览走本地流服务（支持 Range），失败回退原生播放器。
- 新增品牌图形：白底 + 蓝色双向箭头标记（`app.ico` 多尺寸 / `app.png` 512）、
  安装向导页眉与欢迎页图，均由 `packaging/make_icons.py` 生成并接入 exe 与安装包。

### 修复

- 首页：幽灵卡与第三张板块卡同格导致「墨软乐库」卡片被压住、点不动 → 幽灵卡改放下一个空格。
- 搜索页：点「下载（单条或批量）」时 `clicked(bool)` 被当成目标列表，误报「请先勾选」→ 已修。
- 收藏：`checked_track_ids` 把整表当作勾选 → 现在只作用于勾选/选中项，且「收藏」列可单击切换。
- 试听：此前用独立播放器，既无进度也无错误提示 → 改走播放条并补齐错误提示。
- 下载：平台返回版权页 / 占位文件会被当成「下载完成」→ 现在校验音频并明确报错；
  网络抖动自动重试，最终失败给出可读原因（DNS / 超时 / 连接被拒）。
- 打包：NSIS 与 Windows PowerShell 5.1 依赖 UTF-8 BOM，丢失会导致打包失败 → 已加回归测试。

### 更名

- 显示名称统一为「墨软」系列：墨软·工作台 / 墨软书库 / 墨软转换 / 墨软乐库 / 墨软影视；
  内嵌前端不再使用「万能格式转换器」。
- 内部标识保持不变以兼容既有数据：Python 包 `modu_workbench`、`ModuWorkbench.exe`、
  `MODU_*` 环境变量、数据目录 `%APPDATA%\ModuWorkbench`（`library.db` / `music.db` / `video.db`）。

### 发布产物与校验

- `dist\ModuWorkbench\ModuWorkbench.exe`（onedir 主程序，11.5 MB；整目录含依赖与 ffmpeg 共 731.8 MB）
  SHA256：`84B1992AB92788DEC09561D4F43D25F5E3883D34D5BE96547F18C2A90D7353BD`
- `dist\墨软工作台-Setup-0.3.0.exe`（NSIS 安装包，281.7 MB，安装后即为可运行的五板块工作台）
  SHA256：`FB350C10DE998F9019F5FA97688E2946A9D5334E6B7A688CC08F8C7E3DCDC7CB`

```powershell
# 校验下载到的安装包
Get-FileHash '.\墨软工作台-Setup-0.3.0.exe' -Algorithm SHA256

# 打包自检（15 项，含真实音频解码播放、影视/图库/大模型核心、随包 ffmpeg 与品牌图标）
$env:MODU_CHECK_DEPS = "$env:TEMP\modu-check.json"
.\dist\ModuWorkbench\ModuWorkbench.exe
Get-Content $env:MODU_CHECK_DEPS
```

### 已知限制

- 视频格式转换与「HLS 下载为 MP4」推荐使用随包 ffmpeg（已随包分发）；
  若自行替换/缺失，会退回输出 `.ts`（仍可播放），并可用 `MODU_FFMPEG` 指定。
- 墨软影视：SAMPLE-AES 等非 AES-128 的加密流必须依赖 ffmpeg；
  网站 HLS 播放依赖联网加载 hls.js（首次播放）。
- Word/Excel → PDF 高保真需 LibreOffice（`MODU_SOFFICE`），否则使用内置兜底排版。
- 网易云 VIP / 下架曲目、iTunes 仅 30 秒片段、Jamendo 需免费 client_id。
- 在线音源 / 影视采集接口均为第三方公开服务，可能随时变更或失效（影视源可在「源设置」里改地址或换源）。
- 在线音源与采集类影视源仅供个人学习、试听与自有内容备份，请遵守各平台条款与版权要求。
