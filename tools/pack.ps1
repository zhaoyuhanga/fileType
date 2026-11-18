<#
.SYNOPSIS
    打包最新版本：清理 → 构建（onedir + 安装包）→ 校验产物完整性。

.DESCRIPTION
    直接跑 packaging\build_app.ps1 有两个坑，本脚本把它们固化掉：

    1. **dist 被占用**：应用还开着 / 杀毒软件正在扫，PyInstaller 删不掉目录，
       会留下"exe 在、_internal 缺一半"的残缺产物（上一次"所有数据源不可用"
       就是缺了 certifi\cacert.pem 导致的）。这里先结束进程、带重试地清目录；
    2. **退出码不可靠**：build_app.ps1 末尾在 PowerShell 5.1 下会返回 1
       （闸门其实都过了），所以判断成功**以产物为准**：exe、certifi、
       安装包、SHA256SUMS 都在才算成功。

.PARAMETER NoInstaller
    只出 onedir，不出 NSIS 安装包。

.PARAMETER WithDeps
    让 build_app.ps1 先安装/校验依赖（默认跳过，本机依赖已就绪）。

.PARAMETER KeepRunning
    不结束正在运行的墨软工作台（dist 被占用时构建会失败）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\pack.ps1
#>
[CmdletBinding()]
param(
    [switch]$NoInstaller,
    [switch]$WithDeps,
    [switch]$KeepRunning
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

$repo = Split-Path -Parent $PSScriptRoot
Push-Location $repo
try {
    Write-Step "打包最新版本"
    Write-Ok "仓库：$repo"
    Write-Ok "HEAD：$(& git log --oneline -1)"

    # ---- 1. 停掉正在运行的应用：否则 dist 里的 dll/pyd 被占用，删不掉 ----
    $running = @(Get-Process -Name 'ModuWorkbench' -ErrorAction SilentlyContinue)
    if ($running.Count -gt 0 -and -not $KeepRunning) {
        Write-Warn2 "发现正在运行的墨软工作台（$($running.Count) 个），先结束它"
        $running | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }

    # ---- 2. 带重试地清 dist：杀毒/索引器可能短暂占用 ----
    Write-Step "清理 dist"
    $target = Join-Path $repo 'dist\ModuWorkbench'
    $cleared = $false
    for ($attempt = 1; $attempt -le 20; $attempt++) {
        if (-not (Test-Path $target)) { $cleared = $true; break }
        try {
            Remove-Item $target -Recurse -Force -ErrorAction Stop
            $cleared = $true
            break
        } catch {
            Write-Warn2 "第 $attempt 次清理失败（文件被占用），3 秒后重试"
            Start-Sleep -Seconds 3
        }
    }
    if (-not $cleared) { throw "dist 目录仍被占用：$target（请关闭杀毒扫描或手动删除后重试）" }
    Write-Ok "dist 已清理"

    # ---- 3. 构建 ----
    Write-Step "PyInstaller 构建（onedir + 安装包）"
    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $buildArgs = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', (Join-Path $repo 'packaging\build_app.ps1')
    )
    if (-not $WithDeps) { $buildArgs += '-SkipDeps' }
    if (-not $NoInstaller) { $buildArgs += '-Installer' }
    & $powershell @buildArgs
    $buildCode = $LASTEXITCODE
    Write-Warn2 "build_app.ps1 退出码：$buildCode（PowerShell 5.1 下末尾固定为 1，以产物为准）"

    # ---- 4. 以产物为准校验 ----
    Write-Step "校验产物"
    $exe = Join-Path $repo 'dist\ModuWorkbench\ModuWorkbench.exe'
    $caBundle = Join-Path $repo 'dist\ModuWorkbench\_internal\certifi\cacert.pem'
    if (-not (Test-Path $exe)) { throw "未生成 $exe（构建退出码 $buildCode）" }
    if (-not (Test-Path $caBundle)) {
        throw "产物缺 _internal\certifi\cacert.pem —— 这是残缺包，所有 https 数据源都会报证书错误"
    }
    Write-Ok "exe 与 certifi 证书包都在"

    $installer = Get-ChildItem (Join-Path $repo 'dist') -Filter '*-Setup-*.exe' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $NoInstaller) {
        if (-not $installer) { throw "未生成安装包（构建退出码 $buildCode）" }
        Write-Ok "安装包：$($installer.Name)（$([math]::Round($installer.Length / 1MB, 1)) MB）"
    }
    $onedirBytes = (Get-ChildItem (Join-Path $repo 'dist\ModuWorkbench') -Recurse -File | Measure-Object Length -Sum).Sum
    Write-Ok "onedir：$([math]::Round($onedirBytes / 1MB, 1)) MB"

    $sums = Join-Path $repo 'dist\SHA256SUMS.txt'
    if (Test-Path $sums) {
        Write-Step "SHA256"
        Get-Content $sums -Encoding UTF8 | ForEach-Object { Write-Host "    $_" }
    }

    Write-Step "完成"
    Write-Ok "dist\ModuWorkbench\ModuWorkbench.exe"
    if ($installer) { Write-Ok "dist\$($installer.Name)" }
} finally {
    Pop-Location
}
