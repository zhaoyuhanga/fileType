# 墨软·工作台 —— 架构与合并方案

本文记录「fileType（格式转换）」与「win-e-book（墨软书库）」合并为单一 **Python/PySide6** 应用的架构决策与落地方式。

## 背景与决策

- 产品名：**墨软·工作台**；形态：**全 Python 单应用**（PySide6/Qt6），onedir 分发 + NSIS 安装包；
- 仓库：沿用 fileType 仓库演进（历史保留），旧 Electron/React 代码归档于 `archive/electron-formatflow/`；
- 板块化：首页 + 可扩展板块入口，当前为「墨软书库」「墨软转换」「墨软乐库」「墨软影视」「墨软图库」，未来可继续追加；
- 后端主技术：**Python**（无 Node/Python 双运行时）；SQLite 用 Python 标准库 `sqlite3`；
- **界面沿革（route A）**：转换板块与文档预览复用 main 分支 React 界面 —— Vite 产物由
  `packaging/build_webfront.*` 整理进 `src/modu_workbench/webfront/`，QtWebEngine 加载，
  经 QWebChannel（`services/web_bridge.py` + `services/bridge_shim.js`）调用 Python 引擎；
  转换核心、文档读写、媒体流全部在 Python 侧，Electron 主进程与 Node 能力不再参与运行。

## 顶层结构

- `src/modu_workbench/boards/`：板块注册表（BoardSpec + 页面类），新增板块只需注册一条。
- `src/modu_workbench/boards/convert_web.py`：内嵌前端页面（QtWebEngine + 桥）；`convert_board.py`/`doc_viewer.py` 为纯 Qt 回退实现。
- `src/modu_workbench/ui_kit/`：统一设计令牌（ThemeTokens）+ QSS + 基础组件，各板块共用视觉规范。
- `src/modu_workbench/core/reader/`：墨软书库引擎（自 win-e-book 迁移：parser/storage/library/online）。
- `src/modu_workbench/core/convert/`：墨软转换引擎（自 fileType 行为重写：registry、text/pdf/image/media/archive 等）。
- `src/modu_workbench/core/music/`：墨软乐库引擎（models/storage/sources/downloader/library/player）。
- `src/modu_workbench/boards/music_*.py`：墨软乐库界面（搜索下载 / 我的音乐 / 格式转换 / 歌单收藏 / 播放历史 + 常驻播放条）。
- `src/modu_workbench/core/video/`：墨软影视引擎（models/storage/hls/sources/downloader/library）。
- `src/modu_workbench/boards/video_*.py`：墨软影视界面（搜索下载 / 详情选集 / 我的视频 / 分类收藏 / 播放历史 / 源设置 + 播放器窗口）。
- `src/modu_workbench/core/image/`：墨软图库引擎（models/storage/hashing/exif/thumbs/edits/enhance/ai/library）。
- `src/modu_workbench/boards/gallery_*.py`：墨软图库界面（图库浏览 + 大图查看 / 编辑美化 / AI 优化）。
- `src/modu_workbench/ui_kit/settings/`：**分板块设置页**（通用/书库/转换/乐库/影视/图库），统一对话框左侧分页。
- `src/modu_workbench/services/`：桥接与本地服务（web_bridge/web_prepare/media_server/media_player/file_scan）。
- `src/modu_workbench/webfront/`：内嵌前端产物（构建生成，随包分发）。
- `tests/`：pytest 单元与无头冒烟测试。

## 墨软乐库设计要点

- **音源层独立成包**（`core/music/sources/`）：`MusicSource` 接口 + `MusicRegistry` 编排，
  内置 netease / kuwo / audius / archive / ccmixter / itunes / jamendo / direct 八个音源；
  新增音源只需实现 `search()` 与 `download_url()` 并在 `providers/__init__.py` 注册。
- **三重容错**：① HTTP 层退避重试（DNS/连接抖动）；② 注册表熔断——连续失败 3 次的音源临时降级，
  聚合搜索自动跳过；③ 跨源兜底——解析或下载失败时按「曲名 + 歌手 + 时长」到其他音源匹配同一首歌继续尝试，
  下载后仍会校验文件确为音频（避免把版权提示页当成歌曲入库）。
- **数据模型**：`music.db` 存 `tracks` / `playlists` / `playlist_items` / `playlist_remotes`（歌单内待下载项）/ `history` / `settings`；
  音源启用状态、优先级与凭据保存在 `settings` 表（`sources/*`）。
- **歌单与收藏**：`playlists.kind = favorite` 即「我的收藏」，收藏标记与歌单成员双向同步；分类以 `tracks.category` 承载。
- **播放器**：`MusicPlayer` 独立于界面（应用级单例），队列 + 顺序/列表循环/单曲循环/随机；
  在线试听复用同一播放条（进度/错误可见）；无音频后端或测试环境自动进入静默模式。
- **后台任务**：搜索 / 下载 / 转换均为 `QThread` 工作线程，支持取消，进度与结果经信号回主线程。

## 墨软影视设计要点

