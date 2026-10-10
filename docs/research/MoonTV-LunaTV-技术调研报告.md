# MoonTV / LunaTV 影视聚合播放站 —— 事实性技术调研报告

> 调研对象（全部为公开 GitHub 仓库，代码事实以各仓库 `main` 分支为准）：
> - **MoonTV 上游**：`MoonTechLab/LunaTV`（MoonTV 改名后的主仓库，即"LunaTV 原版"）
> - **MoonTV 原始/分支**：`samqin123/MoonTV`、`brucemomo/Moon_TV`（基于 2.0.1 自行维护）、`mtvpls/MoonTVPlus`
> - **LunaTV 增强版**：`stazeng/LunaTV-EE`（v6.1.1，功能最全，本报告的重点样本）
>
> 所有代码引用均给出 URL。无法从公开源码/文档核实的写"**未查到**"。
> 报告时间基准：仓库快照 `MoonTechLab/LunaTV@6c0428d`（main）、`stazeng/LunaTV-EE@main`（v6.1.1，CHANGELOG 最新条目 2026-02-01）。

---

## 1. 技术栈与部署形态

1. **前端框架**：Next.js **App Router** + React。上游 LunaTV 是 Next.js 14 / React 18 量级（`next.config.js` 用 `experimental.instrumentationHook`、`swcMinify`）；LunaTV-EE 升到 **Next.js 16.1.0 + React 19.0.0 + TypeScript 5.8.3 + Tailwind CSS 4.1.18 + Framer Motion 12**。来源：[LunaTV README 技术栈表](https://github.com/MoonTechLab/LunaTV/blob/main/README.md)、[LunaTV-EE README 技术栈表](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)、[EE package.json](https://github.com/stazeng/LunaTV-EE/blob/main/package.json)、[LunaTV next.config.js](https://github.com/MoonTechLab/LunaTV/blob/main/next.config.js)。

2. **后端形态：不是 Edge，而是 Node.js Runtime 的 Route Handler**。几乎所有 API route 都显式声明 `export const runtime = 'nodejs';`（搜索、详情、代理、直播、弹幕、播放记录等）。**唯一例外**是 `brucemomo/Moon_TV` 的 `src/app/api/m3u8/route.ts`，它写的是 `export const runtime = 'edge';`。来源：[LunaTV proxy/m3u8/route.ts L8](https://github.com/MoonTechLab/LunaTV/blob/main/src/app/api/proxy/m3u8/route.ts)、[LunaTV search/route.ts L10](https://github.com/MoonTechLab/LunaTV/blob/main/src/app/api/search/route.ts)、[Moon_TV api/m3u8/route.ts L5](https://github.com/brucemomo/Moon_TV/blob/main/src/app/api/m3u8/route.ts)。

3. **选 Node runtime 的原因直接写在代码注释里**：`src/app/api/proxy/cms/route.ts` 开头注释"使用 Node.js Runtime 以获得更好的网络兼容性"，并且代理层用到了 Node 专属的 `http`/`https` Agent（连接池）——Edge runtime 用不了。来源：[LunaTV-EE proxy/cms/route.ts L17-18](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/cms/route.ts)、[LunaTV-EE proxy/segment/route.ts L9-27](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/segment/route.ts)。

4. **数据库/存储有 5 种后端，靠 `NEXT_PUBLIC_STORAGE_TYPE` 环境变量切换**：`localstorage`（默认，仅浏览器）/ `redis` / `upstash`（Upstash Redis REST）/ `d1`（Cloudflare D1，仅 MoonTV 分支）/ `kvrocks`（LunaTV-EE 的推荐生产方案）。来源：[LunaTV lib/db.ts L9-27](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/db.ts)、[MoonTV(Branch) README 存储支持矩阵](https://github.com/brucemomo/Moon_TV/blob/main/README.md)、[LunaTV-EE README Kvrocks 部署](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)。

5. **部署平台**：Docker（`output: 'standalone'` 多阶段镜像）、Vercel、Netlify、Cloudflare Pages（用 `pnpm run pages:build` + `nodejs_compat`）、Zeabur、Hugging Face Spaces、飞牛 OS、EdgeOne Pages。来源：[MoonTV(Branch) README 部署章节](https://github.com/brucemomo/Moon_TV/blob/main/README.md)、[LunaTV-EE README 部署章节](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)、[LunaTV next.config.js L5](https://github.com/MoonTechLab/LunaTV/blob/main/next.config.js)。

6. **Vercel Cron 用于定时任务**：`vercel.json` 里 `{"path":"/api/cron","schedule":"0 1 * * *"}`，即每天 01:00 跑一次 `/api/cron`。来源：[LunaTV vercel.json](https://github.com/MoonTechLab/LunaTV/blob/main/vercel.json)。

7. **"为什么必须放服务端做代理"——源码里给了 3 条硬理由**（`proxy/cms/route.ts` 文件头注释）：① 代理外部 CMS API 的 HTTP/HTTPS 请求；② **解决 HTTPS 页面无法请求 HTTP 资源（Mixed Content）**；③ **解决第三方 API 的 CORS 限制**；另外还有第 4 条：白名单机制防止被滥用为通用代理。来源：[LunaTV-EE proxy/cms/route.ts L1-11](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/cms/route.ts)。

8. **服务端代理还有一层"绕过防盗链"的动机**：m3u8/segment 代理默认使用 `AptvPlayer/1.4.10` 这类播放器 UA 请求源站，而不是浏览器 UA。来源：[LunaTV lib/live.ts L6 `const defaultUA = 'AptvPlayer/1.4.10'`](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/live.ts)、[LunaTV proxy/segment/route.ts L25](https://github.com/MoonTechLab/LunaTV/blob/main/src/app/api/proxy/segment/route.ts)。

9. **架构上还有一层"外部 Worker 代理"**：LunaTV-EE 支持把源请求交给自建 Cloudflare Worker（默认 `https://corsapi.smone.workers.dev`），Worker 失败时**自动降级到本地代理**。来源：[LunaTV-EE PROXY_CONFIG.md](https://github.com/stazeng/LunaTV-EE/blob/main/PROXY_CONFIG.md)、[LunaTV-EE admin/video-proxy/route.ts L68](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/admin/video-proxy/route.ts)。

10. **仓库自带一个通用 CORS Worker 脚本**：`proxy.worker.js`，路径即目标地址（`/<encoded-url>`），强制 `Cache-Control: no-store` 且 `Access-Control-Allow-Origin: *`，并会重写 HTML 里的相对路径。来源：[LunaTV proxy.worker.js](https://github.com/MoonTechLab/LunaTV/blob/main/proxy.worker.js)。

---

## 2. 视频源

1. **源类型：苹果 CMS / MacCMS V10 的 `vod` JSON API**（不是 m3u8 直链配置，也不是采集站爬虫）。请求形状固定为：搜索 `?ac=videolist&wd=<kw>`、分页 `?ac=videolist&wd=<kw>&pg=<n>`、详情 `?ac=videolist&ids=<id>`。来源：[LunaTV lib/config.ts L36-54](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/config.ts)。

2. **请求头带浏览器 UA 伪装**：`User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... Chrome/122.0.0.0 Safari/537.36`，`Accept: application/json`。来源：[LunaTV lib/config.ts L40-53](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/config.ts)。

3. **多源配置在 `config.json` 的 `api_site` 对象里**，每个源 4 个字段：`api`（vod JSON API 根地址）、`name`（展示名）、`detail`（可选，无法通过 API 拿详情时用于爬网页）、`key`（唯一标识）。来源：[MoonTV(Branch) README 配置说明](https://github.com/brucemomo/Moon_TV/blob/main/README.md)、[LunaTV-EE README 配置文件](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)。

4. **源的运行时增删改与去重**：`configSelfCheck()` 对 `SourceConfig` 按 `key` 去重；配置文件里的源被标记 `from: 'config'`，运行时新增的源标记 `from: 'custom'`。来源：[LunaTV lib/config.ts L396-404](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/config.ts)、[LunaTV lib/config.ts L102-108](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/config.ts)。

5. **搜索聚合：扇出到全部源 + 单源超时 + 失败不致命**。`/api/search` 对每个源发一个请求，用 `Promise.race` 给每个源加 **20 秒**总超时，`Promise.allSettled` 收集，失败只 `console.warn` 并返回空数组。来源：[LunaTV search/route.ts L45-70](https://github.com/MoonTechLab/LunaTV/blob/main/src/app/api/search/route.ts)。

6. **源内部还有 8 秒的 fetch 级超时 + AbortController**：`searchWithCache(apiSite, query, page, url, timeoutMs = 8000)`，超时被识别为 `AbortError` 并写负缓存。来源：[LunaTV lib/downstream.ts L24-43, L127-135](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/downstream.ts)。

7. **多页拉取**：第一页返回的 `pagecount` 决定还要拉几页，上限由 `NEXT_PUBLIC_SEARCH_MAX_PAGE`（默认 5，范围 1-50）控制，额外页**并发** `Promise.all` 拉取后合并。来源：[LunaTV lib/downstream.ts L152-189](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/downstream.ts)。

8. **去重与排序在客户端做**：上游 `/api/search` 只做 `flatten()` 平铺，**不做跨源去重**；搜索页有独立的 `src/lib/search-ranking.ts`（LunaTV-EE）以及 `SearchResultFilter`（按播放源/标题/年份筛选，年份可排序）。来源：[LunaTV search/route.ts L71-88](https://github.com/MoonTechLab/LunaTV/blob/main/src/app/api/search/route.ts)、[LunaTV-EE search-ranking.ts](https://github.com/stazeng/LunaTV-EE/blob/main/src/lib/search-ranking.ts)、[LunaTV SearchResultFilter.tsx](https://github.com/MoonTechLab/LunaTV/blob/main/src/components/SearchResultFilter.tsx)。

9. **结果过滤：集数为 0 的直接丢弃**；`vod_play_url` 用 `$$$` 切播放组、`#` 切分集、`$` 切"标题→链接"，并**只接受以 `.m3u8` 结尾的链接**；多播放组取集数最多的那组。来源：[LunaTV lib/downstream.ts L76-121](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/downstream.ts)。

10. **源健康检查 = SSE 流式逐源上报**：`GET /api/admin/source/validate?q=<关键词>` 返回 `text/event-stream`，事件类型 `start` / `source_result`（`status: 'valid' | 'no_results' | 'invalid'`）/ `source_error` / `complete`，单源 **10 秒** AbortController 超时，判定标准是"返回 200 且 list 非空且**标题里包含搜索词**"。来源：[LunaTV admin/source/validate/route.ts L39-199](https://github.com/MoonTechLab/LunaTV/blob/main/src/app/api/admin/source/validate/route.ts)。

11. **源权重系统（LunaTV-EE）**：每个源可有 `weight`，默认 **50**；`GET /api/source-weights` 返回 `{sourceKey: weight}` 映射，响应头 `Cache-Control: public, max-age=300, s-maxage=300`（缓存 5 分钟），播放页优选时按权重排序。来源：[LunaTV-EE source-weights/route.ts](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/source-weights/route.ts)、[LunaTV-EE CHANGELOG v6.1.1 "视频源权重/优先级系统"](https://github.com/stazeng/LunaTV-EE/blob/main/CHANGELOG)。

12. **搜索缓存（内存 LRU + 负缓存）**：`SEARCH_CACHE` 是进程内 `Map`，key 为 `sourceKey::query::page`，**TTL 10 分钟**，上限 **1000 条**，5 分钟清理一次（按 `expiresAt` 升序淘汰最老的），`cleanupTimer.unref()` 避免阻止进程退出；缓存状态含 `ok` / `timeout` / `forbidden`（403 会写负缓存）。来源：[LunaTV lib/search-cache.ts L1-144](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/search-cache.ts)、[LunaTV lib/downstream.ts L53-56](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/downstream.ts)。

13. **"自动降级"的两处具体实现**：① 搜索层面单源失败返回空数组，不阻塞其他源；② LuaTV-EE 的"备用 API"机制——主 API 失败自动切换（README 明确列出"搜索和首页数据加载支持备用 API，当主 API 失败时自动切换"）。来源：[LunaTV search/route.ts](https://github.com/MoonTechLab/LunaTV/blob/main/src/app/api/search/route.ts)、[LunaTV-EE README 安全与存储章节](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)。

14. **无法通过 API 拿详情的源，靠正则爬网页**：对配置了 `detail` 的源，取 `{detail}/index.php/vod/detail/id/{id}.html`，用正则抽 `.m3u8`（ffzy 有专用正则 `/\$(https?:\/\/[^"'\s]+?\/\d{8}\/\d+_[a-f0-9]+\/index\.m3u8)/g`）。来源：[LunaTV lib/downstream.ts L289-328](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/downstream.ts)。

---

## 3. 播放链路（重点）

### 3.1 各仓库的链路选择并不一致（重要，必须先分清）

**A. LunaTV / LunaTV-EE 的点播（VOD）不走代理。** 播放页把采集源返回的 `.m3u8` 绝对地址**直接**交给 hls.js；`/api/proxy/m3u8` 的构造点只出现在**直播**页面。也就是说这两个项目的点播 m3u8 与 TS 分片由浏览器**直连源站 CDN** 拉取。
来源：[LunaTV-EE play/page.tsx](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/play/page.tsx)、[LunaTV-EE live/page.tsx L2102](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/live/page.tsx)。

**B. 直播（IPTV/LiveConfig）链路——走完整代理 + 播放列表重写**（LunaTV / LunaTV-EE）。这是本节代理实现要点的主要来源。

**C. ⚠️ 但 MoonTVPlus 有真正的【点播代理】——存在按源开关的 VOD 代理链路（重要补充）。**
`mtvpls/MoonTVPlus` 额外提供 **`GET /api/proxy/vod/m3u8`** 与 **`GET /api/proxy/vod/segment`**，是**专为点播视频源**设计的代理路径：
- 参数用 **`source=`**（不是 `moontv-source=`），且 **`source` 为必填**，缺失直接 400 `Missing source`；
- **按源开关**：查 `config.SourceConfig` 找到该源，若 `!videoSource.proxyMode` 则返回 **403 `Proxy mode not enabled for this source`**——即 `proxyMode?: boolean` 是 `SourceConfig` 上的**逐源配置项**，可按源灰度开启点播代理；
- 重写基址是 `${protocol}://${host}/api/proxy/vod`，分片重写为 `/api/proxy/vod/segment?url=...&source=...`，`EXT-X-MAP`→segment、`EXT-X-KEY`→key、`EXT-X-STREAM-INF` 下一行→`/api/proxy/vod/m3u8?url=...&source=...`（标签覆盖面与 LunaTV **原版**一致，共 3 类 + 裸 URL）；
- **请求头伪装**：`User-Agent: Mozilla/5.0 ... Chrome/120.0.0.0 Safari/537.36` + **`Referer: decodedUrl`（把目标 URL 自身当 Referer）**——这是与 LunaTV 系（用 `AptvPlayer/1.4.10`、不带 Referer）**不同的一套绕过策略**；
- **带 SSRF 防护**：`validateProxyUrlServerSide(decodedUrl)` 校验失败返回 403 `Proxy request to local or invalid network is forbidden`（实现在 `src/lib/server/ssrf.ts`）；
- 响应头由 `src/lib/server/proxy-headers.ts` 的 `buildProxyM3u8Headers()` / `buildProxyStreamHeaders()` 统一构造。
来源：[MoonTVPlus src/app/api/proxy/vod/m3u8/route.ts](https://github.com/mtvpls/MoonTVPlus/blob/main/src/app/api/proxy/vod/m3u8/route.ts)、[MoonTVPlus src/app/api/proxy/vod/segment/route.ts](https://github.com/mtvpls/MoonTVPlus/blob/main/src/app/api/proxy/vod/segment/route.ts)、[MoonTVPlus src/lib/server/ssrf.ts](https://github.com/mtvpls/MoonTVPlus/blob/main/src/lib/server/ssrf.ts)、[MoonTVPlus src/lib/config.ts L30 `proxyMode?: boolean`](https://github.com/mtvpls/MoonTVPlus/blob/main/src/lib/config.ts)。

> **对"桌面端卡顿/防盗链"的意义**：如果你们要代理点播分片，**MoonTVPlus 的 `/api/proxy/vod/*` 才是更贴近的参考实现**（按源开关 + 必填 source + SSRF 校验 + Referer 伪装），而不是 LunaTV-EE 的直播代理。两者可组合：抄 EE 的**流式背压/连接池/缓存头**，抄 MoonTVPlus 的**逐源开关与 VOD 路径设计**。

### 3.2 直播/VOD 代理接口清单

| 接口 | 作用 | 关键参数 |
|---|---|---|
| `GET /api/proxy/m3u8` | 拉取并**重写** m3u8 播放列表 | `url`、`allowCORS`、`moontv-source` |
| `GET /api/proxy/segment` | 代理 TS/fMP4 分片、`EXT-X-MAP`、子播放列表 | `url`、`moontv-source` |
| `GET /api/proxy/key` | 代理 `EXT-X-KEY` 的 AES-128 密钥 | `url`、`moontv-source` |
| `GET /api/proxy/logo` | 代理频道台标图 | `url`、`moontv-source` |
| `GET /api/proxy/stream` | 通用流代理（mp4/flv 等），透传 Range | `url`、`moontv-source` / `decotv-source` |
| `GET /api/proxy/cms` | 代理 MacCMS JSON API（白名单） | `url`、`filter` |
| `GET /api/video-proxy` | 代理 mp4 直链，支持 Range + 本地缓存 | `url` |
| `GET /api/image-proxy` | 豆瓣图片代理（禁内网） | `url` |
| `GET /api/live/precheck` | 源测速 + 流类型嗅探 | `url`、`moontv-source` |
| `GET /api/proxy-status` | Worker 代理健康检查 | — |

来源：[LunaTV-EE src/app/api/proxy/ 目录树](https://github.com/stazeng/LunaTV-EE/tree/main/src/app/api/proxy)、[LunaTV src/app/api/proxy/ 目录树](https://github.com/MoonTechLab/LunaTV/tree/main/src/app/api/proxy)。

### 3.3 m3u8 播放列表重写（核心机制）

1. **相对路径→绝对路径再代理**：`getBaseUrl(finalUrl)` 取**重定向后的最终 URL**（不是原始请求 URL）作为 base，再用 `resolveUrl()` 做 `new URL(relative, base)` 解析，最后包成 `encodeURIComponent` 的代理 URL。源码注释解释了为什么用重定向后的 URL。来源：[LunaTV proxy/m3u8/route.ts L48-56,L126-138](https://github.com/MoonTechLab/LunaTV/blob/main/src/app/api/proxy/m3u8/route.ts)、[LunaTV lib/live.ts L277-348](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/live.ts)。

2. **上游（MoonTV/LunaTV 原版）只重写 3 类标签**：裸 URL 行 → `/api/proxy/segment?url=...`；`#EXT-X-MAP:URI="..."` → segment；`#EXT-X-KEY:URI="..."` → key；`#EXT-X-STREAM-INF:` 下一行 → `/api/proxy/m3u8?url=...`。来源：[LunaTV proxy/m3u8/route.ts L96-169](https://github.com/MoonTechLab/LunaTV/blob/main/src/app/api/proxy/m3u8/route.ts)。

3. **LunaTV-EE 把标签覆盖扩到 13 类**（这是原版 → 增强版最大的差异之一）：除上述 3 类外，还处理 `EXT-X-DEFINE`（变量定义）、`EXT-X-MEDIA`（音轨/字幕）、`EXT-X-PART`（LL-HLS 部分片段）、`EXT-X-CONTENT-STEERING`（`SERVER-URI=`）、`EXT-X-SESSION-DATA`、`EXT-X-SESSION-KEY`、`EXT-X-DATERANGE`、`EXT-X-PRELOAD-HINT`（按 `TYPE=PART/MAP` 选端点）、`EXT-X-RENDITION-REPORT`；`EXT-X-SERVER-CONTROL` / `EXT-X-SKIP` 无 URI 保持原样。来源：[LunaTV-EE proxy/m3u8/route.ts L234-605](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/m3u8/route.ts)。

4. **实现了 `EXT-X-DEFINE` 的 `{$var}` 变量替换**：`VARIABLE_REPLACEMENT_REGEX = /\{\$([a-zA-Z0-9-_]+)\}/g`，`NAME=`/`VALUE=` 存入 `Map`，未定义时**保持原引用并告警**（对齐 hls.js 行为）。来源：[LunaTV-EE proxy/m3u8/route.ts L356-386](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/m3u8/route.ts)。

5. **`EXT-X-MEDIA` 的脏 URI 兜底**：如果 URI 为空、是 `nan` 或包含 `nan`，**直接删掉 `URI="..."` 属性**让 hls.js 忽略该音轨，而不是生成坏代理地址（源码注释："防止HLS音轨加载错误"）。来源：[LunaTV-EE proxy/m3u8/route.ts L416-447](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/m3u8/route.ts)、[LunaTV-EE README "EXT-X-MEDIA URI处理"](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)。

6. **`allowCORS=true` 时跳过分片改写**：`const proxyUrl = allowCORS ? resolvedUrl : \`${proxyBase}/segment?url=...\``——即源站本身允许跨域时**让浏览器直连分片**，只走一次播放列表代理，省掉全部 TS 中转带宽。来源：[LunaTV proxy/m3u8/route.ts L110](https://github.com/MoonTechLab/LunaTV/blob/main/src/app/api/proxy/m3u8/route.ts)、[LunaTV-EE proxy/m3u8/route.ts L261](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/m3u8/route.ts)。

7. **代理基地址的两种拼法**：上游用**站内相对路径** `/api/proxy`（注释明确：不依赖可伪造的 Host 头，也不会在 HTTPS 站点下拼出 `http://` 造成 Mixed Content）；EE 版改成从 `referer` 头推协议 + `host` 头拼绝对地址 `${protocol}://${host}/api/proxy`。来源：[LunaTV proxy/m3u8/route.ts L96-99](https://github.com/MoonTechLab/LunaTV/blob/main/src/app/api/proxy/m3u8/route.ts)、[LunaTV-EE proxy/m3u8/route.ts L234-249](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/m3u8/route.ts)。

8. **播放列表响应禁止缓存**：m3u8 响应设 `Cache-Control: no-cache, no-store, must-revalidate` + `Pragma: no-cache` + `Expires: 0`，因为 live 播放列表每次都要新拉。来源：[LunaTV-EE proxy/m3u8/route.ts L148-150](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/m3u8/route.ts)。

### 3.4 分片代理（性能关键）

9. **Node 连接池复用**：segment 代理建了两个 `http.Agent`/`https.Agent`，参数 `keepAlive: true, maxSockets: 100, maxFreeSockets: 20, timeout: 30000, keepAliveMsecs: 30000`；m3u8 代理用较保守的 `maxSockets: 50, maxFreeSockets: 10, timeout: 60000`。来源：[LunaTV-EE proxy/segment/route.ts L9-27](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/segment/route.ts)、[LunaTV-EE proxy/m3u8/route.ts L10-28](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/m3u8/route.ts)。

10. **真流式 + 背压控制（不缓冲整段）**：用 `ReadableStream` + `response.body.getReader()` 逐 chunk `enqueue`，并设置 `highWaterMark: 65536`（**64KB 缓冲区**）+ `size(chunk)` 回传字节数。上游原版有同样的流式 pump 但没有 highWaterMark。来源：[LunaTV-EE proxy/segment/route.ts L221-227](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/segment/route.ts)、[LunaTV proxy/segment/route.ts L94-116](https://github.com/MoonTechLab/LunaTV/blob/main/src/app/api/proxy/segment/route.ts)。

11. **明确不转发 `Content-Length`**：源码注释"不转发 Content-Length，因为流式传输使用 chunked encoding，两者冲突"；改为 `Cache-Control: public, max-age=300`（分片缓存 5 分钟）+ `Accept-Ranges: bytes`，并把 `Content-Range` / `Last-Modified` / `ETag` 原样透传。来源：[LunaTV-EE proxy/segment/route.ts L110-119](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/segment/route.ts)、[LunaTV-EE CHANGELOG v6.1.1 "修复流式代理并发请求：防止并发请求，移除Content-Length头"](https://github.com/stazeng/LunaTV-EE/blob/main/CHANGELOG)。

12. **防盗链/反爬绕过 = UA 伪造 + 精确请求头**：分片请求带 `User-Agent: <源配置的 ua，默认 AptvPlayer/1.4.10>`、`Accept: video/mp2t, video/*, */*`、**`Accept-Encoding: identity`**（避免 gzip 干扰流式处理）、`Cache-Control: no-cache`、`Connection: keep-alive`。**没有**伪造 `Referer`。来源：[LunaTV-EE proxy/segment/route.ts L72-84](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/segment/route.ts)。

13. **`/api/video-proxy` 才伪造 Referer/Origin**（mp4 直链场景）：`'Referer': sourceOrigin + '/'`、`'Origin': sourceOrigin`，其中 `sourceOrigin` 由目标视频 URL 动态推导（`${protocol}//${host}`），另带 `Accept-Encoding: identity;q=1, *;q=0`。来源：[LunaTV-EE video-proxy/route.ts L74-87](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/video-proxy/route.ts)。

14. **超时与错误分类**：分片 30 秒超时、m3u8 15 秒超时，`AbortError` → HTTP **408**，`ENOTFOUND`/`ECONNREFUSED` → HTTP **503**，其他 500。来源：[LunaTV-EE proxy/segment/route.ts L65,L254-260](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/segment/route.ts)、[LunaTV-EE proxy/m3u8/route.ts L63,L196-203](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/m3u8/route.ts)。

15. **资源泄漏防护**：`finally` 里对未被消费的 `response.body.cancel()`；流的 `cancel()` 回调里 `reader.releaseLock()` + `response.body.cancel()`；EE 版还维护 `segmentStats{requests, errors, totalBytes, avgResponseTime, activeStreams}` 统计（每 500 次请求打印一次）。来源：[LunaTV proxy/m3u8/route.ts L81-93](https://github.com/MoonTechLab/LunaTV/blob/main/src/app/api/proxy/m3u8/route.ts)、[LunaTV-EE proxy/segment/route.ts L29-36,L270-277](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/segment/route.ts)。

16. **Range 请求透传**：`/api/proxy/stream` 把客户端的 `Range` 头透传给源站，并把 `content-range` / `accept-ranges` / `content-length` / `content-disposition` 回传；`/api/video-proxy` 用 `const statusCode = rangeHeader && contentRange ? 206 : 200` 保证 206 语义正确。来源：[LunaTV-EE proxy/stream/route.ts L59-105](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/stream/route.ts)、[LunaTV-EE video-proxy/route.ts L195](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/video-proxy/route.ts)。

17. **`/api/proxy/cms` 的 URL 白名单**（防滥用）：只放行匹配以下模式之一的地址：`/\?ac=class/i`、`/\?ac=list/i`、`/\?ac=videolist/i`、`/\?ac=detail/i`、`/\/api\/vod/i`、`/\/index\.php/i`、`/\/api\.php/i`、`/\/provide\/vod/i`。来源：[LunaTV-EE proxy/cms/route.ts L32-56](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/cms/route.ts)。

18. **`/api/image-proxy` 防 SSRF**：`checkImageProxyTarget()` 只允许 `http:`/`https:`，并拒绝内网/环回/链路本地地址（`localhost`、`*.localhost`、`*.local`、`*.internal`、`::1`、`fc00::/7`、IPv4-mapped IPv6、`0.0.0.0/8`、`10/8`、`127/8`、`169.254/16`（含云元数据 `169.254.169.254`）、`172.16/12`、`192.168/16`、CGNAT `100.64/10`）。**注意**：该限制**不施加**到直播 m3u8/segment/key/logo 代理上（注释说明因为 IPTV 组播网关常在局域网内）。来源：[LunaTV lib/proxy-guard.ts](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/proxy-guard.ts)。

19. **直播直连模式（LunaTV-EE）——最值得复用的省带宽机制**：播放前先**智能 CORS 检测**（`/api/live/precheck`），源站允许跨域就**客户端直连播放、完全绕过服务器代理**，不允许才降级到代理模式；管理后台有直连/代理模式统计面板。来源：[LunaTV-EE README "直播直连模式"](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)、[LunaTV-EE live/precheck/route.ts](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/live/precheck/route.ts)、[LunaTV-EE live/page.tsx L2102](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/live/page.tsx)。

20. **`/api/live/precheck` = 首屏延迟 + 流类型嗅探二合一**：发带 `Range: bytes=0-2047` 的请求（8 秒超时），用 `Date.now()` 差值算出 `latencyMs`；类型判定三级回退——① URL 后缀（`.m3u8`/`.mp4`/`.flv`）→ ② `Content-Type` → ③ **读前 2048 字节做魔数嗅探**（FLV 看 `0x46 0x4c 0x56` = "FLV"；MP4 看第 4-7 字节 `ftyp`；m3u8 看文本是否以 `#EXTM3U` 开头）。来源：[LunaTV-EE live/precheck/route.ts L49-202](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/live/precheck/route.ts)。

21. **失败处理：有"同源恢复"，但【没有】自动换源**（重要纠正，与宣传口径不符）：
    - hls.js 的 `Hls.Events.ERROR` 处理器**只在同一源内做恢复**——`FRAG_PARSING_ERROR` → `hls.startLoad()`；含 `timestamp` 的 `BUFFER_APPEND_ERROR` → `hls.trigger(BUFFER_EVENTS.BUFFER_RESET)` 后 `hls.startLoad(currentTime)`（保留进度）；`data.fatal` 时 `NETWORK_ERROR` → `hls.startLoad()`、`MEDIA_ERROR` → `hls.recoverMediaError()`；
    - ArtPlayer 的 `on('error')` 监听器**几乎是空实现**：只 `console.error`，且 `if (currentTime > 0) return;` ——**不会切源、不会提示**；
    - `SourceSwitchDialog` **不是**故障弹窗，而是"多人观影房里房主与成员源不一致"的确认框；
    - 换源是**用户手动**的：`EpisodeSelector` 的换源 tab → `handleSourceChange`；
    - **换源进度保留确实存在**：切源前写 `sessionStorage['temp_progress_{newSource}_{newId}_{episodeIndex}']`（新源集数不足而改集时删除）；`switchQuality()` 用于换源保进度，`switchUrl()` 用于切集归零（归零）。
    来源：[LunaTV-EE play/page.tsx L3788-3830（hls ERROR 恢复）, L4966-4972（空 error 监听）](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/play/page.tsx)、[SourceSwitchDialog.tsx](https://github.com/stazeng/LunaTV-EE/blob/main/src/components/play/SourceSwitchDialog.tsx)。

22. **码率/清晰度选择**：由 hls.js ABR 自动完成（`abrEwmaFastLive/SlowLive`、`abrBandWidthFactor`、`abrEwmaDefaultEstimate`），另有 Anime4K WebGPU 超分作为画质增强。**未查到**任何服务端转码多码率输出的实现。来源：[LunaTV-EE play/page.tsx L3749-3756](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/play/page.tsx)。

---

## 4. 转码 / 兼容性

1. **服务端完全不做转码，也不做 remux。** 三个仓库的依赖树里**没有 ffmpeg / fluent-ffmpeg / WASM 转码器**；LunaTV-EE 的 Dockerfile 只装 `ca-certificates`，没有 `ffmpeg` 包。来源：[LunaTV-EE Dockerfile](https://github.com/stazeng/LunaTV-EE/blob/main/Dockerfile)、[LunaTV-EE package.json](https://github.com/stazeng/LunaTV-EE/blob/main/package.json)、[LunaTV package.json](https://github.com/MoonTechLab/LunaTV/blob/main/package.json)。

2. **唯一的"转码"发生在客户端下载环节**，用 `mux.js` 把 TS 转成 MP4：依赖 `"mux.js": "^6.3.0"`，`src/lib/download/mp4-transmuxer.ts` 导出 `StreamingTransmuxer` 与 `transmuxTSToMP4`。来源：[LunaTV-EE package.json L52](https://github.com/stazeng/LunaTV-EE/blob/main/package.json)、[LunaTV-EE mp4-transmuxer.ts](https://github.com/stazeng/LunaTV-EE/blob/main/src/lib/download/mp4-transmuxer.ts)。

3. **AES-128 加密流在客户端解密**：用 `crypto-js`，`aesDecrypt()` 走 `AES-CBC` + `Pkcs7`，IV 从 `#EXT-X-KEY` 的 `IV=0x...` 十六进制解析，Key 通过单独 fetch `URI` 拿 `arrayBuffer` 再转 CryptoJS `WordArray`。来源：[LunaTV-EE m3u8-downloader.ts L236-312](https://github.com/stazeng/LunaTV-EE/blob/main/src/lib/download/m3u8-downloader.ts)。

4. **不支持的编码怎么兜底——靠 hls.js + 浏览器原生**：播放器内核是 `ArtPlayer 5.3.0` + `hls.js 1.6.15`；EE 版另有 `@vidstack/react` / `vidstack` / `flv.js` 处理其他容器。**没有任何"编码不支持→服务端转码"的兜底路径**；下载侧若 TS 不兼容则提示用户改用 MP4 格式（DOWNLOD_FEATURES.md Q&A 明确写"TS 格式不兼容 → 改用 MP4 格式"）。来源：[LunaTV-EE README 技术栈](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)、[LunaTV-EE DOWNLOAD_FEATURES.md](https://github.com/stazeng/LunaTV-EE/blob/main/DOWNLOAD_FEATURES.md)。

5. **"直播/直链两种模式"确实存在**：① 直播（IPTV）模式——m3u 订阅 + EPG 节目单 + 走 `/api/proxy/m3u8` 重写链路；② 直链模式——`/api/proxy/stream` 与 `/api/video-proxy` 直接代理 mp4/flv 并透传 Range；③ 此外还有"直播直连模式"（CORS 允许时绕过服务器）。来源：[LunaTV-EE src/app/api/proxy/stream/route.ts](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/stream/route.ts)、[LunaTV-EE live/precheck/route.ts](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/live/precheck/route.ts)。

6. **EPG（XMLTV）解析是流式的，避免整文件进内存**：用 `response.body.getReader()` + `TextDecoder` 逐行处理，只保留最后一行不完整缓冲（`buffer = lines.pop() || ''`），并用 `shouldSkipCurrentProgram` 提前跳过不在目标频道集合里的 `<programme>`。来源：[LunaTV lib/live.ts L97-183](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/live.ts)。

---

## 5. 下载 / 离线

1. **支持下载，且是纯客户端实现（浏览器内多线程拉分片后合并）**，不在服务端做。来源：[LunaTV-EE DOWNLOAD_FEATURES.md](https://github.com/stazeng/LunaTV-EE/blob/main/DOWNLOAD_FEATURES.md)。

2. **并发与重试有明确默认值和可调范围**：`downloadM3U8Video(..., concurrency = 6, streamMode = 'disabled', maxRetries = 3, ...)`；UI 侧下载线程数可调 **1-16**（默认 6，推荐 4-8），失败重试 **0-10** 次（默认 3，推荐 3-5）。重试延迟固定 **1000ms**（`const retryDelay = 1000`）。来源：[LunaTV-EE m3u8-downloader.ts L368-376, L549-551](https://github.com/stazeng/LunaTV-EE/blob/main/src/lib/download/m3u8-downloader.ts)、[LunaTV-EE DOWNLOAD_FEATURES.md](https://github.com/stazeng/LunaTV-EE/blob/main/DOWNLOAD_FEATURES.md)。

3. **并发模型是"共享队列 + N 个 worker 协程"**：`downloadQueue: number[]` 存片段下标，启动 `Math.min(concurrency, totalSegments)` 个 `processQueue()` 循环，每个循环 `downloadQueue.shift()` 取一个下标后 `await downloadSegment(index)`。来源：[LunaTV-EE m3u8-downloader.ts L543-546, L704-729](https://github.com/stazeng/LunaTV-EE/blob/main/src/lib/download/m3u8-downloader.ts)。

4. **分片合并策略：按索引顺序串行写入（解决"边下边存"乱序丢数据）**。核心是 `pendingWrites: Map<index, ArrayBuffer|'failed'>` + `nextWriteIndex` 游标 + `writeLock: Promise<void>` 串行化锁；`flushPendingWrites()` 只写 `nextWriteIndex` 连续可用的片段，遇到 `'failed'` 就跳过并递增游标。CHANGELOG 有对应修复条目"解决边下边存并发写入数据丢失问题"。来源：[LunaTV-EE m3u8-downloader.ts L386-509](https://github.com/stazeng/LunaTV-EE/blob/main/src/lib/download/m3u8-downloader.ts)、[LunaTV-EE CHANGELOG v5.6.x "修复流式下载并发写入"](https://github.com/stazeng/LunaTV-EE/blob/main/CHANGELOG)。

5. **三种落盘模式，按能力自动降级**：`getBestStreamMode()` 的优先级是 `file-system` > `service-worker` > `disabled`。① **文件系统直写**——`'showSaveFilePicker' in window`（Chrome/Edge）；② **Service Worker 流式**——`window.isSecureContext && 'serviceWorker' in navigator`（需 HTTPS/localhost）；③ **普通模式**——全部读进内存再 `Blob` 保存，>2GB 有风险。来源：[LunaTV-EE stream-mode-detector.ts L17-60](https://github.com/stazeng/LunaTV-EE/blob/main/src/lib/download/stream-mode-detector.ts)、[LunaTV-EE DOWNLOAD_FEATURES.md](https://github.com/stazeng/LunaTV-EE/blob/main/DOWNLOAD_FEATURES.md)。

6. **断点续传/暂停恢复是"协程式"而非 Range 式**：`PauseResumeController` 用 `isPaused` + `pausePromise`/`resumeResolve`，在 `downloadSegment` 的**4 个检查点**（下载前、下载后、解密后、状态更新前）调用 `await pauseResumeController.waitIfPaused()`。**注意：这不是 HTTP Range 续传**——没有把已完成片段持久化到磁盘后只补缺失片段的能力；"断点续传"的说法来自文档。来源：[LunaTV-EE m3u8-downloader.ts L17-58](https://github.com/stazeng/LunaTV-EE/blob/main/src/lib/download/m3u8-downloader.ts)、[LunaTV-EE DOWNLOAD_FEATURES.md "断点续传"](https://github.com/stazeng/LunaTV-EE/blob/main/DOWNLOAD_FEATURES.md)。

7. **主播放列表自动降清晰度**：`parseM3U8()` 检测到 `#EXT-X-STREAM-INF` 就递归解析（`depth > 5` 抛错防循环），从所有子播放列表里按 `BANDWIDTH=` **降序排序取最高带宽那个**（`extractSubPlaylistUrl`）。来源：[LunaTV-EE m3u8-downloader.ts L109-181](https://github.com/stazeng/LunaTV-EE/blob/main/src/lib/download/m3u8-downloader.ts)。

8. **下载侧的性能宣传数据（来自文档，非实测）**：并发 1→6 线程称"下载速度提升 6 倍"（10 秒 → 1.7 秒）；边下边存称 2GB 视频内存占用从 2GB → 50MB（降 40 倍），支持 10GB+。来源：[LunaTV-EE DOWNLOAD_FEATURES.md 性能对比表](https://github.com/stazeng/LunaTV-EE/blob/main/DOWNLOAD_FEATURES.md)。

9. **支持批量剧集下载**、片段时长追踪与时间范围显示、失败片段单独"重试"按钮、格式可选 TS/MP4。来源：[LunaTV-EE README "M3U8下载功能"、"下载功能增强：添加片段时长追踪、时间范围和完整视频总时长显示"](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)、[LunaTV-EE CHANGELOG](https://github.com/stazeng/LunaTV-EE/blob/main/CHANGELOG)。

10. **MoonTV 原始版也宣称支持下载**："视频下载：支持 M3U8 视频下载，多线程并发加速，边下边存功能（Chrome/Edge）"，且代码库有 `src/lib/m3u8-downloader.ts` + `DownloadManager.tsx` + `GlobalDownloadManager.tsx` + `AddDownloadModal.tsx` + `SegmentViewer.tsx` + `ServiceWorkerRegistration.tsx`。来源：[MoonTV(Branch) README 功能特性](https://github.com/brucemomo/Moon_TV/blob/main/README.md)、[Moon_TV 仓库文件树](https://github.com/brucemomo/Moon_TV/tree/main)。

11. **MoonTV 分支的下载代理路由是 edge runtime 且整段缓冲**：`brucemomo/Moon_TV` 的 `GET /api/m3u8/proxy?url=xxx` 走 `downloadTsSegment(url)` 把整个 TS 读成 ArrayBuffer 再返回，响应头 `Content-Type: application/octet-stream` + `Cache-Control: public, max-age=31536000`（1 年）。对比 LunaTV-EE 的流式 64KB 背压方案，这是明显的性能/内存差异点。来源：[Moon_TV src/app/api/m3u8/route.ts](https://github.com/brucemomo/Moon_TV/blob/main/src/app/api/m3u8/route.ts)。

---

## 6. 弹幕

1. **主用弹幕源是一个自建/第三方 `danmu_api` 兼容服务**，默认 `https://smonedanmu.vercel.app`，默认 token `smonetv`，可在管理后台改（`DanmuApiConfig.useCustomApi` + `customApiUrl` + `customToken` + `timeout`）。MoonTV 原始版的 README 写的是"以 [danmu_api](https://github.com/huangxd-/danmu_api) 为后端，需自行部署"，环境变量 `NEXT_PUBLIC_DANMU_API_BASE_URL`。来源：[LunaTV-EE danmu-external/route.ts L10-11,L41-75](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/danmu-external/route.ts)、[MoonTV(Branch) README 弹幕支持](https://github.com/brucemomo/Moon_TV/blob/main/README.md)。

2. **主用链路是三步 API 调用**（`api/v2`）：
   `GET {base}/{token}/api/v2/search/anime?keyword=<title>` → 拿 `animes[].animeId`；
   `GET {base}/{token}/api/v2/bangumi/{animeId}` → 拿 `bangumi.episodes[].episodeId`；
   `GET {base}/{token}/api/v2/comment/{episodeId}?format=json` → 拿 `comments[]`。
   来源：[LunaTV-EE danmu-external/route.ts L94-202](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/danmu-external/route.ts)。

3. **匹配策略：标题优先 + 年份加权**。先用 `keyword=<title>` 搜索（注释明确"只用标题搜索，不带年份，年份用于后续匹配筛选"），再遍历 `animes` 做 `animeTitle.includes(searchTitle) || searchTitle.includes(animeTitle.split('(')[0].trim())`，**有年份且标题也匹配时立即 break 选它**，否则取第一个标题匹配项。集数按 `episodeNum - 1` 直接索引 `episodes`。来源：[LunaTV-EE danmu-external/route.ts L117-173](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/danmu-external/route.ts)。

4. **备用链路：豆瓣页提取平台链接 → 平台弹幕 API**。① `extractPlatformUrls(doubanId, episode)` 从 `https://movie.douban.com/subject/{id}/` 的 HTML 里正则抓 `play_link:"...v.qq.com..."` / `iqiyi.com` / `youku.com` / `bilibili.com`，并做移动版→PC 版链接转换（`m.youku.com/alipay_video/id_x` → `v.youku.com/v_show/id_x.html` 等）；② 豆瓣拿不到就退到 caiji 采集 API `https://www.caiji.cyou/api.php/provide/vod/?wd=` + `?ac=detail&ids=`。来源：[LunaTV-EE danmu-external/route.ts L363-543, L556-797](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/danmu-external/route.ts)。

5. **"智能内容匹配防预告" = 关键词黑名单跳过**：候选结果里 `vod_name` 含 `解说` / `预告` / `花絮` / `动态漫` / `之精彩` 的直接 `continue` 跳过；另优先取标题**完全相等**的结果。来源：[LunaTV-EE danmu-external/route.ts L415-435](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/danmu-external/route.ts)。

6. **屏蔽规则：只有【服务端硬编码过滤器】，没有用户可配的"关键词/正则/类型/颜色"屏蔽（重要澄清）**。全仓库搜索"屏蔽"**0 命中**；实际生效的是 `danmu-external/route.ts` 里的硬编码预过滤：
   - 长度：`length === 0 || length > 50 || length < 2` 全丢；
   - 纯符号 / 纯数字 / 纯标点：`/^[^\u4e00-\u9fa5a-zA-Z0-9]+$/`、`/^\d+$/`、`/^[!@#$%^&*()_+\-=[\]{};':"\\|,.<>/?]+$/`；
   - 占位/灌水词：`弹幕正在赶来`、`观影愉快`、`视频不错`、`666`；
   - 平台水印：`官方弹幕库`、`哔哩哔哩`。
   时间范围校验 `time < 0 || time > 86400 || !Number.isFinite(time)`。
   > 注：`DanmuSettingsPanel.tsx` 是 UI 面板（字号/速度/透明度/显示区域/防重叠等渲染项），**其"按类型屏蔽"等设置未在服务端过滤链路中查到对应实现**，如需可自行核对。
   来源：[LunaTV-EE danmu-external/route.ts L227-242, L864-876, L955-958](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/danmu-external/route.ts)、[DanmuSettingsPanel.tsx](https://github.com/stazeng/LunaTV-EE/blob/main/src/components/play/DanmuSettingsPanel.tsx)。

7. **防卡顿的关键设计：时间分段 + 段内密度上限 + 批量让出主线程 + 全局智能采样**：
   - `SEGMENT_DURATION = 300`（**按 5 分钟一段**分桶存储/排序）；
   - `MAX_DANMU_PER_SEGMENT = 500`（**每段最多 500 条**，超了有 10% 概率随机替换以保持多样性）；
   - `BATCH_SIZE = 200`（**每 200 条 `await new Promise(r => setTimeout(r, 0))` 让出事件循环**）；
   - `maxAllowedDanmu = 20000`（**全局上限 2 万条**，超出按 `sampleRate` 均匀+随机混合采样，并强制保留首尾条）；
   - 少于 **10 条**判为无效，返回 `null` 让备用方案接管。
   来源：[LunaTV-EE danmu-external/route.ts L204-321](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/danmu-external/route.ts)。

8. **去重键 = 时间(精确到 0.01 秒) + 归一化小写文本 + 颜色**：`${Math.round(time*100)/100}_${text.trim().toLowerCase()}_${color}`，去重时每 100 条 `setTimeout(0)` 让出一次。来源：[LunaTV-EE danmu-external/route.ts L338-360, L1294-1321](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/danmu-external/route.ts)。

9. **渲染侧的性能分级（有确切公式，很适合移植到桌面端）**：三个档位的综合性能评分
   `score = min(hardwareConcurrency/4, 1) * 0.5 + min(deviceMemory_GB/1, 1) * 0.3 + (isMobile ? 0.2 : 0.5) * 0.2`
   → `> 0.7 = high` / `> 0.4 = medium` / 否则 `low`。
   三档差异：**同屏上限 60 / 40 / 25 条**；**丢弃率 0.1 / 0.3 / 0.5**；`useWorker` **三档全为 true**（弹幕位置计算放 Web Worker）；`low` 档额外加 `maxLength: 30` + CSS `translateZ(0)` 硬件加速。客户端用 `artplayer-plugin-danmuku 5.2.0`。
   来源：[LunaTV-EE play/page.tsx L3975-4141](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/play/page.tsx)、[CHANGELOG v4.x 弹幕性能条目](https://github.com/stazeng/LunaTV-EE/blob/main/CHANGELOG)、[README 弹幕生态系统](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)。

10. **弹幕缓存 30 分钟，且是"双层"的**：`DANMU_CACHE_DURATION = 30 * 60`（秒），主存走服务端 `/api/cache`（`ClientCache.get/set`），**localStorage 作兜底兼容**（`enable_external_danmu` 也存 localStorage，默认关闭）。来源：[LunaTV-EE useDanmu.ts L62-133, L153-156](https://github.com/stazeng/LunaTV-EE/blob/main/src/hooks/useDanmu.ts)、[LunaTV-EE client-cache.ts](https://github.com/stazeng/LunaTV-EE/blob/main/src/lib/client-cache.ts)。

11. **可调节项**：字号、速度（`danmaku_speed`，默认 5）、透明度、显示区域、**防重叠开关**、**实时滑块控制**、按类型屏蔽。来源：[LunaTV-EE README 弹幕系统](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)、[LunaTV-EE DanmuSettingsPanel.tsx](https://github.com/stazeng/LunaTV-EE/blob/main/src/components/play/DanmuSettingsPanel.tsx)。

12. **直播源与弹幕的关系：未查到**任何把弹幕挂到 IPTV 直播频道上的实现。弹幕只挂在点播（电影/剧集/动漫/短剧）上，走的是"标题+集数"匹配，而直播侧走的是 m3u 频道 + EPG 节目单，两者数据结构不通。

13. **弹幕结果格式（可直接复用）**：返回 `{ danmu: [{text, time, color, mode}], platforms: [{platform, source/count, url?}], total }`；`mode` 归一化为 `0=滚动 / 1=顶部 / 2=底部`（源侧 `mode===4→1`，`mode===5→2`，其余 `0`）。来源：[LunaTV-EE danmu-external/route.ts L25-30, L258-270, L1172-1176](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/danmu-external/route.ts)。

14. **XML 弹幕解析做了双源 failover**：`['https://fc.lyz05.cn', 'https://danmu.smone.us']` 逐个尝试，15 秒超时，正则 `/<d p="([^"]*)"[^>]*>([^<]*)<\/d>/g`，`p` 属性格式 `time,mode,color,[source]`（**color 在第 4 个字段**，与 JSON API 的 `[time, 位置, 颜色, "", 文本]` 不同）；再从 `https://api.danmu.icu/?url=`（带 `Referer: https://danmu.icu/`）作为二级备用，且按平台差异化超时：**爱奇艺 30 秒 / 优酷 25 秒 / 芒果 25 秒 / 其他 20 秒**。来源：[LunaTV-EE danmu-external/route.ts L800-1011, L1014-1120](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/danmu-external/route.ts)。

---

## 7. 用户数据

1. **多用户 + 密码 + 角色三级**：`role: 'user' | 'admin' | 'owner'`；`USERNAME`/`PASSWORD` 环境变量定义站长（owner），`NEXT_PUBLIC_ENABLE_REGISTER` 控制是否开放注册（默认 false）。来源：[LunaTV lib/admin.types.ts L21-33](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/admin.types.ts)、[MoonTV(Branch) README 环境变量表](https://github.com/brucemomo/Moon_TV/blob/main/README.md)。

2. **密码存储是加盐哈希，并有明文→哈希的迁移任务**：`IStorage.migratePasswords?()`、`src/lib/password.ts`、迁移标记 key `sys:migration:pwd_hash_v1`；LunaTV-EE 更进一步做 **SHA256 的 V2 用户存储系统**。来源：[LunaTV lib/types.ts L88-91](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/types.ts)、[LunaTV lib/redis-base.db.ts L546-571](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/redis-base.db.ts)、[LunaTV-EE README "V2用户存储系统：SHA256加密"](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)。

3. **Redis 上的 key 结构（Hash 化设计，一用户一 Hash）**：
   - 播放记录：`u:{username}:pr` —— 单 Hash，field 为记录 key，value 为 `JSON.stringify(PlayRecord)`；
   - 收藏：`u:{username}:fav` —— 单 Hash；
   - 跳过片头尾：`u:{username}:skip` —— 单 Hash；
   - 搜索历史：`u:{username}:sh` —— **List**，`lPush` + `lTrim(0, SEARCH_HISTORY_LIMIT-1)` + `lRem` 去重；
   - 密码：`u:{username}:pwd`（String）；
   - 用户集合：`sys:users`（Set）；
   - 管理员配置：`sys:admin:config`（`adminConfigKey()`，String）；
   - 迁移标记：`sys:migration:hash_v2`。
   来源：[LunaTV lib/redis-base.db.ts L155-157, L205-207, L251, L316, L348, L373-374, L434, L546](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/redis-base.db.ts)。

4. **有一次"扁平 key → Hash"的数据迁移**：从旧结构 `u:*:pr:*` / `u:*:fav:*` / `u:*:skip:*`（用 `KEYS` 扫描 + `mGet`）搬到新 Hash 结构，然后 `del` 旧的；标记写 `sys:migration:hash_v2 = 'done'`。来源：[LunaTV lib/redis-base.db.ts L439-537](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/redis-base.db.ts)。

5. **Redis 操作全部包了一层重试**：`withRetry(operation, maxRetries?)`，由 `createRetryWrapper(clientName, () => this.client)` 生成；连接首次失败会 5 秒后重试。来源：[LunaTV lib/redis-base.db.ts L29-47, L109-151](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/redis-base.db.ts)。

6. **播放记录的数据结构（可直接照抄的字段设计）**：
   `PlayRecord { title, source_name, cover, year, index(第几集), total_episodes, play_time(秒), total_time(秒), save_time(时间戳), search_title }`。
   来源：[LunaTV lib/types.ts L3-15](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/types.ts)。

7. **收藏的数据结构**：`Favorite { source_name, total_episodes, title, year, cover, save_time, search_title, origin?: 'vod' | 'live' }`。LunaTV-EE 加了"全局收藏 + 跨设备同步 + 分类筛选（电影/剧集/综艺/短剧/番剧）"。来源：[LunaTV lib/types.ts L17-27](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/types.ts)、[LunaTV-EE README 全局收藏功能](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)。

8. **跳过片头尾的数据结构**：`SkipConfig { enable: boolean, intro_time: number(秒), outro_time: number(秒) }`，按 `(user, source, id)` 三元组存取（`skipField(source, id)`）。来源：[LunaTV lib/types.ts L127-132](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/types.ts)、[LunaTV lib/redis-base.db.ts L373-424](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/redis-base.db.ts)。

9. **管理员配置是一整块 JSON**（`AdminConfig`），字段见下：`ConfigSubscribtion{URL,AutoUpdate,LastCheck}`、`ConfigFile`（原始 config.json 字符串）、`SiteConfig{SiteName, Announcement, SearchDownstreamMaxPage, SiteInterfaceCacheTime, DoubanProxyType, DoubanProxy, DoubanImageProxyType, DoubanImageProxy, DisableYellowFilter, FluidSearch, EnableWebLive}`、`UserConfig{Users[], Tags[]}`、`SourceConfig[]`、`CustomCategories[]`、`LiveConfig[]`。来源：[LunaTV lib/admin.types.ts 全文](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/admin.types.ts)。

10. **配置读取有 5 分钟 TTL 内存缓存 + 在途请求合并**：`CONFIG_TTL_MS = 5 * 60 * 1000`；`pendingConfig` 保证冷启动并发不打库；读库失败时**回退旧缓存**避免整站不可用；只在首次初始化时回写，避免每次 TTL 过期都写库。来源：[LunaTV lib/config.ts L57-63, L299-346](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/config.ts)。

11. **按用户/用户组限制可用源**：`getAvailableApiSites(user)` 先看 `userConfig.enabledApis`（优先级更高），再看 `userConfig.tags` 对应的 `Tags[].enabledApis` 并集。来源：[LunaTV lib/config.ts L452-500](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/config.ts)。

12. **其他持久化项**：搜索历史、跳过配置、数据导入导出（`/api/admin/data_migration/export|import`）、LunaTV-EE 的播放统计（`/api/play-stats`、`/api/user/my-stats`）、日历缓存（从 localStorage 迁到数据库，TTL 从 24h 改 8h）、信任网络配置（24h 缓存 + cookie 版本号立即失效）。来源：[LunaTV 目录树](https://github.com/MoonTechLab/LunaTV/tree/main/src/app/api)、[LunaTV-EE README 日历缓存迁移](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)。

---

## 8. UI / 交互

1. **响应式布局：桌面侧边栏 + 移动底部导航**，Tailwind 断点切换；LunaTV-EE 重设计为"桌面水平顶部导航 + 移动 Liquid Glass 底部导航"。来源：[MoonTV(Branch) README 响应式布局](https://github.com/brucemomo/Moon_TV/blob/main/README.md)、[LunaTV-EE README 现代化导航UI](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)。

2. **TV 端适配是"当后端用"，不是自建 TV UI**：项目可作为 `OrionTV`（Android TV，`zimplexing/OrionTV`）和 `Selene`（移动端）的后端；另提供 TVBox 订阅接口 `https://你的域名/api/tvbox/config?pwd=你的口令`（localstorage 模式由 `TVBOX_ENABLED` 控制）。来源：[MoonTV(Branch) README AndroidTV 使用 / TVBox 对接 / Selene 使用](https://github.com/brucemomo/Moon_TV/blob/main/README.md)。

3. **PWA**：`next-pwa`（`dest: 'public'`、`register: true`、`skipWaiting: true`、开发环境禁用），支持离线缓存与安装到桌面。来源：[LunaTV next.config.js L72-79](https://github.com/MoonTechLab/LunaTV/blob/main/next.config.js)。

4. **播放器内核是 `<video>` + hls.js，包在 ArtPlayer 里**：依赖 `artplayer ^5.3.0` + `hls.js ^1.6.15`（EE 版另有 vidstack / flv.js）；EE 版为规避 SSR 问题**动态 import** ArtPlayer（CHANGELOG："通过动态导入解决服务端渲染时的 window 对象访问错误"）。来源：[LunaTV-EE package.json](https://github.com/stazeng/LunaTV-EE/blob/main/package.json)、[LunaTV-EE CHANGELOG v5.2.1](https://github.com/stazeng/LunaTV-EE/blob/main/CHANGELOG)。

5. **原生 HLS 优先（Safari/iOS）**：代码路径是 `if (video.hls) video.hls.destroy()` 后按需 `new Hls({...})`；`testBandwidth: !localIsIOS13` 在 iOS13+ 关闭带宽测试以**快速启动**。来源：[LunaTV-EE play/page.tsx L3706-3756](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/play/page.tsx)。

6. **快捷键 / 交互**：长按菜单（`useLongPress.ts`）、右键/长按上下文菜单（视频卡片与短剧卡片）、触摸滑动手势（移动端横幅）、返回顶部按钮、`<FastLink>`（基于 transition 的快速导航，且加 `prefetch={false}`）。**未查到**键盘快捷键映射表（如方向键/空格的自定义绑定）。来源：[LunaTV src/hooks/useLongPress.ts](https://github.com/MoonTechLab/LunaTV/blob/main/src/hooks/useLongPress.ts)、[LunaTV-EE README 界面体验优化 / FastLink](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)。

7. **缓冲预加载策略——这是对"卡顿"最直接的答案**（EE 版 `play/page.tsx` 里的 hls.js 配置，桌面取用户配置 / 移动端硬编码）：

   | 配置项 | 桌面 | iOS13+ | iOS | Android |
   |---|---|---|---|---|
   | `maxBufferLength` | 用户模式：30 / 45 / 90 s | 8 | 10 | 15 |
   | `backBufferLength` | 30 / 45 / 60 s | 5 | 8 | 10 |
   | `maxBufferSize` | 60 / 90 / 180 MB | 20 MB | 30 MB | 40 MB |
   | `maxMaxBufferLength` | 600 | 60 | 120 | 120 |
   | `maxLoadingDelay` | 4 | 2 | 3 | 3 |
   | `maxBufferHole` | 0.1 | 0.05 | 0.1 | 0.1 |
   | `startFragPrefetch` | `true` | `false` | `false` | `false` |
   | `testBandwidth` | `true` | `false` | `true` | `true` |
   | `lowLatencyMode` | `true` | `false` | `false` | `false` |
   | `enableWorker` | `true` | `true` | `true` | `true` |

   另有 `fragLoadPolicy.default`：`maxTimeToFirstByteMs` 桌面 10000 / 移动 6000；`maxLoadTimeMs` 桌面 120000 / 移动 60000；`timeoutRetry.maxNumRetry` 桌面 4 / 移动 2；`errorRetry.maxNumRetry` 桌面 6 / 移动 3，`retryDelayMs: 1000`，`maxRetryDelayMs` 桌面 8000 / 移动 4000；`liveDurationInfinity: false`。
   来源：[LunaTV-EE play/page.tsx L224-255（三种模式）, L3705-3784（hls.js 配置）](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/play/page.tsx)。

8. **三种缓冲模式的命名与代码不一致（重要澄清）**：
   - README `L164` 的原文是 **"三种缓冲模式（省流、均衡、高质）"**，但**"省流/均衡/高质"这三个词在源码里不存在**；
   - 代码实际取值是 `standard` / `enhanced` / `max`（`getHlsBufferConfig()` 的 switch），UI 文案是 **"默认模式 / 增强模式 / 强力模式"**；
   - 精确值（`play/page.tsx:225-255`）：`standard {maxBufferLength:30, backBufferLength:30, maxBufferSize:60000000}`；`enhanced {45, 45, 90000000}`；`max {90, 60, 180000000}`；
   - 持久化 key：`localStorage['playerBufferMode']`，**全局唯一读取点** `play/page.tsx:3714`；
   - **【重要】仅桌面生效**：移动端走硬编码（8/10/15s），完全忽略用户选择。
   来源：[LunaTV-EE play/page.tsx L224-255, L3714](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/play/page.tsx)、[LunaTV-EE README L164](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)。

9. **ABR 参数**：`abrEwmaFastLive` 桌面 3 / 移动 2；`abrEwmaSlowLive` 桌面 9 / 移动 6；`abrBandWidthFactor` 桌面 0.95 / 移动 0.8；另有 `abrBandWidthUpFactor` 移动 0.6 / 桌面 0.7、`abrMaxWithRealBitrate: true`、`abrEwmaDefaultEstimate` 按设备性能 1.5/3/6 Mbps、`maxStarvationDelay` 移动 2 / 桌面 4。来源：[LunaTV-EE play/page.tsx L3749-3756](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/play/page.tsx)、[LunaTV-EE lib/utils.ts L264-271](https://github.com/stazeng/LunaTV-EE/blob/main/src/lib/utils.ts)。

10. **预加载：播放链路零 preload / prefetch / preconnect（重要纠正）**。全仓库 `preconnect` / `dns-prefetch` **0 命中**；播放链路没有任何预加载。仅有的两处相关实现是：① `useImagePreload.ts:48-56` 对图片注入 `<link rel="preload" as="image" fetchPriority="low">`（最多 10 张）；② `EpisodeSelector.tsx:111-143, 191-219` 的**分批测速**会真实拉 m3u8 + 首个分片（但要切到"换源"tab 才触发）。`play/page.tsx:2944` 的注释写的是 `requestIdleCallback`，**实际实现是 `setTimeout((i+1)*100)`**。另外 `<FastLink.tsx:105>` 主动**关闭**了 Next.js 的 prefetch。来源：[LunaTV-EE useImagePreload.ts](https://github.com/stazeng/LunaTV-EE/blob/main/src/hooks/useImagePreload.ts)、[LunaTV-EE FastLink.tsx](https://github.com/stazeng/LunaTV-EE/blob/main/src/components/FastLink.tsx)、[LunaTV-EE play/page.tsx L2944](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/play/page.tsx)。

---

## 9. 性能优化细节（与"卡顿/帧率/延迟/首屏"直接相关）

1. **并发搜索**：所有源并行请求（`apiSites.map(...)` → `Promise.allSettled`），单源 20 秒总超时 + 8 秒 fetch 超时；多页也是并发 `Promise.all`。来源：[LunaTV search/route.ts](https://github.com/MoonTechLab/LunaTV/blob/main/src/app/api/search/route.ts)、[LunaTV lib/downstream.ts](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/downstream.ts)。

2. **SSE 流式搜索（首屏时间的关键）**：`GET /api/search/ws?q=<kw>` 用 `ReadableStream` + `text/event-stream` **逐源推送**结果，事件类型 `start` / `source_result` / `source_error` / `complete`，事件里带 `timestamp`；有 `streamClosed` 标志在客户端断开时停止处理。这样"最快的源先出结果"，而不是等最慢的源。来源：[LunaTV search/ws/route.ts L40-188](https://github.com/MoonTechLab/LunaTV/blob/main/src/app/api/search/ws/route.ts)。

3. **服务端缓存头的具体写法**（搜索结果按 `cache_time` 默认 7200 秒下发 4 个 CDN 缓存头）：
   ```
   Cache-Control: public, max-age=<cacheTime>, s-maxage=<cacheTime>
   CDN-Cache-Control: public, s-maxage=<cacheTime>
   Vercel-CDN-Cache-Control: public, s-maxage=<cacheTime>
   Netlify-Vary: query
   ```
   并且**空结果不下发缓存**（注释 "no cache if empty"）以防缓存污染。来源：[LunaTV search/route.ts L22-34, L78-88](https://github.com/MoonTechLab/LunaTV/blob/main/src/app/api/search/route.ts)。

4. **代理分片缓存 5 分钟**：`headers.set('Cache-Control', 'public, max-age=300')`；而 m3u8 播放列表则 `no-store`。这个区分很关键——分片可缓存、列表不可缓存。来源：[LunaTV-EE proxy/segment/route.ts L110](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/segment/route.ts)、[LunaTV-EE proxy/m3u8/route.ts L148](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/m3u8/route.ts)。

5. **Range 请求**：`/api/proxy/stream` 与 `/api/video-proxy` 完整支持——透传 `Range`、回传 `Accept-Ranges: bytes` / `Content-Range` / `206` 状态码；`/api/video-proxy` 的 HEAD 方法专门用于"获取视频元数据"（`max-age=3600, stale-while-revalidate=1800`）。来源：[LunaTV-EE proxy/stream/route.ts](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/stream/route.ts)、[LunaTV-EE video-proxy/route.ts L22, L279-331](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/video-proxy/route.ts)。

6. **两层本地缓存（Kvrocks 元数据 + 文件系统视频体）——本项目最狠的省流量机制**：
   - Kvrocks key 前缀：`trailer:url:{douban_id}`、`video:meta:{url_hash}`、`video:total_size`、`video:lru`（Sorted Set，score = 访问时间戳）；
   - `URL_TTL = 15 * 60`（900 秒，因为豆瓣 URL 约 15-20 分钟过期）；`VIDEO_TTL = 12 * 60 * 60`（43200 秒）；`MAX_CACHE_SIZE = 2 * 1024 * 1024 * 1024`（2GB）；目录 `VIDEO_CACHE_DIR`（默认 `/tmp/video-cache`，Docker 里挂 volume 到 `/app/video-cache`）；
   - **缓存 key 优先用 `douban_id`**（正则 `/\/M\/(\d+)\.mp4/` 从 URL 提取），命中不了才降级用 `sha256(url)`——这样 URL 带时间戳刷新后仍能命中同一视频；
   - 实测宣称"28 次请求从 932MB 降至 33MB"，流量节省 **96%**，响应从秒级降到毫秒级。
   来源：[LunaTV-EE video-cache.ts L34-88](https://github.com/stazeng/LunaTV-EE/blob/main/src/lib/video-cache.ts)、[LunaTV-EE README "视频缓存优化（Kvrocks）"](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)、[LunaTV-EE CHANGELOG v6.1.1 "视频缓存LRU淘汰"](https://github.com/stazeng/LunaTV-EE/blob/main/CHANGELOG)。

7. **缓存命中直接走本地文件流（带 Range）**：命中后 `serveVideoFromFile()` 用 `fs.stat` 拿文件大小、`createReadStream(path, {start, end})` 返回，响应头 `Accept-Ranges: bytes` + `Cache-Control: public, max-age=7200`（2 小时）；非 Range 请求返回 200 + 完整 `Content-Length`。来源：[LunaTV-EE video-proxy/route.ts L38-55, L336-374](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/video-proxy/route.ts)。

8. **缓存失效与重验证**：源返回 **403/404 时主动 `deleteVideoCache(videoUrl)`**（防缓存住失效 URL）；支持 `If-None-Match` / `If-Modified-Since` 与 **304** 短路；视频响应头用 `public, max-age=1800, stale-while-revalidate=900, must-revalidate` + `CDN-Cache-Control: public, s-maxage=1800, stale-while-revalidate=900`。来源：[LunaTV-EE video-proxy/route.ts L110-151, L178-187](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/video-proxy/route.ts)。

9. **Node HTTP 连接池（keep-alive）减少 TCP/TLS 握手**：分片代理 `maxSockets: 100` / `keepAliveMsecs: 30000`；m3u8 代理 `maxSockets: 50`。来源：[LunaTV-EE proxy/segment/route.ts L13-27](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/segment/route.ts)。

10. **流式背压（64KB highWaterMark）防止把大分片堆进 Node 堆内存**——直接对应用户关心的"卡顿"。来源：[LunaTV-EE proxy/segment/route.ts L221-227](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/segment/route.ts)。

11. **超时与重试矩阵**（可直接抄的参数表）：
    - 采集源搜索 `fetch`：**8000 ms**（AbortController）；
    - 采集源详情：**10000 ms**；
    - `/api/search` 单源总超时：**20000 ms**；
    - `/api/admin/source/validate` 单源：**10000 ms**；
    - `/api/proxy/m3u8`：**15000 ms**；
    - `/api/proxy/segment`：**30000 ms**；
    - `/api/proxy/cms`：**20000 ms**；
    - `/api/video-proxy`：**30000 ms**；
    - `/api/live/precheck`：**8000 ms**（`AbortSignal.timeout`）；
    - `/api/proxy-status` Worker 健康检查：**5000 ms**。
    来源：[LunaTV lib/downstream.ts L29,L148,L173,L211](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/downstream.ts)、[LunaTV search/route.ts L51](https://github.com/MoonTechLab/LunaTV/blob/main/src/app/api/search/route.ts)、[LunaTV-EE proxy/*.ts](https://github.com/stazeng/LunaTV-EE/tree/main/src/app/api/proxy)。

12. **前端渲染优化（帧率相关）**：`react-window 2.2.3` DOM 虚拟化 + `react-window-infinite-loader`；**所有首页 `ScrollableRow` 启用虚拟化**（CHANGELOG 直言"修复移动端卡顿问题"）；`ResizeObserver` 检测容器尺寸算 2-8 列自适应网格；搜索结果**渐进式加载（初始 12 项，滚动加载更多）**。来源：[LunaTV-EE README 虚拟滚动/响应式网格](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)、[LunaTV-EE CHANGELOG v4.3.6](https://github.com/stazeng/LunaTV-EE/blob/main/CHANGELOG)、[LunaTV-EE CHANGELOG "首页所有ScrollableRow启用虚拟化"](https://github.com/stazeng/LunaTV-EE/blob/main/CHANGELOG)。

13. **Next.js 包体积优化**：`experimental.optimizePackageImports: ['lucide-react','@heroicons/react','framer-motion','react-icons']`；`images.unoptimized: true`（因为代理图片与 Next 图片优化不兼容）；`serverExternalPackages: ['@sparticuz/chromium','puppeteer-core']`。来源：[LunaTV-EE next.config.js L24-49](https://github.com/stazeng/LunaTV-EE/blob/main/next.config.js)、[LunaTV next.config.js L18-30](https://github.com/MoonTechLab/LunaTV/blob/main/next.config.js)。

14. **主线程让出（避免 UI 卡住）**：弹幕处理用 `await new Promise(r => setTimeout(r, 0))` 每 200 条让出一次；详情请求用 `requestIdleCallback` 延迟；弹幕剩余部分用 `requestIdleCallback` 分批加载；CS 滚动动画用 `requestAnimationFrame`；豆瓣页面加"智能防抖 + `flushSync`"。来源：[LunaTV-EE danmu-external/route.ts L276-278](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/danmu-external/route.ts)、[LunaTV-EE CHANGELOG "延迟详情请求：使用requestIdleCallback"](https://github.com/stazeng/LunaTV-EE/blob/main/CHANGELOG)、[LunaTV-EE play/page.tsx L2944](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/play/page.tsx)。

15. **Redis 侧的性能修正**：CHANGELOG 明确"**KEYS 替换为 SCAN 避免阻塞**，Upstash 减少命令使用量"——`KEYS` 在 Redis 上是 O(N) 阻塞命令，这是很实际的坑。来源：[LunaTV-EE CHANGELOG v6.1.1 Changed](https://github.com/stazeng/LunaTV-EE/blob/main/CHANGELOG)。

16. **Cron 任务优化**：并发处理 + 超时控制 + 监控 + 重试机制 + 并行化；CHANGELOG 记录"将 cron 任务超时从 30 秒增加到 **5 分钟**"、"将所有定时任务优化为 **1 小时间隔**"、"可配置 cron 任务优化以减少出站流量"。来源：[LunaTV-EE CHANGELOG](https://github.com/stazeng/LunaTV-EE/blob/main/CHANGELOG)、[LunaTV-EE cron/route.ts](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/cron/route.ts)。

17. **直播频道/EPG 优化**：频道数据内存缓存（`cachedLiveChannels` 模块级对象，`deleteCachedLiveChannels(key)` 手动失效）；EPG 流式逐行解析 + 提前跳过非目标频道；CHANGELOG"直播频道性能优化：添加超时控制和 EPG 缓存"、"直播源数据刷新改为并发刷新"。来源：[LunaTV lib/live.ts L28-49, L83-183](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/live.ts)、[LunaTV-EE CHANGELOG](https://github.com/stazeng/LunaTV-EE/blob/main/CHANGELOG)。

18. **搜索缓存 LRU（见 §2 第 12 条）**：10 分钟 TTL / 1000 条上限 / 5 分钟清理 / 负缓存。

19. **选源测速的实测算法**（注意：**不是**"测本机带宽→推荐缓冲模式"，README 的"自动推荐最优缓冲模式 / 智能提前停止机制"在代码中**无实现**，只存在于 README 与 `lib/changelog.ts`；实际是**源优选测速**，UI 是 Netflix 风格红进度条 `SpeedTestProgress.tsx`）：
    - **桌面**：`concurrency = 3` 小批量并发，**最多测 20 个源**，采样策略 = **权重最高的前 5 个 + 从剩余源里随机 15 个**（`maxTestCount = 20`, `topPriorityCount = 5`）；每批之间还有间隔；
    - **移动端**：不跑完整测速，只做 `HEAD + AbortSignal.timeout(3000)`；**iOS13+/iPad 完全跳过测速**，直接取权重最高的源（同权重按 `['ok','niuhu','ying','wasu','mgtv','iqiyi','youku','qq']` 顺序）；
    - ping：`fetch(m3u8Url, {method:'HEAD', mode:'no-cors'})` + `performance.now()`；
    - 画质：hls.js 加载首个分片后读 `video.videoWidth` 分档（`>=3840→4K`、`>=2560→2K`、`>=1920→1080p`、`>=1280→720p`、`>=854→480p`、否则 `SD`）；
    - 速度：监听 `Hls.Events.FRAG_LOADING` / `FRAG_LOADED`，`speedKBps = payload.byteLength / 1024 / (loadTime/1000)`；
    - **"提前停止"的真实含义**：拿到元数据 + **1 个分片**就 `cleanup()` 结束（不是"足够快就停整个测速"）；整体超时 **移动 3000 ms / 桌面 4000 ms**；
    - **早停阈值（保守，page.tsx:1446-1466）**：`quality === '4K' && speedMBps >= 8` 或 `quality === '2K' && speedMBps >= 6` → `shouldStop = true`；
    - iPad 特殊处理：只做 2 秒 HEAD ping，`quality`/`loadSpeed` 都返回"未知"以免崩溃。
    来源：[LunaTV-EE lib/utils.ts L166-424](https://github.com/stazeng/LunaTV-EE/blob/main/src/lib/utils.ts)、[LunaTV-EE play/page.tsx L1358-1466](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/play/page.tsx)、[SpeedTestProgress.tsx](https://github.com/stazeng/LunaTV-EE/blob/main/src/components/play/SpeedTestProgress.tsx)。

20. **测速后的加权打分**：分辨率 **40%** + 速度 **40%** + 延迟 **20%**；`speedScore = min(100, max(0, speedKBps / maxSpeed * 100))`，速度未知给 30 分；源权重另加成 `weightBonus = 1 + (weight - 50) * 0.005`。来源：[LunaTV-EE play/page.tsx L1607-1623](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/play/page.tsx)、[source-weights/route.ts](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/source-weights/route.ts)。

21. **Anime4K WebGPU 超分**：`anime4k-webgpu ^1.0.0`，可开关 + 模式（代码里有 `ModeC (高质)`）+ 倍率，且 CHANGELOG "优化 Anime4K requestAnimationFrame：减少 CPU 使用率"——说明它是**可选且耗 GPU**的，不是默认开启。来源：[LunaTV-EE package.json L38](https://github.com/stazeng/LunaTV-EE/blob/main/package.json)、[LunaTV-EE play/page.tsx L3915](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/play/page.tsx)、[LunaTV-EE CHANGELOG](https://github.com/stazeng/LunaTV-EE/blob/main/CHANGELOG)。

22. **本地/内存缓存的统一抽象**：`unified-cache.ts` 的 `set<T>(key, data, ttl = 3600)`（**默认 1 小时**）；`database-cache.ts` / `calendar-cache.ts` / `shortdrama-cache.ts` / `tmdb-cache.ts` / `cache-headers.ts`(未在仓库中, **未查到**)；客户端 `ClientCache` 走 `/api/cache?key=` 的 GET/POST/DELETE。来源：[LunaTV-EE unified-cache.ts L78-84](https://github.com/stazeng/LunaTV-EE/blob/main/src/lib/unified-cache.ts)、[LunaTV-EE client-cache.ts](https://github.com/stazeng/LunaTV-EE/blob/main/src/lib/client-cache.ts)。

---

## 10. 已知限制与坑

1. **Vercel 免费版 10 秒函数执行时间限制**——README 原文："Vercel 免费版有 10 秒函数执行时间限制，某些耗时操作可能超时"；另有"每月 100GB 流量"限制和"冷启动约 1-3 秒"。来源：[LunaTV-EE README Vercel 部署注意事项](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)。

2. **Vercel 不支持视频缓存**：README 原文："Vercel 无持久化文件系统，无法使用视频缓存功能（视频仍可正常播放，只是每次都需要代理请求）"；受限功能还包括"大量并发搜索请求 / 超长视频的弹幕加载 / 复杂的数据统计分析"。EdgeOne Pages 同样"不支持视频缓存"且"单次请求有执行时间限制（通常 30 秒）"。来源：[LunaTV-EE README](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)。

3. **Hugging Face Space 免费版 48 小时无访问自动休眠 + 冷启动 30-60 秒 + 无持久化存储**（必须用 Upstash）。来源：[LunaTV-EE README HF Space 注意事项](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)。

4. **带宽是硬瓶颈，README 给了明确阈值**："网络带宽低：**上行带宽低于 5Mbps 会导致视频播放卡顿**"；服务器最低配置建议 2 核 2GB（推荐 4 核 4GB）、"10Mbps 上行带宽（推荐 100Mbps）"。来源：[LunaTV-EE README 最低配置要求 / 常见卡顿原因](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)。

5. **README 把"CPU 不足导致视频转码缓慢"列为卡顿原因之一，但代码里并不存在服务端转码**——这是文档与实际实现不一致的地方（应视为文档口径问题）。来源：[LunaTV-EE README 常见卡顿原因 vs. 仓库无 ffmpeg 依赖](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)。

6. **CORS 是最大痛点，且有多种绕过层级**：① 采集源 JSON API 若不带 CORS 头，必须走 `/api/proxy/cms`（文件头注释第 3 条就写"解决第三方 API 的 CORS 限制"）；② 直播源用"智能 CORS 检测 → 允许则直连、否则代理"；③ EE 版提供外部 Cloudflare Worker（`/p/{sourceId}?url=` 路径形式）做第三方加速，失败降级本地。来源：[LunaTV-EE proxy/cms/route.ts L1-11](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/cms/route.ts)、[LunaTV-EE PROXY_CONFIG.md](https://github.com/stazeng/LunaTV-EE/blob/main/PROXY_CONFIG.md)、[LunaTV-EE README 直播直连模式](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)。

7. **Mixed Content（HTTPS 站点请求 HTTP 源）是被反复处理的坑**：`/api/proxy/cms` 存在的理由之一；m3u8 重写用**站内相对路径**而非绝对 URL，注释明确"不会在 HTTPS 站点下拼出 `http://` 造成混合内容被拦截"。来源：[LunaTV-EE proxy/cms/route.ts](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/cms/route.ts)、[LunaTV proxy/m3u8/route.ts L96-99](https://github.com/MoonTechLab/LunaTV/blob/main/src/app/api/proxy/m3u8/route.ts)。

8. **防盗链/UA 限制**：源站会校验 UA（默认用 `AptvPlayer/1.4.10` 而非浏览器 UA 才能过），部分场景还需 `Referer`/`Origin`（`/api/video-proxy` 动态伪造），并要求 `Accept-Encoding: identity` 以免压缩破坏流式；这些都是"不代理就播不了"的现实原因。来源：[LunaTV lib/live.ts L6](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/live.ts)、[LunaTV-EE video-proxy/route.ts L74-87](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/video-proxy/route.ts)。

9. **源失效是常态**：官方把"部署后为空壳项目，无内置播放源，需要自行收集"写在 README 最显眼位置；EE 版专门做了源浏览器/源测试模块、源健康检查 SSE 接口、自动备用 API、权重新排序来对抗源失效。来源：[MoonTV(Branch) README 注意](https://github.com/brucemomo/Moon_TV/blob/main/README.md)、[LunaTV-EE README 项目状态](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)、[LunaTV admin/source/validate/route.ts](https://github.com/MoonTechLab/LunaTV/blob/main/src/app/api/admin/source/validate/route.ts)。

10. **豆瓣反爬 + 限流**：需要 Puppeteer/Chromium 绕过（EE 版依赖 `puppeteer-core` + `@sparticuz/chromium`），并有请求限流（`MIN_DOUBAN_REQUEST_INTERVAL = 1000` ms）+ 随机延时 `randomDelay(300..1000)`；遇到 302/429 要特殊处理；海报 403 需走图片代理。来源：[LunaTV-EE package.json L32,L55](https://github.com/stazeng/LunaTV-EE/blob/main/package.json)、[LunaTV-EE danmu-external/route.ts L547-571](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/danmu-external/route.ts)、[LunaTV-EE README 豆瓣反爬虫验证](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)。

11. **地区限制/R18 过滤**：`NEXT_PUBLIC_DISABLE_YELLOW_FILTER` 控制色情过滤（默认 false，即开启过滤）；EE 版有"双层过滤系统 + 成人内容管理 + 用户/组级别控制"，`/api/proxy/cms` 里对 `source.is_adult` 的源**静默返回空列表**（`{code:200,msg:'success',list:[]}`）而不是报错。来源：[MoonTV(Branch) README 环境变量](https://github.com/brucemomo/Moon_TV/blob/main/README.md)、[LunaTV-EE proxy/cms/route.ts L127-169](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/cms/route.ts)、[LunaTV-EE README 成人内容管理](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)。

12. **播放列表兼容性坑（EE 版逐条修过）**：`EXT-X-MEDIA` 里出现 `nan` URI 会导致 HLS 音轨加载错误（已用"删掉 URI 属性"兜底）；`EXT-X-DEFINE` 变量不替换会 404；`EXT-X-DEFINE`/`EXT-X-PART`/`EXT-X-PRELOAD-HINT` 这些 LL-HLS 标签原版完全没处理。来源：[LunaTV-EE proxy/m3u8/route.ts L416-447](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/m3u8/route.ts)、[LunaTV-EE README "EXT-X-MEDIA URI处理（防止HLS音轨加载错误）"](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)。

13. **Redis 的 `KEYS` 会阻塞**（已修为 `SCAN`），且 Redis 默认配置"有一定的丢数据风险"（README 原话），推荐 Kvrocks（RocksDB 持久化、Redis 协议兼容）。来源：[LunaTV-EE CHANGELOG "Redis优化：KEYS替换为SCAN避免阻塞"](https://github.com/stazeng/LunaTV-EE/blob/main/CHANGELOG)、[MoonTV(Branch) README "Redis 存储（有一定的丢数据风险）"](https://github.com/brucemomo/Moon_TV/blob/main/README.md)。

14. **localstorage 模式的限制**：管理员配置特性"仅支持通过非 localstorage 存储的部署方式使用"；localstorage 模式下数据不跨设备同步；且 TVBox 开关"仅用于展示（被禁用）"，必须用环境变量控制。来源：[MoonTV(Branch) README 管理员配置 / TVBox 本地存储模式](https://github.com/brucemomo/Moon_TV/blob/main/README.md)。

15. **`Content-Length` 与流式传输冲突**是踩过的坑：EE 版 CHANGELOG 明确"修复流式代理并发请求：防止并发请求，移除 Content-Length 头"，分片代理里有对应注释。来源：[LunaTV-EE CHANGELOG v6.1.1](https://github.com/stazeng/LunaTV-EE/blob/main/CHANGELOG)、[LunaTV-EE proxy/segment/route.ts L112](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/segment/route.ts)。

16. **下载侧的真实限制**：Firefox/Safari 不支持 File System Access API，只能退到 Service Worker（需 HTTPS）或内存模式；内存模式 **>2GB 有崩溃风险**；HTTP 非安全环境无法用 Service Worker。来源：[LunaTV-EE DOWNLOAD_FEATURES.md 浏览器支持表](https://github.com/stazeng/LunaTV-EE/blob/main/DOWNLOAD_FEATURES.md)。

17. **法务/传播与安全提醒**：项目要求必须设 `PASSWORD`、"强烈建议关闭公网注册"（`NEXT_PUBLIC_ENABLE_REGISTER` 默认 false）；EE 版明确"请勿在中国大陆社交平台宣传"；上游 LunaTV 用 **CC BY-NC-SA 4.0**（禁止商业化），而 MoonTV(Branch) 声明 MIT——**两者许可证不同，复用代码前必须确认**。来源：[MoonTV(Branch) README 安全与隐私提醒](https://github.com/brucemomo/Moon_TV/blob/main/README.md)、[LunaTV-EE README 开源协议](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)、[LunaTV README License](https://github.com/MoonTechLab/LunaTV/blob/main/README.md)。

---

## 附：可被桌面应用（Python/PySide6）复用的机制清单

> 归类维度：**卡顿** = 播放中断/重缓冲；**帧率** = UI 渲染/主线程阻塞；**下载慢** = 分片拉取与落盘效率。
> 每条注明来源与适配代价。**不含 Python 代码。**

### A. 解决"卡顿 / 重缓冲"

| # | 机制 | 具体做法（可抄的参数） | 来源 |
|---|---|---|---|
| A1 | **分级缓冲模式，且持久化用户选择** | 三档（代码键 `standard`/`enhanced`/`max`，UI 文案"默认/增强/强力"）：`30s/30s/60MB`、`45s/45s/90MB`、`90s/60s/180MB`；存 localStorage `playerBufferMode`，播放器构造前读取；**注意仅桌面生效** | [play/page.tsx L224-255](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/play/page.tsx) |
| A2 | **桌面/移动分档的 hls.js 参数矩阵** | 桌面 `maxBufferLength=用户配置`、`maxMaxBufferLength=600`、`maxBufferSize=用户配置`；移动 `8~15s / 60~120s / 20~40MB`；`maxBufferHole` 桌面 0.1、iOS13+ 0.05 | [play/page.tsx L3725-3746](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/play/page.tsx) |
| A3 | **首分片预取 + 带宽探测的取舍** | 桌面 `startFragPrefetch: true` / `testBandwidth: true`；移动端两者都关以**换更快起播**；iOS13+ 尤其关掉 `testBandwidth` | [play/page.tsx L3755-3756](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/play/page.tsx) |
| A4 | **细粒度 fragLoadPolicy 超时/重试** | `maxTimeToFirstByteMs` 10000/6000；`maxLoadTimeMs` 120000/60000；`timeoutRetry.maxNumRetry` 4/2；`errorRetry.maxNumRetry` 6/3 且 `retryDelayMs: 1000`、`maxRetryDelayMs` 8000/4000 | [play/page.tsx L3759-3774](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/play/page.tsx) |
| A5 | **ABR 参数按端调优** | `abrBandWidthFactor` 0.95/0.8；`abrEwmaFastLive` 3/2；`abrEwmaSlowLive` 9/6；`abrEwmaDefaultEstimate` 按设备性能 1.5/3/6 Mbps；`maxStarvationDelay` 4/2 | [play/page.tsx L3749-3756](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/play/page.tsx) |
| A6 | **播放前"实测选源"（Netflix 式红进度条）** | 桌面 `concurrency=3` 批次并发、最多测 **20 个源**（权重前 5 + 随机 15）；HEAD 测 ping + 加载**首个分片**算 `speedKBps = size/1024/(loadTime/1000)` + 读 `videoWidth` 判画质；**保守早停**：4K 且 ≥8MB/s、2K 且 ≥6MB/s；打分 = 分辨率 40% + 速度 40% + 延迟 20%，再乘权重加成 `1+(weight-50)*0.005`；iOS13+/iPad 直接跳过测速取最高权重源 | [lib/utils.ts L166-424](https://github.com/stazeng/LunaTV-EE/blob/main/src/lib/utils.ts)、[play/page.tsx L1358-1623](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/play/page.tsx) |
| A7 | **直连优先、代理兜底（CORS 探测）** | 播放前探测源站是否允许跨域：允许 → 客户端直连（零服务器带宽）；不允许 → 走本机代理。管理端统计直连/代理占比 | [live/precheck/route.ts](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/live/precheck/route.ts)、[README 直播直连模式](https://github.com/stazeng/LunaTV-EE/blob/main/README.md) |
| A8 | **播放列表重写（脱离 CORS/防盗链依赖）** | 逐行重写：裸 URL→`/api/proxy/segment?url=`；`EXT-X-MAP`/`EXT-X-KEY`/`EXT-X-MEDIA`/`EXT-X-PART`/`EXT-X-PRELOAD-HINT` 的 `URI=` 分别改写；`EXT-X-STREAM-INF` 下一行→`/api/proxy/m3u8?url=`；`{$var}` 变量替换；脏 URI（`nan`）删属性而非生成坏链 | [proxy/m3u8/route.ts L234-605](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/m3u8/route.ts) |
| A9 | **流式代理 + 64KB 背压** | `ReadableStream` + `reader.read()` 逐块 `enqueue`，`highWaterMark: 65536`，**不转发 `Content-Length`**（chunked 冲突），透传 `Content-Range`/`ETag`/`Last-Modified` | [proxy/segment/route.ts L110-227](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/segment/route.ts) |
| A10 | **HTTP 连接池 keep-alive** | `maxSockets: 100`（分片）/`50`（列表）、`maxFreeSockets: 20/10`、`keepAliveMsecs: 30000`、`timeout: 30000/60000` | [proxy/segment/route.ts L13-27](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/segment/route.ts) |
| A11 | **点播分片代理的路径设计（MoonTVPlus 专有）** | 独立路径 `/api/proxy/vod/m3u8` + `/api/proxy/vod/segment`（与直播代理分离）；参数 `source=`（**必填**，缺则 400）；**逐源开关**——`SourceConfig.proxyMode?: boolean` 为假则 403，可按源灰度；伪造 `Referer: <目标URL自身>` + Chrome UA；`validateProxyUrlServerSide()` 做 SSRF 拦截（403） | [MoonTVPlus proxy/vod/m3u8/route.ts](https://github.com/mtvpls/MoonTVPlus/blob/main/src/app/api/proxy/vod/m3u8/route.ts)、[proxy/vod/segment/route.ts](https://github.com/mtvpls/MoonTVPlus/blob/main/src/app/api/proxy/vod/segment/route.ts)、[lib/server/ssrf.ts](https://github.com/mtvpls/MoonTVPlus/blob/main/src/lib/server/ssrf.ts)、[lib/config.ts L30](https://github.com/mtvpls/MoonTVPlus/blob/main/src/lib/config.ts) |
| A12 | **分片缓存 5 分钟 / 列表不缓存** | 分片 `Cache-Control: public, max-age=300`；m3u8 `no-cache, no-store, must-revalidate` + `Pragma`/`Expires` | [proxy/segment/route.ts L110](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/segment/route.ts)、[proxy/m3u8/route.ts L148-150](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/m3u8/route.ts) |
| A13 | **UA/Referer/Accept-Encoding 伪装** | `User-Agent: AptvPlayer/1.4.10`（或源配置的 ua）；`Referer`/`Origin` 由目标 URL 的 `protocol//host` 动态推导；`Accept-Encoding: identity` 防压缩破坏流 | [lib/live.ts L6](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/live.ts)、[video-proxy/route.ts L74-87](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/video-proxy/route.ts) |
| A14 | **同源错误恢复链（不含自动换源）** | hls.js `ERROR` 事件按 `details`/`type` 分级恢复：`FRAG_PARSING_ERROR`→`startLoad()`；`BUFFER_APPEND_ERROR`+timestamp→`BUFFER_RESET` 后 `startLoad(currentTime)`（**保留进度**）；fatal `NETWORK_ERROR`→`startLoad()`；fatal `MEDIA_ERROR`→`recoverMediaError()`。**注意：它不做自动换源**，换源靠用户手动，但换源会写 `sessionStorage['temp_progress_*']` 保留进度 | [play/page.tsx L3788-3830, L4966-4972](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/play/page.tsx) |
| A15 | **超时/重试/降级矩阵（可直接抄的数值）** | 源搜索 8s、源详情 10s、单源总超时 20s、源校验 10s、m3u8 代理 15s、分片代理 30s、CMS 代理 20s、video-proxy 30s、precheck 8s、Worker 健康检查 5s；单源失败返空不阻塞；403 写负缓存；源站 403/404 主动删本地缓存；主 API 失败切备用 API；Worker 失败切本地代理 | [lib/downstream.ts](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/downstream.ts)、[search/route.ts](https://github.com/MoonTechLab/LunaTV/blob/main/src/app/api/search/route.ts)、[video-proxy/route.ts L131-151](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/video-proxy/route.ts) |
| A16 | **本地文件缓存命中即走文件流 + Range** | 缓存命中直接 `createReadStream(path,{start,end})` 返回 206，`Accept-Ranges: bytes`，元数据 TTL 15min / 视频体 TTL 12h / 上限 2GB / LRU(Sorted Set) 淘汰 / **缓存 key 优先用内容 ID 而非 URL** | [video-cache.ts L34-88](https://github.com/stazeng/LunaTV-EE/blob/main/src/lib/video-cache.ts)、[video-proxy/route.ts L336-374](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/video-proxy/route.ts) |
| A17 | **流类型嗅探（决定用哪种播放路径）** | `Range: bytes=0-2047` 取前 2KB：FLV 头 `46 4C 56`；MP4 第 4-7 字节 `ftyp`；m3u8 文本头 `#EXTM3U`；同时用请求耗时给出 `latencyMs` | [live/precheck/route.ts L49-202](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/live/precheck/route.ts) |

### B. 解决"帧率 / UI 卡顿"

| # | 机制 | 具体做法 | 来源 |
|---|---|---|---|
| B1 | **DOM/列表虚拟化** | `react-window` 虚拟滚动 + infinite-loader；首页所有横向行都虚拟化（作者原话"修复移动端卡顿问题"）；2-8 列自适应网格用 `ResizeObserver` 测容器宽 | [README](https://github.com/stazeng/LunaTV-EE/blob/main/README.md)、[CHANGELOG "首页所有ScrollableRow启用虚拟化"](https://github.com/stazeng/LunaTV-EE/blob/main/CHANGELOG) |
| B2 | **渐进式列表加载** | 搜索结果初始只渲染 **12 项**，滚动时增量加载 | [CHANGELOG v4.3.6](https://github.com/stazeng/LunaTV-EE/blob/main/CHANGELOG) |
| B3 | **主线程批量让出** | 弹幕解析每 **200** 条 `await setTimeout(0)`；去重每 100 条让出；剩余弹幕 `requestIdleCallback` 分批；详情请求 `requestIdleCallback` 延迟；CSS 动画用 `requestAnimationFrame` | [danmu-external/route.ts L276-278](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/danmu-external/route.ts)、[play/page.tsx L2944](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/play/page.tsx)、[CHANGELOG](https://github.com/stazeng/LunaTV-EE/blob/main/CHANGELOG) |
| B4 | **弹幕渲染的性能分级（有确切公式）** | `score = min(cores/4,1)*0.5 + min(memGB/1,1)*0.3 + (isMobile?0.2:0.5)*0.2`；`>0.7` high / `>0.4` medium / else low；三档**同屏上限 60/40/25**、**丢弃率 0.1/0.3/0.5**，`useWorker` 三档全开；low 档 `maxLength:30` + CSS `translateZ(0)`；防重叠算法在低端设备自动降级 | [play/page.tsx L3975-4141](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/play/page.tsx)、[CHANGELOG v4.x](https://github.com/stazeng/LunaTV-EE/blob/main/CHANGELOG) |
| B5 | **弹幕数据分级限流（服务端）** | 5 分钟分段 / 每段 ≤500 条 / 全局 ≤20000 条 / 超限均匀+随机混合采样 / 少于 10 条判无效；去重键 = 时间(0.01s)+小写文本+颜色 | [danmu-external/route.ts L204-321, L1294-1321](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/danmu-external/route.ts) |
| B6 | **包体积/首屏优化** | `optimizePackageImports` 对 lucide-react / heroicons / framer-motion / react-icons 做按需打包；图片预加载 + 骨架屏（`DoubanCardSkeleton`、`SkeletonCard`） | [next.config.js L24-33](https://github.com/stazeng/LunaTV-EE/blob/main/next.config.js)、[CHANGELOG "首屏图片加载优化"](https://github.com/stazeng/LunaTV-EE/blob/main/CHANGELOG) |
| B7 | **导航/滚动不阻塞** | `<FastLink>` 用 transition 做页面切换动画 + `prefetch={false}` 避免无谓预取；分类选择器滚动用 `requestAnimationFrame`；豆瓣页面加防抖 + `flushSync` | [README 移动导航全面重设计 / Material UI Tabs](https://github.com/stazeng/LunaTV-EE/blob/main/README.md) |

### C. 解决"下载慢"

| # | 机制 | 具体做法 | 来源 |
|---|---|---|---|
| C1 | **分片并发下载（队列 + N worker）** | 默认 **6 并发**，UI 可调 **1-16**；共享 `downloadQueue` + `Math.min(concurrency, total)` 个 worker 循环 `shift()` 取任务 | [m3u8-downloader.ts L368-376, L704-729](https://github.com/stazeng/LunaTV-EE/blob/main/src/lib/download/m3u8-downloader.ts) |
| C2 | **失败重试 + 延迟退避** | 默认重试 **3** 次（可调 0-10），每次间隔固定 **1000 ms**；失败分片标记 `'failed'` 跳过而不是整任务失败 | [m3u8-downloader.ts L549-551, L644-698](https://github.com/stazeng/LunaTV-EE/blob/main/src/lib/download/m3u8-downloader.ts) |
| C3 | **边下边存（不占内存）** | 三种落盘模式按能力自动选：**File System Access API**（Chrome/Edge，无大小限制）> **Service Worker 流**（需 HTTPS/localhost）> **内存 Blob**（<500MB 建议）。2GB 视频内存占用从 2GB → 50MB（文档数据） | [stream-mode-detector.ts](https://github.com/stazeng/LunaTV-EE/blob/main/src/lib/download/stream-mode-detector.ts)、[DOWNLOAD_FEATURES.md](https://github.com/stazeng/LunaTV-EE/blob/main/DOWNLOAD_FEATURES.md) |
| C4 | **保序写入（并发下载 + 串行落盘）** | `pendingWrites: Map<index, data>` + `nextWriteIndex` 游标 + `writeLock` Promise 串行化；只写连续可用的分片，遇 `'failed'` 递增游标跳过——这是"并发下载不丢数据"的关键 | [m3u8-downloader.ts L386-509](https://github.com/stazeng/LunaTV-EE/blob/main/src/lib/download/m3u8-downloader.ts) |
| C5 | **可暂停/可恢复的控制器** | `PauseResumeController`（`pause/resume/waitIfPaused`）在下载前、下载后、解密后、状态更新前 **4 个检查点** await，不销毁 worker | [m3u8-downloader.ts L17-58](https://github.com/stazeng/LunaTV-EE/blob/main/src/lib/download/m3u8-downloader.ts) |
| C6 | **主播放列表自动选最高码率** | 检测 `#EXT-X-STREAM-INF` 递归解析子播放列表（限深 5 层防循环），按 `BANDWIDTH=` 降序取最大 | [m3u8-downloader.ts L109-181](https://github.com/stazeng/LunaTV-EE/blob/main/src/lib/download/m3u8-downloader.ts) |
| C7 | **AES-128 分片解密不复用中间文件** | 下载后立刻 `AES-CBC + Pkcs7` 解密再入写队列；IV 从 `IV=0x...` 十六进制解析；key 单独 fetch 一次 | [m3u8-downloader.ts L236-312, L585-587](https://github.com/stazeng/LunaTV-EE/blob/main/src/lib/download/m3u8-downloader.ts) |
| C8 | **TS→MP4 流式转码（客户端）** | `mux.js` 的 `StreamingTransmuxer.pushAndTransmux()`：边写边转，不把整片读进内存；普通模式退化为 `transmuxTSToMP4(segments, duration)` | [mp4-transmuxer.ts](https://github.com/stazeng/LunaTV-EE/blob/main/src/lib/download/mp4-transmuxer.ts)、[DOWNLOAD_FEATURES.md](https://github.com/stazeng/LunaTV-EE/blob/main/DOWNLOAD_FEATURES.md) |
| C9 | **下载/播放共用同一套"源 URL"** | 下载入口直接复用 `detail.episodes[episodeIndex]`（同一个 m3u8），并对不含 `.m3u8` 的 URL 先过滤 | [play/page.tsx L5666-5677](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/play/page.tsx) |
| C10 | **搜索/接口侧缓存降低整体等待** | 搜索内存 LRU（10min/1000 条/5min 清理/负缓存）+ HTTP `s-maxage=cache_time`（默认 7200s）+ 统一缓存默认 TTL 3600s | [search-cache.ts](https://github.com/MoonTechLab/LunaTV/blob/main/src/lib/search-cache.ts)、[search/route.ts](https://github.com/MoonTechLab/LunaTV/blob/main/src/app/api/search/route.ts)、[unified-cache.ts L78-84](https://github.com/stazeng/LunaTV-EE/blob/main/src/lib/unified-cache.ts) |

### D. 需要特别警惕的"反模式"（不要照抄）

| # | 反模式 | 为什么 | 来源 |
|---|---|---|---|
| D1 | 在 Edge runtime 里把整个 TS 分片读成 `ArrayBuffer` 再返回 | 分片通常几 MB，高并发下内存暴涨；应改为流式 + 背压 | [Moon_TV api/m3u8/route.ts](https://github.com/brucemomo/Moon_TV/blob/main/src/app/api/m3u8/route.ts) |
| D2 | 给分片响应设 `max-age=31536000`（1 年） | 分片 URL 常带时效签名，长缓存会缓存住失效内容；本项目 EE 版用 300 秒 | [Moon_TV api/m3u8/route.ts](https://github.com/brucemomo/Moon_TV/blob/main/src/app/api/m3u8/route.ts) vs [EE proxy/segment/route.ts](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/segment/route.ts) |
| D3 | 在流式响应里同时设 `Content-Length` | 与 chunked encoding 冲突，是 EE 版明确修过的 bug | [EE CHANGELOG](https://github.com/stazeng/LunaTV-EE/blob/main/CHANGELOG) |
| D4 | 用 Redis `KEYS` 扫键 | O(N) 阻塞整个实例，应用 `SCAN` | [EE CHANGELOG](https://github.com/stazeng/LunaTV-EE/blob/main/CHANGELOG) |
| D5 | 靠 `Host`/`Referer` 头拼代理绝对地址 | Host 可伪造，且在 HTTPS 页面下会拼出 `http://` 触发 Mixed Content；上游原版改用站内相对路径，EE 版又改回绝对路径（是退化点） | [LunaTV proxy/m3u8/route.ts L96-99](https://github.com/MoonTechLab/LunaTV/blob/main/src/app/api/proxy/m3u8/route.ts) vs [EE L234-249](https://github.com/stazeng/LunaTV-EE/blob/main/src/app/api/proxy/m3u8/route.ts) |
| D6 | 文档宣称的"服务端转码"并不存在 | EE README 把"CPU 不足导致视频转码缓慢"列为卡顿原因，但全仓库无 ffmpeg 依赖——不要据此设计架构 | [EE README](https://github.com/stazeng/LunaTV-EE/blob/main/README.md) |

---

## 明确"未查到" / "有反证"的条目

### A. 确实未查到（不要编造）
- **服务端转码 / remux（ffmpeg、WASM）**：三个仓库均无。只有客户端 `mux.js` TS→MP4。
- **TS 分片在服务端的本地磁盘持久缓存**：未查到。代理分片只在 HTTP 层给了 `max-age=300`，没有落地到磁盘；唯一的磁盘缓存是 `/api/video-proxy` 对豆瓣 mp4 的 12 小时文件缓存。
- **预取/预加载下一集的 m3u8 或分片**：未查到。只有"选源测速"会真实加载首个分片。
- **`<link rel="preconnect">` / `dns-prefetch` 类的连接预热**：全仓库 **0 命中**。
- **键盘快捷键映射表（方向键/空格/音量等自定义绑定）**：未查到。
- **直播频道挂弹幕**：未查到。
- **弹幕客户端参数 `name=` / `type=` / `danmaku=`**：未查到。客户端实际只调 `GET /api/danmu-external?douban_id=&title=&year=&episode=`。
- **用户可配的弹幕"关键词/正则/类型/颜色屏蔽规则"**：全仓库"屏蔽"**0 命中**；只有服务端硬编码过滤器（见 §6 第 6 条）。
- **MoonTV 原始仓库 `samqin123/MoonTV` 的 `next.config.js` 与 CI 细节**：该仓库文件树里没有 `src/app/api/proxy/m3u8` 与 `src/app/api/proxy/segment`（即**无视频代理**），仅有 `src/app/api/image-proxy/route.ts` 与 `src/app/play/page.tsx`。
- **`mtvpls/MoonTVPlus` 的实现细节**：已核实其 **VOD 代理链路**（`src/app/api/proxy/vod/{m3u8,segment}/route.ts`、`src/lib/server/ssrf.ts`、`src/lib/config.ts` 的 `proxyMode`）与 `README`/`package.json`/`Dockerfile`/`next.config.js`/`vercel.json`/`wrangler.toml`（见 §3.1C）。**未逐文件核实**其前端播放页、下载模块与搜索排序逻辑。

### B. 宣传口径 vs. 代码事实（重要反证，引用时务必注意）

| 宣传口径（README / CHANGELOG） | 代码事实 |
|---|---|
| "失败自动换源"；`SourceSwitchDialog` 是故障切源弹窗 | **无自动换源**。`artPlayer.on('error')` 几乎是空实现（仅 `console.error`，`currentTime>0` 时直接 `return`）；hls `ERROR` 只在**同一源内**恢复（`startLoad()` / `recoverMediaError()`）。`SourceSwitchDialog` 是**多人观影房**"房主与成员源不一致"的确认框。换源需用户手动。 |
| "三种缓冲模式（**省流、均衡、高质**）" | 代码实际取值是 `standard` / `enhanced` / `max`，UI 文案为 **"默认模式 / 增强模式 / 强力模式"**；"省流/均衡/高质"三词在源码中不存在。且**仅桌面生效**，移动端忽略该设置、走硬编码值。 |
| "Netflix 风格智能速度测试 / 智能提前停止 / **自动推荐最优缓冲模式**" | 测速**存在**，但它是**源优选**测速：**不测本机带宽、不推荐缓冲模式**（代码中没有任何路径把测速结果写回 `playerBufferMode`）。"提前停止"的真实含义是"拿到元数据 + 1 个分片就 `cleanup()`"；另有一处保守早停阈值（4K 且 ≥8MB/s、2K 且 ≥6MB/s）。 |
| 播放链路有预加载 / 预取下一集 | **零** preload / prefetch / preconnect。`play/page.tsx:2944` 注释写 `requestIdleCallback`，实际实现是 `setTimeout((i+1)*100)`；`FastLink.tsx:105` 还主动关掉了 Next.js prefetch。 |
| README 把"CPU 不足导致**视频转码**缓慢"列为卡顿原因 | 仓库**无 ffmpeg 依赖**，不存在服务端转码。属文档口径问题，**不要据此设计架构**。 |
| "Anime4K 超分辨率"作为一项能力 | 确实存在（`anime4k-webgpu ^1.0.0`，动态 import），但 **opt-in 且不持久化**：`anime4kEnabled` 初始 `false`，`localStorage['enable_anime4k']` **只写不读**，刷新后不自动恢复。需 WebGPU（`navigator.gpu` + `requestAdapter()` 成功），不支持则菜单项整体不渲染。 |
