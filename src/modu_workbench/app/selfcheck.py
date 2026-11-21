"""打包自检（`MODU_CHECK_DEPS` 触发）：逐项验证"该随包的东西真的在"。

**为什么单独成文件**：这些检查是**打包验证**专用，只在
`MODU_CHECK_DEPS=<输出路径>` 时跑一次并退出；与 `main.py`（起 Qt、开主壳）
生命周期完全不同。混在一起会让入口文件 800 多行、每次改自检都像是改启动逻辑。

用法：

    $env:MODU_CHECK_DEPS = "$env:TEMP\\check.json"
    .venv\\Scripts\\python.exe -m modu_workbench
    Get-Content "$env:TEMP\\check.json"        # 期望全部 true

每项都应尽量**真实执行**而不是只 import（例如 `multimedia_playback` 真的播一段 WAV、
`video_core` 真的跑一遍 m3u8 解析与换源定位），否则"打包漏东西"会被静默放行。

## 文件结构（v2.3.0 拆分）

原来 17 个检查是 `run_checks` 里的**嵌套函数**，整个函数 800 多行：改一项要在
一个巨型函数里翻找，也没法单独测某一项。现在按「验的是谁」拆成四个子模块：

| 模块 | 覆盖 |
|---|---|
| `selfcheck_convert.py` | Markdown/JSON 渲染、PDF 导出、QtMultimedia 播放 |
| `selfcheck_platform.py` | 单 Qt 栈、ffmpeg/ffprobe 定位、品牌图标 |
| `selfcheck_media.py` | 乐库/影视/图库内核 + 影视播放内核 |
| `selfcheck_document.py` | 文档 IR 全链路、PDF 打印引擎、大模型路由降级 |

本文件只剩「编排」：`_CHECKS` 是一张**数据表**（名字 + 函数 + 顺序），
`run_checks` 只负责逐条执行、收集结果、写 JSON、给退出码。
新增一项检查 = 在对应子模块写一个 `check_*` 函数，并在 `_CHECKS` 里加一行。
"""
from __future__ import annotations

import sys
from typing import Callable

from .selfcheck_convert import (
    check_json_highlight,
    check_lexer_by_name,
    check_markdown_codeblock,
    check_markdown_extra,
    check_multimedia,
    check_multimedia_playback,
    check_pdf_convert,
)
from .selfcheck_document import (
    check_document_core,
    check_llm_core,
    check_pdf_print_engine,
)
from .selfcheck_media import (
    check_gallery_core,
    check_music_core,
    check_video_core,
    check_video_playback,
)
from .selfcheck_platform import (
    check_branding_icon,
    check_ffmpeg_tools,
    check_single_qt_stack,
)

#: 自检清单：`(JSON 里的键, 检查函数)`。**顺序即输出顺序**，与拆分前逐行对齐；
#: 键名是打包脚本与人工排查都在看的稳定契约，不要改名。
_CHECKS: tuple[tuple[str, Callable[[], bool]], ...] = (
    ("markdown_extra", check_markdown_extra),
    ("markdown_codeblock", check_markdown_codeblock),
    ("json_highlight", check_json_highlight),
    ("lexer_by_name", check_lexer_by_name),
    ("single_qt_stack", check_single_qt_stack),
    ("pdf_convert", check_pdf_convert),
    ("multimedia", check_multimedia),
    ("multimedia_playback", check_multimedia_playback),
    ("video_playback", check_video_playback),
    ("music_core", check_music_core),
    ("video_core", check_video_core),
    ("gallery_core", check_gallery_core),
    ("document_core", check_document_core),
    ("pdf_print_engine", check_pdf_print_engine),
    ("llm_core", check_llm_core),
    ("ffmpeg_tools", check_ffmpeg_tools),
    ("branding_icon", check_branding_icon),
)


