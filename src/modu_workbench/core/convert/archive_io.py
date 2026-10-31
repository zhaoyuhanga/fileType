"""归档处理：ZIP / TAR / GZ / BZ2 / XZ / RAR。

- 压缩：单文件打包（保持旧版语义）——zip 与 tar；tar 另支持 gz/bz2/xz 变体；
  gz/bz2/xz 为「单文件压缩」（不打包目录）；
- 解压：目录为 <输出目录>/<包名>，对条目做路径穿越防护；
  单文件解压默认解到 <输出目录>/<包名>/ 下的原始文件名。

单文件压缩的「文件名保持」约定（compress_single / extract_single 成对使用）：

- gz：把原名写进 gzip 头（RFC 1952 的 FNAME 字段），解压时优先读头里的原名。
  注意标准库 gzip 按 Latin-1 写 FNAME，中文名会被静默丢弃，所以这里写入的是
  「原名的 UTF-8 字节」（GNU gzip 等工具同样按 UTF-8 显示）；
  读不到 FNAME（外部工具产生、或名字里带非 Latin-1 的历史文件）时，
  回退到「去掉 .gz 的包名」。
- bz2 / xz：格式本身没有文件名字段，约定回退为「去掉 .bz2 / .xz 的包名」，
  例如 报告.txt.bz2 → 报告.txt，与压缩前的名字一致。

- RAR：需要系统提供 unrar / 7z（rarfile 后端），否则给出中文提示。
"""
from __future__ import annotations

import bz2
import gzip
import lzma
import shutil
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

_ALWAYS_BLOCKED = ("..",)

# 单文件压缩：算法 → tarfile.open 之外的标准库参数
_TAR_MODES = {"": "w", "gz": "w:gz", "bz2": "w:bz2", "xz": "w:xz"}
_SINGLE_ALGORITHMS = ("gz", "bz2", "xz")
_FALLBACK_STEM = "解压结果"  # 去掉压缩后缀后名字为空时的兜底名


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


def _unique_file(output_dir: Path, name: str) -> Path:
    """已存在同名文件时不覆盖，追加序号：报告.txt → 报告 (2).txt。

    在不区分大小写的文件系统（Windows / macOS）上，"A.txt" 与 "a.txt" 是同一个文件，
    因此用 lower() 比较，避免解压覆盖已有文件。
    """
    taken = {path.name.lower() for path in output_dir.iterdir()} if output_dir.is_dir() else set()
    stem, suffix = Path(name).stem, Path(name).suffix
    candidate = name
    index = 2
    while candidate.lower() in taken:
        candidate = f"{stem} ({index}){suffix}"
        index += 1
    return output_dir / candidate


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

def compress_tar(source_file: Path, output_path: Path, *, compression: str = "") -> None:
    """把单个文件打包成 tar；compression："" / "gz" / "bz2" / "xz"。"""
    mode = _TAR_MODES.get(compression)
    if mode is None:
        raise ValueError(f"不支持的 tar 压缩方式：{compression}（可用：gz / bz2 / xz 或不压缩）")
    try:
        with tarfile.open(output_path, mode) as tf:
            tf.add(source_file, arcname=source_file.name)
    except FileNotFoundError as error:
        raise ValueError(f"找不到要压缩的文件：{source_file}") from error
    except (tarfile.TarError, OSError) as error:
        raise ValueError(f"创建 tar 归档失败（{output_path.name}）：{error}") from error


