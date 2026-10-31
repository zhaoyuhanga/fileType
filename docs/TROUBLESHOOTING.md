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

### 症状：双击后弹出「智能应用控制已阻止可能不安全的应用」

完整对话框文字：

> **智能应用控制已阻止可能不安全的应用**
> 我们已阻止 `C:\Users\<你>\AppData\Local\Programs\墨软工作台\ModuWorkbench.exe`，
> 因为无法验证其发布者，无法确认它是否能安全运行。
> 已阻止此应用以帮助保护你的设备和数据。Microsoft 将审核该应用以确认其安全性。

或命令行 / 构建脚本里出现：

```
An Application Control policy has blocked this file
```

#### 原因（已实测确认）

墨软工作台**没有代码签名证书**，而 Windows 11 的 **Smart App Control（智能应用控制）**
只放行「有可信发布者」的程序。这不是程序缺陷，也不是杀毒误报 —— 是策略。

实测过一个很能说明问题的现象（同一台机器、同一个未签名 exe）：

| 位置 | 结果 |
|---|---|
| `dist\ModuWorkbench\ModuWorkbench.exe`（构建产物） | **能运行** |
| `C:\Users\...\AppData\Local\Programs\墨软工作台\ModuWorkbench.exe`（安装后） | **被拦** |

也就是**换个路径就可能从能跑变成不能跑**，SAC 的判定按文件信誉走、对用户不可预测。

#### 先确认自己是不是这个原因

```powershell
# 1) SAC 是否处于强制模式（1=强制，2=评估，0=关闭）
Get-ItemProperty HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy `
  -Name VerifiedAndReputablePolicyState

# 2) exe 有没有签名（NotSigned = 没签）
Get-AuthenticodeSignature "$env:LOCALAPPDATA\Programs\墨软工作台\ModuWorkbench.exe" |
  Select-Object Status, SignerCertificate
```

`VerifiedAndReputablePolicyState = 1` 且 `Status = NotSigned` → 就是本文说的原因。

#### 三条出路（按推荐顺序）

**① 关闭智能应用控制（最快，但有代价）**

设置 → 隐私和安全性 → Windows 安全中心 → 应用和浏览器控制 → 智能应用控制 → **关闭**。

> ⚠️ **重要**：Smart App Control 一旦关闭，**无法再打开**（除非重置或重装 Windows）。
> Microsoft 就是这样设计的。只在你确认需要长期运行未签名程序时才关。

**② 给程序签名（真正解决，需要证书）**

签名后 SAC 与 SmartScreen 都会放行，且**分发给别人也不会被拦**。
构建脚本已支持，拿到证书后一条命令：

```powershell
# 证书在证书存储里（EV 证书多为硬件令牌，只能这样引用）
powershell -ExecutionPolicy Bypass -File packaging\build_app.ps1 -Installer -Sign `
  -CertificateThumbprint <证书指纹>

# 或者用 .pfx 文件
powershell -ExecutionPolicy Bypass -File packaging\build_app.ps1 -Installer -Sign `
  -PfxPath D:\cert.pfx -PfxPassword "密码"
```

说明：

- 脚本优先用 `signtool`（Windows SDK）。**没装 SDK 也能用** —— 会回退到
  PowerShell 原生 `Set-AuthenticodeSignature`；
- **自签名证书不管用**：SAC 只认受信任的公共 CA 签发的代码签名证书。
  自签名后 `Get-AuthenticodeSignature` 会显示 `UnknownError`
  （"证书链终止于不受信任的根证书"），SAC 照样拦；
- 证书分 OV / EV：**EV 证书**通常能立刻建立 SmartScreen 信誉，OV 需要累积下载量；
- **个人开发者 / 开源项目的免费与低成本路径**（SignPath Foundation 的逐条条件、
  本项目差距清单、落地步骤、Code signing policy 模板）见
  [`docs/SIGNING.md`](SIGNING.md)。

**③ 用构建产物（临时用一下）**

`dist\ModuWorkbench\` 是免安装的 onedir 目录，**实测能运行**（见上表）。
但 SAC 可能过一会儿把它也标记掉，所以只适合临时使用，不适合日常。

#### 相关：发布时请一并提供校验和

构建脚本现在会自动生成 `dist\SHA256SUMS.txt`。未签名的发布物**尤其**应该给出
校验和，让用户能确认拿到的文件没被篡改：

```powershell
Get-FileHash .\墨软工作台-Setup-2.2.0.exe -Algorithm SHA256
# 与 SHA256SUMS.txt 里那一行对比
```

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

### 症状：点开一本书，只有一片空白

**已修（v2.2.0 之后）**：以前原文件丢失/被占用时，阅读器会安静地显示空白页。
现在会**弹提示说明原因并退回书架**，例如：

- `原文件找不到了：D:\books\xxx.txt` → 把文件放回原位置，或重新导入；
- `没有权限读取` → 检查文件是否被其它程序占用；
- `文字编码无法识别` → 先用「墨软转换」转成 UTF-8 的 TXT 再导入。

如果你用的是旧版本遇到这个问题，升级即可。

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
