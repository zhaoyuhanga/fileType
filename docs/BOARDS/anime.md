# 板块：墨软动漫（`anime`）

> **规则驱动**的番剧采集 + 追番 + 弹幕（第七大板块）
> 子页：search / library / schedule / rules / history

## 0. 定位与能力边界（先读这段）

> **没有内置可用的番剧源，也没有默认规则。** 随包两条 `rules_builtin/*.json` 是
> 指向 `127.0.0.1` / `example.com` 的**离线示例**（给自检和试跑面板用）。
> 要让动漫板块真的能搜到番，必须先导入或自己写一条规则 ——
> 详见 **第 3.0 节**（含 2026-10 对 KazumiRules 全量 16 条规则的真实实测结果）。

**与「墨软影视」不重叠**：影视是「聚合 CMS 源 + 下载入库」，动漫是「**规则驱动的番剧采集**
+ 追番 + 弹幕」。播放器、代理、缓存、下载器**全部复用共享层**，所以两边的播放/下载
流畅度一致 —— 动漫不重复造这套东西。

| 维度 | 墨软影视 | 墨软动漫 |
|---|---|---|
| 内容来源 | 苹果 CMS / 公开源（结构化接口） | 规则驱动的番剧站采集（XPath / CSS / JSON API） |
| 核心资产 | 多源聚合 + 下载入库 | 规则库 + 追番 + 弹幕（+ Bangumi 元数据） |
| 换源语义 | 同一作品在多个 CMS 源之间换 | 同一番剧在多条规则/多个线路之间换 |
| 播放器/代理/下载 | 自己的 | **同一份**（`ui_kit/media/`、`services/media_proxy/`） |
| 数据表 | `video_*` | `anime_*`（独立前缀，互不干扰） |

### 明确不做（写死在文档里，不要承诺做不到的事）

| 不做 | 原因 |
|---|---|
| **内置浏览器内核 / 执行页面 JS** | 与"Qt 单栈、不引入 QtWebEngine"冲突。因此**纯 JS 渲染型站点无法支持** —— 规则写对了也拿不到地址。Kazumi 维护者在 issue #914 里也说明 iframe 内的 referer 校验/JS 挑战"基本无法解决" |
| **实时 Anime4K 超分** | 需要 libmpv 播放内核 + 着色器管线，与单栈决策冲突；低端机会直接卡死（Kazumi #739/#591）。需要时用离线 ffmpeg 增强 |
| **发送弹幕** | 弹弹play 的发送属受限接口，官方未开放。我们**只读** |
| **DLNA 投屏 / 一起看（SyncPlay）** | 需要 UPnP/DLNA 或公共服务器与协议实现，单机工具不划算；改为"用外部播放器打开地址" |
| **WebDAV 同步** | 只做追番/进度的 **JSON 导出/导入**（WebDAV 是 Kazumi 缺陷最密集的功能，4 个 issue 佐证） |
| **自动过验证码 / 反爬绕过** | `antiCrawlerConfig` 只读不执行；检测到验证页时提示"请在浏览器中打开该站" |

## 1. 目录

**界面层（`boards/anime/`）**

| 文件 | 行数 | 职责 |
|---|---|---|
| `board.py` | 687 | 板块外壳：五个子页 + 任务条 + 设置入口 + 播放/下载编排 |
| `context.py` | 65 | 板块单例（storage / library / rules） |
| `search.py` | 341 | 搜索页：多规则并发、**边搜边出**、同名合并 |
| `detail.py` | 403 | 番剧详情：元数据 + 线路/剧集 + 追番 + 整季下载 |
| `library_page.py` | 382 | 番剧库/追番（**有新集**角标 + 继续观看 + 导出导入） |
| `schedule.py` | 288 | 时间表（Bangumi 放送时间优先） |
| `rules_page.py` | 656 | 规则管理（启停/导入/导出/订阅/编辑/试跑面板） |
| `history.py` | 248 | 播放历史 |
| `widgets.py` | 675 | 公共控件（海报卡、线路选择器、集数网格、后台线程） |

**引擎层（`core/anime/`）**

