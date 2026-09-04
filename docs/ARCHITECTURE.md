# 墨读·工作台 —— 架构与合并方案

本文记录「fileType（格式转换）」与「win-e-book（墨读书库）」合并为单一 **Python/PySide6** 应用的架构决策与落地方式。

## 背景与决策

- 产品名：**墨读·工作台**；形态：**全 Python 单应用**（PySide6/Qt6），单 exe 分发；
- 仓库：沿用 fileType 仓库演进（历史保留），旧 Electron/React 代码归档于 `archive/electron-formatflow/`；
- 板块化：首页 + 可扩展板块入口，当前为「墨读书库」「墨读转换」，未来可继续追加；
- 后端主技术：**Python**（无 Node/Python 双运行时）；SQLite 用 Python 标准库 `sqlite3`。

## 顶层结构

- `src/modu_workbench/boards/`：板块注册表（BoardSpec + 页面类），新增板块只需注册一条。
- `src/modu_workbench/ui_kit/`：统一设计令牌（ThemeTokens）+ QSS + 基础组件，两板块共用视觉规范。
- `src/modu_workbench/core/reader/`：墨读书库引擎（自 win-e-book 迁移：parser/storage/library/online）。
- `src/modu_workbench/core/convert/`：墨读转换引擎（自 fileType 行为重写：registry、text/pdf/image/media/archive 等）。
- `src/modu_workbench/services/`：文档读写、导入扫描等本地服务。
- `tests/`：pytest 单元与无头冒烟测试。

## 行为对齐清单（fileType → Python）

- 批量任务队列：实时进度 / 取消 / 状态本地化 / Toast 统一提示；
- 输出安全：同名输出自动加序号、归档解压路径穿越防护；
- 文档查看编辑：预览只读、编辑、保存、另存为、JSON 美化、未保存确认；
- 编码自适应：UTF-8 / UTF-16 BOM / GBK(GB18030)；
- 转换等价：文本互转、PDF(Qt 输出)、图片互转(Pillow)、表格(openpyxl/xlrd)、
  音视频(ffmpeg 子进程)、Word(docx)、ZIP/TAR/RAR、mp4 预览(QMediaPlayer)。

## 里程碑

- M0 骨架（首页/板块注册/统一主题/冒烟测试）✅
- M1 墨读书库（书库/章节解析/进度记忆/在线书库）
- M2 墨读转换基础（文本/PDF/图片/归档/查看编辑/任务队列）
- M3 墨读转换高级（docx/xls/ffmpeg 媒体/Word·Excel→PDF）
- M4 整合打包（跨板块联动/设置/PyInstaller/发布）

## 合规要点

- ebooklib(AGPL-3.0) 仅用于本地阅读解析，无网络服务分发场景，保留依赖与声明；
- ffmpeg 静态二进制为 GPL 构建，内部/个人使用，对外分发需替换或声明；
- 在线书源（00shu 等）默认开启，提供一键关闭 + “个人学习用途”声明。
