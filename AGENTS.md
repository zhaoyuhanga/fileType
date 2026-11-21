# 项目规则（供 AI 编码助手读取）

> 本文件是**长期生效的项目底线**，不是任务说明书。本次任务要改什么、改到什么程度，
> 写在对话里（模板见 `docs/AI_OPTIMIZATION_PLAYBOOK.md`）；这里的每一条都默认成立。
>
> 只写「AI 不知道就一定会犯错」的约束。写法上的个人偏好不进这里。
> 本文件随项目演进更新：**接口/架构/命令变了就改这里，别让下一个人照着过期规则干**。

## 技术栈

- 语言/框架：**Python 3.10+（源码必须能在 3.10 上通过语法检查）+ PySide6 6.8+（Qt6 单栈）**
  - 虚拟环境解释器：`.venv\Scripts\python.exe`（本机是 3.12，但 `target-version = "py310"`）
  - 交付形态：PyInstaller onedir + NSIS 安装包；**没有 Node/前端运行时，不要引入**
- 样式方案：QSS + `ui_kit/tokens.py` 设计令牌（浅色专业风格，无直角，圆角 ≥ 10px）
- 测试框架：**pytest**（UI 用例离屏运行）；静态检查：**ruff**（规则集刻意收窄）
- 数据存储：SQLite，直接用标准库 `sqlite3`（`core/platform/db.py` + `migrations.py`）
- 包结构：`src/` 布局，`pyproject.toml` 里 `packages.find where = ["src"]`

## 常用命令

```powershell
# 全量测试（约 2~3 分钟；改了任何逻辑都要跑这一条）
$env:QT_QPA_PLATFORM="offscreen"; .venv\Scripts\python.exe -m pytest tests -q

# 只跑某板块 / 架构闸门
.venv\Scripts\python.exe -m pytest tests/board_video -q
.venv\Scripts\python.exe -m pytest tests/architecture -q

# 静态检查（提交前必须干净）
.venv\Scripts\python.exe -m ruff check src tests

# 打包自检（17 项，验"该随包的东西真的在"）
$env:MODU_CHECK_DEPS="$env:TEMP\check.json"; .venv\Scripts\python.exe -m modu_workbench
Get-Content "$env:TEMP\check.json"      # 期望全部 true；退出码非 0 说明有项失败

# 源码启动
.venv\Scripts\python.exe -m modu_workbench
```

## 架构红线（`tests/architecture/test_architecture.py` 是可执行版本）

```
boards/<板块>  →  同板块包、boards.base、core/<同板块>、core/platform、core/llm、ui_kit、services
boards/<板块>  ✗→ boards/<其他板块>、core/<其他板块>、app.*
core/<板块>    ✗→ core/<其他板块>（只能经 core/platform 或 core/llm）
core.platform  ✗→ 任何 core/<板块>
ui_kit         ✗→ boards/<板块>、core/<板块>
app            →  可依赖任意层，但不许被下层反向依赖
```

**不能"顺手清理"的三个例外**（删了架构闸门立刻失败，或功能当场坏掉）：

1. `services/app_context.py` 是板块之间**唯一合规的沟通通道**（转换产物进书库就靠它）；
2. `core/convert` 是跨板块共享的转换引擎，可被其他 `core/<板块>` 引用，但**不得反向依赖板块**；
3. `ui_kit/media/` 放跨板块复用的播放器/弹幕（不含业务规则，只依赖 `services`/`core/platform`）。

额外硬约束：

- **不许再引入 QtWebEngine / hls.js / 内嵌浏览器**。v1.0.0 起单 Qt 栈，
  自检项 `single_qt_stack` 会用 AST 扫描全仓，发现即失败；
- **板块顺序不许插队**：`app/registry.py` 的 `ACTIVE_BOARDS` 顺序就是顶栏顺序
  （首页 → 书库 → 转换 → 乐库 → 影视 → 图库 → 文档），新板块只能追加在末尾；
- **板块页必须懒构造**：`BoardSpec.page` 是懒解析属性，任何在 `AppShell.__init__` 里
  `spec.page` 的写法都会把六个板块全导入（`test_startup_budget.py` 会拦）；
