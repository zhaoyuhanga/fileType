# 墨软工作台 · 工作流脚本公共部分（被 update-code / push-code / pack 引用）
#
# 本机访问 GitHub 必须走代理。这里统一解决两件事：
#   1. 用哪个代理：环境变量 → Windows 系统代理 → 常见本地端口（7890/7891/7892）；
#   2. 带代理跑 git：统一 HTTP/1.1（代理下 HTTP/2 容易握手失败）。
#
# 只依赖 Windows 自带的 PowerShell / git / curl，不需要安装任何东西。

function Write-Step {
    param([string]$Text)
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Text)
    Write-Host "    $Text" -ForegroundColor Green
}

function Write-Warn2 {
    param([string]$Text)
    Write-Host "    $Text" -ForegroundColor Yellow
}

function Get-SystemProxy {
    <# 读 Windows「系统代理」（设置 → 网络 → 代理），返回 http://host:port 或 $null。 #>
    try {
        $item = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction Stop
    } catch {
        return $null
    }
    if (-not $item.ProxyEnable) { return $null }
    $server = [string]$item.ProxyServer
    if ([string]::IsNullOrWhiteSpace($server)) { return $null }
    # 支持 "127.0.0.1:7890" 与 "http=...;https=...;socks=..." 两种写法
    if ($server.Contains('=')) {
        $https = $null
        $http = $null
        foreach ($part in $server.Split(';')) {
            $index = $part.IndexOf('=')
            if ($index -le 0) { continue }
            $scheme = $part.Substring(0, $index).Trim().ToLower()
            $value = $part.Substring($index + 1).Trim()
            if ($scheme -eq 'https') { $https = $value }
            elseif ($scheme -eq 'http') { $http = $value }
        }
        if ($https) { $server = $https } elseif ($http) { $server = $http } else { $server = $null }
    }
    if ([string]::IsNullOrWhiteSpace($server)) { return $null }
    if (-not $server.Contains('://')) { $server = "http://$server" }
    return $server
}

function Test-ProxyUrl {
    <# 用 curl 探一次 github.com：返回 $true 表示这条代理现在是通的。 #>
    param([string]$ProxyUrl)
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if (-not $curl) { return $false }
    $code = & $curl.Source -s -o NUL -w '%{http_code}' --max-time 8 -x $ProxyUrl https://github.com 2>$null
    return ($code -eq '200')
}

function Resolve-WorkingProxy {
    <# 按"环境变量 → 系统代理 → 常见端口"的顺序挑第一条能用的代理。 #>
    param([string]$Preferred = '')
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($Preferred) { $candidates.Add($Preferred) }
    foreach ($name in @('HTTPS_PROXY', 'HTTP_PROXY', 'https_proxy', 'http_proxy')) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ($value) { $candidates.Add($value) }
    }
    $system = Get-SystemProxy
    if ($system) { $candidates.Add($system) }
    foreach ($port in @(7890, 7891, 7892)) { $candidates.Add("http://127.0.0.1:$port") }

    $seen = @{}
    foreach ($candidate in $candidates) {
        if ($seen.ContainsKey($candidate)) { continue }
        $seen[$candidate] = $true
        Write-Host "    探测代理 $candidate ..." -ForegroundColor DarkGray
        if (Test-ProxyUrl $candidate) { return $candidate }
    }
    return $null
}

function Invoke-GitProxy {
    <# 带代理跑 git，返回退出码（不抛异常，调用方自己判断）。 #>
    param([string]$ProxyUrl, [string[]]$GitArgs)
    $commandArgs = @('-c', "http.proxy=$ProxyUrl", '-c', 'http.version=HTTP/1.1') + $GitArgs
    & git @commandArgs
    return $LASTEXITCODE
}

function Get-CurrentBranch {
    return (& git rev-parse --abbrev-ref HEAD).Trim()
}

function Get-BehindCount {
    <# 本地落后 origin/<branch> 多少个提交；取不到时返回 0。 #>
    param([string]$Branch)
    $remote = "origin/$Branch"
    & git rev-parse --verify --quiet $remote > $null 2>&1
    if ($LASTEXITCODE -ne 0) { return 0 }
    $count = (& git rev-list --count "HEAD..$remote").Trim()
    if ([string]::IsNullOrWhiteSpace($count)) { return 0 }
    return [int]$count
}
