"""版本单一来源测试：pyproject / 包 / 安装包版本必须一致。

（原在 test_web_bridge.py 中；v1.0.0 移除内嵌前端后，前端产物不再参与版本校验。）
"""
from __future__ import annotations

import re
from pathlib import Path

from modu_workbench import __version__

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_version_single_source() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, flags=re.M)
    assert declared, "pyproject.toml 缺少 version"
    assert declared.group(1) == __version__


def test_installer_version_comes_from_package() -> None:
    """安装包版本由 build_installer.ps1 从包内 __version__ 取并传给 NSIS（单一来源）。

    NSIS 里的 `!define APP_VERSION` 只是"直接调用 makensis"时的兜底默认值，
    因此这里断言：一键打包脚本确实传版本，且当前版本不是靠 NSIS 里写死来保证的。
    """
    nsi = (REPO_ROOT / "packaging" / "installer.nsi").read_text(encoding="utf-8-sig")
    assert "APP_VERSION" in nsi
    ps1 = (REPO_ROOT / "packaging" / "build_installer.ps1").read_text(encoding="utf-8-sig")
    assert "/DAPP_VERSION" in ps1, "打包脚本应把版本通过 /DAPP_VERSION 传给 NSIS"
    assert "__version__" in ps1, "打包脚本应从包内 __version__ 取版本（单一来源）"
