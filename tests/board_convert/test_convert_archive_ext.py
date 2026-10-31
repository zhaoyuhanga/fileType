"""归档扩展测试：gz / bz2 / xz 单文件压缩 + tar.gz / tar.bz2 / tar.xz + 防覆盖 / 防穿越。

约定见 `core/convert/archive_io.py` 模块 docstring：
- gz 在头里记录原名，解压优先用原名；
- bz2 / xz 没有名字字段，解压回退为「去掉压缩后缀的包名」。
"""
from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from modu_workbench.core.convert.archive_io import (
    compress_single,
    compress_tar,
    extract_single,
    extract_tar,
    reject_unsafe_entry,
)
from modu_workbench.core.convert.engine import run_conversion
from modu_workbench.core.convert.registry import get_action

SAMPLE_TEXT = "归档扩展测试内容\n第二行（含中文与符号 &%）。\n"


@pytest.fixture()
def sample_txt(tmp_path: Path) -> Path:
    path = tmp_path / "报告.txt"
    path.write_text(SAMPLE_TEXT, encoding="utf-8")
    return path


def _write_evil_tar(path: Path, member_name: str = "../evil.txt") -> Path:
    """手工构造含越界条目的 tar（模拟恶意归档）。"""
    data = b"bad"
    with tarfile.open(path, "w") as tf:
        info = tarfile.TarInfo(member_name)
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return path


@pytest.mark.parametrize("algorithm", ["gz", "bz2", "xz"])
def test_single_roundtrip_keeps_bytes_and_name(
    algorithm: str, sample_txt: Path, tmp_path: Path
) -> None:
    """gz/bz2/xz 压完再解，字节要还原；文件名遵循模块 docstring 的约定。

    原名可能是「头里记录的名字」，也可能是「去掉压缩后缀的包名」——bz2/xz 只能后者，
    gz 在头信息可用时用前者。
    """
    archive = tmp_path / f"报告.txt.{algorithm}"
    compress_single(sample_txt, archive, algorithm=algorithm)
    assert archive.is_file() and archive.stat().st_size > 0

    out = tmp_path / f"out-{algorithm}"
    produced = extract_single(archive, out)
    assert produced.parent == out, "产物必须落在指定输出目录里"
    assert produced.name in {sample_txt.name, archive.stem}, f"{algorithm} 解压名不符合约定"
    assert produced.read_bytes() == sample_txt.read_bytes()


def test_gzip_header_stores_original_name(sample_txt: Path, tmp_path: Path) -> None:
    """gz 的原名写在 gzip 头里（自解压/其他工具也能看到）。"""
    archive = tmp_path / "报告.txt.gz"
    compress_single(sample_txt, archive, algorithm="gz")

    head = archive.read_bytes()[:40]
    assert "报告.txt".encode("utf-8") in head, "gzip 头里应记录原名"


def test_gz_without_name_falls_back_to_archive_stem(tmp_path: Path) -> None:
    """丢失头信息的 gz（外部工具产生）回退到去掉 .gz 的包名。"""
    import gzip

    plain = tmp_path / "外部数据.bin"
    plain.write_bytes(b"external gz payload")
    gz_archive = tmp_path / "外部数据.bin.gz"
    with gzip.GzipFile(filename="", mode="wb", fileobj=open(gz_archive, "wb"), mtime=0) as dst:
        dst.write(plain.read_bytes())

    produced = extract_single(gz_archive, tmp_path / "out-fallback")
    assert produced.name == "外部数据.bin"
    assert produced.read_bytes() == b"external gz payload"


@pytest.mark.parametrize("algorithm", ["bz2", "xz"])
def test_bz2_xz_fall_back_to_archive_stem(
    algorithm: str, sample_txt: Path, tmp_path: Path
) -> None:
    """bz2 / xz 没有名字字段：按 «包名去掉压缩后缀» 还原原名。"""
    archive = tmp_path / f"报告.txt.{algorithm}"
    compress_single(sample_txt, archive, algorithm=algorithm)
    produced = extract_single(archive, tmp_path / f"out-stem-{algorithm}")
    assert produced.name == "报告.txt"
    assert produced.read_bytes() == sample_txt.read_bytes()


@pytest.mark.parametrize("algorithm", ["gz", "bz2", "xz"])
def test_extract_single_twice_does_not_overwrite(
    algorithm: str, sample_txt: Path, tmp_path: Path
) -> None:
    """重复解压不覆盖前一次结果，而是追加序号。"""
    archive = tmp_path / f"报告.txt.{algorithm}"
    compress_single(sample_txt, archive, algorithm=algorithm)
    out = tmp_path / f"out-twice-{algorithm}"

    first = extract_single(archive, out)
    second = extract_single(archive, out)

    assert first != second
    assert first.name == "报告.txt"
    assert second.name == "报告 (2).txt"
    assert first.read_bytes() == second.read_bytes() == sample_txt.read_bytes()


@pytest.mark.parametrize(
    "compression,extension",
    [("", ".tar"), ("gz", ".tar.gz"), ("bz2", ".tar.bz2"), ("xz", ".tar.xz")],
)
def test_tar_variants_roundtrip(
    compression: str, extension: str, sample_txt: Path, tmp_path: Path
) -> None:
    """tar / tar.gz / tar.bz2 / tar.xz 都能打包并按内容自动识别解压。"""
    archive = tmp_path / f"报告{extension}"
    compress_tar(sample_txt, archive, compression=compression)

    with tarfile.open(archive, "r:*") as tf:
        names = tf.getnames()
    assert names == ["报告.txt"], f"{extension} 应把单文件按原名放进归档"

    out = tmp_path / f"out{extension}"
    extract_tar(archive, out)
    assert (out / "报告.txt").read_text(encoding="utf-8") == SAMPLE_TEXT


