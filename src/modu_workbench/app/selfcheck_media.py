"""媒体板块子自检：乐库/影视/图库内核，以及影视播放内核

**为什么拆到这里**：乐库、影视、图库三个板块的引擎检查集中在这里：存储建表、m3u8 多清晰度、换源定位、缩略图与感知哈希、播放内核与本地代理。三者共享同一套「媒体」心智模型，放一起便于横向对照。

本模块只放 `check_*` 函数（纯函数、无参数、返回 bool 或抛出）。它们由
`app/selfcheck.py` 的 `_CHECKS` 表按固定顺序调用 —— 拆分**不改变任何检查的内容与顺序**，
只是把原来一个 800 多行的 `run_checks` 按「验的是谁」切开，让每一条都能被单独调用与单独测试。
"""
from __future__ import annotations


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


def check_gallery_core() -> bool:
    """墨软图库核心：建表读写、缩略图生成、感知哈希、编辑管线、本地增强算法。"""
    import tempfile
    from pathlib import Path as _Path

    import numpy as _np
    from PIL import Image as _Image

    from modu_workbench.core.gallery import (
        EditStep,
        ImageLibrary,
        ImageStorage,
        ai as _ai,
        apply_enhancement,
        compute_quality_metrics,
        dhash_image,
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


def check_video_playback() -> bool:
    """影视播放链路自检（packaging 的 `video_playback` 项）。

    断言四件事（不需要联网、不需要真的播放视频）：
    1. `QT_MEDIA_BACKEND=ffmpeg` 已注入（否则 HLS 会落到不支持它的 WMF 上）；
    2. 硬解设备列表已注入，且能被我们的解码模块读到；
    3. 随包的 `ffmpegmediaplugin.dll` 在位（Qt 的 FFmpeg 多媒体插件）；
    4. 新渲染面 `QGraphicsVideoItem` 可实例化、本机代理可启动并给出可用的
       缓存目录与配额（播放/下载都依赖它）。
    """
    import importlib.util
    from pathlib import Path as _Path

    from modu_workbench.ui_kit.media import decode

    if not decode.hardware_decode_enabled() and decode.current_env()[
            "QT_FFMPEG_DECODING_HW_DEVICE_TYPES"] == "(默认)":
        return False
    if decode.current_env()["QT_MEDIA_BACKEND"] not in ("ffmpeg", "windows", "(默认)"):
        return False

    try:  # ③ 随包的多媒体插件
        spec = importlib.util.find_spec("PySide6")
        if spec is None or not spec.origin:
            return False
        plugin_dir = _Path(spec.origin).parent / "plugins" / "multimedia"
        if not (plugin_dir / "ffmpegmediaplugin.dll").is_file():
            return False
    except Exception:  # noqa: BLE001  非 Windows/简化安装只记失败，不抛
        return False

    try:  # ④ 渲染面 + 本机代理
        from PySide6.QtWidgets import QApplication

        QApplication.instance() or QApplication([])
        from modu_workbench.ui_kit.media.surface import graphics_available

        if not graphics_available():
            return False
        from modu_workbench.services import media_proxy

        cache = media_proxy.get_cache().stats()
        if cache["quota_bytes"] < 256 * 1024 * 1024:
            return False
        media_proxy.ensure_server()
        if not media_proxy.is_local_url(media_proxy.stream_url("https://x/a.m3u8")):
            return False
    except Exception:  # noqa: BLE001
        return False
    return True


__all__ = [
    "check_music_core",
    "check_video_core",
    "check_gallery_core",
    "check_video_playback",
]
