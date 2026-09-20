"""入口：创建 QApplication、套用全局主题并启动 AppShell。"""
from __future__ import annotations

import os
import sys


def main(argv: list[str] | None = None) -> int:
    # 统一数据入口：建库/迁移/导入旧版分库（失败不阻断启动）
    try:
        from .bootstrap import bootstrap_data

        bootstrap_data()
    except Exception as error:  # noqa: BLE001
        print(f"[data] 初始化失败：{error}", file=sys.stderr)

    # 依赖自检（打包验证用）：MODU_CHECK_DEPS=输出路径 时只做检查并退出，不启动界面。
    check_out = os.environ.get("MODU_CHECK_DEPS")
    if check_out:
        return _run_dependency_check(check_out)

    from PySide6.QtWidgets import QApplication

    from .. import APP_NAME, APP_SLOGAN, __version__
    from .shell import AppShell
    from ..ui_kit.theme import apply_theme

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName("ModuWorkbench")
    app.setApplicationVersion(__version__)
    apply_theme(app)

    shell = AppShell()
    shell.setWindowTitle(f"{APP_NAME} · {APP_SLOGAN} v{__version__}")
    shell.resize(1180, 760)
    shell.show()
    return app.exec()


def _run_dependency_check(output_path: str) -> int:
    import json

    results: dict[str, object] = {}

    def record(name: str, fn) -> None:  # noqa: ANN001
        try:
            results[name] = fn()
        except Exception as error:  # noqa: BLE001
            results[name] = f"ERROR: {error}"

    def check_markdown_extra() -> bool:
        """Markdown 渲染可用（标题带样式属性，因此只断言标签与文字，不写死整串）。

        顺带验证新排版管线（`_qt_style_html` 依赖 bs4）在打包环境里同样生效：
        表格必须被属性化成带底色的 Qt 友好 HTML。
        """
        from modu_workbench.boards.convert.doc_viewer import _render_markdown

        html = _render_markdown("# 标题\n\n正文。\n\n| A | B |\n|---|---|\n| 1 | 2 |\n")
        return "<h1" in html and "标题" in html and "正文" in html and 'bgcolor="#eef1f8"' in html.lower()

    def check_markdown_codeblock() -> bool:
        """代码块：保留 <pre> 且被带底色的单元格包住（Qt 单栈下的排版约定）。"""
        from modu_workbench.boards.convert.doc_viewer import _render_markdown

        html = _render_markdown("# 标题\n\n```json\n{\"a\": 1}\n```")
        return "<pre" in html and "#f6f8fc" in html.lower()

    def check_json_highlight() -> bool:
        from modu_workbench.boards.convert.doc_viewer import _render_json

        return "color:" in _render_json('{"a": 1}')

    def check_lexer_by_name() -> bool:
        from pygments.lexers import get_lexer_by_name

        return get_lexer_by_name("json") is not None

    def check_single_qt_stack() -> bool:
        """v1.0.0：前端统一为 Qt 单栈。

        用 AST 精确检查"有没有任何模块 import 了 QtWebEngine"（注释里提到不算），
        并确认内嵌前端产物目录已不存在。
        """
        import ast
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent
        needle = "PySide6.QtWebEngine"
        for path in list(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"), filename=str(path))
            except SyntaxError:
                return False
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    if any(alias.name.startswith(needle) for alias in node.names):
                        return False
                elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith(needle):
                    return False
        return not (root / "webfront").exists()

    def check_pdf_convert() -> bool:
        """墨软转换：PDF 文本抽取依赖 pypdf（打包时必须随包）。"""
        try:
            from pypdf import PdfWriter, PdfReader

            import io

            writer = PdfWriter()
            writer.add_blank_page(width=200, height=200)
            buffer = io.BytesIO()
            writer.write(buffer)
            buffer.seek(0)
            return len(PdfReader(buffer).pages) == 1
        except Exception:  # noqa: BLE001
            return False

    def check_multimedia() -> bool:
        """墨软乐库播放依赖 QtMultimedia（打包必须带上）。"""
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer  # noqa: F401

            from modu_workbench.core.music import MULTIMEDIA_AVAILABLE

            return bool(MULTIMEDIA_AVAILABLE)
        except Exception:  # noqa: BLE001
            return False

    def check_multimedia_playback() -> bool:
        """真实解码自检：播放一段静音 WAV，确认播放位置前进（验证打包后的解码后端）。"""
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return True  # 无头环境不要求音频后端
        import tempfile
        import wave

        try:
            from PySide6.QtCore import QEventLoop, QTimer, QUrl
            from PySide6.QtWidgets import QApplication
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        except Exception:  # noqa: BLE001
            return False

        wav_path = ""
        try:
            app = QApplication.instance() or QApplication([])
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
                wav_path = handle.name
            # 3 秒静音 WAV（纯 Python 生成，无需 ffmpeg；静音避免自检发声）
            with wave.open(wav_path, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(22050)
                wav.writeframes(b"\x00\x00" * 22050 * 3)

            player = QMediaPlayer()
            audio = QAudioOutput()
            audio.setVolume(0.5)
            player.setAudioOutput(audio)
            state = {"position": 0, "error": "", "ended": False}
            loop = QEventLoop()

            def on_position(value: int) -> None:
                state["position"] = max(state["position"], int(value))
                if state["position"] > 0:
                    loop.quit()

            def on_status(status) -> None:  # noqa: ANN001
                if QMediaPlayer is not None and status == QMediaPlayer.MediaStatus.EndOfMedia:
                    state["ended"] = True
                    loop.quit()

            player.positionChanged.connect(on_position)
            player.mediaStatusChanged.connect(on_status)
            player.errorOccurred.connect(lambda error, message="": state.update(error=message or str(error)))
            player.setSource(QUrl.fromLocalFile(wav_path))
            player.play()

            QTimer.singleShot(6000, loop.quit)
            loop.exec()
            player.stop()
            return bool((state["position"] > 0 or state["ended"]) and not state["error"])
        except Exception:  # noqa: BLE001
            return False
        finally:
            if wav_path:
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass

    def check_music_core() -> bool:
        """音乐库存储可建表并完成一轮增删 + 音源注册表完整（验证打包模块齐全）。"""
        import tempfile
        from pathlib import Path as _Path

        from modu_workbench.core.music import MUSIC_TARGETS, MusicStorage, RemoteTrack, Track, registry

        with tempfile.TemporaryDirectory() as folder:
            store = MusicStorage(str(_Path(folder) / "music.db"))
            try:
                track_id = store.upsert_track(Track(path=str(_Path(folder) / "a.mp3"), title="t", format="mp3"))
                playlist_id = store.create_playlist("自检歌单")
                store.add_to_playlist(playlist_id, [track_id])
                ok = (
                    store.get_track(track_id) is not None
                    and store.get_playlist(playlist_id).track_count == 1
                    and len(MUSIC_TARGETS) >= 8
                )
            finally:
                store.close()

        reg = registry()
        providers = reg.order
        expected = {"netease", "kuwo", "audius", "archive", "ccmixter", "itunes", "jamendo", "url"}
        match_ok = False
        try:
            from modu_workbench.core.music.sources import best_match

            base = RemoteTrack(source="netease", remote_id="1", title="屋顶", artist="周杰伦", duration_ms=319_000)
            same = RemoteTrack(source="kuwo", remote_id="2", title="屋顶 Live", artist="周杰伦", duration_ms=317_000)
            match_ok = best_match(base, [same]) is same
        except Exception:  # noqa: BLE001
            match_ok = False
        return bool(ok and expected.issubset(set(providers)) and match_ok)

    def check_video_core() -> bool:
        """墨软影视核心：影视库可建表、m3u8 可解析、数据源注册表齐全、换源可定位同一集。"""
        import tempfile
        from pathlib import Path as _Path

        from modu_workbench.core.video import (
            Episode,
            RemoteVideo,
            Video,
            VideoRegistry,
            VideoStorage,
            parse_m3u8,
            parse_video_name,
            safe_filename,
        )
        from modu_workbench.core.video.downloader import build_filename, looks_like_video
        from modu_workbench.core.video.sources import (
            SourceError,
            SourceInfo,
            VideoSource,
            best_match,
        )

        # 1) 存储可建表并完成一轮增删
        with tempfile.TemporaryDirectory() as folder:
            store = VideoStorage(str(_Path(folder) / "video.db"))
            try:
                video_id = store.upsert_video(
                    Video(title="自检片", kind="movie", source="cms", remote_id="1")
                )
                playlist_id = store.create_playlist("自检分类", kind="category")
                store.add_to_playlist(playlist_id, [video_id])
                store.save_play_record(video_id, "正片", "1080P", "https://x/1.m3u8", "cms")
                store.add_history(video_id, "play", episode_label="正片")
                ok = (
                    store.get_video(video_id) is not None
                    and store.get_playlist(playlist_id).video_count == 1
                    and store.get_play_record(video_id, "正片") is not None
                    and store.list_history()[0].title == "自检片"
                )
            finally:
                store.close()

        # 2) m3u8 主清单可解析出多清晰度
        master = (
            "#EXTM3U\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080\n1080/index.m3u8\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720\n720/index.m3u8\n"
        )
        hls_ok = False
        try:
            playlist = parse_m3u8(master, "https://cdn.example.com/master.m3u8")
            best = playlist.best_variant()
            hls_ok = (
                playlist.is_master
                and len(playlist.variants) == 2
                and best is not None
                and best.height == 1080
                and playlist.variants[1].url.endswith("/720/index.m3u8")
            )
        except Exception:  # noqa: BLE001
            hls_ok = False

        # 3) 注册表：全部内置源已注册 + 换源能定位到同一集
        registry = VideoRegistry()
        expected = {"cms_360", "archive", "wikimedia", "url", "custom"}
        registered_ok = expected.issubset(set(registry.order))

        class _Dead(VideoSource):
            info = SourceInfo(key="dead", label="失效源", note="self-check")

            def search(self, keyword, kind="all", limit=30, page=1):  # noqa: ANN001, ANN003
                return [RemoteVideo(source="dead", remote_id="1", title="片",
                                    episodes=[Episode(name="第01集", url="u", index=0),
                                              Episode(name="第02集", url="u2", index=1)])]

            def play_url(self, video, episode, quality=None):  # noqa: ANN001, ANN003
                raise SourceError("线路失效")

        class _Good(VideoSource):
            info = SourceInfo(key="good", label="可用源", note="self-check")

            def search(self, keyword, kind="all", limit=30, page=1):  # noqa: ANN001, ANN003
                return [RemoteVideo(source="good", remote_id="9", title="片",
                                    episodes=[Episode(name="第01集", url="https://good/1", index=0),
                                              Episode(name="第02集", url="https://good/2", index=1)])]

        fallback_ok = False
        try:
            probe = VideoRegistry([_Dead(), _Good()])
            base = RemoteVideo(source="dead", remote_id="1", title="片",
                               episodes=[Episode(name="第01集", url="u", index=0),
                                         Episode(name="第02集", url="u2", index=1)])
            resolved = probe.resolve(base, base.episodes[1])
            fallback_ok = (
                resolved.source == "good"
                and resolved.switched_from == "dead"
                and resolved.episode is not None
                and resolved.episode.index == 1
            )
        except Exception:  # noqa: BLE001
            fallback_ok = False

        # 4) 文件命名 / 片名解析 / 下载产物嗅探 / 换源匹配打分
        util_ok = False
        try:
            title, label, index = parse_video_name("庆余年 S01E03")
            with tempfile.TemporaryDirectory() as folder:
                real = _Path(folder) / "a.ts"
                real.write_bytes(b"\x47" + b"\x00" * 200_000)
                fake = _Path(folder) / "b.ts"
                fake.write_bytes(b"<html>VIP</html>" + b" " * 200_000)
                sniff_ok = (
                    looks_like_video(real, "video/mp2t")
                    and not looks_like_video(fake, "text/html")
                )
            util_ok = (
                title == "庆余年" and label == "S01E03" and index == 3
                and safe_filename('a/b:c*d?"e') == "a_b_c_d_e"
                and build_filename(RemoteVideo(source="s", remote_id="1", title="片", year="2024"),
                                   Episode(name="第01集", url="u"), ".mp4") == "片 (2024) 第01集.mp4"
                and sniff_ok
                and best_match(
                    RemoteVideo(source="a", remote_id="1", title="片", year="2020"),
                    [RemoteVideo(source="b", remote_id="2", title="片", year="2020")],
                ) is not None
            )
        except Exception:  # noqa: BLE001
            util_ok = False

        return bool(ok and hls_ok and registered_ok and fallback_ok and util_ok)

    def check_ffmpeg_tools() -> bool:
        """随包 ffmpeg/ffprobe 可定位且能执行（音视频转换与 HLS 合流依赖它）。

        未随包时不视为失败：用户可自行安装或用 MODU_FFMPEG 指定 —— 下载会退回 .ts。
        """
        import subprocess

        from modu_workbench.core.platform.media import find_ffmpeg, find_ffprobe

        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            return True   # 可选依赖：缺省不算打包失败
        try:
            out = subprocess.run([ffmpeg, "-hide_banner", "-version"], capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=20,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if out.returncode != 0:
                return False
        except Exception:  # noqa: BLE001
            return False
        # ffprobe 可选（仅用于探测时长），定位不到也不判失败
        ffprobe = find_ffprobe()
        if ffprobe:
            try:
                probe = subprocess.run([ffprobe, "-hide_banner", "-version"], capture_output=True,
                                       text=True, encoding="utf-8", errors="replace", timeout=20,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if probe.returncode != 0:
                    return False
            except Exception:  # noqa: BLE001
                return False
        return True

    def check_gallery_core() -> bool:
        """墨软图库核心：建表读写、缩略图生成、感知哈希、编辑管线、本地增强算法。"""
        import tempfile
        from pathlib import Path as _Path

        import numpy as _np
        from PIL import Image as _Image

        from modu_workbench.core.gallery import (
            EditStep,
            ImageLibrary,
            ImageItem,
            ImageStorage,
            ai as _ai,
            apply_enhancement,
            apply_steps,
            compute_quality_metrics,
            dhash_image,
            hamming_distance,
        )

        with tempfile.TemporaryDirectory() as folder:
            root = _Path(folder)
            # 1) 造两张图（其一一模一样的副本用于去重）
            source = root / "photos"
            source.mkdir()
            try:
                array = (_np.arange(240 * 320 * 3) % 251).reshape((240, 320, 3)).astype("uint8")
                _Image.fromarray(array).save(source / "a.jpg")
                _Image.new("RGB", (320, 240), (200, 120, 60)).save(source / "b.png")
            except Exception:  # noqa: BLE001
                return False
            import shutil as _shutil

            _shutil.copy2(source / "a.jpg", source / "a_copy.jpg")

            store = ImageStorage(str(root / "gallery.db"))
            try:
                library = ImageLibrary(store, root / "cache", image_dir=root / "images")
                result = library.import_paths([str(source)])
                dedupe_ok = result.added == 2 and result.skipped_duplicate == 1

                # 2) 缩略图真的生成
                first = result.images[0]
                thumb = library.thumbnail(first)
                thumb_ok = bool(thumb) and _Path(thumb).is_file() and _Path(thumb).stat().st_size > 0

                # 3) 相册 / 标签 / 收藏
                album_id = library.create_album("自检相册")
                library.add_to_album(album_id, [item.id for item in result.images])
                library.tag_images([first.id], "自检标签")
                library.toggle_favorite(first.id)
                store_ok = (
                    store.get_album(album_id).image_count == 2
                    and store.list_tags()
                    and store.get_image(first.id).favorited
                )

                # 4) 编辑管线（裁剪 + 旋转 + 滤镜 + AI 增强）
                steps = [
                    EditStep("crop", {"box": [8, 8, 120, 90]}),
                    EditStep("rotate", {"angle": 90}),
                    EditStep("filter", {"name": "grayscale", "amount": 1.0}),
                    EditStep("ai", {"kind": "auto_enhance"}),
                ]
                library.save_steps(first.id, steps)
                exported = library.export_edited(first, root / "edited.jpg", fmt="jpg")
                edit_ok = exported.is_file() and exported.stat().st_size > 0

                # 5) 本地增强算法 + 画质指标
                metrics = compute_quality_metrics(
                    apply_enhancement("sharpen", _Image.new("RGB", (64, 64), (120, 120, 120)))
                )
                enhance_ok = all(isinstance(value, float) for value in metrics.values())

                # 6) 感知哈希能区分不同图
                hash_a = dhash_image(_Image.open(source / "a.jpg"))
                hash_b = dhash_image(_Image.open(source / "b.png"))
                hash_ok = bool(hash_a) and bool(hash_b) and hash_a != hash_b
            finally:
                store.close()

        # 7) DeepSeek 客户端可构造、未配置时给出明确错误（不联网）
        ai_ok = False
        try:
            client = _ai.DeepSeekClient(_ai.AiConfig(api_key=""))
            try:
                client.test_connection()
            except _ai.AiConfigError:
                ai_ok = True
            except Exception:  # noqa: BLE001  未配置时不允许发起网络请求
                ai_ok = False
        except Exception:  # noqa: BLE001
            ai_ok = False

        return bool(dedupe_ok and thumb_ok and store_ok and edit_ok and enhance_ok and hash_ok and ai_ok)

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

    def check_branding_icon() -> bool:
        """品牌图标：随包 app.ico 可定位，且能加载出多尺寸 QIcon（窗口/任务栏图标）。"""
        try:
            from PySide6.QtGui import QIcon
            from PySide6.QtWidgets import QApplication

            from modu_workbench.services.assets import app_icon_path

            QApplication.instance() or QApplication([])   # QIcon/QPixmap 需要 GUI 应用实例
            path = app_icon_path()
            if path is None or not path.is_file():
                return False
            icon = QIcon(str(path))
            return (not icon.isNull()) and bool(icon.availableSizes())
        except Exception:  # noqa: BLE001
            return False

    record("markdown_extra", check_markdown_extra)
    record("markdown_codeblock", check_markdown_codeblock)
    record("json_highlight", check_json_highlight)
    record("lexer_by_name", check_lexer_by_name)
    record("single_qt_stack", check_single_qt_stack)
    record("pdf_convert", check_pdf_convert)
    record("multimedia", check_multimedia)
    record("multimedia_playback", check_multimedia_playback)
    record("music_core", check_music_core)
    record("video_core", check_video_core)
    record("gallery_core", check_gallery_core)
    record("document_core", check_document_core)
    record("pdf_print_engine", check_pdf_print_engine)
    record("llm_core", check_llm_core)
    record("ffmpeg_tools", check_ffmpeg_tools)
    record("branding_icon", check_branding_icon)

    try:
        with open(output_path, "w", encoding="utf-8") as fp:
            json.dump(results, fp, ensure_ascii=False, indent=2)
    except Exception as error:  # noqa: BLE001
        return 2 if f"write_error={error}" else 2
    return 0 if all(value is True for value in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
