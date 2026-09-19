# 更新日志（CHANGELOG）

本项目遵循语义化版本；版本号单一来源为 `src/modu_workbench/__init__.py` 的 `__version__`
（与 `pyproject.toml` 保持一致，见 `tests/architecture/test_version.py`）。

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
| `dist/墨软工作台-Setup-1.0.4.exe`（NSIS 安装包） | 151.3 MB | `7F7977A5411B219CD45060E93D85120FB310AD56E0ACB40A1FF130C6C130E1A8` |
| `dist/ModuWorkbench/ModuWorkbench.exe`（onedir 启动器） | 11.5 MB | `CC24E510048869395CC4235C27D870D9ED8409FCC1555E9BADB38882182D8FB9` |

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
