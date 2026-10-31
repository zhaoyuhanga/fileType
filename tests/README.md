# 测试目录说明（tests/）

```
tests/
├── conftest.py            # 公共夹具：offscreen Qt + 隔离数据目录 + qapp
├── architecture/          # ★ 架构与规范闸门（设计被破坏时失败）
├── platform/              # core/platform：单库结构、旧库迁移、索引、本机代理
├── board_book/  board_convert/  board_document/  board_music/
│   board_video/  board_gallery/  board_llm/   # 各板块引擎（*_core）与界面（*_ui）
├── smoke/                 # 主壳导航、板块懒加载、跨板块联动
└── helpers/               # 测试辅助脚本（不参与收集）
```

运行：`python -m pytest tests -q`（详见 `docs/TESTING.md`）。
当前规模：**84 个文件 / 1138 个用例 / 约 2 分钟**。

## architecture/ —— 这些闸门在守什么

每一道都对应一次真实事故，改之前先看那段注释。

| 文件 | 守的东西 | 起因 |
|---|---|---|
| `test_architecture.py` | 板块之间、core 之间、层与层的依赖边界 | 防止板块互相引用导致"改 A 坏 B" |
| `test_no_import_cycles.py` | **模块级导入图无环**，且 `theme_icons` 先于 `theme` 导入必须成功 | v2.1.0 装完启动失败（`theme ↔ theme_icons` 循环导入，源码单测全绿） |
| `test_dynamic_imports_bundled.py` | 每个 **importlib 动态导入目标**都必须在两个 spec 的 hiddenimports 里 | v2.1.0 装完启动失败（`No module named 'modu_workbench.boards.book.board'`） |
| `test_startup_budget.py` | 主壳构造完成时：模块数 ≤60、不得导入板块模块 / 板块内核 / `requests` | v2.0.1 的"按需加载"其实没生效（启动 181/205 个模块） |
| `test_module_contracts.py` | 模块 `__all__` 可解析；门面模块的再导出不得被删 | 批量清理"未使用导入"时删掉了 `ui_kit.theme` 的令牌再导出 |
| `test_spec_integrity.py` | spec 的 datas 路径存在、hiddenimports 可解析、脚本只有一个 UTF-8 BOM | 重复 BOM 让 PowerShell 报 `Unexpected attribute 'CmdletBinding'` 这种指错方向的错误 |
| `test_version.py` | 版本单一来源（包 / pyproject / version_info / nsi） | 发版时漏改某一处 |
| `test_ui_design_system.py` | 令牌尺度、无直角、组件行为、首页栅格、副标题含全部板块 | 界面漂移 |
| `test_ui_page_conventions.py` | 任务条助手、只读属性、`task_bar` 参数约定 | 页面之间行为不一致 |
| `test_branding_assets.py` | 图标与安装包图形随包 | 打包漏资源 |

## platform/

- `test_db_schema.py`：单库 28 张表的结构快照（新增表必须在这里登记）；
- `test_db_indexes.py`：**用 `EXPLAIN QUERY PLAN` 钉住热点查询必须走索引**，
  并覆盖"从 v1.1.0 老库升级"与"缺表不能崩"两种情形；
- `test_media_proxy_urls.py`：本机代理的地址构造与 Range 响应。

## smoke/

- `test_shell_smoke.py`：主壳导航与首页栅格；
- `test_shell_lazy_pages.py`：**启动只构造首页、首次访问才建页、重复进入复用实例**；
- `test_board_entry_points.py` / `test_cross_board_link.py`：板块入口与跨板块联动
  （如"转换 → 加入书库"）。

## 写测试时的两条硬规矩

1. **不允许真的联网**。要网络就用 `http` 替身或 monkeypatch。
   历史上有三个用例在"等超时"，把套件从 1.8 分钟拖到 2.3 分钟
   （两个 HLS 用例 + 一个图库 AI 用例，见 CHANGELOG v2.2.0）。
2. **不允许依赖本机设置**。`QSettings` 读的是**真实注册表**（例如下载限速
   `video/download_speed_limit_mbps`、媒体代理配置），用例必须自己钉死：

   ```python
   monkeypatch.setattr(downloader_module, "_download_speed_limit_bps", lambda: 0.0)
   ```

   否则同一份代码在不同机器上会从 0.2 秒变成 10 秒。

## 新增测试

按上表放进对应板块目录；命名 `test_<模块>_<行为>.py`；
不要再新增 `test_m*`（里程碑命名已被 `smoke/` 取代）。