- **动态导入的字面模块名必须同时出现在 `workbench.spec` 与 `workbench_mac.spec` 的
  hiddenimports 里**（PyInstaller 静态分析看不见字符串，漏了只有装完才炸）。

## 代码规范

- **注释与 docstring 用中文**，写"为什么"而不是"做了什么"。踩过的坑、反直觉的地方、
  被否决的方案都值得写下来 —— 这个仓库的注释就是它的设计文档；
- **优先复用**：新增工具函数前先找 `core/platform/`（`files`/`textutil`/`http`/`logging`/
  `paths`/`media`）与 `ui_kit/components/`。同一能力在多个板块出现时，实现放共享层，
  各板块只留**签名与默认值**的薄封装（`safe_filename` / `unique_path` 就是这么收敛的）；
- 单个文件职责单一，**逻辑代码超过 500 行考虑拆分**；单个函数尽量 ≤ 40 行
  （Qt 的 `_build_ui` 一类装配函数、纯数据表/QSS 字符串可以例外，但要有注释说明）；
- **禁止引入未经确认的新依赖**；确需引入时先说清理由与替代方案（运行时依赖进
  `[project].dependencies`，只给开发/测试用的进 `optional-dependencies.dev`）；
- 遵循现有命名与目录结构，**不擅自重命名公共导出**；
- **不要重排 import 顺序**（`I001` 刻意未启用：一次会重排上百个文件，副作用顺序风险大于收益）；
- **不要删 `# noqa: BLE001` 这类注释**（`RUF100` 刻意未启用：它们记录的是"宽捕获是故意的"
  这一意图，删掉是信息损失）；
- 行长不是问题，**不要为了 110 列去断长中文行**（`E501` 刻意未启用）。

## 行为守恒（铁律）

一次只改**一个最小可验证单元**，改完立刻验证，不做"大爆炸式重构"。

- 不改变公开接口的**输入、输出、错误类型与调用时序**；不删改现有导出名与签名；
- 不改变条件分支的实际执行路径；不合并有副作用的连续调用；不挪动 `await` 位置；
- 不改变 QSS 里的 `objectName`/类名、`setObjectName(...)` 的值、数据库表结构
  （改表必须走 `core/platform/migrations.py` 并同步 `tests/platform/test_db_schema.py`）；
- **不改变用户可见文案的语义**（提示文字是功能的一部分："点了没反应"是被当成 bug 报的）；
- 不修改测试文件本身来让测试变绿。要让旧测试适配新设计，必须**先说明为什么这是设计变更**，
  并在 CHANGELOG 里记一条；
- 重构前先确认：**这个模块现在有测试吗？** 没有就先补测试，再重构
  （"行为不变"是软约束，测试才是硬证据）。

## 忽略失败的写法（统一约定，v2.1.0 起）

**不允许**再写 `except Exception: pass`：

```python
from modu_workbench.core.platform.logging import swallow

try:
    storage.save_position(book_id, index)      # 进度存不下不该让阅读崩
except sqlite3.Error as error:                 # 只吞得起的类型
    swallow("保存阅读进度", error)              # 但必须留痕
```

- 能确定异常类型的 → **收窄类型**（`OSError`/`ValueError`/`sqlite3.Error`/`PdfError`…），
  其他类型属于代码 bug，就该炸出来；
- 第三方回调/Qt 槽等确实可能抛任何异常的边界 → 允许宽捕获，但 `pass` 必须换成 `swallow(...)`；
- 高频路径用 `debug(...)`（只在 `MODU_LOG_LEVEL=debug` 时输出），避免日志刷屏；
- 凡是 `# noqa: BLE001` 的位置，都请在同行或上一行写清楚**为什么不收窄**。

## UI 规范

- **改主题色只动 `ui_kit/tokens.py`；改控件样式只动 `ui_kit/theme_qss_*.py`**。
  QSS 里**禁止写死颜色/间距**，一律用令牌（`{t.accent}` / `{SPACE['md']}`）；
- QSS 按控件族分模块（`theme_qss_base/forms/views/boards/shell.py`），由
  `ui_kit/theme.app_qss` 按固定顺序拼接。**拼接方式改动会改变整份 QSS 的字节序列**，
  重构期间请用 sha256 前后比对（`app_qss(ThemeTokens())`）证明"只搬家、不改样式"；
