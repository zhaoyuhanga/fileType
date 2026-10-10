# Kazumi (`Predidit/Kazumi`, branch `main`) — Topic A (Danmaku) & Topic B (Update detection / notifications / timetable)

**Provenance.** All content below was verified against `main` at commit `2768c230407d5fd8a57716160ba1a85a99161581` (2026-10-10, `feat(download): support android custom directories (#2647)`), obtained by `git clone --depth 1 --branch main`. Every claim cites the file it came from; the canonical URL form is
`https://raw.githubusercontent.com/Predidit/Kazumi/main/<path>` (abbreviated below as `<path>` where the base is unambiguous; the full list is at the end). Line numbers are from that commit. `未查到` is used verbatim where a searched-for capability does not exist.

---

# TOPIC A — Danmaku (弹幕)

## A1. API base URLs and endpoints (exact constants)

`lib/request/config/api_endpoints.dart`:

```dart
/// 弹弹官网
static const String dandanIndex = 'https://www.dandanplay.com/';
...
/// DanDanPlay API Domain
static const String dandanAPIDomain = 'https://api.dandanplay.net';

/// 获取弹幕
static const String dandanAPIComment = "/api/v2/comment/";

/// 检索弹弹番剧元数据
static const String dandanAPISearchEpisodes = "/api/v2/search/episodes";

/// 获取弹弹番剧元数据
static const String dandanAPIInfo = "/api/v2/bangumi/";

/// 获取弹弹番剧元数据（通过BGM番剧ID）
static const String dandanAPIInfoByBgmBangumiId = "/api/v2/bangumi/bgmtv/{0}";
```

Source: `lib/request/config/api_endpoints.dart`.

Concrete request URLs built in `lib/request/apis/danmaku_api.dart`:

| Method (class `DanmakuApi`) | HTTP | URL | Query params |
|---|---|---|---|
| `getDanDanBangumiIDByBgmBangumiID(int bgmBangumiID)` | GET | `https://api.dandanplay.net/api/v2/bangumi/bgmtv/{bgmBangumiID}` | – |
| `getDanDanEpisodesByDanDanBangumiID(int bangumiID)` | GET | `https://api.dandanplay.net/api/v2/bangumi/{bangumiID}` | – |
| `searchAnimes(String title)` | GET | `https://api.dandanplay.net/api/v2/search/episodes` | `{'anime': title, 'v2': 'true'}` |
| `getDanDanmaku(int bangumiID, int episode)` | GET | `https://api.dandanplay.net/api/v2/comment/{bangumiID}{episode zero-padded to 4}` | `{'withRelated': 'true', 'chConvert': '0'\|'1'\|'2'}` |
| `getDanDanmakuByEpisodeID(int episodeID)` | GET | `https://api.dandanplay.net/api/v2/comment/{episodeID}` | same |

Verbatim construction of the automatic episode-id convention (`danmaku_api.dart`):

```dart
static Future<List<DanmakuEntry>> getDanDanmaku(
  int bangumiID,
  int episode,
) async {
  if (bangumiID == 0) {
    return [];
  }
  // Automatic matching relies on an undocumented episode ID convention.
  return _getComments('$bangumiID${episode.toString().padLeft(4, '0')}');
}
```

and the comment fetch:

```dart
static Future<List<DanmakuEntry>> _getComments(String episodeID) async {
  final endPoint =
      '${ApiEndpoints.dandanAPIDomain}${ApiEndpoints.dandanAPIComment}$episodeID';
  final conversion = DanmakuChConvert.fromValue(
    GStorage.getSetting(SettingsKeys.danmakuChConvert),
  );
  KazumiLogger().i('Danmaku: final request URL $endPoint');
  final jsonData = await _client.get(
    endPoint,
    queryParameters: {
      'withRelated': 'true',
      'chConvert': conversion.value.toString(),
    },
  );
  final List<dynamic> comments = jsonData['comments'];
  return [for (final comment in comments) DanmakuEntry.fromJson(comment)];
}
```

The search call carries a code comment explaining why `/api/v2/search/episodes` (not `/api/v2/search/anime`) is used:

```dart
// v2 episode search avoids the anime search's 25-result cap.
// Fetch full episode lists separately; search results can truncate them.
static Future<DanmakuSearchResponse> searchAnimes(String title) async {
  final endPoint =
      ApiEndpoints.dandanAPIDomain + ApiEndpoints.dandanAPISearchEpisodes;
  final jsonData = await _client.get(
    endPoint,
    queryParameters: {'anime': title, 'v2': 'true'},
  );
  return DanmakuSearchResponse.fromJson(jsonData);
}
```

Sources: `lib/request/apis/danmaku_api.dart`, `lib/request/config/api_endpoints.dart`.

## A2. Auth / signature scheme

`lib/request/clients/danmaku_client.dart` (the only HTTP entry point; it exposes **only** a `get` method):

```dart
class DanmakuClient {
  DanmakuClient._();

  static final DanmakuClient instance = DanmakuClient._();

  Future<dynamic> get(
    String url, {
    Map<String, dynamic>? queryParameters,
    Map<String, dynamic> headers = const {},
    CancelToken? cancelToken,
  }) async {
    final timestamp = DateTime.now().millisecondsSinceEpoch ~/ 1000;
    final uri = Uri.parse(url);
    final requestHeaders = <String, dynamic>{
      'user-agent': getRandomUA(),
      'referer': '',
      'X-Auth': 1,
      'X-AppId': dandanCredentials['id'],
      'X-Timestamp': timestamp,
      'X-Signature': generateDandanSignature(uri.path, timestamp),
      ...headers,
    };
    ...
  }
}
```

Signature algorithm — `lib/utils/crypto.dart`:

```dart
String generateDandanSignature(String path, int timestamp) {
  final id = dandanCredentials['id']!;
  final value = dandanCredentials['value']!;
  final data = id + timestamp.toString() + path + value;
  final bytes = utf8.encode(data);
  final digest = sha256.convert(bytes);
  return base64Encode(digest.bytes);
}
```

- Exactly `base64(sha256(appId + timestampSeconds.toString() + uri.path + appSecret))`, matching the scheme stated in the task.
- The signed `path` is `Uri.parse(url).path` of the **full endpoint** (so for comments it includes the episode id, e.g. `/api/v2/comment/12340001`); the query string is **not** part of the signed material.
- Timestamp is **seconds**, not milliseconds (`millisecondsSinceEpoch ~/ 1000`).
- `X-Auth: 1` is also sent, plus a randomized `user-agent` (`getRandomUA()`), `referer: ''`.

**Where appId / appSecret live** — `lib/utils/dandan_credentials.dart` (full file):

```dart
// DanDanPlay API credentials for the client signature flow.
// Release/PR CI injects them via --dart-define=DANDANAPI_APPID / DANDANAPI_KEY.
const Map<String, String> dandanCredentials = {
  'id': String.fromEnvironment('DANDANAPI_APPID'),
  'value': String.fromEnvironment('DANDANAPI_KEY'),
};
```

