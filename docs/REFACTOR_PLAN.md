# 墨软·工作台 v1.0.0 重构方案（评审稿）

> 目标：技术栈统一、模块最小化且互不影响、五大板块目录分明、数据库结构合理可审批、
> 界面统一重设计、测试与文档规范齐全，全部验证通过后定版 **v1.0.0**。
> 本文是执行前的评审稿：`[待审批]` 项需要确认后再动代码。

## 1. 现状实测（本次统计）

| 区域 | 规模 | 现状问题 |
|---|---|---|
| `core/` | 65 文件 / 14 701 行 | 板块核心混在一起，存在跨板块依赖 |
| `boards/` | 29 文件 / 11 320 行 | 五大板块的界面文件**平铺**在同一目录，前缀区分（`video_*`/`music_*`…） |
| `ui_kit/` | 13 文件 / 2 172 行 | 主题/控件无间距·圆角·留白规范，各板块各写各的 |
| `services/` | 10 文件 / 1 294 行 | `app_context.py` 是全局单例集合（5 个板块 + 大模型全塞一起） |
| `main.py` | 591 行 | 依赖自检（`MODU_CHECK_DEPS`）与入口混在一起 |
| `tests/` | 25 文件 / 6 700 行 | 命名不统一（`test_m0_smoke`/`test_m4_link`），无测试规范文档 |
| `docs/` | 1 文件 / 176 行 | 仅 `ARCHITECTURE.md`，无数据库/UI/测试/发布文档 |
| 前端 | Qt(PySide6) × 5 板块 + 内嵌 **React**(Vite，源码在 `archive/electron-formatflow`) × 1.5 板块 | **两套前端技术**：转换板块与文档预览走 QtWebEngine + React |

### 关键耦合（"互不影响"的主要障碍）

1. `core/video/library.py` → `core/music/library.py`（借 `probe_duration_ms`）——**影视依赖乐库**。
2. `core/music/library.py`、`core/video/library.py` → `core/convert/media_io.py`（借 ffmpeg 定位）。
3. `services/app_context.py` → 一次性 import 全部板块 core，任一板块改动都会牵动它。
4. `ui_kit/settings/*` → 直接依赖各板块 core（设置页与板块实现绑死）。
5. 五个 SQLite 库（`library.db`/`music.db`/`video.db`/`gallery.db`/`llm.db`）各自
   重复实现 `history`、`playlists`、`settings`，**没有 schema 版本与迁移机制**
   （只有 `CREATE TABLE IF NOT EXISTS`，字段变更无法升级老数据）。

## 2. 目标架构（目录即边界）

```
src/modu_workbench/
├── app/                     # 应用骨架：入口、主壳、板块注册、依赖自检
│   ├── main.py  shell.py  registry.py  selfcheck.py
├── boards/                  # ★ 五大板块各一个包，互不引用
│   ├── base.py              # BoardSpec / BoardPage 基类 + 板块内公共约定
│   ├── home/                # 首页（板块入口）
│   ├── book/                # 墨软书库：board.py pages/ widgets/ settings.py
│   ├── convert/             # 墨软转换
│   ├── music/               # 墨软乐库
│   ├── video/               # 墨软影视
│   └── gallery/             # 墨软图库
├── core/                    # 业务内核：同样按板块分包
│   ├── platform/            # ★ 板块无关的公共能力（唯一允许被各板块依赖）
│   │   ├── db.py            #   SQLite 连接/事务/迁移执行器
│   │   ├── paths.py         #   数据目录与文件布局
│   │   ├── settings.py      #   统一设置读写（app_settings）
│   │   ├── media_tools.py   #   ffmpeg/ffprobe 定位（从 core/convert 抽出）
│   │   ├── http.py          #   统一 HTTP 客户端（重试/UA/错误翻译）
│   │   └── text.py          #   文件名清洗/时长格式化等纯函数
│   ├── book/  convert/  music/  video/  gallery/  llm/
├── ui_kit/                  # 设计系统（主题令牌 + 通用组件），不含业务
│   ├── tokens.py theme.py
│   ├── components/          #   PageHeader/Card/EmptyState/Toolbar/DataTable/…
│   └── settings/            #   设置外壳（各板块在 board 包内提供设置页）
└── data/                    # 内嵌静态资源（图标/示例）
```

