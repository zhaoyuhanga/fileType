# 生成 Windows 安装包（NSIS）：从 pyproject 读取版本号，覆盖 installer.nsi 的默认值。
#
# 用法（Windows PowerShell 5.1 或 PowerShell 7 均可）：
#   powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1
#   pwsh -ExecutionPolicy Bypass -File packaging\build_installer.ps1 -Makensis "C:\path\makensis.exe"
param(
    [string]$Makensis,
    [string]$SourceDir
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$packageDir = Join-Path $repoRoot "dist\ModuWorkbench"

function Invoke-Native([string]$Exe, [string[]]$Arguments) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Exe @Arguments 2>&1 | ForEach-Object { Write-Host $_ }
    } finally {
        $ErrorActionPreference = $previous
    }
    return $LASTEXITCODE
}

# 1) 版本号：单一来源（modu_workbench.__version__）
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
$version = (& $python -c "import modu_workbench; print(modu_workbench.__version__)").Trim()
if (-not $version) { throw "无法读取版本号" }
Write-Host "版本：$version"

# 2) makensis：参数 > PATH > electron-builder 缓存
if (-not $Makensis) {
    $cmd = Get-Command makensis.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        $Makensis = $cmd.Source
    } else {
        $cached = Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA "electron-builder\Cache\nsis") `
            -Recurse -Filter makensis.exe -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($cached) { $Makensis = $cached.FullName }
    }
}
if (-not $Makensis -or -not (Test-Path $Makensis)) {
    throw "未找到 makensis.exe，请安装 NSIS 或用 -Makensis 指定路径"
}
Write-Host "makensis：$Makensis"

# 3) 待打包目录必须已由 PyInstaller 生成
if (-not $SourceDir) { $SourceDir = $packageDir }
if (-not (Test-Path $SourceDir)) {
    throw "缺少待打包目录：$SourceDir（先执行 pyinstaller workbench.spec --noconfirm --clean）"
}

$arguments = @("/DAPP_VERSION=$version", "/DSOURCE_DIR=$SourceDir", (Join-Path $PSScriptRoot "installer.nsi"))
$code = Invoke-Native $Makensis $arguments
if ($code -ne 0) { throw "makensis 失败（exit $code）" }

$output = Join-Path $repoRoot "dist\墨软工作台-Setup-$version.exe"
if (Test-Path $output) {
    $size = [math]::Round((Get-Item $output).Length / 1MB, 1)
    Write-Host "完成：$output（${size} MB）"
} else {
    throw "未生成安装包：$output"
}
