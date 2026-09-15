# 构建内嵌前端（main 分支 React 产物）→ src/modu_workbench/webfront
#
# 说明：
#   - 只安装渲染进程真正需要的依赖（转换/预览逻辑由 Python 侧实现，
#     Electron 主进程的 sharp/docx/pdf-parse 等依赖不参与 WebView 构建）
#   - 依赖装在仓库根 .webfront-build/（已 gitignore），构建期通过目录联接
#     暴露为 archive/electron-formatflow/node_modules，构建后移除联接
#   - 产物交给 modu_workbench.services.web_prepare 整理（写入 qtwebchannel.js、
#     bridge_shim.js、注入版本号）
#
# 用法：
#   pwsh -File packaging/build_webfront.ps1              # 完整构建
#   pwsh -File packaging/build_webfront.ps1 -SkipInstall # 复用已有依赖
#   pwsh -File packaging/build_webfront.ps1 -KeepLink    # 保留 node_modules 联接
param(
    [switch]$SkipInstall,
    [switch]$KeepLink
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$buildDir = Join-Path $repoRoot ".webfront-build"
$appRoot = Join-Path $repoRoot "archive\electron-formatflow"
$webfront = Join-Path $repoRoot "src\modu_workbench\webfront"
$linkPath = Join-Path $appRoot "node_modules"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Write-TextFile([string]$Path, [string]$Text) {
    [System.IO.File]::WriteAllText($Path, $Text, $utf8NoBom)
}

# 调用外部命令并返回退出码：npm/vite 会往 stderr 写 notice，
# Windows PowerShell 5.1 在 EAP=Stop 下会把 stderr 当成终止性错误，故局部放宽。
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

if (-not (Test-Path $appRoot)) { throw "前端源码目录不存在：$appRoot" }

New-Item -ItemType Directory -Force -Path $buildDir | Out-Null

# 1) 生成最小构建清单（每次覆盖，保证可复现）
$packageJson = @'
{
  "name": "modu-webfront-build",
  "private": true,
  "type": "module",
  "description": "内嵌前端构建壳（依赖仅覆盖渲染进程）",
  "dependencies": {
    "dompurify": "^3.4.14",
    "lucide-react": "^0.541.0",
    "marked": "^18.0.4",
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.5.2",
    "vite": "^6.3.5"
  }
}
'@
Write-TextFile (Join-Path $buildDir "package.json") $packageJson

# 2) Vite 配置：根指向归档源码，输出到构建临时目录
$viteConfig = @'
import path from "node:path";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const here = path.dirname(fileURLToPath(import.meta.url));
const appRoot = path.resolve(here, "..", "archive", "electron-formatflow");

export default defineConfig({
  base: "./",
  root: appRoot,
  plugins: [react()],
  build: {
    outDir: path.join(here, "dist"),
    emptyOutDir: true,
    target: "chrome110",
    sourcemap: false
  }
});
'@
Write-TextFile (Join-Path $buildDir "vite.web.mjs") $viteConfig

# 3) 安装依赖
if (-not $SkipInstall) {
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) { $npm = Get-Command npm -ErrorAction SilentlyContinue }
    if (-not $npm) { throw "未找到 npm，请先安装 Node.js" }
    Write-Host "[1/4] npm install -> $buildDir"
    $code = Invoke-Native $npm.Source @("install", "--no-audit", "--no-fund", "--prefix", $buildDir)
    if ($code -ne 0) { throw "npm install 失败（exit $code）" }
} else {
    Write-Host "[1/4] 跳过 npm install"
}

$nodeModules = Join-Path $buildDir "node_modules"
if (-not (Test-Path $nodeModules)) { throw "缺少 $nodeModules，去掉 -SkipInstall 重新执行" }

# 4) 目录联接（Windows 普通用户即可创建 Junction）
if (Test-Path $linkPath) {
    Invoke-Native "cmd" @("/c", "rmdir", $linkPath) | Out-Null
}
Write-Host "[2/4] 链接 node_modules -> $linkPath"
New-Item -ItemType Junction -Path $linkPath -Target $nodeModules | Out-Null

try {
    Push-Location $appRoot
    Write-Host "[3/4] vite build"
    $viteJs = Join-Path $nodeModules "vite\bin\vite.js"
    $code = Invoke-Native "node" @($viteJs, "build", "--config", (Join-Path $buildDir "vite.web.mjs"))
    if ($code -ne 0) { throw "vite build 失败（exit $code）" }
} finally {
    Pop-Location
    if (-not $KeepLink -and (Test-Path $linkPath)) { Invoke-Native "cmd" @("/c", "rmdir", $linkPath) | Out-Null }
}

# 5) 整理 webfront
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
Write-Host "[4/4] web_prepare -> $webfront"
$env:PYTHONPATH = Join-Path $repoRoot "src"
$code = Invoke-Native $python @("-m", "modu_workbench.services.web_prepare", (Join-Path $buildDir "dist"), $webfront)
if ($code -ne 0) { throw "web_prepare 失败（exit $code）" }

Write-Host "完成：$webfront"
