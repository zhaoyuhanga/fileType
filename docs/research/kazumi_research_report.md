# Kazumi (`Predidit/Kazumi`) — Verified Source-Code Fact Report

**Snapshot:** branch **`main`**, commit `2768c230407d5fd8a57716160ba1a85a99161581` ("feat(download): support android custom directories (#2647)"), `pubspec.yaml` `version: 2.3.8+20308`, Flutter `3.47.6`, Dart SDK `>=3.10.0 <4.0.0`.
**License:** GPL-3.0.

> **⚠ CORRECTION TO THE TASK PREMISE: there is NO `master` branch.** `https://api.github.com/repos/Predidit/Kazumi/branches` returns exactly two branches: **`main`** (default, per `default_branch":"main"` in the repo API) and `m3e-player`. Every URL in this report uses `main`. (`raw.githubusercontent.com/.../master/pubspec.yaml` happens to return 200/identical content, but `master/lib/utils/storage.dart` returns 404 — do not rely on `master`.)

**Evidence standard:** each claim cites `https://raw.githubusercontent.com/Predidit/Kazumi/main/<path>`. Where a capability does not exist, the report says exactly **未查到**. Nothing here is invented.

**Method:** full repo tarball (`https://codeload.github.com/Predidit/Kazumi/tar.gz/refs/heads/main`) extracted and read locally, cross-checked against raw.githubusercontent.com, and corroborated by 4 independent sub-research passes (player/Anime4K, danmaku/timetable, storage/WebDAV, extras) that verified byte-identical file hashes.

**Architecture note (important):** the current layout is *not* the one assumed by the task brief. Hive box opening is in `lib/services/storage/storage.dart`, not `lib/utils/storage.dart` (**does not exist**). `lib/pages/webview/` **does not exist** — it is `lib/webview/{captcha,video}/`. `lib/utils/webdav.dart`, `lib/utils/syncplay.dart`, `lib/utils/external_player.dart`, `lib/request/interceptor.dart`, `lib/utils/string_match.dart` and `lib/pages/download/download_episode_page.dart` **do not exist**. Also note **jsDelivr's `/flat` file listing for `@main` serves a STALE older commit** and must not be used for path discovery.

---

# 1. Rule format

## 1.1 Where rules are parsed — `lib/plugins/`

| File | Role |
|---|---|
| `lib/plugins/plugins.dart` | `class Plugin` — the rule model, `fromJson` / `toJson` / `fromTemplate` / `queryBangumi` / `queryChapterRoads` / `buildHttpHeaders` |
| `lib/plugins/api_rule_config.dart` | `RuleMode`, `ApiBodyType`, `ApiChapterFormat`, `ApiRequestConfig`, `ApiSearchConfig`, `ApiEpisodePageConfig`, `ApiChapterConfig` |
| `lib/plugins/anti_crawler_config.dart` | `CaptchaType`, `CaptchaDetectType`, `AntiCrawlerConfig` |
| `lib/plugins/plugins_controller.dart` | `PluginsController` (MobX), load/save/import/update/catalog |
| `lib/services/plugin/rule_engine.dart` | `RuleEngine`, `RuleRequestExecutor`, `_DefaultRuleRequestExecutor` |
| `lib/services/plugin/xpath_rule_strategy.dart` | `XPathRuleStrategy` (+ `XPathRuleFormatException`, `XPathRuleField`, `XPathRuleFormatKind`) |
| `lib/services/plugin/api_rule_strategy.dart` | `ApiRuleStrategy`, `RestrictedJsonPath`, `ApiRuleFormatException` |
| `lib/services/plugin/rule_engine_models.dart` | `RuleExecutionConfig`, `PreparedRuleRequest`, exceptions |
| `lib/services/plugin/plugin_import_parser.dart` | `PluginImportParser`, `PluginImportParseResult` |
| `lib/utils/encoding.dart` | `jsonToKazumiBase64`, `kazumiBase64ToJson`, `findKazumiRuleLinkSegments` |
| `lib/request/config/api_endpoints.dart` | `ApiEndpoints.apiLevel = 8`, rule-shop URLs |
| `lib/request/apis/plugin_catalog_api.dart` | `PluginCatalogApi.getPluginList/getPlugin` |
| `lib/modules/plugin/plugin_http_module.dart` | `PluginHTTPItem` (catalog entry model) |
| `assets/plugins/*.json` | built-in bundled rules (`7sefun.json`, `DM84.json`) |

Source: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/plugins/plugins.dart`

## 1.2 ALL JSON fields — verbatim from `Plugin.toJson()`

```dart
  Map<String, dynamic> toJson() {
    return <String, dynamic>{
      'api': api,
      'type': type,
      'name': name,
      'version': version,
      'muliSources': muliSources,
      'useWebview': useWebview,
      'useNativePlayer': useNativePlayer,
      'usePost': usePost,
      'useLegacyParser': useLegacyParser,
      'adBlocker': adBlocker,
      'userAgent': userAgent,
      'baseURL': baseUrl,
      'searchURL': searchURL,
      'searchList': searchList,
      'searchName': searchName,
      'searchResult': searchResult,
      'chapterRoads': chapterRoads,
      'chapterResult': chapterResult,
      'referer': referer,
      'searchMode': searchMode,
      'chapterMode': chapterMode,
      // Persisting re-serializes the whole plugin list, so a configured API
      // rule must survive even while the mode points at XPath.
      if (searchMode == RuleMode.api || searchApiConfig.request.url.isNotEmpty)
        'searchApiConfig': searchApiConfig.toJson(),
      if (chapterMode == RuleMode.api ||
          chapterApiConfig.request.url.isNotEmpty)
        'chapterApiConfig': chapterApiConfig.toJson(),
      'antiCrawlerConfig': antiCrawlerConfig.toJson(),
    };
  }
```
Note the JSON key is **`baseURL`** while the Dart field is **`baseUrl`**.

Source: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/plugins/plugins.dart`

### 1.2.1 Top-level fields with exact `fromJson` defaults

```dart
  factory Plugin.fromJson(Map<String, dynamic> json) {
    return Plugin(
      api: json['api']?.toString() ?? '1',
      type: json['type'] as String? ?? 'anime',
      name: json['name'] as String? ?? '',
      version: json['version']?.toString() ?? '',
      muliSources: json['muliSources'] as bool? ?? true,
      useWebview: json['useWebview'] as bool? ?? true,
      useNativePlayer: json['useNativePlayer'] as bool? ?? true,
      usePost: json['usePost'] ?? false,
      useLegacyParser: json['useLegacyParser'] ?? false,
      adBlocker: json['adBlocker'] ?? false,
      userAgent: json['userAgent'] as String? ?? '',
      baseUrl: json['baseURL'] as String? ?? '',
      searchURL: json['searchURL'] as String? ?? '',
      searchList: json['searchList'] as String? ?? '',
      searchName: json['searchName'] as String? ?? '',
      searchResult: json['searchResult'] as String? ?? '',
      chapterRoads: json['chapterRoads'] as String? ?? '',
      chapterResult: json['chapterResult'] as String? ?? '',
      referer: json['referer'] ?? '',
      searchMode: RuleMode.normalize(json['searchMode']),
      chapterMode: RuleMode.normalize(json['chapterMode']),
      searchApiConfig: json['searchApiConfig'] is Map ? ApiSearchConfig.fromJson(...) : ApiSearchConfig(),
      chapterApiConfig: json['chapterApiConfig'] is Map ? ApiChapterConfig.fromJson(...) : ApiChapterConfig(),
      antiCrawlerConfig: json['antiCrawlerConfig'] != null ? AntiCrawlerConfig.fromJson(...) : AntiCrawlerConfig.empty(),
    );
  }
```

Note the source typos preserved verbatim: **`muliSources`** (one `l`) and **`searchURL`**/`chapterRoads` (camel-case) vs `baseURL`.

Source: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/plugins/plugins.dart`

### 1.2.2 Nested `searchApiConfig` / `chapterApiConfig` fields

`ApiRequestConfig`: `method` (default `'GET'`, upper-cased), `url` (`''`), `headers` (`{}`), `query` (`{}`), `bodyType` (`ApiBodyType.none`), `body` (null).

`ApiSearchConfig`: `request` (ApiRequestConfig), `listPath` (default `r'$.data[*]'`), `namePath` (`r'$.name'`), `sourcePath` (`r'$.url'`).

`ApiChapterConfig`: `request`, `format` (`ApiChapterFormat.nested`), `roadsPath` (`r'$.data.roads[*]'`), `roadNamePath` (`r'$.name'`), `episodesPath` (`r'$.episodes[*]'`), `episodeNamePath` (`r'$.name'`), `episodeUrlPath` (`r'$.url'`), `roadNamesPath` (`''`), `roadEpisodesPath` (`''`), `roadSeparator` (`` r'$$$' ``), `episodeSeparator` (`'#'`), `fieldSeparator` (`r'$'`), `variables` (`{}`), `episodePage` (`null`).

`ApiEpisodePageConfig`: `url` (`''`), `query` (`{}`).

Source: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/plugins/api_rule_config.dart`

Enums/constants in the same file:
```dart
class RuleMode {
  static const String xpath = 'xpath';
  static const String api = 'api';
  static String normalize(Object? value) { return value == api ? api : xpath; }
}
class ApiBodyType {  static const String none = 'none'; static const String json = 'json'; static const String form = 'form'; }
class ApiChapterFormat { static const String nested = 'nested'; static const String delimited = 'delimited'; }
```

### 1.2.3 Nested `antiCrawlerConfig` fields

```dart
class CaptchaType {
  static const int imageCaptcha = 1;
  static const int autoClickButton = 2;
  static const int customJavaScript = 3;
}
class CaptchaDetectType {
  static const int xpath = 1;
  static const int text = 2;
  static const int regex = 3;
}
class AntiCrawlerConfig {
  bool enabled;              // default false
  int captchaType;           // default CaptchaType.imageCaptcha (1)
  String captchaImage;       // default ''
  String captchaInput;       // default ''
  String captchaButton;      // default ''
  int captchaDetectType;     // default CaptchaDetectType.xpath (1)
  String captchaDetectValue; // default ''
  String captchaScript;      // default ''
}
```
Source: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/plugins/anti_crawler_config.dart`

### 1.2.4 Real bundled rule (exact JSON)

`assets/plugins/7sefun.json`:
```json
{
    "api": "4",
    "type": "anime",
    "name": "7sefun",
    "version": "1.3",
    "muliSources": true,
    "useWebview": true,
    "useNativePlayer": true,
    "userAgent": "",
    "baseURL": "https://www.7sefun.top/",
    "searchURL": "https://www.7sefun.top/vodsearch/-------------.html?wd=@keyword",
    "searchList": "//div[2]/div[2]/div[2]/div[2]/div",
    "searchName": "//div[2]/text()",
    "searchResult": "//a",
    "chapterRoads": "//div[2]/div[2]/div[2]/div/div[2]/div[1]//div",
    "chapterResult": "//a"
}
```
`assets/plugins/DM84.json`:
```json
{
    "api": "5",
    "type": "anime",
    "name": "DM84",
    "version": "1.4",
    "muliSources": true,
    "useWebview": true,
    "useNativePlayer": true,
    "userAgent": "",
    "adBlocker": true,
    "baseURL": "https://dmbus.cc/",
    "searchURL":"https://dmbus.cc/s----------.html?wd=@keyword",
    "searchList": "//div/div[3]/ul/li",
    "searchName": "//div/a[2]",
    "searchResult": "//div/a[2]",
    "chapterRoads": "//div/div[4]/div/ul",
    "chapterResult": "//li/a"
}
```
Sources: `https://raw.githubusercontent.com/Predidit/Kazumi/main/assets/plugins/7sefun.json`, `.../assets/plugins/DM84.json`

## 1.3 Selector syntax

- XPath, evaluated by the packages `xpath_selector: ^3.0.2` + `xpath_selector_html_parser: ^3.0.1` on HTML parsed by the `html` package: `Element _documentElement(String raw) => parse(raw).documentElement` (`lib/services/plugin/xpath_rule_strategy.dart`).
- Every XPath field is **`root.queryXPath(<selector>).nodes` (list) or `.node` (single)**. `chapterRoads` selects the *road* nodes; within each road, `chapterResult` selects episode nodes and the code reads **`episode.attributes['href']`** and the node text.
- **Only `//`-prefixed selectors are officially supported.** README Q&A states: *"目前我们对 `Xpath` 语法的支持并不完整, 我们目前只支持以 `//` 开头的选择器。"* — **this is documentation, NOT enforced in code**: `XPathRuleStrategy._runSelector` only rejects an *empty* selector. (Repo-wide grep for `startsWith('//')` matches only the WebView video sniffer, never a rule validator.)
- Search keyword substitution: `config.searchUrl.replaceAll('@keyword', Uri.encodeQueryComponent(keyword))` (`lib/services/plugin/xpath_rule_strategy.dart`).
- `usePost: true` converts the GET URL's query parameters into a form body: `PreparedRuleRequest(method: 'POST', url: uri.replace(query: null).toString(), bodyType: 'form', body: uri.queryParameters, includeCookies: true)`.
- Rule URLs are normalized by `normalizeEpisodeUrl(baseUrl, raw)` (`lib/utils/episode_url.dart`) — resolves relative paths, unifies same-host scheme to `baseUrl`'s, strips trailing slashes/empty query, idempotent.
- API mode uses **JSONPath** (`json_path: ^0.9.0`) restricted by `RestrictedJsonPath.validate` to `$`-rooted expressions with only `.name`, `[index]`, `[*]` and `['quoted']` segments; anything else throws `ApiRuleFormatException('不支持的 JSONPath: …')`. Template variables use `@name` syntax: `RegExp(r'(?<![A-Za-z0-9_])@([A-Za-z_][A-Za-z0-9_]*)')`, with `@source`/`@episodeUrl`/`@roadIndex`/`@roadNumber`/`@episodeIndex`/`@episodeNumber` plus rule-declared `variables` (`lib/services/plugin/api_rule_strategy.dart`).

