# 墨读·工作台 (Modu Workbench)

> 本地离线的一站式阅读与转换工作台 —— 板块化设计，能力可扩展。

由两个历史项目合并演进而来：

- **墨读书库**：承接 win-e-book —— TXT/EPUB 本地书库、章节自动解析、进度记忆、阅读主题、在线书库下载（个人学习用途，默认开启）。
- **墨读转换**：承接 fileType —— 六类本地离线转换（文档 / 表格 / 图片 / 媒体 / 归档）与 txt/md/json/mp4 查看编辑。

旧 Electron/React 工程归档于 `archive/electron-formatflow/`（仅作参考，不再构建维护）。

## 板块

启动进入首页，卡片/顶栏切换板块：

| 板块 | 内容 | 状态 |
|---|---|---|
| 墨读书库 | 书架（导入/搜索/卡片/历史）、阅读器（TOC/主题/字号/进度记忆/快捷键）、在线书库（00shu 整本直链下载 + 合规开关） | ✅ |
| 墨读转换 | 文本互转/PDF(Qt)/图片/Word/表格/归档/媒体，查看编辑与 JSON 美化、批量任务（进度/取消/输出防覆盖/解压防穿越） | ✅ |
| （可扩展） | 新增板块：`boards/registry.py` 注册一条即可，首页与顶栏自动出现 | — |

## 技术栈

- Python 3.10+ / PySide6(Qt6) 单应用
- 解析：ebooklib（EPUB）、python-docx、openpyxl/xlrd、Pillow、markdown/html2text、chardet
- 持久化：SQLite（stdlib sqlite3，`books/history` 进度与历史）
- 媒体：ffmpeg 子进程（`MODU_FFMPEG` 指定路径）；LibreOffice 可选（`MODU_SOFFICE`）
- 测试：pytest（offscreen 无头 Qt，`MODU_DATA_DIR` 隔离数据）
- 打包：PyInstaller（`workbench.spec`，onedir）

## 开发运行

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple
python -m modu_workbench
python -m pytest tests -q
```

## 打包 Windows

```powershell
pip install pyinstaller
pyinstaller workbench.spec --noconfirm --clean
# 产物：dist\ModuWorkbench\ModuWorkbench.exe（随包依赖在同目录）
```

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
│   ├── convert_board.py       # 墨读转换（批量任务）
│   └── doc_viewer.py          # 文档查看/编辑器
├── core/reader/               # 书库引擎（迁移自 win-e-book）
│   ├── parser.py / storage.py / library.py / online.py
├── core/convert/              # 转换引擎
│   ├── registry.py / engine.py / formats.py / text_io.py
│   ├── pdf_out.py / image_io.py / archive_io.py
│   ├── office_io.py / sheet_io.py / media_io.py
├── services/                  # 配置 / 共享书库上下文
├── ui_kit/                    # 统一设计规范（theme/toast/组件/设置）
tests/                         # pytest（无头冒烟 + 单元）
workbench.spec                 # PyInstaller 配置
archive/electron-formatflow/   # 旧 Electron 版归档（参考）
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

## 协议

MIT，见 [LICENSE](LICENSE)。第三方依赖：PySide6(LGPL)、ebooklib(AGPL，仅本地阅读解析)、lxml(BSD)、bs4(MIT)、Pillow(MIT-C)、ffmpeg(GPL 构建，随部署另行声明) 等。
在线书源抓取仅限个人学习、试读与自有内容备份，默认需勾选合规声明。