依赖规则（用测试强制）：

```
boards/*        → core/<self> → core/platform        （同板块内可互相引用）
boards/*        ✗→ boards/<other>、core/<other>
core/<board>    ✗→ core/<other>（只能经 core/platform 或 core/llm 这类公共包）
ui_kit          ✗→ boards/*、core/<board>
```

## 3. 数据库方案 `[待审批]`

**提案：单一 `modu.db` + 板块前缀分表 + 版本化迁移 + 旧库自动导入。**

- 位置：`%APPDATA%\ModuWorkbench\modu.db`（WAL 模式；`MODU_DATA_DIR` 仍可覆盖）。
- 版本表：`schema_migrations(version INTEGER PRIMARY KEY, applied_at INTEGER, note TEXT)`。
- 公共设置：`app_settings(key TEXT PRIMARY KEY, value TEXT, updated_at INTEGER)`，
  key 形如 `video/sources/enabled`、`gallery/thumb_size`（分板块命名空间）。
- 板块表统一前缀：`book_*`、`music_*`、`video_*`、`gallery_*`、`llm_*`。
- 统一约定：
  - 主键 `id INTEGER PRIMARY KEY AUTOINCREMENT`；
  - 时间戳统一 `INTEGER`（epoch 秒），字段名统一 `created_at` / `updated_at` / `*_at`；
  - 外键 `REFERENCES x(id) ON DELETE CASCADE`，并 `PRAGMA foreign_keys=ON`；
  - 去重唯一键写进约束：`video_videos(source, remote_id, episode_index)`、
    `gallery_images(sha256)`、`music_tracks(file_path)` 等（现在是代码里 SELECT 判重）。
- 迁移：`core/platform/migrations/v1_initial.py` … 每个版本一个模块，`db.migrate()` 顺序执行；
  首次运行若发现旧库（`library.db`/`music.db`/`video.db`/`gallery.db`/`llm.db`）→ 导入数据后把旧文件改名 `.imported.bak`（不删除）。
- 验收：`tests/test_db_schema.py` 断言完整 DDL 快照 + 每个迁移可重复执行（幂等）；
  `tests/test_db_migration.py` 用旧库样本做一次真实迁移。

> 若你更倾向"保持五个库"，则退化为：保留物理分库 + 统一 `core/platform/db.py` 仓储基类与
> 迁移执行器（同样能解决"无版本、无迁移、重复实现"的问题），但跨板块统计与备份会复杂一些。

## 4. 界面重设计（去棱角 / 去空荡）`[待审批]`

- **令牌统一**（`ui_kit/tokens.py`）：
  - 间距刻度 `4/8/12/16/24/32/48`，页面外边距 `24`，卡片内边距 `16`，控件间距 `8/12`；
  - 圆角：卡片 `14`、输入/按钮 `10`、弹层 `18`（**最小 10，全局无直角**）；
  - 字号 `12/13/14/16/20/28`，行高 1.6；圆角头像/封面统一。
  - 颜色仅用语义角色（`bg/surface/surfaceAlt/border/text/textMuted/accent/success/warn/danger`），
    浅色为主 + 深色主题，两套都走同一令牌。
- **布局规则**（写进 `ui_kit/components/layout.py`）：
  - 每页统一 `PageHeader(标题 + 副标题 + 右侧操作)`；
  - 内容区按 12 栅格，最小卡片宽度 220，卡片等高对齐，禁止"标题一行、内容半屏空白"；
  - 列表页必须有 `EmptyState(图标 + 说明 + 主操作)`，禁止大片空白；
  - 表格/网格统一 `DataTable`（列宽策略、行高 36、hover/选中态一致）。
