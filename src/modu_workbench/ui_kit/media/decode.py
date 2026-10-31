"""解码后端与硬件加速：在 `QApplication` 之前注入 QtMultimedia 环境变量。

对应方案 V1-e / 3.1.3。**这是投入产出比最高的一处改动**：全仓此前
`QT_FFMPEG_DECODING_HW_DEVICE_TYPES` / `QT_MEDIA_BACKEND` **零命中**，
1080p H.264 基本靠软解 —— CPU 打满就是用户说的"帧率低"。

Qt 官方给的相关开关（<https://doc.qt.io/qt-6/advanced-ffmpeg-configuration.html>）：

    QT_MEDIA_BACKEND                        ffmpeg / windows / darwin ...
    QT_FFMPEG_DECODING_HW_DEVICE_TYPES      d3d11va,d3d12va,dxva2（空 = 关硬解）
    QT_DISABLE_HW_TEXTURES_CONVERSION       1 = 关闭 GPU 纹理转换（花屏兜底）
    QT_FFMPEG_HW_ALLOW_PROFILE_MISMATCH     1 = 允许 profile 不匹配（有风险）
    QT_FFMPEG_DEBUG + QT_LOGGING_RULES      诊断面板的日志来源

**环境变量是进程级的**，所以"改硬解要重开播放窗口/重启应用"是诚实的限制；
本模块的 `describe()` 会把当前生效值交出来给诊断面板显示。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

#: 硬解设备优先级（Windows 上的顺序：D3D11 → D3D12 → DXVA2）
HW_DEVICE_TYPES = "d3d11va,d3d12va,dxva2"
#: 关闭硬解时写入的"空列表"（Qt 约定：空的设备列表 = 不启用硬解）
HW_DISABLED = ","

MEDIA_BACKENDS = ("auto", "ffmpeg", "wmf")
HW_MODES = ("auto", "on", "off")

#: 本进程实际注入的值（`describe()` 用；也可被测试断言）
_APPLIED: dict[str, str] = {}
_APPLIED_BY_US: set[str] = set()


def _read_setting_from_db(key: str, default: str) -> str:
    """从应用库读 `video/<key>`（此时 Qt 还没起来，只能读库）。"""
    try:
        from modu_workbench.core.platform.paths import app_db_path

        path = Path(app_db_path())
        if not path.is_file():
            return default
        conn = sqlite3.connect(str(path))
        try:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?", (f"video/{key}",)
            ).fetchone()
        finally:
            conn.close()
        return str(row[0]) if row and row[0] not in (None, "") else default
    except Exception:  # noqa: BLE001  读不到就用默认值（绝不阻断启动）
        return default


def _read_setting_bool(key: str, default: bool) -> bool:
    raw = str(_read_setting_from_db(key, "true" if default else "false")).strip().lower()
    return raw in ("1", "true", "yes", "on")


def apply_decoding_env() -> dict[str, str]:
    """按设置注入解码相关环境变量；返回本次**由我们写入**的键值。

    用 `setdefault` 语义：用户/打包脚本已经显式设置的环境变量优先
    （便于"临时排查"与 CI），我们只填空。
    """
    backend = _read_setting_from_db("media_backend", "auto").strip().lower()
    hw_mode = _read_setting_from_db("hw_decode", "auto").strip().lower()
    texture_conversion = _read_setting_bool("hw_texture_conversion", True)
    profile_mismatch = _read_setting_bool("hw_profile_mismatch", False)
    debug = _read_setting_bool("ffmpeg_debug", False)

    # WMF（Windows Media Foundation）不支持 HLS，所以 auto 也固定 FFmpeg
    if backend == "wmf":
        os.environ.pop("QT_MEDIA_BACKEND", None)
    else:
        _setdefault("QT_MEDIA_BACKEND", "ffmpeg")

    if hw_mode == "off":
        _setdefault("QT_FFMPEG_DECODING_HW_DEVICE_TYPES", HW_DISABLED)
    else:
        _setdefault("QT_FFMPEG_DECODING_HW_DEVICE_TYPES", HW_DEVICE_TYPES)

    if not texture_conversion:
        _setdefault("QT_DISABLE_HW_TEXTURES_CONVERSION", "1")
    if profile_mismatch:
        _setdefault("QT_FFMPEG_HW_ALLOW_PROFILE_MISMATCH", "1")
    if debug:
        _setdefault("QT_FFMPEG_DEBUG", "1")
        _setdefault("QT_LOGGING_RULES", "*.ffmpeg.*=true")

    _APPLIED.update({
        "media_backend": backend,
        "hw_decode": hw_mode,
        "hw_texture_conversion": "1" if texture_conversion else "0",
        "hw_profile_mismatch": "1" if profile_mismatch else "0",
    })
    return dict(_APPLIED)


def _setdefault(key: str, value: str) -> None:
    if key in os.environ:
        return
    os.environ[key] = value
    _APPLIED_BY_US.add(key)


def current_env() -> dict[str, str]:
    """当前进程里实际生效的解码环境变量（诊断面板显示用）。"""
    return {
        "QT_MEDIA_BACKEND": os.environ.get("QT_MEDIA_BACKEND", "(默认)"),
        "QT_FFMPEG_DECODING_HW_DEVICE_TYPES": os.environ.get(
            "QT_FFMPEG_DECODING_HW_DEVICE_TYPES", "(默认)"),
        "QT_DISABLE_HW_TEXTURES_CONVERSION": os.environ.get(
            "QT_DISABLE_HW_TEXTURES_CONVERSION", "0"),
        "QT_FFMPEG_HW_ALLOW_PROFILE_MISMATCH": os.environ.get(
            "QT_FFMPEG_HW_ALLOW_PROFILE_MISMATCH", "0"),
    }


def hardware_decode_enabled() -> bool:
    """当前是否启用硬解（空设备列表 = 关闭）。"""
    value = os.environ.get("QT_FFMPEG_DECODING_HW_DEVICE_TYPES", HW_DEVICE_TYPES)
    return bool(value.strip().strip(","))


def describe() -> str:
    """一句话描述当前解码配置（状态栏/诊断面板用）。"""
    env = current_env()
    hw = env["QT_FFMPEG_DECODING_HW_DEVICE_TYPES"]
    if not hardware_decode_enabled():
        hw_text = "硬解已关闭（软解 CPU 占用较高）"
    else:
        hw_text = f"硬解优先：{hw}"
    return f"后端 {env['QT_MEDIA_BACKEND']}；{hw_text}"


def applied_by_us() -> set[str]:
    """本进程由我们写入的环境变量键（测试用）。"""
    return set(_APPLIED_BY_US)


__all__ = [
    "HW_DEVICE_TYPES", "HW_DISABLED", "HW_MODES", "MEDIA_BACKENDS",
    "applied_by_us", "apply_decoding_env", "current_env", "describe",
    "hardware_decode_enabled",
]
