#!/usr/bin/env bash
# 构建内嵌前端（main 分支 React 产物）→ src/modu_workbench/webfront
#
# 用法：
#   packaging/build_webfront.sh              # 完整构建
#   packaging/build_webfront.sh --skip-install
set -euo pipefail

SKIP_INSTALL=0
for arg in "$@"; do
    case "$arg" in
        --skip-install) SKIP_INSTALL=1 ;;
        -h|--help) sed -n '1,8p' "$0"; exit 0 ;;
        *) echo "未知参数：$arg" >&2; exit 2 ;;
    esac
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
build_dir="$repo_root/.webfront-build"
app_root="$repo_root/archive/electron-formatflow"
webfront="$repo_root/src/modu_workbench/webfront"
link_path="$app_root/node_modules"

[ -d "$app_root" ] || { echo "前端源码目录不存在：$app_root" >&2; exit 1; }
mkdir -p "$build_dir"

cat > "$build_dir/package.json" <<'JSON'
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
JSON

cat > "$build_dir/vite.web.mjs" <<'JS'
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
JS

if [ "$SKIP_INSTALL" -eq 0 ]; then
    command -v npm >/dev/null || { echo "未找到 npm，请先安装 Node.js" >&2; exit 1; }
    echo "[1/4] npm install -> $build_dir"
    npm install --no-audit --no-fund --prefix "$build_dir"
else
    echo "[1/4] 跳过 npm install"
fi

[ -d "$build_dir/node_modules" ] || { echo "缺少 $build_dir/node_modules" >&2; exit 1; }

echo "[2/4] 链接 node_modules -> $link_path"
[ -e "$link_path" ] && rm -rf "$link_path"
ln -s "$build_dir/node_modules" "$link_path"
cleanup() { [ -L "$link_path" ] && rm -f "$link_path" || true; }
trap cleanup EXIT

echo "[3/4] vite build"
( cd "$app_root" && node "$build_dir/node_modules/vite/bin/vite.js" build --config "$build_dir/vite.web.mjs" )

echo "[4/4] web_prepare -> $webfront"
python_bin="$repo_root/.venv/bin/python"
[ -x "$python_bin" ] || python_bin="python3"
PYTHONPATH="$repo_root/src" "$python_bin" -m modu_workbench.services.web_prepare "$build_dir/dist" "$webfront"

echo "完成：$webfront"
