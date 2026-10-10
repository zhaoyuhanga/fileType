# Kazumi (`Predidit/Kazumi`, branch `main`) — "Extras" fact report

Method: the whole `main` archive was downloaded (`https://codeload.github.com/Predidit/Kazumi/zip/refs/heads/main`) and inspected locally; the GitHub contents API (`?ref=main`) was used for directory listings, and `raw.githubusercontent.com` for spot verification. Blob SHAs from `https://api.github.com/repos/Predidit/Kazumi/contents/lib/utils?ref=main` (`http_headers.dart` = `0aaae7971afc082340128b7fe462dfaeb73e26b3`, `constants.dart` = `b90cac746a3cc646d6256118be1a9314dc02b00f`, `m3u8_ad_filter.dart` = `93784ec80bee5929f1259cacf54b8755598abb0d`, `m3u8_parser.dart` = `89895824b4534ec7a98c6f0d4b16e44aaca5d849`) match the tree analyzed here, so this report reflects current `main`. Every claim is cited to `https://raw.githubusercontent.com/Predidit/Kazumi/main/<path>`. The repo has been heavily refactored relative to older Kazumi versions; paths/class names below are the current `main` ones.

### Verification notes on third-party path claims (all checked directly)
- `lib/utils/string_match.dart` — **does not exist on `main`**: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/utils/string_match.dart` returns **HTTP 404**, and the `ref=main` listing of `lib/utils` contains no such entry. There is therefore no string-normalization helper anywhere in the plugin-search dedup path (see §5.2).
- `lib/request/interceptor.dart` — does not exist; the only interceptor files are `lib/request/core/dio_logger_interceptor.dart` (plus the private `_RulesMirrorInterceptor` inside `lib/request/core/dio_factory.dart`).
- `lib/utils/syncplay.dart` — does not exist; SyncPlay lives in `lib/services/player/syncplay_client.dart` + `lib/services/player/syncplay_endpoint.dart` + `lib/pages/player/controller/player_syncplay_controller.dart` + `lib/pages/player/syncplay_sheet.dart`.
- `lib/pages/download/download_episode_page.dart` — does not exist; the equivalent is `lib/pages/download/download_episode_sheet.dart`.
- `lib/request/clients/plugin_site_client.dart`, `lib/utils/episode_url.dart`, `lib/utils/media.dart`, `lib/pages/settings/proxy/{proxy_editor_page,proxy_settings_page,proxy_module}.dart` — all present, as listed in the tree.

---

## 1. Proxy / anti-hotlink handling (Referer / User-Agent injection)

### 1.1 Header helpers

`lib/utils/http_headers.dart` is tiny (13 lines) and contains no interceptor logic — just two random pickers:

```dart
final _random = Random();

String getRandomUA() {
  return userAgentsList[_random.nextInt(userAgentsList.length)];
}

String getRandomAcceptedLanguage() {
  return acceptLanguageList[_random.nextInt(acceptLanguageList.length)];
}
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/utils/http_headers.dart

The lists live in `lib/utils/constants.dart`:

```dart
/// 随机UA列表
const List<String> userAgentsList = [
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
  ... Chrome/136, Chrome/134, Edg/141, Firefox/125.0, Safari/17.5, Safari/16.1, Edg/134, Edg/136 ...
];

/// 随机HTTP请求头accept-language字段列表
const List<String> acceptLanguageList = [
  'zh-CN,zh;q=0.9',
  'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
  'zh-CN,zh-TW;q=0.9,zh;q=0.8,en-US;q=0.7,en;q=0.6',
];

/// Bangumi API 文档要求的UA格式
Map<String, String> bangumiHTTPHeader = {
  'user-agent':
      'Predidit/Kazumi/${ApiEndpoints.version} (Android) (https://github.com/Predidit/Kazumi)',
  'referer': '',
  'content-type': 'application/json'
};
```
(9 UAs total, lines 68–78; `acceptLanguageList` at lines 81–85; `bangumiHTTPHeader` at lines 88–93.)
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/utils/constants.dart

### 1.2 Where the per-rule Referer/UA come from

`lib/plugins/plugins.dart` — `Plugin` carries `bool adBlocker;` (line 35), `String userAgent;` (line 36), `String referer;` (line 44), parsed from rule JSON (`userAgent: json['userAgent'] as String? ?? ''`, `referer: json['referer'] ?? ''`, `adBlocker: json['adBlocker'] ?? false`), and exposes:

```dart
  /// Headers used when resolving or downloading the final media resource.
  Map<String, String> buildHttpHeaders() {
    return {
      'user-agent': userAgent.isEmpty ? getRandomUA() : userAgent,
      if (referer.isNotEmpty) 'referer': referer,
    };
  }
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/plugins/plugins.dart

The rule editor labels it explicitly: `static const referer = '播放请求来源（Referer）';` / `static const refererHelper = '仅用于播放器和下载器。';`
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/plugin_editor/plugin_editor_page.dart

### 1.3 Video requests: mpv `http-header-fields` (NOT a local proxy, NOT a dio interceptor)

`lib/pages/video/video_controller.dart` builds the header map passed to the player (offline playback passes `httpHeaders: {}`, `referer: ''`, `adBlockerEnabled: false`):

```dart
        httpHeaders: {
          'user-agent': currentPlugin.userAgent.isEmpty
              ? getRandomUA()
              : currentPlugin.userAgent,
          if (currentPlugin.referer.isNotEmpty)
            'referer': currentPlugin.referer,
        },
        adBlockerEnabled: forceAdBlocker || currentPlugin.adBlocker,
        episodeTitle: resolvedEpisode.displayTitle,
        referer: currentPlugin.referer,
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/video/video_controller.dart

`lib/pages/player/controller/player_playback_controller.dart` hands them to media_kit's `Media`:

```dart
      await player.open(
        Media(videoUrl(),
            start: Duration(seconds: offset), httpHeaders: httpHeaders),
        play: autoPlay,
      );
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/player/controller/player_playback_controller.dart

`pubspec.yaml` pins a **fork** of media_kit as a git path dependency:
```yaml
  media_kit:
    git:
      url: https://github.com/Predidit/media-kit.git
      ref: 803c4a27912091db6f839d48d1b984a0d9f588c7
      path: ./media_kit
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/pubspec.yaml

In that pinned fork (`media_kit/lib/src/player/native/player/real.dart`) the mpv `on_load` hook converts `Media.httpHeaders` into the mpv property **`http-header-fields`**, and clears it on `on_unload`:

```dart
        // Handle HTTP headers specified in the [Media].
        try {
          final uri = readNativeString(mpv, ctx, 'path');
          final headers = uri == null ? null : Media(uri).httpHeaders;
          if (headers != null) {
            final property = 'http-header-fields'.toNativeUtf8();
            ...
              final data = '$k: $v'.toNativeUtf8();
```
Source: https://raw.githubusercontent.com/Predidit/media-kit/803c4a27912091db6f839d48d1b984a0d9f588c7/media_kit/lib/src/player/native/player/real.dart (≈ lines 1947–1975; teardown at ≈ line 2041: `// Set http-header-fields as [generated.mpv_format.MPV_FORMAT_NONE] [generated.mpv_node].`)

**Conclusion: Referer/User-Agent for video are injected as mpv `http-header-fields` entries of the form `"key: value"`. There is no local HTTP proxy server and no dio interceptor in the video path.**

Negative evidence for a local proxy: a recursive search over every `*.dart` file in `lib/` for `HttpServer` and `.bind(` returns **zero** matches (unrelated hits only: `hintText: 'http://127.0.0.1:7890'` in `lib/pages/settings/proxy/proxy_editor_page.dart`, and a `localhost`/`::1` check in `lib/services/network/system_proxy_service.dart`).

### 1.4 Proxy support (real network proxy, not header injection)

`lib/request/core/network_config.dart`:
```dart
  IOHttpClientAdapter createAdapter() {
    return IOHttpClientAdapter(
      createHttpClient: () {
        final client = HttpClient();
        if (hasProxy) {
          client.findProxy = (_) => 'PROXY $proxyHost:$proxyPort';
        } else if (Platform.isWindows) {
          client.findProxy = SystemProxyService.findProxy;
        }
        if (allowBadCertificates) {
          client.badCertificateCallback = (cert, host, port) => true;
        }
        return client;
      },
    );
  }
```
Defaults `connectTimeout = const Duration(seconds: 12)`, `receiveTimeout = const Duration(seconds: 12)`; `NetworkConfig.fromSettings()` reads `SettingsKeys.proxyEnable` / `SettingsKeys.proxyUrl` via `ProxyUtils.parseProxyUrl` and forces `allowBadCertificates: true` when a proxy is set; `bool get hasProxy => proxyHost != null && proxyPort != null;`.
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/request/core/network_config.dart

For **mpv playback** the proxy becomes an mpv property:
```dart
          await pp.setProperty("http-proxy", formattedProxy);
...
        await pp.setProperty("http-proxy", 'http://${proxy.$1}:${proxy.$2}');
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/player/controller/player_playback_controller.dart

### 1.5 dio default headers / interceptor code

`lib/request/core/dio_factory.dart` — five lazily-created `Dio` instances:
```dart
  static Dio get apiDio => _apiDio ??= _create(
    NetworkConfig.fromSettings(),
    defaultHeaders: {'referer': '', 'user-agent': getRandomUA()},
  );
  static Dio get bangumiDio { ... defaultHeaders: {'referer': ''},
      interceptors: [BangumiAccelerationInterceptor()], ... }
  static Dio get rulesRepoDio => _rulesRepoDio ??= _create(
    NetworkConfig.fromSettings(),
    defaultHeaders: {'user-agent': getRandomUA()},
    interceptors: [_RulesMirrorInterceptor()],
  );
  static Dio get pluginDio => _pluginDio ??= _create(
    NetworkConfig.fromSettings(),
    defaultHeaders: {
      'user-agent': getRandomUA(),
      'accept-language': getRandomAcceptedLanguage(),
    },
  );
  static Dio get downloadDio => _downloadDio ??= _create(
    NetworkConfig.fromSettings(
      connectTimeout: const Duration(seconds: 15),
      receiveTimeout: const Duration(seconds: 30),
    ),
    defaultHeaders: {'user-agent': getRandomUA()},
  );
```
`_create` also sets `validateStatus: (status) => status != null && status >= 200 && status < 300` and appends `DioLoggerInterceptor` when `config.enableLog`.

The only custom interceptor with `onRequest` is `_RulesMirrorInterceptor` (rule-repo mirror when `SettingsKeys.enableGitProxy` is on):
```dart
    final mirrored =
        ApiEndpoints.pluginShopMirror +
        url.substring(ApiEndpoints.pluginShop.length);
    KazumiLogger().d('Rules mirror: $mirrored');
    options.path = mirrored;
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/request/core/dio_factory.dart

`lib/modules/plugin/plugin_http_module.dart` is unrelated to header injection — a 32-line metadata model `PluginHTTPItem {name, version, useNativePlayer, author, lastUpdate, antiCrawlerEnabled}`, where `antiCrawlerEnabled` comes from `json['antiCrawlerConfig']['enabled']` (falling back to `json['antiCrawlerEnabled']`).
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/modules/plugin/plugin_http_module.dart

### 1.6 `lib/webview/*` (directory + header behaviour)

`lib/webview` contains exactly two subdirectories: `captcha/` and `video/`.
Source: https://api.github.com/repos/Predidit/Kazumi/contents/lib/webview?ref=main

Files: `lib/webview/captcha/captcha_webview_controller.dart`, `lib/webview/captcha/impl/captcha_webview_inappwebview_impl.dart`, `..._linux_impl.dart`, `..._windows_impl.dart`; `lib/webview/video/video_webview_controller.dart`, `lib/webview/video/impl/video_webview_impl.dart`, `..._android_impl.dart`, `..._apple_impl.dart`, `..._linux_impl.dart`, `..._windows_impl.dart`.

The video WebView injects a **random UA into the WebView itself** (not into media requests):
```dart
        initialSettings: InAppWebViewSettings(
          userAgent: getRandomUA(),
          mediaPlaybackRequiresUserGesture: true,
          upgradeKnownHostsToHTTPS: false,
          mixedContentMode: MixedContentMode.MIXED_CONTENT_COMPATIBILITY_MODE,
        ),
```
Same pattern in `video_webview_android_impl.dart` (`userAgent: getRandomUA(),`), `video_webview_apple_impl.dart`, and `captcha_webview_inappwebview_impl.dart` — all import `package:kazumi/utils/http_headers.dart`.
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/webview/video/impl/video_webview_impl.dart

