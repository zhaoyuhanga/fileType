"""入口：创建 QApplication、套用全局主题并启动 AppShell。"""
from __future__ import annotations

import os
import sys


def main(argv: list[str] | None = None) -> int:
    # 依赖自检（打包验证用）：MODU_CHECK_DEPS=输出路径 时只做检查并退出，不启动界面。
    check_out = os.environ.get("MODU_CHECK_DEPS")
    if check_out:
        return _run_dependency_check(check_out)

    from PySide6.QtWidgets import QApplication

    from . import APP_NAME, APP_SLOGAN, __version__
    from .app_shell import AppShell
    from .ui_kit.theme import apply_theme

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
        from modu_workbench.boards.doc_viewer import _render_markdown

        return "<h1>标题</h1>" in _render_markdown("# 标题\n\n正文。")

    def check_markdown_codeblock() -> bool:
        from modu_workbench.boards.doc_viewer import _render_markdown

        return "<pre" in _render_markdown("# 标题\n\n```json\n{\"a\": 1}\n```")

    def check_json_highlight() -> bool:
        from modu_workbench.boards.doc_viewer import _render_json

        return "color:" in _render_json('{"a": 1}')

    def check_lexer_by_name() -> bool:
        from pygments.lexers import get_lexer_by_name

        return get_lexer_by_name("json") is not None

    def check_webengine_import() -> bool:
        try:
            import PySide6.QtWebEngineWidgets  # noqa: F401
            import PySide6.QtWebEngineCore  # noqa: F401

            return True
        except Exception:  # noqa: BLE001
            return False

    def check_webengine_render() -> bool:
        """真实渲染自检：创建 QWebEngineView 并确认 HTML 加载成功。"""
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return True  # 无头环境不要求 Chromium 渲染
        try:
            from PySide6.QtCore import QEventLoop, QTimer
            from PySide6.QtWidgets import QApplication
            from PySide6.QtWebEngineWidgets import QWebEngineView

            app = QApplication.instance() or QApplication([])
            view = QWebEngineView()
            state = {"ok": False}
            view.loadFinished.connect(lambda ok: state.update(ok=bool(ok)))
            view.setHtml("<!doctype html><html><body>ok</body></html>")
            loop = QEventLoop()
            view.loadFinished.connect(lambda _ok: loop.quit())
            QTimer.singleShot(4000, loop.quit)
            loop.exec()
            view.close()
            return bool(state["ok"])
        except Exception:  # noqa: BLE001
            return False

    def check_webfront_present() -> bool:
        from modu_workbench.services.webfront import webfront_dir

        front = webfront_dir()
        if front is None:
            return False
        return (front / "index.html").is_file() and (front / "bridge_shim.js").is_file()

    def check_multimedia() -> bool:
        """墨读音乐播放依赖 QtMultimedia（打包必须带上）。"""
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
        """音乐库存储可建表并完成一轮增删（验证 sqlite 表结构与打包模块完整）。"""
        import tempfile
        from pathlib import Path as _Path

        from modu_workbench.core.music import MUSIC_TARGETS, MusicStorage, Track

        with tempfile.TemporaryDirectory() as folder:
            store = MusicStorage(str(_Path(folder) / "music.db"))
            try:
                track_id = store.upsert_track(Track(path=str(_Path(folder) / "a.mp3"), title="t", format="mp3"))
                playlist_id = store.create_playlist("自检歌单")
                store.add_to_playlist(playlist_id, [track_id])
                return (
                    store.get_track(track_id) is not None
                    and store.get_playlist(playlist_id).track_count == 1
                    and len(MUSIC_TARGETS) >= 8
                )
            finally:
                store.close()

    record("markdown_extra", check_markdown_extra)
    record("markdown_codeblock", check_markdown_codeblock)
    record("json_highlight", check_json_highlight)
    record("lexer_by_name", check_lexer_by_name)
    record("webengine_import", check_webengine_import)
    record("webengine_render", check_webengine_render)
    record("webfront_present", check_webfront_present)
    record("multimedia", check_multimedia)
    record("multimedia_playback", check_multimedia_playback)
    record("music_core", check_music_core)

    try:
        with open(output_path, "w", encoding="utf-8") as fp:
            json.dump(results, fp, ensure_ascii=False, indent=2)
    except Exception as error:  # noqa: BLE001
        return 2 if f"write_error={error}" else 2
    return 0 if all(value is True for value in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
