"""归档处理：ZIP / TAR / RAR。

- 压缩：单文件打包（保持旧版语义）；
- 解压：目录为 <输出目录>/<包名>，对条目做路径穿越防护；
- RAR：需要系统提供 unrar / 7z（rarfile 后端），否则给出中文提示。
"""
from __future__ import annotations

import shutil
import tarfile
import zipfile
from pathlib import Path

_ALWAYS_BLOCKED = ("..",)


def reject_unsafe_entry(entry_name: str) -> str:
    """校验归档条目，返回规范化（去分隔符歧义）后的相对名；不安全则抛错。"""
    normalized = entry_name.replace("\\", "/").strip("/")
    if not normalized or normalized.startswith(("/", "\\")):
        raise ValueError(f"归档包含不安全的路径项，已拒绝写入：{entry_name}")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if not parts or any(part in _ALWAYS_BLOCKED for part in parts):
        raise ValueError(f"归档包含越界路径（..），已拒绝写入：{entry_name}")
    if ":" in parts[0]:
        raise ValueError(f"归档包含盘符路径项，已拒绝写入：{entry_name}")
    return "/".join(parts)


def _safe_destination(output_dir: Path, relative: str) -> Path:
    target = (output_dir / relative).resolve()
    root = output_dir.resolve()
    if root not in target.parents and target != root:
        raise ValueError(f"归档路径越出目标目录，已拒绝写入：{relative}")
    return target


# ---------- ZIP ----------

def compress_zip(source_file: Path, output_path: Path) -> None:
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(source_file, arcname=source_file.name)


def extract_zip(archive: Path, output_dir: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            relative = reject_unsafe_entry(member.filename)
            target = _safe_destination(output_dir, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)


# ---------- TAR ----------

def compress_tar(source_file: Path, output_path: Path) -> None:
    with tarfile.open(output_path, "w") as tf:
        tf.add(source_file, arcname=source_file.name)


def extract_tar(archive: Path, output_dir: Path) -> None:
    with tarfile.open(archive) as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            relative = reject_unsafe_entry(member.name)
            target = _safe_destination(output_dir, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            with tf.extractfile(member) as src, open(target, "wb") as dst:  # type: ignore[union-attr]
                shutil.copyfileobj(src, dst)


# ---------- RAR ----------

def _rar_tool() -> str:
    for tool in ("unrar", "7z", "7za", "bsdtar"):
        if shutil.which(tool):
            return tool
    raise RuntimeError("解压 RAR 需要本机安装 unrar 或 7-Zip（未检测到可用工具）")


def extract_rar(archive: Path, output_dir: Path) -> None:
    import rarfile

    rarfile.UNRAR_TOOL = _rar_tool()
    with rarfile.RarFile(str(archive)) as rf:
        for info in rf.infolist():
            if info.isdir():
                continue
            relative = reject_unsafe_entry(info.filename)
            target = _safe_destination(output_dir, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            with rf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