def run_checks(output_path: str) -> int:
    """逐条跑 `_CHECKS`，把结果写成 JSON；全 true 才返回 0。

    单条检查抛异常**不会中断整轮**（打包时要一次看到所有问题，而不是修一个跑一次）：
    异常被收成 `"ERROR: ..."` 字符串写进结果，退出码因此是 1。
    """
    import json

    results: dict[str, object] = {}
    for name, check in _CHECKS:
        try:
            results[name] = check()
        except Exception as error:  # noqa: BLE001  单条失败要记下来，不能拖垮整轮
            results[name] = f"ERROR: {error}"

    try:
        with open(output_path, "w", encoding="utf-8") as fp:
            json.dump(results, fp, ensure_ascii=False, indent=2)
    except OSError as error:
        # 写不出结果 = 打包脚本拿不到结论，必须明确失败（原来那句
        # `return 2 if ... else 2` 永远返回 2，还把 error 丢掉了）
        print(f"[selfcheck] 结果写入失败：{error}", file=sys.stderr)
        return 2
    return 0 if all(value is True for value in results.values()) else 1


def run_board_smoke(output_path: str, app=None) -> int:  # noqa: ANN001
    """启动级冒烟：真的建一次主壳，并把**六个板块逐个打开**。

    ## 为什么必须单独有这一条

    `MODU_CHECK_DEPS` 那条自检在 `QApplication` 创建**之前**就 return 了，
    所以「主题组装 / 板块懒导入 / 板块页构造」它一个都碰不到。v2.1.0 定版就栽在这：
    - `theme.py` 与 `theme_icons.py` 模块级循环导入 → 冻结后启动崩；
    - 板块页改成 `page_target` 字符串 + importlib 懒导入，PyInstaller 静态分析看不到，
      `boards.book.board` 等 6 个模块没进包 → 首屏点「墨软书库」就 `ModuleNotFoundError`。

    两种情况源码与单测都全绿，只有**装完点图标**才暴露。这条冒烟把那条路径补上，
    由 `packaging/build_app.ps1` 在打包后调用。

    结果写法：`{板块 key: true/false/错误信息}`，全部 true 才返回 0。
    """
    import json

    from PySide6.QtWidgets import QApplication

    from modu_workbench.app.registry import ACTIVE_BOARDS
    from modu_workbench.app.shell import HOME_KEY, AppShell
    from modu_workbench.ui_kit.theme import ThemeTokens, app_qss

    results: dict[str, object] = {}
    application = app or QApplication.instance() or QApplication([])
    try:
        # 1) 主题必须能组装（覆盖 theme ↔ theme_icons 互导入、图标现画）
        tokens = ThemeTokens()
        qss = app_qss(tokens)
        results["theme_qss"] = bool(qss) and "navButton" in qss
        application.setStyleSheet(qss)

        # 2) 主壳必须能构造，首页必须起来
        shell = AppShell()
        results["shell"] = shell.current_key == HOME_KEY and shell.current_page() is not None

        # 3) 六个板块逐个真的打开（懒导入 + 页面构造 + on_shown 钩子）
        for spec in ACTIVE_BOARDS:
            try:
                shell.go_page(spec.key)
                page = shell.current_page()
                ok = shell.current_key == spec.key and isinstance(page, spec.page)
                results[spec.key] = True if ok else f"页面类型不符：{type(page).__name__}"
            except Exception as error:  # noqa: BLE001  任意失败都要记下来
                results[spec.key] = f"{type(error).__name__}: {error}"
        shell.close()
    except Exception as error:  # noqa: BLE001
        results["fatal"] = f"{type(error).__name__}: {error}"

    try:
        with open(output_path, "w", encoding="utf-8") as fp:
            json.dump(results, fp, ensure_ascii=False, indent=2)
    except OSError as error:
        print(f"[smoke] 结果写入失败：{error}", file=sys.stderr)
        return 2
    return 0 if all(value is True for value in results.values()) else 1