def extract_tar(archive: Path, output_dir: Path) -> None:
    """解压 tar / tar.gz / tar.bz2 / tar.xz（按内容自动识别压缩），含路径穿越防护。"""
    try:
        with tarfile.open(archive, "r:*") as tf:
            members = tf.getmembers()
            for member in members:
                if not member.isfile():
                    continue
                relative = reject_unsafe_entry(member.name)
                target = _safe_destination(output_dir, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                with tf.extractfile(member) as src, open(target, "wb") as dst:  # type: ignore[union-attr]
                    shutil.copyfileobj(src, dst)
    except (tarfile.TarError, EOFError, OSError) as error:
        raise ValueError(f"不是有效的 tar/tar.gz/tar.bz2/tar.xz 归档，或归档已损坏：{archive.name}") from error


# ---------- GZ / BZ2 / XZ（单文件） ----------

def _open_compressor(output_path: Path, algorithm: str, **extra):
    """按算法打开单文件压缩流（gz 通过 **extra 接收 filename / mtime 等参数）。"""
    if algorithm == "gz":
        return gzip.GzipFile(mode="wb", fileobj=open(output_path, "wb"), **extra)
    if algorithm == "bz2":
        return bz2.open(output_path, "wb")
    if algorithm == "xz":
        return lzma.open(output_path, "wb")
    raise ValueError(f"不支持的单文件压缩算法：{algorithm}")


def _open_decompressor(archive: Path, algorithm: str):
    """按算法打开单文件解压流；失败时给出可读的中文提示。"""
    try:
        if algorithm == "gz":
            return gzip.open(archive, "rb")
        if algorithm == "bz2":
            return bz2.open(archive, "rb")
        if algorithm == "xz":
            return lzma.open(archive, "rb")
    except (OSError, EOFError) as error:
        raise ValueError(f"不是有效的 {algorithm} 文件，或文件已损坏：{archive.name}") from error
    raise ValueError(f"不支持的单文件压缩算法：{algorithm}")


def compress_single(source_file: Path, output_path: Path, *, algorithm: str) -> None:
    """单文件压缩（gzip / bz2 / xz）。

    gz 会把原文件名写进 gzip 头；bz2 / xz 没有名字字段，解压时按包名去掉后缀还原
    （见模块 docstring 的约定）。
    """
    if algorithm not in _SINGLE_ALGORITHMS:
        raise ValueError(f"不支持的单文件压缩算法：{algorithm}（可用：gz / bz2 / xz）")
    if not source_file.is_file():
        raise ValueError(f"找不到要压缩的文件：{source_file}")
    extra = {"filename": _gzip_stored_name(source_file.name), "mtime": 0} if algorithm == "gz" else {}
    try:
        with open(source_file, "rb") as src, _open_compressor(output_path, algorithm, **extra) as dst:
            shutil.copyfileobj(src, dst)
    except (OSError, EOFError) as error:
        raise ValueError(f"创建 {algorithm} 单文件压缩失败（{output_path.name}）：{error}") from error


def _gzip_stored_name(name: str) -> str:
    """把文件名转成 gzip 头能安全写入的形式。

    gzip 按 RFC 1952 要求 FNAME 是 Latin-1，遇到无法用 Latin-1 表示的字符
    （中文文件名必然如此）会**静默丢弃**文件名。这里先把名字编成 UTF-8 字节再按
    Latin-1 解码，gzip 写出的字节就正好是原名的 UTF-8 编码；
    `_gzip_header_name` 读取时反向解码即可还原（GNU gzip 等工具也会显示为原名）。

    另外 gzip 会把结尾的 `.gz` 从名字里剥掉，所以先自己剥掉，写头时正好还原原名。
    """
    try:
        stripped = name
        while stripped.lower().endswith(".gz"):
            stripped = stripped[:-3]
        return stripped.encode("utf-8").decode("latin-1")
    except (UnicodeEncodeError, UnicodeDecodeError):  # pragma: no cover - 理论兜底
        return ""


def _gzip_header_name(archive: Path) -> str:
    """读 gzip 头里的原始文件名（RFC 1952 的 FNAME 字段），读不到返回 ""。

    注意：不能用 `gzip.open(archive).name` —— 那返回的是归档自身的路径
    （gzip 模块把「数据源名」当成原名），拿不到头里记录的名字。
    """
    _FNAME = 0x08  # FLG 里的「存在原始文件名」标志
    try:
        with open(archive, "rb") as stream:
            header = stream.read(10)
            if len(header) < 10 or header[:2] != b"\x1f\x8b":
                return ""
            flags = header[3]
            if not flags & _FNAME:
                return ""
            if flags & 0x04:  # FEXTRA：2 字节长度 + 数据，跳过它才能读到 FNAME
                extra_len = int.from_bytes(stream.read(2), "little")
                stream.seek(10 + extra_len)
            raw = b""
            while len(raw) < 4096:  # FNAME 以 \0 结尾，限制长度防止异常文件拖死
                chunk = stream.read(1)
                if not chunk or chunk == b"\x00":
                    break
                raw += chunk
    except OSError:
        return ""
    name = raw.decode("utf-8", errors="replace").replace("\\", "/")
    return PurePosixPath(name).name if name and name != "\ufffd" else ""


def _single_output_name(archive: Path, algorithm: str) -> str:
    """还原单文件压缩里的原名：gz 读头，读不到或 bz2/xz 则回退为去掉压缩后缀的包名。"""
    if algorithm == "gz":
        original = _gzip_header_name(archive)
        if original:
            return original
    name = archive.name
    suffix = f".{algorithm}"
    if name.lower().endswith(suffix):
        name = name[: -len(suffix)]
    name = name.strip()
    return name or _FALLBACK_STEM


def extract_single(archive: Path, output_dir: Path) -> Path:
    """解压 gz / bz2 / xz 为单个文件，返回产物路径。

    产出目录会自动创建；同名文件不覆盖（追加序号）；产出的名字同样过
    `reject_unsafe_entry`，绝不写到 output_dir 之外。
    """
    algorithm = archive.suffix.lower().lstrip(".")
    if algorithm not in _SINGLE_ALGORITHMS:
        raise ValueError(f"不支持的单文件解压格式：{archive.name}（可用：gz / bz2 / xz）")
    if not archive.is_file():
        raise ValueError(f"找不到要解压的文件：{archive}")

    relative = reject_unsafe_entry(_single_output_name(archive, algorithm))
    # 先按安全相对名解析出目标目录，再在同一目录内取不冲突的文件名（不覆盖已有文件）
    destination = _safe_destination(output_dir, relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    target = _unique_file(destination.parent, Path(relative).name)

    try:
        with _open_decompressor(archive, algorithm) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
    except (OSError, EOFError) as error:
        target.unlink(missing_ok=True)  # 别留下半截的产物
        raise ValueError(f"解压 {algorithm} 文件失败（可能已损坏）：{archive.name}") from error
    return target


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
