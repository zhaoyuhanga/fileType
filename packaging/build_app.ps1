# 墨软·工作台 —— Windows 一键重新打包（onedir）
#
# 用法（在仓库根目录，Windows PowerShell 5.1 或 PowerShell 7 均可）：
#   powershell -ExecutionPolicy Bypass -File packaging\build_app.ps1
#   pwsh -ExecutionPolicy Bypass -File packaging\build_app.ps1      # PowerShell 7
#
# 可选参数：
#   -NoClean      保留 PyInstaller 缓存（默认会 --clean：新增模块时缓存会导致漏打包）
#   -SkipDeps     跳过依赖安装（PyInstaller 已就绪时更快）
#   -Installer    打包成功后继续生成 NSIS 安装包（需要已安装 makensis）
#   -Python       指定解释器（默认优先用 .venv\Scripts\python.exe）
#
# 产物：
#   dist\ModuWorkbench\ModuWorkbench.exe           （onedir，随包依赖）
#   dist\墨软工作台-Setup-<版本>.exe                （仅 -Installer 时）

[CmdletBinding()]
param(
    [switch]$NoClean,
    [switch]$SkipDeps,
    [switch]$Installer,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"

# 统一输出为 UTF-8，避免中文乱码（NSIS / PowerShell 5.1 对 BOM 敏感，见既有约定）
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
} catch { }

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Write-Ok([string]$Text) {
    Write-Host "    $Text" -ForegroundColor Green
}

function Write-Warn2([string]$Text) {
    Write-Host "    $Text" -ForegroundColor Yellow
}

function Fail([string]$Text) {
    Write-Host ""
    Write-Host "打包失败：$Text" -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------- 定位仓库根目录

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
Set-Location $repoRoot

if (-not (Test-Path (Join-Path $repoRoot "workbench.spec"))) {
    Fail "未找到 workbench.spec，请确认脚本位于 <仓库>\packaging\ 下"
}
Write-Ok "仓库根目录：$repoRoot"

# ---------------------------------------------------------------- 选择解释器

Write-Step "检查 Python 解释器"

if (-not $Python) {
    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        $Python = $venvPython
    } else {
        $cmd = Get-Command python -ErrorAction SilentlyContinue
        if ($cmd) { $Python = $cmd.Source }
    }
}

if (-not $Python -or -not (Test-Path $Python)) {
    Fail "未找到 Python 解释器。请先创建虚拟环境：py -3.10 -m venv .venv，或用 -Python 指定路径"
}

$version = & $Python -c "import sys; print(sys.version.split()[0])"
Write-Ok "解释器：$Python (Python $version)"

# ---------------------------------------------------------------- 源码自检（先跑，省得打包完才发现）

Write-Step "源码自检（编译 + 影视核心/依赖自检）"

$checkOut = Join-Path $env:TEMP "modu-build-check.json"
$env:MODU_CHECK_DEPS = $checkOut
Remove-Item $checkOut -ErrorAction SilentlyContinue

& $Python -m modu_workbench
$checkCode = $LASTEXITCODE
Remove-Item Env:\MODU_CHECK_DEPS -ErrorAction SilentlyContinue

if (Test-Path $checkOut) {
    $json = Get-Content $checkOut -Raw -Encoding UTF8 | ConvertFrom-Json
    $failed = @()
    foreach ($prop in $json.PSObject.Properties) {
        if ($prop.Value -ne $true) {
            $failed += "$($prop.Name)=$($prop.Value)"
        }
    }
    if ($failed.Count -gt 0) {
        Write-Warn2 "以下自检项未通过（打包仍会继续，但请留意）："
        $failed | ForEach-Object { Write-Warn2 "  - $_" }
    } else {
        Write-Ok "全部自检项通过"
    }
} else {
    Write-Warn2 "未生成自检结果（退出码 $checkCode），跳过。"
}

# ---------------------------------------------------------------- 依赖

if (-not $SkipDeps) {
    Write-Step "安装/校验依赖（含 PyInstaller）"
    & $Python -m pip install --upgrade pip --quiet
    & $Python -m pip install -e . --quiet
    & $Python -m pip install "pyinstaller>=6.0" --quiet
    if ($LASTEXITCODE -ne 0) { Fail "依赖安装失败" }
    Write-Ok "依赖就绪"
}

$pyi = & $Python -c "import PyInstaller, sys; print(PyInstaller.__version__)"
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller 不可用，请去掉 -SkipDeps 重跑" }
Write-Ok "PyInstaller $pyi"

# ---------------------------------------------------------------- 随包 ffmpeg

Write-Step "检查随包 ffmpeg"