def test_extract_tar_rejects_traversal_entry(tmp_path: Path) -> None:
    """含 ../evil.txt 的 tar 必须被拒绝，且不能写到输出目录之外。"""
    evil = _write_evil_tar(tmp_path / "evil.tar")
    out = tmp_path / "out-evil"

    with pytest.raises(ValueError) as excinfo:
        extract_tar(evil, out)
    assert ".." in str(excinfo.value) or "越界" in str(excinfo.value)
    assert not (tmp_path / "evil.txt").exists(), "越界文件绝不能被写出"


def test_extract_tar_rejects_traversal_in_compressed_tar(tmp_path: Path) -> None:
    """tar.gz 里的越界条目同样要被拦住（识别压缩后仍走同一套校验）。"""
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tf:
        info = tarfile.TarInfo("../escape.txt")
        data = b"bad"
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))

    with pytest.raises(ValueError):
        extract_tar(evil, tmp_path / "out-evil-gz")
    assert not (tmp_path / "escape.txt").exists()


def test_extract_tar_absolute_member_stays_inside_output_dir(tmp_path: Path) -> None:
    """归档里的绝对路径条目会被规范化，写入仍被限制在输出目录内。"""
    archive = tmp_path / "abs.tar"
    with tarfile.open(archive, "w") as tf:
        info = tarfile.TarInfo("/abs/evil.txt")
        data = b"bad"
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))

    out = tmp_path / "out-abs"
    extract_tar(archive, out)
    assert (out / "abs" / "evil.txt").read_bytes() == b"bad"
    assert not Path("/abs/evil.txt").exists()


def test_reject_unsafe_entry_still_guards() -> None:
    """沿用原有防护语义：越界 / 盘符 / 空名都拒绝，正常相对路径放行。"""
    assert reject_unsafe_entry("dir/inner.txt") == "dir/inner.txt"
    for bad in ("../evil.txt", "a/../../evil.txt", "..", "C:/evil.txt", ""):
        with pytest.raises(ValueError):
            reject_unsafe_entry(bad)
    # 历史语义：绝对路径会被规范化成相对名（写入仍在目标目录内，见 _safe_destination）
    assert reject_unsafe_entry("/abs/evil.txt") == "abs/evil.txt"


def test_extract_single_rejects_corrupted_archive(tmp_path: Path) -> None:
    """损坏的 gz 要报错（中文提示），且不留下半截产物。"""
    broken = tmp_path / "坏文件.txt.gz"
    broken.write_bytes(b"this is not gzip data at all")

    out = tmp_path / "out-broken"
    with pytest.raises(ValueError) as excinfo:
        extract_single(broken, out)
    assert "损坏" in str(excinfo.value) or "不是有效" in str(excinfo.value)
    assert list(out.glob("*")) == [], "失败时不应留下空壳文件"


def test_extract_tar_rejects_non_tar(tmp_path: Path) -> None:
    """不是 tar 的文件（含伪装成 .tar.gz 的）给出可读中文错误。"""
    fake = tmp_path / "假的.tar.gz"
    fake.write_bytes(b"not a tar archive")

    with pytest.raises(ValueError) as excinfo:
        extract_tar(fake, tmp_path / "out-fake")
    assert "tar" in str(excinfo.value)


def test_compress_single_rejects_unknown_algorithm(sample_txt: Path, tmp_path: Path) -> None:
    """未知算法要立刻报错，不能产出垃圾文件。"""
    target = tmp_path / "报告.txt.7z"
    with pytest.raises(ValueError):
        compress_single(sample_txt, target, algorithm="7z")
    assert not target.exists()


@pytest.mark.parametrize(
    "compress_id,extension,extract_id",
    [
        ("compress-to-targz", ".tar.gz", "tar-extract"),
        ("compress-to-tarbz2", ".tar.bz2", "tar-extract"),
        ("compress-to-tarxz", ".tar.xz", "tar-extract"),
        ("compress-to-gz", ".gz", "gz-extract"),
        ("compress-to-bz2", ".bz2", "bz2-extract"),
        ("compress-to-xz", ".xz", "xz-extract"),
    ],
)
def test_engine_compressed_roundtrip(
    compress_id: str, extension: str, extract_id: str, sample_txt: Path, tmp_path: Path
) -> None:
    """引擎路由（动作 id → archive_io）端到端可用，内容必须原样回来。

    命名约定（v1.0.4 起）：
    - 单文件压缩 **保留源文件全名**：报告.txt → 报告.txt.gz / 报告.txt.bz2 / 报告.txt.xz
      （bz2/xz 格式本身没有文件名字段，只有这样才能把原名带回去）；
    - tar 系列仍用 stem：报告.tar.gz（条目名就是原名）；
    - 解压后一律拿回 报告.txt（gz 读头里的原名，bz2/xz 由包名去掉压缩后缀）。
    """
    out = tmp_path / f"out-{compress_id}"
    out.mkdir()

    compressed = run_conversion(get_action(compress_id), sample_txt, out)
    assert compressed.status == "succeeded", compressed.message
    archive = Path(compressed.output_path or "")
    expected_name = f"报告{extension}" if extension.startswith(".tar") else f"报告.txt{extension}"
    assert archive.name == expected_name

    extracted = run_conversion(get_action(extract_id), archive, out)
    assert extracted.status == "succeeded", extracted.message
    produced_dir = Path(extracted.output_path or "")
    files = [item for item in produced_dir.rglob("*") if item.is_file()]
    assert len(files) == 1, f"{extract_id} 应产出恰好一个文件，实际：{files}"
    assert files[0].name == sample_txt.name, f"解压后应还原原名，实际 {files[0].name}"
    assert files[0].read_text(encoding="utf-8") == SAMPLE_TEXT
