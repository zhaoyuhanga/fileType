"""墨软动漫的应用级单例（板块内共享；其他板块不直接引用）。

与其它板块的 context 有两点不同，都是被并行的后端开发逼出来的：

1. **后端 import 一律放在函数内部**。`core/anime/` 的引擎/库/存储与界面同时开发，
   `core/anime/__init__.py` 现在会 import 一批还没落地的模块；如果本模块在顶层 import，
   整个动漫板块（乃至主壳 `AppShell`，它会构造每个板块的首页）都会 ImportError。
   放在函数里 + `try/except` 之后，缺后端只是"得到 None / 报一条可读原因"。
2. **单例只缓存"成功构造"的结果**。构造失败时不要缓存异常对象，
   否则后端就绪后（同一个进程里）页面永远拿不到真实现。
"""
from __future__ import annotations

from pathlib import Path

from modu_workbench.core.platform.paths import app_db_path

_anime_storage = None
_anime_library = None
_anime_rules = None


def anime_data_dir() -> Path:
    """动漫数据目录（库文件/下载分部/弹幕缓存/导出兜底）。"""
    from modu_workbench.core.platform import paths

    getter = getattr(paths, "anime_dir", None)
    if callable(getter):
        try:
            return Path(getter())
        except Exception:  # noqa: BLE001  路径函数存在但初始化失败：退回默认位置
            pass
    target = paths.app_data_dir() / "anime"
    target.mkdir(parents=True, exist_ok=True)
    return target


def anime_storage():  # noqa: ANN201
    """动漫存储（`anime_*` 表 CRUD）；后端缺失时抛 ImportError，由调用方降级。"""
    global _anime_storage
    if _anime_storage is None:
        from modu_workbench.core.anime import AnimeStorage

        _anime_storage = AnimeStorage(app_db_path())
    return _anime_storage


def anime_rules():  # noqa: ANN201
    """规则管理器（规则文件的发现/导入/导出/订阅）。

    显式传 `user_dir`：`RuleManager()` 的默认实现依赖 `paths.anime_rules_dir()`，
    那个函数由并行的路径改动提供，还没落地时构造会直接 AttributeError。
    这里自己兜住目录，保证"规则页能打开、能显示空列表"。
    """
    global _anime_rules
    if _anime_rules is None:
        from modu_workbench.core.anime import RuleManager

        user_dir: Path | None = None
        try:
            from modu_workbench.core.platform.paths import anime_rules_dir

            user_dir = Path(anime_rules_dir())
        except Exception:  # noqa: BLE001  路径函数还没加：用数据目录下的默认位置
            user_dir = anime_data_dir() / "rules"
        try:
            _anime_rules = RuleManager(user_dir=user_dir)
        except TypeError:
            # 后端 RuleManager 构造签名不同（只接受位置参数等）：退回默认构造
            _anime_rules = RuleManager()
    return _anime_rules


def anime_library():  # noqa: ANN201
    """动漫业务库（搜索/追番/进度/下载/更新检查）。"""
    global _anime_library
    if _anime_library is None:
        from modu_workbench.core.anime import AnimeLibrary

        _anime_library = AnimeLibrary(
            anime_storage(), rule_manager=anime_rules(), data_dir=anime_data_dir(),
        )
    return _anime_library


__all__ = ["anime_data_dir", "anime_library", "anime_rules", "anime_storage"]