$ffDir = Join-Path $repoRoot "tools\ffmpeg"
$ffExe = Join-Path $ffDir "ffmpeg.exe"
$ffProbe = Join-Path $ffDir "ffprobe.exe"

if ((Test-Path $ffExe) -and (Test-Path $ffProbe)) {
    Write-Ok "已就绪：$ffDir"
} else {
    Write-Warn2 "未找到随包 ffmpeg，尝试自动获取（约 106MB，仅首次）"
    New-Item -ItemType Directory -Force -Path $ffDir | Out-Null
    $fetch = @'
import io, pathlib, shutil, sys, urllib.request, zipfile

dst = pathlib.Path(sys.argv[1])
url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
zip_path = pathlib.Path(sys.argv[2])
want = ("ffmpeg.exe", "ffprobe.exe")

if not zip_path.is_file():
    print("downloading", url)
    urllib.request.urlretrieve(url, zip_path)

with zipfile.ZipFile(zip_path) as z:
    for name in z.namelist():
        base = name.rsplit("/", 1)[-1]
        if base in want:
            with z.open(name) as src, open(dst / base, "wb") as out:
                shutil.copyfileobj(src, out)
            print("extracted", base)
'@
    $fetchPath = Join-Path $env:TEMP "modu_fetch_ffmpeg.py"
    Set-Content -Path $fetchPath -Value $fetch -Encoding UTF8
    & $Python $fetchPath $ffDir (Join-Path $env:TEMP "ffmpeg-release-essentials.zip")
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $ffExe)) {
        Write-Warn2 "自动获取失败。请手动放置 ffmpeg.exe 与 ffprobe.exe 到：$ffDir"
        Write-Warn2 "（缺少它们也能打包：HLS 下载将输出 .ts，且音视频转换不可用）"
    } else {
        Write-Ok "已获取随包 ffmpeg"
    }
}

# ---------------------------------------------------------------- 构建

Write-Step "PyInstaller 打包（onedir）"

$pyiArgs = @("-m", "PyInstaller", "workbench.spec", "--noconfirm")
# 默认 --clean：PyInstaller 会缓存分析结果，新增包（如 core/video）时缓存会导致漏打包
if (-not $NoClean) { $pyiArgs += "--clean" }

Write-Host "    $Python $($pyiArgs -join ' ')" -ForegroundColor DarkGray
& $Python @pyiArgs
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller 退出码 $LASTEXITCODE" }

$exePath = Join-Path $repoRoot "dist\ModuWorkbench\ModuWorkbench.exe"
if (-not (Test-Path $exePath)) { Fail "未生成 $exePath" }
Write-Ok "已生成：$exePath"

# ---------------------------------------------------------------- 打包后验证

Write-Step "打包后验证（第四/五/六板块是否随包）"

# 关键点：墨软影视 / 墨软图库模块必须真的进了包，否则首页板块卡会缺。
# onedir 下纯 Python 模块名存放在 exe 内嵌的 PYZ 归档里，磁盘上没有 .pyz 文件，
# 因此在 exe 字节流中直接检索模块名（模块名以明文 UTF-8 存在归档索引里）。
$exeBytes = [System.IO.File]::ReadAllBytes($exePath)
$exeText = [System.Text.Encoding]::UTF8.GetString($exeBytes)

$required = @(
    "modu_workbench.boards.video.board",
    "modu_workbench.boards.video.search",
    "modu_workbench.core.video.sources",
    "modu_workbench.core.video.sources.providers.cms_vod",
    "modu_workbench.core.video.downloader",
    # v1.1.0：影视播放/下载链路重写的关键模块（代理 + ABR + 并发分片下载 + 共享媒体层）
    "modu_workbench.core.video.abr",
    "modu_workbench.core.video.segment_downloader",
    "modu_workbench.services.media_proxy.server",
    "modu_workbench.services.media_proxy.upstream",
    "modu_workbench.services.media_proxy.cache",
    # v2.1.0：单体拆分后的新模块（fetch/handler 被 server 与 playlist 引用）
    "modu_workbench.services.media_proxy.fetch",
    "modu_workbench.services.media_proxy.handler",
    "modu_workbench.ui_kit.theme_icons",
    "modu_workbench.app.selfcheck",
    "modu_workbench.ui_kit.media.player",
    "modu_workbench.ui_kit.media.surface",
    "modu_workbench.ui_kit.media.decode",
    "modu_workbench.ui_kit.media.danmaku_bridge",
    "modu_workbench.boards.gallery.board",
    "modu_workbench.boards.gallery.widgets",
    "modu_workbench.boards.gallery.editor",
    "modu_workbench.boards.gallery.enhance",
    "modu_workbench.core.gallery.library",
    "modu_workbench.core.gallery.enhance",
    "modu_workbench.core.gallery.ai",
    "modu_workbench.boards.document.board",
    "modu_workbench.boards.document.viewer",
    "modu_workbench.boards.document.merge_page",
    "modu_workbench.boards.document.loop_page",
    "modu_workbench.boards.document.pdf_dialog",
    "modu_workbench.core.document.library",
    "modu_workbench.core.document.merge",
    "modu_workbench.core.document.loop",
    "modu_workbench.core.document.tools",
    "modu_workbench.core.document.pdf_tools",
    "modu_workbench.core.document.templates",
    "modu_workbench.core.document.plugins",
    "modu_workbench.core.llm.router",
    "modu_workbench.ui_kit.settings.llm",
    "modu_workbench.ui_kit.settings.document"
)
$missing = @()
foreach ($name in $required) {
    if (-not $exeText.Contains($name)) { $missing += $name }
}
if ($missing.Count -gt 0) {
    Fail ("打包产物缺少板块模块：" + ($missing -join ", ") + "`n    请确认 workbench.spec 的 hiddenimports 未被改动。")
}
Write-Ok "影视 / 图库 / 文档 / 大模型模块已随包（$($required.Count) 项全部命中）"