- 页面的状态/进度统一走底部 `TaskBar`，状态助手由
  `ui_kit.components.task_status.TaskStatusMixin` 提供，**不要在页面里再抄一份**；
- 控件必须能在离屏（`QT_QPA_PLATFORM=offscreen`）下构造；音视频后端不可用时进入静默模式，
  不能让 UI 测试因为"没有声卡"而失败；
- 只读格式（PDF/DOC/XLS…）**不要给编辑框**，而是提示"另存为 DOCX/XLSX 后再编辑"；
  登记了却做不到，就是用户口中的"点了没反应"。

## 测试规范

- 三层职责：`core` 测纯逻辑与存储（不碰网络与界面，网络用 `FakeHttp` 替身）→
  `ui` 只验控件状态与信号（不断言像素）→ `architecture` 把"不许做"写成断言；
- 需要数据目录的用例**必须**用 `tmp_path` 或 `MODU_DATA_DIR`，绝不能写用户 APPDATA；
- UI 用例用 `qapp` 夹具（`tests/conftest.py`），不要自己 new `QApplication`；
- 每个用例只验证一件事，名字写清"行为"：`test_cms_play_url_resolves_share_page`；
- 断言消息要能自解释（"缺少 xx：请检查 yyy"），失败时不用回头读代码；
- 新增**共享实现**（平台层工具、混入）必须补能**直接覆盖它**的单元测试；
  新增架构约束必须同时把旧的宽松闸门**收窄**，不要只加测试不收紧规则。

## 提交与文档规范

- 提交前四件事：`ruff check` 干净 → 全量 `pytest` 通过 → 涉及的板块冒烟（`docs/TESTING.md`
  第 5 节）→ `MODU_CHECK_DEPS` 自检 17 项全 true；
- **CHANGELOG.md 必须更新**：在"## 未发版"下新增一条 `### NN. 标题`（编号递增、最新的在最上面），
  正文用「场景 / 之前 / 现在」表格，最后一行写"验证：新增/修改了哪些用例、全量多少条通过"；
- 版本号**单一来源**是 `src/modu_workbench/__init__.py` 的 `__version__`，
  必须与 `pyproject.toml` 一致（`tests/architecture/test_version.py` 会拦）；
  发版时还要同步 `packaging/version_info.txt`；
- 改架构、改分层、改打包流程 → 同步 `docs/ARCHITECTURE.md`；
  改测试约定 → 同步 `docs/TESTING.md`；改界面规范 → 同步 `docs/UI_GUIDE.md`；
- **"实现完整但没接界面入口"的能力不要当死代码删**：先看 `docs/ARCHITECTURE.md` 的
  「已知未接线的能力（诚实清单）」，按那里的处理原则办（要么接线，要么明确删掉并同步清单）。

## 这个项目里最容易踩的坑（写下来免得再踩一遍）

- `QTimer.singleShot(0, ...)` 延迟重建树是为了避开"点击处理中销毁正在派发的
  `QTreeWidgetItem`"导致的 `Internal C++ object already deleted`；
- Qt 在 Windows 注册表里把布尔存成字符串 `"true"/"false"`，`bool("false") is True`；
  读设置必须走 `ui_kit/settings` 的类型化读取，不要自己 `bool(...)`；
- 文档/表格的公式解析状态（token 流）必须**每次求值前保存、结束后恢复**，
  否则单元格里再嵌公式会把外层 token 冲掉（表现为"缺少 )"）；
- 打包后的窗口程序**没有控制台**：任何静默吞掉的异常都是"点了没反应"，
  所以自检要"真实执行"而不是只 `import`（例如真的播一段 WAV、真的解析一次 m3u8）；
- 图片相关代码里 `dhash` 必须用 numpy 取像素（`Image.getdata()` 在新 Pillow 上返回空序列，
  会让所有哈希都为 0、去重静默失效）；
- 编辑/增强类操作有"预览尺寸"与"全尺寸"两条路径，改算法时**两条都要过一遍**。
