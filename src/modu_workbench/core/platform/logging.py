"""共享日志小工具（标准库 logging，无新依赖）。

**为什么需要**：仓库里有一批"失败可忽略"的位置（关文件、存进度、可选的诊断回调……）。
它们原来写成 `except Exception: pass` —— 行为上是"静默吞掉"，用户与开发者都看不到发生了什么。
这类位置**保留"不抛"的语义**是对的（一个进度写失败不该让播放崩），但**必须留痕**。

约定：

- 真正可以忽略的失败 → `swallow("作用域", error)`：写到 stderr（打包后是窗口程序，
  stderr 仍可由启动器/日志重定向捕获），**不弹窗、不影响主流程**；
- 需要用户知道的失败 → 不要用这里，走 `Toaster`/`QMessageBox` 或返回错误字符串。

设置 `MODU_LOG_LEVEL=debug` 可以把忽略的失败也打全（默认只打 warning 级）。
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys

LOGGER_NAME = "modu"

_logger = logging.getLogger(LOGGER_NAME)

#: 是否已装配处理器。**放在模块级而不是挂在 logger 上**：
#: `logging.getLogger()` 是全局单例，`importlib.reload(模块)` 不会重置挂在
#: logger 上的属性 —— 测试里一旦先配置过，后续用例就再也加不上文件处理器。
_configured = False


#: 单个日志文件上限与保留份数（1MB × 4 ≈ 4MB 封顶，避免长期运行撑爆磁盘）
LOG_MAX_BYTES = 1024 * 1024
LOG_BACKUP_COUNT = 3


def _file_handler() -> logging.Handler | None:
    """按需建日志文件处理器；任何失败都返回 None（绝不能影响启动）。

    **为什么必须有文件日志**：打包后是窗口程序，双击运行时 stderr 无处可去，
    用户报问题时手上没有任何信息。写到 `<数据目录>/logs/modu.log` 之后，
    用户直接把文件发过来即可。
    """
    try:
        from modu_workbench.core.platform.paths import log_file_path

        path = log_file_path()
        handler = logging.handlers.RotatingFileHandler(
            path,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        return handler
    except Exception:  # noqa: BLE001  日志目录不可写时退化为"只有 stderr"
        return None


def _configure() -> logging.Logger:
    global _configured
    if _configured:
        return _logger
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    _logger.addHandler(handler)
    file_handler = _file_handler()
    if file_handler is not None:
        _logger.addHandler(file_handler)
    level = os.environ.get("MODU_LOG_LEVEL", "warning").lower()
    _logger.setLevel(
        logging.DEBUG if level in ("debug", "trace") else logging.WARNING
    )
    _logger.propagate = False
    _configured = True
    return _logger


def swallow(scope: str, error: BaseException | None = None) -> None:
    """记录一个"被有意忽略"的失败（不抛、不弹窗）。

    `scope` 要写清"在哪一步忽略的"（如 `"关闭 PDF 文档"`），
    否则日志里只有 traceback 的一句话，仍然定位不到。
    """
    _configure()
    detail = f"{type(error).__name__}: {error}" if error is not None else ""
    _logger.warning("忽略失败（%s）%s", scope, detail)


def debug(scope: str, error: BaseException | None = None) -> None:
    """同 `swallow`，但只在 `MODU_LOG_LEVEL=debug` 时输出（高频路径用）。"""
    _configure()
    detail = f"{type(error).__name__}: {error}" if error is not None else ""
    _logger.debug("忽略失败（%s）%s", scope, detail)


__all__ = ["LOGGER_NAME", "LOG_BACKUP_COUNT", "LOG_MAX_BYTES", "debug", "swallow"]
