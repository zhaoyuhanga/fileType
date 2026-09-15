# 墨读·工作台 (Modu Workbench)

> 本地离线的一站式阅读与转换工作台 —— 板块化设计，能力可扩展。

由两个历史项目合并演进而来，现为三大板块：

- **墨读书库**：承接 win-e-book —— TXT/EPUB 本地书库、章节自动解析、进度记忆、阅读主题、在线书库下载（个人学习用途，默认开启）。
- **墨读转换**：承接 fileType —— 六类本地离线转换（文档 / 表格 / 图片 / 媒体 / 归档）与 txt/md/json/mp4 查看编辑。
- **墨读音乐**：新增 —— 联网搜索与下载音乐、内置播放器（顺序/循环/随机）、歌单收藏与分类、播放历史、音频格式转换。

旧 Electron/React 工程归档于 `archive/electron-formatflow/`：其渲染进程（React 界面）仍作为**内嵌前端**使用 ——
构建产物整理进 `src/modu_workbench/webfront/`，由 QtWebEngine 加载，经 QWebChannel 桥接到 Python 引擎；
Electron 主进程代码仅作参考，不再构建维护。

## 板块

启动进入首页，卡片/顶栏切换板块：

| 板块 | 内容 | 状态 |
|---|---|---|
| 墨读书库 | 书架（导入/搜索/卡片/历史）、阅读器（TOC/主题/字号/进度记忆/快捷键）、在线书库（00shu 整本直链下载 + 合规开关） | ✅ |
| 墨读转换 | 文本互转/PDF(Qt)/图片/Word/表格/归档/媒体，查看编辑与 JSON 美化、批量任务（进度/取消/输出防覆盖/解压防穿越） | ✅ |
| 墨读音乐 | 在线搜索（歌曲/歌手/专辑/类型）与批量下载、内置播放条（顺序/列表循环/单曲循环/随机）、我的音乐（导入/收藏/分类/筛选）、歌单收藏（含待下载项）、播放历史、批量音频格式转换 | ✅ |
| （可扩展） | 新增板块：`boards/registry.py` 注册一条即可，首页与顶栏自动出现 | — |

### 墨读音乐使用说明

- **音源**（可插拔，见 `core/music/sources.py`）：
  | 音源 | 说明 | 需要密钥 |
  |---|---|---|
  | 网易云音乐 | 公开 Web 接口检索，`outer` 直链下载非 VIP 曲目 | 否 |
  | iTunes 试听 | Apple 公开接口，30 秒试听片段 + 完整元数据 | 否 |
  | Jamendo | CC 授权完整曲目（自由音乐） | 是（免费 client_id，设置里填写或 `MODU_JAMENDO_CLIENT_ID`） |
  | 音频直链 | 粘贴 http(s) 音频直链直接下载 | 否 |
- **批量流程**：选择「歌手 / 类型」检索 → 结果列表勾选 → 一键「下载（单条或批量）」或先「加入歌单」（歌单内显示为「待下载」，之后可整单下载）。
  只选中一行时即下载该首；右键结果行还有「试听这首 / 下载这首 / 加入歌单」；
  试听按钮可再次点击停止。
- **收藏与分类**：曲目表「收藏」列可直接点击切换单曲收藏，右键菜单还可播放单曲 / 设置分类 / 加入歌单 / 移除 / 打开文件位置。
- **播放**：底部播放条常驻，切板块不中断；歌单页支持顺序 / 列表循环 / 单曲循环 / 随机。
  在线试听同样走底部播放条（可看进度/时长/音量），试听失败会在播放条与搜索页状态栏给出具体原因；
  播放失败的本地曲目会带曲名提示并自动跳到下一首；导入的本地文件在首次播放后会自动补全时长。
- **格式转换**：mp3 / m4a / wav / flac / aac / ogg / opus / wma 互转（依赖 ffmpeg，可用 `MODU_FFMPEG` 指定）。
- **合规**：抓取与下载仅限个人学习、试听与自有内容备份，界面默认勾选合规声明；请遵守各平台条款与版权要求。
  平台返回「版权受限/VIP」页面时会明确报错，不会把无效文件当成歌曲入库。

