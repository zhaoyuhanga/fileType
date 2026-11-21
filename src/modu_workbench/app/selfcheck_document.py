"""文档与大模型子自检：IR 全链路、PDF 打印引擎、模型路由降级

**为什么拆到这里**：文档板块的检查最长（美化/写出/公式/合并/循环/AI 工具/插件/带图往返/布尔设置），再加上它的两个横切能力（PDF 打印引擎、大模型路由降级）。单独成模块，避免把其它板块的检查淹没。

本模块只放 `check_*` 函数（纯函数、无参数、返回 bool 或抛出）。它们由
`app/selfcheck.py` 的 `_CHECKS` 表按固定顺序调用 —— 拆分**不改变任何检查的内容与顺序**，
只是把原来一个 800 多行的 `run_checks` 按「验的是谁」切开，让每一条都能被单独调用与单独测试。
"""
from __future__ import annotations


def check_document_core() -> bool:
    """墨软文档核心：建表读写、解析/写出、美化、公式计算、排序筛选、合并、
    PDF 页面操作、工具与循环。"""
    import tempfile
    from pathlib import Path as _Path

    from modu_workbench.core.document import (
        TOOL_REGISTRY,
        BeautifyOptions,
        DocumentLibrary,
        DocumentStorage,
        LoopOptions,
        MergeOptions,
        TableData,
        compress_pdf,
        evaluate_formula,
        filter_table,
        merge_pdfs,
        parse_markdown,
        pdf_info,
        sort_table,
        split_pdf,
        tool_names,
        write_document,
    )

    try:
        with tempfile.TemporaryDirectory() as folder:
            root = _Path(folder)
            store = DocumentStorage(root / "modu.db")
            try:
                library = DocumentLibrary(store, output_dir=root / "out")
                ir = parse_markdown(
                    "# 标题\n\n## 1.1 数据\n\n正文。\n\n| A | B |\n|---|---|\n| 1 | 2 |\n",
                    title="标题")
                document_id = library.register(ir)

                # 1) 美化（模板 + 目录 + 表格）
                beautified = library.beautify(ir, template="report")
                beautify_ok = bool(beautified.changes) and beautified.ir.table_count() == 1

                # 2) 写出（Word + Excel 的冻结表头/条件格式/数据验证）+ 版本快照
                exported = library.export(beautified.ir, "docx", name="自检导出")
                sheet_result = write_document(
                    ir, root / "自检.xlsx", "xlsx",
                    options=BeautifyOptions(conditional_format=True, data_validation=True))
                version_ok = (exported.path.is_file() and sheet_result.path.is_file()
                              and bool(store.list_versions(document_id)))

                # 3) 公式计算（含跨行引用）与排序/筛选
                table = TableData(rows=[["名称", "数量"], ["A", "3"], ["B", "4"]],
                                  name="Sheet1", header=True)
                formula_ok = float(evaluate_formula("=SUM(B2:B3)", table,
                                                    sheet_name="Sheet1")) == 7.0
                sheet_ok = (len(sort_table(table, "数量", descending=True).body_rows()) == 2
                            and len(filter_table(table, "名称", "A").body_rows()) == 1)

                # 4) 两个文档合并 + 报告落库
                other = parse_markdown("# 副文档\n\n补充内容。", title="副文档")
                merged = library.merge(ir, other, MergeOptions(mode="append"))
                merge_ok = ("副文档" in merged.ir.text()
                            and bool(store.list_merge_reports()))

                # 5) PDF 页面操作（拆分 / 合并 / 压缩）
                from pypdf import PdfWriter

                first = root / "a.pdf"
                second = root / "b.pdf"
                for path, pages in ((first, 2), (second, 1)):
                    writer = PdfWriter()
                    for _ in range(pages):
                        writer.add_blank_page(width=595, height=842)
                    with open(path, "wb") as handle:
                        writer.write(handle)
                merged_pdf = merge_pdfs([first, second], root / "merged.pdf")
                parts = split_pdf(merged_pdf, root / "parts", mode="each")
                compressed = compress_pdf(merged_pdf, root / "compressed.pdf")
                pdf_ok = (pdf_info(merged_pdf).pages == 3 and len(parts) == 3
                          and pdf_info(compressed).pages == 3)

                # 6) 循环美化（离线本地规则）与工具注册表
                loop_result = library.run_loop(
                    beautified.ir,
                    LoopOptions(goal="规范标点并统一格式", max_rounds=2, use_ai=False,
                                quality_threshold=0.99))
                loop_ok = loop_result.round_count >= 1 and bool(loop_result.log)
                tools_ok = (len(tool_names()) >= 50
                            and "beautify" in TOOL_REGISTRY
                            and "merge_documents" in TOOL_REGISTRY
                            and "pdf_merge" in TOOL_REGISTRY
                            and "add_comment" in TOOL_REGISTRY)

                # 7) 批注（文档层 + PDF 标注）与本地模板库
                from modu_workbench.core.document import (
                    annotate_pdf,
                    list_annotations,
                    load_template,
                    save_template,
                )

                library.add_comment(ir, 1, "自检批注", author="自检")
                comments_ok = bool(library.list_comments(ir))
                annotated = annotate_pdf(first, root / "annotated.pdf",
                                         [{"page": 1, "text": "自检 PDF 批注"}])
                annotate_ok = list_annotations(annotated)[0]["text"] == "自检 PDF 批注"
                template_path = save_template("自检模板", BeautifyOptions(font_cn="黑体",
                                                                       auto_number=True))
                template_ok = (load_template(template_path).options.font_cn == "黑体")

                # 8) 插件加载器（自定义工具）：临时文件 → 注册 → 可调用 → 清理
                from modu_workbench.core.document import plugins as plugin_module
                from modu_workbench.core.document import call_tool as run_tool

                plugin_file = root / "selfcheck_plugin.py"
                plugin_file.write_text(
                    "def register(api):\n"
                    "    def ping(context, args):\n"
                    "        return api.ToolResult(True, '插件自检 OK')\n"
                    "    api.register_tool(api.ToolSpec(name='selfcheck_ping', label='自检',\n"
                    "        description='插件自检', category='read', handler=ping))\n",
                    encoding="utf-8")
                plugin_record = plugin_module.load_file(plugin_file)
                plugin_ok = False
                if plugin_record.ok:
                    result = run_tool("selfcheck_ping", library.tool_context(ir), {})
                    plugin_ok = result.ok and "插件自检 OK" in result.summary
                    TOOL_REGISTRY.pop("selfcheck_ping", None)

                # 9) 带图 Word 往返（图片曾经会静默丢失）+ 布尔设置读注册表字符串
                import base64 as _base64
                import zipfile as _zip

                from docx import Document as _Docx
                from docx.shared import Inches as _Inches

                from modu_workbench.core.document import parse_document
                from modu_workbench.core.document import permissions_from_settings

                seed_png = root / "seed.png"
                seed_png.write_bytes(_base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
                    "z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="))
                with_image = root / "with-image.docx"
                seed = _Docx()
                seed.add_paragraph("图前正文")
                seed.add_picture(str(seed_png), width=_Inches(1.0))
                seed.save(with_image)
                image_ir = parse_document(with_image)
                image_ok = any(block.kind == "image" for block in image_ir.blocks)
                library.save(image_ir, with_image, overwrite=True)
                with _zip.ZipFile(with_image) as bundle:
                    image_ok = image_ok and any(name.startswith("word/media/")
                                                for name in bundle.namelist())
                # Qt 在 Windows 注册表里存的是 "true"/"false" 字符串：不能 bool("false")
                settings_ok = permissions_from_settings(
                    lambda key, default=None: "false" if key.endswith("allow_cloud") else default
                ).allow_cloud is False
            finally:
                store.close()
        return bool(beautify_ok and version_ok and formula_ok and sheet_ok and merge_ok
                    and pdf_ok and loop_ok and tools_ok and comments_ok and annotate_ok
                    and template_ok and plugin_ok and image_ok and settings_ok)
    except Exception:  # noqa: BLE001
        return False