Sources: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/plugin/xpath_rule_strategy.dart`, `.../lib/services/plugin/api_rule_strategy.dart`, `.../lib/utils/episode_url.dart`, `.../README.md`

## 1.4 API level / version fields

- **`api`** is the **rule API level** (a string in JSON, compared as int). **`ApiEndpoints.apiLevel = 8`**:
```dart
class ApiEndpoints {
  /// 当前版本
  static const String version = appBuildName ?? '0.0.0';
  /// 规则API级别
  static const int apiLevel = 8;
```
- Gate: `bool get requiresNewerClient => int.parse(api) > ApiEndpoints.apiLevel;` (`lib/plugins/plugins.dart`).
- Import rejects such rules: `if (plugin.requiresNewerClient) { throw const FormatException('规则需要更高版本客户端'); }` (`lib/services/plugin/plugin_import_parser.dart`).
- Rule update returns `PluginUpdateResult.requiresNewerClient` (`lib/plugins/plugins_controller.dart`).
- **`version`** is a separate free-form rule version string (e.g. `"1.3"`), compared with `needUpdate(local, remote)` (`lib/utils/version.dart` — dot-segment integer compare, missing segments = 0).
- The editor auto-writes the current API level when either mode is API: `api: searchMode == RuleMode.api || chapterMode == RuleMode.api ? ApiEndpoints.apiLevel.toString() : _api` (`lib/pages/plugin_editor/plugin_editor_page.dart`).

Sources: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/request/config/api_endpoints.dart`, `.../lib/plugins/plugins.dart`, `.../lib/utils/version.dart`, `.../lib/pages/plugin_editor/plugin_editor_page.dart`

## 1.5 Validation

| Validator | What it checks | Source |
|---|---|---|
| `PluginImportParser.parse` / `_parseEntry` | non-empty content; each entry must be a JSON object, a bare base64 string, or a `kazumi://` link; **`plugin.name.trim().isEmpty` → `FormatException('规则名称不能为空')`; `plugin.requiresNewerClient` → `FormatException('规则需要更高版本客户端')`**; dedups by `pluginNameKey(name)` (lower-cased) counting `duplicateCount` | `lib/services/plugin/plugin_import_parser.dart` |
| `XPathRuleStrategy._runSelector` | empty selector → `XPathRuleFormatKind.invalidSelector`; unparsable selector → same kind with `cause`; bad URL → `invalidUrl`; unparsable HTML → `invalidDocument`. Fields labelled in Chinese (`'搜索结果列表'`, `'条目名称'`, `'条目链接'`, `'播放线路列表'`, `'剧集列表'`, `'验证页检测'`, `'验证码图片'`, `'验证按钮'`) | `lib/services/plugin/xpath_rule_strategy.dart` |
| `ApiRuleStrategy.validateSearchConfig` | validates `listPath`, `namePath`, `sourcePath` | `lib/services/plugin/api_rule_strategy.dart` |
| `ApiRuleStrategy.validateChapterConfig` | validates `variables` + per-format fields; delimited requires non-empty `roadSeparator`/`episodeSeparator`/`fieldSeparator` (`'章节分隔符不能为空'`); nested requires either `episodeUrlPath` or `episodePage` (`'必须配置播放入口地址路径或播放页地址模板'`) | same |
| `PluginCatalogApi.parsePluginList` | catalog root must be a JSON array; invalid items skipped and counted (`skippedItems`); all-invalid → `FormatException('Rule catalog contains no valid items')` | `lib/request/apis/plugin_catalog_api.dart` |
| `PluginsController._preparePluginUpdate` | downloaded rule `name` must case-insensitively match the catalog name, else `FormatException('Downloaded rule name … does not match …')`; rejects older/equal versions (`PluginUpdateResult.notNewer` — never downgrades) | `lib/plugins/plugins_controller.dart` |
| `PluginValidityTracker` | only tracks whether a rule returned search results this session; **no rule correctness validation** | `lib/services/plugin/plugin_validity_tracker.dart` |

There is also a built-in **rule test page**: `lib/pages/plugin_editor/plugin_test_page.dart` (`PluginTestPage`) calls `plugin.traceSearch(keyword)` then `plugin.traceChapters(firstItem.src)` (`lib/pages/plugin_editor/plugin_test_page.dart`).

## 1.6 Import / export / share

**On-disk storage** — a single `plugins.json` in the app support dir (`lib/plugins/plugins_controller.dart`):
```dart
  String pluginsFileName = "plugins.json";
  Future<void> init() async {
    final directory = await getApplicationSupportDirectory();
    oldPluginDirectory = Directory('${directory.path}/plugins');
    ...
    newPluginDirectory = Directory('${directory.path}/plugins/v2');
```
Comment at the top of the file: *"从 1.5.1 版本开始，规则文件储存在单一的 plugins.json 文件中。"* Legacy per-file `plugins/*.json` are merged on first load then deleted. Bundled assets are copied on first run by `copyPluginsToExternalDirectory()` (filters `asset.startsWith('assets/plugins/') && asset.endsWith('.json')`).

**Four import paths** — `enum RuleAddSource { catalog, clipboard, file, create }` (`lib/pages/plugin_editor/rule_dialogs.dart`):
1. **catalog** → `context.pushNamed('/settings/plugin/shop')` (rule shop)
2. **clipboard** → `Clipboard.getData(Clipboard.kTextPlain)` → import dialog (`'支持多条 kazumi:// 链接、单条 JSON 或 JSON 数组。同名规则会被替换。'`)
3. **file** → `FilePicker.platform.pickFiles(type: FileType.custom, allowedExtensions: const ['json'], withData: true)` → `PluginImportParser.parse(utf8.decode(bytes))`
4. **create** → editor seeded with `Plugin.fromTemplate()`

**Share/export** — a `kazumi://<base64>` link, copied to the clipboard (`lib/pages/plugin_editor/rule_dialogs.dart`):
```dart
Future<void> showRuleShareDialog(BuildContext context, Plugin plugin) async {
  final link = jsonToKazumiBase64(jsonEncode(plugin.toJson()));
```
```dart
String jsonToKazumiBase64(String jsonStr) {
  final base64Str = base64Encode(utf8.encode(jsonStr));
  return 'kazumi://$base64Str';
}
```
`kazumiBase64ToJson` tolerates URL-safe base64 (`-`/`_`), missing padding, percent-encoding, whitespace, and multiple links in one blob (`findKazumiRuleLinkSegments`, `RegExp(r'kazumi:(?://)?', caseSensitive: false)`).

**`kazumi://` is NOT a registered OS deep link** — it is only an in-app text/clipboard encoding:
- `android/app/src/main/AndroidManifest.xml` intent-filters are only `MAIN`/`LAUNCHER`, `android.media.browse.MediaBrowserService`, `android.intent.action.MEDIA_BUTTON`, and `android.intent.action.PROCESS_TEXT`.
- `ios/Runner/Info.plist` has **no `CFBundleURLTypes`** at all; the strings `Kazumi`/`kazumi` are only `CFBundleDisplayName`/`CFBundleName`, and `LSApplicationQueriesSchemes` contains only `vlc-x-callback`.

Sources: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/plugins/plugins_controller.dart`, `.../lib/pages/plugin_editor/rule_dialogs.dart`, `.../lib/pages/plugin_editor/plugin_view_page.dart`, `.../lib/utils/encoding.dart`, `.../android/app/src/main/AndroidManifest.xml`, `.../ios/Runner/Info.plist`

## 1.7 Rule shop (sharing ecosystem)

```dart
  /// 规则仓库
  static const String pluginShop =
      'https://raw.githubusercontent.com/Predidit/KazumiRules/main/';
  /// 规则仓库镜像
  static const String pluginShopMirror =
      'https://raw.gitcode.com/gh_mirrors/ka/KazumiRules/raw/main/';
```
`PluginCatalogApi.getPluginList()` fetches `${ApiEndpoints.pluginShop}index.json`; `getPlugin(name)` fetches `${ApiEndpoints.pluginShop}$name.json`. The mirror is used when `SettingsKeys.enableGitProxy` is true, via `_RulesMirrorInterceptor.onRequest` in `lib/request/core/dio_factory.dart`. Real catalog entry shape (from `https://raw.githubusercontent.com/Predidit/KazumiRules/main/index.json`): `{"name":"7sefun","version":"1.3","useNativePlayer":true,"antiCrawlerEnabled":false,"author":"","lastUpdate":1786362131000}` → parsed by `PluginHTTPItem` (`name`, `version`, `useNativePlayer`, `author`, `lastUpdate`, `antiCrawlerEnabled`). Catalog is cached for `Duration(minutes: 5)`; batch updates use `_maxConcurrentRuleDownloads = 4`.

Sources: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/request/config/api_endpoints.dart`, `.../lib/request/apis/plugin_catalog_api.dart`, `.../lib/request/core/dio_factory.dart`, `.../lib/modules/plugin/plugin_http_module.dart`, `.../lib/plugins/plugins_controller.dart`

---

# 2. Query / parser implementation

## 2.1 Class map

| Concern | Class / method | File |
|---|---|---|
| Facade on a rule | `Plugin.queryBangumi(keyword, {shouldRethrow, cancelToken})`, `Plugin.queryChapterRoads(source, {cancelToken})`, `Plugin.traceSearch`, `Plugin.traceChapters`, `Plugin.parseHarvestedSearch(html)`, `Plugin.buildFullUrl`, `Plugin.buildHttpHeaders()` | `lib/plugins/plugins.dart` |
| Engine | `class RuleEngine` → `search(RuleExecutionConfig, keyword, {cancelToken})`, `queryChapters(config, source, {cancelToken})`, `tryParseHarvestedSearch(config, raw)`, `_isCaptchaChallengeResponse(...)` | `lib/services/plugin/rule_engine.dart` |
| XPath execution | `class XPathRuleStrategy` → `prepareSearchRequest`, `prepareChapterRequest`, `parseSearch`, `parseChapters`, `detectsCaptchaChallenge` | `lib/services/plugin/xpath_rule_strategy.dart` |
| API execution | `class ApiRuleStrategy` → `prepareRequest`, `parseSearch`, `parseChapters`, `validateSearchConfig`, `validateChapterConfig`, `decodeResponse`; `class RestrictedJsonPath` | `lib/services/plugin/api_rule_strategy.dart` |
| HTTP transport | `abstract interface class RuleRequestExecutor`; `class _DefaultRuleRequestExecutor`; `PluginSiteClient.instance.requestText(url, method:, headers:, queryParameters:, data:, cancelToken:)` | `lib/services/plugin/rule_engine.dart`, `lib/request/clients/plugin_site_client.dart` |
| Cookies (captcha clearance) | `class PluginCookieManager` → `saveFromWebView(pluginName, pageUrl, cookieString, {userAgent})`, `loadForRequest`, `userAgentFor` | `lib/services/plugin/plugin_cookie_manager.dart` |
| Search fan-out | `class PluginSearchService` → `querySource`, `queryAllSource`, `applyHarvestedSearchResult`, `cancel` | `lib/services/plugin/plugin_search_service.dart` |
| Models | `class SearchItem { String name; String src; }`, `class PluginSearchResponse { String pluginName; List<SearchItem> data; }`, `enum PluginSearchStatus { pending, success, error, noResult, captcha }`, `class Road { String name; List<String> data; List<String> identifier; }` | `lib/modules/search/plugin_search_module.dart`, `lib/modules/roads/road_module.dart` |
| Trace models | `RuleSearchTrace{rawResponse, response, matchedFragments, diagnostics}`, `RuleChapterTrace{rawResponse, roads, diagnostics}`, `RuleSearchParseResult`, `RuleChapterParseResult`, `PreparedRuleRequest`, `RuleExecutionConfig` | `lib/services/plugin/rule_engine_models.dart` |
| Exceptions | `SearchErrorException`, `ChapterErrorException`, `NoResultException`, `CaptchaRequiredException`, `XPathRuleFormatException`, `ApiRuleFormatException` | `rule_engine_models.dart`, `xpath_rule_strategy.dart`, `api_rule_strategy.dart` |

## 2.2 Signature / flow

`Plugin.queryBangumi` delegates to the engine with an immutable snapshot:
```dart
  RuleExecutionConfig get _executionConfig => RuleExecutionConfig(
        pluginName: name, baseUrl: baseUrl, usePost: usePost,
        searchMode: searchMode, chapterMode: chapterMode,
        searchUrl: searchURL, searchList: searchList, searchName: searchName,
        searchResult: searchResult, chapterRoads: chapterRoads,
        chapterResult: chapterResult, searchApiConfig: searchApiConfig,
        chapterApiConfig: chapterApiConfig, antiCrawlerConfig: antiCrawlerConfig,
      );
```
`RuleEngine.search` picks the strategy by `config.searchMode == RuleMode.api`, catches `SearchErrorException` and promotes an anti-crawler challenge via `_isCaptchaChallengeResponse` (which also honours the Cloudflare header `cf-mitigated: challenge`) into `CaptchaRequiredException`. Empty results throw `NoResultException`.

Request building for XPath: GET with `@keyword` substituted and `includeCookies: true` (search only — *"XPath chapter requests historically go out without stored cookies; only search requests attach them."*). The default executor builds headers:
```dart
    final headers = <String, dynamic>{
      'referer': '${config.baseUrl}/',
      if (cookieHeader.isNotEmpty) 'cookie': cookieHeader,
      for (final entry in request.headers.entries)
        entry.key.toLowerCase(): entry.value,
      if (verifiedUserAgent != null) 'user-agent': verifiedUserAgent,
    };
```
plus `content-type: application/json` / `x-www-form-urlencoded` for POST. Requests go through `PluginSiteClient.requestText` → `DioFactory.pluginDio` with default headers `{'user-agent': getRandomUA(), 'accept-language': getRandomAcceptedLanguage()}`.

Sources: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/plugin/rule_engine.dart`, `.../lib/services/plugin/xpath_rule_strategy.dart`, `.../lib/request/clients/plugin_site_client.dart`, `.../lib/request/core/dio_factory.dart`

## 2.3 Get details (episode list) → `Road[]`

`RuleEngine.queryChapters` → `XPathRuleStrategy.parseChapters` / `ApiRuleStrategy.parseChapters`, producing `List<Road>`. XPath version: roads from `chapterRoads`; per road, episodes from `chapterResult` reading `href`; names whitespace-stripped with fallback `'第${episodeIndex + 1}集'`; road name auto-generated `'播放线路${roads.length + 1}'`. API version supports `nested` (`roadsPath`/`roadNamePath`/`episodesPath`/`episodeNamePath`/`episodeUrlPath`) and `delimited` (`roadNamesPath`/`roadEpisodesPath` + 3 separators).

## 2.4 Parse video URL — WebView sniffer, JavaScript hook, and the iframe legacy parser

The task's guesses map onto real code as follows:

- **"PluginQueryer"-like pipeline** = `VideoSourceResolverPool` + `WebViewVideoSourceService` + `VideoWebviewControllerFactory`:
```dart
class VideoWebviewControllerFactory {
  static VideoWebviewController getController() {
    if (Platform.isWindows) return VideoWebviewWindowsImpl();
    if (Platform.isLinux) return VideoWebviewLinuxImpl();
    if (Platform.isMacOS || Platform.isIOS) return VideoWebviewAppleImpl();
    if (Platform.isAndroid && WebViewFeatureService.isDocumentStartScriptSupported) return VideoWebviewAndroidImpl();
    return VideoWebviewImpl();
  }
}
```
Interface (`lib/webview/video/video_webview_controller.dart`):
```dart
abstract class VideoWebviewController<T> {
  T? webviewController;
  int count = 0;
  int offset = 0;
  bool isIframeLoaded = false;
  bool isVideoSourceLoaded = false;
  Future<void> init();
  Stream<bool> get onInitialized;
  Stream<String> get onLog;
  Stream<bool> get onVideoLoading;
  Stream<VideoParserEvent> get onVideoURLParser;   // typedef VideoParserEvent = ({String url, int offset, VideoSourceFormat format})
  Future<void> loadUrl(String url, bool useLegacyParser, {int offset = 0});
  Future<void> unloadPage();
  Future<void> dispose();
}
```
- **`IVideoSourceService`** (`lib/services/video_source/video_source_service.dart`): `Future<VideoSource> resolve(String episodeUrl, {required bool useLegacyParser, int offset = 0, Duration timeout = const Duration(seconds: 30)})`, `cancel()`, `dispose()`; `class VideoSource { url, offset, type, format }`, `enum VideoSourceType { online, cached }`, `enum VideoSourceFormat { auto, hls }`, exceptions `VideoSourceNotFoundException`, `VideoSourceTimeoutException`, `VideoSourceCancelledException`. `WebViewVideoSourceService` is the only implementation (`timeout` default **15 s**), serialized per instance by `_resolveTail`.
- **JavaScript hook (modern parser, `useLegacyParser == false`)** — `lib/webview/video/impl/video_webview_impl.dart`:
  - `onLoadStart` injects a blob/XHR parser that patches `window.Response.prototype.text` and `window.XMLHttpRequest.prototype.open` and, when the body `text.trim().startsWith("#EXTM3U")`, calls `window.flutter_inappwebview.callHandler('LogBridge', …)` / `('VideoBridgeDebug', this.url)`. It also patches the same prototypes **inside every iframe** (`injectIntoIframe`) and watches for new iframes via `MutationObserver`.
  - `onLoadStop` injects a `<video>` scanner using `MutationObserver` with `attributeFilter: ['src']`, reading `video.getAttribute('src')` and nested `<source>` tags, skipping `blob:` and `googleads`.
  - A 1-second `Timer.periodic` re-polls (`_pollVideoSource`).
  - Native request interception also fires: `shouldInterceptRequest` → `_isM3U8Url` (path ends `.m3u8`) or `_isRangeVideoRequest` (`Range: bytes=` header, excluding `.js/.css/.html/.json/.png/.jpg/.gif/.svg/.woff/.woff2/.wasm`), then `notifyVideoSourceResolved(url)`.
- **iframe `src` legacy parser (`useLegacyParser == true`)** — same file. It registers handler **`JSBridgeDebug`**, injects an iframe scanner (`processIframeElement` reading `iframe.getAttribute('src')`, `MutationObserver` with `attributeFilter: ['src']`), and accepts a message when `message.contains('http') || message.startsWith('//')` while rejecting `googleads`, `googlesyndication.com`, `prestrain.html`, `prestrain%2Ehtml`, `adtrafficquality`. It then post-processes with `decodeVideoSource(encodedUrl)`:
```dart
String decodeVideoSource(String iframeUrl) {
  final decodedUrl = Uri.decodeFull(iframeUrl);
  final regExp = RegExp(r'(http[s]?://.*?\.m3u8)|(http[s]?://.*?\.mp4)', caseSensitive: false);
  final uri = Uri.parse(decodedUrl);
  final params = uri.queryParameters;
  var matchedUrl = iframeUrl;
  params.forEach((key, value) { if (regExp.hasMatch(value)) { matchedUrl = value; return; } });
  return Uri.encodeFull(matchedUrl);
}
```
  Handler names: modern = **`LogBridge` + `VideoBridgeDebug`**; legacy = **`LogBridge` + `JSBridgeDebug`**. UI strings: `'If there is audio but no video, please report it to the rule developer.'`
- **Captcha WebView** (`lib/webview/captcha/`): handlers **`CaptchaImageBridge`**, **`CaptchaStatusBridge`**, **`CaptchaGoneBridge`**, **`ButtonClickedBridge`**, **`CaptchaLogBridge`**, and an injected JS API **`window.KazumiCaptcha = { log, clicked, done, fail }`** (documented in `captcha_webview_controller.dart`: `/// 脚本可调用 window.KazumiCaptcha.log/clicked/done/fail。`). Driver: `lib/services/plugin/captcha_verification_service.dart`; UI flow `lib/pages/info/source_captcha_flow.dart`.
- **Result hand-off to the player**: `VideoSource(url, offset, type, format)` → `lib/pages/video/video_controller.dart` builds `PlaybackInitParams` (fields: `videoUrl, offset, isLocalPlayback, videoSourceFormat, bangumiId, pluginName, episode, danmakuEpisodeNumber, pageUrl, sortNumber, httpHeaders, adBlockerEnabled, episodeTitle, referer, currentRoad, coverUrl, bangumiName`) → `PlayerPlaybackController` → `player.open(Media(videoUrl(), start: Duration(seconds: offset), httpHeaders: httpHeaders), play: autoPlay)`.

Sources: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/webview/video/video_webview_controller.dart`, `.../lib/webview/video/impl/video_webview_impl.dart`, `.../lib/webview/video/impl/video_webview_android_impl.dart`, `.../lib/services/video_source/video_source_service.dart`, `.../lib/services/video_source/webview_video_source_service.dart`, `.../lib/services/video_source/video_source_resolver_pool.dart`, `.../lib/utils/media.dart`, `.../lib/webview/captcha/captcha_webview_controller.dart`, `.../lib/webview/captcha/impl/captcha_webview_inappwebview_impl.dart`, `.../lib/pages/player/controller/player_models.dart`, `.../lib/pages/player/controller/player_playback_controller.dart`

## 2.5 Search aggregation

`PluginSearchService.queryAllSource` clears state, marks every rule `pending`, and runs all rules concurrently:
```dart
    await Future.wait(plugins.map((plugin) => _queryPlugin(plugin, keyword)));
```
Each plugin gets an `AsyncSessionOwner` so a newer query invalidates an older write-back; results land in `InfoController.pluginSearchResponseList` (`ObservableList<PluginSearchResponse>`) with status in `pluginSearchStatus`. There is **no cross-source merging or ranking** (see §8.5).

Source: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/plugin/plugin_search_service.dart`

---

# 3. Player

## 3.1 Packages and versions (`pubspec.yaml`)

- **media_kit** and **media_kit_video** are **git dependencies on a Predidit fork** — **no pub semver**:
```yaml
  media_kit:
    git:
      url: https://github.com/Predidit/media-kit.git
      ref: 803c4a27912091db6f839d48d1b984a0d9f588c7
      path: ./media_kit
  media_kit_video:
    git:
      url: https://github.com/Predidit/media-kit.git
      ref: 803c4a27912091db6f839d48d1b984a0d9f588c7
      path: ./media_kit_video
```
  `pubspec.lock` records the fork's own versions: `media_kit: "1.1.11"`, `media_kit_video: "1.2.5"`, both `source: git` at that ref.
- **libmpv is the native backend**, not a pub package. Android's libmpv build is a submodule: `.gitmodules` → `fastlane/.libmpv-android-video-build` = `https://github.com/Predidit/libmpv-android-video-build.git`.
- **`video_player` is NOT a dependency** (未查到 in `pubspec.yaml` and `pubspec.lock`).
- Other relevant: `audio_service: ^0.18.15` (locked 0.18.18), `audio_service_mpris` git `d30b53fb7dbdd8f10ceda7447fe0a0218711a220`, `audio_service_win` git `8ad650c6eb487208b9861d44755c8e58db061c88`, `audio_session: ^0.2.2`, `dlna_dart: ^0.0.8` (locked 0.0.9), `url_launcher: ^6.3.0`, `screen_brightness_android/ios/ohos`, `window_manager: ^0.5.1`, `saver_gallery: ^4.1.0`, `canvas_danmaku: ^0.3.3`, `flutter_volume_controller: ^2.0.1`, `audio_video_progress_bar: ^2.0.2`, `file_picker: ^10.3.7`, `flutter_inappwebview_*`, `webview_windows` (fork), `desktop_webview_window` (fork), `macos_secure_bookmarks: ^0.2.1`.

Sources: `https://raw.githubusercontent.com/Predidit/Kazumi/main/pubspec.yaml`, `.../pubspec.lock`, `.../.gitmodules`

## 3.2 Engine construction

```dart
  final candidate = _OwnedPlayer(
    Player(
      configuration: PlayerConfiguration(
        bufferSize: cachePolicy.bufferSize,
        osc: false,
        logLevel: MPVLogLevel.values[debug.playerLogLevel],
        adBlocker: adBlockerEnabled,
      ),
    ),
  );
```
```dart
  videoController ??= VideoController(
    player,
    configuration: VideoControllerConfiguration(
      vo: videoRenderer,
      enableHardwareAcceleration: hAenable,
      enableAndroidSurfaceProducer: false,
      hwdec: hAenable ? hardwareDecoder : 'no',
      androidAttachSurfaceAfterVideoParameters: false,
    ),
  );
  player.setPlaylistMode(PlaylistMode.none);
```
Sources: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/player/controller/player_playback_controller.dart`

## 3.3 Capability matrix

| Capability | Status | Detail (verbatim identifiers) |
|---|---|---|
| **HLS / m3u8** | Yes | `enum VideoSourceFormat { auto, hls }`; hint only: `if (videoSourceFormat == VideoSourceFormat.hls) { await pp.setProperty('demuxer-lavf-format', 'hls'); }`; Windows sniffer tags HLS. Full HLS parser + ad filter exist for **downloads** (`lib/utils/m3u8_parser.dart`, `lib/utils/m3u8_ad_filter.dart`). |
| **Hardware decoding** | Yes, default ON | `bool hAenable = true;` from `SettingsKeys.hAenable` (default `true`); `late String hardwareDecoder;` from `SettingsKeys.hardwareDecoder` (default **`'auto-safe'`**); applied as `hwdec: hAenable ? hardwareDecoder : 'no'` (never via `setProperty('hwdec', …)`). `hardwareDecodersList` includes `auto`, `auto-safe`, `auto-copy`, `d3d11va`, `d3d11va-copy`, `videotoolbox`, `vaapi`, `nvdec`, `drm`, `vulkan`, `dxva2`, `vdpau`, `mediacodec`, `cuda`, `crystalhd`, `rkmpp`. |
| **Android renderer** | Yes | `androidVideoRenderersList` = `auto` / `gpu` / `gpu-next` / `mediacodec_embed`; `auto` → `gpu-next` when `PlatformEnvironmentService.getAndroidSdkVersion() >= 34`, else `gpu`. `mediacodec_embed` forces `hAenable = true; hardwareDecoder = 'mediacodec'; superResolutionMode = SuperResolutionMode.off;` |
| **Playback speed** | Yes | `setPlaybackSpeed(double)` → `mediaPlayer!.setRate(playerSpeed)`; `double playerSpeed = 1.0` from `SettingsKeys.defaultPlaySpeed`; `const List<double> defaultPlaySpeedList = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0];`; mpv `af` = `"scaletempo2=max-speed=8"`; long-press speed `defaultShortcutForwardPlaySpeed` default `2.0`. |
| **Quality / source switching** | **Road (线路)/episode level only — no in-stream track switch** | `class Road { String name; List<String> data; List<String> identifier; }`; `VideoPageController.roadList`; `EpisodeSelectionPanel` + `_RoadSelector`, `void _selectRoad(int road)`. **未查到** `setVideoTrack`, `vid`/`video-track` mpv usage. |
| **Aspect ratio** | Yes | `enum PlayerAspectRatio { automatic(storageValue: 1, '自动', BoxFit.contain), crop(2,'裁切填充',cover), stretch(3,'拉伸填充',fill), ratio4x3(4,'4:3',fill, 4/3) }` |
| **Subtitles** | **Styling only — no track selection** | `SubtitleViewConfiguration(style: TextStyle(color: Colors.pink, fontSize: 48.0, …), textAlign: TextAlign.center, padding: EdgeInsets.all(24.0))` in `lib/pages/player/player_item_surface.dart`. **未查到** `setSubtitleTrack`, `SubtitleTrack`, external `.srt/.ass/.vtt` loading, or any `sub-*`/`secondary-sub-*` mpv property in Kazumi. (The pinned fork's `PlatformPlayer.setSubtitleTrack` throws `UnimplementedError` and is never called; `PlayerConfiguration.libass` is never set, so rendering stays Flutter-widget based.) Only read-only debug output of `player.stream.track` → `playerAudioTracks`/`playerVideoTracks`. |
| **External player** | Yes, via MethodChannel (not `url_launcher`) | `class ExternalPlayer` on `MethodChannel('com.predidit.kazumi/intent')`: `launchUrlWithMime(url, mimeType)` → `'openWithMime'`; `launchUrlWithReferer(url, referer)` → `'openWithReferer'`; `launchLinuxDesktopPlayer(url)` → `'openWithDesktopPlayer'`. Dispatch in `ExternalPlaybackLauncher.launch()`: Android/Windows w/o referer → `launchUrlWithMime(currentVideoUrl, 'video/mp4')`; macOS/iOS → `launchUrlWithReferer`; Linux w/o referer → `launchLinuxDesktopPlayer`; else toast `'暂不支持该规则'`. UI label `'外部播放'`. |
| **DLNA casting** | Yes | `lib/services/player/remote.dart`, `class RemotePlay` → `castVideo(String video, String referer)` using `DLNAManager()`, `searcher.start()/stop()`, `dlna.devices.stream`, `DLNADevice(value.info).setUrl(video)`, `DLNADevice(value.info).play()`; device fields `friendlyName`/`deviceType`/`URLBase`. UI label `'远程投屏'` (pauses local playback first). |
| **Picture-in-Picture** | Yes | `lib/services/player/pip_utils.dart`, `class PipUtils` on `MethodChannel('com.predidit.kazumi/pip')`: `isAndroidPIPSupported`, `enterAndroidPIPWindow({width,height})`, `updateAndroidPIPActions({playing, danmakuEnabled, width, height, sourceRect})`, `setAndroidAutoEnterPIPEnabled`, `setAndroidPIPInPlayerPage`, `initPipHandler({onAction, onModeChanged})`; desktop via `window_manager`: `enterDesktopPIPWindow` (`const double pipWidth = 480`, `setAlwaysOnTop(true)`, `setAspectRatio`), `exitDesktopPIPWindow` (1280×860 / 800×600), `getPIPAspectSize` (GCD reduce). Setting `androidAutoEnterPIP` default **`false`**. |
| **Screenshots** | Yes | `player.safeScreenshot(format: 'image/png')` (fork API). Mobile: `SaverGallery.saveImage(...)`; desktop: `PlayerScreenshotController` (`_maxCandidates = 24`, `_maxBytes = 96 << 20`) + `ScreenshotExportService.chooseDestination()` = `FilePicker.platform.getDirectoryPath(dialogTitle: '选择截图保存文件夹', lockParentWindow: true)`, filename `'Kazumi_${title}_${episode}_${timeLabel}_${id}.png'`. |
| **Keyboard shortcuts** | Yes | `PlayerKeyboardShortcuts` uses `FocusManager.instance.addEarlyKeyEventHandler(_handleKeyEvent)`; custom bindings read per action from `'shortcut_${entry.key}'` with `defaultShortcuts` fallback: `playorpause [' ']`, `forward ['Arrow Right']`, `rewind ['Arrow Left']`, `next ['N']`, `prev ['P']`, `volumeup ['Arrow Up']`, `volumedown ['Arrow Down']`, `togglemute ['M']`, `fullscreen ['F']`, `exitfullscreen ['Escape']`, `toggledanmaku ['D']`, `screenshot ['S']`, `skip ['K']`, `speed1/2/3 ['1','2','3']`, `speedup ['X']`, `speeddown ['Z']`. Skips: `buttonSkipTime` default `80`s, `arrowKeySkipTime` default `10`s. |
| **Background / audio_service** | Yes | `AudioController` → `AudioService.init(builder: () => _KazumiAudioHandler(), config: AudioServiceConfig(androidNotificationChannelId: 'io.github.Predidit.Kazumi.channel.audio', androidNotificationChannelName: 'Kazumi Playback', androidNotificationOngoing: true))`; Linux MPRIS via `AudioServiceMpris.init(dBusName: 'io.github.Predidit.Kazumi.channel.audio', identity: 'Kazumi Playback', …)`. `backgroundPlayback` setting default **`false`**; when false `AppLifecycleState.paused` → `setPrefetchSuspended(true)` + pause. |
| **Low-memory / cache** | Yes | See §8.4. `demuxer-max-bytes` / `demuxer-max-back-bytes` = 2 MiB (low-memory) or 1500 MiB (default). |
| **Proxy** | Yes | mpv `http-proxy` set from `SettingsKeys.proxyEnable`/`proxyUrl` via `ProxyUtils.getFormattedProxyUrl`, else `SystemProxyService.proxyFor('https')` → `'http://host:port'`. |
| **Volume / brightness** | Yes | `flutter_volume_controller` on mobile, `mediaPlayer.setVolume` on desktop; `playerMuted`, `defaultVolume`; 80 ms coalescing timer. Brightness: `ScreenBrightnessPlatform.instance.setApplicationScreenBrightness(value)`. |
| **Audio track** | Forced auto | `await player.setAudioTrack(AudioTrack.auto());` — no user-facing audio track selection. |

## 3.4 Complete mpv property/command set used by Kazumi

| Property / command | Value |
|---|---|
| `demuxer-cache-dir` | `await getPlayerTempPath()` (forces media-kit's on-disk dual cache on all platforms) |
| `af` | `scaletempo2=max-speed=8` |
| `volume-max` (Android) | `100` |
| `ao` (Android) | `opensles` or `audiotrack` |
| `http-proxy` | configured/system proxy |
| `demuxer-lavf-format` | `hls` |
| `cache-secs` (Android) | `0` suspended / `36000` normal |
| `demuxer-readahead-secs` (Android) | `0` suspended / `1` normal |
| `demuxer-max-bytes`, `demuxer-max-back-bytes` | buffer size string |
| `change-list glsl-shaders set <paths>` / `change-list glsl-shaders clr ''` | Anime4K |

Source: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/player/controller/player_playback_controller.dart`, `.../lib/services/player/playback_cache_policy.dart`

