"""墨软文档板块的应用级单例（板块内共享；其它板块不直接引用）。

设置分两处存放，各有理由：
- **权限与开关**（是否允许云端上传、脱敏规则、轮次/阈值上限、版本保留）走 `QSettings`
  （与其它板块的设置页一致，界面改完立即生效）；
- **数据**（文档库、版本快照、合并报告、AI 与审计日志）走单库 `modu.db` 的 `doc_*` 表。

`document_library()` 会在首次创建时读取一次设置；设置页保存后调用
`refresh_library()` 让权限与输出目录立即生效（不必重启应用）。需要"每次用之前都读一次"
的设置以读取器形式注入：`permissions_provider`（AI 调用前）、`version_keep_provider`
（每次裁剪版本快照前）。
"""
from __future__ import annotations

from pathlib import Path

from modu_workbench.core.document import (
    BeautifyOptions,
    DocumentAi,
    DocumentLibrary,
    DocumentStorage,
)
from modu_workbench.core.document import security as document_security
from modu_workbench.core.document.storage import MAX_VERSIONS_PER_DOCUMENT
from modu_workbench.core.llm.context import llm_router, llm_storage  # noqa: F401  文档 AI 复用大模型配置
from modu_workbench.core.platform.paths import app_db_path, document_dir
from modu_workbench.ui_kit.settings import app_settings

_storage: DocumentStorage | None = None
_library: DocumentLibrary | None = None
_ai: DocumentAi | None = None


def _read_version_keep() -> int:
    """设置页的「版本保留」（每次裁剪都读一次，改完立即生效）。"""
    return int(app_settings().value("document/max_versions", MAX_VERSIONS_PER_DOCUMENT)
               or MAX_VERSIONS_PER_DOCUMENT)


def document_storage() -> DocumentStorage:
    global _storage
    if _storage is None:
        _storage = DocumentStorage(app_db_path())
        _storage.version_keep_provider = _read_version_keep
    return _storage


def _read_permissions() -> document_security.Permissions:
    return document_security.permissions_from_settings(app_settings().value)


def document_ai() -> DocumentAi:
    """文档 AI 服务（权限实时读取设置；未配置大模型时其它功能不受影响）。"""
    global _ai
    if _ai is None:
        _ai = DocumentAi(
            llm_router(), document_storage(),
            permissions=_read_permissions(), permissions_provider=_read_permissions,
            price_per_1k=float(app_settings().value("document/price_per_1k", 0.0) or 0.0))
    return _ai


def document_output_dir() -> Path:
    """默认输出目录：设置里的值优先，否则 `数据目录/document`。"""
    stored = str(app_settings().value("document/output_dir", "", type=str) or "").strip()
    path = Path(stored) if stored else document_dir()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        path = document_dir()
    return path


def default_options() -> BeautifyOptions:
    """从设置组装默认美化参数（模板与字号等）。"""
    settings = app_settings()
    options = BeautifyOptions()
    options.template = str(settings.value("document/template", "general") or "general")
    options.font_cn = str(settings.value("document/font_cn", options.font_cn) or options.font_cn)
    options.body_size = float(settings.value("document/body_size", options.body_size)
                              or options.body_size)
    # 布尔值一律用 `type=bool`：Windows 注册表里 Qt 存的是 "true"/"false" 字符串，
    # 直接 bool(value) 会把 "false" 当成 True（设置页关了却不生效）
    options.auto_number = bool(settings.value("document/auto_number", False, type=bool))
    options.build_toc = bool(settings.value("document/build_toc", False, type=bool))
    options.beautify_tables = bool(settings.value("document/beautify_tables", True, type=bool))
    options.freeze_header = bool(settings.value("document/freeze_header", True, type=bool))
    options.conditional_format = bool(
        settings.value("document/conditional_format", False, type=bool))
    options.data_validation = bool(
        settings.value("document/data_validation", False, type=bool))
    options.brand_color = str(settings.value("document/brand_color", options.brand_color)
                              or options.brand_color)
    return options


def document_library() -> DocumentLibrary:
    global _library
    if _library is None:
        _library = DocumentLibrary(
            document_storage(), ai=document_ai(), output_dir=str(document_output_dir()),
            options=default_options())
        # 插件默认关闭（等于执行本机代码）；启用后在这里注册自定义工具
        try:
            from modu_workbench.core.document import plugins as document_plugins

            _library.load_plugins(enabled=document_plugins.plugins_enabled(_library.storage))
        except Exception:  # noqa: BLE001  插件加载失败不影响板块启动
            pass
    return _library


def refresh_library() -> DocumentLibrary:
    """设置改完后重建单例（权限、输出目录、默认美化参数立即生效）。"""
    global _library, _ai
    _ai = None
    _library = None
    return document_library()
