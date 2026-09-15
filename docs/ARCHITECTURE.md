# 墨读·工作台 —— 架构与合并方案

本文记录「fileType（格式转换）」与「win-e-book（墨读书库）」合并为单一 **Python/PySide6** 应用的架构决策与落地方式。

## 背景与决策

- 产品名：**墨读·工作台**；形态：**全 Python 单应用**（PySide6/Qt6），onedir 分发 + NSIS 安装包；
- 仓库：沿用 fileType 仓库演进（历史保留），旧 Electron/React 代码归档于 `archive/electron-formatflow/`；
- 板块化：首页 + 可扩展板块入口，当前为「墨读书库」「墨读转换」「墨读音乐」，未来可继续追加；
- 后端主技术：**Python**（无 Node/Python 双运行时）；SQLite 用 Python 标准库 `sqlite3`；
- **界面沿革（route A）**：转换板块与文档预览复用 main 分支 React 界面 —— Vite 产物由
  `packaging/build_webfront.*` 整理进 `src/modu_workbench/webfront/`，QtWebEngine 加载，
  经 QWebChannel（`services/web_bridge.py` + `services/bridge_shim.js`）调用 Python 引擎；
  转换核心、文档读写、媒体流全部在 Python 侧，Electron 主进程与 Node 能力不再参与运行。

## 顶层结构

- `src/modu_workbench/boards/`：板块注册表（BoardSpec + 页面类），新增板块只需注册一条。
- `src/modu_workbench/boards/convert_web.py`：内嵌前端页面（QtWebEngine + 桥）；`convert_board.py`/`doc_viewer.py` 为纯 Qt 回退实现。
- `src/modu_workbench/ui_kit/`：统一设计令牌（ThemeTokens）+ QSS + 基础组件，两板块共用视觉规范。
- `src/modu_workbench/core/reader/`：墨读书库引擎（自 win-e-book 迁移：parser/storage/library/online）。
- `src/modu_workbench/core/convert/`：墨读转换引擎（自 fileType 行为重写：registry、text/pdf/image/media/archive 等）。
- `src/modu_workbench/core/music/`：墨读音乐引擎（models/storage/sources/downloader/library/player）。
- `src/modu_workbench/boards/music_*.py`：墨读音乐界面（搜索下载 / 我的音乐 / 格式转换 / 歌单收藏 / 播放历史 + 常驻播放条）。
- `src/modu_workbench/services/`：桥接与本地服务（web_bridge/web_prepare/media_server/media_player/file_scan）。
- `src/modu_workbench/webfront/`：内嵌前端产物（构建生成，随包分发）。
- `tests/`：pytest 单元与无头冒烟测试。

## 墨读音乐设计要点

- **音源抽象**：`MusicSource` 接口 + 注册表（`netease` / `itunes` / `jamendo` / `url`），
  搜索结果统一为 `RemoteTrack`，下载地址解析与歌词获取由音源实现，便于后续接入新音源。
- **数据模型**：`music.db` 存 `tracks` / `playlists` / `playlist_items` / `playlist_remotes`（歌单内待下载项）/ `history` / `settings`。
- **歌单与收藏**：`playlists.kind = favorite` 即「我的收藏」，收藏标记与歌单成员双向同步；分类以 `tracks.category` 承载。
- **播放器**：`MusicPlayer` 独立于界面（应用级单例），队列 + 顺序/列表循环/单曲循环/随机；
  无音频后端或测试环境自动进入静默模式（仅维护状态，不阻塞 UI 与测试）。
- **后台任务**：搜索 / 下载 / 转换均为 `QThread` 工作线程，支持取消，进度与结果经信号回主线程。

## 行为对齐清单（fileType → Python）

- 批量任务队列：实时进度 / 取消 / 状态本地化 / Toast 统一提示；
- 输出安全：同名输出自动加序号、归档解压路径穿越防护；
- 文档查看编辑：预览只读、编辑、保存、另存为、JSON 美化、未保存确认；
- 编码自适应：UTF-8 / UTF-16 BOM / GBK(GB18030)；
- 转换等价：文本互转、PDF(Qt 输出)、图片互转(Pillow)、表格(openpyxl/xlrd)、
  音视频(ffmpeg 子进程)、Word(docx)、ZIP/TAR/RAR、mp4 预览(QMediaPlayer)。

## 里程碑

- M0 骨架（首页/板块注册/统一主题/冒烟测试）✅
- M1 墨读书库（书库/章节解析/进度记忆/在线书库）✅
- M2 墨读转换基础（文本/PDF/图片/归档/查看编辑/任务队列）✅
- M3 墨读转换高级（docx/xls/ffmpeg 媒体/Word·Excel→PDF）✅
- M4 整合打包（跨板块联动/设置/PyInstaller/发布）✅
- M5 内嵌 main 分支前端（QtWebEngine + QWebChannel 桥）/ mp4 流式预览 / NSIS 安装包 ✅
- M6 墨读音乐（在线搜索下载 / 播放器 / 歌单收藏分类 / 播放历史 / 音频格式转换）✅

## 合规要点

- ebooklib(AGPL-3.0) 仅用于本地阅读解析，无网络服务分发场景，保留依赖与声明；
- ffmpeg 静态二进制为 GPL 构建，内部/个人使用，对外分发需替换或声明；
- 在线书源（00shu 等）默认开启，提供一键关闭 + “个人学习用途”声明。
- 在线音乐音源（网易云 / iTunes / Jamendo）仅供个人学习、试听与自有内容备份，
  界面默认开启合规声明勾选；请遵守各平台条款与版权要求，勿传播受版权保护的内容。