## 3.5 Player file inventory (`lib/pages/player/`)

`player_controller.dart`, `player_item.dart` (60 KB), `player_item_panel.dart`, `player_item_surface.dart`, `player_transport_bar.dart`, `player_adjustment_hud.dart`, `player_gesture_detector.dart`, `player_keyboard_shortcuts.dart`, `player_panel_hold.dart`, `player_pointer_interaction.dart`, `player_screenshot_controls.dart`, `player_screenshot_feedback_overlay.dart`, `player_screenshot_image.dart`, `player_screenshot_sheet.dart`, `video_details_sheet.dart`, `syncplay_sheet.dart`, `danmaku_source_sheet.dart`, `danmaku_destination_sheet.dart`, `episode_comments_*.dart`, and `controller/`: `player_aspect_ratio.dart`, `player_danmaku_controller.dart`, `player_debug_controller.dart`, `player_models.dart`, `player_panel_controller.dart`, `player_playback_controller.dart`, `player_screenshot_controller.dart`, `player_seek_controller.dart`, `player_super_resolution.dart`, `player_syncplay_controller.dart`.

---

# 4. Danmaku

## 4.1 Service and API endpoints

Service = **弹弹play (DanDanPlay) open platform** (README: *"本项目使用了 弹弹play开放平台 以提供弹幕交互"*). Base + endpoints in `lib/request/config/api_endpoints.dart`:
```dart
  /// 弹弹官网
  static const String dandanIndex = 'https://www.dandanplay.com/';
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

| `DanmakuApi` method (`lib/request/apis/danmaku_api.dart`) | URL | Query |
|---|---|---|
| `getDanDanBangumiIDByBgmBangumiID(int)` | `/api/v2/bangumi/bgmtv/{bgmId}` | — |
| `getDanDanEpisodesByDanDanBangumiID(int)` | `/api/v2/bangumi/{danDanId}` | — |
| `searchAnimes(String title)` | `/api/v2/search/episodes` | `{'anime': title, 'v2': 'true'}` |
| `getDanDanmaku(int bangumiID, int episode)` | `/api/v2/comment/{bangumiID}{episode.padLeft(4,'0')}` | `{'withRelated': 'true', 'chConvert': '0|1|2'}` |
| `getDanDanmakuByEpisodeID(int episodeID)` | `/api/v2/comment/{episodeID}` | same |

```dart
static Future<List<DanmakuEntry>> getDanDanmaku(int bangumiID, int episode) async {
  if (bangumiID == 0) { return []; }
  // Automatic matching relies on an undocumented episode ID convention.
  return _getComments('$bangumiID${episode.toString().padLeft(4, '0')}');
}
```

**Auth/signature** (`lib/request/clients/danmaku_client.dart` + `lib/utils/crypto.dart`): `X-Auth: 1`, `X-AppId`, `X-Timestamp` (seconds), `X-Signature` =
```dart
String generateDandanSignature(String path, int timestamp) {
  final id = dandanCredentials['id']!;
  final value = dandanCredentials['value']!;
  final data = id + timestamp.toString() + path + value;
  final digest = sha256.convert(utf8.encode(data));
  return base64Encode(digest.bytes);
}
```
= `base64(sha256(appId + timestampSeconds + uri.path + appSecret))`; the **query string is not signed**. Credentials (`lib/utils/dandan_credentials.dart`):
```dart
const Map<String, String> dandanCredentials = {
  'id': String.fromEnvironment('DANDANAPI_APPID'),
  'value': String.fromEnvironment('DANDANAPI_KEY'),
};
```
— **no in-repo defaults**; repo-wide grep for `DANDANAPI_APPID|DANDANAPI_KEY` matches only this file, and `.github/workflows/pr.yaml` / `release.yaml` contain no `dart-define`, so both resolve to `''` in this public snapshot. `DanmakuClient` exposes **only `get` — no `post`**.

Sources: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/request/config/api_endpoints.dart`, `.../lib/request/apis/danmaku_api.dart`, `.../lib/request/clients/danmaku_client.dart`, `.../lib/utils/crypto.dart`, `.../lib/utils/dandan_credentials.dart`

