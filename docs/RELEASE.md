# 发布规范（RELEASE）

> 版本单一来源：`src/modu_workbench/__init__.py` 的 `__version__`
> （`pyproject.toml`、`packaging/version_info.txt`、`workbench_mac.spec` 必须与之一致，
> 见 `tests/architecture/test_version.py`）。
> 安装包版本由 `packaging/build_installer.ps1` 从包内读取并通过 `/DAPP_VERSION` 传给 NSIS。
> 改版本号时同步改 `packaging/installer.nsi` 里的兜底 `APP_VERSION`（手动 makensis 时用）。
>
> makensis 的查找顺序：`-Makensis` 参数 → `PATH` → **electron-builder 缓存**
> （`%LOCALAPPDATA%\electron-builder\Cache\nsis\**\makensis.exe`，本机实测可用 v3.04，
> 因此不装 NSIS 也能出包）。

## 1. 发版前检查

```powershell
# 1) 全量测试（含架构闸门）
.venv\Scripts\python.exe -m pytest tests -q

# 2) 源码自检（18 项：文档渲染/JSON 高亮/词法器/单栈/PDF/音视频/播放链路（video_playback）/五个板块核心（乐库·影视·动漫·图库·文档）/PDF 打印渲染/ffmpeg/品牌）
$env:MODU_CHECK_DEPS="$env:TEMP\check.json"
.venv\Scripts\python.exe -m modu_workbench; type $env:TEMP\check.json

# 3) 界面截图（人眼过一遍排版，产物进 docs/ui/）
.venv\Scripts\python.exe packaging\ui_snapshot.py
```

## 2. 打包

```powershell
# onedir（默认：自动跑源码自检 + 打包后模块核验 + 打包产物自检）
powershell -ExecutionPolicy Bypass -File packaging\build_app.ps1

# 顺带生成 NSIS 安装包（需要 makensis）
powershell -ExecutionPolicy Bypass -File packaging\build_app.ps1 -Installer
```

产物：
- `dist\ModuWorkbench\ModuWorkbench.exe`（onedir，随包 ffmpeg 约 196MB，v1.0.2 目录合计约 382.5MB）
- `dist\墨软工作台-Setup-<版本>.exe`（NSIS 安装包，v1.0.2 约 151MB）
- 两个产物都带 Windows 版本资源：启动器由 `workbench.spec` 挂载 `packaging/version_info.txt`
  （PyInstaller `VSVersionInfo`），安装包由 `installer.nsi` 的 `VIProductVersion` + `VIAddVersionKey`
  注入；版本号仍来自 `__version__`（`tests/architecture/test_version.py` 守住一致性）。

打包脚本内置三道自检，任一处 `Fail` 都会直接中断：
1. 源码自检（`MODU_CHECK_DEPS`）；
2. exe 字节流里检索关键模块名（七个板块/平台层/共享媒体层，共 48 项）；
3. 运行打包产物做能力自检（18 项）。

## 3. 体积基线与精简记录

| 版本 | onedir 目录 | 安装包 | 说明 |
|---|---|---|---|
| v0.3.x | 731 MB | — | 含 QtWebEngine + 内嵌 React 前端 |
| v1.0.0 | **371 MB** | 150 MB | 移除 QtWebEngine（约 340MB）与 Qml/Quick/Pdf/虚拟键盘（约 18MB） |
| v1.0.2 | 382.5 MB | 151 MB | 控件样式补齐 + 在线曲目质量过滤；打包环境 Python 3.12.10 / PySide6 6.11.2 / PyInstaller 6.22.3 |
| v1.0.4 | 382.9 MB | 151.3 MB | 墨软转换格式扩到 68 种（+PyYAML 约 0.4MB） |
| v1.0.5 | **379.1 MB** | **152.1 MB** | 新增第六大板块（文档/PDF/OCR）+ 三批自查修复；**QtPdf 回归**（约 1.5MB）：文档板块打印 PDF 要逐页渲染，见 `app/main.py` 的 `pdf_print_engine` 自检；安装包实测静默安装→自检 16/16→静默卸载闭环 |
| v1.1.0 | **388.5 MB** | **154.7 MB** | 新增第七大板块（动漫：规则引擎/嗅探/弹幕/Bangumi）+ 影视播放下载链路重写（代理/ABR/并发分片下载/离线增强）；自检 16 → **18 项**；实测静默安装→自检 18/18→静默卸载闭环（见下） |

精简原则：只删除**已用二进制导入表核实无依赖**的 Qt 模块；
每次精简后必须重跑打包产物自检（`multimedia_playback` 会真实播放一段音频，是最有效的回归）。

## 4. 已知环境限制

