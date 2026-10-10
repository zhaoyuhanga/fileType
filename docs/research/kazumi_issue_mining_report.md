# Kazumi (`Predidit/Kazumi`) Issue-Tracker Mining — Known Limitations, Common Problems, Pitfalls

**Method.** All items below were retrieved live from the GitHub REST API (`/repos/Predidit/Kazumi/issues`, `/search/issues`, `/releases`, `/releases/tags/<tag>`) on the dates of this session. Nothing is invented; every number, title and state comes from the API response. Items marked **(PR)** are pull requests that the tracker returned and that carry substantive root-cause discussion (the task's `pull_request` filter is noted explicitly so they are not mistaken for bug reports). `state` is the value returned by the API (`state_reason: reopened` is shown as `open(reopened)`).

---

## 1. 播放失败 / 黑屏 / 无法解析视频地址

- **#1276 · [Bug]: 部分荣耀设备更新1.7.8后开始出现闪退现象 · closed · 105 comments** — https://github.com/Predidit/Kazumi/issues/1276
  Honor Magic 5 Pro (MagicOS 9.0.187, Android 15) crashes after ~22 min of unattended playback. Logs show repeated `h264_mediacodec: Both surface and native_window are NULL`, `tcp: ffurl_read returned 0xffffff92` on `m3u8.girigirilove.com` HLS playlists, AAC decode errors, A/V desync, and separately `webDav download and patch history failed … Not Found`. Highest-traffic issue in the tracker; the crash appears **starting with 1.7.8**.

- **#681 · [Bug] 播放器在某些荣耀设备上闪退 · closed · 65 comments** — https://github.com/Predidit/Kazumi/issues/681
  Maintainer-owned tracking issue for the Honor crash family (#680 #664 #596). Maintainer's stated conclusions: switching the rendering engine does not help, no Flutter-community reports, so it is **not Flutter**; identical crash locations and the fact that **disabling the advanced cache does not mitigate** it mean it is **not memory exhaustion** but an **mpv-level compatibility problem**. Fix was assumed to come from #541.

- **#1652 · [Bug]: Android 14+vulkan 1.4无法播放视频 · closed · 18 comments** — https://github.com/Predidit/Kazumi/issues/1652
  After an update every title parses but shows only audio with a purple picture; the same source plays in an external player. Root cause is in the log: `shaderc: internal error: compilation succeeded but failed to optimize: Invalid SPIR-V binary version 1.6 for target environment SPIR-V 1.0 (under Vulkan 1.0 semantics)` → `vk_compile_glsl(...): VK_ERROR_INITIALIZATION_FAILED`, i.e. mpv's `gpu-next`/libplacebo path failing on older Vulkan.

- **#1282 · [Bug]: 通过flatpak安装的kazumi OpenGL加载失败 · closed · 14 comments** — https://github.com/Predidit/Kazumi/issues/1282
  Flatpak sandbox on Arch/KDE Wayland: `libmpv_render: glGetString(GL_VERSION) returned NULL` / `OpenGL not initialized.`, plus `Unsupported hwdec: drm`. Toggling hardware acceleration in settings lets exactly one video play, then the second fails again.

- **#1590 · [Bug]: linux平台下从1.8.8版本开始无法播放 · OPEN (reopened) · 26 comments** — https://github.com/Predidit/Kazumi/issues/1590
  Any source hangs on the loading page; **1.8.7 is the last working version**. Log shows `PlatformException(failed to evaluate javascript., Unsupported result type)` raised from `WebviewLinuxItemControllerImpel.redirect2Blank` (`desktop_webview_window`), so the Linux webview-based parser cannot be driven.

- **#1055 · [Bug][Linux]: 视频资源解析器某些情况下无法工作 · closed · 27 comments** — https://github.com/Predidit/Kazumi/issues/1055
  Windows is fine but on Manjaro the parser always times out, with no network-level log records; the reporter states the workaround from a similar issue did not apply, so it was filed as a distinct bug.

- **#1348 · [Bug]: Linux平台下无法使用nvdec解码 · OPEN · 23 comments** — https://github.com/Predidit/Kazumi/issues/1348
  Linux/NVIDIA: media_kit falls back to `Using S/W rendering` and nvdec is never used, leaving software decoding.

- **#2039 · [Bug]: 视频解析完成后，会直接导致软件退出 · OPEN · 12 comments** — https://github.com/Predidit/Kazumi/issues/2039
  Windows 10: as soon as parsing finishes and the player starts loading, the app exits by itself with no error-log entry; not present in other versions (a version-introduced crash).

- **#1489 · [Bug]: flatpak linux在1.8.8的引入的设置播放速度失败的bug，影响播放器性能 · closed · 15 comments** — https://github.com/Predidit/Kazumi/issues/1489
  Playback succeeds but is extremely stuttery on a low-end Celeron N3450 because hardware decoding never engages (`Unsupported hwdec: vulkan-copy` → `Using software decoding.`). The visible error is a **regression introduced in 1.8.8**: `设置播放速度失败 LateInitializationError: Field 'danmakuController' has not been initialized.`

- **#1615 · [Bug]: VIVO OriginOS 6，播放视频一段时间就卡住了，最近几个版本都如此 · closed · 31 comments** — https://github.com/Predidit/Kazumi/issues/1615
  VIVO X200U / OriginOS 6 (Android 16): after one or two episodes the app freezes and the process must be killed; no error log content, and it reproduces across recent versions.

- **#1756 · [Bug]: 在两倍速播放的时候每一集必在15-24分钟卡死画面，能听到声音 · closed · 31 comments** — https://github.com/Predidit/Kazumi/issues/1756
  At 2× speed the picture freezes once per episode between 15–24 min while audio continues; reproduced across several sources (xfdm, 7sefun) and still present in 2.0.2. Logs also show CDN connections to `*.tiktokcdn.com` timing out.

- **#226 · [bug]：android/iOS 偶发的视频比例错误 · closed · 23 comments** — https://github.com/Predidit/Kazumi/issues/226
  Long-pressing to speed up playback intermittently squeezes the video horizontally; only a refresh restores the aspect ratio.

- **#468 · 正在使用局域网 网络异常 的问题 · OPEN · 19 comments** — https://github.com/Predidit/Kazumi/issues/468
  Search is unusable ("正在使用局域网 网络异常") while 追番/history still play fine; VPN on/off and version updates do not help. Logs: `Resolve bangumi list failed type 'Null' is not a subtype of type 'Iterable<dynamic>'`, `Resolve calendar failed type '_Map<String, String>' is not a subtype of type 'Iterable<dynamic>'`.

- **#2450 · [BUG]: 7sefun规则无法读取七彩b线线路 · closed · 3 comments** — https://github.com/Predidit/Kazumi/issues/2450
  Kazumi only exposes 七彩a线; the 七彩b line (`/vodplay/27117-2-1.html`) is not recognised. Logs are dominated by upstream anti-bot responses: `403` from `agedm.io`, `mwcy.net`, `dalvdm.cc`, plus `receiveTimeout` on `xfdm.pro` at the fixed 12 s limit.

- **#2455 · [BUG]: xfdm源搜索不到任何动漫 · closed · 3 comments** — https://github.com/Predidit/Kazumi/issues/2455
  Source stopped working overnight; source test reports a network error. Logs show `Plugin: MXdm search skipped 5 node(s): 搜索节点 N 缺少名称或来源，已跳过` (rule nodes silently dropped), connection timeouts, and `api.bgm.tv` unreachable.

- **#2612 · [BUG]: xfdmnext解析超时 · OPEN · 8 comments** — https://github.com/Predidit/Kazumi/issues/2612
  On the **same network** the phone plays xfdmnext fine but the tablet cannot play at all (HarmonyOS 4.2 tablet). Logs show `Failed host lookup: 'enlienli.link'`, `404` (libvio), `403` (TvTFun), `599` (aowu) and `receiveTimeout` (ylys) — i.e. per-device DNS/egress plus upstream blocking.

- **#360 · clicli更新之后ios用不了，但之前可以 · closed · 102 comments** — https://github.com/Predidit/Kazumi/issues/360
  After the upstream `clicli` site changed, the source stopped working on iOS although it had worked before — the canonical "upstream changed, rule/parser broke" report.

- **#53 · 在时间表那里点进去看番，有些番的某个源就看不了，而搜索出来的番的片源却正常显示 · closed · 49 comments** — https://github.com/Predidit/Kazumi/issues/53
  Entering via the timetable (追番/时间表) yields no sources for some titles, while the same title found via search does — a metadata/subject-id resolution mismatch rather than a parser failure.

- **#1823 · 番源的验证问题 · closed · 29 comments** — https://github.com/Predidit/Kazumi/issues/1823
  Follow-up to #1797/#1804: sources now sit behind an interactive human/verification step, and the reporter asks whether the acquisition flow can be adapted. This is the generic "source requires verification / JS challenge" class predicted in #914.

---

## 2. 规则失效 / 规则解析问题

- **#914 · 解析模块迭代 · OPEN · 54 comments** — https://github.com/Predidit/Kazumi/issues/914
  Maintainer-authored root-cause document. Kazumi cannot intercept requests inside the webview to obtain direct video URLs; it can only inject JS into the page context, and **because of CORS the injected script's scope does not include embedded iframes**, so many "unclean" workarounds were needed — this is stated as the cause of **slow parsing and parse failures on some sites**. The proposed `flutter_inappwebview` request-hijack has three accepted blockers: (1) MB-scale objects over PlatformChannel → performance loss/freezes/crashes; (2) hijacked requests must then handle **referer validation and JS challenge mechanisms inside embedded iframes, which is "基本无法解决"**; (3) the plugin is incompatible with Linux and ohos, breaking cross-platform consistency.

- **#1524 · (PR) 本PR的目的是为了尝试解决看番时遭遇的广告问题 · closed (not merged) · 37 comments** — https://github.com/Predidit/Kazumi/pull/1524
  Documents a widespread source-side failure mode: the stream occasionally plays a gambling ad and then loops forever, restarting at 0:00 with a 10 s ad, together with sporadic decode errors (`co located POCs unavailable`). Proposes skipping by heuristic; the author notes it is not a root-cause fix. Shows that **ads/injected segments are a first-class rule/stream problem**.

- **#1439 · (PR) 添加规则测试页面 · closed (merged) · 14 comments** — https://github.com/Predidit/Kazumi/pull/1439
  Adds a rule-testing page — evidence that rule authoring/debugging was a felt pain point.

- **#508 · (PR) enhance plugin update · closed (merged) · 17 comments** — https://github.com/Predidit/Kazumi/pull/508
  Improves the rule/plugin update flow, including a "update all rules" button in the UI, i.e. rule updates are a routine operational burden.

- **#2118 · [Bug]: 7sefun源下载程序崩溃 · closed · 5 comments** — https://github.com/Predidit/Kazumi/issues/2118
  The 7sefun rule crashes the download routine.

- **#1940 · 超时空辉夜姬看不了 · closed · 3 comments** — https://github.com/Predidit/Kazumi/issues/1940
  A specific title could not be played at all (source/rule coverage gap).

- **#53 / #2450 / #2455 / #2612 / #360 / #1823** (detailed in bucket 1) are equally rule/source-failure evidence: node skipping (`缺少名称或来源`), one line of a multi-line source not recognised, and upstream 403/404/599/verification gates.

- **Rule-format / validation capability changes** (from release notes, see bucket 6): 2.1.2 introduced **"新增规则自定义 JS 验证支持"**, i.e. rules gained a custom-JS validation stage; this is the point in the project history where a rule could depend on executable JS verification rather than pure declarative matching.

---

## 3. 弹幕问题

- **#671 · 无法搜索弹幕 · closed · 4 comments** — https://github.com/Predidit/Kazumi/issues/671
  Danmaku search returns nothing; the reporter tried direct connection, a VPN and a clean reinstall without success.

- **#1819 · [Bug]: 弹幕获取失败 · closed · 3 comments** — https://github.com/Predidit/Kazumi/issues/1819
  iOS 26. Log: `type 'Null' is not a subtype of type 'List<dynamic>'` thrown in `_PlayerController.getDanDanmakuByBgmBangumiID (player_controller.dart:730)` followed by `PlayerController: failed to get danmaku [BgmBangumiID] 515759` — the third-party danmaku response was null and the code did not defend against it.

- **#2044 · [Bug]: 频繁卡顿 · OPEN · 3 comments** — https://github.com/Predidit/Kazumi/issues/2044
  Windows/i5-13500HX/4060: "3 seconds frozen, 1 second playback". Same danmaku code path fails (`player_controller.dart:737`, `NoSuchMethodError: The method '[]' was called on null … Tried calling: []("episodes")`, `failed to get danmaku [BgmBangumiID] 451756`), plus `Plugin: getPluginList error: type '_Map<String, String>' is not a subtype of type 'String'`.

- **#1839 · [Bug]: iOS端无法使用远程投屏 · OPEN · 3 comments** — https://github.com/Predidit/Kazumi/issues/1839
  100 % reproducible on iPhone 17 Pro Max / iOS 26. Logs combine `getPluginList error: type '_Map<String, String>' is not a subtype of type 'String'`, the danmaku null-list failure for `BgmBangumiID 522414`, and `type 'Null' is not a subtype of type 'List<dynamic>'`.

- **#1518 · [Bug]: linux flatpak弹幕显示异常，滚动起来非常卡顿 · closed · 23 comments** — https://github.com/Predidit/Kazumi/issues/1518
  Flatpak/KDE Wayland: danmaku with white stroke render incorrectly, rolling danmaku stutters badly ("性能好像不如1.8.8之前"), and enabling danmaku transparency causes frequent screen flashing; no error is logged.

- **#105 · [Bug]: 顶部弹幕有时候会到中间 · closed · 9 comments** — https://github.com/Predidit/Kazumi/issues/105
  Top-anchored danmaku occasionally drift into the middle of the screen — a track-allocation bug.

- **#1335 · 弹幕显示区域再细分 · closed · 7 comments** — https://github.com/Predidit/Kazumi/issues/1335
  Request to subdivide danmaku display regions (density/overlap complaints).

- **#1489** (see bucket 1) is simultaneously a danmaku bug: `LateInitializationError: Field 'danmakuController' has not been initialized` aborts speed setting on flatpak.

- **#1964 · (PR) feat: 添加弹幕列表面板 · closed · 50 comments** — https://github.com/Predidit/Kazumi/pull/1964
  Large danmaku-UI rework (list panel), showing danmaku interaction/performance was a sustained area of churn.

- **Release-note evidence of systemic danmaku issues:** 2.1.2 *"新增弹幕时间轴偏移设置，优化弹幕发射逻辑 (#369)"* (timeline **offset** setting → known sync/mismatch problem) and 2.3.4 *"改进的弹幕检索流程 (@moonrailgun)"* (search/matching flow rework).

---

## 4. 性能与资源问题（超分卡顿 / 内存 / GPU）

- **#739 · Linux 超分辨率 GPU 负载比 Windows 多 · closed · 51 comments** — https://github.com/Predidit/Kazumi/issues/739
  With super-resolution on the 效率档 preset: Windows ≈60 % GPU; Linux (AMD Radeon 660M iGPU) hits **100 % GPU with visible stutter and a laggy mouse**. i.e. the Anime4K/GLSL pipeline is far more expensive on Linux.

- **#591 · [问题询问] 疑似太吃 GPU · closed · 7 comments** — https://github.com/Predidit/Kazumi/issues/591
  With `hwdec=auto-safe` on Intel UHD Graphics, Kazumi holds a steady **~70 % GPU** during playback, versus **~20 %** for PotPlayer using hardware DXVA — a large baseline rendering overhead.

- **#995 · [Android] 支持 HDR 和杜比视界 · OPEN · 25 comments** — https://github.com/Predidit/Kazumi/issues/995
  Maintainer explanation: **because of Flutter Texture limitations, HDR and Dolby Vision cannot be supported**; the plan is to migrate media_kit's Flutter Texture path to Android **Hybrid Composition** platform views. Also states the participating Android test path has no HDR sources, so priority is low — a standing platform limitation.

- **#2233 · [Bug]: Windows版在近两个版本很容易出现CPU瞬间占满卡死 · closed · 10 comments** — https://github.com/Predidit/Kazumi/issues/2233
  AMD AI 7 350 + RTX 5060. Certain in-app clicks spike the AMD CPU to 100 % and thermally throttle the app; playing and **immediately going fullscreen triggers it almost every time**, even freezing/crashing. Logs tie the moment to `_PlayerItemState._syncHistoryWithWebDav` being invoked from `_handleFullscreenChange`, throwing `Exception: History is currently syncing` → `WebDav: auto history sync failed`.

- **#1926 · [Bug]: Xiaomi 14在使用最新的2.0.6版本时会出现异常发热 · closed · 6 comments** — https://github.com/Predidit/Kazumi/issues/1926
  Abnormal device heating on Xiaomi 14 with 2.0.6.

- **#2044 / #1756 / #1615 / #1518** (buckets 1 and 3) are all performance-class reports: 3 s freeze per 1 s of playback, per-episode picture freeze at 2× speed, app freeze after 1–2 episodes, danmaku stutter under flatpak.

- **Hardware-decoding fallback is a recurring performance root cause:** #1348 (`Using S/W rendering` on Linux/NVIDIA), #1489 (`Unsupported hwdec: vulkan-copy`), #1282 (`Unsupported hwdec: drm`), #739 (Linux GPU saturation).

- **Maintainer statement on memory/low-memory mode** — in #681: because the crash position is consistent and **disabling the advanced cache (高级缓存) does not mitigate it**, the crash is unrelated to memory exhaustion; i.e. lowering cache/memory pressure is not a workaround.

---

## 5. 平台 / 地区限制、网络、代理、WebDAV 同步、一起看 (SyncPlay)

### 地区封锁（Bangumi 被墙）— maintainer announcement
- **#2133 · [Announcement] 对于近期无法正常使用问题的回复 · closed · 19 comments (26 reactions)** — https://github.com/Predidit/Kazumi/issues/2133
  Bangumi, the metadata backend Kazumi depends on, was **massively blocked by the GFW**; confirmed blocked endpoints: `https://api.bgm.tv/`, `https://next.bgm.tv/`, `https://chii.in/`, `https://bangumi.tv/`. Mainland users therefore cannot load covers/names. Documented workarounds: proxy software with **TUN mode**, or configuring the in-app proxy. **Important explicit limitation:** *"新版本中，启用镜像后无法同步属于预期行为"* — with the mirror enabled, **sync is expected not to work** (mirror-side user authentication is technically hard and a security risk). Related: #2129 #2130 #2132.

- **#2341 · [Bug]: 2.2.0版本多客户端无法同步 · closed · 5 comments** — https://github.com/Predidit/Kazumi/issues/2341
  Regression in 2.2.0 (2.1.8 unaffected): Android and Windows both report sync success with **no error**, yet a title added on device A never appears on device B. Silent-failure sync.

- **#138 · WebDAV多端/跨设备同步 · OPEN (reopened) · 20 comments** — https://github.com/Predidit/Kazumi/issues/138
  Nutstore WebDAV: progress from the phone never reaches the PC client; syncing the same authorisation reports "未经授权" (unauthorised).

- **#861 · [Bug]: webdav同步错误 · closed · 16 comments** — https://github.com/Predidit/Kazumi/issues/861
  Nutstore sync restores the wrong episode (episode 3 state while watching episode 4); manual up/download and file deletion do not help. Logs expose several distinct implementation defects: `Invalid argument(s): No host specified in URI /kazumiSync/histories.tmp.cache`, `FileSystemException: Cannot rename file to '…\hive\histories.hive' … (OS Error: 系统无法将文件移到不同的磁盘驱动器, errno = 17)`, `FileSystemException: File closed`, and `HttpException: Content size exceeds specified contentLength. 143472 bytes written while expected 141120`.

- **#1280 · 无法同步追番列表 · closed · 10 comments** — https://github.com/Predidit/Kazumi/issues/1280
  With Nutstore, playback history syncs but **syncing the 追番 list always errors**, on both Win10 and Android.

- **#1204 · [Enhancement] 优化 WebDAV 同步 · OPEN · 27 comments** — https://github.com/Predidit/Kazumi/issues/1204
  Requests a more robust WebDAV implementation modelled on PicaComic: **user-defined directory, error retry, timestamp-based file naming** — i.e. exactly the gaps that #861 exposes.

- **#61 · PC端历史记录&同步设置存在问题 · closed · 12 comments** — https://github.com/Predidit/Kazumi/issues/61
  WebDAV connects, `kazumiSync` folder is created with read/write rights, yet no watch records are ever generated so synchronisation always "fails".

- **#54 · [功能请求]可不可以请求在云同步中加入同步追番列表的功能 · closed · 24 comments** — https://github.com/Predidit/Kazumi/issues/54
  Long-running request to sync the 追番 list via cloud sync (was later added; #1280 shows it is fragile).

### 一起看 / SyncPlay
- **#834 · 基于 SyncPlay 实现放映室聊天 · closed · 16 comments** — https://github.com/Predidit/Kazumi/issues/834
  Maintainer's design issue. Stated blocker: **Dart cannot establish a `secureSocket` to the official SyncPlay server, and plain sockets cannot be upgraded to `secureSocket`; the attempts do not throw but simply time out**, suspected cause `syncplay 官方服务器使用了相当过时的 tls 版本`. Work had to proceed in three stages (TLS → custom SyncPlay protocol messages → chat UI). The feature shipped in 2.0.0 (`新增一起看模式下弹幕聊天功能 (#834)`).

### 平台 / 系统限制
- **#1056 · [Bug]: 关于在澎湃2小窗不显示问题 · closed · 29 comments** — https://github.com/Predidit/Kazumi/issues/1056
  HyperOS 2 (Android 15) small-window mode fails to display, 100 % reproducible on multiple Xiaomi devices; no exception is thrown.

- **#666 · [Bug]: 鸿蒙next系统无法打开软件 · closed · 19 comments** — https://github.com/Predidit/Kazumi/issues/666
  HarmonyOS NEXT cannot launch the app.

- **#1676 · [Bug]: 安卓9打开白屏 · closed · 20 comments** — https://github.com/Predidit/Kazumi/issues/1676
  Android 9 white screen; fixed in 2.0.0 (`修复 Andorid8/9 应用白屏的问题 (#1676)`), so pre-2.0.0 releases were broken on Android 8/9.

- **#235 · Kazumi会上鸿蒙NEXT平台吗 · closed · 28 comments** — https://github.com/Predidit/Kazumi/issues/235
  OpenHarmony's Flutter version is too old and many libraries are not ported, which is why a HarmonyOS NEXT build is not feasible.

- **#2101 · F-Droid reproducible build failed · OPEN · 18 comments** — https://github.com/Predidit/Kazumi/issues/2101
  F-Droid cannot reproduce the build; the requested fix is to **disable the build-id of `dartjni`**. Blocks verifiable/FOSS distribution.

- **#819 · [iOS] 自签名注意事项 · closed · 27 comments** and **#826 · [ios]（高版本ios自签相关）是什么原因导致了Kazumi无法直接使用sidestore侧载 · closed · 28 comments** — https://github.com/Predidit/Kazumi/issues/819 · https://github.com/Predidit/Kazumi/issues/826
  On high iOS versions SideStore sideloading of the signed IPA fails; #826 documents that re-packaging after signing with an (expired) enterprise certificate makes SideStore sideloading work, i.e. **how the IPA is packaged determines sideload compatibility**.

- **#1319 · 为kazumi架设一个官方网站 · closed · 24 comments** — https://github.com/Predidit/Kazumi/issues/1319
  A third-party "official site" was actually a set of links into a disbanded QQ group, and Chinese users often cannot reach GitHub — a distribution/region-access pitfall.

- **#15 · 寻找 Arch AUR 维护者 · closed · 25 comments** — https://github.com/Predidit/Kazumi/issues/15
  Linux packaging (AUR) had to be maintained by outside volunteers; the Linux libbz2.so.1.0 static-link caveat is recorded in #429.

---

## 6. 版本升级导致的破坏性变更 / 平台与规则能力变更

All quotes below are verbatim from the release bodies fetched from `/releases` and `/releases/tags/<tag>`.

- **2.0.0 (2026-02-06)** — https://github.com/Predidit/Kazumi/releases/tag/2.0.0
  `- 新增离线缓存功能 (#26 #316)` · `- 新增一起看模式下弹幕聊天功能 (#834)` · `- 新增定时关闭功能 (#832 #1586)` · `- 修复 Andorid8/9 应用白屏的问题 (#1676)` · `- 修复自动更新功能特定情况下无法打开文件夹的问题 (#1655)` · `- 修复 Android 键鼠操作时不正确的绿色应用边框` · `- 修复 Linux 平台视频解析器的线程安全问题`.
  **Reading:** a large-magnitude release. It fixes an **Android 8/9 white-screen** defect (so <2.0.0 was broken there) and a **Linux parser thread-safety** defect (the class of bug behind #1055/#1590). It also bundles the SyncPlay-based 一起看 chat that #834 had blocked on TLS.

- **2.1.2 (2026-05-21)** — https://github.com/Predidit/Kazumi/releases/tag/2.1.2
  `- 新增规则自定义 JS 验证支持` · `- 新增弹幕时间轴偏移设置，优化弹幕发射逻辑 (#369)` · `- 适配 flutter 3.44` · `- 修复 Bangumi 特殊标签解析导致搜索结果异常的问题` · `- 修复离线播放器初始化时机问题 (#2070)`.
  **Reading:** rules gained a **custom-JS validation** stage (a rule can now require executable verification), and a **danmaku timeline-offset** setting was added — both are backwards-compatibility pressure points for existing rules and for danmaku alignment.

- **2.1.3** — https://github.com/Predidit/Kazumi/releases/tag/2.1.3
  `- 新增Bangumi镜像支持` with the explicit note `Bangumi镜像为实验性功能，在网络连通性良好时保持禁用`.
  **Reading:** the mirror is declared **experimental** and is meant to stay disabled on healthy networks.

- **2.1.7 (2026-06-25)** — https://github.com/Predidit/Kazumi/releases/tag/2.1.7
  `- 现在 Bangumi 镜像功能与 Bangumi 同步功能可以兼容` · `- 现在缓存视频支持播放记录功能` · `- 新增可选的追番计数器功能` · `- 新增实验性 windows arm 支持`.
  **Reading:** "now the mirror and Bangumi sync **can be compatible**" confirms that before 2.1.7, **enabling the mirror broke Bangumi sync** — matching the #2133 note "启用镜像后无法同步属于预期行为". Windows ARM support is explicitly **experimental**.

- **2.3.4 (2026-09-22)** — https://github.com/Predidit/Kazumi/releases/tag/2.3.4
  `- 重新设计的竖屏模式我的页面` · `- 现在追番页面支持卡片样式与列表样式切换` · `- 改进的弹幕检索流程 (@moonrailgun)` · `- 修复 android 平台偶发的崩溃问题` · `- 修复 macOS x64 应用界面闪烁的问题 (#2577)`.
  **Reading:** another danmaku-retrieval rework, plus another round of "occasional Android crash" fixes.

- **2.3.8 (2026-10-07, latest fetched)** — https://github.com/Predidit/Kazumi/releases/tag/2.3.8
  Entirely UI/robustness fixes: screenshot interaction, unified dropdown styling, large-font overlap on the detail page (#2622), duplicated nested settings pages (#2607), player control-bar animation restart on hover, redundant subtitle in the episode-comment header, other UI improvements.

- **Version-introduced regressions documented in issues (not just release notes):**
  - **#1590** — Linux playback stopped working **starting 1.8.8** (1.8.7 was the last working version); still OPEN.
  - **#1489** — 1.8.8 introduced the `设置播放速度失败 LateInitializationError: Field 'danmakuController' has not been initialized` defect that leaves hardware decoding off and playback stuttering.
  - **#1276** — Honor-device crashes began **after updating to 1.7.8**.
  - **#2341** — multi-client sync regression in **2.2.0** (2.1.8 fine).
  - **#1652** — playback regression on Android 14 after an update (Vulkan/libplacebo SPIR-V mismatch).
  - **#1495 · [Bug]: flutter3.38导致字体错误 · closed · 26 comments** — https://github.com/Predidit/Kazumi/issues/1495 and **#1296 · [Bug]: Flatpak 版本的 UI 渲染使用了混合字体和框架 · closed · 13 comments** — https://github.com/Predidit/Kazumi/issues/1296: **Flutter SDK upgrades break fonts/UI rendering** (cf. 2.1.2 `适配 flutter 3.44`).
  - **#718 · [windows][Linux] 页面过渡动画在 flutter 3.29 损坏 · closed · 37 comments** — https://github.com/Predidit/Kazumi/issues/718: upgrading to Flutter 3.29 **silently removed all page-transition animations** on Windows/Linux, with nothing in the Flutter release notes — an upstream-SDK-driven breakage.
  - **#2612 / #2455 / #2450** show the *rule/source* side of version drift: Kazumi 2.x consumes **plugin/rule node lists** and silently drops nodes (`搜索节点 N 缺少名称或来源，已跳过`), so a stale or partially-broken rule degrades quietly instead of failing loudly.

- **Standing platform minimums/limits surfaced by the tracker:** Android 8/9 white screen (fixed only in 2.0.0), HarmonyOS NEXT unsupported (#666, #235), HDR/Dolby Vision impossible due to Flutter Texture (#995), F-Droid reproducible build failure (#2101), SideStore/IPA repackaging requirements (#819, #826).

---

## Recurring root causes (most common failure classes)

1. **Player / hardware-decoding & GPU stack (mpv + media_kit + libplacebo) is the single biggest source of hard failures.** Symptoms: black/purple screen, crash-on-play, stutter on weak hardware. Concrete mechanisms seen: `h264_mediacodec: Both surface and native_window are NULL` (#1276), libplacebo `Invalid SPIR-V binary version 1.6 … under Vulkan 1.0` → `VK_ERROR_INITIALIZATION_FAILED` (#1652), `glGetString(GL_VERSION) returned NULL` / `OpenGL not initialized` (#1282), `Unsupported hwdec: drm` / `vulkan-copy` → silent `Using software decoding` (#1282, #1489, #1348), and device-specific mpv incompatibility on certain Honor SoCs explicitly **not** caused by Flutter or memory exhaustion (#681, #1276).
2. **The parsing architecture is fundamentally limited.** Direct-URL extraction relies on JS injection into a webview; **CORS means the injected script cannot reach embedded iframes**, so referer validation and JS challenges inside iframes are stated by the maintainer to be essentially unsolvable, producing slow parsing and site-specific parse failures (#914, #1823). On Linux the parser additionally depends on `desktop_webview_window`, which can die with `failed to evaluate javascript` (#1590).
3. **Upstream source volatility and anti-hotlink/anti-bot measures.** Sources disappear or start returning `403`/`404`/`599`, time out at the fixed 12 s limit, or require human verification; rule node lists silently skip entries (`缺少名称或来源`), and a rule may only see one of several playback lines (#2450, #2455, #2612, #360, #1823, #1524). Some streams inject ads that loop and decode-error (#1524).
4. **Regional blocking of the metadata backend.** All four Bangumi endpoints are blocked in mainland China (#2133); without a proxy/TUN the app cannot load covers/names, and **mirror mode deliberately breaks sync** (#2133, 2.1.3/2.1.7 notes).
5. **WebDAV sync is the most defect-dense feature.** Documented defects: URI without host (`/kazumiSync/histories.tmp.cache`), Hive file rename/`File closed`/cross-drive rename failures, `Content size exceeds specified contentLength`, concurrent `History is currently syncing` exceptions that can be triggered on every fullscreen toggle, silently-successful-but-not-synced clients, wrong-episode restore, 追番-list sync always failing, and no user-defined directory / retry / timestamped naming (#861, #1280, #2341, #138, #1204, #61, #2233).
6. **Danmaku depends on a fragile external ID mapping and is fetched without null-safety.** The `BgmBangumiID` lookup returns null lists and throws `type 'Null' is not a subtype of type 'List<dynamic>'` / `NoSuchMethodError … []("episodes")`, leaving danmaku missing (#1819, #2044, #1839, #671); alignment needed a dedicated timeline-offset setting (#369 via 2.1.2); rendering is buggy and slow under flatpak/Wayland (#1518, #105); and initialize-order bugs (`danmakuController` not initialized) abort unrelated operations (#1489).
7. **Flutter/plugin SDK upgrades cause silent rendering regressions** — lost page transitions on Flutter 3.29 (#718), font/UI corruption on 3.38/flatpak (#1495, #1296), SPIR-V/Vulkan breakage (#1652): the project tracks Flutter tightly (2.1.2 `适配 flutter 3.44`) and each track bump risks UI regressions.
8. **Strong platform asymmetry.** Linux (especially flatpak: OpenGL init, hwdec, GPU saturation, webview parser) and mobile vendor ROMs (Honor crash family, VIVO freeze, Xiaomi HyperOS small-window/heating) diverge sharply from Windows, and HarmonyOS/ohos plus HDR/DV are explicitly out of scope (#739, #1282, #1348, #1590, #666, #235, #995, #1056, #1615, #1926).

---

*Source: GitHub REST API responses for `Predidit/Kazumi` (issues list sorted by comment count, keyword searches for 播放失败 / 规则 / 弹幕 / 超分 / webdav / 一起看 / 403 / 内存 / 地区 / apiLevel, individual issue lookups, and the releases endpoints). All issue numbers, titles and states are as returned by the API; no item was inferred or fabricated.*
