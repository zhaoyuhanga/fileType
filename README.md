# 墨读·工作台 (Modu Workbench)

> 本地离线的一站式阅读与转换工作台 —— 板块化设计，能力可扩展。

墨读·工作台由两个历史项目合并演进而来（架构决策见 docs/ARCHITECTURE.md）：

- **墨读书库**：承接 win-e-book（墨读）—— TXT/EPUB 本地书库、章节解析、进度记忆、在线书库下载（个人学习用途，默认开启）。
- **墨读转换**：承接 fileType —— 六类本地离线转换（文档 / 表格 / 图片 / 媒体 / 归档）与 txt/md/json/mp4 查看编辑。

旧 Electron/React 工程已归档至 archive/electron-formatflow（仅作参考，不再维护/构建）。

## 当前状态（M0：应用骨架）

- ✅ 全新 Python/PySide6 单应用骨架（`python -m modu_workbench` 启动）
- ✅ 首页：产品介绍 + 可扩展板块入口（墨读书库 / 墨读转换）
- ✅ 板块注册机制（boards/registry.py），后续板块自动出现在首页与顶栏
- ✅ 统一设计规范 ui_kit（ThemeTokens 令牌 + 全局 QSS + 基础组件）
- ⏳ 墨读书库（M1）、墨读转换（M2/M3）、打包分发（M4）按里程碑推进中

## 环境与运行

要求 **Python 3.10+**（已在 3.14 上验证）。

```powershell
# 1) 创建虚拟环境
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2) 安装（项目 + 依赖）
pip install -e .          # 国内网络可加 -i https://pypi.tuna.tsinghua.edu.cn/simple

# 3) 启动
python -m modu_workbench

# 4) 测试
pip install pytest
python -m pytest tests -v
```

## 里程碑

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M0 | Python 骨架、首页、板块注册、统一主题 | ✅ |
| M1 | 墨读书库（win-e-book 能力迁移，含在线书库） | ⏳ |
| M2 | 墨读转换基础：文本 / PDF(Qt) / 图片 / 归档 / 查看编辑 / 任务队列 | ⏳ |
| M3 | 墨读转换高级：docx/doc、xlsx/xls、ffmpeg 媒体、Word/Excel→PDF | ⏳ |
| M4 | 跨板块联动、设置、PyInstaller 打包、发布 | ⏳ |

## 协议

内部项目，仅供学习使用。第三方依赖声明随里程碑补充。