## 技术栈

- Python 3.10+ / PySide6(Qt6) 单应用
- 内嵌前端：QtWebEngine + QWebChannel（React 产物见 `src/modu_workbench/webfront/`，桥协议见 `services/bridge_shim.js`）
- 解析：ebooklib（EPUB）、python-docx、openpyxl/xlrd、Pillow、markdown/html2text、chardet、Pygments（预览高亮）
- 持久化：SQLite（stdlib sqlite3，`library.db` 书籍进度/历史、`music.db` 曲库/歌单/播放历史）
- 媒体：ffmpeg 子进程（`MODU_FFMPEG` 指定路径）；LibreOffice 可选（`MODU_SOFFICE`）；
  WebView 内 mp4 通过本地流服务（`services/media_server.py`，支持 Range）播放，失败时回退原生播放器
- 音乐播放：QtMultimedia（QMediaPlayer + QAudioOutput）；在线音源用 requests 抓取公开接口
- 测试：pytest（offscreen 无头 Qt，`MODU_DATA_DIR` 隔离数据）
- 打包：PyInstaller（`workbench.spec`，onedir）+ NSIS（`packaging/installer.nsi`）

## 开发运行

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple
python -m modu_workbench
python -m pytest tests -q
```

## 重建内嵌前端（仅在改动 `archive/electron-formatflow/` 界面时需要）

```powershell
pwsh -ExecutionPolicy Bypass -File packaging\build_webfront.ps1   # 首次会 npm install 到 .webfront-build\
# macOS / Linux: packaging/build_webfront.sh
```

脚本流程：安装渲染进程最小依赖 → 目录联接 `node_modules` → `vite build` →
`python -m modu_workbench.services.web_prepare`（清理旧产物、写入 `qtwebchannel.js`/`bridge_shim.js`、注入版本号）。
纯 Python 改动无需重建前端。

## 打包 Windows

```powershell
pip install pyinstaller
pyinstaller workbench.spec --noconfirm --clean
# 产物：dist\ModuWorkbench\ModuWorkbench.exe（随包依赖在同目录）