- **组件库**（`ui_kit/components/`）：`PageHeader`、`Card`、`StatChip`、`Toolbar`、`DataTable`、
  `EmptyState`、`SidebarNav`、`PlayerBar`、`DialogFrame`、`Toast`、`ProgressRing`。
- **验收**：每个板块的每个页面用 offscreen 渲染截图（`QWidget.grab()`）产出
  `docs/ui/board-*.png`，人眼过一遍"无直角、无过大留白、信息密度合理"。

## 5. 前端技术统一 `[待审批]`

现状是"Qt + 内嵌 React（转换板块、文档预览）"两套。QtWebEngine 体积约 200MB，
且其内置 Chromium **不含 H.264/AAC**（影视板块注释里已实测），因此不建议把全部界面改 Web。

- **方案 A（推荐）**：统一到 **Qt/PySide6 单栈** —— 转换板块与文档预览改为原生控件
  （预览用 `QTextBrowser`/`QTextDocument` 渲染 Markdown/HTML/JSON，媒体用 `QtMultimedia`），
  移除 `webfront/`、`web_bridge.py`、`web_prepare.py`、QtWebEngine 依赖；
  体积显著下降，前后端边界清晰（Python 即后端）。
- **方案 B**：保留 React 内嵌，但把前端源码从 `archive/` 收进仓库正规目录
  （`frontend/`，独立 `package.json` + 构建脚本），只统一规范与构建流程。
- **方案 C**：全部 Web（不推荐）：影视/音乐无法解码 H.264/AAC，播放体验会退化。

## 6. 测试与文档规范

- 测试：`tests/` 按板块分目录（`tests/board_video/`、`tests/core_platform/`…），
  命名 `test_<模块>_<行为>.py`；补 `tests/README.md`（分层：单元/集成/UI 冒烟，
  统一 offscreen + `MODU_DATA_DIR` 隔离 + 每个板块的"冒烟清单"）。
- 文档：`docs/ARCHITECTURE.md`（依赖规则 + 目录）、`docs/DATABASE.md`（ER + 字段字典）、
  `docs/UI_GUIDE.md`（令牌/组件/页面模板）、`docs/TESTING.md`、`docs/RELEASE.md`、
  `docs/BOARDS/<board>.md`（每板块功能/数据/接口）。CHANGELOG 保持单一版本来源。

## 7. 阶段计划与验收

| 阶段 | 内容 | 验收 |
|---|---|---|
| P1 | 目录重构：`app/`、`boards/<板块>/`、`core/platform/` | 全量 pytest 通过；依赖规则测试通过 |
| P2 | 解耦：ffmpeg 定位、时长探测、HTTP、设置抽到 `core/platform/`；`app_context` 拆分为板块各自 context | 无跨板块 import；全量测试通过 |
| P3 | 数据库：单库 + 迁移 + 旧库导入 + 仓储层统一 | `test_db_schema`/`test_db_migration` 通过；旧数据可读 |
| P4 | UI 重设计：令牌 + 组件库 + 五板块页面重构 | 截图验收 + UI 测试通过 |
| P5 | 前端统一（方案 A/B） | 转换/文档预览功能对照旧版逐项通过 |
| P6 | 测试与文档补齐 | 覆盖率报告、`docs/*` 齐全、每板块冒烟清单 |
| P7 | 定版 v1.0.0 | 全量测试 + 打包自检 + 安装包（如需） |

每阶段结束都会：跑全量测试 → 更新 CHANGELOG → 提交一次 commit（可回滚）。

## 8. 风险

- 大范围移动文件会丢失 git blame 连续性（用 `git mv` 保留历史）。
- 数据库合并属破坏性变更：迁移脚本 + 旧文件备份双保险，先在本机真实数据上演练。
- UI 重设计涉及 5 板块 × 多页面，逐板块推进、每板块独立验收，避免"一次性大爆炸"。
