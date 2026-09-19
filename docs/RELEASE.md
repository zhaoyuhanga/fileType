# 发布规范（RELEASE，v1.0.2）

> 版本单一来源：`src/modu_workbench/__init__.py` 的 `__version__`
> （`pyproject.toml` 必须与之一致，见 `tests/architecture/test_version.py`）。
> 安装包版本由 `packaging/build_installer.ps1` 从包内读取并通过 `/DAPP_VERSION` 传给 NSIS。

## 1. 发版前检查

```powershell
# 1) 全量测试（含架构闸门）
.venv\Scripts\python.exe -m pytest tests -q

# 2) 源码自检（14 项：文档渲染/JSON 高亮/词法器/单栈/PDF/音视频/四个板块核心/ffmpeg/品牌）
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
2. exe 字节流里检索关键模块名（影视/图库/乐库/书库/转换/大模型/平台层，共 15 项）；
3. 运行打包产物做能力自检。

## 3. 体积基线与精简记录

| 版本 | onedir 目录 | 安装包 | 说明 |
|---|---|---|---|
| v0.3.x | 731 MB | — | 含 QtWebEngine + 内嵌 React 前端 |
| v1.0.0 | **371 MB** | 150 MB | 移除 QtWebEngine（约 340MB）与 Qml/Quick/Pdf/虚拟键盘（约 18MB） |
| v1.0.2 | 382.5 MB | 151 MB | 控件样式补齐 + 在线曲目质量过滤；打包环境 Python 3.12.10 / PySide6 6.11.2 / PyInstaller 6.22.3 |
| v1.0.4 | 382.9 MB | 151.3 MB | 墨软转换格式扩到 68 种（+PyYAML 约 0.4MB） |

精简原则：只删除**已用二进制导入表核实无依赖**的 Qt 模块；
每次精简后必须重跑打包产物自检（`multimedia_playback` 会真实播放一段音频，是最有效的回归）。

## 4. 已知环境限制

- **Smart App Control**：Windows 11 开启该功能时，未签名 exe 会被拦（`An Application Control
  policy has blocked this file`）。此时需关闭 Smart App Control、或对 exe 签名、或改用源码运行。
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