Facts: the constants are compile-time `String.fromEnvironment` values named `DANDANAPI_APPID` (appId) and `DANDANAPI_KEY` (appSecret). They have **no default literal** in the repository. A repo-wide search for `DANDANAPI_APPID` / `DANDANAPI_KEY` returned matches only in this one file, and a scan of `.github/workflows/pr.yaml` and `.github/workflows/release.yaml` found **no** `dart-define` / `DANDAN` references — so in this public snapshot the credentials resolve to the empty string unless the builder passes them. (Distinct, separate credentials exist for the Bangumi mirror: `lib/utils/bangumi_mirror_credentials.dart` with `generateBangumiMirrorSearchSignature`, used only by `lib/request/clients/bangumi_client.dart`.)

Sources: `lib/request/clients/danmaku_client.dart`, `lib/utils/crypto.dart`, `lib/utils/dandan_credentials.dart`, `.github/workflows/pr.yaml`, `.github/workflows/release.yaml`.

## A3. Exact classes and methods for fetching danmaku

| Class | File | Fetch-related members |
|---|---|---|
| `DanmakuApi` (all `static`) | `lib/request/apis/danmaku_api.dart` | `getDanDanBangumiIDByBgmBangumiID(int) → Future<int>`, `getDanDanEpisodesByDanDanBangumiID(int) → Future<DanmakuEpisodeResponse>`, `searchAnimes(String) → Future<DanmakuSearchResponse>`, `getDanDanmaku(int bangumiID, int episode) → Future<List<DanmakuEntry>>`, `getDanDanmakuByEpisodeID(int episodeID) → Future<List<DanmakuEntry>>`, private `_getComments(String episodeID)` |
| `DanmakuClient` (singleton) | `lib/request/clients/danmaku_client.dart` | `DanmakuClient.instance`, `get(...)` |
| `PlayerDanmakuController` (= `_PlayerDanmakuController with _$PlayerDanmakuController`, MobX `Store`) | `lib/pages/player/controller/player_danmaku_controller.dart` | `fetchDanmaku(int bangumiId, String pluginName, int episode) → Future<DanmakuLoadResult>`; `beginDanmakuLoad()`; `applyDanmakuLoad(DanmakuLoadResult, {required bool enableDanmaku})`; `applyUnavailableDanmakuLoad(DanmakuLoadResult)`; `finishDanmakuLoad({bool disableDanmaku = false})`; `getDanDanmakuByEpisodeID(int episodeID) → Future<bool>`; private `_fetchCachedDanmaku(...)`, `_fetchDanDanmakuByBgmBangumiID(int bgmBangumiID, int episode)` |
| `DanmakuLoadStatus` (enum) | same file | `success`, `empty`, `failed` |
| `DanmakuLoadResult` | same file | fields `danmakus`, `bangumiID`, `status`; factories `.success`, `.failed`; getters `hasDanmakus`, `isFailed` |
| `DanmakuTimeline` | same file | `resolveSourceSecond(Duration, double)`, `staggerDelayMilliseconds({required int index, required int total})` |
| `VideoPageController` | `lib/pages/video/video_controller.dart` | `_loadPlaybackDanmaku(...)`, `cancelAutomaticDanmakuLoad()`, `commentEpisodeForSelection(...)` |

`fetchDanmaku` branches on local vs online playback:

```dart
Future<DanmakuLoadResult> fetchDanmaku(
  int bangumiId,
  String pluginName,
  int episode,
) async {
  if (isLocalPlayback()) {
    return await _fetchCachedDanmaku(bangumiId, pluginName, episode);
  }
  return await _fetchDanDanmakuByBgmBangumiID(bangumiId, episode);
}
```

The automatic trigger site is `lib/pages/video/video_controller.dart`:

```dart
final danmakuSession = _danmakuSessions.begin();
playerController.danmaku.beginDanmakuLoad();
try {
  final result = await playerController.danmaku.fetchDanmaku(
    params.bangumiId,
    params.pluginName,
    params.danmakuEpisodeNumber,
  );
```

Sources: `lib/request/apis/danmaku_api.dart`, `lib/request/clients/danmaku_client.dart`, `lib/pages/player/controller/player_danmaku_controller.dart`, `lib/pages/video/video_controller.dart`.

## A4. Episode-matching strategy (exact)

**Automatic (default) path — by Bangumi (`bgm.tv`) subject id → dandanplay `bangumiId` → `bangumiId`+`episodeNumber`:**

1. Kazumi's own `bangumiItem.id` is a bgm.tv subject id (the Bangumi API is credited in the README: *"特别感谢 [Bangumi](https://bangumi.tv/) 本项目使用了 Bangumi 开放 API 以提供番剧元数据"*, `README.md`).
2. `DanmakuApi.getDanDanBangumiIDByBgmBangumiID(bgmBangumiID)` → `GET /api/v2/bangumi/bgmtv/{bgmBangumiID}` and returns `.bangumiId`, which the model reads as `json['bangumi']['animeId']`.
3. `DanmakuApi.getDanDanmaku(danDanBangumiID, episode)` → `GET /api/v2/comment/{danDanBangumiID}{episode.padLeft(4,'0')}`.

There is **no file-name matching** anywhere in the danmaku path (no video filename is sent to dandanplay). The episode number passed in is `EpisodeRef.danmakuEpisodeNumber` (`lib/pages/video/video_controller.dart`), which is derived **from the episode display title**, not from a file name:

```dart
factory EpisodeRef.online({...}) {
  final parsedEpisodeNumber = extractEpisodeNumber(displayTitle);
  return EpisodeRef(
    ...
    sortNumber: parsedEpisodeNumber > 0 ? parsedEpisodeNumber : null,
    historyEpisodeNumber: listIndex,
    danmakuEpisodeNumber:
        parsedEpisodeNumber > 0 ? parsedEpisodeNumber : listIndex,
    ...
```

with `lib/utils/media.dart`:

```dart
int extractEpisodeNumber(String input) {
  final regExp = RegExp(r'第?(\d+)[话集]?');
  final match = regExp.firstMatch(input);
  if (match != null && match.group(1) != null) {
    return int.tryParse(match.group(1)!) ?? 0;
  }
  return 0;
}
```

Offline/downloaded playback uses the download record's `episodeNumber` for `danmakuEpisodeNumber` (`_OfflineEpisodeRef`).

**Manual path — by title search + explicit `episodeId`:** the source sheet (`showDanmakuSourceSheet`) does keyword → `DanmakuApi.searchAnimes(keyword)` (`/api/v2/search/episodes`) → user picks `DanmakuSearchAnime` → `DanmakuApi.getDanDanEpisodesByDanDanBangumiID(anime.animeId)` (`/api/v2/bangumi/{animeId}`) → user picks a `DanmakuEpisode` → `PlayerDanmakuController.getDanDanmakuByEpisodeID(episode.episodeId)` (`/api/v2/comment/{episodeId}`). The initial keyword is the Bangumi title:

```dart
unawaited(showDanmakuSourceSheet(
  context,
  bangumiId: videoPageController.bangumiItem.id,
  initialKeyword: videoPageController.title,
  danmakuController: playerController.danmaku,
  onBeforeApply: videoPageController.cancelAutomaticDanmakuLoad,
));
```

