"""EXIF 读取：拍摄时间、相机、镜头、光圈/快门/ISO、GPS、方向。

用 Pillow 自带的 ExifTags 解析，不引入额外依赖。
返回给界面的都是「可直接显示」的中文友好文本，同时保留原始 JSON 供详情查看。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ExifTags

# 需要提取并转成中文标签的字段
_FIELD_LABELS = {
    "Make": "厂商",
    "Model": "机型",
    "LensModel": "镜头",
    "FNumber": "光圈",
    "ExposureTime": "快门",
    "ISOSpeedRatings": "ISO",
    "FocalLength": "焦距",
    "FocalLengthIn35mmFilm": "等效焦距",
    "ExposureProgram": "曝光程序",
    "MeteringMode": "测光模式",
    "Flash": "闪光灯",
    "WhiteBalance": "白平衡",
    "Orientation": "方向",
    "Software": "软件",
    "Artist": "作者",
    "Copyright": "版权",
}

_EXPOSURE_PROGRAMS = {
    0: "未定义", 1: "手动", 2: "程序自动", 3: "光圈优先", 4: "快门优先",
    5: "创意模式", 6: "动作模式", 7: "人像模式", 8: "风景模式",
}
_METERING_MODES = {
    0: "未知", 1: "平均", 2: "中央重点", 3: "点测光", 4: "多点", 5: "评价测光", 6: "局部",
}
_FLASH = {0x0: "未闪光", 0x1: "闪光", 0x5: "闪光(未检测到返回光)", 0x7: "闪光(检测到返回光)",
          0x9: "强制闪光", 0x10: "关闭闪光", 0x18: "自动(未闪光)", 0x19: "自动(闪光)"}
_WHITE_BALANCE = {0: "自动", 1: "手动"}


@dataclass
class ExifData:
    """解析后的 EXIF 摘要（display 用于界面，raw 用于详情/排错）。"""

    taken_at: int = 0
    camera: str = ""
    lens: str = ""
    gps_lat: float | None = None
    gps_lon: float | None = None
    orientation: int = 1
    display: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @property
    def has_gps(self) -> bool:
        return self.gps_lat is not None and self.gps_lon is not None

    @property
    def gps_text(self) -> str:
        if not self.has_gps:
            return ""
        return f"{self.gps_lat:.6f}, {self.gps_lon:.6f}"

    def to_json(self) -> str:
        return json.dumps(
            {"display": self.display, "raw": self.raw, "gps": self.gps_text},
            ensure_ascii=False,
        )


def _to_float(value) -> float | None:  # noqa: ANN001
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_exposure(value) -> str:  # noqa: ANN001
    """快门：把 1/250 这类有理数格式化成「1/250 s」。"""
    try:
        numerator, denominator = value.numerator, value.denominator
    except AttributeError:
        number = _to_float(value)
        return f"{number:g} s" if number else ""
    if not numerator or not denominator:
        return ""
    if numerator >= denominator:
        return f"{numerator / denominator:g} s"
    return f"1/{round(denominator / numerator)} s"


def _format_field(key: str, value):  # noqa: ANN001
    if key == "FNumber":
        number = _to_float(value)
        return f"f/{number:g}" if number else ""
    if key == "ExposureTime":
        return _format_exposure(value)
    if key in ("FocalLength", "FocalLengthIn35mmFilm"):
        number = _to_float(value)
        return f"{number:g} mm" if number else ""
    if key == "ISOSpeedRatings":
        if isinstance(value, (list, tuple)):
            return ", ".join(str(v) for v in value)
        return str(value)
    if key == "ExposureProgram":
        return _EXPOSURE_PROGRAMS.get(int(value), str(value))
    if key == "MeteringMode":
        return _METERING_MODES.get(int(value), str(value))
    if key == "Flash":
        return _FLASH.get(int(value), str(value))
    if key == "WhiteBalance":
        return _WHITE_BALANCE.get(int(value), str(value))
    if key == "Orientation":
        return str(value)
    text = str(value).strip()
    return text


def _gps_to_degrees(value) -> float | None:  # noqa: ANN001
    """EXIF GPS 的 (度, 分, 秒) 有理数组 → 十进制度。"""
    try:
        degrees, minutes, seconds = (float(part) for part in value)
    except (TypeError, ValueError):
        return None
    return degrees + minutes / 60 + seconds / 3600


def _parse_datetime(text: str) -> int:
    """EXIF 时间形如 '2023:07:18 15:04:05'。"""
    import time

    for pattern in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return int(time.mktime(time.strptime(str(text).strip(), pattern)))
        except ValueError:
            continue
    return 0


def read_exif(path: str | Path) -> ExifData:
    """读取 EXIF；任何异常都退化为空结果（绝不因坏 EXIF 阻断导入）。"""
    data = ExifData()
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            if not exif:
                return data
            # 取到的是数字 tag，先映射成名字
            named: dict = {}
            for tag_id, value in exif.items():
                named[ExifTags.TAGS.get(tag_id, str(tag_id))] = value

            data.orientation = int(named.get("Orientation") or 1)

            make = str(named.get("Make") or "").strip()
            model = str(named.get("Model") or "").strip()
            # 机型常已包含厂商前缀，避免「Canon Canon EOS R5」
            if make and model.lower().startswith(make.lower()):
                data.camera = model
            elif make or model:
                data.camera = f"{make} {model}".strip()
            data.lens = str(named.get("LensModel") or "").strip()

            for key in _FIELD_LABELS:
                if key in named:
                    text = _format_field(key, named[key])
                    if text:
                        data.display[_FIELD_LABELS[key]] = text

            for key in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
                stamp = named.get(key)
                if stamp:
                    data.taken_at = _parse_datetime(str(stamp))
                    if data.taken_at:
                        data.display["拍摄时间"] = str(stamp)
                        break

            gps = exif.get_ifd(ExifTags.IFD.GPSInfo) or {}
            if gps:
                lat = _gps_to_degrees(gps.get(2))       # GPSLatitude
                lon = _gps_to_degrees(gps.get(4))       # GPSLongitude
                if lat is not None and str(gps.get(1, "N")).upper().startswith("S"):
                    lat = -lat
                if lon is not None and str(gps.get(3, "E")).upper().startswith("W"):
                    lon = -lon
                data.gps_lat, data.gps_lon = lat, lon
                if data.has_gps:
                    data.display["定位"] = data.gps_text

            # 原始值保留（数值型统一转成可 JSON 化的形式）
            for key, value in named.items():
                try:
                    json.dumps(value)
                    data.raw[key] = value
                except (TypeError, ValueError):
                    data.raw[key] = str(value)
    except Exception:  # noqa: BLE001  坏图/无 EXIF 一律静默
        return data
    return data


__all__ = ["ExifData", "read_exif"]
