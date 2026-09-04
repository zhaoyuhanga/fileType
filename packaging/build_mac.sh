#!/usr/bin/env bash
# 墨读·工作台 macOS 打包脚本（在 Mac 上执行）
# 用法: bash packaging/build_mac.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> 1/4 创建虚拟环境 (python3 -m venv .venv-mac)"
python3 -m venv .venv-mac
source .venv-mac/bin/activate

echo "==> 2/4 安装项目与打包工具"
python -m pip install --upgrade pip
pip install -e .
pip install pyinstaller

echo "==> 3/4 构建 .app（PyInstaller onedir）"
python -m PyInstaller workbench_mac.spec --noconfirm --clean

echo "==> 4/4 产物"
APP="dist/ModuWorkbench.app"
if [ -d "$APP" ]; then
  echo "打包完成: $APP"
  echo
  echo "直接双击运行即可；若系统提示“来自身份不明的开发者”，执行："
  echo "  xattr -dr com.apple.quarantine \"$APP\""
  echo
  echo "如需制作 DMG 安装镜像（Mac 上执行）："
  echo "  hdiutil create -volname \"墨读工作台\" -srcfolder \"$APP\" -ov -format UDZO \"dist/墨读工作台-0.3.0-mac.dmg\""
else
  echo "打包失败：未找到 $APP" >&2
  exit 1
fi