| 文件 | 行数 | 职责 |
|---|---|---|
| `models.py` | 239 | 规则/番剧/线路/剧集/进度/弹幕 模型 + 标题归一化 + 集数解析 |
| `selectors.py` | 368 | XPath / CSS / JSONPath 三种求值器（统一 `first/all/nodes/attr`） |
| `rules.py` | 521 | 规则解析/校验/变量替换/导入导出/订阅校验和 |
| `sniffer.py` | 558 | 播放页静态嗅探（`player_aaaa` / JSON 扫描 / iframe 递归 / JS 变量 / 解码） |
| `engine.py` | 791 | 规则执行：搜索 → 详情 → 线路/剧集 → 播放地址（三步走） |
| `providers.py` | 16 | **转发壳** → `ui_kit/media/rule_sources.py`（架构闸门要求） |
| `storage.py` | 784 | `anime_*` 七张表 CRUD + 导出/导入载荷 |
| `library.py` | 1184 | 业务编排：搜索/追番/进度/下载/换源/更新检查 |
| `danmaku.py` | 709 | 弹弹play 客户端（签名鉴权/搜索/匹配/弹幕 XML/缓存） |
| `bangumi.py` | 465 | Bangumi 元数据（只读 + 缓存 + 限速） |
| `update_check.py` | 235 | 追番更新检查（并发 2、可取消） |
| `rules_builtin/*.json` | — | 随包只读示例规则（1 条 XPath + 1 条 API，都指向本机 fixture） |

**共享层（两个板块共用；v1.1.0 新增）**

| 文件 | 行数 | 职责 |
|---|---|---|
| `ui_kit/media/player.py` | 808 | 播放器窗口（`QGraphicsVideoItem` + 字幕/弹幕/诊断/自愈） |
| `ui_kit/media/danmaku.py` | 450 | 弹幕数据/XML/性能分级/轨道分配/渲染层 |
| `ui_kit/media/danmaku_bridge.py` | 169 | 弹幕取数接线（弹弹play 只读 + 本地 XML 导入导出） |
| `ui_kit/media/rule_sources.py` | 344 | **把动漫规则包装成影视源**的适配器（跨板块桥） |
| `ui_kit/media/subtitle.py` | 151 | 外挂字幕解析 |
| `ui_kit/media/surface.py` | 161 | 视频渲染面（含 `QVideoWidget` 回退） |
| `ui_kit/media/decode.py` | 116 | 解码后端/硬解环境变量注入 |
| `services/media_proxy/*` | — | 本机代理（连接池/流式/磁盘缓存/预热） |

> **为什么有"共享层"这一层**：架构闸门（`tests/architecture/test_architecture.py`）规定
> **板块之间不得互相引用**。动漫要复用影视的播放器、代理、下载器，这些就只能住在
> `ui_kit/`（`boards/*` 可引用）或 `services/`。同理，`core/anime/*` 需要
> `RemoteVideo/Episode/VideoSource` 时不能直接 import `core/video/*` —— 所以
> 规则源适配器放在 `ui_kit/media/rule_sources.py`，`core/anime/providers.py` 只留转发壳。

## 2. 数据

数据库：单库 `modu.db`（迁移 `v3_anime`，纯增量、可重复执行）。详见 `docs/DATABASE.md`。

- `anime_rules` — 规则索引（文件是唯一真相，这里存启用状态与最近测试结果）
- `anime_series` — 番剧库（元数据缓存 + 每规则一份来源记录，`UNIQUE(rule_key, remote_id)`）
- `anime_favorites` — 追番（`UNIQUE(series_id)`）
- `anime_progress` — 观看进度（`UNIQUE(series_id, episode_key)`）
- `anime_history` — 播放历史
- `anime_danmaku` — 弹幕缓存索引（文件在 `数据目录/anime/danmaku/`）
- `anime_download_tasks` — 下载任务（动漫自己一份，不借影视的表 —— 板块数据互不干扰）

规则文件位置：

| 来源 | 路径 | 可编辑 |
|---|---|---|
| 内置 | `src/modu_workbench/core/anime/rules_builtin/*.json` | 否（随包只读） |
| 用户 | `数据目录/anime/rules/*.json` | 是 |
| 订阅 | `数据目录/anime/rules/_subscribed/<订阅名>/*.json` | 否（更新时整体覆盖） |

## 3. 规则引擎要点

### 3.0 先回答"有默认规则吗"——没有，而且要说清为什么