## 4.2 Episode matching

- **Automatic (default): Bangumi subject id → DanDanPlay `bangumiId` → comment id `bangumiId + episode.padLeft(4,'0')`.** Not by filename; the video filename is never sent.
- Episode number source: `EpisodeRef.danmakuEpisodeNumber`, parsed from the **episode display title**:
```dart
int extractEpisodeNumber(String input) {
  final regExp = RegExp(r'第?(\d+)[话集]?');
  final match = regExp.firstMatch(input);
  if (match != null && match.group(1) != null) return int.tryParse(match.group(1)!) ?? 0;
  return 0;
}
```
with fallback `listIndex`. Comment in `danmaku_api.dart`: *"Automatic matching relies on an undocumented episode ID convention."*
- **Manual (source sheet): keyword → `/api/v2/search/episodes` → user picks anime → `/api/v2/bangumi/{animeId}` → user picks episode → `/api/v2/comment/{episode.episodeId}`** (`showDanmakuSourceSheet(...)`, `PlayerDanmakuController.getDanDanmakuByEpisodeID(episodeId)`).
- Download/offline playback: `_OfflineEpisodeRef` uses the stored `episodeNumber`; cached danmaku read from `danmaku.json`.

## 4.3 Model and parsing — JSON only, NO XML

```dart
class DanmakuEntry {
  String message;  // 弹幕内容
  double time;     // 弹幕时间
  int type;        // 弹幕类型 (1-普通弹幕，4-底部弹幕，5-顶部弹幕)
  Color color;     // 弹幕颜色
  String source;   // 弹幕来源 ([BiliBili], [Gamer])

  factory DanmakuEntry.fromJson(Map<String, dynamic> json) {
    String messageValue = json['m'];
    List<String> parts = json['p'].split(',');
    double timeValue = double.parse(parts[0]);
    int typeValue = int.parse(parts[1]);
    Color color = generateDanmakuColor(int.parse(parts[2]));
    String sourceValue = parts[3];
    return DanmakuEntry(time: timeValue, message: messageValue, type: typeValue, color: color, source: sourceValue);
  }

  Map<String, dynamic> toJson() {
    final colorValue = ((color.r * 255).toInt() << 16) | ((color.g * 255).toInt() << 8) | (color.b * 255).toInt();
    return { 'm': message, 'p': '$time,$type,$colorValue,$source' };
  }
}
```
- JSON keys consumed: **`m` and `p` only**. **There is NO `cid` field** (repo-wide grep for `cid` → no matches) and no user/`u` field.
- `p` = **4 parts** (`time`, `type`, `color`, `source`) — *not* DanDanPlay's 8-part `p`.
- Colour: `generateDanmakuColor(int v)` → `Color.fromARGB(255, (v>>16)&0xFF, (v>>8)&0xFF, v&0xFF)` (`lib/utils/danmaku.dart`).
- **NO XML parsing anywhere**: grep for `xml|Xml` across all Dart files matches only the `danmakuOnSvg` constant in `constants.dart`. Comments come from `jsonData['comments']`.
- Episode metadata models: `DanmakuEpisode {episodeId, episodeTitle}`; `DanmakuEpisodeResponse {bangumiId, episodes, errorCode, success, errorMessage}` reading `json['bangumi']['animeId']`, `json['bangumi']['episodes']`. Search models: `DanmakuSearchAnime {animeId, animeTitle, typeDescription}`; `DanmakuSearchResponse {animes, hasMore}`.

Sources: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/modules/danmaku/danmaku_module.dart`, `.../lib/modules/danmaku/danmaku_episode_response.dart`, `.../lib/modules/danmaku/danmaku_search_response.dart`, `.../lib/utils/danmaku.dart`, `.../lib/utils/media.dart`

## 4.4 Rendering — `canvas_danmaku 0.3.3`

`canvas_danmaku: ^0.3.3` (lock: `version: "0.3.3"`). Imports `package:canvas_danmaku/canvas_danmaku.dart`, `package:canvas_danmaku/models/danmaku_content_item.dart`, and `as canvas` in the controller (`late canvas.DanmakuController canvasController;`).
```dart
child: DanmakuScreen(
  key: _danmuKey,
  createdController: (DanmakuController e) {
    playerController.danmaku.canvasController = e;
    WidgetsBinding.instance.addPostFrameCallback((_) { playerController.updateDanmakuSpeed(); });
  },
  option: DanmakuOption(
    hideTop: _hideTop, hideScroll: _hideScroll, hideBottom: _hideBottom,
    area: _danmakuArea, opacity: _opacity, fontSize: _fontSize,
    duration: _danmakuDuration, lineHeight: _danmakuLineHeight,
    strokeWidth: _border ? _danmakuBorderSize : 0.0,
    fontWeight: _danmakuFontWeight, massiveMode: _massiveMode,
    fontFamily: _danmakuUseSystemFont ? null : customAppFontFamily,
  ),
),
```
Emission: 1-second `Timer.periodic` → buckets from `Map<int, List<DanmakuEntry>> danDanmakus` by integer second → `canvasController.addDanmaku(DanmakuContentItem(danmaku.message, color: color, type: _danmakuItemType(danmaku)))`; type map: `4 → DanmakuItemType.bottom`, `5 → DanmakuItemType.top`, else `DanmakuItemType.scroll`. Same-second stagger: `index * 1000 ~/ total` ms (`DanmakuTimeline.staggerDelayMilliseconds`). Speed coupling: `duration = followSpeed ? (baseDuration / playerSpeed) : baseDuration` then `canvasController.updateOption(option.copyWith(duration: duration))`.

Controller: `PlayerDanmakuController` (= `_PlayerDanmakuController with _$PlayerDanmakuController`) with `fetchDanmaku`, `beginDanmakuLoad`, `applyDanmakuLoad`, `applyUnavailableDanmakuLoad`, `finishDanmakuLoad`, `getDanDanmakuByEpisodeID`, `addDanmakus`, `updateDanmakuSpeed`, `danmakusForPlaybackPosition`, `resolveDanmakuSecond`, `clearAndInvalidateScheduledDanmakus`, `setDanmakuEnabled`; `enum DanmakuLoadStatus { success, empty, failed }`; `DanmakuLoadResult`.

Appearance settings keys: `danmakuEnhance, danmakuBorder, danmakuBorderSize, danmakuOpacity, danmakuFontSize, danmakuTop, danmakuScroll, danmakuBottom, danmakuMassive, danmakuDeduplication, danmakuChConvert, danmakuArea, danmakuColor, danmakuDuration, danmakuLineHeight, danmakuTimeOffset, danmakuEnabledByDefault, danmakuBiliBiliSource, danmakuGamerSource, danmakuDanDanSource, danmakuFontWeight, danmakuFollowSpeed`.

Sources: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/player/player_item.dart`, `.../lib/pages/player/controller/player_danmaku_controller.dart`, `.../lib/pages/settings/danmaku/danmaku_settings_sheet.dart`, `.../pubspec.lock`

## 4.5 Shield / blocking rules

Storage: Hive box **`shieldList`** (`static late Box<String> shieldList;` … `_openBoxSafe<String>('shieldList')`). Validation (`lib/modules/danmaku/danmaku_shield_rule.dart`):
```dart
abstract final class DanmakuShieldRule {
  static const maxLength = 64;
  static DanmakuShieldRuleError? validate(String rule) {
    if (rule.isEmpty) return DanmakuShieldRuleError.empty;
    if (rule.length > maxLength) return DanmakuShieldRuleError.tooLong;
    return null;
  }
}
```
Matching (`lib/pages/my/my_controller.dart`):
```dart
bool isDanmakuBlocked(String? danmaku) {
  if (danmaku == null || danmaku.isEmpty) return false;
  for (String item in shieldList) {
    if (item.isEmpty) continue;
    if (item.startsWith('/') && item.endsWith('/')) {
      if (item.length <= 2) continue;
      String pattern = item.substring(1, item.length - 1);
      try { if (RegExp(pattern).hasMatch(danmaku)) return true; }
      catch (_) { KazumiLogger().e('Danmaku: invalid danmaku shield regex pattern: $pattern'); continue; }
    } else {
      if (danmaku.contains(item)) return true;
    }
  }
  return false;
}
```
→ `/…/`-wrapped rules are case-sensitive Dart `RegExp`; anything else is a case-sensitive substring test. Editor hint `'关键词或 /正则表达式/'`, helper `'包含关键词的弹幕会被隐藏。用 / / 包裹正则表达式。'`.
- **Type/mode shielding**: `DanmakuOption.hideScroll` / `hideTop` / `hideBottom` (ui 滚动/顶部/底部).
- **Source shielding**: `_isDanmakuSourceEnabled` with `danmakuBiliBiliSource` / `danmakuGamerSource` / `danmakuDanDanSource` (BiliBili/Gamer/else→DanDan).
- **User blocking: 未查到** — no per-uid blocking; the model has no user field.
- **Dedup** (`lib/utils/danmaku.dart`, `mergeDuplicateDanmakus(list, {timeWindowSeconds = 0})`): normalizes text (`0x3000`→space, `0xFF01–0xFF5E`→ASCII, strip `\s+`, strip outside `[\w\u4e00-\u9fff\u3040-\u309F\u30A0-\u30FF\u31F0-\u31FF\uFF65-\uFF9F]`, collapse `(.)\1{2,}`→3), and for groups ≥2 emits `DanmakuEntry(message: '${first.message} x${len}', type: 5, color: generateDanmakuColor(0xFFFFFF), …)`. Enabled by `SettingsKeys.danmakuDeduplication`, called with `timeWindowSeconds: 5`.
- **Cloud sync of shields**: `lib/modules/danmaku/danmaku_shield_sync.dart` — `DanmakuShieldSyncEntry{rule, updatedAt, deviceId, deleted}` LWW by `updatedAt` → `deviceId` → `deleted`; `DanmakuShieldSyncState.encode()` = `jsonEncode({'version': 1, 'entries': [...]})`; per-device files at `/kazumiSync/danmakuShield/<32-hex>.json`; `lib/services/sync/danmaku_shield_sync_service.dart` debounces 500 ms.
- **Chinese conversion** (`danmaku_ch_convert.dart`): `enum DanmakuChConvert { none(0), simplified(1), traditional(2) }` — **server-side only** via the `chConvert` query param; no local conversion table.

Sources: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/modules/danmaku/danmaku_shield_rule.dart`, `.../lib/pages/my/my_controller.dart`, `.../lib/utils/danmaku.dart`, `.../lib/modules/danmaku/danmaku_ch_convert.dart`, `.../lib/modules/danmaku/danmaku_shield_sync.dart`, `.../lib/pages/player/player_item.dart`, `.../lib/services/storage/storage.dart`

## 4.6 Posting danmaku

`DanmakuClient` has **no `post`**, and no DanDanPlay send endpoint exists. UI `enum DanmakuDestination { chatRoom, remoteDanmaku }`:
```dart
  if (destination == DanmakuDestination.chatRoom) {
    ...
    unawaited(playerController.sendSyncPlayChatMessage(message));
  } else {
    // This provider has no send API; display a local echo.
    playerController.danmaku.canvasController.addDanmaku(DanmakuContentItem(message, selfSend: true));
  }
```
→ chat room transmits over **SyncPlay** only; "视频弹幕 / 发送至远程弹幕库" is an explicit **local echo**. Limits: non-empty, ≤100 chars (`'弹幕内容过长'`, `'当前剧集不支持弹幕发送的说'`).

Source: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/player/player_item_panel.dart`, `.../lib/pages/player/danmaku_destination_sheet.dart`

---

# 5. Storage / favorites / history

## 5.1 DB technology and exact box names

**Hive Community Edition** — `hive_ce: ^2.16.0`, `hive_ce_flutter: ^2.3.3` (generator `hive_ce_generator: ^1.10.0`); lock: `hive_ce 2.19.3`, `hive_ce_flutter 2.3.4`. No SQL, no `shared_preferences`, no `sqflite`. Entry: `lib/main.dart` → `final hivePath = '${(await getApplicationSupportDirectory()).path}/hive'; await Hive.initFlutter(hivePath); await GStorage.init();`.

**`lib/services/storage/storage.dart` (class `GStorage`) — the ONLY place boxes are opened** (not `core_module.dart`, which only wires DI):
```dart
  /// Don't use favorites box, it's replaced by collectibles.
  static late Box<BangumiItem> favorites;
  static late Box<CollectedBangumi> collectibles;
  static late Box<History> histories;
  static late Box<CollectedBangumiChange> collectChanges;
  static late Box<String> shieldList;
  static late final Box<dynamic> _setting;
  static late Box<SearchHistory> searchHistory;
  static late Box<DownloadRecord> downloads;
```
```dart
    _hivePath = '${(await getApplicationSupportDirectory()).path}/hive';
    Hive.registerAdapters();
    favorites = await _openBoxSafe<BangumiItem>('favorites');
    collectibles = await _openBoxSafe<CollectedBangumi>('collectibles');
    histories = await _openBoxSafe<History>('histories');
    _setting = await _openBoxSafe<dynamic>('setting');
    collectChanges = await _openBoxSafe<CollectedBangumiChange>('collectchanges');
    shieldList = await _openBoxSafe<String>('shieldList');
    searchHistory = await _openBoxSafe<SearchHistory>('searchHistory');
    downloads = await _openBoxSafe<DownloadRecord>('downloads');
```
**The 8 persisted box names: `favorites`, `collectibles`, `histories`, `setting`, `collectchanges`, `shieldList`, `searchHistory`, `downloads`.** Mismatches vs. the task's guesses: there is **no `bangumi` box** (BangumiItem is embedded in `collectibles`/`histories`), **no `webdav` box** (config lives in `setting`), no `settings` (it is singular **`setting`**), no `download` (it is `downloads`), no `shield` (it is `shieldList`). `favorites` is legacy and migrated on startup by `CollectController.migrateCollect()` from `InitPage._migrateStorage()`. Temp boxes read from raw bytes: `'tempHistoryBox'`, `'tempCollectiblesBox'`, `'tempCollectChangesBox'`. Corruption recovery: `_openBoxSafe` deletes `'$_hivePath/$boxName.hive'` + `'$boxName.lock'` and reopens empty.

## 5.2 Stored models (typeIds and fields, from the generated adapters)

Adapter registry `lib/hive_registrar.g.dart`: `BangumiItemAdapter, BangumiTagAdapter, CollectedBangumiAdapter, CollectedBangumiChangeAdapter, DownloadEpisodeAdapter, DownloadRecordAdapter, HistoryAdapter, ProgressAdapter, SearchHistoryAdapter`.