URL sniffing + ad-URL blocking in the request interceptor:
```dart
        shouldInterceptRequest: (controller, request) async {
          if (useLegacyParser || isVideoSourceLoaded) return null;
          final url = request.url.toString();
          final lower = url.toLowerCase();
          if (_isAdUrl(lower)) return null;
          if (_isM3U8Url(lower) ||
              _isRangeVideoRequest(lower, request.headers)) {
            logEventController.add('Native intercepted video URL: $url');
            ...
            notifyVideoSourceResolved(url);
          }
          return null;
        },
```
```dart
  bool _isM3U8Url(String lower) { ... return uri.path.endsWith('.m3u8'); }

  bool _isRangeVideoRequest(String lower, Map<String, String>? headers) {
    if (headers == null) return false;
    final range = headers['Range'] ?? headers['range'];
    if (range == null || !range.startsWith('bytes=')) return false;
    if (lower.endsWith('.js') || lower.endsWith('.css') || lower.endsWith('.html') ||
        lower.endsWith('.json') || lower.endsWith('.png') || lower.endsWith('.jpg') ||
        lower.endsWith('.gif') || lower.endsWith('.svg') || lower.endsWith('.woff') ||
        lower.endsWith('.woff2') || lower.endsWith('.wasm')) { return false; }
    return true;
  }

  bool _isAdUrl(String lower) {
    return lower.contains('googleads') ||
        lower.contains('googlesyndication') ||
        lower.contains('adtrafficquality') ||
        lower.contains('doubleclick');
  }
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/webview/video/impl/video_webview_impl.dart

`lib/webview/video/video_webview_controller.dart` defines `abstract class VideoWebviewController<T>` with `init()`, `loadUrl(String url, bool useLegacyParser, {int offset = 0})`, `unloadPage()`, `dispose()`, plus `typedef VideoParserEvent = ({String url, int offset, VideoSourceFormat format});`, `onLog`, `onVideoLoading`, `onVideoURLParser`, and `class VideoWebviewControllerFactory { static VideoWebviewController getController() {...} }` (Windows / Linux / macOS+iOS / Android-with-document-start-script / fallback).
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/webview/video/video_webview_controller.dart

`lib/services/video_source/webview_video_source_service.dart` — `class WebViewVideoSourceService implements IVideoSourceService`, `resolve(episodeUrl, {required bool useLegacyParser, int offset = 0, Duration timeout = const Duration(seconds: 15)})`, serializing resolves per WebView instance via `_resolveTail`, throwing `VideoSourceTimeoutException` / `VideoSourceCancelledException`.
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/video_source/webview_video_source_service.dart

---

## 2. HLS / m3u8 ad filtering and parsing

### 2.1 `M3u8AdFilter` — exact algorithm

`lib/utils/m3u8_ad_filter.dart` (86 lines), `class M3u8AdFilter`:

```dart
class M3u8AdFilter {
  /// Filter ad segments from a media playlist.
  /// Mimics FFmpeg hls_ad_filter behavior using discontinuity groups.
  static List<M3u8Segment> filterAds(List<M3u8Segment> segments) {
    if (segments.isEmpty) return segments;

    // Group segments by discontinuityGroup
    final groups = <int, List<M3u8Segment>>{};
    for (final seg in segments) {
      groups.putIfAbsent(seg.discontinuityGroup, () => []);
      groups[seg.discontinuityGroup]!.add(seg);
    }

    // Only one group means no ads detected
    if (groups.length <= 1) return segments;

    // Calculate total duration per group
    ...
    // Find the longest group as the "main content" reference
    ...
      // Skip the main content group
      if (groupDuration == maxDuration) continue;
      bool isAd = false;
      // Short segments relative to main content (< 30%)
      if (groupDuration < maxDuration * 0.3) { isAd = true; }
      // First or last group with short duration (< 30s)
      if ((groupId == sortedKeys.first || groupId == sortedKeys.last) &&
          groupDuration < 30.0) { isAd = true; }
      // Very short segments (< 10s) are almost certainly ads
      if (groupDuration < 10.0) { isAd = true; }
      if (isAd) { adGroups.add(groupId); }
    ...
    return segments
        .where((seg) => !adGroups.contains(seg.discontinuityGroup))
        .toList();
  }

  /// Calculate the new target duration after filtering
  static double calculateTargetDuration(List<M3u8Segment> segments) {
    if (segments.isEmpty) return 0;
    double maxSegDuration = 0;
    for (final seg in segments) { if (seg.duration > maxSegDuration) maxSegDuration = seg.duration; }
    return maxSegDuration;
  }
}
```
Exact heuristics: **more than one discontinuity group is required; the longest group is treated as main content; any other group is an ad if `groupDuration < maxDuration * 0.3`, OR (`first`/`last` group by sorted group id AND `groupDuration < 30.0`), OR `groupDuration < 10.0`.** `calculateTargetDuration` returns the maximum remaining segment duration.
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/utils/m3u8_ad_filter.dart

Negative findings for this file (each searched): **no `#EXT-X-CUE-OUT`/`#EXT-X-CUE-IN` handling, no keyword list, no segment-URL or segment-naming patterns, no per-segment length threshold, no domain blacklist.** `未查到` for all of those.

### 2.2 `M3u8Parser` — data model and parsing

`lib/utils/m3u8_parser.dart` (335 lines). Classes: `M3u8Key {method, uri, iv}` (with `==`/`hashCode`, and `toString()` → `'#EXT-X-KEY:METHOD=$method,URI="$uri"'` plus `,IV=$iv`), `M3u8Segment {duration, uri, discontinuityGroup, key}`, `M3u8Variant {bandwidth, resolution, uri}`, `M3u8MasterPlaylist {variants}` with:
```dart
  M3u8Variant get bestVariant {
    return variants.reduce((a, b) => a.bandwidth > b.bandwidth ? a : b);
  }
```
`M3u8MediaPlaylist {segments, targetDuration, isVod}`, `enum M3u8Type { master, media }`.

Type detection:
```dart
  static M3u8Type detectType(String content) {
    if (content.contains('#EXT-X-STREAM-INF')) { return M3u8Type.master; }
    return M3u8Type.media;
  }
```

URL resolution (absolute / root-relative / path-relative):
```dart
  static String resolveUrl(String baseUrl, String relativeUrl) {
    if (relativeUrl.startsWith('http://') || relativeUrl.startsWith('https://')) return relativeUrl;
    final baseUri = Uri.parse(baseUrl);
    if (relativeUrl.startsWith('/')) {
      return '${baseUri.scheme}://${baseUri.host}${baseUri.hasPort ? ':${baseUri.port}' : ''}$relativeUrl';
    }
    final basePath = baseUrl.substring(0, baseUrl.lastIndexOf('/') + 1);
    return '$basePath$relativeUrl';
  }
```

Master playlist parsing — `#EXT-X-STREAM-INF:` attributes via `RegExp(r'BANDWIDTH=(\d+)')` and `RegExp(r'RESOLUTION=([^\s,]+)')`; the URI is the next line when it does not start with `#`.

Media playlist parsing — supported tags exactly:
```dart
      if (line.startsWith('#EXT-X-TARGETDURATION:')) { targetDuration = double.parse(line.substring('#EXT-X-TARGETDURATION:'.length)); }
      else if (line == '#EXT-X-ENDLIST') { hasEndList = true; }
      else if (line == '#EXT-X-PLAYLIST-TYPE:VOD') { isExplicitVod = true; }
      else if (line == '#EXT-X-PLAYLIST-TYPE:EVENT') { isLiveEvent = true; }
      else if (line == '#EXT-X-DISCONTINUITY') { currentDiscontinuityGroup++; }
      else if (line.startsWith('#EXT-X-KEY:')) { currentKey = _parseKey(line, baseUrl); }
      else if (line.startsWith('#EXTINF:')) { currentDuration = double.parse(line.substring('#EXTINF:'.length).split(',')[0]); }
      else if (line.isNotEmpty && !line.startsWith('#')) { /* append M3u8Segment, then currentDuration = 0 */ }
```
VOD decision (verbatim):
```dart
    // Consider it VOD if:
    // 1. Has #EXT-X-ENDLIST, or
    // 2. Has #EXT-X-PLAYLIST-TYPE:VOD, or
    // 3. Has finite segments and is not explicitly a live EVENT stream.
    // Many third-party video sources omit #EXT-X-ENDLIST for VOD content.
    final bool isVod =
        hasEndList || isExplicitVod || (!isLiveEvent && segments.isNotEmpty);
```
Key parsing: `RegExp(r'METHOD=([^,]+)')` (returns `null` when `METHOD=NONE`), `RegExp(r'URI="([^"]+)"')`, `RegExp(r'IV=(0x[0-9a-fA-F]+)')`.