(`lib/pages/player/player_item.dart`). So: **no** `bangumiId + episodeNumber` search-API query, **no** filename matching; automatic = bgm-id→danDan-id + zero-padded episode number, manual = `/api/v2/search/episodes` + `episodeId`.

Sources: `lib/pages/player/controller/player_danmaku_controller.dart`, `lib/request/apis/danmaku_api.dart`, `lib/pages/video/video_controller.dart`, `lib/utils/media.dart`, `lib/pages/player/danmaku_source_sheet.dart`, `lib/pages/player/player_item.dart`.

## A5. Comment model and JSON fields (exact field names)

The one and only danmaku comment class is `DanmakuEntry` in `lib/modules/danmaku/danmaku_module.dart` (whole file quoted):

```dart
import 'package:flutter/material.dart';
import 'package:kazumi/utils/danmaku.dart';

class DanmakuEntry {
  // 弹幕内容
  String message;
  // 弹幕时间
  double time;
  // 弹幕类型 (1-普通弹幕，4-底部弹幕，5-顶部弹幕)
  int type;
  // 弹幕颜色
  Color color;
  // 弹幕来源 ([BiliBili], [Gamer])
  String source;

  DanmakuEntry(
      {required this.message,
      required this.time,
      required this.type,
      required this.color,
      required this.source});

  factory DanmakuEntry.fromJson(Map<String, dynamic> json) {
    String messageValue = json['m'];
    List<String> parts = json['p'].split(',');
    double timeValue = double.parse(parts[0]);
    int typeValue = int.parse(parts[1]);
    Color color = generateDanmakuColor(int.parse(parts[2]));
    String sourceValue = parts[3];
    return DanmakuEntry(
        time: timeValue,
        message: messageValue,
        type: typeValue,
        color: color,
        source: sourceValue);
  }

  /// 序列化为 JSON 格式 (与 fromJson 格式一致)
  Map<String, dynamic> toJson() {
    // 只存储 RGB 部分 (与 DanDanPlay API 格式一致)
    final colorValue = ((color.r * 255).toInt() << 16) |
        ((color.g * 255).toInt() << 8) |
        (color.b * 255).toInt();
    return {
      'm': message,
      'p': '$time,$type,$colorValue,$source',
    };
  }
}
```

Field-by-field facts:

- JSON keys consumed: `m` (message text) and `p` (comma-separated parameter string). **There is no `cid` field anywhere** — a repo-wide search for `'cid'`/`"cid"` returned no matches, and no `u` (user) field is read either.
- `p` layout: `p[0]` = time in seconds (parsed as `double`), `p[1]` = type/mode (`int`), `p[2]` = RGB colour integer, `p[3]` = source string. This is **4** parts, not the 8-part dandanplay `p` (`time,mode,size,color,timestamp,pool,userhash,rowid`).
- Type/mode semantics are documented in-code: `1` = 普通弹幕 (scroll), `4` = 底部弹幕 (bottom), `5` = 顶部弹幕 (top).
- Colour decoding — `lib/utils/danmaku.dart`: `generateDanmakuColor(int colorValue)` → `Color.fromARGB(255, (v>>16)&0xFF, (v>>8)&0xFF, v&0xFF)`.
- Source values seen in code: `'BiliBili'`, `'Gamer'`, and "anything else" treated as DanDan (`_danmakuDanDanSource` branch in `player_item.dart`).
- **No XML parsing exists.** The whole `lib/` tree contains exactly one XML/XML-namespace string, the inline `danmakuOnSvg` constant in `lib/utils/constants.dart`; a search for `xml|Xml` across all Dart files returned only that. Comment parsing is purely `jsonData['comments']` → `DanmakuEntry.fromJson`.

Episode-metadata models — `lib/modules/danmaku/danmaku_episode_response.dart`:

```dart
class DanmakuEpisode {
  int episodeId;
  String episodeTitle;
  ...
  factory DanmakuEpisode.fromJson(Map<String, dynamic> json) {
    return DanmakuEpisode(
      episodeId: json['episodeId'],
      episodeTitle: json['episodeTitle'],
    );
  }
}

class DanmakuEpisodeResponse {
  int bangumiId;
  List<DanmakuEpisode> episodes;
  int errorCode;
  bool success;
  String errorMessage;
  ...
  factory DanmakuEpisodeResponse.fromJson(Map<String, dynamic> json) {
    var list = json['bangumi']['episodes'] as List;
    List<DanmakuEpisode> episodeList =
        list.map((i) => DanmakuEpisode.fromJson(i)).toList();

    return DanmakuEpisodeResponse(
      bangumiId: json['bangumi']['animeId'],
      episodes: episodeList,
      errorCode: json['errorCode'],
      success: json['success'],
      errorMessage: json['errorMessage'],
    );
  }
```

Search-result model — `lib/modules/danmaku/danmaku_search_response.dart`:

```dart
/// Response of `/api/v2/search/episodes`.
///
/// Entries carry no cover, rating or air date, unlike `/api/v2/search/anime`.
class DanmakuSearchAnime {
  final int animeId;
  final String animeTitle;
  final String typeDescription;

  factory DanmakuSearchAnime.fromJson(Map<String, dynamic> json) {
    return DanmakuSearchAnime(
      animeId: json['animeId'],
      animeTitle: json['animeTitle'],
      typeDescription: json['typeDescription'] ?? '',
    );
  }
}

class DanmakuSearchResponse {
  final List<DanmakuSearchAnime> animes;
  /// Result set was truncated; the user should narrow the keyword.
  final bool hasMore;
  ...
  factory DanmakuSearchResponse.fromJson(Map<String, dynamic> json) {
    var list = json['animes'] as List;
    return DanmakuSearchResponse(
      animes: list.map((i) => DanmakuSearchAnime.fromJson(i)).toList(),
      hasMore: json['hasMore'] ?? false,
    );
  }
}
```

(`hasMore` drives a "仅显示部分结果" banner in `danmaku_source_sheet.dart`.)

Sources: `lib/modules/danmaku/danmaku_module.dart`, `lib/modules/danmaku/danmaku_episode_response.dart`, `lib/modules/danmaku/danmaku_search_response.dart`, `lib/utils/danmaku.dart`, `lib/utils/constants.dart`.

## A6. Rendering — the `canvas_danmaku` widget and exact class names

- Dependency: `pubspec.yaml` line 55 → `canvas_danmaku: ^0.3.3`; resolved in `pubspec.lock` → `canvas_danmaku` `version: "0.3.3"`, `sha256: "1ff071d027b1cf3320d4b58ff1aee6811de51ce39b5b5773c2e0d7693e02d401"`.
- Import in the player: `import 'package:canvas_danmaku/canvas_danmaku.dart';` (`lib/pages/player/player_item.dart`) and, for models, `import 'package:canvas_danmaku/models/danmaku_content_item.dart';` (`lib/pages/video/video_page.dart`, `lib/pages/player/player_item_panel.dart`).
- The controller field is typed through the aliased import in `player_danmaku_controller.dart`: `import 'package:canvas_danmaku/canvas_danmaku.dart' as canvas;` then `late canvas.DanmakuController canvasController;`.
- The render widget is `DanmakuScreen`, fed by a `DanmakuOption`, inside `Positioned.fill` in `lib/pages/player/player_item.dart`:

```dart
Positioned.fill(
  child: DanmakuScreen(
    key: _danmuKey,
    createdController: (DanmakuController e) {
      playerController.danmaku.canvasController = e;
      WidgetsBinding.instance.addPostFrameCallback((_) {
        playerController.updateDanmakuSpeed();
      });
    },
    option: DanmakuOption(
      hideTop: _hideTop,
      hideScroll: _hideScroll,
      hideBottom: _hideBottom,
      area: _danmakuArea,
      opacity: _opacity,
      fontSize: _fontSize,
      // Speed scaling is applied after canvas creation.
      duration: _danmakuDuration,
      lineHeight: _danmakuLineHeight,
      strokeWidth: _border ? _danmakuBorderSize : 0.0,
      fontWeight: _danmakuFontWeight,
      massiveMode: _massiveMode,
      fontFamily: _danmakuUseSystemFont
          ? null
          : customAppFontFamily,
    ),
  ),
),
```

- Emission: a 1-second `Timer.periodic` in `getPlayerTimer()` calls `_emitDanmakusForCurrentPosition()`, which looks up `playerController.danmaku.danmakusForPlaybackPosition(currentPosition)` (a `Map<int, List<DanmakuEntry>> danDanmakus` bucketed by integer second), staggers same-second entries, and then:

```dart
playerController.danmaku.canvasController.addDanmaku(
  DanmakuContentItem(
    danmaku.message,
    color: color,
    type: _danmakuItemType(danmaku),
  ),
);
```

- Type mapping verbatim (`_danmakuItemType`): `type == 4 → DanmakuItemType.bottom`, `type == 5 → DanmakuItemType.top`, otherwise `DanmakuItemType.scroll`.
- Speed coupling: `updateDanmakuSpeed(double playerSpeed)` sets `duration = followSpeed ? (baseDuration / playerSpeed) : baseDuration` via `canvasController.updateOption(canvasController.option.copyWith(duration: duration))`.
- `clearAndInvalidateScheduledDanmakus()` calls `canvasController.clear()` and bumps a generation counter; `onWindowRestore()` also calls `playerController.danmaku.canvasController.clear()`.
- All appearance values are persisted through `GStorage.getSetting(SettingsKeys.danmaku*)`: `danmakuBorder`, `danmakuBorderSize`, `danmakuOpacity`, `danmakuFontSize`, `danmakuArea`, `danmakuTop`, `danmakuBottom`, `danmakuScroll`, `danmakuMassive`, `danmakuColor`, `danmakuDuration`, `danmakuLineHeight`, `danmakuFontWeight`, `danmakuFollowSpeed`, `danmakuTimeOffset`, `danmakuEnabledByDefault`, `danmakuDeduplication`, `danmakuChConvert`, `danmakuEnhance`, `useSystemFont` (`lib/services/storage/settings_keys.dart`).
- UI for these is `lib/pages/settings/danmaku/danmaku_settings_sheet.dart` (`showDanmakuSettingsSheet({required BuildContext context, required DanmakuController danmakuController, required VoidCallback onUpdateDanmakuSpeed, VoidCallback? onTimelineOffsetChanged})`, two tabs `['外观', '播放']`, sliders 字体大小 10–48, 不透明度 0.1–1, 显示区域 0–1 (8 divisions), 行间距 0–3 (30 divisions), 停留时间 2–16 s, toggle 跟随视频倍速, plus entries 简繁转换 / 时间校准 / 屏蔽规则).

Sources: `lib/pages/player/player_item.dart`, `lib/pages/player/controller/player_danmaku_controller.dart`, `lib/pages/settings/danmaku/danmaku_settings_sheet.dart`, `lib/services/storage/settings_keys.dart`, `pubspec.yaml`, `pubspec.lock`.

## A7. Keyword / shield / blocking rules (屏蔽词, regex, sources, types)

**Rule storage and validation.** `lib/modules/danmaku/danmaku_shield_rule.dart` (whole file):

```dart
enum DanmakuShieldRuleError { empty, tooLong }

abstract final class DanmakuShieldRule {
  static const maxLength = 64;

  static DanmakuShieldRuleError? validate(String rule) {
    if (rule.isEmpty) return DanmakuShieldRuleError.empty;
    if (rule.length > maxLength) return DanmakuShieldRuleError.tooLong;
    return null;
  }
}
```

Rules live in the Hive box `GStorage.shieldList` (`lib/services/storage/storage.dart`: `static late Box<String> shieldList;` … `shieldList = await _openBoxSafe<String>('shieldList');`).

**Matching algorithm** — `lib/pages/my/my_controller.dart`:

```dart
bool isDanmakuBlocked(String? danmaku) {
  if (danmaku == null || danmaku.isEmpty) return false;
  for (String item in shieldList) {
    if (item.isEmpty) continue;
    if (item.startsWith('/') && item.endsWith('/')) {
      if (item.length <= 2) continue;
      String pattern = item.substring(1, item.length - 1);
      try {
        if (RegExp(pattern).hasMatch(danmaku)) return true;
      } catch (_) {
        KazumiLogger()
            .e('Danmaku: invalid danmaku shield regex pattern: $pattern');
        continue;
      }
    } else {
      if (danmaku.contains(item)) return true;
    }
  }
  return false;
}
```

So: a rule wrapped in `/.../` is a Dart `RegExp` (case-sensitive, first match wins, invalid patterns are logged and skipped); any other rule is a case-sensitive substring test on the comment text. This is invoked immediately before each comment is drawn (`player_item.dart` line 1163: `... || myController.isDanmakuBlocked(danmaku.message)) { return; }`).

**Type/mode shielding** is not a shield-rule concern but `DanmakuOption` flags, toggled by chips 滚动 / 顶部 / 底部 (`hideScroll`/`hideTop`/`hideBottom`).

**Source shielding** — `lib/pages/player/player_item.dart`:

```dart
bool _isDanmakuSourceEnabled(DanmakuEntry danmaku) {
  if (!_danmakuBiliBiliSource && danmaku.source.contains('BiliBili')) {
    return false;
  }
  if (!_danmakuGamerSource && danmaku.source.contains('Gamer')) {
    return false;
  }
  if (!_danmakuDanDanSource &&
      !(danmaku.source.contains('BiliBili') ||
          danmaku.source.contains('Gamer'))) {
    return false;
  }
  return true;
}
```

backed by settings `danmakuBiliBiliSource`, `danmakuGamerSource`, `danmakuDanDanSource`.

**User blocking: 未查到.** There is no per-user/per-uid blocking: the comment model has no user field, `isDanmakuBlocked` takes only the message text, and no `uid`/`userId` handling exists in the danmaku code.

**Editor UI** — `lib/pages/settings/danmaku/danmaku_shield_editor.dart`: hint `'关键词或 /正则表达式/'`, helper text `'包含关键词的弹幕会被隐藏。用 / / 包裹正则表达式。'`, list rows labelled `'正则表达式'` when wrapped in `/`; add/remove go through `MyController.addShieldList` / `removeShieldList` → `IDanmakuShieldRepository.setRule(rule, {required bool deleted})`.