| Class | File | typeId | Fields (HiveField index → name) |
|---|---|---|---|
| `BangumiItem` | `lib/modules/bangumi/bangumi_item.dart` | **0** | 0 `id`, 1 `type`, 2 `name`, 3 `nameCn`, 4 `summary`, 5 `airDate`, 6 `airWeekday`, 7 `rank`, 8 `images`(Map<String,String>), 9 `tags`(List<BangumiTag>), 10 `alias`(List<String>), 11 `ratingScore`(double), 12 `votes`(int), 13 `votesCount`(List<int>), 14 `info` |
| `BangumiTag` | `lib/modules/bangumi/bangumi_tag.dart` | **4** | 0 `name`, 1 `count`, 2 `totalCount` |
| `CollectedBangumi` | `lib/modules/collect/collect_module.dart` | **3** | 0 `bangumiItem`, 1 `time`(DateTime), 2 `type`(int); `String get key => bangumiItem.id.toString();` |
| `CollectedBangumiChange` | `lib/modules/collect/collect_change_module.dart` | **5** | 0 `id`, 1 `bangumiID`, 2 `action` (1 add / 2 update / 3 delete), 3 `type`, 4 `timestamp` |
| `History` | `lib/modules/history/history_module.dart` | **1** | 0 `progresses`(Map<int,Progress>), 1 `lastWatchEpisode`, 2 `adapterName`, 3 `bangumiItem`, 4 `lastWatchTime`(DateTime), 5 `lastSrc`, 6 `lastWatchEpisodeName`, 7 `entryKind`, 8 `episodePageUrl` |
| `Progress` | same | **2** | 0 `episode`, 1 `road`, 2 `_progressInMilli`, 3 `updatedAtMs`; `Duration get/set progress` |
| `SearchHistory` | `lib/modules/search/search_history_module.dart` | **6** | 0 `keyword`, 1 `timestamp`; `key => timestamp.toString()` |
| `DownloadRecord` | `lib/modules/download/download_module.dart` | **7** | 0 `bangumiId`, 1 `bangumiName`, 2 `bangumiCover`, 3 `pluginName`, 4 `episodes`(Map<int,DownloadEpisode>), 5 `createdAt`; `key => '${pluginName}_$bangumiId'` |
| `DownloadEpisode` | same | **8** | 0 `episodeNumber`, 1 `episodeName`, 2 `road`, 3 `status`, 4 `progressPercent`, 5 `totalSegments`, 6 `downloadedSegments`, 7 `localM3u8Path`, 8 `downloadDirectory`, 9 `networkM3u8Url`, 10 `completedAt`, 11 `errorMessage`, 12 `totalBytes`, 13 `episodePageUrl`, 14 `danmakuData`, 15 `danDanBangumiID` |

Non-Hive: `WatchStats` (derived value object in `lib/modules/my/watch_stats.dart`), `DanmakuShieldSyncEntry`/`DanmakuShieldSyncState` (JSON only). Enums: `CollectType` (0 none/1 watching/2 planToWatch/3 onHold/4 watched/5 abandoned), `CollectLayout`, `HistoryEntryKind` (`'online'`/`'offline'`) with `scopedKey(n, s, entryKind) = '${legacyKey(n,s)}::${HistoryEntryKind.normalize(entryKind)}'`, `DownloadStatus {pending:0, resolving:1, downloading:2, completed:3, failed:4, paused:5}`.
Settings are plain keys in the `setting` box (`GStorage.getSetting/putSetting` with typed `SettingKey<T>`; `GStorage.resetSettings/resetPlayerSettings/resetDanmakuSettings`).

Sources: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/storage/storage.dart`, `.../lib/hive_registrar.g.dart`, `.../lib/modules/bangumi/bangumi_item.dart`, `.../lib/modules/collect/collect_module.dart`, `.../lib/modules/collect/collect_change_module.dart`, `.../lib/modules/history/history_module.dart`, `.../lib/modules/search/search_history_module.dart`, `.../lib/modules/download/download_module.dart`, `.../lib/services/storage/settings_keys.dart`

## 5.3 WebDAV / cross-device sync

Client: `webdav_client: ^1.2.2` used as `webdav.newClient(webDavURL, user: webDavUsername, password: webDavPassword, debug: false)` + `client.setHeaders({'accept-charset': 'utf-8'})`, `client.c.options.contentType = 'application/octet-stream'`, `await client.ping()`. `WebDav` is a singleton. Credentials stored **unencrypted** in the `setting` box under keys `webDavURL`, `webDavUsername`, **`webDavPasswd`** (note: not `webDavPassword`), plus `webDavEnable`, `webDavEnableHistory`, `webDavEnableCollect`, `webDavEnableDanmakuShield`, and `bangumiAccessToken`.

**Remote layout (verbatim):**
```dart
static const String _syncRootPath = '/kazumiSync';
static const String _historyRootPath = '$_syncRootPath/history';
static const String _historyChangesPath = '$_historyRootPath/changes';
static const String _historySnapshotPath = '$_historyRootPath/snapshot.json';
static const String _danmakuShieldPath = '$_syncRootPath/danmakuShield';
```
| Remote path | Content |
|---|---|
| `/kazumiSync/collectibles.tmp` | byte copy of local `collectibles.hive` (`File(localFilePath).copy(tempFilePath)`) |
| `/kazumiSync/collectchanges.tmp` | byte copy of `collectchanges.hive` (uploaded only `if (GStorage.collectChanges.isNotEmpty)`) |
| `/kazumiSync/histories.tmp` | **legacy** one-way import of the old history box |
| `/kazumiSync/history/snapshot.json` | history snapshot JSON (`schemaVersion: 1`) |
| `/kazumiSync/history/changes/<deviceId>.jsonl` | per-device JSON-Lines event log |
| `/kazumiSync/danmakuShield/<32-hex>.json` | shield rules (readers accept only `RegExp(r'^[0-9a-f]{32}\.json$')`) |

Atomic publish: `WebDavRemoteFileCommitter.replaceFile` = remove temp → `writeFromFile(source, temp)` → remove destination → `rename(temp, destination)`, temp = `'$destinationPath.cache'`.

**Encryption: 未查到.** `grep -iE "encrypt|decrypt|AES|cipher"` over `lib/` → zero matches; `Hive.openBox` is never given `encryptionCipher`/`HiveAesCipher`; uploads are byte-for-byte copies of plaintext Hive files plus plaintext JSON/JSONL. `lib/utils/crypto.dart` is API request signing (`generateDandanSignature`, `generateBangumiMirrorSearchSignature`, `calculateFileHash`) — not storage encryption.

**Merge algorithms:**
- **Collectibles (bidirectional, tombstones)** — `CollectSyncMerger.mergeWebDav({localCollectibles, localChanges, remoteCollectibles, remoteChanges})` → `CollectiblesMergeResult{collectibles, changes}`: base = remote collectibles; replay local changes whose `id` is absent remotely, sorted by `timestamp`; `action == 3` removes, `1` adds/replaces, `2` replaces if present; merged change map = `{remote..., local-new...}`. Applied by `GStorage.patchCollectibles(...)` (clears + rewrites both boxes, `flush()`, resets the change-id counter).
- **Collectibles vs Bangumi.tv** — `CollectSyncMerger.planBangumi({localCollectibles, remoteCollections, priority})` → `BangumiCollectiblesMergePlan{localOnlyUploads, remoteOnlyPuts, conflictUploads, conflictLocalUpdates}`; policy `BangumiSyncPriority { localFirst(0,'本地优先'), bangumiFirst(1,'Bangumi优先'), timeFirst(2,'最新优先') }` (local wins ties). Driven by `lib/services/sync/bangumi_sync_service.dart` → `BangumiApi.updateBangumiByType(...)`.
- **History (event-sourced LWW with tombstones)** — `lib/modules/history/history_sync.dart`: `HistorySyncOp { upsertProgress, upsertWatchState, deleteHistory, clearAll }`; version string `'${updatedAt.padLeft(16,'0')}|$eventId'` compared lexicographically; `HistorySyncState.apply`, `HistorySyncMerger.merge({snapshot, events})`, `HistorySyncStreamMerger` (keeps only latest per slot); tombstones in `deletedVersions` plus a global `clearVersion` gate (`_isNewerThanClear`); snapshot JSON `{'schemaVersion': 1, 'generatedAt', 'histories', 'itemVersions', 'progressVersions', 'deletedVersions', if (clearVersion != null) 'clearVersion'}`.
- **Danmaku shield** — union merge with per-rule winner by `updatedAt` → `deviceId` → `deleted`; per-device files "prevent simultaneous uploads from losing edits".

**Direction/triggers:** collectibles = download both remote boxes → merge → write back to local **and** re-upload; history = download snapshot + all `changes/*.jsonl` → merge with local logs → apply locally → checkpoint (`snapshot.json`, delete own log) or upload own log, with `checkpointLogThresholdBytes = 1024 * 1024`; shield = read all device files, merge, publish own. Serialization: `AsyncSerialQueue _webDavOperationQueue`, `AsyncSingleFlight<void> _historySyncSingleFlight`, `HistoryStorageCoordinator`, `GStorage._runCollectChangesWriteExclusive`. Triggers: startup `InitPage._webDavInit()`, playback `player_item.dart#_syncHistoryWithWebDav`, manual `lib/pages/settings/sync/webdav_sync_page.dart`, collect dialog `CollectSyncStep.{webDav,bangumi,upload}`. Corrupt remote event logs are quarantined as `'<path>.invalid.<millis>'`; stale `history-sync-run-*` dirs older than 24 h are deleted.

**Not synced:** the `setting` box, `searchHistory`, `downloads`, raw `shieldList`, and `favorites`.

Sources: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/sync/webdav.dart`, `.../lib/services/sync/webdav_remote_file_commit.dart`, `.../lib/services/sync/history_sync_service.dart`, `.../lib/services/sync/bangumi_sync_service.dart`, `.../lib/modules/collect/collect_sync_merger.dart`, `.../lib/modules/collect/collect_sync_plan.dart`, `.../lib/modules/history/history_sync.dart`, `.../lib/modules/bangumi/sync_priority.dart`, `.../lib/pages/settings/sync/webdav_sync_page.dart`, `.../lib/pages/settings/sync/webdav_server_page.dart`

## 5.4 File import/export of storage — 未查到

No user-facing DB backup/restore/export UI. `lib/pages/settings/storage_settings.dart` is image-cache clearing + an error-log link only; `lib/pages/error/storage_error_page.dart` only shows the app-support path. Storage-layer helpers exist but are mostly dead code: `GStorage.backupBox(boxName, path)` and `GStorage.restoreCollectibles(path)` have **no callers**; only `getCollectiblesFromFile`, `getCollectChangesFromFile` and `patchHistory` are called (all from `webdav.dart`). The WebDAV upload is the de-facto export.

---

# 6. Update / notification

**Bottom line: a weekly timetable exists; new-episode detection and episode notifications do NOT.**

## 6.1 Timetable — exists

Route `/timeline` (`lib/pages/router.dart`), registered in `lib/pages/index_module.dart` (`..addSingleton<TimelineController>(TimelineController.new)`, `..module(timelineModule)`), localized `'/tab/timeline/': '时间表'`.
```dart
return DefaultTabController(
  length: 7,
  initialIndex: DateTime.now().weekday - 1,
  animationDuration: MediaQuery.disableAnimationsOf(context) ? Duration.zero : null,
  child: _buildContent(context),
);
```
`TimelineController.loadSeason` uses `BangumiApi.getCalendar()` for the current season, else `_getSchedulesBySeason`; `isSameSeason`, `enum TimelineSort { popularity, rating, defaultOrder }`; filters `notShowAbandonedBangumis`, `notShowWatchedBangumis`, `onlyShowWatchingBangumis`.

**Bangumi endpoints** (`lib/request/config/api_endpoints.dart`): `bangumiAPIDomain = 'https://api.bgm.tv'`, `bangumiAPINextDomain = 'https://next.bgm.tv'`, `bangumiMirrorDomain = 'https://api.kazumi.fyi'`, `bangumiAuthAPIMirrorDomain = 'https://api.bgmapi.com'`, `bangumiCalendar = '/p1/calendar'`, `bangumiTrendsNext = '/p1/trending/subjects'`, `bangumiMirrorSeasonCalendar = '/kazumi/v1/calendar/season'`, `bangumiInfoByID = '/v0/subjects/{0}'`, `bangumiEpisodeByID = '/v0/episodes'`, `bangumiGetAllCollections = '/v0/users/{0}/collections?subject_type=2&limit={1}&offset={2}'`.
- `getCalendar()` → GET `https://next.bgm.tv/p1/calendar`, reading `jsonData['$i']` for i = 1..7 → `BangumiItem.fromJson(jsonItem['subject'])`.
- `getCalendarBySearch(dateRange, limit, offset)` → POST `https://api.bgm.tv/v0/search/subjects?limit={0}&offset={1}` with body `{"keyword":"","sort":"rank","filter":{"type":[2],"tag":["日本"],"air_date":[">=${dateRange[0]}","<${dateRange[1]}"],"rank":[">0","<=99999"],"nsfw":true}}`, bucketed by `bangumiItem.airWeekday`.
- `getBangumiMirrorSeasonCalendar(dateRange)` → GET `https://api.kazumi.fyi/kazumi/v1/calendar/season?start=..&end=..` (only when `BangumiAcceleration.current == mirror`).
- `lib/utils/anime_season.dart`: `AnimeSeason`, `toSeasonStartAndEnd()`, `getSeasonStringByMonth`, `isSameSeason`; `toString()` → `'${year}年${冬季|春季|夏季|秋季}新番'`.
- `lib/utils/date_time.dart`: formatting only (`formatTimestampToRelativeTime` → `'N年前|N个月前|N天前|N小时前|N分钟前|刚刚'`, `dateFormat`, `dateStringToWeekday`, `formatDate`, `formatDateTime`). **No scheduling or comparison logic.**
- `lib/bean/card/bangumi_timeline_card.dart`: `BangumiTimelineCard` displays an episode count scraped from `bangumiItem.info` via `static final _episodePattern = RegExp(r'^\s*([1-9]\d*)\s*[话話集](?=\s*(?:[/／]|$))');` — **display only; nothing compares it over time.**

## 6.2 New-episode / "today's updates" detection — 未查到

- No logic anywhere diffs episode counts, airdates or "last seen" values. `ICollectRepository` exposes only the three timeline booleans plus Bangumi collection sync.
- README feature checklist (verbatim):
```
- [X]  一起看
- [X]  番剧下载
- [ ]  番剧更新提醒
- [ ]  还有更多 (/・ω・＼)
```
  (contrast `- [X]  番剧时间表` and `- [X]  番剧弹幕`).

## 6.3 Scheduled tasks / local notifications — 未查到

| Sought | Result |
|---|---|
| `flutter_local_notifications` | **未查到** (absent from pubspec and tree) |
| `workmanager` | **未查到** |
| `background_fetch` / `flutter_background_fetch` | **未查到** |
| `awesome_notifications` / `local_notifier` | **未查到** |
| Any periodic episode check | **未查到** — repo-wide `Timer.periodic` has exactly 3 hits, all unrelated: `lib/webview/video/impl/video_webview_impl.dart` (1 s video parser), `lib/pages/player/player_item.dart` (1 s player tick), `lib/services/player/timed_shutdown_service.dart` (1 s sleep timer) |
| Notification toggle in settings | **未查到** — `lib/pages/settings/interface_settings.dart` has only 启动 / 展示信息 / 窗口行为 |

`flutter_foreground_task: ^9.0.0` **is** present but is **downloads only** (`lib/services/download/background_download_service.dart`, channel `'kazumi_download_channel'`, title `'正在下载'`, button `pause_all`); the other notification is the audio_service media notification.

## 6.4 The only "update check" is the app self-updater

`lib/pages/init_page.dart`:
```dart
unawaited(runStartupUpdateCheck(
  isEnabled: () => GStorage.getSetting(SettingsKeys.autoUpdate),
  checkForUpdate: () async { await updateController.checkUpdate(type: 'auto'); },
));
```
`lib/services/update/startup_update_check.dart`: `const startupUpdateCheckDelay = Duration(milliseconds: 500);`. `lib/services/update/auto_updater.dart`: `class AutoUpdater` singleton with `checkForUpdates()`, `autoCheckForUpdates()`, `manualCheckForUpdates()`, `enum InstallationType { windowsMsix, windowsPortable, linuxDeb, linuxTar, macosDmg, androidApk, ios, unknown }`, SHA-256 verification, source `ApiEndpoints.latestAppMirror` = `'$bangumiMirrorDomain/kazumi/v1/app/latest'` = `https://api.kazumi.fyi/kazumi/v1/app/latest` (also `latestApp = 'https://api.github.com/repos/Predidit/Kazumi/releases/latest'`). UI `lib/pages/settings/update_settings.dart` (`UpdateSettingsPage`, route `/settings/update`) has exactly two switches: **应用更新** (`autoUpdate`) and **规则更新** (`checkPluginUpdateOnStartup`). `upgrader: ^12.3.0` is declared in `pubspec.yaml` but **referenced by no Dart file**.

---

# 7. Super resolution (Anime4K)

## 7.1 Shader files

`assets/shaders/` contains exactly 9 `.glsl` files + `LICENSE` (registered as a Flutter asset `- assets/shaders/`):

| file | bytes |
|---|---|
| `Anime4K_AutoDownscalePre_x2.glsl` | 1560 |
| `Anime4K_AutoDownscalePre_x4.glsl` | 1568 |
| `Anime4K_Clamp_Highlights.glsl` | 2795 |
| `Anime4K_Restore_CNN_M.glsl` | 35916 |
| `Anime4K_Restore_CNN_S.glsl` | 17136 |
| `Anime4K_Restore_CNN_VL.glsl` | 144075 |
| `Anime4K_Upscale_CNN_x2_M.glsl` | 37685 |
| `Anime4K_Upscale_CNN_x2_S.glsl` | 18638 |
| `Anime4K_Upscale_CNN_x2_VL.glsl` | 146743 |
| `LICENSE` | 1062 |

These are **mpv `.glsl` user shaders with `//!` hook directives** (not `.hook` files): e.g. `Anime4K_Clamp_Highlights.glsl` starts `//!DESC Anime4K-v4.0-De-Ring-Compute-Statistics` / `//!HOOK MAIN` / `//!BIND HOOKED` / `//!SAVE STATSMAX` / `//!COMPONENTS 1`, then `//!DESC Anime4K-v4.0-De-Ring-Clamp` / `//!HOOK PREKERNEL`; `Anime4K_Restore_CNN_*.glsl` = `//!DESC Anime4K-v4.0-Restore-CNN-*-Conv-4x3x3x3` / `//!HOOK MAIN`; `Anime4K_Upscale_CNN_x2_*.glsl` = `//!DESC Anime4K-v3.2-Upscale-CNN-x2-*-Conv-4x3x3x3` with `//!WHEN OUTPUT.w MAIN.w / 1.200 > OUTPUT.h MAIN.h / 1.200 > *`; `Anime4K_AutoDownscalePre_x4.glsl` uses `//!WIDTH OUTPUT.w 2 /`. CNN/Clamp shaders carry `// MIT License … Copyright (c) 2019-2021 bloc97`; the two `AutoDownscalePre` shaders carry the Unlicense text.

## 7.2 Exact shader lists (verbatim, `lib/utils/constants.dart` lines 133–150)

```dart
/// 超分辨率滤镜
const List<String> mpvAnime4KShaders = [
  'Anime4K_Clamp_Highlights.glsl',
  'Anime4K_Restore_CNN_VL.glsl',
  'Anime4K_Upscale_CNN_x2_VL.glsl',
  'Anime4K_AutoDownscalePre_x2.glsl',
  'Anime4K_AutoDownscalePre_x4.glsl',
  'Anime4K_Upscale_CNN_x2_M.glsl'
];

/// 超分辨率滤镜 (轻量)
const List<String> mpvAnime4KShadersLite = [
  'Anime4K_Clamp_Highlights.glsl',
  'Anime4K_Restore_CNN_M.glsl',
  'Anime4K_Restore_CNN_S.glsl',
  'Anime4K_Upscale_CNN_x2_M.glsl',
  'Anime4K_AutoDownscalePre_x2.glsl',
  'Anime4K_AutoDownscalePre_x4.glsl',
  'Anime4K_Upscale_CNN_x2_S.glsl'
];
```
Note `mpvAnime4KShadersLite` (7 entries) is **longer** than `mpvAnime4KShaders` (6): "efficiency" = M/S restore+upscale; "quality" = VL restore + VL upscale + final M upscale. Both begin with `Anime4K_Clamp_Highlights.glsl`.

## 7.3 Profiles and the DEFAULT

```dart
enum SuperResolutionMode {
  off(storageValue: 1, label: '关闭', description: '默认禁用超分辨率'),
  efficiency(storageValue: 2, label: '效率档', description: '默认启用基于Anime4K的超分辨率 (效率优先)'),
  quality(storageValue: 3, label: '质量档', description: '默认启用基于Anime4K的超分辨率 (质量优先)');
  ...
  static SuperResolutionMode fromStorageValue(int value) =>
      SuperResolutionMode.values.firstWhere((mode) => mode.storageValue == value, orElse: () => SuperResolutionMode.off);
}
```
```dart
static const defaultSuperResolutionMode = SettingKey<int>(
  _SettingBoxKey.defaultSuperResolutionMode,   // 'defaultSuperResolutionType'
  1,
  group: SettingGroup.player,
);
static const disableSuperResolutionWarning = SettingKey<bool>(
  _SettingBoxKey.disableSuperResolutionWarning, // 'superResolutionWarn'
  false,
  group: SettingGroup.player,
);
```
Runtime field: `SuperResolutionMode superResolutionMode = SuperResolutionMode.off;` then `superResolutionMode = SuperResolutionMode.fromStorageValue(GStorage.getSetting(SettingsKeys.defaultSuperResolutionMode));`

**→ Super resolution is OFF by default** (stored `1` = `off`; unknown values also fall back to `off`). The enum label literals call mode 2/3 "默认启用…" but that text is per-option description, not the default.

## 7.4 How it is applied — exact mpv command

```dart
Future<void> setShader(SuperResolutionMode mode, {Player? player}) async {
  final currentPlayer = player ?? mediaPlayer;
  if (currentPlayer == null) return;
  try {
    var pp = currentPlayer.platform as NativePlayer;
    await pp.waitForPlayerInitialization;
    await pp.waitForVideoControllerInitializationIfAttached;
    if (!identical(mediaPlayer, currentPlayer)) { return; }
    switch (mode) {
      case SuperResolutionMode.efficiency:
        await pp.command(['change-list', 'glsl-shaders', 'set',
          buildShadersAbsolutePath(shaderAssetService.shadersDirectory.path, mpvAnime4KShadersLite)]);
        break;
      case SuperResolutionMode.quality:
        await pp.command(['change-list', 'glsl-shaders', 'set',
          buildShadersAbsolutePath(shaderAssetService.shadersDirectory.path, mpvAnime4KShaders)]);
        break;
      case SuperResolutionMode.off:
        await pp.command(['change-list', 'glsl-shaders', 'clr', '']);
        break;
    }
    superResolutionMode = mode;
  } catch (e) { KazumiLogger().w('PlayerController: failed to set shader', error: e); }
}
```
The path list is joined `;` on Windows and `:` elsewhere:
```dart
String buildShadersAbsolutePath(String baseDirectory, List<String> shaders) {
  final absolutePaths = shaders.map((shader) => path.join(baseDirectory, shader)).toList();
  if (Platform.isWindows) { return absolutePaths.join(';'); }
  return absolutePaths.join(':');
}
```
Auto-applied after controller creation when the stored mode is not `off`:
```dart
    if (superResolutionMode != SuperResolutionMode.off) {
      await setShader(superResolutionMode, player: player);
```
**未查到**: `libplacebo`, the literal `vo=gpu`/`vo=gpu-next` option string (renderer goes through `VideoControllerConfiguration.vo`), `profile=`, `user-shaders`, `glsl-shader-opts`, `libplacebo-opts`, and any `setProperty('glsl-shaders', ...)` call.

## 7.5 Runtime shader copy

`lib/services/shaders/shader_asset_service.dart` — `class ShaderAssetService` with `late Directory shadersDirectory;`:
```dart
    shadersDirectory = Directory(path.join(directory.path, 'anime_shaders'));
    ...
    final shaderFiles = assets.where((String asset) =>
        asset.startsWith('assets/shaders/') && asset.endsWith('.glsl'));
    ...
      final targetFile = File(path.join(shadersDirectory.path, fileName));
      if (await targetFile.exists()) { … 'GLSL Shader exists, skip' … continue; }
      final data = await rootBundle.load(filePath);
      final List<int> bytes = data.buffer.asUint8List();
      await targetFile.writeAsBytes(bytes);
```
Destination `<getApplicationSupportDirectory()>/anime_shaders`, via `writeAsBytes` + `rootBundle.load` (**not** `File(...).copy`), skipping existing files. Registered as a DI singleton and invoked from `InitPage._loadShaders()` — **which is called unawaited** in `_initializeApp()` (`_loadShaders();`), so a cold-start race with early playback is possible.

## 7.6 GPU requirements / warnings (verbatim)

- Settings page header: `Text('超分辨率需要启用硬件解码, 若启用硬件解码后仍然不生效, 尝试切换视频渲染器为 gpu')`; page `SuperResolutionSettings`, route `/settings/player/super`, radio over `SuperResolutionMode.values`, plus switch `'关闭提示'`/`'关闭每次启用超分辨率时的提示'`.
- Android `mediacodec_embed` guard in `handleSuperResolutionChange`:
```dart
    if (Platform.isAndroid && mode != SuperResolutionMode.off) {
      final String androidVideoRenderer = GStorage.getSetting(SettingsKeys.androidVideoRenderer);
      if (androidVideoRenderer == 'mediacodec_embed') {
        … title: const Text('兼容性提示'),
            content: const Text('MediaCodec 渲染器不支持超分辨率功能。\n\n'
                '如需使用超分辨率，请在播放设置中将视频渲染器切换为 gpu 或 gpu-next。'),
```
- Quality-mode confirmation: `final bool requiresPerformanceWarning = mode == SuperResolutionMode.quality;` → dialog `'性能提示'` / `'启用超分辨率（质量档）可能会造成设备卡顿，是否继续？'` with `Checkbox` `'下次不再询问'` writing `SettingsKeys.disableSuperResolutionWarning`.
- Renderer notes: `'gpu-next': '基于 Vulkan, 在新设备上表现最好'`, `'mediacodec_embed': '功耗最低，不支持超分辨率'`; auto-selection comment states GPU-NEXT *"需要 Vulkan 1.2 支持"* and is chosen from Android 14 (SDK ≥ 34).
- README Q&A: *"超分辨率功能对 GPU 性能要求较高, 如果没有在高性能独立显卡上运行 Kazumi, 尽量选择效率档而非质量档。"*
- **未查到** any explicit minimum-GPU/VRAM number, and no verification that hardware decoding is actually active before applying shaders.

Sources: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/utils/constants.dart`, `.../lib/pages/player/controller/player_super_resolution.dart`, `.../lib/pages/player/controller/player_playback_controller.dart`, `.../lib/pages/settings/super_resolution_settings.dart`, `.../lib/pages/player/player_item.dart`, `.../lib/services/shaders/shader_asset_service.dart`, `.../lib/utils/media.dart`, `.../lib/services/storage/settings_keys.dart`, `.../assets/shaders/<each file>`

---

# 8. Extras

## 8.1 Proxy / anti-hotlink handling (referer / user-agent injection)

- **Per-rule headers** come from the rule itself (`lib/plugins/plugins.dart`, `Plugin.userAgent` / `Plugin.referer`):
```dart
  /// Headers used when resolving or downloading the final media resource.
  Map<String, String> buildHttpHeaders() {
    return {
      'user-agent': userAgent.isEmpty ? getRandomUA() : userAgent,
      if (referer.isNotEmpty) 'referer': referer,
    };
  }
```
  Editor labels: `referer = '播放请求来源（Referer）'`, `refererHelper = '仅用于播放器和下载器。'`.
- **Video requests are not proxied locally.** Referer/UA are handed to mpv by media_kit: `player.open(Media(videoUrl(), start: Duration(seconds: offset), httpHeaders: httpHeaders), play: autoPlay)`, and the pinned fork converts `Media.httpHeaders` into the mpv property **`http-header-fields`** (`'$k: $v'` entries) in its `on_load` hook (`media_kit/lib/src/player/native/player/real.dart`, cleared on `on_unload`).
- **Rule-engine requests** always inject `'referer': '${config.baseUrl}/'` plus lower-cased rule headers, stored clearance cookies, and the WebView-verified UA (`lib/services/plugin/rule_engine.dart`). Dio defaults: `apiDio` `{'referer':'','user-agent':getRandomUA()}`, `pluginDio` + `accept-language`, `downloadDio` UA only (connect 15 s / receive 30 s), `rulesRepoDio` + `_RulesMirrorInterceptor`.
- **Random UA pool**: `getRandomUA()` / `getRandomAcceptedLanguage()` (`lib/utils/http_headers.dart`) over `userAgentsList` (9 UAs: Chrome 141/136/134, Edge 141/134/136, Firefox 125, Safari 17.5/16.1) and `acceptLanguageList` (3). Also `bangumiHTTPHeader` with the documented UA `'Predidit/Kazumi/${ApiEndpoints.version} (Android) (https://github.com/Predidit/Kazumi)'`.
- **Proxy**: dio via `NetworkConfig.createAdapter()` → `client.findProxy = (_) => 'PROXY $proxyHost:$proxyPort'`, else on Windows `SystemProxyService.findProxy`; `allowBadCertificates: true` forced when a proxy is set. mpv gets `http-proxy` (app setting, else system proxy). `ProxyManager.applyProxy()`/`clearProxy()` call `DioFactory.reset()` and re-point `CachedNetworkImageProvider.defaultCacheManager`; comment `// WebView proxies are configured separately by their platform controllers.` Android WebView uses `android_webview.AndroidProxyController.instance().setProxyOverride(settings: ProxySettings(proxyRules: [ProxyRule(url: formattedProxy)]))` after checking `WebViewFeature.PROXY_OVERRIDE`.
- **There is NO local HTTP proxy/relay server**: repo-wide grep for `HttpServer` and `.bind(` across `lib/**/*.dart` → **zero matches** (only `hintText: 'http://127.0.0.1:7890'` in the proxy editor and a `localhost`/`::1` check in `system_proxy_service.dart`).

Sources: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/plugins/plugins.dart`, `.../lib/services/plugin/rule_engine.dart`, `.../lib/request/core/dio_factory.dart`, `.../lib/request/core/network_config.dart`, `.../lib/utils/http_headers.dart`, `.../lib/utils/constants.dart`, `.../lib/services/network/proxy_manager.dart`, `.../lib/pages/player/controller/player_playback_controller.dart`, `.../lib/webview/video/impl/video_webview_impl.dart`, `.../lib/pages/video/video_controller.dart`, `https://raw.githubusercontent.com/Predidit/media-kit/803c4a27912091db6f839d48d1b984a0d9f588c7/media_kit/lib/src/player/native/player/real.dart`

