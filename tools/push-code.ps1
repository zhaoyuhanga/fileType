<#
.SYNOPSIS
    提交并推送：暂存 → 提交 → 探活代理 →（必要时先变基）→ push → 复核远端。

.DESCRIPTION
    这台机器 push 经常卡在代理上（Connection reset / TLS EOF），手动重试很烦。
    本脚本把整套动作固化：

      1. `git add -u`（已跟踪文件的增删改）+ `-Include` 指定的新文件；
      2. 用 `-Message` 提交（没给信息且确实有改动时直接报错，不生成垃圾信息）；
      3. 探活代理后 fetch；落后就先 `git rebase origin/<branch>`；
      4. push 失败会重新探活/再 fetch 重试，最多 3 轮；
      5. 最后用 `git ls-remote` 复核远端 SHA 与本地 HEAD 一致。

.PARAMETER Message
    提交信息（必填，除非没有改动）。

.PARAMETER Include
    额外要加入提交的路径（新文件必须显式列出来，避免把无关杂物带进去）。

.PARAMETER Proxy
    手动指定代理；默认自动探测。

.PARAMETER NoVerify
    跳过最后的 ls-remote 复核（网络太差时用）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\push-code.ps1 -Message "feat: xxx" -Include tools\pack.ps1
#>
[CmdletBinding()]
param(
    [string]$Message = "",
    [string[]]$Include = @(),
    [string]$Proxy = "",
    [switch]$NoVerify
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

# 从命令行传 `-Include a,b,c` 时 PowerShell 会把它当成一个字符串传进来，
# 这里统一按逗号拆开（路径里真带逗号的情况极少，先不处理）。
if ($Include.Count -gt 0) {
    $Include = @($Include | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

$repo = Split-Path -Parent $PSScriptRoot
Push-Location $repo
try {
    Write-Step "提交并推送"
    Write-Ok "仓库：$repo"

    Write-Step "暂存改动"
    & git add -u
    if ($Include.Count -gt 0) {
        & git add -- $Include
        if ($LASTEXITCODE -ne 0) { throw "git add 失败：$($Include -join ', ')" }
    }
    $staged = (& git diff --cached --name-only).Trim()
    if ([string]::IsNullOrWhiteSpace($staged)) {
        Write-Warn2 "没有可提交的改动（未跟踪文件不会自动加入，请用 -Include 指定）"
        exit 0
    }
    Write-Host $staged
    if ([string]::IsNullOrWhiteSpace($Message)) {
        throw "有改动但没有 -Message：请给出提交信息（如 'feat(video): ...'）"
    }

    Write-Step "提交"
    & git commit -m $Message
    if ($LASTEXITCODE -ne 0) { throw "git commit 失败" }
    $head = (& git rev-parse HEAD).Trim()
    Write-Ok "本地 HEAD：$head"

    $proxyUrl = if ($Proxy) { $Proxy } else { Resolve-WorkingProxy }
    if (-not $proxyUrl) { throw "找不到可用代理，无法推送（本地提交已保留）" }
    Write-Ok "代理：$proxyUrl"

    $branch = Get-CurrentBranch
    $pushed = $false
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        Write-Step "推送（第 $attempt 轮）"
        if (-not (Test-ProxyUrl $proxyUrl)) {
            Write-Warn2 "代理暂时不通，10 秒后重试"
            Start-Sleep -Seconds 10
            continue
        }
        $code = Invoke-GitProxy $proxyUrl @('fetch', 'origin', $branch)
        if ($code -ne 0) { Write-Warn2 "fetch 失败，10 秒后重试"; Start-Sleep -Seconds 10; continue }

        $behind = Get-BehindCount $branch
        if ($behind -gt 0) {
            Write-Warn2 "远端领先 $behind 个提交，先变基"
            & git rebase "origin/$branch"
            if ($LASTEXITCODE -ne 0) { throw "rebase 失败：请手动解决冲突后再跑" }
            $head = (& git rev-parse HEAD).Trim()
        }

        $code = Invoke-GitProxy $proxyUrl @('push', '--no-thin', 'origin', $branch)
        if ($code -eq 0) { $pushed = $true; break }
        Write-Warn2 "push 失败（退出码 $code），10 秒后重试"
        Start-Sleep -Seconds 10
    }
    if (-not $pushed) { throw "推送失败（本地提交已保留，可稍后重跑本脚本）" }

    if (-not $NoVerify) {
        Write-Step "复核远端"
        $remoteSha = (& git -c "http.proxy=$proxyUrl" -c 'http.version=HTTP/1.1' ls-remote origin "refs/heads/$branch" 2>$null)
        if ($remoteSha) {
            $remoteSha = ($remoteSha -split '\s+')[0]
        }
        if ($remoteSha -ne $head) {
            throw "远端 SHA（$remoteSha）与本地 HEAD（$head）不一致，推送可能没真正落库"
        }
        Write-Ok "远端 $branch = $head"
    }

    Write-Step "完成"
    Write-Ok "$(& git log --oneline -1)"
} finally {
    Pop-Location
}
