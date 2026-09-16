# 墨软·工作台 —— 架构与合并方案

本文记录「fileType（格式转换）」与「win-e-book（墨软书库）」合并为单一 **Python/PySide6** 应用的架构决策与落地方式。

## 背景与决策

- 产品名：**墨软·工作台**；形态：**全 Python 单应用**（PySide6/Qt6），onedir 分发 + NSIS 安装包；
- 仓库：沿用 fileType 仓库演进（历史保留），旧 Electron/React 代码归档于 `archive/electron-formatflow/`；
- 板块化：首页 + 可扩展板块入口，当前为「墨软书库」「墨软转换」「墨软乐库」「墨软影视」，未来可继续追加；
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

## 合规要点

- ebooklib(AGPL-3.0) 仅用于本地阅读解析，无网络服务分发场景，保留依赖与声明；
- ffmpeg 静态二进制为 GPL 构建，内部/个人使用，对外分发需替换或声明；
- 在线书源（00shu 等）默认开启，提供一键关闭 + “个人学习用途”声明。
- 在线音乐音源（网易云 / 酷我 / Audius / Internet Archive / ccMixter / iTunes / Jamendo）仅供个人学习、
  试听与自有内容备份，界面默认开启合规声明勾选；请遵守各平台条款与版权要求，勿传播受版权保护的内容。
- 影视采集类数据源来自第三方公开站点（苹果CMS 协议接口），仅作技术研究与个人学习用途；
  界面默认开启合规声明勾选，并支持在「源设置」里停用或更换任意数据源。