## 8.2 HLS / m3u8 ad filtering and parsing

`M3u8AdFilter` (`lib/utils/m3u8_ad_filter.dart`, 86 lines) — heuristic is **discontinuity groups only**:
```dart
class M3u8AdFilter {
  /// Filter ad segments from a media playlist.
  /// Mimics FFmpeg hls_ad_filter behavior using discontinuity groups.
  static List<M3u8Segment> filterAds(List<M3u8Segment> segments) {
    // group by seg.discontinuityGroup; if (groups.length <= 1) return segments;
    // longest group = main content (maxDuration)
      if (groupDuration < maxDuration * 0.3) { isAd = true; }
      if ((groupId == sortedKeys.first || groupId == sortedKeys.last) && groupDuration < 30.0) { isAd = true; }
      if (groupDuration < 10.0) { isAd = true; }
    ...
    return segments.where((seg) => !adGroups.contains(seg.discontinuityGroup)).toList();
  }
  static double calculateTargetDuration(List<M3u8Segment> segments) { /* max segment duration */ }
}
```
**未查到**: `#EXT-X-CUE-OUT`/`CUE-IN`, keyword lists, segment-naming or URL-pattern ad rules, per-segment thresholds, domain blacklists.

`M3u8Parser` (`lib/utils/m3u8_parser.dart`) — `M3u8Key{method,uri,iv}`, `M3u8Segment{duration,uri,discontinuityGroup,key}`, `M3u8Variant{bandwidth,resolution,uri}`, `M3u8MasterPlaylist{bestVariant}` (max BANDWIDTH), `M3u8MediaPlaylist{segments,targetDuration,isVod}`, `enum M3u8Type { master, media }`; `detectType` (`#EXT-X-STREAM-INF`), `resolveUrl`, `extractUniqueKeys`, `resolveNestedSegments(segments, fetcher, {maxDepth = 3})`, `buildLocalM3u8(...)` emitting `#EXTM3U`, `#EXT-X-VERSION:3`, `#EXT-X-TARGETDURATION:${targetDuration.ceil()}`, `#EXT-X-MEDIA-SEQUENCE:0`, `#EXT-X-DISCONTINUITY`, `#EXT-X-KEY:METHOD=…`, `#EXTINF:<%.6f>,`, `seg_%05d.ts`, `#EXT-X-ENDLIST`. VOD rule:
```dart
    final bool isVod = hasEndList || isExplicitVod || (!isLiveEvent && segments.isNotEmpty);
```

**Where it applies (no server):** the **downloader** writes a filtered local playlist to disk:
```dart
      if (adBlockerEnabled) { segments = M3u8AdFilter.filterAds(segments); }
      ...
      final targetDuration = adBlockerEnabled ? M3u8AdFilter.calculateTargetDuration(segments) : resolvedPlaylist.targetDuration;
      await File(m3u8Path).writeAsString(localM3u8);
```
`M3u8AdFilter` is referenced **only** in `lib/services/download/download_manager.dart` (L473, L601). **Online playback** instead delegates to the pinned media_kit fork: `PlayerConfiguration(adBlocker: adBlockerEnabled)` → fork sets `demuxer-lavf-o` including `'hls_ad_filter=${configuration.adBlocker ? 1 : 0}'`. UI: rule editor `adBlocker = '广告过滤'` / `'启用 HLS 广告过滤'`; player setting `forceAdBlocker` = `'强制启用HLS广告过滤，忽略规则设置'` (default `false`), combined as `forceAdBlocker || currentPlugin.adBlocker`.