def check_pdf_print_engine() -> bool:
    """PDF 打印渲染（QtPdf）：文档板块「打印」靠它把页面画到打印机上。

    打包时 `workbench.spec` 特意**不排除** QtPdf（Python 绑定与 Qt6Pdf.dll 都要在），
    这个自检就是那道保证。
    """
    import tempfile
    from pathlib import Path as _Path

    from PySide6.QtCore import QSize
    from PySide6.QtWidgets import QApplication
    from pypdf import PdfWriter

    from modu_workbench.boards.document.viewer import _PdfPages

    QApplication.instance() or QApplication([])      # QPdfDocument 渲染需要 GUI 应用实例

    with tempfile.TemporaryDirectory() as folder:
        path = _Path(folder) / "blank.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        with open(path, "wb") as handle:
            writer.write(handle)
        pages = _PdfPages(path, 150)
        try:
            if pages.count != 1:
                return False
            image = pages.image(0, QSize(200, 280))
            return bool(image is not None and not image.isNull() and image.width() > 0)
        finally:
            pages.close()


def check_llm_core() -> bool:
    """大模型能力中心：多类型存储、优先级排序、失败自动降级、旧配置迁移。"""
    import tempfile
    from pathlib import Path as _Path

    from modu_workbench.core.llm import (
        KIND_IMAGE,
        KIND_TEXT,
        LlmRequestError,
        LlmStorage,
        ModelProfile,
        ModelRouter,
    )

    try:
        with tempfile.TemporaryDirectory() as folder:
            store = LlmStorage(_Path(folder) / "llm.db")
            try:
                # 1) 同类型多份配置，按添加顺序即优先级
                store.save_profile(ModelProfile(
                    kind=KIND_TEXT, name="主力", provider="deepseek",
                    base_url="https://x/v1", api_key="k1", model="m1"))
                store.save_profile(ModelProfile(
                    kind=KIND_TEXT, name="备用", provider="deepseek",
                    base_url="https://x/v1", api_key="k2", model="m2"))
                store.save_profile(ModelProfile(
                    kind=KIND_IMAGE, name="看图", provider="deepseek",
                    base_url="https://x/v1", api_key="k3", model="m3"))

                order_ok = [p.name for p in store.list_profiles(KIND_TEXT)] == ["主力", "备用"]

                # 2) 上移/下移真的改优先级，且不跨类型
                moved = store.move_profile(store.list_profiles(KIND_TEXT)[1].id, -1)
                move_ok = moved and [p.name for p in store.list_profiles(KIND_TEXT)] == ["备用", "主力"]
                move_ok = move_ok and store.move_profile(
                    store.list_profiles(KIND_IMAGE)[0].id, -1) is False

                # 3) 停用后不再参与调用
                store.set_enabled(store.list_profiles(KIND_TEXT)[0].id, False)
                router = ModelRouter(store)
                disable_ok = len(router.usable_profiles(KIND_TEXT)) == 1
                store.set_enabled(store.list_profiles(KIND_TEXT)[0].id, True)

                # 4) 降级：第一份必然失败（地址不可解析），应自动落到第二份并给出合并错误
                for profile in store.list_profiles(KIND_TEXT):
                    profile.timeout = 3.0
                    store.save_profile(profile)
                first = store.list_profiles(KIND_TEXT)[0]
                first.base_url = "http://127.0.0.1:9/definitely-not-listening"
                store.save_profile(first)
                fallback_ok = False
                try:
                    router.chat(KIND_TEXT, [{"role": "user", "content": "hi"}])
                except LlmRequestError:
                    fallback_ok = True          # 两份都失败 → 合并报错（说明确实逐个试过）
                except Exception:               # noqa: BLE001
                    fallback_ok = False

                # 5) 旧版图库配置能迁移成文字 + 图片两份配置
                legacy = LlmStorage(_Path(folder) / "legacy.db")
                try:
                    migrated = legacy.migrate_legacy(
                        api_key="sk-old", model="deepseek-chat",
                        vision_model="deepseek-vl")
                    kinds = {p.kind for p in legacy.list_profiles()}
                    migrate_ok = migrated and kinds == {KIND_TEXT, KIND_IMAGE}
                finally:
                    legacy.close()
            finally:
                store.close()
        return bool(order_ok and move_ok and disable_ok and fallback_ok and migrate_ok)
    except Exception:  # noqa: BLE001
        return False


__all__ = [
    "check_document_core",
    "check_pdf_print_engine",
    "check_llm_core",
]
