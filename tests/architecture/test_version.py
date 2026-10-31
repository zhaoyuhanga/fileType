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


def _version_tuple() -> tuple[int, ...]:
    return tuple(int(part) for part in __version__.split(".")) + (0,) * (4 - len(__version__.split(".")))


def test_installer_declares_version_resource() -> None:
    """安装包 exe 必须带版本资源，且版本取自 ${APP_VERSION}（不能写死）。"""
    nsi = (REPO_ROOT / "packaging" / "installer.nsi").read_text(encoding="utf-8-sig")
    assert 'VIProductVersion "${APP_VERSION}.0"' in nsi, "缺少四段式 VIProductVersion"
    assert 'VIAddVersionKey "FileVersion" "${APP_VERSION}"' in nsi
    assert 'VIAddVersionKey "ProductVersion" "${APP_VERSION}"' in nsi
    # 版本资源里不应出现写死的三段式版本号
    assert not re.search(r'VIAddVersionKey\s+"\w+"\s+"\d+\.\d+\.\d+"', nsi)


def test_exe_version_resource_matches_package() -> None:
    """PyInstaller 版本资源（packaging/version_info.txt）必须与包版本一致。"""
    spec = (REPO_ROOT / "workbench.spec").read_text(encoding="utf-8")
    assert 'version="packaging/version_info.txt"' in spec, "workbench.spec 未挂载版本资源"

    text = (REPO_ROOT / "packaging" / "version_info.txt").read_text(encoding="utf-8")
    wanted = _version_tuple()
    for key in ("filevers", "prodvers"):
        found = re.search(rf"{key}=\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)", text)
        assert found, f"version_info.txt 缺少 {key}"
        assert tuple(int(g) for g in found.groups()) == wanted, f"{key} 与 __version__ 不一致"
    for key in ("FileVersion", "ProductVersion"):
        found = re.search(rf'StringStruct\("{key}",\s*"([^"]+)"\)', text)
        assert found, f"version_info.txt 缺少 {key}"
        assert found.group(1) == __version__, f"{key} 与 __version__ 不一致"