Sources: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/utils/m3u8_ad_filter.dart`, `.../lib/utils/m3u8_parser.dart`, `.../lib/services/download/download_manager.dart`, `.../lib/pages/video/video_controller.dart`, `.../lib/pages/plugin_editor/plugin_editor_page.dart`, `https://raw.githubusercontent.com/Predidit/media-kit/803c4a27912091db6f839d48d1b984a0d9f588c7/media_kit/lib/src/player/native/player/real.dart`

## 8.3 Video download

Engine `lib/services/download/download_manager.dart` (985 lines), `class DownloadManager implements IDownloadManager`:
- **Concurrent HLS segment downloader** (not a single mp4 fetch): `int maxParallelEpisodes = 2; int maxParallelSegments = 3;` loaded from `SettingsKeys.downloadParallelEpisodes` / `downloadParallelSegments`; `final semaphore = _Semaphore(maxParallelSegments);` (hand-rolled `_Semaphore` class).
- `_downloadSegmentWithRetry(url, savePath, headers, cancelToken, {int maxRetries = 3})`: downloads to `'$savePath.tmp'`, `await File(tmpPath).rename(savePath)`, retry backoff `Duration(seconds: [1, 3, 9][retryCount - 1])`.
- **Non-m3u8 fallback**: `_fetchM3u8` rejects content not starting with `#EXTM3U` (`throw _NotM3u8Exception('URL 不是 M3U8 播放列表')`) and `_runEpisodeDownload` catches it → `_runDirectFileDownload(...)` → `video.mp4` via `Range` resume.
- **Resume: yes.** Segment level skips existing non-empty `seg_NNNNN.ts` (scans `existingSegments`, then builds `pendingIndices`); direct-file level uses `requestHeaders['Range'] = 'bytes=$existingBytes-'` with `FileMode.append` (or `write` when 0) and recovers from 416 by deleting the `.tmp` and retrying without `Range`. Pause = `task.isPaused = true` + `cancelToken.cancel('paused')` → `DownloadStatus.paused`.
- **Output layout**: `_getEpisodeDir` = `path.join(downloadBase, '${bangumiId}_$pluginName', '$episodeNumber')`; HLS → `seg_00000.ts…`, `key_i.key`, `playlist.m3u8` (`episode.localM3u8Path`); direct → `video.mp4`; danmaku cache `danmaku.json` (`_danmakuFilePath` = `'$downloadDirectory/danmaku.json'`). Size accounting counts only `RegExp(r'^seg_\d+\.ts$')` (`calculateM3u8VideoBytes`).
- **Storage guard**: `static const _minRequiredSpace = 100 * 1024 * 1024; // 100MB minimum`; `MethodChannel('com.predidit.kazumi/storage')` → `getAvailableStorage`.
- **DB**: box `GStorage.downloads`; `DownloadRecord` (typeId 7) and `DownloadEpisode` (typeId 8) — see §5.2 for the full field list. `DownloadRepository` persists only on status change (`final shouldPersist = lastStatus != episode.status;`).
- **Platform save location**: `lib/services/download/download_directory_service.dart` — custom dir supported on Android/macOS/Windows; Android `MethodChannel('com.predidit.kazumi/download_directory')` (`requestStorageAccess`, `pickDirectory`) handled by `android/app/src/main/kotlin/com/example/kazumi/DownloadDirectoryHandler.kt` (`Intent.ACTION_OPEN_DOCUMENT_TREE` + `EXTRA_LOCAL_ONLY`, `Environment.isExternalStorageManager()` on API ≥ R, resolves only local volumes else `UNSUPPORTED_DIRECTORY`); elsewhere `FilePicker.platform.getDirectoryPath(dialogTitle: '选择下载位置', …)`; macOS security-scoped bookmarks (`downloadDirectoryBookmark`) with `'下载目录访问权限已失效，请在下载设置中重新选择该目录'`. Default root `<ApplicationSupportDirectory>/downloads` (`lib/utils/file_system.dart`).
- **`open_filex` is NOT used for downloaded videos** — the only `OpenFilex` hit in `lib/` is `lib/services/update/auto_updater.dart:672` (update package). Downloaded episodes play in-app via `download_page.dart::_playEpisode` → `context.pushNamed('/video/', arguments: OfflineVideoPlaybackArgs(...))`.
- **Anti-hotlink**: downloads **replicate** the rule headers rather than bypass them — `final httpHeaders = plugin.buildHttpHeaders(); bool adBlockerEnabled = _repository.getForceAdBlocker() || plugin.adBlocker;` and those headers are reused for playlist, AES keys and every segment.
- Foreground service (Android only): `lib/services/download/background_download_service.dart` via `flutter_foreground_task`, channel `'kazumi_download_channel'` / `'下载服务'`, `NotificationChannelImportance.LOW`, `allowWakeLock: true`, `allowWifiLock: true`, button `pause_all` (`'暂停全部'`), `FlutterForegroundTask.sendDataToMain({'action': 'navigate_to_download'})`. Permission dialog title `'需要通知权限'`.

Sources: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/download/download_manager.dart`, `.../lib/pages/download/download_controller.dart`, `.../lib/pages/download/download_page.dart`, `.../lib/services/download/download_directory_service.dart`, `.../lib/services/download/background_download_service.dart`, `.../lib/modules/download/download_module.dart`, `.../lib/repositories/download_repository.dart`, `.../lib/utils/file_system.dart`, `.../lib/utils/m3u8_parser.dart`, `.../android/app/src/main/kotlin/com/example/kazumi/DownloadDirectoryHandler.kt`

## 8.4 Cache / low-memory mode

```dart
  static const int _lowMemoryBufferSize = 2 * 1024 * 1024;      // 2 MiB
  static const int _defaultBufferSize = 1500 * 1024 * 1024;     // 1500 MiB
  int get bufferSize => LowMemoryMode.current.isEnabled(
        isMetered: MeteredNetworkService.isMetered,
        isLocalPlayback: _isLocalPlayback(),
      ) ? _lowMemoryBufferSize : _defaultBufferSize;
  ...
        final size = bufferSize.toString();
        await pp.setProperty('demuxer-max-bytes', size);
        await pp.setProperty('demuxer-max-back-bytes', size);
```
**Exact mpv properties: `demuxer-max-bytes` and `demuxer-max-back-bytes` — 2 MiB in low-memory mode, 1500 MiB (1 572 864 000 bytes) by default.** The same value is passed to `PlayerConfiguration(bufferSize: cachePolicy.bufferSize)`.
```dart
enum LowMemoryMode {
  auto('auto'), always('always'), never('never');
  static LowMemoryMode get current => switch (GStorage.getSetting(SettingsKeys.lowMemoryPolicy)) {
      'always' => always, 'never' => never,
      null => GStorage.getSetting(SettingsKeys.lowMemoryMode) ? always : auto,
      _ => auto,
    };
  bool isEnabled({required bool isMetered, bool isLocalPlayback = false}) => switch (this) {
      auto => isMetered && !isLocalPlayback, always => true, never => false,
    };
}
```
Keys: `lowMemoryPolicy = SettingKey<String?>('lowMemoryPolicy', null, group: SettingGroup.player)` (default **`null`**) and legacy `lowMemoryMode = SettingKey<bool>(_SettingBoxKey.lowMemoryMode, false, …)`. Toast on auto-enable: `'移动数据下已自动开启低内存模式，可在播放设置中改为始终关闭'`.
**未查到** any `cacheSize` / `demuxerMaxBytes` / `demuxerMaxBackBytes` identifier, any user-facing video buffer-size setting, or a `bufferSize` settings key (the only "cache" setting is image cache in `storage_settings.dart`).

Sources: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/player/playback_cache_policy.dart`, `.../lib/services/player/low_memory_mode.dart`, `.../lib/pages/player/controller/player_playback_controller.dart`, `.../lib/services/storage/settings_keys.dart`, `.../lib/pages/settings/low_memory_mode_settings.dart`

## 8.5 Search aggregation / dedup / source alias

**Aggregation** — all rules queried in parallel by `PluginSearchService.queryAllSource` (`Future.wait`), each with an `AsyncSessionOwner`; results appended to `InfoController.pluginSearchResponseList` in **completion order**; status per rule in `pluginSearchStatus`.

**Dedup — exact code (`lib/pages/info/source_sheet.dart`):**
```dart
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
- **Per-source dedup only**, keyed on the raw `SearchItem.src` string via an exact `Set<String>`. **Normalization used: NONE** — no `toLowerCase`, no `trim`, no URL canonicalization, no title/fuzzy matching (`lib/utils/string_match.dart` does not exist — 404).
- **No cross-source dedup and no merged/ranked list**: the same title from three rules appears three times, grouped per source. Group order follows `pluginList` (configured) order; within a group, the rule's own order.
- Group labels (`source_sheet_view.dart`): `'检索中…'`, `'N 个结果'` / `'无结果'`, `'检索失败'`, `'需要验证'`; header `'检索中 N/M · K 个结果'` or `'M 个来源 · K 个结果'`; per-group menu 修改此来源的检索词 / 使用别名检索此来源 / 重新检索此来源 / 在浏览器中打开.
- Unrelated pagination dedup by subject id exists for Bangumi lists: `final existingIds = bangumiList.map((item) => item.id).toSet(); final newItems = page.items.where((item) => existingIds.add(item.id)).toList();` (`lib/pages/search/search_controller.dart`).
- `lib/utils/search_parser.dart` is **not** a source-result parser — it is the Bangumi query DSL (`_fieldNames = 'id|tag|sort|season|date|rank|score|weekday|nsfw'`; `toFilterState()`, `fromFilterState()`, `seasonToDateRange()`).

**Source alias** — `lib/pages/info/source_alias_dialog.dart` is `part of 'source_sheet.dart'`:
```dart
  void _showAliasPicker(String pluginName) {
    if (widget.infoController.bangumiItem.alias.isEmpty) {
      KazumiDialog.showToast(message: '无可用别名，试试修改检索关键词'); return;
    }
    _showAliasPickerDialog(
      sourceName: pluginName,
      aliases: widget.infoController.bangumiItem.alias,
      onAliasSelected: (alias) => _querySource(alias, pluginName),
      onAliasesChanged: () => _collectController.updateLocalCollect(widget.infoController.bangumiItem),
    );
  }
```
Aliases are the Bangumi item's own alias list (`BangumiItem.alias`, Hive field 10), **shared across all sources** (`// Aliases are shared across source rules.`); selecting one re-queries **only that source**. Per-source keyword overrides are in-memory only: `final Map<String, String> _sourceKeywords = {};` with `_keywordFor(name) => _sourceKeywords[name] ?? _keyword;` and base `_keyword = item.nameCn.isEmpty ? item.name : item.nameCn`. Custom keyword dialog `'修改检索词'` with `helperText: '仅检索来源：$sourceName'` persists new keywords into `item.alias`.

