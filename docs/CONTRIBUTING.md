# 开发指南（CONTRIBUTING）

面向**要改这个项目的人**。只想运行/安装请看 `README.md`；架构规则看 `docs/ARCHITECTURE.md`；
发版流程看 `docs/RELEASE.md`。

## 1. 环境

```powershell
# Python 3.12（本项目实测 3.12.10）
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"

# 跑起来（源码）
$env:PYTHONPATH="D:\work\fileType\src"
.venv\Scripts\python.exe -m modu_workbench

# 跑测试（约 1.8 分钟；无头，不需要显示器）
.venv\Scripts\python.exe -m pytest tests -q
```

**ffmpeg**：转换/视频下载/媒体探测要用。仓库不提交二进制，
放到 `tools/ffmpeg/ffmpeg.exe` 与 `tools/ffmpeg/ffprobe.exe` 即可（该目录已被 `.gitignore` 忽略）。
没有 ffmpeg 时大部分功能仍然可用，只是会走"退化路径"（见各板块文档的「诚实边界」）。

**不要在测试里依赖你的个人设置**：`QSettings` 读写的是真实注册表，
`MODU_DATA_DIR` 只隔离数据目录。用例里要显式钉死相关输入（见 `tests/README.md`）。

## 2. 五个常用环境变量

| 变量 | 作用 | 什么时候用 |
|---|---|---|
| `MODU_DATA_DIR` | 覆盖数据目录（默认 `%APPDATA%\ModuWorkbench`） | 测试隔离、多套配置并存 |
| `MODU_CHECK_DEPS=<json路径>` | 跑 17 项**打包自检**后退出，不建窗口 | 打包验证（`build_app.ps1` 自动用） |
| `MODU_SMOKE_BOARDS=<json路径>` | 建主壳 + **逐个打开六个板块**后退出 | 打包验证的"启动级"冒烟 |
| `MODU_LOG_LEVEL=debug` | 把"被有意忽略的失败"也打出来（`core/platform/logging.py`） | 排查"什么都没发生"的怪问题 |
| `MODU_MEDIA_ALLOW_PRIVATE=1` | 允许媒体代理访问环回/私网（默认拒绝，SSRF 收口） | 本地 fixture 测试 |

## 3. 加一个板块：按这个顺序做，别跳步

1. 建包 `boards/<key>/`（入口固定 `board.py`，页面类继承 `QWidget`）；
2. 建内核 `core/<key>/`（业务逻辑；**不许** import 其他板块的 core）；
3. 在 `app/registry.py` 加一条 `BoardSpec`，`page_target="modu_workbench.boards.<key>.board:XxxBoardPage"`；
4. 在 `boards/<key>/context.py` 暴露本板块单例，并把它登记进 `app/context.py` 的 `_LAZY`；
5. **两个 spec 的 `hiddenimports` 都要加**：`boards.<key>.board`、`boards.<key>.context`
   （漏了就是"装完启动失败"，`test_dynamic_imports_bundled.py` 会当场失败）；
6. 在 `ui_kit/settings/` 加设置页并登记进 `PAGE_FACTORIES`；
7. 补 `docs/BOARDS/<key>.md`，更新 `README.md` 的板块表与副标题
   （`test_ui_design_system.py` 会检查副标题是否覆盖全部板块）；
8. 补测试目录 `tests/board_<key>/`。

## 4. 提交前必须过的三道关

```powershell
# 1) 全量测试（含全部架构闸门）
.venv\Scripts\python.exe -m pytest tests -q

# 2) 源码自检
$env:MODU_CHECK_DEPS="$env:TEMP\check.json"; .venv\Scripts\python.exe -m modu_workbench

# 3) 只有改了打包相关才需要：完整打包（含板块冒烟 + GUI 冒烟）
powershell -ExecutionPolicy Bypass -File packaging\build_app.ps1 -Installer
```

**改了打包/依赖/新模块 → 必须跑第 3 步。** 这个项目已经两次栽在
"单测全绿、装完起不来"上（循环导入、动态导入没进包），那两次都是构建闸门才抓到的。

## 5. 代码约定

- **注释写"为什么"，不写"是什么"**。特别是反直觉的取舍（例如
  `services/app_context.py` 为什么不能删、`v4_indexes` 为什么不能改成部分索引）——
  这些都是踩过坑之后写下的，删之前先看注释里的数字；
- **失败可以忽略的位置必须留痕**：

  ```python
  from modu_workbench.core.platform.logging import swallow

  try:
      storage.save_position(book_id, index)
  except sqlite3.Error as error:          # 只吞得起的类型
      swallow("保存阅读进度", error)       # 但必须留痕
  ```

  全仓已无 `except Exception: pass`（有闸门盯着观感，靠 code review 守）；
- **不要为了让测试变绿而放宽断言**。这个项目的闸门基本都是"事故遗迹"，
  放宽 = 把事故请回来；
- **新增依赖要极其克制**：目前 18 个运行时依赖，都是"不做就得自己写 500 行"的那种
  （例如 AES 解密刻意用纯 Python 实现，就是为了不给用户增加依赖）。

## 6. 三条最容易踩的坑

1. **写了 `except Exception: pass`** → 静默失败，用户和你都看不到。用 `swallow()`。
2. **用正则批量改导入** → 会误删同名行里的其他名字（`from PySide6.QtCore import Qt, Signal`
   整行消失 → 启动 `NameError`）。批量改完**必须跑全量测试**。
3. **改 PowerShell 脚本的编码** → 用"读文本 + 带 BOM 写回"会把 U+FEFF 写成正文，
   PowerShell 报的却是 `Unexpected attribute 'CmdletBinding'` 这种指错方向的错误。
   正确做法：写回前 `while (t[0] -eq [char]0xFEFF) { t = t.Substring(1) }`。
   `test_spec_integrity.py` 会检查"只有一个 BOM"。

## 7. 目录速查

| 想改什么 | 去哪 |
|---|---|
| 启动流程 | `app/main.py`（59 行）+ `app/shell.py` |
| 板块清单 / 顺序 | `app/registry.py` |
| 打包自检 / 启动冒烟 | `app/selfcheck.py` |
| 数据表 / 迁移 | `core/platform/migrations/`（**已发布的不许改**，只能加新的） |
| 主题配色 | `ui_kit/tokens.py`（唯一来源） |
| 控件样式 | `ui_kit/theme.py` |
| 设计令牌与组件 | `ui_kit/components/` |
| 媒体代理 | `services/media_proxy/`（fetch / handler / server / playlist / cache / upstream） |
| 打包 | `packaging/build_app.ps1`、`workbench.spec`、`packaging/installer.nsi` |