**Cloud sync of shield rules (WebDAV).** `lib/modules/danmaku/danmaku_shield_sync.dart` defines `DanmakuShieldSyncEntry` (`rule`, `updatedAt`, `deviceId`, `deleted`) with last-write-wins via `updatedAt` → `deviceId` → `deleted`, and `DanmakuShieldSyncState` with `encode()`/`decode()` producing:

```dart
return jsonEncode({
  'version': 1,
  'entries': sorted.map((entry) => entry._toJson()).toList(),
});
```

`lib/repositories/danmaku_shield_repository.dart` (`IDanmakuShieldRepository`, `DanmakuShieldRepository`, `enum DanmakuShieldChange { localEdit, restore }`) projects the merged state back into `GStorage.shieldList` and persists metadata to `SettingsKeys.danmakuShieldSyncState`, using a random 16-byte hex `deviceId`. `lib/services/sync/danmaku_shield_sync_service.dart` debounces local edits by 500 ms (`Timer(const Duration(milliseconds: 500), ...)`) and `lib/services/sync/webdav.dart` (`syncDanmakuShield(...)`) writes per-device files to `'$_syncRootPath/danmakuShield'` named `'$deviceId.json'`, matching only `RegExp(r'^[0-9a-f]{32}\.json$')`, gated by `isDanmakuShieldSyncEnabled` (`webDavEnable && webDavEnableDanmakuShield`).

**Deduplication / merge** — `lib/utils/danmaku.dart`, `mergeDuplicateDanmakus(List<DanmakuEntry>, {double timeWindowSeconds = 0})`: normalizes text (full-width `0x3000`→space, `0xFF01–0xFF5E`→ASCII, strip `\s+`, strip everything outside `[\w\u4e00-\u9fff\u3040-\u309F\u30A0-\u30FF\u31F0-\u31FF\uFF65-\uFF9F]`, collapse `(.)\1{2,}` to 3 repeats), groups, and when a group has ≥2 entries emits

```dart
DanmakuEntry(
  message: '${group.first.message} x${group.length}',
  time: group.first.time,
  type: 5,
  color: generateDanmakuColor(0xFFFFFF),
  source: group.first.source,
),
```

Invoked from `PlayerDanmakuController.addDanmakus` only when `SettingsKeys.danmakuDeduplication` is on, with `timeWindowSeconds: 5`.

Sources: `lib/modules/danmaku/danmaku_shield_rule.dart`, `lib/modules/danmaku/danmaku_shield_sync.dart`, `lib/repositories/danmaku_shield_repository.dart`, `lib/services/sync/danmaku_shield_sync_service.dart`, `lib/services/sync/webdav.dart`, `lib/pages/my/my_controller.dart`, `lib/pages/settings/danmaku/danmaku_shield_editor.dart`, `lib/pages/settings/danmaku/danmaku_shield_settings_sheet.dart`, `lib/utils/danmaku.dart`, `lib/services/storage/storage.dart`, `lib/services/storage/settings_keys.dart`, `lib/pages/player/player_item.dart`.

## A8. Chinese conversion (simplified/traditional)

`lib/modules/danmaku/danmaku_ch_convert.dart` (whole file) — note it is **a parameter enum only; the actual conversion is server-side**:

```dart
// Persisted values match dandanplay's chConvert parameter.
enum DanmakuChConvert {
  none(0),
  simplified(1),
  traditional(2);

  const DanmakuChConvert(this.value);

  final int value;

  static DanmakuChConvert fromValue(int value) => switch (value) {
    1 => simplified,
    2 => traditional,
    _ => none,
  };
}
```

The value is persisted under `SettingsKeys.danmakuChConvert` (`SettingKey<int>('danmakuChConvert', ...)`) and sent as the `chConvert` query parameter on every `/api/v2/comment/{episodeId}` request (`danmaku_api.dart`, quoted in A1). UI is `lib/pages/settings/danmaku/danmaku_ch_convert_tile.dart`: `SettingsDropdownTile<DanmakuChConvert>` titled `'简繁转换'`, description `'下次联网加载弹幕时生效，已缓存弹幕保留下载时的文字'`, options `不转换` / `转为简体` / `转为繁体`. There is **no local OpenCC-style conversion table** in the repo.

Sources: `lib/modules/danmaku/danmaku_ch_convert.dart`, `lib/request/apis/danmaku_api.dart`, `lib/pages/settings/danmaku/danmaku_ch_convert_tile.dart`, `lib/services/storage/settings_keys.dart`.

## A9. Danmaku send / post support

**There is no upload API.** `DanmakuClient` exposes only `get` (no `post`), and no dandanplay send endpoint is defined in `ApiEndpoints`.

The UI does exist — `lib/pages/player/danmaku_destination_sheet.dart` offers two destinations:

```dart
enum DanmakuDestination {
  chatRoom,
  remoteDanmaku,
}
```

with tiles `'一起看聊天室'` (`DanmakuDestination.chatRoom`) and `'视频弹幕'` / `'发送至远程弹幕库'` (`DanmakuDestination.remoteDanmaku`).

`lib/pages/player/player_item_panel.dart`, `_submitDanmakuText(String message)`, validates and then:

```dart
if (playerController.danmaku.danDanmakus.isEmpty) {
  KazumiDialog.showToast(message: '当前剧集不支持弹幕发送的说');
  return;
}
if (message.length > 100) {
  KazumiDialog.showToast(message: '弹幕内容过长');
  return;
}

if (destination == DanmakuDestination.chatRoom) {
  if (playerController.syncplay.syncplayRoom.isEmpty) {
    KazumiDialog.showToast(message: '你还没有加入一起看，无法发送聊天室弹幕');
    return;
  }

  final sender =
      playerController.syncplay.syncplayController?.username ?? '我';
  playerController.danmaku.canvasController.addDanmaku(
    DanmakuContentItem(
      '$sender：$message',
      color: Colors.orange,
      isColorful: true,
      type: DanmakuItemType.bottom,
      extra: DateTime.now().millisecondsSinceEpoch,
    ),
  );
  unawaited(playerController.sendSyncPlayChatMessage(message));
} else {
  // This provider has no send API; display a local echo.
  playerController.danmaku.canvasController
      .addDanmaku(DanmakuContentItem(message, selfSend: true));
}
```

So: the **chat-room** destination really transmits, but only over the SyncPlay "一起看" channel (`sendSyncPlayChatMessage`); the **"视频弹幕 / remote remote-danmaku-library"** destination is an explicit code-commented **local echo only** (`selfSend: true`) — nothing is posted to dandanplay. Limits: non-empty, ≤100 characters.

Sources: `lib/pages/player/danmaku_destination_sheet.dart`, `lib/pages/player/player_item_panel.dart`, `lib/request/clients/danmaku_client.dart`, `lib/request/config/api_endpoints.dart`.

## A10. Local danmaku caching

`lib/pages/download/download_controller.dart`:

```dart
/// 弹幕文件路径
String _danmakuFilePath(String downloadDirectory) {
  return '$downloadDirectory/danmaku.json';
}
```

```dart
/// 写入弹幕数据到文件 (新格式，包含 danDanBangumiID)
Future<void> _writeDanmakuToFile(String downloadDirectory,
    List<DanmakuEntry> danmakus, int danDanBangumiID) async {
  ...
  final wrapper = {
    'danDanBangumiID': danDanBangumiID,
    'danmakus': danmakus.map((d) => d.toJson()).toList(),
  };
  await file.writeAsString(jsonEncode(wrapper));
}
```

`getCachedDanmakus(int bangumiId, String pluginName, int episodeNumber)` / `updateCachedDanmakus(...)` back `PlayerDanmakuController._fetchCachedDanmaku`, which falls back to an online fetch when the cache is missing. `_readDanmakuFromFile` accepts both the new wrapper `{danDanBangumiID, danmakus}` and a legacy bare array. This is the only XML-free, JSON-only persistence path.

Sources: `lib/pages/download/download_controller.dart`, `lib/pages/player/controller/player_danmaku_controller.dart`.

## A11. Timeline offset (time calibration)

`lib/pages/player/controller/player_danmaku_controller.dart`:

```dart
static int? resolveSourceSecond(
  Duration playbackPosition,
  double timelineOffsetSeconds,
) {
  final sourceMilliseconds = playbackPosition.inMilliseconds -
      (timelineOffsetSeconds * 1000).round();
  if (sourceMilliseconds < 0) {
    return null;
  }
  return Duration(milliseconds: sourceMilliseconds).inSeconds;
}
```

Persisted as `SettingsKeys.danmakuTimeOffset` (double, seconds), edited in `lib/pages/settings/danmaku/danmaku_time_offset_sheet.dart` with `_minDanmakuTimeOffset = -180`, `_maxDanmakuTimeOffset = 180`, `_danmakuTimeOffsetDivisions = 36`, and formatted as `'无偏移'` or `'延后 HH:MM'` / `'提前 HH:MM'`. Stagger for same-second comments: `index * 1000 ~/ total` ms.

Sources: `lib/pages/player/controller/player_danmaku_controller.dart`, `lib/pages/settings/danmaku/danmaku_time_offset_sheet.dart`.

---

# TOPIC B — Update detection / notifications / timetable

## B1. A timetable exists — but it is a weekly Bangumi calendar, not a "new episode" tracker

There **is** an airing timetable. Route `/timeline` (`lib/pages/router.dart`: `MenuRouteItem(path: '/timeline')`), registered in `lib/pages/index_module.dart` (`..addSingleton<TimelineController>(TimelineController.new)`, `..module(timelineModule)`), localized as `'/tab/timeline/': '时间表'` (`lib/pages/settings/interface_settings.dart`).

`lib/pages/timeline/timeline_page.dart` — a seven-day, weekday-indexed view defaulting to today:

```dart
return DefaultTabController(
  length: 7,
  initialIndex: DateTime.now().weekday - 1,
  animationDuration:
      MediaQuery.disableAnimationsOf(context) ? Duration.zero : null,
  child: _buildContent(context),
);
```

with `initState` calling `_controller.loadSeason(_controller.selectedDate)` when the calendar is empty.

`lib/pages/timeline/timeline_controller.dart` — state and loading:

```dart
@observable
ObservableList<List<BangumiItem>> bangumiCalendar =
    ObservableList<List<BangumiItem>>();
...
Future<void> loadSeason(DateTime date) async {
  ...
  if (isSameSeason(date, DateTime.now())) {
    bangumiCalendar.addAll(await BangumiApi.getCalendar());
    isTimeOut = bangumiCalendar.isEmpty;
  } else {
    await _getSchedulesBySeason(date);
    isTimeOut = bangumiCalendar.every((day) => day.isEmpty);
  }
  if (!isTimeOut) changeSort(_sort);
```

Sorting: `enum TimelineSort { popularity, rating, defaultOrder }` → by `votes`, `ratingScore`, `id`. Filters (persisted via `ICollectRepository`): `notShowAbandonedBangumis`, `notShowWatchedBangumis`, `onlyShowWatchingBangumis` (`SettingsKeys.timelineNotShowAbandonedBangumis` / `timelineNotShowWatchedBangumis` / `timelineOnlyShowWatchingBangumis`).

### Bangumi API base URLs and calendar endpoints (exact)

`lib/request/config/api_endpoints.dart`:

```dart
/// bangumi API
static const String bangumiAPIDomain = 'https://api.bgm.tv';
...
/// Bangumi Next API Domain
static const String bangumiAPINextDomain = 'https://next.bgm.tv';
...
/// 每日放送
static const String bangumiCalendar = '/p1/calendar';
...
/// Kazumi Bangumi 镜像季节时间表
static const String bangumiMirrorSeasonCalendar =
    '/kazumi/v1/calendar/season';
```

`lib/request/apis/bangumi_api.dart`:

- `getCalendar()` → `GET https://next.bgm.tv/p1/calendar`, then reads `jsonData['$i']` for `i = 1..7` and `BangumiItem.fromJson(jsonItem['subject'])`.
- `getCalendarBySearch(List<String> dateRange, int limit, int offset)` → `POST https://api.bgm.tv/v0/search/subjects?limit={0}&offset={1}` with body:

```dart
var params = <String, dynamic>{
  "keyword": "",
  "sort": "rank",
  "filter": {
    "type": [2],
    "tag": ["日本"],
    "air_date": [">=${dateRange[0]}", "<${dateRange[1]}"],
    "rank": [">0", "<=99999"],
    "nsfw": true
  }
};
```

  then buckets results by `bangumiItem.airWeekday` for weekdays `1..7`.
- `getBangumiMirrorSeasonCalendar(List<String> dateRange)` → `GET https://api.kazumi.fyi/kazumi/v1/calendar/season?start=..&end=..` (`bangumiMirrorDomain = 'https://api.kazumi.fyi'`), used only when `BangumiAcceleration.current == BangumiAcceleration.mirror`.

Season math — `lib/utils/anime_season.dart`: `AnimeSeason(DateTime).toSeasonStartAndEnd()` (returns previous-season start + season end, with the in-code rationale *"why -1? because the air date is the launch date of the anime, it is usually a few days before the start of the season"*), `AnimeSeason.toString()` → `'${year}年${冬季|春季|夏季|秋季}新番'`, plus `getSeasonStringByMonth(int)` and `isSameSeason(DateTime, DateTime)`.

Episode-count display — `lib/bean/card/bangumi_timeline_card.dart` (`BangumiTimelineCard`, `static double heightFor(TextScaler, {bool compact = false})`, `_buildCover` / `_buildDetails` / `_buildRating`):

```dart
static final _episodePattern =
    RegExp(r'^\s*([1-9]\d*)\s*[话話集](?=\s*(?:[/／]|$))');
...
String _supportingText(String title) {
  // Calendar entries expose episode counts through info.
  final episodes = _episodePattern.firstMatch(bangumiItem.info);
```