Sources: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/pages/info/source_sheet.dart`, `.../lib/pages/info/source_sheet_view.dart`, `.../lib/pages/info/source_alias_dialog.dart`, `.../lib/services/plugin/plugin_search_service.dart`, `.../lib/pages/search/search_controller.dart`, `.../lib/utils/search_parser.dart`

## 8.6 Logging / error handling

`logger: ^2.6.2` (+ `synchronized: any`). `lib/services/logging/logger.dart` — `KazumiLogger` singleton wrapping `Logger(filter: KazumiLogFilter(), printer: KazumiLogPrinter(), output: KazumiLogOutput())`; API `t/d/i/w/e/f(message, {error, stackTrace, forceLog = false})`, `forceLog` via `runZoned(..., zoneValues: {_forceLogKey: true})`.
- Filter: `return event.level.index >= Logger.level.index;` (package default level).
- File output: only when `event.level.index >= Level.warning.index || forceLog`; path `<ApplicationSupportDirectory>/logs/kazumi_logs.log`; `FileMode.writeOnlyAppend`; serialized by `static final Lock _logLock = Lock();`; ANSI stripping `RegExp(r'\x1B\[[0-9;]*m')`; fatal appends ≤ `_fatalStackFrameLimit = 8` frames.
- **Retention/rotation: 未查到** — no `rotation`/`maxLogSize`/age cleanup anywhere; only `clearLogs()`.
- UI `lib/pages/logs/logs_page.dart`: `_initialLoadCount = 50`, `_loadMoreCount = 100`, 80 % scroll threshold, 「清空日志」/「复制日志」.
- HTTP logs: `lib/request/core/dio_logger_interceptor.dart` (`'HTTP: --> METHOD uri'`, `'HTTP: <-- status … Nms'`), gated by `config.enableLog`.
- Separate mpv log level: `playerLogLevel` default `2`; `playerLogLevelMap = {0:"错误",1:"警告",2:"简略",3:"详细"}` → `MPVLogLevel.values[debug.playerLogLevel]`.
- **Global error handling: 未查到.** `lib/main.dart` has **no** `runZonedGuarded`, **no** `FlutterError.onError`, **no** `PlatformDispatcher.instance.onError`; the only guard is a try/catch around Hive init that renders `StorageErrorPage` (`runApp(MaterialApp(title: '初始化失败', …))`). The single `FlutterError` reference in `lib/` is `FlutterError.reportError(FlutterErrorDetails(` in `lib/pages/player/player_keyboard_shortcuts.dart`. No crash reporting / telemetry (README: *"我们不收集任何用户数据, 不使用任何遥测组件。"*).
- `lib/request/core/network_error_mapper.dart` maps `DioException` to Chinese user messages (`'证书有误！'`, `'服务器异常，请稍后重试！'`, `'连接错误，请检查网络设置'`, `'网络连接超时，请检查网络设置'`, `'响应超时，请稍后重试！'`, `'$connection 网络异常'` via `connectivity_plus` labels, `'响应解析失败'`).
- `lib/bean/settings/network_mirror_settings.dart` is the 「访问加速」 section (`NetworkMirrorSettings`): 番剧条目加速 (`direct`/`ech`/`mirror`), 规则仓库镜像 (`enableGitProxy`), 图片加速 — not logging.

Sources: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/logging/logger.dart`, `.../lib/main.dart`, `.../lib/pages/logs/logs_page.dart`, `.../lib/request/core/dio_logger_interceptor.dart`, `.../lib/request/core/network_error_mapper.dart`, `.../lib/bean/settings/network_mirror_settings.dart`, `.../lib/services/storage/settings_keys.dart`

## 8.7 "一起看" (SyncPlay)

Files: `lib/services/player/syncplay_client.dart` (951 lines), `lib/services/player/syncplay_endpoint.dart`, `lib/pages/player/controller/player_syncplay_controller.dart`, `lib/pages/player/syncplay_sheet.dart` (807 lines). (`lib/utils/syncplay.dart` does not exist.)
- **Protocol**: file header `// https://syncplay.pl/about/protocol/`; `joinRoom` sends `version: '1.7.0'`.
```dart
/// Official endpoint selected for new and reset settings.
const String defaultSyncPlayEndPoint = 'syncplay.pl:8996';

/// Official public endpoints that must use STARTTLS.
const Set<String> officialSyncPlayEndPoints = {
  'syncplay.pl:8995', defaultSyncPlayEndPoint, 'syncplay.pl:8997', 'syncplay.pl:8998', 'syncplay.pl:8999',
};
```
  `SyncPlayEndPoint{host,port}`, `parseSyncPlayEndPoint` (trims, `[IPv6]:port`, port 1..65535), `isOfficialSyncPlayEndPoint` (normalizes `'${host.toLowerCase()}:${port}'`).
- **Messages** (`syncplay_client.dart`): `SyncplayMessage` (abstract), `HelloMessage` → `{"Hello":{"username":…,"room":{"name":room},"version":"1.7.0","features":{"sharedPlaylists":true,"chat":true,"featureList":true,"readiness":true,"managedRooms":false}}}`; `StateMessage` → `{"State":{"ignoringOnTheFly":{…},"ping":{clientRtt, clientLatencyCalculation, latencyCalculation},"playstate":{position, paused, setBy, doSeek}}}`; `SetMessage` → `{"Set":{room:{"room":{"name":room},"event":{"joined":true}}}}` / `{"Set":{"ready":{"isReady":true,"manuallyInitiated":false}}}` / `{"Set":{"file":{"duration":…,"name":…,"size":…},"user":{…}}}`; `ChatMessage` → `{"Chat": message}`; private `_TLSMessage` → `{"TLS":{"startTLS":"send"}}`. Received `TLS` requires `'true'` (else "server rejected TLS connection upgrade"); `State` **replies with a State** (ping loop); unknown → `SyncplayProtocolException('SyncPlay: unknown message type')`.
- **Transport**: raw `RawSocket.connect`, JSON + `'\r\n'`, brace-counted framing, STARTTLS via `RawSecureSocket.secure`; `_tlsHandshakeTimeout = 10s`, `_socketWriteTimeout = 10s`; RTT smoothing `_pingMovingAverageWeight = 0.85`, forward delay `_fd = _avrRtt/2 + (clientRtt - serverRtt)` when `serverRtt < clientRtt` else `_avrRtt/2`.
- **User list**: no roster message — presence from `Set.user[<username>].event.keys.first` (`joined`/`left`), surfaced as `'SyncPlay: 您是当前房间中的唯一用户'` (then becomes the source of truth) or `'SyncPlay: 您不是当前房间中的唯一用户, 当前以用户 X 进度为准'`.
- **Host/guest**: single `createRoom(room, username, changeEpisode)` path → `parseSyncPlayEndPoint` → `enableTLS = isOfficialSyncPlayEndPoint(parsed)` → `client.connect(enableTLS:)` → `client.joinRoom(room, username)`. Whoever finds the room empty announces `await client.setSyncPlayPlaying("${bangumiId()}[${currentEpisode()}]", 10800, 220514438);` (file name encodes `bangumiId[episode]`; hard-coded duration 10800 / size 220514438). Guests parse `RegExp(r'(\d+)\[(\d+)\]')` → `changeEpisode(episode, currentRoad: currentRoad())`, mirror pause/play with `enableSync: false`, and seek when `(playerPosition - calculatedPositon*1000).abs() > 1000` ms or `doSeek`. Observable `syncplayClientRtt` shown as 「网络延迟」.
- **UI**: `showSyncPlaySheet(context, {required PlayerController playerController, required Future<void> Function(int episode, {int currentRoad, int offset}) changeEpisode})`, `enum _SyncPlayDestination { create, join, server }`, 「一起看」/「和朋友同步看番」; room number `List.generate(8, (_) => _random.nextInt(10)).join()`; nickname from `'bcdfghjklmnpqrstvwxyz'`/`'aeiou'`; validation `RegExp(r'^[0-9]{6,10}$')` → `'房间号为 6-10 位数字'` and `RegExp(r'^[a-zA-Z]{4,12}$')` → `'昵称为 4-12 位英文字母'`; server sheet lists the 5 official endpoints + 「自定义服务器」 (hint `'example.com:8996'`, error `'地址格式为 host:port'`).

Sources: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/services/player/syncplay_client.dart`, `.../lib/services/player/syncplay_endpoint.dart`, `.../lib/pages/player/controller/player_syncplay_controller.dart`, `.../lib/pages/player/syncplay_sheet.dart`

## 8.8 trace.moe image search

```dart
  /// 图片识别番剧
  static const String traceApi = 'https://api.trace.moe/search';
```
```dart
  static Future<ImageSearchItem> searchAnimeByImageFile(File imageFile, {int anilistInfo = 2}) async {
    final bytes = await imageFile.readAsBytes();
    final data = await _client.post(
      ApiEndpoints.traceApi,
      queryParameters: {'anilistInfo': anilistInfo},
      data: bytes,
      headers: {'Content-Type': 'image/jpeg'},
    );
    return ImageSearchItem.fromJson(data as Map<String, dynamic>);
  }
  static Future<ImageSearchItem> searchAnimeByImageUrl(String imageUrl, {int anilistInfo = 2}) async {
    final data = await _client.post(ApiEndpoints.traceApi,
      queryParameters: {'anilistInfo': anilistInfo, 'url': imageUrl});
    return ImageSearchItem.fromJson(data as Map<String, dynamic>);
  }
```
- **Request format is NOT multipart**: `POST https://api.trace.moe/search?anilistInfo=2` with **raw image bytes** as the body and `Content-Type: image/jpeg`; the URL variant adds `url=<imageUrl>`. No `FormData` anywhere. `TraceClient.post` uses `DioFactory.apiDio` (thus `{'referer':'', 'user-agent': getRandomUA()}`).
- **Parsing** (`lib/modules/search/image_search_module.dart`): `ImageSearchItem {frameCount, error, result}`; `ResultItem {anilist, filename, episode (num | List<num> | null), from, at, to, duration, similarity, video, image}`; `Anilist {id, type, idMal, title{native,romaji,chinese,english}, format, genres, season, source, status, endDate, startDate, isAdult, siteUrl, studios, duration, episodes, synonyms, relations, seasonInt, coverImage{...}, popularity, seasonYear, bannerImage, externalLinks, countryOfOrigin, synonymsChinese}` (wire key `json['synonyms_chinese']`).
- **Mapping to Bangumi — by title text, not id**:
```dart
String _title(ResultItem result) {
  final title = result.anilist?.title;
  for (final value in [title?.chinese, title?.native, title?.romaji, title?.english, result.filename]) {
    if (value != null && value.trim().isNotEmpty) return value.trim();
  }
  return '未知番剧';
}
```
  Results are sorted by `similarity` descending; selecting one pops the title (`onSelect: (title) => context.pop(title)`) which is then submitted as a normal Bangumi search keyword. **`anilist.id` / `idMal` are parsed but 未查到 any code using them to look up a Bangumi subject.**
- Extras: `_episode` → `'第 N 集'`/`'第 N / M 集'`/`'集数未知'`; `_similarity` → `'NN.N%'`; low-confidence flag `similarity < .87`; disclosure `'识别由 trace.moe 提供。开始识别后，所选图片或图片链接会发送至该服务。'`
- **image_picker**: `image_picker: ^1.2.1`; `final _picker = ImagePicker(); await _picker.pickImage(source: ImageSource.gallery);` (gallery only), rejects `> 25 * 1024 * 1024` → `'图片超过 25 MB，请换一张较小的截图'`; hint `'JPG、PNG、WebP · 最大 25 MB'`. Grep for `image_picker|ImagePicker|ImageSource` matches only `image_search_page.dart` (+ the unrelated `_ImageSourceSelector` widget) — **`lib/pages/my/my_space_view.dart` and `lib/pages/index_module.dart` contain no image-picker usage** (未查到).

Sources: `https://raw.githubusercontent.com/Predidit/Kazumi/main/lib/request/config/api_endpoints.dart`, `.../lib/request/apis/trace_api.dart`, `.../lib/request/clients/trace_client.dart`, `.../lib/modules/search/image_search_module.dart`, `.../lib/pages/search/image_search_page.dart`, `.../lib/pages/search/image_search_widgets.dart`

---

# Source URL index

**Repo metadata / config**
`https://api.github.com/repos/Predidit/Kazumi` · `.../branches` · `https://raw.githubusercontent.com/Predidit/Kazumi/main/pubspec.yaml` · `.../pubspec.lock` · `.../.gitmodules` · `.../README.md` · `.../android/app/src/main/AndroidManifest.xml` · `.../ios/Runner/Info.plist` · `.../android/app/src/main/kotlin/com/example/kazumi/DownloadDirectoryHandler.kt`

**§1 Rule format**
`lib/plugins/plugins.dart` · `lib/plugins/api_rule_config.dart` · `lib/plugins/anti_crawler_config.dart` · `lib/plugins/plugins_controller.dart` · `lib/plugins/plugin_validity_tracker.dart` · `lib/plugins/plugin_install_time_tracker.dart` · `lib/services/plugin/plugin_import_parser.dart` · `lib/services/plugin/xpath_rule_strategy.dart` · `lib/services/plugin/api_rule_strategy.dart` · `lib/services/plugin/rule_engine_models.dart` · `lib/utils/encoding.dart` · `lib/utils/version.dart` · `lib/utils/episode_url.dart` · `lib/request/config/api_endpoints.dart` · `lib/request/apis/plugin_catalog_api.dart` · `lib/request/clients/rules_repo_client.dart` · `lib/modules/plugin/plugin_http_module.dart` · `lib/pages/plugin_editor/plugin_editor_page.dart` · `lib/pages/plugin_editor/plugin_view_page.dart` · `lib/pages/plugin_editor/rule_dialogs.dart` · `lib/pages/plugin_editor/plugin_shop_page.dart` · `lib/pages/plugin_editor/plugin_catalog_view.dart` · `lib/pages/plugin_editor/plugin_test_page.dart` · `assets/plugins/7sefun.json` · `assets/plugins/DM84.json` · `https://raw.githubusercontent.com/Predidit/KazumiRules/main/index.json`

**§2 Query/parser**
`lib/services/plugin/rule_engine.dart` · `lib/services/plugin/api_rule_engine.dart` · `lib/services/plugin/plugin_search_service.dart` · `lib/services/plugin/plugin_cookie_manager.dart` · `lib/services/plugin/captcha_verification_service.dart` · `lib/request/clients/plugin_site_client.dart` · `lib/request/core/dio_factory.dart` · `lib/modules/search/plugin_search_module.dart` · `lib/modules/roads/road_module.dart` · `lib/webview/video/video_webview_controller.dart` · `lib/webview/video/impl/video_webview_impl.dart` · `lib/webview/video/impl/video_webview_android_impl.dart` · `lib/webview/video/impl/video_webview_apple_impl.dart` · `lib/webview/video/impl/video_webview_windows_impl.dart` · `lib/webview/video/impl/video_webview_linux_impl.dart` · `lib/webview/captcha/captcha_webview_controller.dart` · `lib/webview/captcha/impl/captcha_webview_inappwebview_impl.dart` · `lib/services/video_source/video_source_service.dart` · `lib/services/video_source/webview_video_source_service.dart` · `lib/services/video_source/video_source_resolver_pool.dart` · `lib/services/video_source/video_source_format.dart` · `lib/utils/media.dart` · `lib/pages/video/video_controller.dart` · `lib/pages/info/source_captcha_flow.dart`

**§3 Player**
`lib/pages/player/player_controller.dart` · `player_item.dart` · `player_item_panel.dart` · `player_item_surface.dart` · `player_transport_bar.dart` · `player_adjustment_hud.dart` · `player_gesture_detector.dart` · `player_keyboard_shortcuts.dart` · `video_details_sheet.dart` · `syncplay_sheet.dart` · `lib/pages/player/controller/player_playback_controller.dart` · `player_models.dart` · `player_panel_controller.dart` · `player_debug_controller.dart` · `player_aspect_ratio.dart` · `player_screenshot_controller.dart` · `lib/services/player/{playback_cache_policy,low_memory_mode,pip_utils,remote,external_player,external_playback_launcher,audio_controller,screenshot_export_service}.dart` · `lib/pages/settings/{player_settings,decoder_settings,renderer_settings,low_memory_mode_settings,settings_module}.dart` · `lib/utils/constants.dart` · `https://raw.githubusercontent.com/Predidit/media-kit/803c4a27912091db6f839d48d1b984a0d9f588c7/media_kit/lib/src/player/platform_player.dart` · `.../media_kit_video/lib/src/video_controller/platform_video_controller.dart`

**§4 Danmaku**
`lib/request/apis/danmaku_api.dart` · `lib/request/clients/danmaku_client.dart` · `lib/utils/crypto.dart` · `lib/utils/dandan_credentials.dart` · `lib/utils/danmaku.dart` · `lib/modules/danmaku/{danmaku_module,danmaku_episode_response,danmaku_search_response,danmaku_ch_convert,danmaku_shield_rule,danmaku_shield_sync}.dart` · `lib/pages/player/controller/player_danmaku_controller.dart` · `lib/pages/player/{danmaku_source_sheet,danmaku_destination_sheet,player_item,player_item_panel}.dart` · `lib/pages/settings/danmaku/{danmaku_module,danmaku_settings,danmaku_settings_sheet,danmaku_shield_settings,danmaku_shield_settings_sheet,danmaku_shield_editor,danmaku_ch_convert_tile,danmaku_time_offset_sheet}.dart` · `lib/pages/my/my_controller.dart` · `lib/repositories/danmaku_shield_repository.dart` · `lib/services/sync/danmaku_shield_sync_service.dart` · `lib/pages/download/download_controller.dart`

**§5 Storage**
`lib/services/storage/{storage,settings_keys,history_storage_coordinator}.dart` · `lib/hive_registrar.g.dart` · `lib/main.dart` · `lib/core_module.dart` · `lib/services/sync/{webdav,webdav_remote_file_commit,history_sync_service,bangumi_sync_service,danmaku_shield_sync_service}.dart` · `lib/modules/{bangumi/bangumi_item,bangumi/bangumi_tag,bangumi/sync_priority,collect/collect_module,collect/collect_change_module,collect/collect_sync_merger,collect/collect_sync_plan,collect/collect_type,collect/collect_layout,history/history_module,history/history_sync,search/search_history_module,download/download_module,my/watch_stats}.dart` · `lib/repositories/{collect_repository,collect_crud_repository,history_repository,download_repository,search_history_repository,danmaku_shield_repository}.dart` · `lib/pages/init_page.dart` · `lib/pages/collect/{collect_page,collect_controller,collect_sync_dialog}.dart` · `lib/pages/settings/sync/{webdav_sync_page,webdav_server_page,bangumi_sync_page}.dart` · `lib/pages/settings/storage_settings.dart` · `lib/pages/error/storage_error_page.dart`

**§6 Update/notification**
`lib/pages/timeline/{timeline_page,timeline_controller,timeline_module,timeline_options,timeline_week_selector}.dart` · `lib/request/apis/bangumi_api.dart` · `lib/utils/{anime_season,date_time}.dart` · `lib/bean/card/bangumi_timeline_card.dart` · `lib/pages/{index_module,index_page,router,init_page}.dart` · `lib/services/update/{auto_updater,startup_update_check}.dart` · `lib/services/download/background_download_service.dart` · `lib/services/player/{audio_controller,timed_shutdown_service}.dart` · `lib/pages/settings/{update_settings,interface_settings}.dart` · `lib/repositories/collect_repository.dart`

**§7 Super resolution**
`lib/utils/constants.dart` · `lib/utils/media.dart` · `lib/pages/player/controller/{player_super_resolution,player_playback_controller}.dart` · `lib/pages/player/player_item.dart` · `lib/pages/settings/super_resolution_settings.dart` · `lib/services/shaders/shader_asset_service.dart` · `lib/services/storage/settings_keys.dart` · `assets/shaders/{Anime4K_Clamp_Highlights,Anime4K_Restore_CNN_S,Anime4K_Restore_CNN_M,Anime4K_Restore_CNN_VL,Anime4K_Upscale_CNN_x2_S,Anime4K_Upscale_CNN_x2_M,Anime4K_Upscale_CNN_x2_VL,Anime4K_AutoDownscalePre_x2,Anime4K_AutoDownscalePre_x4}.glsl` · `assets/shaders/LICENSE`

**§8 Extras**
`lib/utils/{http_headers,constants,m3u8_parser,m3u8_ad_filter,file_system,search_parser,episode_url}.dart` · `lib/plugins/plugins.dart` · `lib/services/plugin/rule_engine.dart` · `lib/request/core/{dio_factory,network_config,dio_logger_interceptor,network_error_mapper}.dart` · `lib/request/clients/{plugin_site_client,download_http_client,trace_client}.dart` · `lib/services/network/{proxy_manager,system_proxy_service}.dart` · `lib/services/download/{download_manager,download_directory_service,download_path_migration,background_download_service}.dart` · `lib/services/player/{playback_cache_policy,low_memory_mode,syncplay_client,syncplay_endpoint}.dart` · `lib/services/logging/logger.dart` · `lib/services/plugin/plugin_search_service.dart` · `lib/pages/download/{download_controller,download_page,download_widgets,download_episode_sheet}.dart` · `lib/pages/info/{info_controller,source_sheet,source_sheet_view,source_alias_dialog}.dart` · `lib/pages/logs/logs_page.dart` · `lib/pages/search/{search_controller,image_search_page,image_search_widgets}.dart` · `lib/pages/player/controller/player_syncplay_controller.dart` · `lib/request/apis/trace_api.dart` · `lib/modules/search/image_search_module.dart` · `lib/bean/settings/network_mirror_settings.dart` · `https://raw.githubusercontent.com/Predidit/media-kit/803c4a27912091db6f839d48d1b984a0d9f588c7/media_kit/lib/src/player/native/player/real.dart`

**Directory listings used** (GitHub contents API, now rate-limited — capture early if re-running)
`https://api.github.com/repos/Predidit/Kazumi/contents/lib?ref=main` · `.../contents/lib/utils?ref=main` · `.../contents/lib/plugins?ref=main` · `.../contents/lib/pages/player?ref=main` · `.../contents/lib/webview?ref=main` · `.../contents/lib/services?ref=main` · `.../contents/lib/repositories?ref=main` · `.../contents/assets/shaders?ref=main` · `.../contents/assets/plugins?ref=main` · `https://api.github.com/repos/Predidit/Kazumi/git/trees/main?recursive=1`

**Tooling caveats**
- `git/trees/master` → **404** (no `master`); use `git/trees/main`.
- jsDelivr `/v1/package/gh/Predidit/Kazumi@main/flat` returns a **stale older commit** — do not use for path discovery.
- `api.github.com` unauthenticated hits HTTP 403 after ~10 requests; the `codeload.github.com/.../tar.gz/refs/heads/main` tarball (~17 MB) is the most reliable way to get the whole tree.
