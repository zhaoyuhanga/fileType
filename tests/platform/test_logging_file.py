"""日志落盘测试。

## 为什么必须有它

打包后是**窗口程序**：双击运行时 `stderr` 无处可去。原来的日志只写 stderr，
于是用户遇到问题时**手上没有任何可提供的信息**，维护者只能靠猜。

现在日志写到 `<数据目录>/logs/modu.log`（轮转，1MB × 4 封顶）。
本文件守住四件事：

1. `swallow()` 的记录真的进文件（不只是 stderr）；
2. 日志里有**时间戳**（判断"什么时候出的问题"）与**作用域**（判断"哪一步"）；
3. 日志目录不可写时**不能让程序崩**（退化为只有 stderr）；
4. 文件大小有上限（轮转生效，不会把磁盘写满）。
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture()
def fresh_logging(tmp_path: Path, monkeypatch):  # noqa: ANN001, ANN201
    """把数据目录指到 tmp_path 并重置 logging 模块的单例状态。"""
    monkeypatch.setenv("MODU_DATA_DIR", str(tmp_path))
    from modu_workbench.core.platform import logging as log_module, paths

    importlib.reload(paths)          # 让 app_data_dir 读到新的 MODU_DATA_DIR
    importlib.reload(log_module)     # 清掉 _modu_configured 与已有 handler
    # 去掉 stderr 处理器：本文件要放大段日志触发轮转，别把控制台刷满
    for handler in list(log_module._logger.handlers):
        if isinstance(handler, log_module.logging.StreamHandler) and not isinstance(
            handler, log_module.logging.FileHandler
        ):
            log_module._logger.removeHandler(handler)
    yield log_module, paths
    # 收尾：移除本次加的 handler，避免污染其它用例
    for handler in list(log_module._logger.handlers):
        log_module._logger.removeHandler(handler)
        handler.close()
    importlib.reload(log_module)
    importlib.reload(paths)


def test_log_file_is_created_under_data_dir(fresh_logging) -> None:  # noqa: ANN001
    """日志文件落在 `<数据目录>/logs/modu.log`。"""
    log_module, paths = fresh_logging
    log_module.swallow("测试作用域", ValueError("故意失败"))

    path = paths.log_file_path()
    assert path.is_file(), f"日志文件没有生成：{path}"
    assert path.parent == paths.logs_dir()


def test_log_contains_timestamp_scope_and_error(fresh_logging) -> None:  # noqa: ANN001
    """一条记录要能回答"何时、在哪、什么错"三件事。"""
    log_module, paths = fresh_logging
    log_module.swallow("关闭 PDF 文档", OSError("文件被占用"))

    text = paths.log_file_path().read_text(encoding="utf-8")
    assert "关闭 PDF 文档" in text, "日志里没有作用域，定位不到是哪一步"
    assert "OSError" in text and "文件被占用" in text, "日志里没有异常类型/信息"
    # 时间戳形如 2026-11-01 23:43:44,882
    assert any(part.isdigit() for part in text.split("-")[0]), "日志行首没有日期"


def test_debug_is_hidden_by_default(fresh_logging) -> None:  # noqa: ANN001
    """`debug()` 默认不写（高频路径，默认只留 warning）。"""
    log_module, paths = fresh_logging
    log_module.debug("高频路径", ValueError("x"))
    text = paths.log_file_path().read_text(encoding="utf-8") if paths.log_file_path().is_file() else ""
    assert "高频路径" not in text


def test_unwritable_log_dir_does_not_crash(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """日志目录不可写时**必须静默退化**，绝不能影响程序启动。

    用一个"文件"占住 `logs` 这个名字，让 mkdir 必然失败。
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "logs").write_text("我是文件，不是目录", encoding="utf-8")
    monkeypatch.setenv("MODU_DATA_DIR", str(data_dir))

    from modu_workbench.core.platform import logging as log_module, paths

    importlib.reload(paths)
    importlib.reload(log_module)
    try:
        log_module.swallow("目录不可写场景", ValueError("x"))   # 不抛即通过
    finally:
        for handler in list(log_module._logger.handlers):
            log_module._logger.removeHandler(handler)
            handler.close()
        importlib.reload(log_module)
        importlib.reload(paths)


def test_rotation_caps_file_size(fresh_logging) -> None:  # noqa: ANN001
    """日志必须轮转：单文件不超过上限，总量受备份份数约束。"""
    log_module, paths = fresh_logging
    assert log_module.LOG_MAX_BYTES <= 4 * 1024 * 1024, "单文件上限过大"
    assert log_module.LOG_BACKUP_COUNT >= 1, "没有备份份数，等于不轮转"

    # 先走一次 swallow 触发 _configure()（直接 _logger.warning 不会装配处理器）
    log_module.swallow("轮转测试预热")

    # 写超过上限的内容，触发轮转
    big = "x" * 4096
    for _ in range((log_module.LOG_MAX_BYTES // len(big)) + 40):
        log_module._logger.warning("%s", big)

    main_file = paths.log_file_path()
    assert main_file.is_file()
    assert main_file.stat().st_size <= log_module.LOG_MAX_BYTES * 1.2, (
        "主日志文件超过上限，轮转没生效"
    )
    rotated = list(paths.logs_dir().glob("modu.log.*"))
    assert rotated, "没有生成轮转文件"
    total = main_file.stat().st_size + sum(p.stat().st_size for p in rotated)
    limit = log_module.LOG_MAX_BYTES * (log_module.LOG_BACKUP_COUNT + 1) * 1.3
    assert total <= limit, f"日志总量 {total} 超过上限 {limit}"


def test_startup_note_creates_log_immediately(fresh_logging) -> None:  # noqa: ANN001
    """**启动即建日志**：一切正常时也要有文件。

    原先是懒初始化（只有 `swallow` 被调用才装配处理器），结果是"一切正常就
    不产生日志"——用户真遇到崩溃时可能一个字都没有。`note_startup()` 在启动
    时先写一行，日志文件从第一次运行就存在，至少能回答
    "什么时候启动过、哪个版本、数据目录在哪"。
    """
    log_module, paths = fresh_logging
    log_module.note_startup("9.9.9", data_dir="D:/demo")

    path = paths.log_file_path()
    assert path.is_file(), "启动后没有立即生成日志文件"
    text = path.read_text(encoding="utf-8")
    assert "9.9.9" in text, "启动标记里没有版本号"
    assert "D:/demo" in text, "启动标记里没有数据目录（排查时最需要的信息）"


def test_startup_note_does_not_pollute_stderr(fresh_logging, capsys) -> None:  # noqa: ANN001
    """启动标记只进文件，不该刷到终端（stderr 默认只收 WARNING）。"""
    log_module, _paths = fresh_logging
    log_module.note_startup("9.9.9", data_dir="D:/demo")
    captured = capsys.readouterr()
    assert "9.9.9" not in captured.err, "INFO 级启动标记泄漏到了 stderr"