**没有开箱即用、无需任何配置的默认规则。** 随包的两条
`core/anime/rules_builtin/*.json` 指向 `http://127.0.0.1:8000/` 与
`https://api.example.com/`，是**离线示例 / 自检夹具**，导入后搜索必然是 0 条
（这是"导入了没反应"最常见的原因 —— 校验会"通过"，因为字段齐全、语法合法）。

为此规则列表的「提示」列会显式写 **"这是示例/模板地址，搜索必定 0 条"**，
试跑面板也会给出同样的话。这一条**故意不算校验失败**：示例规则必须能正常导入
（用户要照它改），把它做成失败反而会挡住正常用法（有回归测试盯着，
见 `test_template_rule_still_imports_but_warns`）。

2026-10 实测 `KazumiRules` 仓库**全量 16 条**规则（逐条打真站点）：

| 阶段 | 结果 |
|---|---|
| 搜索 | **11 / 16** 可用（`mutefun` `sorani` `moonci` `baimao` `akianime` `7sefun` `DM84` `MXdm` `xfdmnext` …） |
| 详情 / 选集 | 同样 11 条可用（线路 1–6 条，集数 20–1074） |
| **取到播放地址** | **0 / 16** —— 这批站全部 `useWebview: true`，真地址由站点 JS 算出或站点自定义加密（实测 `encrypt=3`，以及"再跳一层播放页"的 `encrypt=1` + 百分号编码） |

所以：**能不能播取决于源站要不要跑 JS，而不是规则写得对不对**——
没有浏览器内核这条边界（第 0 节）在这里是硬约束。用户可按这个顺序试：

1. **自己写一条 XPath 规则**（字段表见下），用规则页的**试跑面板**逐步调；
   苹果 CMS v10 站的选择器形状基本固定，改 `baseURL` / `searchURL` 就能复用；
2. **导入 KazumiRules 的 API 模式规则**：能搜、能选集，播放看站点；
3. **外部播放器兜底**：详情页右键 → 用外部播放器打开；或在浏览器里复制 `.m3u8` 地址后手动播放。

### 3.1 真实规则逼出来的四条兼容性修复（都有回归测试）

| 症状 | 根因 | 修法 |
|---|---|---|
| 搜索命中 **0 条** | 规则把子选择器写成 `//div/a[2]` 这种**绝对路径**。XPath 规范里 `//x` 是"从文档根找"，**在元素上求值也一样** → 每条命中都拿到全页第一个匹配（实测取到站点头部的 `IOS用户使用欧路Via浏览器…全功能拦截`） | `XPathSelector` 在**元素**上遇到绝对路径时先作用域化（`//x` → `.//x`），与 `engine._scoped()` 同一语义 |
| 详情 **404**、选集全空 | `searchResult: "//h2/a"` 指向 `<a>` **元素**，我们取的是它的**文本**（标题），地址于是变成 `https://站/葬送的芙莉莲` | `<a>` 元素优先取 `href`（显式写 `@href`/`text()` 不受影响） |
| 整条规则**崩掉** | 某规则写 `"searchResult": "//"` —— 合法 JSON、**非法 XPath**，`XPathError` 把整站搜索打掉 | 空路径（`//` `/` `.` `./`）当成"字段没写" |
| 播放地址拿不到（可救的那类） | `player_aaaa.url` 解出来是**另一个播放页**（`encrypt=1` + 百分号编码，如 `next.xifanacg.com/anime/3252/play/…`），旧实现只接受"像媒体的结果"于是判失败 | 支持"再嗅探一层"（受 `max_iframe_depth` 限制防打转） |

回归测试：`tests/board_anime/test_anime_kazumi_compat.py`（用离线 fixture 固定这四条语义）。
另有 `tests/board_anime/test_anime_rule_compat.py` 守着 `bodyType`（json/form/text）
与 query/body 里 bool/int **不被字符串化**（真实规则里有 `"enabled": true`、`"sortDesc": false`、
`"page": 1`，转成字符串会让严格校验的接口 400 或涨序变降序）。

- **字段与 Kazumi 一一对应**，因此 `KazumiRules` 仓库的 **API 模式**规则可直接导入；
  **XPath 模式**规则只在其播放地址能被静态嗅探时可用（见第 0 节的边界）。
- **选择器**：XPath（lxml）/ CSS（我们扩展，写 `css:div.item > a`，`@attr` 取属性）/
  JSONPath 子集（`$`、`.a.b`、`[*]`、`[0]`、`['k']`）。**不支持**递归 `..`、过滤 `[?(…)]`、
  切片 `[0:2]`、函数 `length()` —— 会给出**明确错误**而不是静默返回空。