- **数据源层独立成包**（`core/video/sources/`）：`VideoSource` 接口 + `VideoRegistry` 编排，
  内置苹果CMS( maccms V10 )采集源若干 + Internet Archive / Wikimedia Commons / 直链 / 自定义采集源；
  新增数据源只需实现 `search()`（按需 `detail()` / `play_url()`）并在 `providers/__init__.py` 注册。
- **三重容错**：① HTTP 层退避重试（并统一补 Referer/UA）；② 注册表熔断——连续失败 3 次的源临时降级，
  聚合搜索自动跳过；③ 跨源兜底——解析不出地址时按「片名 + 年份 + 主演」到其他源匹配**同一部片、同一集**继续尝试。
- **多清晰度**：`hls.py` 解析 m3u8 主清单（`EXT-X-STREAM-INF` → 真实分辨率），
  媒体清单解析分片（`EXTINF`）与加密标记（`EXT-X-KEY`）；找不到变体时回退到源给出的线路画质标签。
- **数据模型**：`video.db` 存 `videos` / `playlists` / `playlist_items` / `history` / `play_records` / `settings`；
  `videos.file_path` 为空表示「只入库未下载」（可直接在线播放），因此收藏/分类/历史对在线与本地条目统一可用；
  `history.title_snapshot` 让条目删除后历史仍可读。
- **播放内核**：优先 QtMultimedia（原生进度/音量/倍速），m3u8 或原生不支持的地址自动切到
  QtWebEngine + hls.js；远程地址统一经 `services/media_server.py` 的本机代理（服务端补 Referer/UA，
  HLS 清单内的分片地址被重写为继续走代理）。
- **下载**：HLS 优先用 ffmpeg `-c copy` 合流为 MP4（AAC 的 `aac_adtstoasc` 失败时自动去掉重试），
  无 ffmpeg 时退化为分片拼接为 `.ts`；直链走流式下载；两者都做容器嗅探（`looks_like_video`）。
- **后台任务**：搜索 / 详情 / 解析 / 下载 / 转换均为 `QThread` 工作线程，支持取消与进度回报。

## 墨软图库设计要点

- **桌面端本地相册**：不引入手机相册权限模型，直接在文件系统上扫描与索引（`IMAGE_EXTENSIONS`）。
- **性能三件套**（对应「1 万张图流畅滚动」）：
  1. `thumbs.py` 按「路径+mtime+大小+目标尺寸」生成**磁盘缩略图缓存**，滚动只读几十 KB 小图；
  2. 网格**懒加载**——只为视口上下各一屏的项目创建 QPixmap，其余保持占位；
  3. 缩略图缓存带内存 LRU（只丢索引，磁盘缓存保留）。
  懒加载之外还有 `fill_thumbnails_lazily()`：替换数据后**分批补齐视口外的缩略图**（`QTimer` 分块，
  不阻塞界面），否则用户没滚动到的位置会一直是灰色占位（实测反馈「图片显示好丑」）。
- **左侧分类导航树**（`_refresh_side()`）：按「全部图片（含我的收藏 / 最近导入 7 天）／相册／
  时间（年月）／标签／来源」生成节点，节点文本带数量、`ITEM_ROLE` 存筛选 payload（字典），
  点击即调用 `reload()` 重查；分组标题只置灰不可点。状态栏 `_scope_text()` 回显当前范围。
  注意：**树的重建必须延后**（`QTimer.singleShot(0, ...)`，带重入保护）——在点击处理中直接
  `clear()` 会销毁正在派发事件的那个 `QTreeWidgetItem`，随后报
  `libshiboken: Internal C++ object (QTreeWidgetItem) already deleted`。
- **结果集语义一致**：`ThumbnailGrid.set_items()` 会**尽量保留原选中项、否则选中第一张**，
  并提供 `ensure_current()`；上一张/下一张、编辑等操作都以「当前网格列表」为准，
  因此搜索/筛选后翻页不会跳到结果集之外的图片（`currentRow == -1` 曾导致「下一张没反应」）。
- **卡片式绘制**（`ThumbnailDelegate`）：默认图标模式会把文件名压在缩略图底部，
  所以改为自绘卡片 —— 图片区恒为 `thumb×thumb`、文件名单独占 `TITLE_HEIGHT` 高的一条，
  几何上不可能重叠；配合 `_cell_size()` 统一算格子尺寸（改缩略图档位时一并更新）。
- **大图查看的缩放模型**（`ImageViewer`）：状态是「模式 + 百分比」而不是「系数 + 是否自适应」。
  `fit` 模式按窗口自适应；`manual` 模式记住百分比，**翻页时保持同一比例**；
  从 fit 按 +/− 时先把当前适应百分比换算出来再乘系数，画面连续不跳变。
  查看大图时会收起浏览底栏（`show_page` 控制），右侧固定详情面板，图片区加 1px 细边 + 浅色画布。
- **右键目标解析**：`ThumbnailGrid.focus_at(pos)` 命中光标下的图片并选中它。
  默认右键不改变选中项，会导致菜单作用于"上一次选中的图"，甚至在无选中项时根本不弹菜单。