# 真正跑一次打包产物：MODU_CHECK_DEPS 会输出各能力自检结果（含 video_core）
Write-Step "运行打包产物做能力自检"

$checkOut2 = Join-Path $env:TEMP "modu-dist-check.json"
$env:MODU_CHECK_DEPS = $checkOut2
Remove-Item $checkOut2 -ErrorAction SilentlyContinue
# 注意：刚构建出来的未签名 exe 可能被 Windows「智能应用控制（Smart App Control）」拦下
# （An Application Control policy has blocked this file）。这是系统策略而非打包缺陷，
# 因此这里捕获异常给出提示并继续（否则安装包步骤永远跑不到）。
$selfCheckError = ""
try {
    & $exePath | Out-Null
} catch {
    $selfCheckError = $_.Exception.Message
}
Remove-Item Env:\MODU_CHECK_DEPS -ErrorAction SilentlyContinue

if ($selfCheckError) {
    Write-Warn2 "打包产物未能运行自检：$selfCheckError"
    Write-Warn2 "  若是「Application Control policy」，请关闭「智能应用控制」或对 exe 签名后重跑；"
    Write-Warn2 "  也可以只做模块级核验（上面 $($required.Count) 项已通过）。"
}

if (Test-Path $checkOut2) {
    $json2 = Get-Content $checkOut2 -Raw -Encoding UTF8 | ConvertFrom-Json
    $failed2 = @()
    foreach ($prop in $json2.PSObject.Properties) {
        if ($prop.Value -ne $true) { $failed2 += "$($prop.Name)=$($prop.Value)" }
    }
    if ($failed2.Count -gt 0) {
        Write-Warn2 "以下自检项未通过："
        $failed2 | ForEach-Object { Write-Warn2 "  - $_" }
    } else {
        Write-Ok "全部自检项通过（含 video_core）"
    }
    if ($null -eq $json2.video_core) {
        Write-Warn2 "自检结果里没有 video_core 项，请确认 app/selfcheck.py 的 record(\"video_core\", ...) 存在。"
    }
    if ($null -eq $json2.gallery_core) {
        Write-Warn2 "自检结果里没有 gallery_core 项，请确认 app/selfcheck.py 的 record(\"gallery_core\", ...) 存在。"
    }
    if ($null -eq $json2.document_core) {
        Write-Warn2 "自检结果里没有 document_core 项，请确认 app/selfcheck.py 的 record(\"document_core\", ...) 存在。"
    }
    if ($null -eq $json2.pdf_print_engine) {
        Write-Warn2 "自检结果里没有 pdf_print_engine 项：文档板块打印 PDF 需要 QtPdf，请确认 spec 的 EXCLUDES 没有排除 PySide6.QtPdf（以及 Qt6Pdf.dll）。"
    }
} else {
    Write-Warn2 "打包产物未输出自检结果，请手动运行 $exePath 确认首页有六张板块卡。"
}

# 真正"起一次窗口"：上面的 MODU_CHECK_DEPS 自检在创建 QApplication/主壳**之前**就 return 了，
# 所以主题组装、板块页构造、循环导入这类问题它一个都测不到。
# v2.1.0 就栽在这里：theme.py 与 theme_icons.py 模块级互相导入，源码里 theme 总是先导入，
# 1154 个测试 + 自检 17/17 全绿，但冻结后加载顺序变成 theme_icons 先跑 → 装完点图标启动失败。
Write-Step "启动 GUI 冒烟（必须真的出窗口）"

