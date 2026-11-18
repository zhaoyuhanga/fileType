<#
.SYNOPSIS
    更新代码：自动挑代理 → git fetch → 快进合并到最新。

.DESCRIPTION
    本机直连 GitHub 会 Connection reset，必须走代理；而代理节点会换、端口会变。
    本脚本按「环境变量 → Windows 系统代理 → 7890/7891/7892」顺序探活，
    用同一条代理 fetch，然后只在**能快进**时合并（绝不自动 merge/rebase，
    有本地改动时会明确告诉你先处理）。

.PARAMETER Proxy
    手动指定代理（如 http://127.0.0.1:7890）。默认自动探测。

.PARAMETER NoPull
    只 fetch 不合并（想先看看远端有什么时用）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\update-code.ps1
#>
[CmdletBinding()]
param(
    [string]$Proxy = "",
    [switch]$NoPull
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

$repo = Split-Path -Parent $PSScriptRoot
Push-Location $repo
try {
    Write-Step "更新代码"
    Write-Ok "仓库：$repo"

    $proxyUrl = if ($Proxy) { $Proxy } else { Resolve-WorkingProxy }
    if (-not $proxyUrl) {
        throw "找不到可用代理（直连与 7890/7891/7892 都不通）—— 请检查代理软件是否在运行"
    }
    Write-Ok "代理：$proxyUrl"

    Write-Step "git fetch"
    $code = Invoke-GitProxy $proxyUrl @('fetch', '--all', '--prune')
    if ($code -ne 0) { throw "fetch 失败（退出码 $code），代理可能已失效" }
    Write-Ok "远端引用已更新"

    $branch = Get-CurrentBranch
    $behind = Get-BehindCount $branch
    if ($behind -eq 0) {
        Write-Ok "已是最新（$branch）"
    } elseif ($NoPull) {
        Write-Warn2 "落后 origin/$branch $behind 个提交（-NoPull：只取不合并）"
    } else {
        Write-Step "快进合并 $behind 个提交"
        & git merge --ff-only "origin/$branch"
        if ($LASTEXITCODE -ne 0) {
            throw "无法快进（本地有分叉或未提交改动）：请先提交/处理后再跑"
        }
        & git log --oneline -n ([Math]::Min($behind, 10))
    }

    Write-Step "完成"
    Write-Ok "HEAD：$(& git log --oneline -1)"
    if ((& git status --porcelain)) {
        Write-Warn2 "工作区有未提交改动（脚本不会替你提交）"
    }
} finally {
    Pop-Location
}
