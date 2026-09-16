# 测试规范（TESTING，v1.0.0）

> 目录约定见 `tests/README.md`；架构边界由 `tests/architecture/` 锁死；
> 所有 UI 测试离屏运行（`QT_QPA_PLATFORM=offscreen`），数据目录隔离（`MODU_DATA_DIR`）。

## 1. 分层

| 目录 | 内容 | 是否依赖 Qt |
|---|---|---|
| `tests/architecture/` | 架构与规范闸门：依赖边界、页面约定、版本单一来源、品牌资产 | 否／少量 |
| `tests/platform/` | `core/platform`：数据库结构快照、旧库迁移、本机流服务 | 否 |
| `tests/board_<板块>/` | 板块引擎 + 板块界面（`*_core` / `*_ui`） | 是（UI 部分） |
| `tests/smoke/` | 端到端冒烟：主壳与导航、跨板块联动 | 是 |
| `tests/helpers/` | 测试辅助脚本（如 AES 参考实现），不参与用例收集 | 否 |

## 2. 三层测试职责

1. **单元（core）**：纯逻辑与存储，不碰网络与界面。网络用 `FakeHttp` 替身（见 `board_video/test_video_core.py`）。
2. **界面（ui）**：只验证"控件状态与信号"，不断言像素；需要 Qt Multimedia 的用例允许静默模式。
3. **架构（architecture）**：把"不许做"的规则写成断言——这些测试失败意味着**设计被破坏**，不是代码写错了。

## 3. 写测试的硬性约定

- 每个用例只验证一件事，名字写清"行为"：`test_cms_play_url_resolves_share_page`；
- 需要应用数据目录的用例**必须**用 `tmp_path` 或 `MODU_DATA_DIR`，绝不能写用户 APPDATA；
- UI 用例用 `qapp` 夹具（`tests/conftest.py`），不要自己 new `QApplication`；
- 断言错误信息要能自解释（例如"缺少 xx：请检查 yyy"）；
- 页面里的状态/进度统一走 `TaskBar`，测试断言文字用 `page._status.text()`（兼容属性）。

## 4. 常用命令

```powershell
# 全量（约 1~2 分钟）
.venv\Scripts\python.exe -m pytest tests -q

# 只跑某板块
.venv\Scripts\python.exe -m pytest tests/board_video -q

# 架构与规范闸门
.venv\Scripts\python.exe -m pytest tests/architecture -q

# 打包前自检（源码）与打包后自检（产物）
$env:MODU_CHECK_DEPS="$env:TEMP\check.json"; .venv\Scripts\python.exe -m modu_workbench
```

## 5. 每板块冒烟清单（发版前手动过一遍）

| 板块 | 冒烟路径 |
|---|---|
| 墨软书库 | 导入 TXT/EPUB → 书架出现 → 阅读 → 退出再进恢复进度 → 在线书库合规开关 |
| 墨软转换 | 添加文件 → 选中动作 → 开始转换 → 输出目录有产物 → 双击查看/编辑并保存 |
| 墨软乐库 | 搜索 → 试听（走播放条）→ 下载 → 我的音乐可见 → 加入歌单 → 歌单播放 |
| 墨软影视 | 源设置「测试全部」→ 搜索 → 选集播放 → 下载一集 → 我的视频/历史可见 |
| 墨软图库 | 导入图片 → 缩略图生成 → 分类/收藏 → 编辑美化保存 → AI 优化（本地算法） |
| 通用 | 设置对话框五个分区可保存；切换板块无卡顿；关闭应用不残留后台线程 |