$guiOk = $false
$guiNote = ""
for ($attempt = 1; $attempt -le 3 -and -not $guiOk; $attempt++) {
    try {
        $proc = Start-Process -FilePath $exePath -PassThru -ErrorAction Stop
    } catch {
        $guiNote = $_.Exception.Message
        Start-Sleep -Seconds 20           # 智能应用控制拦新文件后会自动放行
        continue
    }
    # 只认**主窗口**：标题必须含品牌名。
    # v2.1.0 教训：之前只判断"标题非空"，而启动崩溃时 PyInstaller 会弹一个
    # 标题为 "Unhandled exception in script" 的对话框 —— 它也是"有标题的窗口"，
    # 于是闸门绿灯放行、装完却起不来。现在把崩溃框当失败，并尽量读出它的正文。
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 400
        $live = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
        if (-not $live) { break }
        $title = $live.MainWindowTitle
        if ($title -and $title -like "*墨软*") { $guiOk = $true; break }
        if ($title -and $title -like "*Unhandled exception*") {
            $guiNote = "启动崩溃：$title → $(Get-PyInstallerDialogText -ProcessId $proc.Id)"
            break
        }
    }
    if (-not $guiOk -and -not $guiNote) {
        $live = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
        if ($live) {
            $guiNote = "进程存活但 60 秒内主窗口没起来（当前标题：'$($live.MainWindowTitle)'）"
        } else {
            $guiNote = "进程已退出（退出码 $($proc.ExitCode)）—— 启动期崩溃"
        }
    }
    if (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
}

# 启动级冒烟：不止"有没有窗口"，还要**逐个打开六个板块**。
# 这条覆盖 GUI 冒烟看不到的部分（点板块才触发的懒导入与页面构造）。
$smokeOut = Join-Path $env:TEMP "modu-board-smoke.json"
$env:MODU_SMOKE_BOARDS = $smokeOut
Remove-Item $smokeOut -ErrorAction SilentlyContinue
$smokeError = ""
try { & $exePath | Out-Null } catch { $smokeError = $_.Exception.Message }
Remove-Item Env:\MODU_SMOKE_BOARDS -ErrorAction SilentlyContinue

if ($smokeError) {
    Write-Warn2 "板块冒烟未能运行：$smokeError"
} elseif (Test-Path $smokeOut) {
    $smoke = Get-Content $smokeOut -Raw -Encoding UTF8 | ConvertFrom-Json
    $smokeBad = @()
    foreach ($prop in $smoke.PSObject.Properties) {
        if ($prop.Value -ne $true) { $smokeBad += "$($prop.Name)=$($prop.Value)" }
    }
    if ($smokeBad.Count -gt 0) {
        $smokeBad | ForEach-Object { Write-Warn2 "  - $_" }
        Fail "板块冒烟失败：有板块在打包产物里打不开（懒导入模块可能没进包，检查 spec 的 hiddenimports）"
    } else {
        Write-Ok "板块冒烟通过（主题 + 主壳 + 六个板块全部可打开）"
    }
} else {
    Fail "板块冒烟未输出结果：$smokeOut"
}

if ($guiOk) {
    Write-Ok "GUI 冒烟通过（主窗口已出现）"
} else {
    Write-Warn2 "这通常意味着打包产物启动期崩溃（循环导入 / 缺模块 / 主题组装失败）。"
    Write-Warn2 "排查：先用源码跑 .venv\Scripts\python.exe -m modu_workbench 对照；"
    Write-Warn2 "      或临时把 spec 的 console 改成 True 重新打包以看到 traceback。"
    Fail "GUI 冒烟失败：$guiNote"
}

# ---------------------------------------------------------------- 安装包（可选）

if ($Installer) {
    Write-Step "生成 NSIS 安装包"
    # 不再依赖 pwsh：Windows PowerShell 5.1 里没有 pwsh 命令，直接同会话调用脚本更稳
    $installerScript = Join-Path $scriptDir "build_installer.ps1"
    try {
        & $installerScript
        Write-Ok "安装包已生成（dist\墨软工作台-Setup-<版本>.exe）"
    } catch {
        Write-Warn2 "安装包生成失败：$($_.Exception.Message)"
        Write-Warn2 "  可单独重跑：powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1"
    }
}

# ---------------------------------------------------------------- 完成

Write-Step "完成"
Write-Ok "onedir 产物：dist\ModuWorkbench\ModuWorkbench.exe"
Write-Ok "启动后首页应显示六张板块卡：墨软书库 / 墨软转换 / 墨软乐库 / 墨软影视 / 墨软图库 / 墨软文档"
Write-Host ""