- **Smart App Control**：Windows 11 开启该功能时，未签名 exe 会被拦（`An Application Control
  policy has blocked this file`）。此时需关闭 Smart App Control、或对 exe 签名、或改用源码运行。
  实测表现是**新写入的文件会被拦一段时间、之后自动放行**（同一份 `dist` 里的 exe，
  构建完立刻跑被拦，等约 20 秒后同样文件就能跑），所以打包后的自检偶发"跑不起来"
  不一定是构建有问题 —— 先等一会再试。
- **卸载前请先退出程序**：NSIS 卸载器删不掉**正在被占用**的文件。实测：
  应用还开着时卸载，`Uninstall.exe`、`ModuWorkbench.exe` 与 `_internal/` 会留在
  `%LOCALAPPDATA%\Programs\墨软工作台`（约 128MB），卸载器自身也被删掉，
  需先结束进程再手工删目录。注册表项、桌面/开始菜单快捷方式仍会被清干净；
  用户数据（`%APPDATA%\ModuWorkbench\modu.db`）不动 —— 这是刻意设计。
- **防火墙**：本机媒体流服务只监听 `127.0.0.1` 随机端口（用于 <video>/ffmpeg 取流），无需放行入站。

## 5. 数据与兼容

- 单库 `%APPDATA%\ModuWorkbench\modu.db`（`MODU_DATA_DIR` 可覆盖）；媒体在 `music/` `video/` `gallery/`。
- 从 v0.3.x 升级：首次启动自动导入旧分库（`library.db`/`music.db`/`video.db`/`gallery.db`/`llm.db`），
  旧文件改名为 `*.imported.bak`（不删除）；详见 `docs/DATABASE.md`。
- 备份 = 复制 `modu.db`（WAL 模式下连同 `-wal`/`-shm`，或先关闭应用）。

## 6. 提交与推送约定

- 每个阶段一次 commit，信息用 `type(scope): 说明`（如 `refactor(p4c): …`）；
- 推送走本机代理：`git -c http.proxy=http://127.0.0.1:7897 push origin main`；
- CHANGELOG 按版本分节，定版时把「进行中」小节合并为定版说明。

## 7. v1.1.0 打包实测（安装 → 自检 → 卸载闭环）

环境：Windows 11 26200 / Python 3.12.10 / PyInstaller 6.22.3 / PySide6 6.11 /
makensis（electron-builder 缓存里的 3.0.4.1，`winget install NSIS.NSIS` 装的 3.13 也可用）。

```
powershell -ExecutionPolicy Bypass -File packaging\build_app.ps1 -SkipDeps -Installer
```

| 步骤 | 结果 |
|---|---|
| 源码自检 | 18/18 通过 |
| PyInstaller onedir | `dist\ModuWorkbench\ModuWorkbench.exe`，目录 **388.5 MB** |
| exe 字节流模块核验 | **48 项全部命中** |
| 打包产物自检 | 18/18 通过（含 `video_core` / `anime_core` / `video_playback`） |
| NSIS | `dist\墨软工作台-Setup-1.1.0.exe`，**154.7 MB**，版本资源 1.1.0 |
| 静默安装 `/S` | 退出码 0；装到 `%LOCALAPPDATA%\Programs\墨软工作台`（**388.6 MB**，随包 `_internal\tools\ffmpeg\{ffmpeg,ffprobe}.exe` 均在）；注册表 DisplayVersion=1.1.0；桌面与开始菜单快捷方式均已创建 |
| 已安装产物自检 | 退出码 0，**18/18 通过** |
| 静默卸载 `/S` | 退出码 0；安装目录、卸载注册表项、桌面/开始菜单快捷方式、自有注册表键**全部清干净**；`%APPDATA%\ModuWorkbench\modu.db`（用户数据）**保留**（卸载不删用户数据） |

### 这一步顺带加固了一处"会误报通过"的自检

`ffmpeg_tools` 原先的逻辑是"定位不到 ffmpeg 也算通过"（把随包 ffmpeg 当可选依赖）。
问题在于：如果 `workbench.spec` 的 `datas` 写错、随包丢失，冻结运行时会**回退到系统 PATH**
找一个 ffmpeg，于是自检照样报 `true` —— 用户实际拿到的是"HLS 下载退化为 .ts、音视频转换不可用"。

现在改成：**冻结运行时**必须同时满足「`_MEIPASS/tools/ffmpeg` 存在」且「解析出的 ffmpeg 来自包内」，
否则判失败；源码运行仍按可选依赖处理。并做了反向验证：临时把已安装产物里的
`ffmpeg.exe` 改名后跑自检 → 退出码 1、`ffmpeg_tools=false`，恢复后立即回到 18/18。