i.e. the card renders an episode count scraped from the calendar payload's `info` string. It is **display-only**; nothing compares it against previously seen values.

`lib/utils/date_time.dart` is formatting only: `formatTimestampToRelativeTime(int)` (`'N年前' | 'N个月前' | 'N天前' | 'N小时前' | 'N分钟前' | '刚刚'`), `dateFormat(int, {String formatType = 'list'})`, `dateStringToWeekday(String)`, `formatDate(String)`, `formatDateTime(DateTime)` — **no** scheduling, comparison, or notification logic.

`lib/utils/constants.dart` contains no timetable/notification constants (it holds UI radii, the randomized `userAgentsList`, `acceptLanguageList`, `bangumiHTTPHeader`, decoder/renderer maps, Anime4K shader lists, `defaultPlaySpeedList`, the `danmakuOnSvg` string, `defaultAnimeTags`, default shortcuts and their Chinese labels).

## B2. Does Kazumi detect new episodes / "today's updates"? — **未查到**

- There is **no** "new episode" detection logic. A repo-wide search for `更新提醒|番剧更新|订阅更新` and for `notification|Notification` across `lib/` found only: Flutter's `NotificationListener` widgets, the **download** foreground notification, the audio_service media notification, and a system-proxy change notifier. Nothing about airing episodes.
- The README feature checklist itself marks it unimplemented (`README.md` line 67):

```
- [X]  番剧下载
- [ ]  番剧更新提醒
- [ ]  还有更多 (/・ω・＼)
```

  (Contrast: line 47 `- [X]  番剧时间表` is checked, line 56 `- [X]  番剧弹幕` is checked.) So the README's unchecked `番剧更新提醒` is consistent with the code: the timetable exists, the reminder does not.
- No comparison of "episode counts over time" exists: `BangumiTimelineCard` only renders the count; `TimelineController` only sorts/filters; `ICollectRepository` exposes only the three timeline *filter* booleans plus Bangumi-collection sync (`getBangumiIdsByType`). No last-seen-episode, no airdate diffing.

## B3. Scheduled tasks / local notifications / background fetch

| Sought | Result | Evidence |
|---|---|---|
| `flutter_local_notifications` | **未查到** | absent from `pubspec.yaml` dependencies (full list read) |
| `workmanager` | **未查到** | absent from `pubspec.yaml` |
| `background_fetch` / `flutter_background_fetch` | **未查到** | absent from `pubspec.yaml` |
| `flutter_foreground_task` | present, **downloads only** | `pubspec.yaml` line 71 `flutter_foreground_task: ^9.0.0`; the only importer is `lib/services/download/background_download_service.dart` (`import 'package:flutter_foreground_task/flutter_foreground_task.dart';`, `FlutterForegroundTask.init(...)`, `startService(...)`, `notificationTitle: '正在下载'`) |
| Any periodic timer / background fetch for episodes | **未查到** | repo-wide `Timer.periodic` has exactly 3 hits, all unrelated: `lib/webview/video/impl/video_webview_impl.dart` (`Duration(seconds: 1)` video parser), `lib/pages/player/player_item.dart` (`Duration(seconds: 1)` player tick), `lib/services/player/timed_shutdown_service.dart` (`Duration(seconds: 1)` sleep timer) |
| Scheduled*update* work | app self-update only | see B4 |

The only notification machinery in the app is:
- `lib/services/download/background_download_service.dart` — a `flutter_foreground_task` foreground service for background downloads, with `AndroidNotificationOptions(channelImportance: NotificationChannelImportance.LOW, priority: NotificationPriority.LOW)`, `IOSNotificationOptions(showNotification: false)`, buttons `NotificationButton(id: 'pause_all', text: '暂停全部')`, and `FlutterForegroundTask.sendDataToMain({'action': 'navigate_to_download'})` + `launchApp()`. Its permission flow is wired in `lib/pages/init_page.dart` (`_setupBackgroundDownloadNavigation()` → `backgroundService.onNotificationPermissionRequired` showing the dialog `'需要通知权限'` with the text `'开启通知权限后，可以在后台下载时显示进度，并防止系统终止下载任务。'`).
- `lib/services/player/audio_controller.dart` — audio_service media notification (`androidNotificationChannelId: 'io.github.Predidit.Kazumi.channel.audio'`, `androidNotificationChannelName: 'Kazumi Playback'`).

At startup, `lib/pages/init_page.dart` (`_initializeApp()`) performs: danmaku shield sync start, storage migration, shader load, shield-list load, WebDAV init, Bangumi sync ping, download init, X11 check, Windows shortcut dialog, plugin init, and then a **single** app-update check:

```dart
unawaited(runStartupUpdateCheck(
  isEnabled: () => GStorage.getSetting(SettingsKeys.autoUpdate),
  checkForUpdate: () async {
    await updateController.checkUpdate(type: 'auto');
  },
));
```

There is **no** anime/episode check at startup (or anywhere else).

## B4. What "update" means in this repo: the **app** self-updater (distinct from episode updates)

- `lib/services/update/startup_update_check.dart` (whole file): `const startupUpdateCheckDelay = Duration(milliseconds: 500);` and `runStartupUpdateCheck({required bool Function() isEnabled, required Future<void> Function() checkForUpdate, Duration delay = startupUpdateCheckDelay})`.
- `lib/services/update/auto_updater.dart`: `class AutoUpdater` (singleton, `AutoUpdater._internal()`) with `Future<UpdateInfo?> checkForUpdates()`, `autoCheckForUpdates()`, `manualCheckForUpdates()`; `enum InstallationType { windowsMsix, windowsPortable, linuxDeb, linuxTar, macosDmg, androidApk, ios, unknown }`; version compare via `needUpdate(currentVersion, remoteVersion)`; SHA-256 verification of the downloaded artifact (`getUpdateFileHashFromAsset`, `calculateFileHash`). Its data source is the Kazumi mirror:

```dart
Future<Map<String, dynamic>> _latestRelease() async {
  final raw = await _downloadClient.getPlain(ApiEndpoints.latestAppMirror);
  ...
}
```

  with `latestAppMirror = 'https://api.kazumi.fyi/kazumi/v1/app/latest'` and `latestApp = 'https://api.github.com/repos/Predidit/Kazumi/releases/latest'` (`api_endpoints.dart`). Gated by `SettingsKeys.autoUpdate`; `MyController.checkUpdate({String type = 'manual'})` is the UI entry point (`lib/pages/my/my_controller.dart`).
- `upgrader: ^12.3.0` is listed in `pubspec.yaml` but is not referenced by any Dart file in `lib/` (grep for `upgrader|Upgrader` matched only `AutoUpdater`-related code).

**This is application-version updating, not anime episode updating. It is the only "update detection" in the codebase.**

## B5. Files the task named, and what they actually contain

