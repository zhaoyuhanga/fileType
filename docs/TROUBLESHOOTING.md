# 故障排查（TROUBLESHOOTING）

按**症状**查。先看一句话结论，再看原因与处理办法。
这里的每一条都来自实际发生过的问题，不是假设。

## 一、装完打不开 / 启动就闪退

### 症状：双击图标，弹窗 `Unhandled exception in script`

**原因**：程序在导入阶段就崩了。历史上发生过两次，报错内容不同：

| 弹窗里的关键句 | 真实原因 | 修复版本 |
|---|---|---|
| `No module named 'modu_workbench.boards.book.board'` | 板块页用 `importlib` 字符串懒导入，PyInstaller 静态分析看不到，**模块没打进包** | v2.1.1 |
| `cannot import name '_icon_urls' from partially initialized module` | `theme.py` ↔ `theme_icons.py` **循环导入**，冻结后模块加载顺序变了才暴露 | v2.1.1 |

**处理**：升级到最新版。若你是开发者，跑
`powershell -ExecutionPolicy Bypass -File packaging\build_app.ps1 -Installer`
—— 现在的构建链有**板块冒烟**（真的逐个打开六个板块）和**GUI 冒烟**（认标题、认崩溃框），
这两类问题会在打包时就失败，不会再流到用户手里。

### 症状：双击没反应，或提示 `An Application Control policy has blocked this file`

**原因**：Windows 11 的 **Smart App Control** 拦截了未签名的 exe。它有个很坑的特性：
**刚构建/刚下载的文件会被拦一段时间，过后自动放行**（实测从几秒到十几分钟不等）。

**处理**：等一会儿再试。持续被拦就三选一：关闭 Smart App Control、对 exe 签名、
或改用源码运行（见 `docs/CONTRIBUTING.md`）。**这不是程序缺陷**。

### 症状：卸载器 `/S` 静默退出、什么都没删

