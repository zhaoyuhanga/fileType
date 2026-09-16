# 更新日志（CHANGELOG）

本项目遵循语义化版本；版本号单一来源为 `src/modu_workbench/__init__.py` 的 `__version__`
（与 `pyproject.toml`、内嵌前端注入版本保持一致，见 `tests/test_web_bridge.py::test_version_single_source`）。

## v0.3.0 — 墨软·工作台 定版

首个三板块齐备、可离线分发的定版：**墨软书库 / 墨软转换 / 墨软乐库**。

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

- 显示名称统一为「墨软」系列：墨软·工作台 / 墨软书库 / 墨软转换 / 墨软乐库；
  内嵌前端不再使用「万能格式转换器」。
- 内部标识保持不变以兼容既有数据：Python 包 `modu_workbench`、`ModuWorkbench.exe`、
  `MODU_*` 环境变量、数据目录 `%APPDATA%\ModuWorkbench`（`library.db` / `music.db`）。

### 发布产物与校验

- `dist\ModuWorkbench\ModuWorkbench.exe`（onedir，随包依赖，解压后约 507 MB）
  SHA256：`AFA1AC4B4C20A8260511E4A4007CF0EDE07B8D92B40FFA518DFDB0BC4C91424F`
- `dist\墨软工作台-Setup-0.3.0.exe`（NSIS 安装包，199.5 MB）
  SHA256：`2938993A14B903A68C5280CB6F3391147229D5B9CBF8578E70B7EAE3D6285ED2`

```powershell
# 校验下载到的安装包
Get-FileHash '.\墨软工作台-Setup-0.3.0.exe' -Algorithm SHA256

# 打包自检（12 项，含真实音频解码播放与品牌图标）
$env:MODU_CHECK_DEPS = "$env:TEMP\modu-check.json"
.\dist\ModuWorkbench\ModuWorkbench.exe
Get-Content $env:MODU_CHECK_DEPS
```

### 已知限制

- 音频格式转换需系统安装 ffmpeg（或用 `MODU_FFMPEG` 指定）；未随包分发。
- Word/Excel → PDF 高保真需 LibreOffice（`MODU_SOFFICE`），否则使用内置兜底排版。
- 网易云 VIP / 下架曲目、iTunes 仅 30 秒片段、Jamendo 需免费 client_id。
- 在线音源仅供个人学习、试听与自有内容备份，请遵守各平台条款与版权要求。