| Path | Exists? | Content relevant to Topic B |
|---|---|---|
| `lib/bean/card/bangumi_timeline_card.dart` | yes | `BangumiTimelineCard` widget; `_episodePattern` scrapes `N 话/話/集` from `bangumiItem.info` for display |
| `lib/utils/anime_season.dart` | yes | `AnimeSeason`, `toSeasonStartAndEnd()`, `getSeasonStringByMonth`, `isSameSeason` |
| `lib/utils/date_time.dart` | yes | relative/absolute date formatting only |
| `lib/pages/index_module.dart` | yes | DI + routing; registers `TimelineController`, `timelineModule`, `DanmakuShieldSyncService` |
| `lib/pages/index_page.dart` | yes (18 lines) | `class IndexPage extends StatefulWidget` → `ScaffoldMenu(location: widget.location)`; nothing else |
| `lib/utils/constants.dart` | yes | theme/UA/decoder/shader/shortcut constants; no timetable or notification logic |
| `lib/services/` | yes | 30 subdirectories' worth of files under `download/`, `logging/`, `network/`, `platform/`, `player/`, `plugin/`, `shaders/`, `storage/`, `sync/`, `update/`, `video_source/` — no episode-notification or scheduling service exists |
| `lib/repositories/` | yes | `collect_crud_repository.dart`, `collect_repository.dart`, `danmaku_shield_repository.dart`, `download_repository.dart`, `history_repository.dart`, `search_history_repository.dart` — none deals with episode-update reminders |

---

# Summary of the two topics

**Topic A.** Kazumi talks to `https://api.dandanplay.net` through a single `DanmakuClient.get` that signs requests with `X-AppId` / `X-Timestamp` / `X-Signature = base64(sha256(appId + t + uri.path + appSecret))` (plus `X-Auth: 1`), using `String.fromEnvironment('DANDANAPI_APPID')` / `('DANDANAPI_KEY')` from `lib/utils/dandan_credentials.dart` (no in-repo defaults). Endpoints: `/api/v2/bangumi/bgmtv/{bgmId}`, `/api/v2/bangumi/{danDanId}`, `/api/v2/search/episodes?anime=&v2=true`, `/api/v2/comment/{episodeId}?withRelated=true&chConvert=N`. Automatic matching is bgm subject id → dandanplay `bangumiId` → comment id `bangumiId + episode.padLeft(4,'0')`; manual matching is title search → explicit `episodeId`. Comments are JSON-only (`m`, `p` with `p = time,type,color,source`; **no `cid`, no XML**), rendered via `canvas_danmaku: ^0.3.3`'s `DanmakuScreen` + `DanmakuController` + `DanmakuOption` + `DanmakuContentItem`. Shielding = substring keywords or `/regex/` in the Hive `shieldList` box (max 64 chars), plus type (scroll/top/bottom) and source (BiliBili/Gamer/DanDan) toggles; no per-user blocking. Simplified/traditional conversion is delegated to the server via the `chConvert` enum/param. Sending exists in UI only: chat-room messages go over SyncPlay, the "video danmaku" option is an explicit local echo (`// This provider has no send API; display a local echo.`).

**Topic B.** A seven-day Bangumi airing timetable exists (`/timeline`, `TimelineController`, `TimelinePage`, `BangumiTimelineCard`) backed by `https://next.bgm.tv/p1/calendar`, `https://api.bgm.tv/v0/search/subjects`, and mirror `https://api.kazumi.fyi/kazumi/v1/calendar/season`. **New-episode / "today's updates" detection: 未查到. Local notifications for episodes: 未查到. `workmanager` / `background_fetch` / `flutter_local_notifications`: 未查到.** The only background service is `flutter_foreground_task` for downloads; the only timed work is three unrelated 1-second `Timer.periodic`s; the only "update check" is the app self-updater against `https://api.kazumi.fyi/kazumi/v1/app/latest`. This matches the README's unchecked `- [ ]  番剧更新提醒`.

---

# Source URLs (all read via `https://raw.githubusercontent.com/Predidit/Kazumi/main/<path>`)

**Topic A**
1. `lib/request/config/api_endpoints.dart`
2. `lib/request/clients/danmaku_client.dart`
3. `lib/request/apis/danmaku_api.dart`
4. `lib/utils/crypto.dart`
5. `lib/utils/dandan_credentials.dart`
6. `lib/modules/danmaku/danmaku_module.dart`
7. `lib/modules/danmaku/danmaku_episode_response.dart`
8. `lib/modules/danmaku/danmaku_search_response.dart`
9. `lib/modules/danmaku/danmaku_ch_convert.dart`
10. `lib/modules/danmaku/danmaku_shield_rule.dart`
11. `lib/modules/danmaku/danmaku_shield_sync.dart`
12. `lib/utils/danmaku.dart`
13. `lib/utils/media.dart`
14. `lib/pages/player/controller/player_danmaku_controller.dart`
15. `lib/pages/player/danmaku_source_sheet.dart`
16. `lib/pages/player/danmaku_destination_sheet.dart`
17. `lib/pages/player/player_item.dart`
18. `lib/pages/player/player_item_panel.dart`
19. `lib/pages/settings/danmaku/danmaku_settings_sheet.dart`
20. `lib/pages/settings/danmaku/danmaku_shield_settings_sheet.dart`
21. `lib/pages/settings/danmaku/danmaku_shield_editor.dart`
22. `lib/pages/settings/danmaku/danmaku_ch_convert_tile.dart`
23. `lib/pages/settings/danmaku/danmaku_time_offset_sheet.dart`
24. `lib/pages/my/my_controller.dart`
25. `lib/repositories/danmaku_shield_repository.dart`
26. `lib/services/sync/danmaku_shield_sync_service.dart`
27. `lib/services/sync/webdav.dart`
28. `lib/services/storage/settings_keys.dart`
29. `lib/services/storage/storage.dart`
30. `lib/pages/download/download_controller.dart`
31. `lib/pages/video/video_controller.dart`
32. `lib/pages/video/video_page.dart`
33. `lib/request/clients/bangumi_client.dart`
34. `lib/core_module.dart`
35. `pubspec.yaml`
36. `pubspec.lock`

**Topic B**
37. `lib/bean/card/bangumi_timeline_card.dart`
38. `lib/utils/anime_season.dart`
39. `lib/utils/date_time.dart`
40. `lib/utils/constants.dart`
41. `lib/pages/index_module.dart`
42. `lib/pages/index_page.dart`
43. `lib/pages/init_page.dart`
44. `lib/pages/router.dart`
45. `lib/pages/timeline/timeline_controller.dart`
46. `lib/pages/timeline/timeline_page.dart`
47. `lib/pages/timeline/timeline_module.dart`
48. `lib/request/apis/bangumi_api.dart`
49. `lib/repositories/collect_repository.dart`
50. `lib/services/update/auto_updater.dart`
51. `lib/services/update/startup_update_check.dart`
52. `lib/services/download/background_download_service.dart`
53. `lib/services/player/audio_controller.dart`
54. `lib/services/player/timed_shutdown_service.dart`
55. `lib/services/player/syncplay_client.dart`
56. `lib/webview/video/impl/video_webview_impl.dart`
57. `lib/pages/settings/interface_settings.dart`
58. `README.md`
59. `.github/workflows/pr.yaml` (inspected; no `dart-define` / `DANDAN` references)
60. `.github/workflows/release.yaml` (inspected; no `dart-define` / `DANDAN` references)
