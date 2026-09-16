# 测试目录说明（tests/）

```
tests/
├── conftest.py            # 公共夹具：offscreen Qt + 隔离数据目录 + qapp
├── architecture/          # 架构与规范闸门（设计被破坏时失败）
│   ├── test_architecture.py          # 板块/核心层依赖边界（AST 解析 import）
│   ├── test_ui_page_conventions.py   # 任务条助手、只读属性、task_bar 参数约定
│   ├── test_ui_design_system.py      # 令牌尺度、无直角、组件行为、首页栅格
│   ├── test_version.py               # 版本单一来源（包 / pyproject / 安装包）
│   └── test_branding_assets.py       # 图标与安装包图形随包
├── platform/              # core/platform：单库结构快照、旧库迁移、本机流服务
├── board_book/  board_convert/  board_music/  board_video/  board_gallery/  board_llm/
│                          # 各板块引擎（*_core）与界面（*_ui）
├── smoke/                 # 主壳导航、跨板块联动
└── helpers/               # 测试辅助脚本（不参与收集）
```

运行：`python -m pytest tests -q`（详见 `docs/TESTING.md`）。

> 新增测试时：按上表放进对应板块目录；命名 `test_<模块>_<行为>.py`；
> 不要再新增 `test_m*`（里程碑命名已被 `smoke/` 取代）。