- **变量替换**：`@keyword`（URL 编码）、`@source`、`@roadIndex`/`@episodeIndex`（0 起）、
  `@roadNumber`/`@episodeNumber`（1 起）、`chapterApiConfig.variables` 里的响应变量。
- **版本门控**：`api > 8` 的规则**加载但灰显**并注明"该规则需要更新的客户端"，不静默失败；
  `deprecated` 显示为"已退役"但**保留文件**；`useWebview=true` 提示"依赖网页渲染，可能解析失败"。
- **播放地址解析三步走**（从便宜到昂贵）：规则直接给媒体地址 → 嗅探剧集页 → 用
  `episodePage` 模板构造播放页再嗅探。
- **嗅探策略**（`sniffer.py`，按可靠性排序）：`player_aaaa`（苹果 CMS v10，覆盖绝大多数站）
  → `/player/` 接口 → JSON 扫描 → 正则（含 `atob`/`unescape`/`\uXXXX`/`\/` 解码）→ iframe 递归（≤3 层）。
  失败时给出**可读原因**（试跑面板直接显示）。

## 4. 弹幕

- 数据源**只有弹弹play**（官方文档路线：`/api/v2/search` → `episodeId` → `/api/v2/comment/{id}`）。
  **不采用** Kazumi 的 `bangumiId + 补零集数` 未公开约定 —— 官方接口才是稳定路径。
- 鉴权：签名模式 `X-Signature = base64(sha256(AppId + Timestamp + Path + AppSecret))`，
  `Path` 不含域名与查询串；也支持凭证模式。**签名每个请求按新时间戳重算**，凭证只存本机设置。
- **必须缓存**（官方明确建议 2–6 小时，老番 2–7 天）：XML 落 `数据目录/anime/danmaku/`，
  DB 记索引；新鲜缓存直接返回、不打网络，失败时退回过期缓存。
- 渲染：`ui_kit/media/danmaku.py`（轨道分配 + **性能分级** 60/40/25 同屏上限 + 池化 item），
  支持关键词与 `/正则/` 屏蔽、同屏重复合并（默认关）。
- 合规：**只读、严格缓存、不批量抓取**。

## 5. 测试

| 文件 | 覆盖 |
|---|---|
| `tests/board_anime/conftest.py` | 共享夹具（本地假站点 fixture、临时库） |
| `test_anime_rules.py` / `test_anime_selectors.py`（并入 engine 文件） | 规则解析/校验/变量/选择器 |
| `test_anime_sniffer.py` | 各嗅探策略、解码、失败原因 |
| `test_anime_engine.py` | 搜索/详情/选集/播放页构造/超时/限速 |
| `test_anime_library.py` | 搜索合并、追番、进度、换线路、更新检查 |
| `test_anime_storage.py` | 七张表 CRUD、唯一约束、导出导入 |
| `test_anime_danmaku.py` | 签名向量、匹配、XML、缓存 TTL、Bangumi |
| `test_anime_update_check.py` | 「总集数变多」等更新判定 |
| `test_anime_providers.py` | 规则 → 影视源适配（跨板块桥） |
| `test_anime_ui.py` | 板块构建、五个子页、规则试跑、海报卡合并、集数网格 |

`tests/platform/test_db_schema.py` 会守住 `anime_*` 表与前缀/索引命名；`app/main.py` 的
`anime_core` 自检用内置假站点 HTML 走一遍"规则 → 选择器 → 嗅探 → 弹幕 → 追番/进度"。

## 6. 变更须知

- 改数据结构：加迁移模块并同步 `tests/platform/test_db_schema.py` 与 `docs/DATABASE.md`；
- 改规则字段：同步 `core/anime/rules.py` 的解析与校验、`docs/VIDEO_ANIME_PLAN.md` 的字段表；
- 改界面：跑 `pytest tests/board_anime tests/architecture -q`，并确认设置页不出现横向滚动条
  （长路径标签要插入零宽断点，见 `ui_kit/settings/general.py` 的 `_pickable`）；
- 新增内置规则：放进 `core/anime/rules_builtin/`，必须是**可离线验证**的样例（不要指向真实站点）。