Other methods: `extractUniqueKeys(M3u8MediaPlaylist)` (dedup by key `uri`), `resolveNestedSegments(List<M3u8Segment> segments, Future<String> Function(String url) fetcher, {int maxDepth = 3})` (expands segments whose URI path ends with `.m3u8`, offsetting `discontinuityGroup`, falling back to the original segment on fetch/parse error), `_isM3u8Url(String url)` → `Uri.parse(url).path.toLowerCase().endsWith('.m3u8')`, and `buildLocalM3u8(...)` emitting verbatim:
```dart
    sb.writeln('#EXTM3U');
    sb.writeln('#EXT-X-VERSION:3');
    sb.writeln('#EXT-X-TARGETDURATION:${targetDuration.ceil()}');
    sb.writeln('#EXT-X-MEDIA-SEQUENCE:0');
    ...  sb.writeln('#EXT-X-DISCONTINUITY');
    ...  sb.writeln('#EXT-X-KEY:METHOD=NONE');   // or
         '#EXT-X-KEY:METHOD=${seg.key!.method},URI="$localUri"' (+ ',IV=${seg.key!.iv}')
      sb.writeln('#EXTINF:${seg.duration.toStringAsFixed(6)},');
      sb.writeln('seg_${i.toString().padLeft(5, '0')}.ts');
    sb.writeln('#EXT-X-ENDLIST');
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/utils/m3u8_parser.dart

### 2.3 Is a local HTTP server used to serve the filtered playlist? — NO

- A recursive search for `HttpServer` and `.bind(` across all `lib/**/*.dart` returns **no matches**.
- For **downloads**, the filtered playlist is written as a file (`playlist.m3u8`) next to the segments:
```dart
      List<M3u8Segment> segments = resolvedPlaylist.segments;
      if (adBlockerEnabled) {
        segments = M3u8AdFilter.filterAds(segments);
      }
      ...
      final targetDuration = adBlockerEnabled
          ? M3u8AdFilter.calculateTargetDuration(segments)
          : resolvedPlaylist.targetDuration;
      final localM3u8 = M3u8Parser.buildLocalM3u8(
        segments,
        targetDuration: targetDuration,
        keyUriToLocal: keyUriToLocal,
      );
      final m3u8Path = path.join(episodeDir, 'playlist.m3u8');
      await File(m3u8Path).writeAsString(localM3u8);
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/download/download_manager.dart

- For **online playback**, filtering is delegated to mpv through the pinned media_kit fork, which maps `adBlocker` onto FFmpeg's HLS option:
```dart
          'demuxer-lavf-o': [
            'seg_max_retry=5',
            'strict=experimental',
            'allowed_extensions=ALL',
            'hls_ad_filter=${configuration.adBlocker ? 1 : 0}',
            'protocol_whitelist=[${configuration.protocolWhitelist.join(',')}]',
          ].join(','),
```
Source: https://raw.githubusercontent.com/Predidit/media-kit/803c4a27912091db6f839d48d1b984a0d9f588c7/media_kit/lib/src/player/native/player/real.dart (line ≈2169)

Kazumi sets that flag via `PlayerConfiguration`:
```dart
      Player(
        configuration: PlayerConfiguration(
          bufferSize: cachePolicy.bufferSize,
          osc: false,
          logLevel: MPVLogLevel.values[debug.playerLogLevel],
          adBlocker: adBlockerEnabled,
        ),
      ),
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/player/controller/player_playback_controller.dart

`adBlockerEnabled` = `forceAdBlocker || currentPlugin.adBlocker` (player) and `_repository.getForceAdBlocker() || plugin.adBlocker` (download). `SettingsKeys.forceAdBlocker` default `false`; plugin editor labels `static const adBlocker = '广告过滤';` / `static const adBlockerDesc = '启用 HLS 广告过滤';`.
Sources: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/video/video_controller.dart , https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/download/download_controller.dart , https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/storage/settings_keys.dart , https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/plugin_editor/plugin_editor_page.dart

`M3u8AdFilter` is referenced **only** from `lib/services/download/download_manager.dart` (lines 473 and 601) — confirmed by an exhaustive grep for `M3u8AdFilter|filterAds|adBlocker|ad-blocker` across `lib/`.

---

## 3. Video download

### 3.1 Engine

`lib/services/download/download_manager.dart` (985 lines) — `class DownloadManager implements IDownloadManager`, using dio through `DownloadHttpClient` (`lib/request/clients/download_http_client.dart`: singleton `DownloadHttpClient.instance` with `getStream` / `getPlain` / `download`, all on `DioFactory.downloadDio`).

Concurrency and queueing:
```dart
  final Map<String, DownloadTask> _activeTasks = {};
  final List<DownloadRequest> _queue = [];
  int maxParallelEpisodes = 2;
  int maxParallelSegments = 3;
```
Both come from settings (`SettingsKeys.downloadParallelEpisodes`, `SettingsKeys.downloadParallelSegments`). Segment concurrency uses a hand-rolled semaphore:
```dart
        final semaphore = _Semaphore(maxParallelSegments);
        ...
          await semaphore.acquire();
          ...
          _downloadSegmentWithRetry(
            segments[idx].uri,
            path.join(episodeDir, 'seg_${idx.toString().padLeft(5, '0')}.ts'),
            httpHeaders,
            task.cancelToken,
          ).then((bytes) { ... });
```
So the engine is a **concurrent segment downloader with a direct-file fallback**, not a single dio `download()` of an mp4.

`_downloadSegmentWithRetry` — 3 retries, back-off `[1, 3, 9]` seconds, `.tmp` then rename:
```dart
  Future<int> _downloadSegmentWithRetry(String url, String savePath, Map<String,String> headers, CancelToken cancelToken, {int maxRetries = 3}) async {
    final tmpPath = '$savePath.tmp';
    ...
        await _http.download(url, tmpPath, headers: headers, cancelToken: cancelToken);
        await File(tmpPath).rename(savePath);
        return await File(savePath).length();
    ...
        final delay = Duration(seconds: [1, 3, 9][retryCount - 1]);
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/download/download_manager.dart

### 3.2 M3U8 flow and the non-M3U8 fallback

`_runEpisodeDownload` → `_fetchM3u8` → `M3u8Parser.detectType` → (`bestVariant` for master) → `parseMediaPlaylist` → `resolveNestedSegments` → VOD check → optional `M3u8AdFilter.filterAds` → download `key_$i.key` → download segments → `buildLocalM3u8` → `playlist.m3u8`.

Playlist fetch guard (15 s receive timeout, aborts above 2 MB, must be a real playlist):
```dart
      final trimmed = content.trimLeft();
      if (!trimmed.startsWith('#EXTM3U')) {
        throw _NotM3u8Exception('URL 不是 M3U8 播放列表');
      }
```
```dart
          if (received > 2 * 1024 * 1024) { fetchToken.cancel('too large'); }
```
Fallback:
```dart
      } on _NotM3u8Exception {
        KazumiLogger().i('DownloadManager: URL is not M3U8, falling back to direct file download ...');
        await _runDirectFileDownload(...);
```
`_runDirectFileDownload` streams to `video.mp4` via `video.mp4.tmp`, supports Range resume, and recovers from 416:
```dart
        if (e.statusCode == 416 && useRange) {
          KazumiLogger().w('DownloadManager: 416 Range Not Satisfiable, deleting tmp file and retrying');
          await tmpFile.delete(); existingBytes = 0; requestHeaders.remove('Range');
```
Live streams are rejected: `episode.errorMessage = '不支持下载直播流 (无有效分片)';` when `!resolvedPlaylist.isVod`; empty playlist → `'M3U8 中未找到可下载的分片'`.

### 3.3 Resume support — yes

- Segment level: existing non-empty `seg_XXXXX.ts` files are detected and skipped:
```dart
      final existingSegments = <int>{};
      var existingBytes = 0;
      for (int i = 0; i < segments.length; i++) {
        final segFile = File(path.join(episodeDir, 'seg_${i.toString().padLeft(5, '0')}.ts'));
        if (await segFile.exists()) {
          final segmentBytes = await segFile.length();
          if (segmentBytes <= 0) continue;
          existingSegments.add(i); episode.downloadedSegments++; existingBytes += segmentBytes;
        }
      }
      final pendingIndices = <int>[];
      for (int i = 0; i < segments.length; i++) { if (!existingSegments.contains(i)) pendingIndices.add(i); }
```
Stale `.tmp` files are deleted up-front. Pause sets `task.isPaused = true` then `task.cancelToken.cancel('paused')` → `DownloadStatus.paused`; resume goes through `resume(DownloadRequest)` / `retryDownload(...)`; `DownloadRequest` re-resolves the stream via `startDownload` when `networkM3u8Url` is empty.
- Direct-file level: `Range: bytes=$existingBytes-` against `video.mp4.tmp`.

### 3.4 Output naming / extension / locations

```dart
  String _getEpisodeDir(String downloadBase, int bangumiId, String pluginName, int episodeNumber) {
    return path.join(downloadBase, '${bangumiId}_$pluginName', '$episodeNumber');
  }
```
- HLS episode: `<downloadBase>/<bangumiId>_<pluginName>/<episodeNumber>/seg_00000.ts … seg_NNNNN.ts`, `key_0.key …`, generated `playlist.m3u8` recorded in `episode.localM3u8Path`.
- Direct file: same directory, `video.mp4` (also recorded in `localM3u8Path`).
- Cached danmaku (when `SettingsKeys.downloadDanmaku`, default `true`): `danmaku.json` (`_danmakuFilePath` → `'$downloadDirectory/danmaku.json'`).
- Size accounting counts only `RegExp(r'^seg_\d+\.ts$')` files: `Future<int> calculateM3u8VideoBytes(String episodeDir)`.

Default root:
```dart
Future<String> getDefaultDownloadDirectory() async {
  final appSupport = await getApplicationSupportDirectory();
  return path.join(appSupport.path, 'downloads');
}
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/utils/file_system.dart

Storage guard:
```dart
  static const _minRequiredSpace = 100 * 1024 * 1024; // 100MB minimum
  static const _storageChannel = MethodChannel('com.predidit.kazumi/storage');
  ... invokeMethod<int>('getAvailableStorage', {'path': path})
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/download/download_manager.dart

### 3.5 DB model / module fields

Hive (`hive_ce`) box `GStorage.downloads`, from `lib/modules/download/download_module.dart`:
```dart
@HiveType(typeId: 7)
class DownloadRecord {
  @HiveField(0) int bangumiId;
  @HiveField(1) String bangumiName;
  @HiveField(2) String bangumiCover;
  @HiveField(3) String pluginName;
  @HiveField(4) Map<int, DownloadEpisode> episodes;
  @HiveField(5) DateTime createdAt;
  String get key => '${pluginName}_$bangumiId';
}

@HiveType(typeId: 8)
class DownloadEpisode {
  @HiveField(0) int episodeNumber;
  @HiveField(1) String episodeName;
  @HiveField(2) int road;
  /// 0=pending 1=resolving 2=downloading 3=completed 4=failed 5=paused
  @HiveField(3) int status;
  @HiveField(4) double progressPercent;
  @HiveField(5) int totalSegments;
  @HiveField(6) int downloadedSegments;
  @HiveField(7) String localM3u8Path;
  @HiveField(8) String downloadDirectory;
  @HiveField(9) String networkM3u8Url;
  @HiveField(10) DateTime? completedAt;
  @HiveField(11, defaultValue: '') String errorMessage;
  @HiveField(12, defaultValue: 0) int totalBytes;
  @HiveField(13, defaultValue: '') String episodePageUrl;
  /// 缓存的弹幕数据 (JSON 字符串格式)
  @HiveField(14, defaultValue: '') String danmakuData;
  /// DanDanPlay 番剧 ID (用于弹幕查询缓存)
  @HiveField(15, defaultValue: 0) int danDanBangumiID;
}

class DownloadStatus {
  static const int pending = 0; static const int resolving = 1; static const int downloading = 2;
  static const int completed = 3; static const int failed = 4; static const int paused = 5;
}
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/modules/download/download_module.dart

Repository: `lib/repositories/download_repository.dart` — `abstract class IDownloadRepository` (`changes`, `getAllRecords`, `getRecord`, `putRecord`, `deleteRecord`, `updateEpisode`, `deleteEpisode`, `getForceAdBlocker`, `getRecordByBangumiId`, `getEpisode`, `getCompletedEpisodes`, `getEpisodeByUrl`); `class DownloadRepository implements IDownloadRepository` uses `final _downloadsBox = GStorage.downloads;`, keeps progress in memory, and persists only on status change:
```dart
      // Only persist to Hive when status changes (not on every progress update)
      // This dramatically reduces disk I/O and prevents corruption on crash
      final statusKey = '${recordKey}_$episodeNumber';
      final lastStatus = _lastPersistedStatus[statusKey];
      final shouldPersist = lastStatus != episode.status;
```
`getForceAdBlocker()` → `GStorage.getSetting(SettingsKeys.forceAdBlocker)`; `Stream<void> get changes => _downloadsBox.watch();`
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/repositories/download_repository.dart

`lib/repositories` listing: `collect_crud_repository.dart`, `collect_repository.dart`, `danmaku_shield_repository.dart`, `download_repository.dart`, `history_repository.dart`, `search_history_repository.dart`.
Source: https://api.github.com/repos/Predidit/Kazumi/contents/lib/repositories?ref=main

Download settings defaults (`lib/services/storage/settings_keys.dart`):
```dart
  static const downloadParallelEpisodes = SettingKey<int>(_SettingBoxKey.downloadParallelEpisodes, 2, group: SettingGroup.download);
  static const downloadParallelSegments = SettingKey<int>(_SettingBoxKey.downloadParallelSegments, 3, group: SettingGroup.download);
  static const downloadDanmaku = SettingKey<bool>(_SettingBoxKey.downloadDanmaku, true, group: SettingGroup.download);
  static const downloadDirectory = SettingKey<String>(_SettingBoxKey.downloadDirectory, '', group: SettingGroup.download);
  // macOS only: security-scoped bookmark that keeps downloadDirectory writable across app restarts under the sandbox.
  static const downloadDirectoryBookmark = SettingKey<String>('downloadDirectoryBookmark', '', group: SettingGroup.download);
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/storage/settings_keys.dart

### 3.6 Platform-specific save location

`lib/services/download/download_directory_service.dart` — `class DownloadDirectoryService`:
- `bool get supportsCustomDirectory => Platform.isAndroid || Platform.isMacOS || Platform.isWindows;`
- Android: `static const _androidChannel = MethodChannel('com.predidit.kazumi/download_directory');` → `requestStorageAccess` then `pickDirectory` (`{'initialDirectory': initialDirectory}`); errors map to `'未授予存储访问权限，下载位置未修改'`, `'请选择内部存储或 SD 卡中的本地文件夹'` (code `UNSUPPORTED_DIRECTORY`), `'选择下载位置失败，请重试'`.
- Non-Android: `FilePicker.platform.getDirectoryPath(dialogTitle: '选择下载位置', initialDirectory: initialDirectory)`.
- macOS keeps a security-scoped bookmark (`macos_secure_bookmarks`) in `SettingsKeys.downloadDirectoryBookmark`, restored by `restoreAccess()`; if lost, `getDownloadDirectory()` throws `DownloadDirectoryException('下载目录访问权限已失效，请在下载设置中重新选择该目录')`.
- Comment: `// A document tree grant alone does not allow direct file paths.`
Sources: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/download/download_directory_service.dart , https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/utils/file_system.dart (`ensureDirectoryWritable` writes/deletes `.kazumi_write_test_<micros>.tmp`)

Android native — `android/app/src/main/kotlin/com/example/kazumi/DownloadDirectoryHandler.kt`:
```kotlin
/**
 * Grants storage access and picks a local directory when the user changes the
 * download location. Downloads then use the plain filesystem path.
 */
class DownloadDirectoryHandler(private val activity: Activity) : MethodChannel.MethodCallHandler {
    companion object {
        const val CHANNEL = "com.predidit.kazumi/download_directory"
        private const val ACCESS_REQUEST = 53010
        private const val DIRECTORY_REQUEST = 53011
        private const val EXTERNAL_DOCUMENTS = "com.android.externalstorage.documents"
    }
    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "requestStorageAccess" -> requestStorageAccess(result)
            "pickDirectory" -> pickDirectory(call.argument<String>("initialDirectory"), result)
            else -> result.notImplemented()
        }
    }
```
Access check uses `Environment.isExternalStorageManager()` on API ≥ R, else `WRITE_EXTERNAL_STORAGE`; permission intent `Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION` (fallback `ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION`); picker is `Intent.ACTION_OPEN_DOCUMENT_TREE` + `putExtra(Intent.EXTRA_LOCAL_ONLY, true)`; `resolveDirectory(uri)` accepts only `scheme == "content"`, `authority == "com.android.externalstorage.documents"`, `DocumentsContract.isTreeUri`, resolving the volume id against `storageRoots()` (`primary` + `StorageManager.storageVolumes`), else `UNSUPPORTED_DIRECTORY` ("Only directories on local storage volumes are supported"). Result returned as `directory.canonicalPath`.
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/android/app/src/main/kotlin/com/example/kazumi/DownloadDirectoryHandler.kt

`open_filex`: an exhaustive grep for `OpenFilex` across `lib/**/*.dart` yields exactly **one** hit — `lib/services/update/auto_updater.dart:672: final result = await OpenFilex.open(filePath);` (opening the downloaded **update package**). **It is not used for downloaded videos.** Downloaded episodes are played in-app: `download_page.dart::_playEpisode` resolves the local path and pushes `/video/` with `OfflineVideoPlaybackArgs(bangumiItem:, pluginName:, episodeNumber:, road:, downloadedEpisodes:)`.
Sources: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/update/auto_updater.dart , https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/download/download_page.dart

`未查到` any `open_filex` / "open with external app" path for downloaded video files.

### 3.7 Android background service

`lib/services/download/background_download_service.dart` — `BackgroundDownloadService` singleton on `flutter_foreground_task`:
```dart
/// 使用 Foreground Service 保持 app 进程存活，防止系统在后台时杀死下载进程。
/// 下载逻辑仍在主 Isolate 运行，此服务仅负责：
/// 1. 显示通知栏进度
/// 2. 保持进程存活
/// 3. 提供通知栏交互（暂停全部）
...
      androidNotificationOptions: AndroidNotificationOptions(
        channelId: 'kazumi_download_channel',
        channelName: '下载服务',
        channelDescription: '视频下载后台服务',
        channelImportance: NotificationChannelImportance.LOW,
        priority: NotificationPriority.LOW,
        onlyAlertOnce: true,
      ),
      ...
        allowWakeLock: true,
        allowWifiLock: true,
```
`bool get isSupported => Platform.isAndroid;`
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/download/background_download_service.dart

### 3.8 Do downloads bypass anti-hotlink? — No

`lib/pages/download/download_controller.dart`:
```dart
      final httpHeaders = plugin.buildHttpHeaders();
      bool adBlockerEnabled =
          _repository.getForceAdBlocker() || plugin.adBlocker;

      await _downloadManager.enqueue(DownloadRequest(
        recordKey: request.recordKey,
        bangumiId: request.bangumiId,
        pluginName: request.pluginName,
        episodeNumber: request.episodeNumber,
        m3u8Url: m3u8Url,
        httpHeaders: httpHeaders,
        adBlockerEnabled: adBlockerEnabled,
        episode: freshEpisode,
      ));
```
`DownloadManager` passes those headers to `_fetchM3u8` (playlist), `_downloadFile` (AES keys) and `_downloadSegmentWithRetry` (segments). The only header ever added is `Range` for the direct-file fallback. Downloads therefore **replicate** the rule's Referer/UA rather than bypassing anti-hotlink.
Sources: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/download/download_controller.dart , https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/download/download_manager.dart

### 3.9 Other download files

- `lib/pages/download/download_controller.dart` (1089 lines) — MobX `DownloadController`: `init`, `_migrateDanmakuDataToFiles`, `_initBackgroundService`, `_onDownloadProgress`, `refreshRecords`, `getLocalVideoPath`, `getCachedDanmakus`/`updateCachedDanmakus`, `startDownload`, `_processResolveQueue`, `_resolveAndEnqueue`, `pauseDownload`, `pauseAllDownloads`, `retryDownload`, `priorityDownload`, `resumeAllDownloads`, `deleteEpisode`, `deleteRecord`.
- `lib/pages/download/download_page.dart` (372 lines) — `DownloadPage`; `_getStatusText` yields `'已完成 · <bytes>'`, `'<pct>% · x/y 分片 · <speed>'`, `'已暂停 · <pct>%'`, `'排队中'`, `'正在解析视频源'`, `'下载失败'`; actions 播放 / 暂停 / 继续 / 重试 / 优先下载 / 删除.
- `lib/pages/download/download_widgets.dart` (324 lines) — `DownloadRecordCard`, `DownloadEpisodeTile`, `_EpisodeStatusBadge`; aggregate progress = mean of per-episode progress with completed counted as `1.0`.
- `lib/pages/download/download_episode_sheet.dart` — `DownloadEpisodeSheet` / `_EpisodeTile` / `_startBatchDownload`.
- `lib/services/download/download_path_migration.dart` — `Future<void> rebaseIosDownloadPaths(Box<DownloadRecord> downloads, {Future<String> Function() downloadDirectory = getDefaultDownloadDirectory})`; comment "iOS can move an app's data container on update. Download files move with it, but the absolute paths stored in Hive still point to the old container.", rewriting `episode.downloadDirectory` / `episode.localM3u8Path` to `path.join(downloadBase, '${record.bangumiId}_${record.pluginName}', '${entry.key}')`.
Sources: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/download/download_controller.dart , https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/download/download_page.dart , https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/download/download_widgets.dart , https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/download/download_path_migration.dart

---

## 4. Cache / low-memory mode

### 4.1 Exact mpv properties and default values

`lib/services/player/playback_cache_policy.dart` — `class PlaybackCachePolicy`:
```dart
  static const int _lowMemoryBufferSize = 2 * 1024 * 1024;
  static const int _defaultBufferSize = 1500 * 1024 * 1024;
  ...
  bool get networkAutomatic =>
      LowMemoryMode.current == LowMemoryMode.auto &&
      !_isLocalPlayback() &&
      MeteredNetworkService.isMetered;

  int get bufferSize => LowMemoryMode.current.isEnabled(
        isMetered: MeteredNetworkService.isMetered,
        isLocalPlayback: _isLocalPlayback(),
      )
          ? _lowMemoryBufferSize
          : _defaultBufferSize;
  ...
      await _writes.run(() async {
        if (!identical(_currentPlayer(), player)) { return; }
        final size = bufferSize.toString();
        await pp.setProperty('demuxer-max-bytes', size);
        await pp.setProperty('demuxer-max-back-bytes', size);
      });
```
- **Low-memory: `2 * 1024 * 1024` = 2 MiB** for both properties.
- **Default: `1500 * 1024 * 1024` = 1 572 864 000 bytes (1500 MiB)** for both.
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/player/playback_cache_policy.dart

The same value is passed to media_kit at construction (`bufferSize: cachePolicy.bufferSize`), and the pinned fork applies it as `'demuxer-max-bytes': configuration.bufferSize.toString()` / `'demuxer-max-back-bytes': configuration.bufferSize.toString()`.
Sources: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/player/controller/player_playback_controller.dart , https://raw.githubusercontent.com/Predidit/media-kit/803c4a27912091db6f839d48d1b984a0d9f588c7/media_kit/lib/src/player/native/player/real.dart (≈ lines 2162–2163)

Complete set of cache-related mpv properties set by Kazumi (exact strings):

| mpv property | value | source file:line |
|---|---|---|
| `demuxer-max-bytes` | `bufferSize` (2 MiB low-memory / 1500 MiB default) | `lib/services/player/playback_cache_policy.dart`:65 |
| `demuxer-max-back-bytes` | `bufferSize` | `lib/services/player/playback_cache_policy.dart`:66 |
| `cache-secs` | `'0'` suspended, `'36000'` resumed (Android only) | `lib/pages/player/controller/player_playback_controller.dart`:255 |
| `demuxer-readahead-secs` | `'0'` / `'1'` (Android only, prefetch suspend) | same file:259 |
| `demuxer-cache-dir` | `await getPlayerTempPath()` | same file:323 |
| `demuxer-lavf-format` | `'hls'` when the resolved format is HLS | same file:453 |
| `http-proxy` | formatted proxy / `'http://${proxy.$1}:${proxy.$2}'` | same file:355, 364 |
| `af` | `"scaletempo2=max-speed=8"` | same file:331 |
| `volume-max` | `"100"` (Android) | same file:336 |
| `ao` | `"opensles"` or `"audiotrack"` (Android) | same file:341, 343 |
| `hls_ad_filter` | `${configuration.adBlocker ? 1 : 0}` (media_kit fork) | media-kit fork `real.dart`:2169 |

`setPrefetchSuspended` (Android only):
```dart
      final pp = player.platform as NativePlayer;
      await pp.setProperty('cache-secs', wanted ? '0' : '36000');
      ...
      await pp.setProperty('demuxer-readahead-secs', wanted ? '0' : '1');
```
and the disk-cache comment:
```dart
      // media-kit 默认启用硬盘作为双重缓存，这可以维持大缓存的前提下减轻内存压力
      // media-kit 内部硬盘缓存目录按照 Linux 配置，这导致该功能在其他平台上被损坏
      // 该设置可以在所有平台上正确启用双重缓存
      await pp.setProperty("demuxer-cache-dir", await getPlayerTempPath());
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/player/controller/player_playback_controller.dart

### 4.2 Where the setting is stored

`lib/services/player/low_memory_mode.dart`:
```dart
enum LowMemoryMode {
  auto('auto'), always('always'), never('never');
  ...
  static LowMemoryMode get current {
    return switch (GStorage.getSetting(SettingsKeys.lowMemoryPolicy)) {
      'always' => always,
      'never' => never,
      null => GStorage.getSetting(SettingsKeys.lowMemoryMode) ? always : auto,
      _ => auto,
    };
  }
  static Stream<void> watch() => GStorage.watchSettings([
        SettingsKeys.lowMemoryPolicy,
        SettingsKeys.lowMemoryMode,
      ]);
  Future<void> save() => GStorage.putSetting(SettingsKeys.lowMemoryPolicy, _storageValue);
  bool isEnabled({required bool isMetered, bool isLocalPlayback = false}) {
    return switch (this) {
      auto => isMetered && !isLocalPlayback,
      always => true,
      never => false,
    };
  }
}
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/player/low_memory_mode.dart

Keys / defaults (`lib/services/storage/settings_keys.dart`, `SettingGroup.player`):
```dart
  static const lowMemoryMode = SettingKey<bool>(
    _SettingBoxKey.lowMemoryMode, false, group: SettingGroup.player,
  );
  // Null preserves the legacy lowMemoryMode choice until a policy is selected.
  static const lowMemoryPolicy = SettingKey<String?>(
    'lowMemoryPolicy', null, group: SettingGroup.player,
  );
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/storage/settings_keys.dart

UI — `lib/pages/settings/low_memory_mode_settings.dart`: `LowMemoryModeSettingsTile` (title 「低内存模式」) → `_LowMemoryModeDialog`, listing `LowMemoryMode.values` as radio tiles 跟随网络 (`auto`, labelled `${option.label}（默认）`) / 始终开启 / 始终关闭, with descriptions `'移动数据自动开启，WLAN / 有线网络自动关闭'`, `'所有网络均减少缓存，降低内存占用'`, `'使用完整缓存，移动数据下也不自动开启'`, and the footnote `'减少缓存可降低内存占用和额外流量，网络不稳定时可能更容易缓冲。\n选择后立即生效并记住选择；跟随网络仅影响在线播放。'`
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/settings/low_memory_mode_settings.dart

Metered toast when auto engages: `KazumiDialog.showToast(message: '移动数据下已自动开启低内存模式，可在播放设置中改为始终关闭');`

### 4.3 Negative findings

- No Dart identifiers `cacheSize`, `demuxerMaxBytes`, `demuxerMaxBackBytes` exist. `cacheSize` appears only as an unrelated Flutter image-cache helper (`lib/utils/image_extension.dart`, `lib/bean/card/network_img_layer.dart`).
- `未查到` any user-facing video cache-size setting; the only cache control in settings is the **image** cache (`lib/pages/settings/storage_settings.dart` → `ImageCacheService().sizeInBytes()` / `.clear()`, dialog text `'图片会在下次加载时重新下载。视频、记录和设置不会删除。'`).
- `未查到` a `bufferSize` settings key — buffer size is derived entirely from `LowMemoryMode` + metered state (`SettingsKeys.lowMemoryPolicy` / `SettingsKeys.lowMemoryMode`).
Sources: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/utils/image_extension.dart , https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/settings/storage_settings.dart

---

## 5. Search aggregation / dedup / source alias

### 5.1 Aggregation across rules/sources

`lib/services/plugin/plugin_search_service.dart` — `class PluginSearchService`:
```dart
  Future<void> queryAllSource(String keyword) async {
    infoController.pluginSearchResponseList.clear();
    infoController.pluginSearchStatus.clear();

    final plugins = List<Plugin>.of(pluginsController.pluginList);
    for (final plugin in plugins) {
      infoController.pluginSearchStatus[plugin.name] = PluginSearchStatus.pending;
    }
    await Future.wait(
      plugins.map((plugin) => _queryPlugin(plugin, keyword)),
    );
  }

  Future<void> _queryPlugin(Plugin plugin, String keyword) async {
    if (_isCancelled) return;
    final session = _querySessions.putIfAbsent(plugin.name, AsyncSessionOwner.new).begin();
    try {
      final result = await plugin.queryBangumi(keyword, shouldRethrow: true, cancelToken: _cancelToken);
      if (_isCancelled || session.isStale) return;
      infoController.pluginSearchStatus[plugin.name] = PluginSearchStatus.success;
      if (result.data.isNotEmpty) { pluginsController.validityTracker.markSearchValid(plugin.name); }
      infoController.pluginSearchResponseList.add(result);
    } catch (error) {
      if (_isCancelled || session.isStale) return;
      _handleSearchError(plugin, error);
    }
  }
```
- All rules are queried **in parallel** (`Future.wait` over `pluginsController.pluginList`), each with its own `AsyncSessionOwner` (`/// Per-plugin sessions so a replacement query (alias/manual search) invalidates the write-back of the still-running previous one.`).
- `querySource(String keyword, String pluginName)` re-queries a single rule: it removes that plugin's previous response (`pluginSearchResponseList.removeWhere((response) => response.pluginName == pluginName)`), sets status `pending`, and queries only that plugin.
- Captcha shortcut — `applyHarvestedSearchResult(String pluginName, String html)` → `plugin.parseHarvestedSearch(html)`, marks `success` + `validityTracker.markSearchValid`, pushes the response; returns `false` to make callers fall back to `querySource`.
- Error classification:
```dart
  void _handleSearchError(Plugin plugin, Object error) {
    if (error is CaptchaRequiredException) { ... PluginSearchStatus.captcha; return; }
    if (error is NoResultException) { ... PluginSearchStatus.noResult; return; }
    final name = error is SearchErrorException ? error.pluginName : plugin.name;
    infoController.pluginSearchStatus[name] = PluginSearchStatus.error;
  }
```
- `void cancel() { _isCancelled = true; _cancelToken.cancel(); }`
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/plugin/plugin_search_service.dart

Models — `lib/modules/search/plugin_search_module.dart`:
```dart
/// Per-plugin search progress, written solely by PluginSearchService.
enum PluginSearchStatus { pending, success, error, noResult, captcha }

class SearchItem { String name; String src; ... }
class PluginSearchResponse { String pluginName; List<SearchItem> data; ... }
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/modules/search/plugin_search_module.dart

Controller state — `lib/pages/info/info_controller.dart`:
```dart
  var pluginSearchResponseList = ObservableList<PluginSearchResponse>();
  var pluginSearchStatus = ObservableMap<String, PluginSearchStatus>();
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/info/info_controller.dart

### 5.2 Deduplication — exact code and (absence of) normalization

`lib/pages/info/source_sheet.dart`, inside `_SourceSheetState.build`:
```dart
          final groupsByName = {
            for (final plugin in _pluginsController.pluginList)
              plugin.name: _SourceSearchGroup(
                name: plugin.name,
                keyword: _keywordFor(plugin.name),
                status: widget.infoController.pluginSearchStatus[plugin.name] ?? PluginSearchStatus.pending,
                results: <SearchItem>[],
              ),
          };
          final seenBySource = <String, Set<String>>{};
          String? firstResultSource;
          // Responses follow completion order; groups keep configured order.
          for (final response in widget.infoController.pluginSearchResponseList) {
            final group = groupsByName[response.pluginName];
            if (group == null) continue;
            final seen = seenBySource.putIfAbsent(group.name, () => <String>{});
            for (final result in response.data) {
              if (seen.add(result.src)) group.results.add(result);
            }
            if (group.hasResults) firstResultSource ??= group.name;
          }
```
- **Dedup is per-source only**, keyed on the raw `SearchItem.src` string via an exact `Set<String>` membership test. **No normalization whatsoever** — no `toLowerCase()`, no `trim()`, no URL canonicalization, no title/fuzzy matching, no Levenshtein distance. A `lib/utils/string_match.dart` helper does **not** exist on `main` (see head of this report).
- **No cross-source dedup / no merged ranked list**: the same title returned by three rules is displayed three times, once per source group.
- Ordering: group order = `_pluginsController.pluginList` order (configured order); within a group, the rule's own result order; across groups, response-list order, which is completion order. `firstResultSource` (first group with results) is auto-expanded to preview.
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/info/source_sheet.dart

Group rendering / labels — `_SourceSearchGroup` in `lib/pages/info/source_sheet_view.dart`:
```dart
  bool get isSearching => status == PluginSearchStatus.pending;
  bool get hasResults => status == PluginSearchStatus.success && results.isNotEmpty;

  String get statusLabel => switch (status) {
        PluginSearchStatus.pending => '检索中…',
        PluginSearchStatus.success => results.isEmpty ? '无结果' : '${results.length} 个结果',
        PluginSearchStatus.noResult => '无结果',
        PluginSearchStatus.error => '检索失败',
        PluginSearchStatus.captcha => '需要验证',
      };
```
with `enum _SourceDisplay { preview, expanded, collapsed }`, `_autoExpandFirstResult()` (sets `_display[name] = _SourceDisplay.preview`), per-group menu items 修改此来源的检索词 / 使用别名检索此来源 / 重新检索此来源 / 在浏览器中打开, and header summary `'检索中 ${groups.length - pending}/${groups.length} · $resultCount 个结果'` or `'${groups.length} 个来源 · $resultCount 个结果'`.
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/info/source_sheet_view.dart

A separate, unrelated dedup exists for Bangumi API pagination in `lib/pages/search/search_controller.dart`, keyed by Bangumi subject id:
```dart
      final existingIds = bangumiList.map((item) => item.id).toSet();
      final newItems =
          page.items.where((item) => existingIds.add(item.id)).toList();
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/search/search_controller.dart

### 5.3 `lib/utils/search_parser.dart` is NOT the plugin-search parser

`SearchParser` implements the **Bangumi search query DSL**, not source-result handling. It declares `static const String _fieldNames = 'id|tag|sort|season|date|rank|score|weekday|nsfw';` and regexes such as `RegExp(r'id:(\d+)')`, `RegExp(r'sort:([\w\-]+)')`, `RegExp(r'season:(\d{4}Q[1-4])')`, `RegExp(r'date:(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})')`, `RegExp(r'rank:(\d*)\.\.(\d*)')`, `RegExp(r'score:(\d+(?:\.\d+)?)?\.\.(\d+(?:\.\d+)?)?')`, `RegExp(r'weekday:([1-7](?:,[1-7])*)')`; exposes `parseId/parseTags/parseSort/parseSeason/parseDateRange/parseRankRange/parseScoreRange/parseWeekdays/parseKeywords`, `toFilterState()`, `static String fromFilterState(SearchFilterState state)`, and `static SearchDateRange? seasonToDateRange(String season)` (e.g. `2024Q1` → `DateTime(year, startMonth - 1, 1)` .. `DateTime(year, startMonth + 2, 1)`). Supporting types: `SearchDateRange`, `SearchIntRange` (`String toToken() => '${min ?? ''}..${max ?? ''}'`), `SearchDoubleRange`, `SearchFilterState` (defaults `id: ''`, `sort: 'heat'`, `tags: const []`, `weekdays: const []`).
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/utils/search_parser.dart

### 5.4 Source-alias feature

`lib/pages/info/source_alias_dialog.dart` is a `part` of `source_sheet.dart`:
```dart
part 'source_alias_dialog.dart';
part 'source_captcha_flow.dart';
part 'source_sheet_view.dart';
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/info/source_sheet.dart

Alias entry points in `source_sheet.dart`:
```dart
  void _showAliasPicker(String pluginName) {
    if (widget.infoController.bangumiItem.alias.isEmpty) {
      KazumiDialog.showToast(message: '无可用别名，试试修改检索关键词');
      return;
    }
    _showAliasPickerDialog(
      sourceName: pluginName,
      aliases: widget.infoController.bangumiItem.alias,
      onAliasSelected: (alias) => _querySource(alias, pluginName),
      onAliasesChanged: () => _collectController
          .updateLocalCollect(widget.infoController.bangumiItem),
    );
  }

  void _showCustomKeyword(String pluginName) => _showCustomKeywordDialog(
        initialKeyword: _keywordFor(pluginName),
        sourceName: pluginName,
        onSubmit: (keyword) {
          final item = widget.infoController.bangumiItem;
          if (!item.alias.contains(keyword)) {
            item.alias.add(keyword);
            _collectController.updateLocalCollect(item);
          }
          _querySource(keyword, pluginName);
        },
      );
```
Key facts:
- Aliases are the **Bangumi item's own alias list** (`BangumiItem.alias`, persisted as Hive field 10 — `bangumi_item.g.dart`: `alias: fields[10] == null ? [] : (fields[10] as List).cast<String>(),`), populated from subject JSON by `parseBangumiAliases(Map<String, dynamic> jsonData)`.
- They are **shared across all source rules** — dialog comment: `// Aliases are shared across source rules.`
- Base keyword: `final _keyword = item.nameCn.isEmpty ? item.name : item.nameCn;`; per-source overrides are in-memory only: `final Map<String, String> _sourceKeywords = {};` with `String _keywordFor(String name) => _sourceKeywords[name] ?? _keyword;`.
- Selecting an alias re-queries **only that source**; retry is `void _retry(String name) => _querySource(_keywordFor(name), name);`.
- `_AliasPickerDialog` renders 「别名检索」 + `'仅检索来源：${widget.sourceName}'`, one `ListTile` per alias, delete with confirm 「删除后无法恢复，确认要永久删除这个别名吗？」 then `widget.onAliasesChanged()`; it pops when the last alias is removed.
- `_showCustomKeywordDialog` renders 「修改检索词」 with `helperText: '仅检索来源：$sourceName'` and label `'检索关键词'`.
Sources: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/info/source_alias_dialog.dart , https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/modules/bangumi/bangumi_item.g.dart , https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/collect/collect_controller.dart (`Future<void> updateLocalCollect(BangumiItem bangumiItem)`)

---

## 6. Logging / error handling

### 6.1 Logger package usage

`pubspec.yaml`: `logger: ^2.6.2` (plus `synchronized: any` for the log-file lock).
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/pubspec.yaml

`lib/services/logging/logger.dart` (253 lines) — `class KazumiLogger` singleton over `package:logger`:
```dart
  KazumiLogger._internal() {
    _logger = Logger(
      filter: KazumiLogFilter(),
      printer: KazumiLogPrinter(),
      output: KazumiLogOutput(),
    );
  }

  static final KazumiLogger _instance = KazumiLogger._internal();
  factory KazumiLogger() { return _instance; }
```
Public API: `t`, `d`, `i`, `w`, `e`, `f`, each `(dynamic message, {Object? error, StackTrace? stackTrace, bool forceLog = false})`. `forceLog` wraps the call in a zone:
```dart
  void _log(void Function() logFn, bool forceLog) {
    if (forceLog) {
      runZoned(logFn, zoneValues: {_forceLogKey: true});
    } else {
      logFn();
    }
  }
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/logging/logger.dart

### 6.2 Log levels and filtering

```dart
class KazumiLogFilter extends LogFilter {
  @override
  bool shouldLog(LogEvent event) {
    final forceLog = Zone.current[_forceLogKey] as bool? ?? false;
    if (forceLog) { return true; }
    return event.level.index >= Logger.level.index;
  }
}
```
Threshold is package:logger's global `Logger.level` (default `Level.debug`); `forceLog` bypasses it. All six levels are colorized in `KazumiLogPrinter._colorizeLevel`: `trace` → `\x1B[90m`, `debug` → `36`, `info` → `32`, `warning` → `33`, `error` → `31`, `fatal` → `35`, printed as `level.name.toUpperCase().padRight(7)`.

Line format: `final lines = <String>['$time $level $message$error'];` with `_formatTime` producing `HH:mm:ss.SSS`; messages flattened by `_singleLineLogText` (`value.toString().replaceAll(RegExp(r'\s+'), ' ').trim()`); `Map`/`Iterable` messages `jsonEncode`d with `toEncodable: (object) => object.toString()`. Fatal appends up to `static const int _fatalStackFrameLimit = 8;` frames, dropping lines containing `package:logger/` or `package:kazumi/services/logging/logger.dart`.

Separately, the **mpv/player** log level is a distinct setting: `SettingsKeys.playerLogLevel` (default `2`), presented through `const Map<int, String> playerLogLevelMap = { 0: "错误", 1: "警告", 2: "简略", 3: "详细", /* 4: "调试", 5: "全部" are hidden: 输出内容过于冗长 */ };` and applied as `logLevel: MPVLogLevel.values[debug.playerLogLevel]` (`lib/pages/player/controller/player_debug_controller.dart`: `int playerLogLevel = 2;`).
Sources: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/logging/logger.dart , https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/utils/constants.dart , https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/storage/settings_keys.dart , https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/player/controller/player_playback_controller.dart , https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/player/controller/player_debug_controller.dart

### 6.3 Log file and retention

```dart
class KazumiLogOutput extends LogOutput {
  static final Lock _logLock = Lock();
  static String? _logFilePath;

  static Future<String> _getLogFilePath() async {
    if (_logFilePath != null) return _logFilePath!;
    final dir = (await getApplicationSupportDirectory()).path;
    final logDir = p.join(dir, "logs");
    final directory = Directory(logDir);
    if (!await directory.exists()) { await directory.create(recursive: true); }
    _logFilePath = p.join(logDir, "kazumi_logs.log");
    return _logFilePath!;
  }

  @override
  void output(OutputEvent event) {
    for (var line in event.lines) { print(line); }

    // Write to file if: warning/error/fatal OR forceLog is enabled
    final forceLog = Zone.current[_forceLogKey] as bool? ?? false;
    if (event.level.index >= Level.warning.index || forceLog) { _writeToFile(event); }
  }
```
- Path: **`<ApplicationSupportDirectory>/logs/kazumi_logs.log`**, written with `mode: FileMode.writeOnlyAppend` inside `_logLock.synchronized(...)`; ANSI escapes stripped by `_removeAnsiCodes` (`RegExp(r'\x1B\[[0-9;]*m')`); failures degrade to `print('Failed to write log to file: ...')`.
- **Only warning and above (plus `forceLog`) is persisted**; every level is printed to stdout.
- Helpers: `Future<File> getLogsPath()` (creates dir/file) and `Future<bool> clearLogs()` (truncates via `file.writeAsString('')`).
- **Retention: 未查到.** An exhaustive recursive search over all `lib/**/*.dart` for `rotation`, `maxLogSize`, `retention`, `deleteLogs` finds no log-rotation, size-cap, or age-based cleanup code — the file grows unbounded and is only ever emptied by explicit user action.
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/logging/logger.dart

### 6.4 `lib/pages/logs/logs_page.dart`

`class LogsPage` re-derives the same path independently:
```dart
  Future<File> _getLogsFile() async {
    final directory = await getApplicationSupportDirectory();
    final path = directory.path;
    return File('$path/logs/kazumi_logs.log');
  }
```
Paging: `static const int _initialLoadCount = 50;` / `static const int _loadMoreCount = 100;`, loading more when scroll passes `final threshold = maxScroll * 0.8;`. Rendering: `SelectionArea` → horizontal `SingleChildScrollView` → `ListView.builder` with `fontFamily: 'monospace', fontSize: 12`, `softWrap: false`. Two FABs: `tooltip: '清空日志'` (`_clearLogs`) and `tooltip: '复制日志'` (`_copyLogs` → `Clipboard.setData`). Error state `GeneralErrorWidget(title: '无法读取日志', errMsg: '请稍后重新加载。', icon: Icons.receipt_long_rounded, onRetry: _loadLogs)`; empty state `GeneralEmptyState(icon: Icons.receipt_long_rounded, title: '还没有日志记录')`; success toast `'已复制到剪贴板'`, clear failure `'清空失败: $e'`.
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/logs/logs_page.dart

### 6.5 Global error handling

`lib/main.dart` (111 lines) contains **no `FlutterError.onError`, no `PlatformDispatcher.instance.onError`, and no `runZonedGuarded`**. The only error path is the storage-init try/catch:
```dart
  try {
    final hivePath = '${(await getApplicationSupportDirectory()).path}/hive';
    await Hive.initFlutter(hivePath);
    await GStorage.init();
  } catch (e) {
    debugPrint('Storage initialization failed: $e');
    if (isDesktop()) { await windowManager.ensureInitialized(); ... }
    runApp(MaterialApp(
        title: '初始化失败',
        ...
        builder: (context, child) { return const StorageErrorPage(); }));
    return;
  }
```
This is confirmed by an exhaustive recursive grep over all `lib/**/*.dart` for `FlutterError|PlatformDispatcher|runZonedGuarded|ErrorWidget\.builder|onError`: the only `FlutterError` reference in the whole lib tree is
```dart
      FlutterError.reportError(
        FlutterErrorDetails(
```
in `lib/pages/player/player_keyboard_shortcuts.dart` (line ≈181). There is no `PlatformDispatcher.instance.onError` and no `runZonedGuarded(_)`. **`未查到` a global/uncaught-error handler** — error handling is per-call-site plus the shared network mapper below.
Sources: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/main.dart , https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/player/player_keyboard_shortcuts.dart

Network error handling — `lib/request/core/network_error_mapper.dart`, `class NetworkErrorMapper`:
```dart
  static Future<NetworkException> mapException(DioException error) async {
    switch (error.type) {
      case DioExceptionType.badCertificate:   ... message: '证书有误！'
      case DioExceptionType.badResponse:      ... message: '服务器异常，请稍后重试！'   (statusCode captured)
      case DioExceptionType.cancel:           ... message: '请求已被取消，请重新请求'
      case DioExceptionType.connectionError:  ... message: '连接错误，请检查网络设置'
      case DioExceptionType.connectionTimeout:... message: '网络连接超时，请检查网络设置'
      case DioExceptionType.receiveTimeout:   ... message: '响应超时，请稍后重试！'
      case DioExceptionType.sendTimeout:      ... message: '发送请求超时，请检查网络设置'
      case DioExceptionType.transformTimeout: ... type: parseError, message: '响应解析超时，请稍后重试！'
      case DioExceptionType.unknown: final connection = await _connectionLabel(); ... message: '$connection 网络异常'.trimLeft()
    }
  }

  static NetworkException parse(Object error, StackTrace stackTrace) { ... type: NetworkExceptionType.parseError, message: '响应解析失败' ... }
```
`_connectionLabel()` uses `connectivity_plus`: `'正在使用移动流量'`, `'正在使用wifi'`, `'正在使用局域网'`, `'正在使用代理网络'`, `'正在使用其他网络'`, `'未连接到任何网络'`, else `''`.
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/request/core/network_error_mapper.dart

HTTP logging interceptor — `lib/request/core/dio_logger_interceptor.dart`, `class DioLoggerInterceptor extends Interceptor`, timing stored in `options.extra['_kazumiStartedAt']`:
```dart
    KazumiLogger().d('HTTP: --> ${options.method} ${options.uri}');
...
    KazumiLogger().d('HTTP: <-- ${response.statusCode} ${response.requestOptions.method} ${response.requestOptions.uri}${elapsed == null ? '' : ' ${elapsed}ms'}');
...
    KazumiLogger().w('HTTP: <-- $status ${err.requestOptions.method} ${err.requestOptions.uri}${elapsed == null ? '' : ' ${elapsed}ms'}', error: err.message);
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/request/core/dio_logger_interceptor.dart

### 6.6 `lib/bean/settings/network_mirror_settings.dart`

This file is **not** logging-related. It is `class NetworkMirrorSettings` (section title 「访问加速」) with three tiles:
- 番剧条目加速 — `BangumiAcceleration` modes with extension descriptions `direct => '访问官方接口'`, `ech => '启用加密握手'`, `mirror => '镜像接口（默认）'`; key `SettingsKeys.bangumiAcceleration`.
- 规则仓库镜像 — `SettingsTile.switchTile` bound to `SettingsKeys.enableGitProxy`, description `'加速规则的下载与更新'`.
- 图片加速 — `ImageAcceleration`, labels `直连` / `ECH` / `镜像`, descriptions `'加载原始图片'` / `'加密握手（推荐）'` / `'使用图片镜像'`; key `SettingsKeys.imageAcceleration`.

Selection is persisted by `_save(setting, selected.name)` → `GStorage.putSetting(key, value)`. The file contains no logger calls.
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/bean/settings/network_mirror_settings.dart

`catch (e` sites are pervasive across `lib/` (dozens), typically paired with `KazumiLogger().w(...)` / `.e(..., error: e)`, e.g. `} catch (e) { episode.status = DownloadStatus.failed; episode.errorMessage = e.toString(); ... KazumiLogger().e('DownloadManager: episode download failed', error: e); }` and `} on PlatformException catch (e) { throw DownloadDirectoryException(...); }`. The count was not exhaustively enumerated.

---

## 7. "一起看" SyncPlay

### 7.1 Where the service lives

`lib/services` consists of the subdirectories `download`, `logging`, `network`, `platform`, `player`, `plugin`, `shaders`, `storage`, `sync`, `update`, `video_source`.
Source: https://api.github.com/repos/Predidit/Kazumi/contents/lib/services?ref=main

SyncPlay lives under `lib/services/player/`:
- **`lib/services/player/syncplay_client.dart`** (951 lines) — protocol implementation
- **`lib/services/player/syncplay_endpoint.dart`** — endpoint parsing / TLS policy

plus the controller `lib/pages/player/controller/player_syncplay_controller.dart` and the UI `lib/pages/player/syncplay_sheet.dart`. (`lib/utils/syncplay.dart` does not exist.)

### 7.2 Protocol version

```dart
// https://syncplay.pl/about/protocol/
...
  Future<void> joinRoom(String room, String username) async {
    await _sendMessage(HelloMessage(
      username: username,
      version: '1.7.0',
      room: room,
    ));
  }
```
**Version string sent in `Hello`: `'1.7.0'`.**
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/player/syncplay_client.dart

### 7.3 Default public server host/port

`lib/services/player/syncplay_endpoint.dart`:
```dart
/// Official endpoint selected for new and reset settings.
const String defaultSyncPlayEndPoint = 'syncplay.pl:8996';

/// Official public endpoints that must use STARTTLS.
const Set<String> officialSyncPlayEndPoints = {
  'syncplay.pl:8995',
  defaultSyncPlayEndPoint,
  'syncplay.pl:8997',
  'syncplay.pl:8998',
  'syncplay.pl:8999',
};

class SyncPlayEndPoint {
  final String host;
  final int port;
  const SyncPlayEndPoint({required this.host, required this.port});
}

SyncPlayEndPoint? parseSyncPlayEndPoint(String endPoint) { /* trims, supports [IPv6]:port, validates port 1..65535, else null */ }

bool isOfficialSyncPlayEndPoint(SyncPlayEndPoint endPoint) {
  final normalizedEndPoint = '${endPoint.host.toLowerCase()}:${endPoint.port}';
  return officialSyncPlayEndPoints.contains(normalizedEndPoint);
}
```
**Default server: `syncplay.pl:8996`.** The five official endpoints are exactly `syncplay.pl:8995`, `:8996`, `:8997`, `:8998`, `:8999`; all force STARTTLS (`final enableTLS = isOfficialSyncPlayEndPoint(parsed);`).
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/player/syncplay_endpoint.dart

Setting: `static const syncPlayEndPoint = SettingKey<String>(_SettingBoxKey.syncPlayEndPoint, defaultSyncPlayEndPoint, group: SettingGroup.player);` and `static const syncPlayUserName = SettingKey<String>('syncPlayUserName', '', group: SettingGroup.player);`
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/storage/settings_keys.dart

### 7.4 Implemented messages

| class | wire payload |
|---|---|
| `HelloMessage{username, version, room}` | `{"Hello":{"username":..,"room":{"name":room},"version":version,"features":{"sharedPlaylists":true,"chat":true,"featureList":true,"readiness":true,"managedRooms":false}}}` |
| `StateMessage{position, paused, doSeek, setBy, clientAck, serverAck, clientLatencyCalculation, latencyCalculation, clientRtt}` | `{"State":{"ignoringOnTheFly":{"client":..,"server":..},"ping":{"clientRtt":..,"clientLatencyCalculation":..,"latencyCalculation":..},"playstate":{"position":..,"paused":..,"setBy":..,"doSeek":..}}}` |
| `SetMessage{duration, fileName, username, size, setBy, room, setJoined, setReady}` | `{"Set":{room:{"room":{"name":room},"event":{"joined":true}}}}` **or** `{"Set":{"ready":{"isReady":true,"manuallyInitiated":false}}}` **or** `{"Set":{"file":{"duration":..,"name":..,"size":..},"user":{setBy:{"room":{"name":room}}}}}` |
| `ChatMessage{message}` | `{"Chat": message}` |
| `_TLSMessage{message}` (private) | `{"TLS":{"startTLS": message}}` — sent with `'send'` to request STARTTLS |

Verbatim fragments:
```dart
class HelloMessage extends SyncplayMessage { ... 'version': version,
          'features': { 'sharedPlaylists': true, 'chat': true, 'featureList': true, 'readiness': true, 'managedRooms': false } ... }

class ChatMessage extends SyncplayMessage { ... Map<String, dynamic> toJson() => {'Chat': message}; }

class _TLSMessage extends SyncplayMessage { ... Map<String, dynamic> toJson() => {'TLS': {'startTLS': message}}; }
```

Received-message dispatch (`_handleMessage(dynamic data, RawSocket sourceSocket)`):
- `TLS` — requires `tlsData['startTLS'] == 'true'` → `unawaited(_upgradeToTLS(sourceSocket))`; otherwise `SyncplayConnectionException('SyncPlay: server rejected TLS connection upgrade')`.
- `Hello` — sets `_username = json['Hello']['username']`, `_currentRoom = json['Hello']['room']['name']`, then `_runInBackground(_setReady())`; emits `{'username':..,'room':..}` on `onGeneralMessage`.
- `State` — reads `ping.latencyCalculation` / `ping.serverRtt`, calls `_updateClientRttAndFd(...)`, handles `ignoringOnTheFly` client/server acks, then (when not locally ignoring) computes `calculatedPositon` → emits `onPositionChangedMessage` with `{'calculatedPositon','position','paused','doSeek','setBy','clientRtt','serverRtt','avrRtt','fd'}`, and **replies with `_sendState(...)`** (the ping loop).
- `Set` — `playlistIndex` → `{'type':'init','username': json['Set']['playlistIndex']['user'] ?? ''}`; `user` map → each user's `event.keys.first` emitted as `{'type': event, 'username': username}`, plus `user[..]['file']['name']` → `_currentFileName` and `onFileChangedMessage`.
- `Chat` — requires both `message` and `username`; emits `onChatMessage`.
- anything else → `SyncplayProtocolException('SyncPlay: unknown message type')` on the general-message error stream.

Transport: raw TCP `RawSocket.connect(_host, _port)`; messages are `jsonEncode(message.toJson())` + `'\r\n'`; incoming bytes UTF-8 decoded and split by brace counting. Timeouts `const Duration _tlsHandshakeTimeout = Duration(seconds: 10);` and `const Duration _socketWriteTimeout = Duration(seconds: 10);`. `connect({required bool enableTLS})` throws `StateError('SyncplayClient.connect may only be called once')` on reuse; `disconnect()` is permanent.

Ping/RTT math:
```dart
const double _pingMovingAverageWeight = 0.85;
...
    _clientRtt = newClientRtt;
    if (_avrRtt == 0) { _avrRtt = _clientRtt; }
    // Use moving average to update RTT, smooth the delay data
    _avrRtt = _avrRtt * _pingMovingAverageWeight + _clientRtt * (1 - _pingMovingAverageWeight);
    // Calculate the forward delay based on the sender's RTT
    if (senderRtt < _clientRtt) { _fd = _avrRtt / 2 + (_clientRtt - senderRtt); }
    else { _fd = _avrRtt / 2; }
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/player/syncplay_client.dart

### 7.5 User list

There is **no explicit roster message**. Presence is derived from the `Set.user` map of received `Set` messages:
```dart
      if (json['Set'].containsKey('user')) {
        Map<String, dynamic> userMap = data['Set']['user'];
        userMap.forEach((username, details) {
          if (!details.containsKey('event')) { return; }
          var event = details['event'].keys.first ?? 'unknown';
          _roomMessageController?.add({'type': event, 'username': username});
        });
        for (var username in userMap.keys) {
          var userData = userMap[username];
          if (userData is Map && userData.containsKey('file')) {
            var fileData = userData['file'];
            var fileName = fileData['name'];
            _currentFileName = fileName;
            _flieChangedMessageController?.add({'name': fileName, 'setBy': username});
          }
        }
      }
```
`PlayerSyncPlayController` consumes those events:
```dart
          if (message['type'] == 'init') {
            if (message['username'] == '') {
              KazumiDialog.showToast(message: 'SyncPlay: 您是当前房间中的唯一用户', duration: const Duration(seconds: 5));
              setPlayingBangumi();
            } else {
              KazumiDialog.showToast(message: 'SyncPlay: 您不是当前房间中的唯一用户, 当前以用户 ${message['username']} 进度为准');
            }
          }
          if (message['type'] == 'left') { KazumiDialog.showToast(message: 'SyncPlay: ${message['username']} 离开了房间', ...); }
          if (message['type'] == 'joined') { KazumiDialog.showToast(message: 'SyncPlay: ${message['username']} 加入了房间', ...); }
```
Sources: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/player/syncplay_client.dart , https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/player/controller/player_syncplay_controller.dart

### 7.6 How host/guest sync works

`lib/pages/player/controller/player_syncplay_controller.dart` — `class PlayerSyncPlayController` (MobX) with `@observable SyncplayClient? syncplayController;`, `@observable String syncplayRoom = '';`, `@observable int syncplayClientRtt = 0;`, `bool get hasSession => syncplayController != null;`. Create and join share one method:

```dart
  Future<void> createRoom(String room, String username,
      Future<void> Function(int episode, {int currentRoad, int offset}) changeEpisode) async {
    ...
    final String syncPlayEndPoint = GStorage.getSetting(SettingsKeys.syncPlayEndPoint);
    KazumiLogger().i('SyncPlay: connecting to $syncPlayEndPoint');
    final parsed = parseSyncPlayEndPoint(syncPlayEndPoint);
    if (parsed == null) { KazumiDialog.showToast(message: 'SyncPlay: 服务器地址不合法 $syncPlayEndPoint'); ... return; }
    final enableTLS = isOfficialSyncPlayEndPoint(parsed);
    final client = SyncplayClient(host: parsed.host, port: parsed.port);
    syncplayController = client;
    try {
      await client.connect(enableTLS: enableTLS);
      ... listeners: onGeneralMessage / onRoomMessage / onFileChangedMessage / onChatMessage / onPositionChangedMessage ...
      await client.joinRoom(room, username);
      ...
      syncplayRoom = room;
    } catch (e) { ... KazumiDialog.showToast(message: 'SyncPlay: 连接失败 $message', ...); }
  }
```

"Host" is decided by the server's room state, not by a client flag: when the room reports no other member (`type == 'init'` with empty username), this client announces the current episode as the shared file and pushes its position:
```dart
  Future<void> setPlayingBangumi({bool? forceSyncPlaying, double? forceSyncPosition}) async {
    final client = syncplayController;
    if (client == null) { return; }
    await _runBestEffortSync(() async {
      await client.setSyncPlayPlaying("${bangumiId()}[${currentEpisode()}]", 10800, 220514438);
      if (!identical(syncplayController, client)) { return; }
      setCurrentPosition(forceSyncPlaying: forceSyncPlaying, forceSyncPosition: forceSyncPosition);
      await client.sendSyncPlaySyncRequest(doSeek: null);
    });
  }
```
The shared "file name" is literally `"<bangumiId>[<episode>]"` with hard-coded `duration = 10800` and `size = 220514438`.

"Guest" behaviour — episode switching driven by the file-change message:
```dart
          RegExp regExp = RegExp(r'(\d+)\[(\d+)\]');
          Match? match = regExp.firstMatch(message['name']);
          if (match != null) {
            int bangumiID = int.tryParse(match.group(1) ?? '0') ?? 0;
            int episode = int.tryParse(match.group(2) ?? '0') ?? 0;
            if (bangumiID != 0 && episode != 0 && episode != currentEpisode()) {
              KazumiDialog.showToast(message: 'SyncPlay: ${message['setBy'] ?? 'unknown'} 切换到第 $episode 话', duration: const Duration(seconds: 3));
              changeEpisode(episode, currentRoad: currentRoad());
            }
          }
```
Play-state and position mirroring:
```dart
          syncplayClientRtt = (message['clientRtt'].toDouble() * 1000).toInt();
          ...
          if (message['paused'] != !playing()) {
            if (message['paused']) { if (message['position'] != 0) { ... pause(enableSync: false); } }
            else { if (message['position'] != 0) { ... play(enableSync: false); } }
          }
          if ((((playerPosition().inMilliseconds -
                              (message['calculatedPositon'].toDouble() * 1000).toInt()).abs() > 1000) ||
                  message['doSeek']) &&
              duration().inMilliseconds > 0) {
            seek(Duration(milliseconds: (message['calculatedPositon'].toDouble() * 1000).toInt()), enableSync: false);
          }
```
i.e. pause/play are mirrored immediately, and a seek is issued when the position drift exceeds **1000 ms** or the server set `doSeek`. Local state is pushed out by `setCurrentPosition({bool? forceSyncPlaying, double? forceSyncPosition})` (it uses `currentPosition()` instead of `playerPosition()` when the gap exceeds 2000 ms), `requestSync({bool? doSeek})`, and `sendChatMessage(String)`. Teardown: `@action Future<void> exitRoom()` and `dispose()`.
Sources: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/player/controller/player_syncplay_controller.dart , https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/player/syncplay_client.dart

### 7.7 UI (`lib/pages/player/syncplay_sheet.dart`, 807 lines)

- Entry point: `Future<void> showSyncPlaySheet(BuildContext context, {required PlayerController playerController, required Future<void> Function(int episode, {int currentRoad, int offset}) changeEpisode})`, with `// Close each step before opening the next to avoid stacked modal routes.` and `enum _SyncPlayDestination { create, join, server }`.
- Sheet title 「一起看」, description 「和朋友同步看番」. The home sheet shows either the connected view (room card with copy button labelled `tooltip: _copied ? '已复制' : '复制'`, 「网络延迟」 `${rtt} ms`, 「同步服务器」 endpoint, footer 「分享房间号，好友即可加入」), a connecting card showing `_readEndPoint()`, or the lobby with 「创建房间」/「加入房间」 choice cards plus a 「同步服务器」 `ListTile`.
- Disconnect button label switches between 「取消连接」 and 「断开连接」 (`connecting ? '取消连接' : '断开连接'`).
- Room generation: `static String _generateRoomNumber() => List.generate(8, (_) => _random.nextInt(10)).join();` (8 digits) with a 「重新生成」 button.
- Random nickname: `const String consonants = 'bcdfghjklmnpqrstvwxyz'; const String vowels = 'aeiou'; final int syllables = 3 + _random.nextInt(2);` then capitalized.
- Validation: room `if (!RegExp(r'^[0-9]{6,10}$').hasMatch(text)) return '房间号为 6-10 位数字';` (hint `'6-10 位数字'`); nickname `if (!RegExp(r'^[a-zA-Z]{4,12}$').hasMatch(text)) return '昵称为 4-12 位英文字母';` (helper `'4-12 位英文字母，房间内可见'`).
- Server sheet: `static const String _customOption = '自定义服务器';`, lists `officialSyncPlayEndPoints` + custom; custom validated by `parseSyncPlayEndPoint` with error `'地址格式为 host:port'` and hint `'example.com:8996'`; description 「房间成员需使用同一服务器」.
- Persisted via `GStorage.putSetting<String>(SettingsKeys.syncPlayUserName, username)` and `GStorage.putSetting<String>(SettingsKeys.syncPlayEndPoint, endPoint)`.
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/player/syncplay_sheet.dart

---

## 8. trace.moe image search

### 8.1 Exact endpoint and request format

`lib/request/config/api_endpoints.dart`:
```dart
  /// 图片识别番剧
  static const String traceApi = 'https://api.trace.moe/search';
```
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/request/config/api_endpoints.dart

`lib/request/apis/trace_api.dart` — **not multipart**; the file endpoint posts raw bytes as the body:
```dart
class TraceApi {
  static final TraceClient _client = TraceClient.instance;

  ///根据图片搜索番剧信息
  static Future<ImageSearchItem> searchAnimeByImageFile(File imageFile,
      {int anilistInfo = 2}) async {
    final bytes = await imageFile.readAsBytes();

    final data = await _client.post(
      ApiEndpoints.traceApi,
      queryParameters: {
        'anilistInfo': anilistInfo,
      },
      data: bytes,
      headers: {'Content-Type': 'image/jpeg'},
    );
    return ImageSearchItem.fromJson(data as Map<String, dynamic>);
  }

  ///根据图片URL搜索番剧信息
  static Future<ImageSearchItem> searchAnimeByImageUrl(String imageUrl,
      {int anilistInfo = 2}) async {
    final data = await _client.post(
      ApiEndpoints.traceApi,
      queryParameters: {
        'anilistInfo': anilistInfo,
        'url': imageUrl,
      },
    );
    return ImageSearchItem.fromJson(data as Map<String, dynamic>);
  }
}
```
Facts: **`POST https://api.trace.moe/search?anilistInfo=2`** (`anilistInfo` defaults to `2`); file mode sends the raw image bytes with `Content-Type: image/jpeg`; URL mode sends `?anilistInfo=2&url=<imageUrl>`. **No `FormData` / multipart is used anywhere in this path.**
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/request/apis/trace_api.dart

Client — `lib/request/clients/trace_client.dart`:
```dart
class TraceClient {
  TraceClient._();
  static final TraceClient instance = TraceClient._();

  Future<dynamic> post(String url, {Object? data, Map<String, dynamic>? queryParameters,
      Map<String, dynamic> headers = const {}, CancelToken? cancelToken}) async {
    try {
      final response = await DioFactory.apiDio.post(url, data: data,
          queryParameters: queryParameters, options: Options(headers: headers), cancelToken: cancelToken);
      return response.data;
    } on DioException catch (e) { throw await NetworkErrorMapper.mapException(e); }
  }
}
```
so it inherits `apiDio`'s default headers `{'referer': '', 'user-agent': getRandomUA()}`.
Sources: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/request/clients/trace_client.dart , https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/request/core/dio_factory.dart

### 8.2 Response parsing

`lib/modules/search/image_search_module.dart` (410 lines) — hand-written models:
```dart
class ImageSearchItem {
  final int? frameCount;
  final String? error;
  final List<ResultItem>? result;

  factory ImageSearchItem.fromJson(Map<String, dynamic> json) {
    return ImageSearchItem(
      frameCount: json['frameCount'] as int?,
      error: json['error'] as String?,
      result: json['result'] == null ? null
          : (json['result'] as List).map((e) => ResultItem.fromJson(e as Map<String, dynamic>)).toList(),
    );
  }
}

class ResultItem {
  final Anilist? anilist;
  final String? filename;
  final dynamic episode; // num, List<num> or null
  final double? from; final double? at; final double? to;
  final double? duration; final double? similarity;
  final String? video; final String? image;

  factory ResultItem.fromJson(Map<String, dynamic> json) {
    dynamic ep = json['episode'];
    if (ep is List) { ep = ep.whereType<num>().toList(); } else if (ep is! num) { ep = null; }
    return ResultItem(anilist: json['anilist'] == null ? null : Anilist.fromJson(json['anilist'] as Map<String, dynamic>),
      filename: json['filename'] as String?, episode: ep, ...);
  }
}
```
`Anilist` fields parsed from JSON: `id, type, idMal, title, format, genres, season, source, status, endDate, startDate, isAdult, siteUrl, studios, duration, episodes, synonyms, relations, seasonInt, coverImage, popularity, seasonYear, bannerImage, externalLinks, countryOfOrigin, synonymsChinese` — note the snake_case wire key: `synonymsChinese: json['synonyms_chinese'] == null ? null : List<String>.from(json['synonyms_chinese'] as List),`. Support types: `AnilistTitle {native, romaji, chinese, english}`, `DateInfo {day, year, month}`, `CoverImage {color, large, medium, extraLarge}`, `Studios`/`StudioEdge`/`StudioNode {id, name, siteUrl}`, `Relations`/`RelationEdge`/`RelationNode {id, title}`, `ExternalLink {id, url, site}`.
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/modules/search/image_search_module.dart

Controller — `lib/pages/search/search_controller.dart`:
```dart
  @action
  Future<void> searchImageByFile(File imageFile) async {
    isImageSearching = true; imageSearchError = ''; imageSearchResults.clear();
    try {
      final result = await TraceApi.searchAnimeByImageFile(imageFile);
      imageSearchResults.addAll(result.result ?? []);
      if (result.error != null && result.error!.isNotEmpty) { imageSearchError = result.error!; }
      else if (imageSearchResults.isEmpty) { imageSearchError = '未找到匹配结果'; }
    } catch (e) { imageSearchError = '图片搜索失败，请稍后重试'; }
    finally { isImageSearching = false; }
  }
```
(`searchImageByUrl` is identical but calls `TraceApi.searchAnimeByImageUrl` and fails with `'图片搜索失败，请检查图片地址或稍后重试'`.) State: `bool isImageSearching`, `String imageSearchError`, `ObservableList<ResultItem> imageSearchResults`, plus `void clearImageSearchState()`.
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/search/search_controller.dart

### 8.3 How results map to Bangumi entries

Mapping is **by title text, not by id**. `lib/pages/search/image_search_widgets.dart`:
```dart
String _title(ResultItem result) {
  final title = result.anilist?.title;
  for (final value in [
    title?.chinese,
    title?.native,
    title?.romaji,
    title?.english,
    result.filename
  ]) {
    if (value != null && value.trim().isNotEmpty) return value.trim();
  }
  return '未知番剧';
}
```
Results are sorted by similarity descending in `lib/pages/search/image_search_page.dart`:
```dart
    final results = _controller.imageSearchResults.toList()
      ..sort((a, b) => (b.similarity ?? 0).compareTo(a.similarity ?? 0));
```
Selecting a result pops that title string out of the route — `_ImageSearchResults(results:..., onSelect: (title) => context.pop(title))`, triggered by the 「搜索这部番」 button (`onPressed: () => onSelect(_title(result))`, in `_BestMatch`) or by tapping another match (`onTap: () => onSelect(_title(result))`, in `_OtherMatch`).
Then `lib/pages/search/search_page.dart` feeds it to the ordinary Bangumi search:
```dart
  Future<void> _imageSearch() async {
    _inputFocus.unfocus();
    final result = await context.pushNamed('/search/image');
    if (!mounted || result is! String || result.isEmpty) return;
    await _submit(result);
  }
```
So the extracted title becomes the keyword of a normal Bangumi subject search. `anilist.id` / `idMal` are parsed into the model, but **`未查到` any code that uses them to look up a Bangumi subject** — there is no id-based (bgm.tv ↔ MAL) mapping step in this path.

Extra derived presentation (same file):
```dart
String _episode(dynamic value) {
  String number(num n) => n % 1 == 0 ? n.toInt().toString() : n.toString();
  if (value is num && value.isFinite) return '第 ${number(value)} 集';
  if (value is List) { ... return '第 ${numbers.join(' / ')} 集'; }
  return '集数未知';
}

String _similarity(ResultItem result) {
  final value = result.similarity;
  return value == null || !value.isFinite ? '未知' : '${(value.clamp(0, 1) * 100).toStringAsFixed(1)}%';
}

String _timeRange(ResultItem result) { /* from ?? at → to, mm:ss, else '时间未知' */ }
```
Low-similarity warning: `final lowSimilarity = similarity == null || !similarity.isFinite || similarity < .87;` → 「相似度偏低，建议对照画面，或换一张截图。」 The 「预览片段」 button requires `result.video` to be a valid http/https URL (`_parseHttpUrl`); thumbnails load `Image.network(result.image)` inside an `AspectRatio(aspectRatio: 16 / 9, ...)`. Section headings: `'找到 ${results.length} 个相似片段'`, 「其他相似片段」, 「相似度最高」.
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/search/image_search_widgets.dart

User-facing disclosure in `image_search_page.dart::_showHelp`: `'识别由 trace.moe 提供。开始识别后，所选图片或图片链接会发送至该服务。'` plus a 「访问 trace.moe」 button → `Uri.parse('https://trace.moe')`. Page headings: `'这一幕，\n出自哪部番？'` / `'用截图找到番名'` / `'搜索图片'`; footer `'由 trace.moe 识别动画截图'`.
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/search/image_search_page.dart

### 8.4 image_picker usage

`pubspec.yaml`: `image_picker: ^1.2.1`.
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/pubspec.yaml

`lib/pages/search/image_search_page.dart`:
```dart
import 'package:image_picker/image_picker.dart';
...
  final _picker = ImagePicker();
...
  Future<void> _pickImage() async {
    if (_busy) return;
    setState(() { _isPicking = true; _inputError = null; });
    try {
      final image = await _picker.pickImage(source: ImageSource.gallery);
      if (image == null) return;
      final file = File(image.path);
      if (await file.length() > 25 * 1024 * 1024) {
        if (mounted) setState(() => _inputError = '图片超过 25 MB，请换一张较小的截图');
        return;
      }
      if (!mounted) return;
      setState(() { _resetResults(); _imageFile = file; });
    } catch (_) {
      if (mounted) setState(() => _inputError = '无法读取图片，请重新选择并检查相册权限');
    } finally { if (mounted) setState(() => _isPicking = false); }
  }
```
Only `ImageSource.gallery` is used (no camera). Size hint shown to the user: `'JPG、PNG、WebP · 最大 25 MB'`. The alternative input mode is an HTTP(S) image URL with a 500 ms debounced preview (`_previewDebounce = Timer(const Duration(milliseconds: 500), ...)`), with the note `'使用可直接访问的 HTTP 或 HTTPS 图片地址'`.
Source: https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/search/image_search_page.dart

**`lib/pages/my/my_space_view.dart` and `lib/pages/index_module.dart`: `未查到` any image-picker usage.** An exhaustive grep for `image_picker|ImagePicker|ImageSource` across all of `lib/` matches only `lib/pages/search/image_search_page.dart` (import, `ImagePicker()`, `ImageSource.gallery`) and the unrelated source-toggle widget `_ImageSourceSelector` in `lib/pages/search/image_search_widgets.dart`; a case-insensitive grep for `image|picker` under `lib/pages/my/` returns **no matches**. Image picking is confined to the 以图搜番 route (`/search/image`, reached from the search bar's `IconButton(tooltip: '以图搜番', onPressed: _imageSearch, icon: Icon(Icons.image_search_rounded))`).

---

## Source URL list

Kazumi (`main` branch):
- https://raw.githubusercontent.com/Predidit/Kazumi/main/pubspec.yaml
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/main.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/utils/constants.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/utils/episode_url.dart *(exists on main; not needed for this report)*
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/utils/file_system.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/utils/http_headers.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/utils/image_extension.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/utils/m3u8_ad_filter.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/utils/m3u8_parser.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/utils/media.dart *(exists on main; not needed for this report)*
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/utils/search_parser.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/utils/string_match.dart *(VERIFIED 404 — does not exist)*
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/modules/plugin/plugin_http_module.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/modules/download/download_module.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/modules/search/plugin_search_module.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/modules/search/image_search_module.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/modules/bangumi/bangumi_item.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/modules/bangumi/bangumi_item.g.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/plugins/plugins.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/plugins/anti_crawler_config.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/repositories/download_repository.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/request/config/api_endpoints.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/request/core/dio_factory.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/request/core/dio_logger_interceptor.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/request/core/network_config.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/request/core/network_error_mapper.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/request/clients/download_http_client.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/request/clients/plugin_site_client.dart *(exists on main; not needed for this report)*
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/request/clients/trace_client.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/request/apis/trace_api.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/logging/logger.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/storage/settings_keys.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/download/download_manager.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/download/download_directory_service.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/download/download_path_migration.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/download/background_download_service.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/player/low_memory_mode.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/player/playback_cache_policy.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/player/syncplay_client.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/player/syncplay_endpoint.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/plugin/plugin_search_service.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/video_source/webview_video_source_service.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/update/auto_updater.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/network/system_proxy_service.dart *(referenced for the negative local-proxy finding)*
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/video/video_controller.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/player/controller/player_playback_controller.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/player/controller/player_debug_controller.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/player/controller/player_syncplay_controller.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/player/player_keyboard_shortcuts.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/player/syncplay_sheet.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/download/download_controller.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/download/download_page.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/download/download_widgets.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/download/download_episode_sheet.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/info/info_controller.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/info/source_sheet.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/info/source_sheet_view.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/info/source_alias_dialog.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/logs/logs_page.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/search/search_controller.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/search/search_page.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/search/image_search_page.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/search/image_search_widgets.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/settings/low_memory_mode_settings.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/settings/download_settings.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/settings/storage_settings.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/settings/proxy/proxy_editor_page.dart *(referenced for the negative local-proxy finding)*
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/plugin_editor/plugin_editor_page.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/collect/collect_controller.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/bean/settings/network_mirror_settings.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/webview/video/video_webview_controller.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/webview/video/impl/video_webview_impl.dart
- https://raw.githubusercontent.com/Predidit/Kazumi/main/android/app/src/main/kotlin/com/example/kazumi/DownloadDirectoryHandler.kt

GitHub API listings (all `?ref=main`):
- https://api.github.com/repos/Predidit/Kazumi/contents/lib
- https://api.github.com/repos/Predidit/Kazumi/contents/lib/utils
- https://api.github.com/repos/Predidit/Kazumi/contents/lib/services
- https://api.github.com/repos/Predidit/Kazumi/contents/lib/repositories
- https://api.github.com/repos/Predidit/Kazumi/contents/lib/webview
- https://codeload.github.com/Predidit/Kazumi/zip/refs/heads/main

Pinned media_kit fork (`Predidit/media-kit` @ `803c4a27912091db6f839d48d1b984a0d9f588c7`) — required because `httpHeaders` → `http-header-fields` and `adBlocker` → `hls_ad_filter` are implemented there, not in Kazumi:
- https://raw.githubusercontent.com/Predidit/media-kit/803c4a27912091db6f839d48d1b984a0d9f588c7/media_kit/lib/src/player/native/player/real.dart
