"""品牌图形测试：应用图标与安装包图形随包可用，且被打包/安装脚本引用。"""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image

from modu_workbench.services import assets

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS = REPO_ROOT / "src" / "modu_workbench" / "assets"


def test_icon_assets_exist_with_expected_sizes() -> None:
    ico = Image.open(ASSETS / "app.ico")
    sizes = {(w, h) for w, h in ico.info.get("sizes", [])}
    # Windows 需要的常见尺寸都在（小图标可读性依赖 16/32/48）
    assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= sizes, sizes

    png = Image.open(ASSETS / "app.png")
    assert png.size == (512, 512)
    assert png.mode == "RGBA"

    header = Image.open(ASSETS / "installer_header.bmp")
    assert header.size == (150, 57)          # NSIS 页眉位图尺寸
    welcome = Image.open(ASSETS / "installer_welcome.bmp")
    assert welcome.size == (164, 314)        # NSIS 欢迎页位图尺寸


def test_icon_has_blue_mark_and_white_backdrop() -> None:
    image = Image.open(ASSETS / "app.png").convert("RGB")
    palette = image.getcolors(maxcolors=1 << 20) or []
    total = sum(count for count, _color in palette)
    blue = sum(count for count, (r, g, b) in palette if b > 150 and b - r > 40)
    white = sum(count for count, (r, g, b) in palette if r > 240 and g > 240 and b > 240)
    assert total > 0
    assert 0.03 < blue / total < 0.30, "蓝色标记面积异常"
    assert white / total > 0.35, "白色底面积不足"


def test_assets_resolution_helpers() -> None:
    icon = assets.app_icon_path()
    assert icon is not None and icon.is_file()
    assert assets.asset_path("app.png") is not None
    assert assets.asset_path("missing-file.png") is None


def test_window_icon_applied() -> None:
    from PySide6.QtWidgets import QApplication

    from modu_workbench.ui_kit.theme import apply_theme

    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    icon = app.windowIcon()
    assert not icon.isNull()
    assert icon.availableSizes(), "图标未包含任何尺寸"


def test_specs_and_installer_reference_assets() -> None:
    spec = (REPO_ROOT / "workbench.spec").read_text(encoding="utf-8")
    assert 'icon="src/modu_workbench/assets/app.ico"' in spec
    assert '("src/modu_workbench/assets", "modu_workbench/assets")' in spec

    mac_spec = (REPO_ROOT / "workbench_mac.spec").read_text(encoding="utf-8")
    assert "src/modu_workbench/assets" in mac_spec

    nsi = (REPO_ROOT / "packaging" / "installer.nsi").read_text(encoding="utf-8-sig")
    assert re.search(r'!define MUI_ICON "\$\{ASSETS_DIR\}\\app\.ico"', nsi)
    assert "MUI_HEADERIMAGE_BITMAP" in nsi
    assert "MUI_WELCOMEFINISHPAGE_BITMAP" in nsi


def test_build_scripts_keep_utf8_bom() -> None:
    """NSIS 与 Windows PowerShell 5.1 都依赖 UTF-8 BOM，丢了会导致打包失败。"""
    for name in (
        "packaging/installer.nsi",
        "packaging/build_installer.ps1",
    ):
        raw = (REPO_ROOT / name).read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf"), f"{name} 缺少 UTF-8 BOM"
