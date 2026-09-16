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

Write-Step "打包后验证（第四/第五板块是否随包）"

# 关键点：墨软影视 / 墨软图库模块必须真的进了包，否则首页板块卡会缺。
# onedir 下纯 Python 模块名存放在 exe 内嵌的 PYZ 归档里，磁盘上没有 .pyz 文件，
# 因此在 exe 字节流中直接检索模块名（模块名以明文 UTF-8 存在归档索引里）。
$exeBytes = [System.IO.File]::ReadAllBytes($exePath)
$exeText = [System.Text.Encoding]::UTF8.GetString($exeBytes)

$required = @(
    "modu_workbench.boards.video.board",
    "modu_workbench.boards.video.search",
    "modu_workbench.boards.video.player",
    "modu_workbench.core.video.sources",
    "modu_workbench.core.video.sources.providers.cms_vod",
    "modu_workbench.core.video.downloader",
    "modu_workbench.boards.gallery.board",
    "modu_workbench.boards.gallery.widgets",
    "modu_workbench.boards.gallery.editor",
    "modu_workbench.boards.gallery.enhance",
    "modu_workbench.core.gallery.library",
    "modu_workbench.core.gallery.enhance",
    "modu_workbench.core.gallery.ai",
    "modu_workbench.core.llm.router",
    "modu_workbench.ui_kit.settings.llm"
)
$missing = @()
foreach ($name in $required) {
    if (-not $exeText.Contains($name)) { $missing += $name }
}
if ($missing.Count -gt 0) {
    Fail ("打包产物缺少板块模块：" + ($missing -join ", ") + "`n    请确认 workbench.spec 的 hiddenimports 未被改动。")
}
Write-Ok "影视 / 图库 / 大模型模块已随包（$($required.Count) 项全部命中）"

# 真正跑一次打包产物：MODU_CHECK_DEPS 会输出各能力自检结果（含 video_core）
Write-Step "运行打包产物做能力自检"

$checkOut2 = Join-Path $env:TEMP "modu-dist-check.json"
$env:MODU_CHECK_DEPS = $checkOut2
Remove-Item $checkOut2 -ErrorAction SilentlyContinue
& $exePath | Out-Null
Remove-Item Env:\MODU_CHECK_DEPS -ErrorAction SilentlyContinue

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
        Write-Warn2 "自检结果里没有 video_core 项，请确认 app/main.py 的 record(\"video_core\", ...) 存在。"
    }
    if ($null -eq $json2.gallery_core) {
        Write-Warn2 "自检结果里没有 gallery_core 项，请确认 app/main.py 的 record(\"gallery_core\", ...) 存在。"
    }
} else {
    Write-Warn2 "打包产物未输出自检结果，请手动运行 $exePath 确认首页有五张板块卡。"
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
Write-Ok "启动后首页应显示五张板块卡：墨软书库 / 墨软转换 / 墨软乐库 / 墨软影视 / 墨软图库"
Write-Host ""