- **两级去重**：`sha256` 内容哈希（导入阶段直接跳过完全相同的文件）+
  `dhash` 感知哈希（汉明距离聚类，识别原图与缩放/微调副本）。
  注意 dhash 必须用 numpy 取像素 —— 旧版 `Image.getdata()` 在新 Pillow 上会返回空序列，
  导致所有哈希都为 0（去重与相似识别静默失效）。
- **非破坏性编辑**：`edits.py` 把操作记成 `EditStep` 步骤栈，随时重放/撤销/改参数；
  预览在缩放后的图上渲染（`PREVIEW_MAX`），只有保存时才全尺寸渲染，避免大图卡顿。
  ⚠️ `gallery_editor.py` 必须 `from PIL import Image`：预览缩放用到
  `Image.Resampling.LANCZOS`，漏导入时只在"图片大于 PREVIEW_MAX"这条分支上抛 `NameError`，
  而打包后的窗口程序没有控制台 —— 用户看到的就是「右键 → 编辑美化 → 没反应」。
- **AI 优化的能力边界**：`enhance.py` 用 numpy + Pillow 实现本地算法（一键增强/超分/降噪/
  去模糊/白平衡/去雾/人像柔化/背景移除/消除/老照片/风格化），**不依赖外部模型**；
  `EnhanceSpec.fidelity` 明确区分「本地算法」与「近似效果」，界面上如实标注，
  并提供 `compute_quality_metrics` 给出前后客观指标（清晰度/噪点/对比/亮度）。
- **DeepSeek 只做文本**：`ai.py` 负责描述/标签/检索关键词/参数建议。
  **它不能生成或编辑图片**，因此不参与像素级优化 —— 这一点在设置页与图库页都有明确说明，
  避免做成"点了没反应"的按钮。默认关闭图片上传，关闭时只发送元数据。
- **后台任务**：导入扫描 / 增强 / AI 分析 / 重复检测均为 `QThread`，支持取消与进度回报。

## 设置分区

设置页从"一个混合对话框"改为 `ui_kit/settings/` 下的**分板块页面**：
每页实现 `SettingsPage` 的 `load()` / `save()` / `hint()`，统一对话框左侧分页装配；
各板块顶栏的「⚙ 板块设置」用 `SettingsDialog(initial=<板块key>)` 直达本板块页。
单页构造失败会用占位页显示原因，不影响其它板块。

## 行为对齐清单（fileType → Python）

- 批量任务队列：实时进度 / 取消 / 状态本地化 / Toast 统一提示；
- 输出安全：同名输出自动加序号、归档解压路径穿越防护；
- 文档查看编辑：预览只读、编辑、保存、另存为、JSON 美化、未保存确认；
- 编码自适应：UTF-8 / UTF-16 BOM / GBK(GB18030)；
- 转换等价：文本互转、PDF(Qt 输出)、图片互转(Pillow)、表格(openpyxl/xlrd)、
  音视频(ffmpeg 子进程)、Word(docx)、ZIP/TAR/RAR、mp4 预览(QMediaPlayer)。

## 里程碑

- M0 骨架（首页/板块注册/统一主题/冒烟测试）✅
- M1 墨软书库（书库/章节解析/进度记忆/在线书库）✅
- M2 墨软转换基础（文本/PDF/图片/归档/查看编辑/任务队列）✅
- M3 墨软转换高级（docx/xls/ffmpeg 媒体/Word·Excel→PDF）✅
- M4 整合打包（跨板块联动/设置/PyInstaller/发布）✅
- M5 内嵌 main 分支前端（QtWebEngine + QWebChannel 桥）/ mp4 流式预览 / NSIS 安装包 ✅
- M6 墨软乐库（在线搜索下载 / 播放器 / 歌单收藏分类 / 播放历史 / 音频格式转换）✅
- M7 墨软影视（多源搜索下载 / 多清晰度播放器 / 分类收藏 / 播放历史 / 视频格式转换）✅
- M8 墨软图库（本地相册 / 分类标签收藏 / 导入去重 / 美化编辑 / 本地增强）+ 设置按板块分区 ✅

## 合规要点

- ebooklib(AGPL-3.0) 仅用于本地阅读解析，无网络服务分发场景，保留依赖与声明；
- ffmpeg 静态二进制为 GPL 构建，内部/个人使用，对外分发需替换或声明；
- 在线书源（00shu 等）默认开启，提供一键关闭 + “个人学习用途”声明。
- 在线音乐音源（网易云 / 酷我 / Audius / Internet Archive / ccMixter / iTunes / Jamendo）仅供个人学习、
  试听与自有内容备份，界面默认开启合规声明勾选；请遵守各平台条款与版权要求，勿传播受版权保护的内容。
- 影视采集类数据源来自第三方公开站点（苹果CMS 协议接口），仅作技术研究与个人学习用途；
  界面默认开启合规声明勾选，并支持在「源设置」里停用或更换任意数据源。