# 可选：生成 NSIS 安装包（需 makensis；脚本自动读取版本号）
pwsh -ExecutionPolicy Bypass -File packaging\build_installer.ps1
```

### 打包自检（含音频解码）

```powershell
$env:MODU_CHECK_DEPS = "$env:TEMP\modu-check.json"
.\dist\ModuWorkbench\ModuWorkbench.exe   # 检查完自动退出，结果写入该 json
Get-Content $env:MODU_CHECK_DEPS
```

共 11 项：markdown 渲染 / 高亮 / Pygments / WebEngine 导入与渲染 / webfront 产物 / QtMultimedia /
**真实音频解码播放**（播放一段静音 WAV 并确认播放位置前进）/ 音乐库建表读写。全部为 `true` 才算打包正常。
播放自检需要真实桌面与音频设备；设置 `QT_QPA_PLATFORM=offscreen` 时会跳过解码检查。

可选环境变量：`MODU_DATA_DIR`（数据目录，默认 `%APPDATA%\ModuWorkbench`）、`MODU_FFMPEG`、`MODU_SOFFICE`。
首次启动会自动迁移 win-e-book 旧库（`%APPDATA%\WinEBook\library.db`）。

## 目录结构

```
src/modu_workbench/
├── main.py / __main__.py      # 入口
├── app_shell.py               # 主壳：顶栏（首页/板块/设置）+ 页面栈
├── boards/                    # ★ 板块注册与页面
│   ├── registry.py / base.py  # 板块注册表（可扩展）
│   ├── home_board.py          # 首页：介绍 + 板块入口
│   ├── book_board.py          # 墨读书库
│   ├── book_shelf.py          # 书架
│   ├── book_reader.py         # 阅读器
│   ├── book_online.py         # 在线书库
│   ├── convert_board.py       # 墨读转换（Qt 回退实现）
│   ├── convert_web.py         # 墨读转换（内嵌前端 + QWebChannel）
│   ├── doc_viewer.py          # 文档查看/编辑器（Qt 回退实现）
│   ├── music_board.py         # 墨读音乐（板块外壳 + 底部播放条）
│   ├── music_search.py        # 墨读音乐：在线搜索与批量下载
│   ├── music_library_page.py  # 墨读音乐：我的音乐 + 格式转换
│   ├── music_playlist.py      # 墨读音乐：歌单收藏
│   ├── music_history.py       # 墨读音乐：播放历史
│   └── music_widgets.py       # 墨读音乐：表格/对话框/后台线程
├── core/reader/               # 书库引擎（迁移自 win-e-book）
│   ├── parser.py / storage.py / library.py / online.py
├── core/music/                # 音乐引擎
│   ├── models.py              # 曲目/在线结果/歌单/历史模型
│   ├── storage.py             # SQLite：曲库/歌单/待下载项/历史/设置
│   ├── sources.py             # 在线音源（网易云/iTunes/Jamendo/直链）
│   ├── downloader.py          # 流式下载（进度/取消/封面/歌词）
│   ├── library.py             # 本地曲库：导入/时长探测/转换入口
│   └── player.py              # 播放器：队列/循环/随机/进度/音量
├── core/convert/              # 转换引擎
│   ├── registry.py / engine.py / formats.py / text_io.py
│   ├── pdf_out.py / image_io.py / archive_io.py
│   ├── office_io.py / sheet_io.py / media_io.py
├── services/                  # 桥接与运行时服务
│   ├── web_bridge.py          # QWebChannel 桥（前端 RPC → Python 引擎）
│   ├── bridge_shim.js         # 注入前端的 window.formatFlow 兼容层
│   ├── web_prepare.py         # 整理 Vite 产物为 webfront
│   ├── media_server.py        # 本地媒体流（Range）
│   ├── media_player.py        # 原生播放器回退
│   └── file_scan.py           # 导入文件扫描（唯一实现）
├── webfront/                  # 内嵌前端产物（由 build_webfront 生成）
├── ui_kit/                    # 统一设计规范（theme/toast/组件/设置）
tests/                         # pytest（无头冒烟 + 单元）
workbench.spec                 # PyInstaller 配置（Windows）
workbench_mac.spec             # PyInstaller 配置（macOS，BUNDLE）
packaging/                     # NSIS 安装脚本 + 前端构建脚本
archive/electron-formatflow/   # 旧 Electron 版归档（前端源码 + 主进程参考）
docs/ARCHITECTURE.md           # 架构与合并方案
```

## 里程碑

| 阶段 | 内容 | 状态 |
|---|---|---|
| M0 | Python 骨架 / 首页 / 板块注册 / ui_kit 主题 | ✅ |
| M1 | 墨读书库（书库/阅读/在线书库，旧库迁移） | ✅ |
| M2 | 转换基础（文本/PDF(Qt)/图片/归档/查看编辑/任务队列） | ✅ |
| M3 | 转换高级（Word/表格/媒体，LibreOffice 高保真） | ✅ |
| M4 | 跨板块联动 / 设置 / PyInstaller 打包 / 推送 | ✅ |
| M5 | 内嵌 main 分支前端（QtWebEngine + QWebChannel）/ mp4 内联预览 / NSIS 安装包 | ✅ |
| M6 | 墨读音乐板块（在线搜索下载 / 播放器 / 歌单收藏分类 / 播放历史 / 音频格式转换） | ✅ |

## 协议

MIT，见 [LICENSE](LICENSE)。第三方依赖：PySide6(LGPL)、ebooklib(AGPL，仅本地阅读解析)、lxml(BSD)、bs4(MIT)、Pillow(MIT-C)、ffmpeg(GPL 构建，随部署另行声明) 等。
在线书源抓取仅限个人学习、试读与自有内容备份，默认需勾选合规声明。