**判断方法**：看 `%TEMP%\~nsu*.tmp\` 里是不是堆着 `Un_A.exe` … `Un_Z.exe`（每个 62KB）。
有这堆文件 = 卸载器的自复制被执行策略拦了，**卸载 Section 根本没跑到**
（开始菜单、桌面快捷方式、注册表项也都还在）。

**处理**：关掉 Smart App Control 后重试，或手动清理（`docs/RELEASE.md` 第 4 节有现成脚本）。
**卸载前务必先退出程序**：NSIS 删不掉被占用的文件。

---

## 二、影视板块

### 症状：搜索一直转圈、没有结果

**原因**：内置源站失效、或被网络环境拦截。影视依赖的是**聚合接口站**，
站点换域名/加校验就会这样。

**处理**：① 换一个源（设置 → 影视 → 视频源，可以逐个试）；
② 看 `MODU_LOG_LEVEL=debug` 的 stderr 输出，里面会有具体失败原因；
③ 这不是能靠软件修的东西 —— 源站换了就换源。

### 症状：能搜到剧、能看到剧集列表，但播放打不开

**原因**：该源只给了"播放页地址"，真正的视频地址要网页 JS 现算。
本项目**不内置浏览器内核**，这类站点播不了（这也是当初「动漫」板块被整体移除的原因：
KazumiRules 全量 16 条规则里，取到播放地址的是 **0 条**）。

**处理**：换源，或选那种地址可静态解析的源。

### 症状：播放在线视频很卡

**排查顺序**：
1. 设置 → 影视 → 播放质量：`auto`（自适应）会按实测带宽选档；
2. 播放器 F12 诊断面板里看是"回源慢"还是"解码慢"——前者换源，后者看下面；
3. 硬件解码是**进程级环境变量**，改完设置要重开播放窗口（必要时重启应用）才生效。

### 症状：下载特别慢

**可能**：设置里配了下载限速（`video/download_speed_limit_mbps`）。
设为 0 表示不限速。此外集间并发默认 2、分片并发默认 6，可以在设置里调，
但调太高对源站不友好、也更容易被限流。

---

## 三、文档板块

### 症状：PDF 打不开 / 打印 PDF 报错

PDF 渲染依赖 **QtPdf**（随包，约 4.4 MB）。自检项 `pdf_print_engine` 专门验证它。
若自检失败，说明打包时被误裁掉了（`workbench.spec` 的 EXCLUDES 不能排除 `PySide6.QtPdf`）。

### 症状：Excel 公式算不对

先在「文档 → 表格」里看公式面板的提示。**诚实边界**：本项目的公式引擎是自研的子集，
支持常用函数与区域引用，但**不保证与 Excel 完全一致**（尤其数组公式、易失函数、
跨工作簿引用）。算不出来的地方会明确报错，不会静默给个错数。

### 症状：Office 文档转换（docx/xlsx → PDF）失败

需要 **LibreOffice**（`soffice`）。没有时转换会失败并提示；
设置里可以手动指定 `soffice` 路径。

---

## 四、图库板块

### 症状：导入一批照片后界面卡

图片解码与缩略图都在后台线程，但**首次扫描大目录**仍会占用较多 IO。
建议一次导入一个目录，或先关掉其他板块的下载任务。

### 症状：AI 增强/AI 打标没反应

**检查**：设置 → 大模型，确认已配置可用的 OpenAI 兼容端点与密钥，
并点「测试连接」。图库的大模型能力复用 `core/llm`，与文档板块共用配置。

**注意**：图库的「只发元数据」模式（`allow_upload=False`）**不上传图片**，
只把文件名/EXIF/尺寸发给模型 —— 这是隐私取舍，不是缺陷。

---

## 五、乐库板块

### 症状：某些歌搜得到、点了播放没声音

多数是源站返回的是**试听片段或失效链接**。设置 → 乐库 里有"最短完整时长"过滤
（`DEFAULT_MIN_FULL_SECONDS`），可以把明显是试听的条目挡掉。

---

## 六、书库板块

### 症状：导入 EPUB 后章节乱了

章节识别按标题正则分级。遇到排版特殊的书（例如全靠样式而非标题层级）可能分错。
可以先在阅读器里看目录，必要时用「转换」板块把 EPUB → TXT 后自己整理。

### 症状：在线书库下载失败

在线书源是**第三方站点**，随时可能改版或失效；合规开关在 设置 → 书库。
本项目只做"按需下载已授权内容"的工具，请自行确认使用范围。

---

## 七、开发者：测试/打包

### 症状：某个用例单独跑很快、全量跑很慢或偶发失败

典型原因是**测试之间互相污染**或**依赖了本机状态**。已知两处：

- 下载限速读真实 `QSettings` 注册表 → 必须在用例里 `monkeypatch` 成 0；
- 预热/异步任务要等"真正想要的条件"，而不是等一个代理信号
  （例如等 `cache.count() >= 3` 而不是等队列排空——失败时队列也会排空）。

### 症状：`build_app.ps1` 报 `Unexpected attribute 'CmdletBinding'` / `Missing expression after ','`

都是**脚本自身的语法/编码问题**，不是业务代码：

- 前者 = 文件里有**重复 BOM**（U+FEFF 被写成正文）。写回时先剥掉首字符；
- 后者 = PowerShell 数组字面量不允许**尾逗号**（删条目后容易留下）。

`tests/architecture/test_spec_integrity.py` 会检查 BOM。

### 症状：打包后"模块核验 40 项"通过，但装完还是起不来

**核验只看 exe 字节流里有没有这个名字**，注释和字符串里的名字也算命中，
所以它**不能**证明模块真的可导入。真正管用的是：

```powershell
$env:MODU_SMOKE_BOARDS="$env:TEMP\smoke.json"; & .\dist\ModuWorkbench\ModuWorkbench.exe
Get-Content "$env:TEMP\smoke.json"   # 八个键必须全 true
```

### 症状：想看启动到底加载了多少模块

```powershell
$env:MODU_LOG_LEVEL="debug"
python -c "import sys; from PySide6.QtWidgets import QApplication; app=QApplication([]); \
  from modu_workbench.app.shell import AppShell; s=AppShell(); \
  print(len([m for m in sys.modules if m.startswith('modu_workbench')]))"
```

当前基线是 **24**（v2.2.0）。超过 60 就说明又有模块在启动路径上被提前导入了
（`test_startup_budget.py` 会失败）。

---

## 八、怎么给我一份有用的报告

出问题时请附上：

1. **版本号**：设置 → 通用 里能看到，或看 exe 属性；
2. **弹窗/日志原文**（`MODU_LOG_LEVEL=debug` 后的 stderr）；
3. **复现步骤**：哪个板块、点了什么、对什么文件；
4. 若是打包相关：`packaging/build_app.ps1` 的完整输出。

有这四项，定位通常几分钟；只说"用不了"的话，只能靠猜。
