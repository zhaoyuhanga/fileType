# 影视板块（第六）体验改造 + 墨软动漫（第七）建设方案

> **状态**：评审稿 —— 本文只做设计，**不含任何代码改动**；确认后按第 5 节的里程碑分阶段实施。
> **基线**：v1.0.5（已发版，含第六大板块墨软文档）。
> **参考项目**：[MoonTV](https://github.com/samqin123/MoonTV.git)、[LunaTV](https://github.com/MoonTechLab/LunaTV.git)（影视聚合播放站）、[Kazumi](https://github.com/Predidit/Kazumi.git)（番剧采集客户端）。
> **技术栈约束（不引入新体系）**：PySide6 / Qt 6.11 单栈（**不引入 QtWebEngine**）、`requests`、随包 `ffmpeg`（`tools/ffmpeg`）、单库 `modu.db` + 版本化迁移、`boards/<key>/` + `core/<key>/` 分包、`ui_kit` 设计令牌、pytest、PyInstaller onedir + NSIS。
> 新增依赖：**零**（XPath 用已装的 `lxml`，CSS 选择器用已装的 `beautifulsoup4`）。
> **事实来源与取证方式**：对标结论来自**实读源码 / 官方文档 / issue 核实**，支撑材料在 `docs/research/`（4 份报告，含"宣传口径 vs. 代码事实"反证表）。
> 引用到的对标数据都标了出处编号（M*=MoonTV、L*=LunaTV 家族、K*=Kazumi）；**凡 README 宣传语与源码不一致的，一律以源码为准**——别照抄不存在的功能。

---

## 0. TL;DR（先看这 12 条）

1. **播放卡、帧率低的主因不是"源不好"，而是我们自己的链路**：远程地址一律经 Python 写的本地 HTTP 代理（`services/media_server.py`），而该代理**每个分片都新开一次 TLS（无 `requests.Session`）、把整个响应体读进内存后才转发、所有响应 `Cache-Control: no-store`、失败还带 3 次 0.6s 退避**。1080p/2s 分片意味着每分钟约 30 次握手 + 30 次 Python 全量缓冲，抖动直接体现为卡顿。
2. **第二个主因是解码**：`QMediaPlayer` 用默认后端，代码里**没有任何**硬解/后端选择；PySide6 6.11 同包了 `ffmpegmediaplugin.dll` 与 `windowsmediaplugin.dll`，而 Qt 官方文档明确给了 `QT_FFMPEG_DECODING_HW_DEVICE_TYPES=d3d11va,d3d12va,dxva2`、`QT_DISABLE_HW_TEXTURES_CONVERSION`、`QT_FFMPEG_HW_ALLOW_PROFILE_MISMATCH` 等开关——我们现在一个都没用，1080p H.264 基本靠软解，CPU 打满就是"帧率低"。
3. **第三个主因是选路策略**：`library._lock_best_variant()` 永远锁定**最高码率**变体（`max(rank)`），没有带宽探测、没有卡顿降档。慢网下"锁最高"＝持续 stall。
4. **首帧慢/卡死无自愈**：`player._check_native_started()` 等 20s 只打印一句提示，不会自动降清晰度、换源或"先下后播"。
5. **下载慢的主因**：`download_many()` 逐条串行；HLS 分片下载（无 ffmpeg 时）**严格串行 + 每片 `requests.get` 新建连接**；走 ffmpeg 时 ffmpeg 自己也串行取分片，而且**分片仍然要过我们的 Python 代理**（`_localize_playlist()` 把分片改写成本机代理地址）。三重串行 + 一重 Python 中转。
6. **代理必须"流式 + 连接复用 + 磁盘缓存"**：改造后它从"瓶颈"变成"加速器"（本地缓存命中即零外网、Range 精确、keep-alive、并发预取、缓存键用内容身份而非 URL）。
7. **下载改造 = 并发分片 + 保序写入 + 断点续传 + 直连优先**：自研分片下载器（`ThreadPoolExecutor` + 每线程 `Session` + 单写线程保序落盘 + `.parts` 续传 + 坏片重试/跳过），ffmpeg 路径改为"能把请求头直接交给 ffmpeg 时不走代理"（本地 playlist 与 `-headers` 双路探测）。
8. **渲染路径要换**：`QVideoWidget` 是原生窗口，**无法在上面可靠叠加 Qt 控件**（弹幕/字幕/统计就做不了）。改用 `QGraphicsView + QGraphicsVideoItem`：视频是场景里的一个 item，弹幕/字幕/缓冲指示/调试信息都是叠加 item —— 这是第七大板块弹幕的前置条件。
9. **对标 MoonTV/LunaTV 的可抄清单**：缓冲深度档位参数（三档数值）、源测速打分公式（分辨率 40% + 速度 40% + 延迟 20%）、下载器"并发下载 + 单线程保序落盘 + 暂停检查点"、代理的 keep-alive 连接池与背压、两级本地缓存（**缓存键用稳定 ID 而非 URL**）、SSE 流式搜索、搜索负缓存与 LRU、图片代理；**点播代理的路径设计**（逐源开关 + 必填 source + SSRF 校验 + Referer 伪装）以 `mtvpls/MoonTVPlus` 的 `/api/proxy/vod/*` 为最贴近的参考。**不抄**：服务端转码（他们根本没有 ffmpeg）、Docker/Vercel/Redis 部署形态、多用户与鉴权。
   ⚠️ **两个必须纠正的认知**：① **点播代理并非所有对标项目都有**——`LunaTV` / `LunaTV-EE` 的点播是直连源站（只有直播走代理），`MoonTVPlus` 才有 VOD 代理；② **它们都没有"失败自动换源"**（`on('error')` 近乎空实现）——这两件事我们都要**自己做**（我们已有 registry 健康度与跨源定位，属于领先项）。
10. **第七大板块「墨软动漫」定位**：与影视板块**不重叠** —— 影视是"聚合 CMS 源 + 下载入库"，动漫是"**规则驱动的番剧采集 + 追番 + 弹幕**"（对标 Kazumi 的 XPath/API 规则、弹弹play 弹幕、追番更新提醒）。
11. **动漫板块的规则引擎**：JSON 规则文件（字段与 Kazumi **一一对应**，便于直接吃 `KazumiRules` 仓库的规则），选择器**同时支持 XPath 与 CSS**（`lxml` + `soupsieve`），并带**API level 版本门控、`deprecated` 灰显、目录 `index.json` 批量安装、订阅更新、试跑调试器**；内置规则随包，用户规则存数据目录。
12. **动漫板块不做的**：实时超分辨率（Qt 单栈下需要 libmpv/Anime4K 着色器，与"不引入新播放内核"冲突）、内置浏览器内核（因此**纯 JS 渲染站点无法支持**，Kazumi 维护者也承认这类站点"基本无法解决"）、DLNA/一起看/发送弹幕；对应地，超分改为**离线 ffmpeg 增强**并如实标注边界。

---

## 1. 现状体检：第六大板块「墨软影视」

### 1.1 模块与数据流（现状）

```
boards/video/                        core/video/
├── board.py      666 行  板块外壳、播放会话、下载调度        ├── models.py      425  数据模型
├── search.py     432     聚合搜索                             ├── sources/registry.py 410  源注册表/健康度/换源
├── detail.py     337     剧集与清晰度选择                     ├── sources/providers/cms_vod.py 462  苹果CMS 源
├── library_page.py 451   我的视频                             ├── sources/providers/public.py  337  公开源
├── history.py    253     播放历史                             ├── sources/matcher.py 92  同名匹配
├── player.py     585     QtMultimedia 播放器                  ├── library.py    417  业务编排
├── sources.py    345     源设置对话框                          ├── downloader.py 769  HLS/直链下载
└── widgets.py    553     公共控件                              ├── hls.py        259  m3u8 解析
                                                               └── storage.py    532  video_* 五张表
services/media_server.py 457 行  本机 HTTP 流服务（本地文件 Range / 远程直链代理 / HLS 清单代理）
```

播放链路（现状，逐步说明瓶颈在哪）：

```
用户点播放
  └─ board._play_online → _resolve_and_play（QThread 里解析）
       └─ registry.resolve()：换源 → 拿到 m3u8/mp4 直链 + Referer
            └─ library._lock_best_variant()：探测主清单，**锁最高码率**
                 └─ VideoPlayerDialog._load()
                      └─ media_server.hls_url()/stream_url()   ← 包一层本机代理
                           └─ QMediaPlayer.setSource(本机代理地址)
                                └─ Qt 的 FFmpeg 插件 → 拉本机 m3u8 → 逐分片请求本机
                                     └─ [瓶颈] 我们的 Python 代理 → 新建 TLS → 全量缓冲 → 转发
```

下载链路（现状）：

```
download_episode
  ├─ 有 ffmpeg：_localize_playlist() 把 m3u8 分片改写成**本机代理**地址
  │    └─ ffmpeg -c copy 拉本地 m3u8 → 分片仍逐个经过 Python 代理（串行）
  └─ 无 ffmpeg：_download_segments() → for 分片: _fetch_bytes()（**严格串行**，每片新建连接）
download_many()：for 任务（**逐条串行**，一季 12 集就是 12 次串行）
```

### 1.2 三个缺陷的根因（按影响排序，全部带代码证据）

#### A. 播放卡顿 / 帧率低

| # | 根因 | 证据（文件:行） | 影响 |
|---|---|---|---|
| A1 | 代理**不复用连接**：每个分片/new Range 都是新的 TCP+TLS | `services/media_server.py:79`（`requests.get`），无 `Session` | 境外源 RTT 200–400ms 时，每片多付 1 个 RTT 的握手；并发分片时更明显 |
| A2 | 代理**全量缓冲**才转发：`body = upstream.content` | `media_server.py:322`、`330`（清单重写也走全量） | 分片延迟 = 完整下载时间；直链 MP4 更糟（整文件进内存才回第一字节，Range 语义也失真） |
| A3 | 代理**禁用缓存**：`Cache-Control: no-store` | `media_server.py:345`、`383`、`276` | 拖动进度条、清晰度来回切、Qt 重试都要重新走外网；seek 卡顿的常见来源 |
| A4 | 失败重试策略不适合实时流：3 次 × `sleep(0.6*attempt)` | `media_server.py:69-90`（`_upstream_get`） | 一个坏分片最坏阻塞 1.8s+；播放器已经 stall |
| A5 | 读超时 60s | `media_server.py:49` | 卡住的连接占着播放线程，表现为"永久缓冲" |
| A6 | **无任何解码后端/硬解配置** | 全仓 grep `QT_FFMPEG_DECODING_HW_DEVICE_TYPES`/`QT_MEDIA_BACKEND` = 0 命中 | 1080p H.264 软解 → CPU 100%、掉帧 |
| A7 | 播放器只有 `QVideoWidget`，没有 `QVideoSink` 统计/自定义渲染 | `boards/video/player.py:266-285` | 无法叠加弹幕/字幕/统计，也无法验证"掉帧" |
| A8 | **锁最高码率 + 无 ABR** | `core/video/library.py:290-332`（`max(rank)`） | 慢网必卡；也没有"卡了就降一档"的回路 |
| A9 | 起播失败只提示不自愈 | `player.py:344-367`（20s 后仅改文字） | 用户看到的是"卡住"，实际需要降档/换源/先下后播 |
| A10 | HLS 清单每次都重写并全量读取（数百 KB 也进内存） | `media_server.py:357-394` | 起播慢一小截，长清单更明显 |

Qt 官方可用的开关（本轮改造直接采用，出处见附录 C）：

- `QT_MEDIA_BACKEND=ffmpeg`：显式固定 FFmpeg 后端（我们同包了 `ffmpegmediaplugin.dll` 与 `windowsmediaplugin.dll`，实测都在 `.venv/Lib/site-packages/PySide6/plugins/multimedia/`）。
- `QT_FFMPEG_DECODING_HW_DEVICE_TYPES=d3d11va,d3d12va,dxva2`：硬解优先级（可空列表 `,` 关闭）。
- `QT_DISABLE_HW_TEXTURES_CONVERSION=1`：GPU 纹理转换出问题时的一键回退。
- `QT_FFMPEG_HW_ALLOW_PROFILE_MISMATCH=1`：profile 不匹配时的兜底（有风险，仅作为"用户手动开关"）。
- `QT_FFMPEG_DEBUG=1` + `QT_LOGGING_RULES="*.ffmpeg.*=true"`：打印实际使用的解码器与硬解能力（做"播放诊断"面板的信息源）。

#### B. 下载慢

| # | 根因 | 证据 | 影响 |
|---|---|---|---|
| B1 | 批量下载串行 | `downloader.py:736-779`（`for index, (video, episode, quality) in enumerate(items)`） | 一季 12 集 = 串行 12 次 |
| B2 | 分片下载串行、每片新建连接 | `downloader.py:675-684`（`for … _fetch_bytes`）、`717-723`（`requests.get`） | 单连接 + 无 keep-alive，吞吐受 RTT 与丢包限制 |
| B3 | 分片下载重试弱 | `downloader.py:719`（`range(1, 3)`，只 2 次） | 抖动时整集失败重来 |
| B4 | 无断点续传 | `downloader.py:671-688`（`.part` 只用于"写坏就删"） | 失败即从 0 开始 |
| B5 | ffmpeg 路径无并发/无网络调优参数 | `downloader.py:421-437`（只有 `-c copy`、`-bsf:a`、`-movflags`） | ffmpeg HLS 解复用器逐片串行；`-rw_timeout`/`-reconnect*`/`-http_persistent` 全未设置 |
| B6 | **下载仍走 Python 代理** | `downloader.py:467-508`（`_localize_playlist`/`_proxy_playlist` → `media_server.stream_url`） | 多一跳（自己→自己）+ 代理的 A1/A2/A3 问题叠加 |
| B7 | 无速度/ETA、无并发上限 | `downloader.py:511-612`（只有百分比与 MB） | 用户无法判断"慢"是源慢还是我们慢 |

#### C. 体验缺口（对标时补齐）

| # | 缺口 | 现状 |
|---|---|---|
| C1 | 无**自动连播下一集** | `player.py:501-503`：`EndOfMedia` 只写一句提示 |
| C2 | 无**弹幕** | 全仓 grep `弹幕/danmaku/dandanplay` = 0 命中 |
| C3 | 无**字幕**（外挂/内嵌切换） | 只在文档板块有字幕无关的转换；播放器无字幕 API |
| C4 | 无**播放诊断**（后端/硬解/缓冲事件/码率/丢帧） | 只有 `_status` 文本 |
| C5 | 无**线路/源测速与排序** | `sources/registry.py` 有健康度（成功率/降级），无延迟/带宽测量 |
| C6 | 无**图片代理**，海报可能因防盗链 403 | 搜索结果直接用源站图片 URL |
| C7 | 无**超分/画质增强**入口 | 图库板块有增强，影视没有 |

### 1.3 改造前先量化（把"卡"变成数字）

实施前先加一节测量（可临时脚本，不发布），改造后用同一套指标对比：

| 指标 | 采集方式 | 现状预期 | 目标 |
|---|---|---|---|
| 首帧时间（点播放 → 第一帧） | `player` 记录 `setSource` 到 `LoadedMedia` 的毫秒差 | 3–20s | ≤ 2.5s（同一源、同清晰度） |
| 卡顿次数/10 分钟 | 统计 `mediaStatusChanged` 进入 `StalledMedia/BufferingMedia` 的次数与总时长 | 高 | ≤ 2 次 |
| CPU 占用（1080p 播放） | 任务管理器 / `psutil` 采样 | 60–100% | ≤ 25%（硬解生效） |
| 代理分片平均延迟 | 在代理里打点：收到请求 → 转发完成 | 100–600ms | ≤ 40ms（缓存命中）/ ≤ 200ms（回源） |
| 分片缓存命中率 | 代理计数 | 0% | ≥ 30%（seek/重播场景更高） |
| 单集下载平均速度 | 分片下载器统计（MB/s）与 `speed=` | — | 相对现状 ≥ 2× |
| 下载失败重试成功率 | 失败分片二次尝试成功比例 | — | ≥ 95% |

> 收集方式统一走"播放诊断"面板（见 3.3.4），运行时按 `F12` 打开、可导出日志文件。

---

## 2. 对标：MoonTV / LunaTV 与我们的差距

> 两个项目都是"服务端影视聚合站"（Web 端），我们是"桌面单机客户端"。**能抄的是机制，不是部署形态**。
> 以下事实来自各自仓库 README（链接见附录 C）。

### 2.1 MoonTV（`samqin123/MoonTV`）要点

| # | 机制 | 事实 |
|---|---|---|
| M1 | 技术栈 | Next.js 14 + Tailwind + TypeScript；播放器 **ArtPlayer + HLS.js**；可部署到 Docker / Vercel / Cloudflare |
| M2 | **多源聚合搜索** | "内置数十个免费资源站点，一次搜索立刻返回全源结果"；源配置在 `config.json` 的 `api_site`，**标准苹果 CMS V10 API**（`api` / `name` / `detail` 三字段） |
| M3 | **接口缓存** | `config.json` 的 `cache_time`（秒，示例 7200）—— 源接口结果按 TTL 缓存，避免重复打源站 |
| M4 | 搜索代理与图片代理 | 环境变量 `NEXT_PUBLIC_SEARCH_MAX_PAGE`（搜索最大页数）、`NEXT_PUBLIC_IMAGE_PROXY`（浏览器端图片代理）、`NEXT_PUBLIC_DOUBAN_PROXY` |
| M5 | 自定义分类 | `custom_category`（豆瓣 type+query 关键词） |
| M6 | 收藏/继续观看 | 存 localstorage / redis / D1 / upstash，**多端同步** |
| M7 | 智能去广告 | "自动跳过视频中的切片广告（实验性）" |
| M8 | 播放记录与多账户 | 非 localstorage 部署支持管理员后台 `/admin` 与多账户 |

### 2.2 LunaTV 家族要点（含信息密度最高的增强版 `stazeng/LunaTV-EE`）

> `MoonTechLab/LunaTV` 的 README 自述仍是 **MoonTV**，**仅支持 Docker 部署**（Kvrocks 推荐），部署后**为空壳**需自配源；增强版 `stazeng/LunaTV-EE`（v6.1.1）与 `brucemomo/Moon_TV`、`mtvpls/MoonTVPlus` 是主要分支。下面的"可借鉴机制"多数来自 EE 的**实读源码**（见 `docs/research/MoonTV-LunaTV-技术调研报告.md`）。

| # | 机制 | 事实（源码级） |
|---|---|---|
| L1 | **各仓库的链路并不一致（点播代理：有的有、有的没有）** | ⚠️ **认知纠正（第二版口径）**：<br>① `MoonTechLab/LunaTV` 与 `stazeng/LunaTV-EE`：**点播（VOD）不走代理**——把采集源返回的 `.m3u8` 绝对地址直接交给 hls.js；只有**直播**走 `/api/proxy/m3u8`（`src/app/live/page.tsx`）；② **`mtvpls/MoonTVPlus` 有真正的点播代理**：`GET /api/proxy/vod/m3u8` 与 `GET /api/proxy/vod/segment`，`source=` **必填**（缺失 400），并按 `SourceConfig.proxyMode?: boolean` **逐源开关**（未开启返 403 `Proxy mode not enabled for this source`），重写基址 `${protocol}://${host}/api/proxy/vod`，覆盖裸 URL 与 `EXT-X-MAP`/`EXT-X-KEY`/`EXT-X-STREAM-INF` 三类标签，另有 `validateProxyUrlServerSide()` 的 **SSRF 拦截**（`src/lib/server/ssrf.ts`）与统一的响应头构造（`src/lib/server/proxy-headers.ts`） |
| L2 | 服务端**不转码** | 三个仓库依赖树里都没有 ffmpeg/fluent-ffmpeg/WASM 转码器，Dockerfile 也不装；唯一的"转码"是客户端 `mux.js` 做 TS→MP4（下载用）。EE README 把"CPU 不足导致转码缓慢"列为卡顿原因**与实现不符**，别据此设计架构 |
| L3 | **hls.js 分级缓冲参数矩阵**（最值得抄的数值） | 三档模式：源码取值为 `standard` / `enhanced` / `max`（README 写作"省流/均衡/高质"，UI 文案是"默认/增强/强力"，**命名以代码为准**），参数分别 **30s/30s/60MB**、**45s/45s/90MB**、**90s/60s/180MB**（key `localStorage.playerBufferMode`）；桌面 `maxMaxBufferLength: 600`（移动 60–120）；`startFragPrefetch`/`testBandwidth` 桌面 true（**换更快起播**）；`fragLoadPolicy.default`：`maxTimeToFirstByteMs` 10000/6000、`maxLoadTimeMs` 120000/60000、`timeoutRetry.maxNumRetry` 4/2、`errorRetry.maxNumRetry` 6/3 + `retryDelayMs: 1000`；ABR：`abrBandWidthFactor` 0.95/0.8、`abrEwmaFastLive` 3/2、`maxStarvationDelay` 4/2。⚠️ **仅桌面生效**：移动端完全忽略该设置，走硬编码 8/10/15s |
| L3 | **hls.js 分级缓冲参数矩阵**（最值得抄的数值） | 三档模式：源码取值为 `standard` / `enhanced` / `max`（README 写作"省流/均衡/高质"，UI 文案是"默认/增强/强力"，**命名以代码为准**），参数分别 **30s/30s/60MB**、**45s/45s/90MB**、**90s/60s/180MB**（key `localStorage.playerBufferMode`）；桌面 `maxMaxBufferLength: 600`（移动 60–120）；`startFragPrefetch`/`testBandwidth` 桌面 true（**换更快起播**）；`fragLoadPolicy.default`：`maxTimeToFirstByteMs` 10000/6000、`maxLoadTimeMs` 120000/60000、`timeoutRetry.maxNumRetry` 4/2、`errorRetry.maxNumRetry` 6/3 + `retryDelayMs: 1000`；ABR：`abrBandWidthFactor` 0.95/0.8、`abrEwmaFastLive` 3/2、`maxStarvationDelay` 4/2。⚠️ **仅桌面生效**：移动端完全忽略该设置，走硬编码 8/10/15s |
| L4 | **"Netflix 式"测速**（注意语义） | 它做的是**源优选（选哪个源/线路）**，**不是**"测本机带宽后推荐缓冲模式"（README 有此说法但代码里没有任何路径把测速结果写回 `playerBufferMode`）。实现：桌面 `concurrency = 3` 批次并发、**最多测 20 个源**（权重最高的前 5 + 从剩余随机 15）；移动端只 `HEAD + AbortSignal.timeout(3000)`；**iOS13+/iPad 完全跳过测速**直接取权重最高源。HEAD 测 ping → 载首个分片算 `speedKBps = payload.byteLength/1024/(loadTime/1000)` → `video.videoWidth` 判画质（3840→4K/2560→2K/1920→1080p）→ 整体超时 3–4s → "提前停止" = 拿到元数据 + **1 个分片**即 `cleanup()`（保守阈值：4K 且 ≥8MB/s 或 2K 且 ≥6MB/s）→ **打分 = 分辨率 40% + 速度 40% + 延迟 20%**，再乘权重加成 `1+(weight-50)*0.005` |
| L5 | **下载器（质量很高）** | 队列 + N worker（**默认 6 并发**，UI 可调 1–16）、失败重试 **3 次 / 固定 1000ms**；**保序写入**：`pendingWrites: Map<index,data>` + `nextWriteIndex` 游标 + `writeLock` 串行化（**并发下载、串行落盘**，这是不丢数据的关键）；落盘三档自动降级（File System Access API → Service Worker 流 → 内存 Blob）；`PauseResumeController` 在 4 个检查点 await 实现暂停恢复。⚠️ 它**不是** HTTP Range 断点续传（其文档说法夸大了） |
| L6 | **代理分片的工程细节**（若要做代理，照这个做） | Node `http/https.Agent` keep-alive（`maxSockets:100`、`maxFreeSockets:20`、`keepAliveMsecs:30000`）、`ReadableStream` + **`highWaterMark: 65536` 背压**、**明确不转发 `Content-Length`**（与 chunked 冲突，是他们修过的 bug）、分片 `Cache-Control: max-age=300` 而**列表 `no-store`**、`Accept-Encoding: identity`、UA 伪装 `AptvPlayer/1.4.10` |
| L7 | **本地缓存两层架构** | 元数据进 Kvrocks（TTL 15min）+ 视频体进文件系统（TTL 12h、上限 2GB、LRU）；**缓存 key 优先用 `douban_id` 而不是 URL hash**（URL 时间戳刷新后仍能命中）；命中后 `createReadStream({start,end})` 直返 206；宣称节省流量 96% |
| L8 | **SSE 流式搜索 + 搜索内存缓存** | `/api/search/ws?q=` 逐源推 `source_result` 事件（最快的源先出结果）；搜索内存 LRU：10min TTL / 1000 条上限 / 5min 清理 / **403 写负缓存**；HTTP 侧 4 个 CDN 缓存头，**空结果不缓存** |
| L9 | 直播直连模式 | 播放前探测源站 CORS，允许则客户端直连（完全不占服务器带宽） |
| L10 | 许可证 | LunaTV = **CC BY-NC-SA 4.0（禁商用）**；MoonTV 分支声明 MIT —— **复用代码前必须确认**（我们只借鉴机制，不复制代码） |
| L11 | 豆瓣/图片代理可配 | 数据与图片代理都有 direct / 第三方 CDN / **server（服务端代理）** / custom 多档 |
| L12 | 授权与安全姿态 | 强密码、关闭公网注册、仅个人使用（版权风险提示）；配置可**订阅分发**（LunaTV：配置文件 base58 编码后提供 HTTP 服务即订阅链接） |
| L13 | **没有"失败自动换源"** | `artPlayer.on('error')` 近乎空实现（只 `console.error`），`hls.on(ERROR)` 只在**同一个源内部**恢复（`FRAG_PARSING_ERROR`→`startLoad()`、`BUFFER_APPEND_ERROR`→`BUFFER_RESET`、fatal `NETWORK_ERROR`→`startLoad()`、fatal `MEDIA_ERROR`→`recoverMediaError()`）；换源是**用户手动**的。✅ 但**换源保留进度**有实现：切源前写 `sessionStorage['temp_progress_{newSource}_{newId}_{episodeIndex}']` |
| L14 | 播放链路**零**预加载 | 全仓库 `preconnect`/`dns-prefetch` **0 命中**；`play/page.tsx` 注释写 `requestIdleCallback`、实现其实 `setTimeout((i+1)*100)`；`FastLink.tsx` 还主动关掉 Next 预取。只有图片 preload（≤10 张）与换源 tab 的分批测速 |
| L15 | 弹幕**无用户可配屏蔽** | 全仓库"屏蔽"0 命中；只有服务端硬编码过滤（长度 <2 或 >50、纯数字/纯符号、灌水词、平台水印）。**用户级关键词/正则屏蔽是 Kazumi 的能力，不是 LunaTV 的** |
| L16 | 弹幕**性能分级公式**（可移植） | `score = min(cores/4,1)*0.5 + min(memGB/1,1)*0.3 + (isMobile?0.2:0.5)*0.2` → `>0.7` high / `>0.4` medium / else low；三档**同屏上限 60/40/25**、**丢弃率 0.1/0.3/0.5**，全部走 Worker；low 档 `maxLength:30` + `translateZ(0)` |

> ⚠️ **对标时请以代码为准**：`docs/research/MoonTV-LunaTV-技术调研报告.md` 里有一节
> "**宣传口径 vs. 代码事实**"（6 行对照表），列出了 README 宣传语与源码实现不一致的地方
> （缓冲模式命名、测速的作用范围、自动换源、预加载、超分持久化、"CPU 转码导致卡顿"）。
> 本项目只要**机制与数值**，不采信未经源码验证的说法。

### 2.3 差距矩阵（我们的改造清单来源）

| 机制 | MoonTV/LunaTV | 我们现状 | 结论 |
|---|---|---|---|
| 请求头代填（Referer/UA） | ✅ 服务端代理（**VOD 代理只有 MoonTVPlus 有**：`/api/proxy/vod/*` + 逐源 `proxyMode`） | ✅ 已有（`media_server`，覆盖点播） | 保留并**重写实现**（3.1.1）。**我们的代理要做的事 = MoonTVPlus 的 VOD 路径设计 + EE 的流式/连接池工程细节** |
| Referer 伪装策略 | MoonTVPlus：`Referer: <目标 URL 自身>` + Chrome UA；EE：`AptvPlayer/1.4.10` 且不带 Referer | 只用"源站 origin"（`guess_referer`） | **增强为三段回退**：源站 origin → 目标 URL 自身 → 不带 Referer（缓存本次成功的组合，见 3.1.1） |
| m3u8 清单重写 | ✅ | ✅ 已有（`_rewrite_playlist`） | 保留，加短缓存与 ETag |
| 分片级缓存 | 边缘/CDN 缓存 | ❌ `Cache-Control: no-store` | **新增**（磁盘 LRU + Range 精确） |
| 连接复用/并发 | Node 连接池 | ❌ 每请求新连接 | **新增**（`Session` + `HTTPAdapter` 池） |
| 源接口结果缓存 | ✅ `cache_time` | ❌ 每次实时打源 | **新增**（搜索/详情 TTL 缓存，默认 30 分钟） |
| 流式搜索（边搜边出） | ✅（LunaTV） | ❌ 全部源返回后一次性显示 | **新增**（`search_all` 改成逐源回调） |
| 自动换源/失效降级 | ❌ **没有**（`on('error')` 近乎空实现，换源靠用户手动；HLS 错误只在同源内恢复） | ✅ **已有**（`sources/registry.py` 健康度 + 自动换源 + 跨源定位同一集） | **这是我们的领先项**：保留并增强（加测速排序） |
| 换源保留播放进度 | ✅（`sessionStorage` 临时进度） | ✅ 已有（`switch_source(start_ms=…)`） | 对齐即可 |
| 线路/源测速 | ✅（打分 = 分辨率 40% + 速度 40% + 延迟 20%） | ❌ 只有成功率健康度 | **新增**（把这套打分搬进 `SourceHealth`） |
| 缓冲深度/ABR 档位预设 | ✅（三档参数矩阵，**仅桌面**生效） | ❌ 固定缓存、无档位 | **新增**（数值借用，命名自定：省流/均衡/高质） |
| 播放链路预加载 | ❌ 零预取（只有图片 preload） | ❌ 无 | **新增**（分片预取 + 清单短缓存，见 3.1.2） |
| 弹幕用户级屏蔽 | ❌ 只有服务端硬编码过滤 | ❌ | **新增**（学 Kazumi：关键词 + `/正则/` 语法） |
| 弹幕性能分级（同屏上限/丢弃率） | ✅ 有公式（L16） | ❌ | **新增**（按 CPU/内存给 25/40/60 三档同屏上限） |
| 配置/源订阅 | ✅（base58 订阅链接） | ❌ | **新增**（第七板块的规则订阅先落地，影视源订阅复用同一机制） |
| 弹幕 | ✅（LunaTV） | ❌ | **新增**（3.3.3，第七板块复用） |
| 图片代理 | ✅ | ❌ | **新增**（3.3.6 海报防盗链） |
| 自动连播下一集 | ✅ | ❌ | **新增**（3.3.1） |
| 广告分片跳过 | ✅（实验性） | ❌ | P2（复用 HLS 分片元数据，见 3.3.7） |
| 转码/remux | 服务端可选 | 有本地 ffmpeg | **按需 remux**（不做实时转码） |
| 多用户/鉴权/云同步 | ✅ | 不需要（单机 SQLite） | 不抄 |
| 部署形态（Docker/Vercel/Redis） | ✅ | 不需要 | 不抄 |

---

## 3. 第六大板块改造方案

> 原则：**先让"卡"和"慢"消失，再补体验**；每一步都能单独验收、可回滚；不改变既有数据库字段语义（只做**增量**迁移）；既有测试全部保持通过。

### 3.1 阶段 V1（P0）：播放流畅度

#### 3.1.1 重写本机媒体代理（`services/media_server.py` → `services/media_proxy/`）

目标：把代理从"瓶颈"变成"加速器"。

```
services/media_proxy/
├── __init__.py     对外 API（保持现有函数名兼容：stream_url/hls_url/media_url/is_local_url/wrap_playlist/local_base_url）
├── server.py       ThreadingHTTPServer（保留），路由与响应
├── upstream.py     requests.Session 池 + 超时/重试策略 + 头注入
├── cache.py        分片磁盘缓存（LRU + 配额 + 索引 + Range 索引）
└── playlist.py     m3u8 清单重写 + 短时缓存（按 URL 记忆 2–5s，避免同一份清单被反复拉取）
```

关键改动（逐条对应 1.2-A）：

| 编号 | 改动 | 做法 |
|---|---|---|
| V1-a | 连接复用 | 每目标主机一个 `requests.Session`（懒创建、上限 8 个），`HTTPAdapter(pool_connections=8, pool_maxsize=16, max_retries=0)`；`Session.headers` 预置 UA |
| V1-b | 流式转发 | 不再 `upstream.content`；用 `iter_content(_CHUNK)` 边收边发；**有 `Content-Length` 就带上，没有就用 chunked 并显式 `Connection: close`**（保留现在"能界定长度"的经验，避免 HTTP/1.1 挂死） |
| V1-c | 分片磁盘缓存 | 键 = `sha1(url + Range)`，值 = 文件（`数据目录/video/cache/<前2位>/<hash>.bin`）+ 元数据（状态码、Content-Type、Range、ETag）。命中直接 `sendfile` 式转发，**根本不出外网** |
| V1-d | 缓存淘汰 | 配额（默认 2 GB，可配 256 MB–20 GB）+ LRU；只缓存"媒体分片/清单/图片"这三类，不缓存 JSON 接口 |
| V1-e | Range 语义 | 客户端带 `Range` 时透传给上游并**只在 206 时缓存该区间**；缓存命中区间不足时回源补齐（避免"半截缓存"坏播放） |
| V1-f | 重试策略分层 | 清单：3 次 + 退避（保留）；**分片：1 次快速重试（≤150ms）→ 立刻返回 502 让播放器自己重试**（播放器重试更聪明，别把它堵住）；图片：2 次 |
| V1-g | 超时分层 | 清单 (5, 15)s；分片 (5, 20)s；图片 (5, 10)s；直链大文件 (5, 60)s 但改为**流式**后不再是瓶颈 |
| V1-h | 并发上限 | `ThreadingHTTPServer` 加信号量（默认 16 并发），防止十几个分片同时打满出口带宽导致每片都慢 |
| V1-i | 预热与预取 | 清单重写时**顺手预取前 N 个分片**（默认 3，可配 0–10）到磁盘缓存（后台线程，可取消）；播放器请求到时直接命中 |
| V1-j | 清单短缓存 | 同一 m3u8 在 TTL（默认 3s）内直接返回上次重写结果，避免"一首歌反复拉清单" |
| V1-k | 可观测 | 计数器：请求数 / 命中数 / 回源字节 / 平均延迟 / 502 数，暴露给"播放诊断"面板 |
| V1-l | **缓存键用"内容身份"而不是 URL** | 分片键优先用 `sha1(episode_key + 分片序号 + Range)`，若拿不到 episode 上下文再退化为 `sha1(url + Range)`。理由：源站常给 URL 挂时间戳/签名（`?t=1712…&sign=…`），纯 URL 键会让"同一个分片"永远不命中——这正是 LunaTV-EE 用 `douban_id` 作键的原因（见 L7） |
| V1-m | 清单/分片缓存头策略（借用 L6） | **清单 `no-store`**（要能及时反映源站变化）、**分片 `max-age=300`**；上游请求带 `Accept-Encoding: identity`（避免把 gzip 内容当媒体转发、也省 CPU）；对"URL 每次都变"的源，命中判定走 V1-l 的身份键而不是浏览器缓存 |
| V1-n | 上游连接池参数（借用 L6 的量级，按桌面端缩小） | `HTTPAdapter(pool_connections=8, pool_maxsize=16)`、`Connection: keep-alive`、空闲连接上限 20、keep-alive 30s；**上游是 chunked（无 `Content-Length`）时不要伪造长度**：要么按 chunked 原样转发、要么读完再发（缓存写入路径），避免"长度与内容不符"这类播放器难以诊断的错误 |
| V1-o | **逐目标"代理开关"（借 MoonTVPlus 的 `proxyMode`）** | 不是所有源都需要代理（能直连的更省一跳）。为每个源/主机维护 `proxy_mode`：`auto`（默认，失败过一次再改走代理）/ `always` / `never`，成功率与 403 计数写进源健康度（3.3.5）。这一条同时解决了"代理本身开销"与"某些源必须带 Referer"这对矛盾 |
| V1-p | **Referer/UA 三段回退（借两个实现的差异）** | 同一 URL 依次尝试：① 源站 origin（现有 `guess_referer`）→ ② **目标 URL 自身**（MoonTVPlus 的做法）→ ③ 不带 Referer；把成功的组合缓存进源健康度，后续直接用（失败也不至于把播放卡死：每段只试 1 次、总量 3 次封顶） |
| V1-q | **本地代理的安全收口（借 MoonTVPlus 的 SSRF 校验思路）** | 我们只监听 `127.0.0.1` + 随机路径令牌（已有）；在此基础上再加：只接受 `http/https`（现有代码已限制 ✓）、**拒绝环回/链路本地/私网/组播地址**（防被诱导去读本机或内网服务）、响应大小上限（分片 ≤ 64 MB、清单 ≤ 4 MB），并把这些规则写成测试 |

**兼容性**：`stream_url` / `hls_url` / `media_url` / `is_local_url` / `wrap_playlist` / `local_base_url` / `ensure_server` 全部保留同名同签名（`boards/video/player.py`、`core/video/downloader.py`、`boards/video/widgets.py` 无需改动即可获得收益）。旧模块保留为**转发壳**，避免一次性大改。

#### 3.1.2 边下边播缓冲层（PlaybackBuffer）

在新代理之上加一个"播放会话"概念，解决"起播慢 + 卡了就死"：

- 物件：`services/media_proxy/session.py::PlaybackSession`（每个播放地址一个）
  - `warmup(n=3)`：并发取前 n 个分片入缓存（并发 3）；
  - `prefetch(position_ratio)`：按播放进度把"接下来 10–30 秒"的分片排队预取（队列并发 2–4，优先级：即将播放 > 后续）；
  - `stall_watchdog()`：连续 2 次 502/超时或缓冲 > 3s，向上层发信号（触发降档/换源，见 3.1.5/3.1.6）；
  - `close()`：取消队列、清理超出配额的缓存。
- 与播放器对接：`VideoPlayerDialog` 在 `_load()` 时创建会话；`player.positionChanged` 每 2s 更新预取位置。

**为什么不做"边下边播的自研播放器"**：QtMultimedia 的 HLS 解复用器已经很稳，我们要做的只是让它**读本地**。分片进缓存后，播放器读的就是本机磁盘（零外网抖动）。

#### 3.1.3 解码后端与硬件加速（改动小、收益大）

在 `app/main.py` 创建 `QApplication` 之前设置（**必须早于任何 QtMultimedia 使用**）：

```python
# 伪代码，仅示意（实施时放在 app/main.py 最前面 + 设置项控制）
os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")            # WMF 不支持 HLS，固定 FFmpeg
if video_hw_decode_pref():                                     # 设置项：硬解开/关/自定义
    os.environ.setdefault("QT_FFMPEG_DECODING_HW_DEVICE_TYPES", "d3d11va,d3d12va,dxva2")
else:
    os.environ.setdefault("QT_FFMPEG_DECODING_HW_DEVICE_TYPES", ",")   # 空列表 = 关闭硬解
```

配套：

- 设置项（`设置 → 影视` 新增「播放」分组）：`video/hw_decode`（自动/开启/关闭）、`video/media_backend`（自动/ffmpeg/wmf，默认自动=ffmpeg）、`video/hw_texture_conversion`（GPU 纹理转换，默认开）、`video/hw_profile_mismatch`（默认关，附风险说明）。
- **自动降级**：若开启硬解后 3 秒内 `errorOccurred` 或一直 `StalledMedia`，记一次失败日志并提示"已回退软解（重启播放生效）"（环境变量是进程级，回退需重启播放窗口 → 用**独立播放进程**解决，见 3.1.4 备选）。
- 诊断面板显示：当前后端（`QT_MEDIA_BACKEND` 值）、硬解设备列表、`QT_FFMPEG_DEBUG` 摘录出的解码器名。

> 备选（若"重启才能改硬解"被判定体验差）：把播放器做成**独立子进程**（`python -m modu_workbench.player_host <json>`），主界面用 `QProcess` 通信。这样每个播放会话都能带自己的环境变量与开关，也顺带隔离了播放崩溃。**代价**：窗口嵌入、快捷键、进度回传都要重做。建议：**V1 先用环境变量 + 重启提示，V3 再评估独立进程**。

#### 3.1.4 渲染路径：`QVideoWidget` → `QGraphicsView + QGraphicsVideoItem`

为什么必须换：`QVideoWidget` 在 Windows 上是原生子窗口，**Qt 控件无法可靠叠在上面**（QSS 与透明叠加都会出问题）→ 弹幕（第七板块核心）、字幕、缓冲指示、统计浮层全都做不了。

方案：

```
DanmakuSurface(QGraphicsView)
└── QGraphicsScene
    ├── QGraphicsVideoItem      # 视频（setSize/keepAspectRatio）
    ├── SubtitleItem            # 字幕（底部居中，可描边）
    ├── DanmakuLayer            # 弹幕轨道（QGraphicsSimpleTextItem 池化复用）
    └── StatsOverlay            # F12 诊断浮层
```

要点：

- `QGraphicsVideoItem` 需要 `QGraphicsScene` + `QGraphicsView`，`view.setViewportUpdateMode(MinimalViewportUpdate)`、`setOptimizationFlags(DontSavePainterState | DontAdjustForAntialiasing)`；
- 视频尺寸变化用 `item.setSize(...)` + `fitInView(..., KeepAspectRatio)`；
- 全屏 = `view` 的 `showFullScreen()`（复用现有 `toggle_fullscreen`）；
- 鼠标事件：`view` 上装事件过滤器实现"单击暂停/双击全屏"（替代现在的 `_ClickVideoWidget`）；
- 弹幕层用**轨道分配器**（屏幕高度切 N 条轨道，每条轨道记录"当前占用到的时间戳"），空闲 item 入池复用（避免每帧 new）。

风险与回退：若 `QGraphicsVideoItem` 在某台机器上渲染异常（少见），保留 `QVideoWidget` 分支，用设置项 `video/render_path`（auto/graphics/widget）切换；`auto` = 先 graphics，检测到 3 次 `errorOccurred` 或黑屏（1.5s 内无 positionChanged）则本次会话回退 widget。

#### 3.1.5 自适应选路（ABR-lite）

现状是"永远锁最高"，改造为**三段式**：

1. **开播选路**：拿到主清单后按"上次实测带宽 × 0.7"挑变体；没有历史时用"目标档 = 720P（可配：自动/1080P/720P/最低）"。
2. **播放中降档**：`stall_watchdog` 触发（3s 内缓冲 2 次或单次 > 3s）→ 自动降到下一档（同源同清晰度列表里 `rank` 次高），提示"网络偏慢，已切到 720P"。最多降 2 次；已是最后一档则触发换源。
3. **稳定升档**（可选，默认关）：连续 3 分钟无卡顿且带宽余量 > 1.5× → 升一档（避免"反复横跳"，仅在设置里开启）。

实现落点：

- `core/video/abr.py`（新增）：`BandwidthMeter`（滑动窗口记录最近 20 次分片下载的 `bytes/duration`，取 P50）、`pick_variant(variants, meter, policy)`、`next_lower(variants, current)`；
- `core/video/library.py::_lock_best_variant` → 改名为 `_pick_variant`（保留旧函数名转发，避免破坏调用方与测试），内部走 ABR 策略；
- 播放器新增信号 `stallDetected(reason)`，`board.py` 接收后走"降档 → 换源"链路（复用现有 `_on_quality_requested` 与 `registry.resolve(exclude=…)`）。

设置项：`video/quality_policy`（auto/1080p/720p/lowest/highest）、`video/auto_downgrade`（默认开）、`video/auto_upgrade`（默认关）。

**补充：缓冲深度档位预设（`video/buffer_mode`）**

"缓存多少才够"直接决定卡不卡，而对标项目已经给出了经过实践的三档参数（L3，数值取自 LunaTV-EE，**命名我们按中文习惯定为"省流 / 均衡 / 高质"**）。我们把它映射到自己的三个旋钮上：

| 档位 | 分片缓存配额 | 预取深度 | 起播等待（warmup） | 适用 |
|---|---|---|---|---|
| 省流 | 256 MB | 2 片 | 1 片即可起播 | 移动热点、按流量计费、老机器 |
| **均衡（默认）** | 1 GB | 3–5 片 | 2 片 | 家用宽带 |
| 高质 | 2 GB（= 默认缓存配额上限） | 6–10 片 | 3 片 | 千兆/局域网源、追求不卡 |

> 对照说明：LunaTV-EE 的 `standard/enhanced/max` 是 hls.js 的**秒数**语义（30/45/90s），我们是"本地磁盘片数"语义；**秒数与片数的换算 = 档位秒数 ÷ 分片时长**（典型 4–10s/片），所以"均衡 ≈ 45s ≈ 5–10 片"。上表取的是"够用且不占满磁盘"的保守值，并且**与 3.1.2 的预取队列共用同一个档位开关**。

#### 3.1.6 起播加速与自愈

| 场景 | 现状 | 改造 |
|---|---|---|
| 首次缓冲 | 等 20s 只提示 | 3s 未起播 → 提示"线路慢，正在换用 720P 起播"；8s 未起播 → 自动换源（`exclude` 当前源）；20s → 建议"先下载后播放"并给出按钮 |
| 播放中 stall | 无处理 | 3s → 预取加急；6s → 降档；12s → 换源（同集） |
| 换档/换源 | 用户手动 | 自动，且**保留播放位置**（`start_ms` 已在 `switch_source` 支持） |
| 片源失效 | 报错文本 | 报错同时给"换源重试"按钮（已有能力，UI 更明确） |

`NATIVE_GRACE_*` 常量语义同步调整（`player.py:44-46`），并把判定从"只看 mediaStatus"改为"看 position 是否前进"（更准）。

> **换源与自愈是我们的领先项**：对标项目里 `on('error')` 近乎空实现、换源全靠用户手动（L13），而 `sources/registry.py` 已经能"跨源定位到同一集"。所以这块不是"补齐"，而是"把已有的能力接进播放器的自动回路"。

### 3.2 阶段 V2（P0/P1）：下载提速

#### 3.2.1 自研分片下载器（`core/video/segment_downloader.py`，新增）

不再依赖 ffmpeg 才能快：

```python
class SegmentDownloader:
    def __init__(self, *, concurrency: int = 8, session_pool: int = 4,
                 retries: int = 3, timeout: float = 20.0, on_progress=None, cancel=None)
    def fetch_playlist(url, headers) -> HlsPlaylist          # 复用 hls.parse_m3u8
    def download(playlist, dest: Path, *, resume: bool = True) -> Path
```

关键设计：

| 项 | 做法 |
|---|---|
| 并发 | `ThreadPoolExecutor(max_workers=concurrency)`（**默认 6**，可配 2–16——默认值取自 LunaTV-EE 的 6 worker 与 Kazumi 的"分片并发默认 3"之间的折中） |
| 连接 | **每线程独立 `Session`**（避免 requests 线程安全问题，同时保持 keep-alive） |
| **保序写入** | 借鉴 L5 的"并发下载、串行落盘"：完成的分片按序号进 `pending` 字典，写盘由**单一写线程 + 游标 `next_write_index`** 顺序推进（`threading.Condition` 实现"写锁"）。这样即使并发乱序完成，也不会把 TS 拼错 |
| 落盘位置 | 分片先写 `dest.with_suffix('.parts/')`（每片一文件），全部完成后**顺序拼接**为目标文件；拼接失败可原样重试（分片还在） |
| 断点续传 | `.parts/` 里已存在且大小/校验通过的分片直接跳过（记录在 `manifest.json`：url/序号/大小/是否加密/CRC），**支持"下载几十集、中途关掉明天继续"** |
| 暂停/继续 | 参考 L5 的"检查点"做法：在"取清单后 / 每片开始前 / 每片写盘后 / 拼接前"四个点等待门闸（`pause_event`），不打断已开始的单次请求；`shutdown()` 保证线程退出、`.parts` 保留 |
| 重试 | 单分片 3 次（0.3/0.8/1.5s 退避，失败重试间隔与 LunaTV-EE 的 1000ms/3 次同量级）→ 仍失败标记坏片；坏片率 > 2% 判定整集失败（避免产出残片） |
| 解密 | 复用现有 `aes.aes128_cbc_decrypt` + `hls.segment_iv`（SAMPLE-AES 仍交 ffmpeg） |
| 进度 | 已下载字节 / 总字节（用 `EXTINF` 估算总时长与平均码率 → 估算总字节）→ 百分比 + 速度 + ETA；同时给出"分成 N 片、已完成 M 片" |
| 限速 | 令牌桶（默认不限，可配 1–50 MB/s），避免把家里带宽打满影响别人 |
| 合并 | 顺序拼接 `.ts`；若目标为 `.mp4` 且 ffmpeg 可用，`ffmpeg -i concat -c copy`（一次 remux，不重编码） |

#### 3.2.2 ffmpeg 路径改造（不再当唯一选项，而是当"最稳的那条"）

1. **优先直连**：探测目标 ffmpeg 是否支持 `-headers`（`ffmpeg -h protocol=http` 或直接跑一次极小请求）→ 支持就直连源站带 `-headers Referer/UA`，**跳过 Python 代理**；
2. 不支持则**回退本地 playlist + 代理**（现有路径，但代理已被 3.1.1 重写，不再拖后腿）；
3. 网络参数补齐：`-rw_timeout 20000000`、`-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5`、`-http_persistent 1`、`-thread_queue_size 1024`（按 ffmpeg 版本探测支持性，不支持就跳过）；
4. **按需 remux**：`-c copy` 保持不变（不重编码）；仅当输出格式不被目标容器支持时才 `-c:v copy -c:a aac`（极少）。

#### 3.2.3 批量下载并发

- `download_many()` 改为 `ThreadPoolExecutor(max_workers=video_download_parallel)`（**默认 2**，可配 1–4——与 Kazumi 的"同时下载集数默认 2"一致），**集内并发**（3.2.1 的 6 路）× **集间并发**（2）× 全局上限（防止 12 路打满）；
- 队列与进度统一走现有 `TaskBar`（`on_progress`/`on_task_done` 签名不变，UI 不改）；
- 失败项进入"重试队列"（面板上给"重试失败项"按钮）；
- 新增"暂停/继续"（见 3.2.1 的四检查点门闸；`shutdown()` 保证线程退出）。

### 3.3 阶段 V3（P1）：体验补齐

#### 3.3.1 自动连播与播放队列

- `EndOfMedia` → 若"下一集"存在且设置 `video/auto_next`（默认开）→ 3 秒倒计时后切下一集（窗口内可点"取消"）；
- 播放队列：搜索页/详情页"播放整季"→ 队列入播放器；播放器加"上一集/下一集/停止连播"；
- 与 `video_play_records` 结合：续播按集记录（已支持）。

#### 3.3.2 字幕

- 外挂：`SubRip/WebVTT` 解析（标准库即可）+ 自动匹配文件名（`<视频名>.srt`）→ `QGraphicsSimpleTextItem` 渲染（描边：先画 8 方向黑色偏移再画白色）；
- 内嵌：QtMultimedia 无字幕轨 API → **不做内嵌**，如实提示"内嵌字幕请在下载后用《墨软转换》抽出"（ffmpeg `-map 0:s:0` 已具备）；
- 设置：`video/subtitle_auto`、`video/subtitle_font_size`、`video/subtitle_color`。

#### 3.3.3 弹幕（影视板块先做"能看"，第七板块做"完整"）

- 数据源：**只做弹弹play 开放接口**（附录 A：`/api/v2/search` → `episodeId` → `/api/v2/comment/{episodeId}?withRelated=true`）；Kazumi 另外接了 BiliBili/Gamer 两源，但那两条链路没有公开文档与稳定约定，**不做**；
- 匹配：标题 + 集数（复用 `core/video/sources/matcher.py` 的相似度）；匹配不上时给"手动搜索/导入本地 xml"入口；
- 渲染：`DanmakuLayer`（见 3.1.4），轨道分配 + 速度按屏幕宽度与时长计算 + 透明度/字号可调；**同屏上限按机器性能分级**（借 L16 的公式：`min(cores/4,1)*0.5 + min(mem_GB/1,1)*0.3 + 0.1`，三档上限 25/40/60、超出丢弃率 0.5/0.3/0.1）；
- 缓存：弹幕 XML 落盘（`数据目录/video/danmaku/<hash>.xml`），支持导入/导出（.xml，兼容主流格式）；
- 设置：`video/danmaku_enabled`（默认**关**，首次由用户开启）、`video/danmaku_opacity`、`video/danmaku_speed`、`video/danmaku_block_words`（**关键词 + `/正则/`**）、`video/danmaku_max_lines`、`video/danmaku_dedupe`（同屏重复合并，默认关）；
- 合规：只读、严格缓存、不做批量抓取（附录 A 列了官方使用约定）。

#### 3.3.4 播放诊断面板（F12）

显示并导出：

| 分组 | 内容 |
|---|---|
| 环境 | Qt 版本、`QT_MEDIA_BACKEND`、硬解设备列表、是否 GPU 纹理转换 |
| 媒体 | 容器/编码/分辨率/帧率/码率（从 `QMediaMetaData` 与 ffprobe 摘取）、当前变体与档位策略 |
| 网络 | 代理计数器（请求/命中/回源字节/P50 延迟/502 数）、分片缓存占用、预取队列长度 |
| 播放 | 起播耗时、stall 次数与总时长、position 前进速率（近似有效帧率）、当前音频/视频同步漂移 |
| 操作 | "复制诊断信息"、"导出日志"（含最后 200 行 ffmpeg/Qt 日志）、"清空分片缓存" |

#### 3.3.5 源测速与排序（复用 registry 健康度）

- 在 `SourceHealth` 上加 `latency_ms`（滑动 P50）与 `last_speed_mbps`；
- 搜索/解析成功后记录耗时；"源设置"里加"测速"按钮（**默认并发 3、最多测 20 个源**，与 LunaTV-EE 的量级一致；可按源数缩小），结果写入健康度并按延迟排序（可开关 `video/sort_by_speed`）；
- **打分公式借自 L4**（它解决的是"选哪个源/线路"，不是"调缓冲"）：
  `score = 分辨率 40% + 速度 40% + 延迟 20%`，再乘权重加成 `1 + (weight - 50) * 0.005`；
  其中"速度"用"取首个分片的 `bytes/耗时`"估算（不必下完整个分片），整体超时 3–4 秒，命中"4K 且 ≥8 MB/s 或 2K 且 ≥6 MB/s"即可提前结束；
- 换源顺序 = 用户顺序 × 健康度 × 测速得分（默认只按健康度降级，**测速排序默认关**，保持行为可预期）。

#### 3.3.6 图片代理（海报防盗链）

- 新增代理路由：`/img/?u=…&r=…`（复用 3.1.1 的 Session/缓存，只是 Content-Type 属图片、加 `Cache-Control: max-age=86400`）；
- 搜索/详情/历史列表的海报 URL 统一过 `media_server.image_url()`；
- 失败回退：占位图（用 `ui_kit` 的 EmptyState 图形或统一占位 PNG）。

#### 3.3.7 广告分片跳过（P2，对标 M7）

- 现状：HLS 已能拿到每个分片的 `EXTINF` 时长与 URI；
- 做法（保守）：只对**用户显式开启**的会话生效 `video/skip_ad_segments`；识别规则 = "某分片时长 < 0.5×中位时长 且 与前一片分属不同 CDN 目录"，命中后**提示**"疑似广告分片（第 N 片，X 秒），已跳过"而不是静默丢弃；跳过即在重写的清单里删除该行（`#EXT-X-DISCONTINUITY` 一并处理）；
- 风险：误判会丢正片内容 → 默认关闭，且只跳过"独立成片、时长明显异常"的分片，最多跳 3 片。

### 3.4 接口与数据变更清单

**新增设置键（`设置 → 影视`，全部 `type=bool`/`int`/`str` 读，遵循 v1.0.5 的 `type=bool` 约定）**

```
video/media_backend            auto|ffmpeg|wmf       默认 auto（=ffmpeg）
video/hw_decode                 auto|on|off          默认 auto（on）
video/hw_texture_conversion     bool                 默认 true
video/hw_profile_mismatch       bool                 默认 false
video/render_path               auto|graphics|widget 默认 auto
video/cache_dir                 str                  默认 数据目录/video/cache
video/cache_quota_mb            int（256–20480）     默认 2048
video/prefetch_segments         int（0–10）          默认 3
video/proxy_concurrency         int（2–32）          默认 16
video/segment_concurrency       int（2–16）          默认 6
video/download_parallel         int（1–4）           默认 2
video/download_speed_limit_mbps int（0=不限）        默认 0
video/skip_ad_segments          bool                 默认 false（P2，见 3.3.7）
video/proxy_mode                auto|always|never    默认 auto（见 3.1.1 V1-o；也可按源覆盖）
video/quality_policy            auto|highest|1080p|720p|lowest  默认 auto
video/auto_downgrade            bool                 默认 true
video/auto_upgrade              bool                 默认 false
video/auto_switch_on_error      bool                 默认 true（播放失败自动换源/换线路，含保留进度）
video/buffer_mode               low|balanced|high    默认 balanced（对应"省流/均衡/高质"，见 3.1.5）
video/auto_next                 bool                 默认 true
video/subtitle_auto             bool                 默认 true
video/danmaku_enabled           bool                 默认 false（首次需用户开启）
video/danmaku_opacity           int（20–100）        默认 80
video/danmaku_speed             int（4–12）          默认 8（秒/屏，对齐 Kazumi 的默认 8s）
video/danmaku_block_words       str（逗号分隔，`/正则/` 生效）  默认 ""
video/danmaku_max_lines         int（10–60）         默认 按性能分级自动（25/40/60）
video/danmaku_dedupe            bool                 默认 false
video/sort_by_speed             bool                 默认 false
video/diag_log_lines            int（100–2000）      默认 200
```

**数据库（迁移 `v3_video_cache`，纯增量）**

```sql
-- 下载断点续传与分片索引（也可只落文件，这里落库便于 UI 展示与清理）
CREATE TABLE IF NOT EXISTS video_download_tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id      INTEGER NOT NULL DEFAULT 0,
    episode_label TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT '',
    url           TEXT NOT NULL DEFAULT '',
    dest_path     TEXT NOT NULL DEFAULT '',
    parts_dir     TEXT NOT NULL DEFAULT '',
    total_parts   INTEGER NOT NULL DEFAULT 0,
    done_parts    INTEGER NOT NULL DEFAULT 0,
    state         TEXT NOT NULL DEFAULT 'pending',   -- pending/running/paused/failed/done
    message       TEXT NOT NULL DEFAULT '',
    updated_at    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_video_download_state ON video_download_tasks(state, updated_at DESC);

-- 播放事件采样（诊断面板与"卡顿率"统计）
CREATE TABLE IF NOT EXISTS video_play_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id   INTEGER NOT NULL DEFAULT 0,
    episode    TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL DEFAULT '',   -- start/stall/recover/switch_quality/switch_source/end
    detail     TEXT NOT NULL DEFAULT '',
    value_ms   INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_video_play_events_time ON video_play_events(created_at DESC);
```

> 注意：`tests/platform/test_db_schema.py` 有"表名前缀"与"索引不重名"闸门，新增表必须加进该测试的表清单，并在 `docs/DATABASE.md` 同步。

**代码接口（新增/改动，含兼容策略）**

| 文件 | 变更 | 兼容 |
|---|---|---|
| `services/media_server.py` | 保留为转发壳，实现移入 `services/media_proxy/` | 同名同签名 |
| `services/media_proxy/{server,upstream,cache,playlist,session}.py` | 新增 | — |
| `core/video/abr.py` | 新增（带宽估算 + 档位选择） | — |
| `core/video/segment_downloader.py` | 新增（并发分片下载 + 续传） | — |
| `core/video/downloader.py` | `download_many` 并发化；`_download_hls` 优先走 `segment_downloader`；ffmpeg 参数补齐 | `download_episode/download_many` 签名不变 |
| `core/video/library.py` | `_lock_best_variant` → `_pick_variant`（旧名保留转发） | 旧测试不受影响 |
| `core/video/storage.py` | 新表 CRUD（下载任务/播放事件） | 纯增量 |
| `boards/video/player.py` | `QGraphicsVideoItem` 渲染 + 弹幕/字幕层 + 诊断面板 + 自动连播信号 | `VideoPlayerDialog` 构造参数兼容，新增 keyword-only |
| `boards/video/board.py` | 接收 stall/换档信号、下载队列 UI、诊断入口 | — |
| `ui_kit/settings/video.py` | 新增「播放」「缓存」「弹幕」三组设置 | — |
| `app/main.py` | 启动时按设置注入 QtMultimedia 环境变量；自检新增 `video_playback`（见 3.5） | — |

### 3.5 测试与验收

**单元/集成测试（`tests/board_video/`）**

| 用例 | 断言 |
|---|---|
| `test_proxy_cache_hit` | 同一分片请求两次 → 上游只被请求 1 次；第二次延迟 < 50ms（用假上游计时） |
| `test_proxy_streaming_first_byte` | 大响应（10 MB 假上游）在收到第 1 个 chunk 后 100ms 内就开始回写（不等全量） |
| `test_proxy_range_semantics` | `Range: bytes=100-199` → 上游收到相同 Range、响应 206、缓存只记该区间 |
| `test_proxy_uses_session` | 连续 3 次同主机请求复用同一连接（假 Session 计数 adapter 调用） |
| `test_proxy_retry_policy` | 分片失败 1 次即返回 502（不再阻塞 1.8s+）；清单失败会重试 |
| `test_proxy_quota_eviction` | 配额 1 MB 时写入 3 MB 分片 → 旧文件被淘汰、总占用 ≤ 配额 |
| `test_prefetch_warms_cache` | `PlaybackSession.warmup(3)` → 前 3 片已入缓存 |
| `test_abr_picks_by_bandwidth` | 估算带宽 3 Mbps → 选 720P；`policy=highest` → 选 1080P |
| `test_abr_downgrade_on_stall` | 注入 2 次 stall → 档位下降一级；已最低档 → 抛"需要换源" |
| `test_segment_downloader_concurrency` | 12 片、并发 4：假上游记录最大同时在线数 ∈ [2,4]；产出文件字节序正确 |
| `test_segment_downloader_resume` | 中断后重跑 → 已完成分片不再下载（假上游计数），最终文件完整 |
| `test_segment_downloader_retry_and_badpart` | 1 片连续失败 → 报错且不产出残片；瞬时失败 1 次 → 成功 |
| `test_download_many_parallel` | 4 个任务、并发 2 → 总耗时 < 串行 60%（假下载器 sleep） |
| `test_player_graphics_surface` | `VideoPlayerDialog` 用 graphics 路径构建成功；全屏/暂停/seek 不异常（offscreen） |
| `test_danmaku_layer_tracks` | 100 条弹幕、8 条轨道 → 冲突轨道不重叠（算法单测，不用 GUI） |
| `test_subtitle_parse_and_render` | srt 解析时间轴正确；渲染 item 文本与位置符合设置 |
| `test_auto_next_flow` | 播放结束 → 触发 `episodeRequested(1)`（替身播放器） |
| `test_settings_defaults_and_read` | 新设置键默认值正确、`type=bool` 读取（含注册表字符串 "false"） |

**性能预算测试（`tests/board_video/test_video_nonfunctional.py`，新增）**

- 本地假上游（`http.server`）+ 200 个 512 KB 分片：
  - 代理吞吐（4 并发）：≥ 80 MB/s（本机回环，排除外网）→ 挡住"每请求新建连接/全量缓冲"的回归；
  - 缓存命中路径：≥ 300 MB/s；
  - 分片下载器（8 并发）：≥ 60 MB/s；
  - `_serve_proxy` 首字节延迟：< 120ms。
- 自检项：`app/main.py` 新增 `video_playback`（`MODU_CHECK_DEPS` 里）：
  - 断言 `QT_MEDIA_BACKEND=ffmpeg` 生效、`ffmpegmediaplugin.dll` 随包、`QGraphicsVideoItem` 可实例化、用一个本地 2 秒测试视频（`packaging` 里已有的 WAV 生成逻辑扩展成"1 帧 mp4"或复用 ffmpeg 生成）能起播到 `LoadedMedia`。

**手工验收（发版前）**

| 场景 | 通过标准 |
|---|---|
| 1080p 剧集播放 10 分钟 | CPU ≤ 25%（硬解生效）、stall ≤ 2 次、无音画不同步 |
| 拖动进度条 10 次 | 每次恢复 < 2s，且不重复下载已缓存分片（诊断面板可见命中） |
| 弱网（用 `clumsy`/限速 2 Mbps） | 自动降到 720P 并继续播放，不出现"永久缓冲" |
| 下载一季 12 集（1080p） | 并发 2 时速度 ≥ 单线程的 1.8×；中途断网 30s 后能续传 |
| 换源/换清晰度 | 保留播放位置；失败自动换源 ≤ 2 次后给明确提示 |
| 弹幕开关 | 开启后有弹幕、关闭即消失；屏蔽词生效；导出/导入 xml 成功 |

### 3.6 风险与降级

| 风险 | 影响 | 降级 |
|---|---|---|
| 硬解在某显卡/驱动上花屏 | 播放异常 | 设置项一键关硬解；诊断面板提示；`QT_DISABLE_HW_TEXTURES_CONVERSION=1` 兜底 |
| `QGraphicsVideoItem` 渲染异常 | 黑屏 | `video/render_path=widget` 回退旧路径 |
| 分片缓存占满磁盘 | 磁盘告警 | 配额 + LRU + 设置页"清空缓存"；启动时校验配额 |
| 并发过高被源站限流/封 IP | 播放失败 | 并发默认保守（分片 8/代理 16/集间 2）+ 自动降并发（连续 3 次 429/403 时减半）+ 限速开关 |
| 弹幕接口变更 | 弹幕不可用 | 弹幕是"可选增强"：失败只提示，不影响播放；支持本地 xml |
| 新表迁移失败 | 启动异常 | 迁移用 `CREATE TABLE IF NOT EXISTS`（纯增量，可重复执行），并在 `tests/platform/test_db_schema.py` 加回归 |
| 旧测试被改动打破 | 回归 | 所有旧函数名/签名保留（转发壳），新增行为用新设置键默认值控制（默认=接近现状，仅优化类默认开启） |

---

## 4. 第七大板块「墨软动漫」方案

### 4.0 与第六大板块的边界（先说清楚，避免重复建设）

| 维度 | 墨软影视（第六） | 墨软动漫（第七） |
|---|---|---|
| 内容来源 | 苹果 CMS / 公开源（**结构化接口**） | **规则驱动的番剧站采集**（XPath / JSON API 规则） |
| 核心资产 | 多源聚合 + 下载入库 | **规则库 + 追番 + 弹幕**（+ Bangumi 元数据） |
| 换源语义 | 同一作品在多个 CMS 源之间换 | 同一番剧在**多条规则/多个线路**之间换 |
| 播放器 | 共用（改造后的 `ui_kit/media/player.py`） | 同左（**同一个实现**，不重复造） |
| 代理/缓存/ABR/下载器 | `services/media_proxy/`、`core/video/*` | **直接复用**（动漫不新写下载与代理） |
| 数据表 | `video_*` | `anime_*`（独立前缀，互不干扰） |
| 设置页 | 「影视」 | 「动漫」（新页） |
| 与对方的关系 | 不引用 | 不引用（复用只走 `ui_kit` / `services` / `core/platform`） |

> **架构闸门**：`tests/architecture/test_architecture.py` 规定"板块不得引用其他板块"。因此第七板块**不能** `from modu_workbench.boards.video import player`。解决办法是把播放器**上移到共享层**：
> - `ui_kit/media/player.py`（播放器窗口：视频面/控制条/快捷键/清晰度/倍速/进度）
> - `ui_kit/media/danmaku.py`（弹幕层与轨道算法）
> - `ui_kit/media/subtitle.py`（字幕层）
> - `ui_kit/media/proxy.py`（薄封装，转发到 `services/media_proxy`）
>
> 这是本方案的**前置改造**（第六板块的 V1/V3 会自然把它做出来）；动漫板块只做"接线 + 弹幕/追番业务"。

### 4.1 能力清单（对标 Kazumi，逐项给取舍）

Kazumi 的功能清单来自其 README 的"功能/开发计划"（附录 C）与我们实际需要：

| Kazumi 功能 | 我们 | 做法 / 理由 |
|---|---|---|
| 规则编辑器 | ✅ P1 | JSON 表单 + 选择器输入 + **试跑面板**（命中数/预览/报错定位） |
| 番剧目录 | ✅ P1 | 规则可选提供 `catalogue`（目录页）→ 采集条目入"番剧库" |
| 番剧搜索 | ✅ P0 | 多规则并发搜索 + 名称相似度合并（复用 `core/video/sources/matcher.py` 思路） |
| 番剧时间表 | ✅ P1 | 优先用 **Bangumi 放送时间**（开放 API），规则目录页为备选 |
| 番剧字幕 | ⚠️ P1 部分 | 外挂字幕（本地 srt/ass→vtt 转换后渲染）；内嵌字幕不做（Qt 无字幕轨 API） |
| 分集播放 | ✅ P0 | 线路 + 剧集选择，自动连播 |
| 视频播放器 | ✅ P0 | 复用第六板块改造后的播放器（`QGraphicsVideoItem` + 弹幕层） |
| 多视频源 | ✅ P0 | 多规则 / 单规则多线路 |
| 规则分享 | ✅ P0 | JSON 导入导出 + **订阅 URL**（对标 LunaTV 的订阅思路） |
| 硬件加速 | ✅ P0 | 复用第六板块的硬解开关 |
| 高刷适配 | ✅ P0 | 渲染层不设帧率上限；`MinimalViewportUpdate`；避免在主线程做网络/解析 |
| 追番列表 | ✅ P0 | `anime_favorites` + 一键追番 |
| 番剧弹幕 | ✅ P0（只读） | 弹弹play（附录 A）+ 本地 xml 导入导出；发送弹幕不做（受限接口未开放）。屏蔽用"关键词 + `/正则/`"（学 Kazumi），不做 LunaTV 那种服务端硬编码过滤 |
| 在线更新 / 番剧更新提醒 | ✅ P1 | 逐条比对追番的剧集列表（**Kazumi 自己还没做**，这是我们的差异化） |
| 历史记录 | ✅ P0 | `anime_history` + 续播 |
| 倍速播放 | ✅ P0 | 播放器已有（Kazumi 的倍速范围 0.25–3.0x、长按 1.25–3.0x，可作设置项参考） |
| 播放快捷键 | ✅ P0 | 现有：空格/←→/F/ESC；**补齐**：`N`/`P` 上下一集、`D` 弹幕开关、`S` 截图、`[`/`]` 调速（对齐 Kazumi 的快捷键习惯） |
| 下载默认值 | ✅ P0 | 分片并发默认 6（范围 2–16）、同时下载集数默认 2（范围 1–4）——与 Kazumi（3 / 2）和 LunaTV-EE（6 worker）同量级，见 3.2 |
| 配色方案 | ✅ P0 | 已有主题令牌 |
| 跨设备同步 | ⚠️ P2 | 先做"追番与进度导出/导入 JSON"；WebDAV 留作后续（Kazumi 用 WebDAV，我们不做账号体系） |
| 无线投屏 DLNA | ❌ | 需要 UPnP/DLNA 协议栈（新依赖 + 大量兼容性坑），与本项目定位不符；改为"外部播放器打开地址" |
| 外部播放器播放 | ✅ P1 | `QDesktopServices.openUrl` / `os.startfile` 把真实地址交给系统播放器（也解决反盗链拿不到的少数源） |
| 超分辨率 | ⚠️ 离线 | 见 4.8：**不做实时 Anime4K**，提供离线 ffmpeg 增强 |
| 一起看（SyncPlay） | ❌ | 需要公共服务器与协议实现，单机工具不划算 |
| 番剧下载 | ✅ P0 | 复用 `core/video/downloader.py`（第六板块 V2 提速后直接受益） |
| Bangumi 元数据 | ✅ P1 | 封面/简介/评分/放送时间（开放 API，只读、缓存） |
| 以图搜番（trace.moe） | ⚠️ P2 | 需要把用户截图上传第三方；默认关闭，作为高级功能 |

### 4.2 规则引擎（本板块的核心，也是最需要写清楚的）

#### 4.2.1 规则文件与存放

| 项 | 约定 |
|---|---|
| 格式 | 单个 JSON 文件；**字段名与 Kazumi 规则一一对应**（便于直接吃 [KazumiRules](https://github.com/Predidit/KazumiRules) 仓库的规则） |
| 内置规则 | `src/modu_workbench/core/anime/rules_builtin/*.json`（随包，只读） |
| 用户规则 | `数据目录/anime/rules/*.json`（可编辑、可删除） |
| 订阅规则 | `数据目录/anime/rules/_subscribed/<订阅名>/<规则名>.json`（只读，带订阅来源与校验和） |
| 索引与状态 | DB 表 `anime_rules`（启用状态、最近测试结果、来源、版本） |
| 编码 | UTF-8（无 BOM），换行 LF；导入时容错 BOM 与 CRLF |

#### 4.2.2 规则字段（兼容 Kazumi + 我们的扩展）

```jsonc
{
  "api": "8",                       // 兼容字段：规则 API 等级（我们 = 8）
  "type": "anime",                  // anime | comic | novel（本期只实现 anime）
  "name": "示例番剧站",
  "version": "1.0",
  "baseURL": "https://example.com/",
  "referer": "",                    // 可选：覆盖播放/下载请求的 Referer
  "userAgent": "",                  // 可选：覆盖 UA
  "searchMode": "xpath",            // xpath | api
  "chapterMode": "xpath",           // xpath | api

  // ---- XPath 模式字段（Kazumi 同名）----
  "searchURL": "https://example.com/search/-------------.html?wd=@keyword",
  "searchList": "//div[@class='module-items']//div[@class='module-item']",
  "searchName": "//a[@class='module-item-title']/@title",
  "searchResult": "//a[@class='module-item-cover']/@href",
  "chapterRoads": "//div[@class='module-play-list']",
  "chapterResult": "//a/@href",

  // ---- API 模式字段（Kazumi 同名）----
  "searchApiConfig": {
    "request": { "method": "GET", "url": "https://example.com/api/search",
                 "headers": {}, "query": { "q": "@keyword", "pageSize": 20 }, "body": null },
    "listPath": "$.data.videos[*]", "namePath": "$.name", "sourcePath": "$.id"
  },
  "chapterApiConfig": {
    "request": { "method": "GET", "url": "https://example.com/api/detail/@source" },
    "format": "nested",             // nested | flat
    "roadsPath": "$.data.playSources[*]", "roadNamePath": "$.name",
    "episodesPath": "$.episodes[*]", "episodeNamePath": "$.name", "episodeUrlPath": "",
    "variables": { "slug": "$.data.slug" },
    "episodePage": { "url": "https://example.com/video/@slug/play",
                     "query": { "source": "@roadIndex", "episode": "@episodeIndex" } }
  },

  // ---- 高级选项（Kazumi 同名 + 我们的扩展）----
  "usePost": false,                 // 搜索用 POST（表单字段写在 searchURL 的查询串里）
  "useLegacyParser": false,         // 简易解析（容错解析器，优先 lxml.html，失败退 html.parser）
  "adBlocker": false,               // HLS 广告分片过滤（对应 3.3.7）
  "muliSources": true,              // 兼容字段（Kazumi 的拼写）：本规则支持多源
  "useNativePlayer": true,          // 兼容字段：忽略（我们只有内置播放器）
  "useWebview": true,               // 兼容字段：忽略；为 true 时在"规则详情"里提示"该规则部分源依赖网页渲染，可能无法解析"
  "antiCrawlerConfig": { "enabled": false },   // 兼容字段：我们不做自动过验证，见下

  // ---- 我们的扩展 ----
  "catalogue": {                    // 可选：目录页（番剧库/时间表用）
    "url": "https://example.com/anime/",
    "list": "//div[@class='module-item']", "name": "//a/@title",
    "link": "//a/@href", "cover": "//img/@data-src", "update": "//span[@class='module-item-note']/text()"
  },
  "schedule": { "url": "https://example.com/schedule/", "list": "...", "day": "...", "items": "..." },
  "episodeParse": "(?:第|EP|E)(\\d{1,4})",   // 可选：集数正则（默认内置多套正则）
  "timeout": 20, "retries": 2,      // 网络参数覆盖
  "rateLimit": { "qps": 2 },        // 礼貌限速（默认 2 请求/秒/规则）
  "notes": "自用规则，勿外传"
}
```

**变量替换（与 Kazumi 一致）**

| 变量 | 含义 | 可用位置 |
|---|---|---|
| `@keyword` | 用户输入的关键词（URL 编码） | 搜索 URL/query |
| `@source` | 搜索结果里的 `sourcePath` 值（作品内部 ID/URL） | 选集 URL/query |
| `@roadIndex` | 线路序号（**从 0 开始**） | 播放页模板/query |
| `@episodeIndex` | 剧集序号（**从 0 开始**） | 播放页模板/query |
| `@roadNumber` / `@episodeNumber` | 线路/剧集序号（**从 1 开始**） | 播放页模板/query |
| `<响应变量名>` | `chapterApiConfig.variables` 里保存的值（如 `@slug`） | 播放页模板/query |

**选择器子集**

| 类型 | 支持 | 说明 |
|---|---|---|
| XPath | 全部 XPath 1.0（`lxml`），**兼容** Kazumi 的"必须以 `//` 开头"约定；同时允许 `@attr` 取属性、`text()` 取文本 | Kazumi 只支持以 `//` 开头的选择器（其文档明示），我们更宽松但保持兼容 |
| CSS 选择器 | 支持（`soupsieve`，`bs4`/`lxml` 都可） | 我们的扩展：写 `css:div.item > a` 即按 CSS 解析 |
| JSONPath 子集 | `$`、`$.a.b`、`$.a[*]`、`$.a[0]`、`$['a-b']` | 与 Kazumi 相同：**不支持**递归 `..`、过滤 `[?(...)]`、切片 `[0:2]`、函数 `length()` |
| 集数解析 | 内置多套正则（`第N集/话`、`EPn`、`En`、`【n】`、`n.`）→ 归一为整数 | 用于弹幕匹配、进度、更新比对 |

**反爬与验证码（诚实边界）**

- `antiCrawlerConfig` 我们**只读不执行**：当检测到验证页（响应含 `captcha`/`验证`/`cloudflare` 特征）时，给出明确提示"该规则需要人工通过站点验证，请换规则或在浏览器中打开该站"；
- 不做自动打码、不做浏览器内核（**与 Qt 单栈冲突**），也不内置任何绕过手段。

**版本门控与"能兼容到什么程度"（必须提前说清）**

| 项 | 我们怎么做 |
|---|---|
| 规则 API level | 规则里的 `api` 是"所需能力等级"（Kazumi 客户端当前 `apiLevel = 8`）。我们同样声明 `SUPPORTED_API_LEVEL = 8`：`api > 8` 的规则**加载但灰显**并注明"该规则需要更新的客户端"，**不静默失败** |
| 退役规则 | 兼容规则里的顶层 `deprecated`（KazumiRules 仓库用 CI 脚本自动打这个标记）：显示为"已退役"并默认不参与搜索，但**保留文件**不删除（用户可以手动改回） |
| 目录/订阅清单 | 兼容 KazumiRules 的 `index.json` 结构（`name` / `version` / `useNativePlayer` / `antiCrawlerEnabled` / `author` / `lastUpdate`），支持从**目录清单**批量安装与更新 |
| `useWebview` / `useNativePlayer` / `muliSources` | **只读兼容**：`useWebview=true` 时在规则详情里明确提示"该规则部分源依赖网页 JS 渲染，我们无浏览器内核，可能解析失败" |
| **Kazumi 规则能直接用吗** | **分两类**：① **API 模式规则**（`searchMode/chapterMode = api`）——结构完全一致，**可直接导入使用**（只要站点接口还在）；② **XPath 模式规则**——只在其播放地址**能被静态嗅探**（苹果 CMS `player_aaaa` / JSON / iframe 等）时可用；依赖 WebView 执行 JS 的站点**不支持**。Kazumi 维护者在 issue #914 里也说明：iframe 内的 referer 校验与 JS 挑战"基本无法解决"，必须依赖注入脚本——这正是我们没有的能力，所以**不要承诺"Kazumi 规则全兼容"** |

#### 4.2.3 执行引擎（`core/anime/`）

```
core/anime/
├── __init__.py      门面导出（AnimeLibrary / AnimeRule / DANMAKU_* / describe() …）
├── models.py        AnimeRule / AnimeSeries / AnimeEpisode / Road / DanmakuItem / Progress
├── selectors.py     XPath / CSS / JSONPath 三种求值器（统一 first()/all()/text()/attr() 接口）
├── rules.py         规则解析、校验、变量替换、导入导出、订阅校验和
├── engine.py        规则执行：搜索 → 详情 → 线路/剧集 → 播放地址（含嗅探）
├── sniffer.py       播放页静态嗅探（见下）
├── providers.py     把规则包装成"源"，接口与 core/video 的 VideoSource 对齐（search/detail/resolve）
├── library.py       业务编排（搜索/追番/进度/下载/换源/更新检查）
├── storage.py       anime_* 表 CRUD
├── danmaku.py       弹弹play 客户端 + XML 解析/生成 + 匹配（标题+集数）
├── bangumi.py       Bangumi 元数据（封面/简介/评分/放送时间），只读 + 缓存
├── update_check.py  追番更新检查（并发 2、可取消、写回 total/latest）
└── tasks.py         后台任务封装（QThread 之外的纯 Python 任务，UI 侧用 QThread 包一层）
```

**播放地址解析（三步走，从便宜到昂贵）**

1. **规则直接给**：`episodeUrlPath`（API 模式）或 `chapterResult` 命中的 `<a>` 的 `href` 本身是媒体地址（`looks_like_media_url()`）→ 直接用；
2. **播放页嗅探**：拿到播放页 URL → `sniffer.py` 抓 HTML/JSON 并提取真实地址（见下）；
3. **播放页构造 + 嗅探**：API 模式用 `episodePage` 模板 + 变量构造播放页 URL → 嗅探。

**嗅探器（`sniffer.py`）—— 无浏览器场景的关键**

| 策略 | 说明 | 覆盖的真实站点模式 |
|---|---|---|
| 苹果 CMS `player_aaaa` | 页面内 `<script>var player_aaaa={...}</script>`，取其中的 `url` / `link_next` / `encrypt` | **绝大多数采集站/番剧站（苹果 CMS v10）** |
| JSON 字段扫描 | 在 HTML 中找 `m3u8`/`mp4` 字面量，或在返回的 JSON 里递归找以媒体后缀结尾的字符串 | 各种自研播放器 |
| iframe 递归 | 抓 `<iframe src>`（最多 3 层），在子页面继续同样策略 | 播放页套播放器页 |
| JS 变量/函数 | `url:`、`source:`、`videoUrl`、`player.xxx(`、`atob('...')`、`unescape('...')`、`\uXXXX` 解码 | 常见 JS 播放器初始化 |
| `/player/` 接口 | 若页面里出现 `player/?id=xxx`（或类似）形态的 iframe/URL，直接请求该接口并按 JSON 解析 | 苹果 CMS 播放器接口 |
| 用户手动兜底 | 嗅探失败时给"在浏览器中打开该页 → 复制 m3u8 地址 → 粘贴播放"（`ui_kit` 已有的输入对话框） | 强 JS 渲染站点 |

> **明确不做**：不内置浏览器内核、不执行页面 JS（与"Qt 单栈、不引入 QtWebEngine"冲突）。因此**纯 JS 渲染型站点无法支持**——这一点必须写进 `docs/BOARDS/anime.md` 的"能力边界"，而不是让用户以为"规则写对了就一定能看"。

#### 4.2.4 规则导入 / 导出 / 订阅

| 能力 | 做法 |
|---|---|
| 导入 | 文件（多选）、剪贴板、URL（单个 JSON、JSON 数组、KazumiRules 仓库的目录清单） |
| 校验 | 解析 → 字段必填校验 → 选择器语法校验 → 记录到 `anime_rules.test_result`（"未测试/通过（命中 N 条）/失败（原因）"） |
| 导出 | 单条/多条导出为 JSON（可选"打包成订阅文件"：`{"version":1,"rules":[...]}`） |
| 订阅 | `anime/rule_subscriptions`（JSON 数组：`{name,url,enabled,last_checked,etag,hash}`）；手动"立即更新" + 可选的"启动时更新"（默认关）；更新时**逐条校验**，失败的规则保留旧版本并提示 |
| 分发体 | 订阅内容就是 JSON（不引入 base58/压缩等额外编码；LunaTV 用 base58 是因为要贴到配置文件里，我们直接给 URL/文件更直观） |

### 4.3 数据模型（迁移 `v3_anime`，纯增量）

```sql
-- 规则索引（文件是唯一真相，这里存状态与索引）
CREATE TABLE IF NOT EXISTS anime_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT NOT NULL UNIQUE,       -- 文件路径的稳定标识（用户规则=文件名，内置=builtin:name）
    name        TEXT NOT NULL DEFAULT '',
    version     TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'xpath',   -- xpath | api
    path        TEXT NOT NULL DEFAULT '',
    builtin     INTEGER NOT NULL DEFAULT 0,
    enabled     INTEGER NOT NULL DEFAULT 1,
    source_url  TEXT NOT NULL DEFAULT '',   -- 订阅来源
    test_state  TEXT NOT NULL DEFAULT '',   -- '' | ok | fail
    test_note   TEXT NOT NULL DEFAULT '',
    updated_at  INTEGER NOT NULL DEFAULT 0
);

-- 番剧库（元数据缓存 + 每规则一份的来源记录）
CREATE TABLE IF NOT EXISTS anime_series (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_key       TEXT NOT NULL DEFAULT '',
    remote_id      TEXT NOT NULL DEFAULT '',   -- 规则里的作品标识（sourcePath 值或详情 URL）
    title          TEXT NOT NULL DEFAULT '',
    title_norm     TEXT NOT NULL DEFAULT '',   -- 归一化标题（匹配用）
    cover          TEXT NOT NULL DEFAULT '',
    year           TEXT NOT NULL DEFAULT '',
    summary        TEXT NOT NULL DEFAULT '',
    bangumi_id     INTEGER NOT NULL DEFAULT 0,
    total_episodes INTEGER NOT NULL DEFAULT 0,
    latest_episode TEXT NOT NULL DEFAULT '',
    air_weekday    INTEGER NOT NULL DEFAULT -1,  -- 0=周一 … 6=周日，-1 未知
    created_at     INTEGER NOT NULL DEFAULT 0,
    updated_at     INTEGER NOT NULL DEFAULT 0,
    UNIQUE(rule_key, remote_id)
);

-- 追番
CREATE TABLE IF NOT EXISTS anime_favorites (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id  INTEGER NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL DEFAULT 0,
    UNIQUE(series_id)
);

-- 观看进度（按集）
CREATE TABLE IF NOT EXISTS anime_progress (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id     INTEGER NOT NULL,
    episode_key   TEXT NOT NULL DEFAULT '',   -- 线路+集数的稳定标识
    episode_name  TEXT NOT NULL DEFAULT '',
    episode_index INTEGER NOT NULL DEFAULT 0,
    position_ms   INTEGER NOT NULL DEFAULT 0,
    duration_ms   INTEGER NOT NULL DEFAULT 0,
    watched       INTEGER NOT NULL DEFAULT 0,
    updated_at    INTEGER NOT NULL DEFAULT 0,
    UNIQUE(series_id, episode_key)
);

-- 播放历史
CREATE TABLE IF NOT EXISTS anime_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id    INTEGER NOT NULL DEFAULT 0,
    rule_key     TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL DEFAULT '',
    episode_name TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT '',
    played_at    INTEGER NOT NULL DEFAULT 0
);

-- 弹幕缓存索引（文件在 数据目录/anime/danmaku/）
CREATE TABLE IF NOT EXISTS anime_danmaku (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id   INTEGER NOT NULL DEFAULT 0,
    episode_key TEXT NOT NULL DEFAULT '',
    provider    TEXT NOT NULL DEFAULT 'dandanplay',
    episode_id  INTEGER NOT NULL DEFAULT 0,
    count       INTEGER NOT NULL DEFAULT 0,
    path        TEXT NOT NULL DEFAULT '',
    fetched_at  INTEGER NOT NULL DEFAULT 0,
    UNIQUE(series_id, episode_key, provider)
);

CREATE INDEX IF NOT EXISTS idx_anime_series_rule ON anime_series(rule_key, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_anime_history_time ON anime_history(played_at DESC);
CREATE INDEX IF NOT EXISTS idx_anime_progress_time ON anime_progress(updated_at DESC);
```

> 下载任务直接复用第六板块 V2 新增的 `video_download_tasks`（动漫与影视共用同一张表与下载器，`video_id` 语义扩展为"任意条目 id + kind"，实现时用 `kind` 字段区分；若担心耦合，则在 `v3_anime` 里加同构的 `anime_download_tasks`——**推荐后者**，两个板块的数据互不干扰，符合"板块最小化"原则）。

**登记点（漏一个测试就会红）**：`tests/platform/test_db_schema.py`（表清单 + 前缀 + 索引不重名）、`docs/DATABASE.md`、`core/platform/migrations/__init__.py`（`MIGRATIONS = (v1_initial, v2_document, v3_anime)`）。

### 4.4 模块与文件清单（含架构登记点）

**新增（`core/anime/`）**：见 4.2.3 的目录树（11 个文件，预计 2 600–3 200 行）。

**新增（`boards/anime/`）**

| 文件 | 职责 | 预计 |
|---|---|---|
| `__init__.py` | 空导出（与其他板块一致） | 3 行 |
| `context.py` | 板块单例（storage / library / 规则管理器） | 60 行 |
| `board.py` | 外壳：5 个 nav 按钮 + `TaskBar` + 设置入口 | 600 行 |
| `search.py` | 搜索页（多规则并发、边搜边出、追番/下载/播放入口） | 420 行 |
| `detail.py` | 番剧详情对话框（元数据 + 线路/剧集 + 批量下载 + 追番） | 380 行 |
| `library_page.py` | 番剧库/追番（含"有新集"角标、继续观看） | 420 行 |
| `schedule.py` | 时间表（Bangumi 优先，规则备选） | 260 行 |
| `rules_page.py` | 规则管理（列表/启停/导入/导出/订阅/试跑编辑器） | 520 行 |
| `history.py` | 播放历史 | 220 行 |
| `widgets.py` | 公共控件（海报卡片、线路选择器、追番按钮、集数标签） | 380 行 |

**共享层改造（第六板块 V1/V3 产出，动漫直接复用）**

| 文件 | 说明 |
|---|---|
| `ui_kit/media/player.py` | 从 `boards/video/player.py` 抽出（影视板块改为薄封装调用） |
| `ui_kit/media/danmaku.py` | 弹幕解析/轨道分配/渲染层 |
| `ui_kit/media/subtitle.py` | 字幕解析/渲染层 |
| `services/media_proxy/*` | 代理/缓存/预取（两个板块共用） |

**登记点（照第六板块的"接入清单"逐项做）**

| 位置 | 动作 |
|---|---|
| `app/registry.py` | 新增 `ANIME_BOARD`，追加到 `ACTIVE_BOARDS` **末尾**（不改既有顺序） |
| `app/context.py` | 暴露 `anime_library()` 等单例 |
| `core/platform/paths.py` | 新增 `anime_dir()`（数据目录/动漫） |
| `core/platform/migrations/` | `v3_anime.py` + 注册 |
| `ui_kit/settings/anime.py` + `ui_kit/settings/__init__.py` | 新设置页并入清单 |
| `tests/architecture/test_architecture.py` | 新增板块 key（并断言"动漫不得引用影视"） |
| `tests/smoke/test_cross_board_link.py` | 设置页清单加「动漫」 |
| `workbench.spec` / `workbench_mac.spec` | `core.anime` / `boards.anime` 的 hiddenimports（两个 spec 必须一致） |
| `packaging/build_app.ps1` | 模块核验清单加动漫模块 |
| `app/main.py` | 自检项 `anime_core`（见 4.9） |
| `docs/BOARDS/anime.md`、`docs/ARCHITECTURE.md`、`docs/DATABASE.md`、`docs/TESTING.md`、`README.md`、`CHANGELOG.md` | 文档同步 |

### 4.5 界面设计

**子页（nav 按钮顺序）**：`🔍 搜索` · `📺 番剧库` · `🗓 时间表` · `🧩 规则` · `🕘 历史`

**搜索页**

- 顶部：关键词输入 + "搜索（全部规则）" + 规则过滤下拉 + "只搜已启用规则"；
- 结果：**边搜边出**（每完成一条规则就插入一批），每条显示"来源规则 + 标题 + 封面 + 年份 + 更新信息"，同一作品多规则命中时**合并成一张卡**（展开可选具体来源）；
- 卡片操作：播放（默认第一条线路第一集）、详情、追番、下载（单集/整季）。

**详情对话框**

- 上半：封面/标题/年份/评分/简介（Bangumi 元数据，缺失时用规则数据）；
- 中部：线路下拉（多规则 = 多组线路）+ 剧集网格（已看过的集数打勾、正在看的加进度条）；
- 下部：`追番` / `下载整季` / `在浏览器打开` / `复制播放地址`；
- 右键剧集：`用外部播放器打开` / `重新解析地址`（换线路）。

**玩家（复用改造后的播放器）**

- 新增：弹幕开关/透明度/速度/屏蔽词（设置页）、`F12` 诊断、自动连播；
- 动漫特有的"下一集"逻辑：优先同线路下一集 → 无则换线路第一集 → 无则换规则。

**规则页**

- 列表：名称 / 版本 / 类型（XPath/API）/ 启用 / 最近测试结果 / 来源（内置/用户/订阅）；
- 操作：导入（文件/剪贴板/URL）、导出、编辑、删除、试跑、订阅管理；
- **试跑面板**：输入关键词 → 依次显示「搜索 URL → 命中 N 条 → 第一条作品名/链接 → 详情页命中线路数/集数 → 第一集解析结果（含嗅探命中策略）」；任一步失败给出**具体原因**（选择器返回 0 条 / HTTP 403 / JSONPath 未命中 / 无媒体地址）。

### 4.6 播放与弹幕

**弹幕数据源**：弹弹play 开放弹幕网络（附录 A）。要点：

- 需要 **AppId/AppSecret**（在 dev.dandanplay.com 申请）→ 设置页提供填写（存 `QSettings`，与其它密钥一致的做法；**不进代码库**）；
- 鉴权：**签名模式**（推荐，桌面端）：`X-AppId` / `X-Timestamp` / `X-Signature = base64(sha256(AppId + Timestamp + Path + AppSecret))`，Path 不含域名与查询串（如 `/api/v2/comment/123450001`）；
- 匹配（**走官方文档路线，不抄野路子**）：`/api/v2/search`（按标题搜番剧）→ 选中条目取其分集 → `episodeId` → `/api/v2/comment/{episodeId}?withRelated=true` 取弹幕。
  > Kazumi 用的是"Bangumi ID → 弹弹 bangumiId → `episodeId = bangumiId + 4 位左补零集数`"这条**未公开约定**（其源码注释自述 undocumented）。我们**不采用**：官方接口才是稳定路径，并额外提供"手动指定 episodeId / 导入本地 xml"兜底；
- **必须缓存**（官方明确建议 2–6 小时，热门更新日 0.5–1 小时，老番 2–7 天）：我们按"番剧 id + 集 + 天"缓存到磁盘并在 DB 记索引，避免触发限流；
- 合规：**只读**（发送弹幕属受限接口，官方未开放）；不做批量抓取；弹幕功能免费（我们本来就免费）。

**本地弹幕**：导入/导出 `.xml`（主流弹幕格式，字段 `time/p`/`text`），支持"手动匹配"（用户指定 episodeId 或直接导入文件）。

**渲染**（`ui_kit/media/danmaku.py`）

- 数据：`[(time_ms, mode, font_size, color, text)]`，只取滚动/顶部/底部三种基本模式；默认参数参考 Kazumi：**持续 8 秒、不透明度 100%、弹幕区域 100%、字号 25、描边 1.5、顶部开/滚动开/底部关**（都做成设置项）；
- 轨道分配：按字号把屏幕高度切成轨道，每条轨道维护"上一条弹幕的离开时间"，插入时选最早可用的轨道；
- **性能分级（借用 LunaTV-EE 的公式，可移植）**：`score = min(cores/4,1)*0.5 + min(mem_GB/1,1)*0.3 + 0.5*0.2`（桌面恒为 0.5 那项）→ `>0.7` 高 / `>0.4` 中 / 其他 低；三档**同屏上限 60 / 40 / 25**、**超出丢弃率 0.1 / 0.3 / 0.5**（无 Worker 可用，这是我们与浏览器的差异：我们直接在 Qt 主线程做轻量池化渲染，因此**同屏上限更保守**）；
- 池化：`QGraphicsSimpleTextItem` 复用（避免频繁 new/GC 掉帧）；视频尺寸变化只重算轨道边界；
- 屏蔽（学 Kazumi）：关键词列表 + **`/正则/` 语法**（以 `/` 开头结尾视作正则）+ 按类型（滚动/顶/底）+ 同屏密度上限；
- 去重（学 Kazumi 的归一化）：全角转半角 → 去空白与标点 → 连续 3 个以上相同字符合并为 3 个 → 分组后合并显示为 `原文 xN`；**默认关闭**（合并会改变观感）；
- 性能预算：单帧渲染 ≤ 2ms（500 条同屏数据，实际只渲染上限内的条数）。

### 4.7 追番与更新提醒

| 能力 | 做法 |
|---|---|
| 追番 | `anime_favorites`；搜索/详情/播放页一键追番；番剧库页按"最近更新/我追的/在看的"分组 |
| 进度 | 播放器每 5 秒上报 `position_ms`（复用影视板块的进度定时器），退出/切集时立即写回；剧集网格显示进度条 |
| 更新检查 | 对追番列表逐条调规则的详情接口取剧集列表，比较 `total_episodes` 与末集名；**并发 2、可取消、失败不影响其他条目**；结果写 `anime_series.latest_episode` 并打"新集"标记 |
| 展示 | nav 按钮上的 chip（"3 部有新集"）+ 番剧库页角标；点击后只显示有更新的条目 |
| 触发 | 手动按钮（默认）+ 可选"启动后延迟 30 秒自动检查一次"（默认关，避免启动慢/打扰） |
| 不做 | 系统托盘/气泡通知（本项目没有托盘体系）、后台常驻轮询 |
| 为什么不照抄 WebDAV | Kazumi 的跨设备同步是**缺陷最密集**的功能（issue #861 恢复错集数、#1280 追番同步一直失败、#2341 "显示成功但未同步"、#2233 全屏切换触发同步把 CPU 打满）；其 Bangumi 域名还长期被阻断（#2133，官方承认"启用镜像后无法同步属预期"）。因此我们 P2 先做**显式导出/导入 JSON**（用户看得见、可校验），WebDAV 只有在有明确需求时才评估 |

### 4.8 超分辨率（诚实边界）

| 方案 | 结论 |
|---|---|
| 实时超分（Anime4K / mpv 着色器） | ❌ 不做。需要 libmpv 播放内核 + 着色器管线，与"Qt 单栈（QtMultimedia）"决策冲突；Kazumi 自己的 Q&A 也说明"质量档对 GPU 要求高、低端机会卡" |
| 离线增强 | ✅ 做（P2）：复用转换引擎的 ffmpeg，提供两个档位：① 放大（`scale=1920:1080:flags=lanczos`，可选 `unsharp` 轻锐化）；② 重编码（`libx264 -crf 20 -preset medium`）。入口在"下载完成后处理"，并**明确标注"这是离线重编码，不是 Anime4K 实时超分"** |
| 播放中 4K 升频 | ❌ 不做（同理） |

### 4.9 分阶段实施计划（可直接排期）

| 阶段 | 内容 | 交付物 | 验收 |
|---|---|---|---|
| **A. 前置（第六板块 V1/V2/V3 的共享层）** | 代理重写、缓存/预取、硬解、`QGraphicsVideoItem` 播放器上移到 `ui_kit/media/`、弹幕层、下载器并发 | 第六板块改造完成 + 4 个共享模块 | 见 3.5 的测试与手工验收；`pytest tests/board_video tests/architecture` 全绿 |
| **B. 动漫 P0（MVP，可看）** | 规则引擎（XPath + API）、选择器、嗅探器、搜索/详情/播放、追番 + 进度、下载（复用）、弹幕（只读） | `core/anime/*`、`boards/anime/*`、迁移 v3_anime、设置页 | 用 1 条内置规则 + 本地假站点 fixture 跑通"搜索→详情→选集→嗅探→播放（本地样本）→下载→弹幕渲染"；`pytest tests/board_anime -q` 全绿 |
| **C. 动漫 P1（好用）** | 规则编辑器 + 试跑、规则订阅、番剧库/时间表、Bangumi 元数据、更新检查、字幕、外部播放器、图片代理 | 规则页 + 时间表 + 元数据 + 更新角标 | 手工：导入 1 条自写规则 → 试跑通过 → 追番 → 播放 → 有新集提示；规则订阅更新不破坏旧规则 |
| **D. 动漫 P2（可选）** | 追番进度导出/导入（+WebDAV）、离线增强、广告分片跳过、以图搜番 | 导出 JSON、增强任务 | 手工：导出的追番/进度能在另一台机器导入还原 |
| **E. 收尾** | 打包（spec + build 脚本 + 自检 `anime_core`）、文档（`docs/BOARDS/anime.md` 等）、发版 | v1.1.0 产物（onedir + Setup） | 三道自检全绿；安装包静默安装→自检→卸载闭环（照 v1.0.5 的做法） |

**依赖关系**：B 依赖 A（播放器/代理/下载器）；C 依赖 B；D 可选；E 依赖 B/C。

### 4.10 测试与打包清单

**`tests/board_anime/`（新增，预计 9 个文件 / 180+ 用例）**

| 文件 | 覆盖 |
|---|---|
| `test_anime_rules.py` | 规则解析/校验/变量替换/导入导出/订阅校验和/坏规则不致命/Kazumi 规则兼容（含 `muliSources` 等杂字段） |
| `test_anime_selectors.py` | XPath（含 `@attr`/`text()`/`//` 兼容）、CSS（`css:` 前缀）、JSONPath 子集（`[*]`/`[0]`/`['k']`）、不支持的语法给出明确错误 |
| `test_anime_engine.py` | 搜索/详情/选集/播放页构造（用本地 HTML/JSON fixture 起 `http.server`）、POST 搜索、超时重试、限速 |
| `test_anime_sniffer.py` | `player_aaaa`、iframe 递归、JSON 扫描、base64/unicode 解码、失败时的可读提示 |
| `test_anime_danmaku.py` | 弹弹play 客户端（替身）：签名算法正确、缓存命中、限流退避、xml 解析/导出、标题+集数匹配、轨道分配不重叠 |
| `test_anime_library.py` | 追番/进度/历史/更新检查（复现"总集数变多"）、换线路/换规则、下载入库（复用假下载器） |
| `test_anime_storage.py` | `anime_*` 表 CRUD、唯一约束、级联清理 |
| `test_anime_ui.py` | 板块构建、五个子页、搜索边出边显示、详情、规则页试跑（替身引擎）、播放器接线、快捷键 |
| `test_anime_acceptance.py` | 「需求 7.x 验收」端到端：规则导入 → 搜索 → 追番 → 播放 → 弹幕 → 下载 → 更新提示 |

**打包与自检**

- `workbench.spec` / `workbench_mac.spec`：hiddenimports 加 `modu_workbench.core.anime.*`、`modu_workbench.boards.anime.*`、`modu_workbench.ui_kit.media.*`（两个 spec 必须一致——`test_spec_integrity.py` 会拦）；
- `packaging/build_app.ps1`：模块核验清单加动漫模块（现 28 项 → 约 36 项）；
- `app/main.py`：新增自检项 `anime_core`（16 → 17 项）：用**内置规则 + 内置假站点 HTML**跑通"解析规则 → 选择器命中 → 嗅探出 m3u8"，并断言弹幕 xml 解析、追番/进度读写；
- `docs/TESTING.md`：加动漫板块冒烟项（规则导入 → 搜索 → 播放 → 追番 → 更新检查）。

---

## 5. 里程碑与任务清单（可勾选）

### 5.1 第六大板块（影视改造）

- [ ] **V1-a** 媒体代理：`services/media_proxy/{server,upstream,cache,playlist}.py` + `media_server.py` 兼容壳
- [ ] **V1-b** 分片磁盘缓存（LRU + 配额 + Range 索引）+ 设置页「缓存」组
- [ ] **V1-c** 连接池/流式转发/分层超时与重试/并发上限
- [ ] **V1-d** `PlaybackSession`（warmup + 预取 + stall 看门狗）
- [ ] **V1-e** 硬解环境变量注入（`app/main.py`）+ 设置项 + 自动降级提示
- [ ] **V1-f** `QGraphicsView + QGraphicsVideoItem` 渲染路径（含 `QVideoWidget` 回退开关）
- [ ] **V1-g** `core/video/abr.py`（带宽估算 + 档位选择 + 降档/升档）
- [ ] **V1-h** 起播加速与自愈（3s/8s/20s 三段策略，position 前进判定）
- [ ] **V2-a** `core/video/segment_downloader.py`（并发 + 续传 + 重试/坏片 + 限速 + 合并）
- [ ] **V2-b** ffmpeg 路径：直连优先 + `-rw_timeout/-reconnect*/-http_persistent` 探测注入
- [ ] **V2-c** `download_many` 并发化 + 暂停/继续 + 失败重试队列 + 速度/ETA
- [ ] **V3-a** 自动连播 + 播放队列
- [ ] **V3-b** 字幕层（外挂 srt/vtt）
- [ ] **V3-c** 弹幕层（`ui_kit/media/danmaku.py`）+ 影视板块接线（弹弹play 只读）
- [ ] **V3-d** 播放诊断面板（F12，含导出日志）
- [ ] **V3-e** 源测速与排序（`SourceHealth.latency_ms`）
- [ ] **V3-f** 图片代理（海报）
- [ ] **V3-g** 测试：3.5 全部用例 + `test_video_nonfunctional.py` 性能预算
- [ ] **V3-h** 自检 `video_playback`；文档/CHANGELOG 同步

### 5.2 第七大板块（动漫）

- [ ] **A-a** 共享层抽取：`ui_kit/media/{player,danmaku,subtitle,proxy}.py`（影视板块改为调用）
- [ ] **B-a** `core/anime/{models,rules,selectors}.py`（规则解析/校验/三种选择器）
- [ ] **B-b** `core/anime/{engine,sniffer,providers}.py`（执行/嗅探/源接口）
- [ ] **B-c** `core/anime/{storage,library}.py` + 迁移 `v3_anime` + 表登记
- [ ] **B-d** `core/anime/danmaku.py`（弹弹play 客户端 + xml + 匹配）
- [ ] **B-e** `boards/anime/*`（搜索/详情/番剧库/历史 + 设置页）
- [ ] **B-f** 内置规则 3–5 条（含 1 条 API 模式）+ 规则导入导出
- [ ] **B-g** 测试 `tests/board_anime/*` + 架构/DB/设置页登记
- [ ] **C-a** 规则编辑器 + 试跑面板
- [ ] **C-b** 规则订阅（拉取/校验/更新）
- [ ] **C-c** `bangumi.py` 元数据 + 时间表页
- [ ] **C-d** `update_check.py` + "有新集"角标
- [ ] **C-e** 字幕 + 外部播放器打开
- [ ] **D-a** 追番/进度导出导入（JSON，预留 WebDAV）
- [ ] **D-b** 离线增强（ffmpeg 放大/重编码）
- [ ] **D-c** 广告分片跳过（与 3.3.7 共用）
- [ ] **E-a** 打包（spec/build/自检 `anime_core`）+ 文档 + 发版 v1.1.0

---

## 6. 风险、合规与"不做"清单

### 6.1 风险

| 风险 | 说明 | 对策 |
|---|---|---|
| 源站反爬/封 IP | 并发过高、频繁请求会被限流 | 默认保守并发 + 每规则限速（默认 2 QPS）+ 失败退避 + "礼貌暂停"（连续 429 自动降并发） |
| 版权与合规 | 采集站内容 | 沿用既有 `compliance` 开关与提示：**仅个人学习**、不内置任何源（影视已有"空壳"姿态）、不提供分享/公开服务、缓存目录可一键清空（参考弹弹play 与 Kazumi 的合规要求） |
| 弹幕接口变更/额度 | 官方 2026-06-25 起分层额度与限流 | 严格缓存 + 只读 + 失败不影响播放；AppId 由用户自备 |
| 纯 JS 渲染站点无法解析 | 无浏览器内核 | 明确写进能力边界 + 提供"浏览器打开/粘贴地址/外部播放器"兜底 |
| 规则失效 | 站点改版 | 规则版本号 + 试跑 + 订阅更新 + 多规则合并展示（一条失效不影响其它） |
| 磁盘占用 | 分片缓存 + 下载 + 弹幕 | 缓存配额（默认 2 GB）+ LRU + 设置页显示占用与一键清理 |
| 播放器替换引入回归 | `QGraphicsVideoItem` 与 QSS/全屏交互差异 | 保留 `widget` 回退开关；完整 UI 测试 + 手工验收清单 |
| 迁移与既有数据 | 新增表 | 纯增量 `CREATE TABLE IF NOT EXISTS`；`test_db_schema.py` 守住 |

### 6.2 明确"不做"（避免半成品）

- ❌ 服务端转码/云转码（桌面单机，按需 remux 足够）
- ❌ 多用户、账号、鉴权、公网服务
- ❌ QtWebEngine / 内嵌浏览器 / 自动过验证码
- ❌ 实时 Anime4K 超分、DLNA 投屏、SyncPlay 一起看
- ❌ 发送弹幕（受限接口，官方未开放）
- ❌ 内置视频源/规则仓库镜像（只提供"导入/订阅"能力与少量示例规则）

---

## 7. 附录

### A. 弹弹play 开放弹幕网络（我们只用到的部分）

- 基础地址：`https://api.dandanplay.net`；API v2；需在 <https://dev.dandanplay.com> 申请 `AppId` / `AppSecret`。
- 鉴权（签名模式，桌面端推荐）：请求头 `X-AppId`、`X-Timestamp`（秒）、`X-Signature = base64(sha256(AppId + Timestamp + Path + AppSecret))`；`Path` 不含域名与查询串（例：`/api/v2/comment/123450001`）。凭证模式为 `X-AppId` + `X-AppSecret`。
- 用到的端点（实现时以 <https://api.dandanplay.net/swagger> 为准）：
  - 搜索番剧/节目：`/api/v2/search`
  - 获取弹幕：`/api/v2/comment/{episodeId}?withRelated=true`
  - （可选）文件识别：`/api/v2/match`（前 16MB 的 MD5）——用于"本地视频文件匹配弹幕"，P2
- 官方要求：**按需调用 + 缓存**（2–6 小时；热门更新日 0.5–1 小时；老番 2–7 天）、禁止批量抓取、禁止商用、弹幕对用户免费。
- 参考：[弹弹play 开放弹幕网络文档](https://doc.dandanplay.com/open/)

### B. 规则示例

**B1. XPath 规则（本站内 fixture 用，可离线测试）**

```json
{
  "api": "8", "type": "anime", "name": "本地样例站", "version": "1.0",
  "baseURL": "http://127.0.0.1:PORT/",
  "searchMode": "xpath", "chapterMode": "xpath",
  "searchURL": "http://127.0.0.1:PORT/search?wd=@keyword",
  "searchList": "//div[@class='module-items']//div[@class='module-item']",
  "searchName": "//a[@class='module-item-title']/@title",
  "searchResult": "//a[@class='module-item-cover']/@href",
  "chapterRoads": "//div[@class='module-play-list']",
  "chapterResult": "//a/@href"
}
```

**B2. API 规则（结构对齐 Kazumi 文档里的 TvTFun 示例）**

```json
{
  "api": "8", "type": "anime", "name": "TvTFun 风格", "version": "1.0",
  "baseURL": "https://www.example.com/",
  "searchMode": "api", "chapterMode": "api",
  "searchApiConfig": {
    "request": { "method": "GET", "url": "https://www.example.com/api/videos/search",
                 "query": { "q": "@keyword", "pageSize": 20 } },
    "listPath": "$.data.videos[*]", "namePath": "$.name", "sourcePath": "$.id"
  },
  "chapterApiConfig": {
    "request": { "method": "GET", "url": "https://www.example.com/api/videos/@source" },
    "format": "nested",
    "roadsPath": "$.data.playSources[*]", "roadNamePath": "$.name",
    "episodesPath": "$.episodes[*]", "episodeNamePath": "$.name", "episodeUrlPath": "",
    "variables": { "slug": "$.data.slug" },
    "episodePage": { "url": "https://www.example.com/video/@slug/play",
                     "query": { "source": "@roadIndex", "episode": "@episodeIndex" } }
  }
}
```

### C. 参考资料

**被对标/借鉴的项目**

- MoonTV（`samqin123/MoonTV`）：<https://github.com/samqin123/MoonTV> —— 多源聚合、`config.json`（`cache_time`/`api_site` 苹果 CMS V10/`custom_category`）、ArtPlayer + HLS.js、图片/豆瓣代理环境变量、智能去广告。
- LunaTV（`MoonTechLab/LunaTV`）：<https://github.com/MoonTechLab/LunaTV> —— 空壳 + 自配源、**配置订阅（base58）**、**流式搜索**、豆瓣/图片代理多档、Docker + Kvrocks/Redis/Upstash。
- **LunaTV-EE（`stazeng/LunaTV-EE`）**：<https://github.com/stazeng/LunaTV-EE> —— 本方案**数值来源最集中**的参考（缓冲参数矩阵、源测速打分、下载器保序写入、分片代理工程细节、两级缓存）。
- **MoonTVPlus（`mtvpls/MoonTVPlus`）**：<https://github.com/mtvpls/MoonTVPlus> —— **点播代理解析最贴近的参考**：`src/app/api/proxy/vod/m3u8/route.ts`、`src/app/api/proxy/vod/segment/route.ts`、`src/lib/server/ssrf.ts`、`src/lib/server/proxy-headers.ts`、`src/lib/config.ts`（`SourceConfig.proxyMode`）。另有 `brucemomo/Moon_TV` 等分支。
- Kazumi（`Predidit/Kazumi`）：<https://github.com/Predidit/Kazumi> —— Flutter 番剧采集客户端；**规则编辑器/规则分享**、番剧目录/搜索/时间表、追番、弹幕（弹弹play）、硬件加速、高刷适配、多源、外部播放器、下载；**"番剧更新提醒"未实现**（README 开发计划未勾选）。
  - XPath 规则开发文档：<https://kazumi.app/docs/rules/develop-rules>（字段：Name/Version/BaseURL/SearchURL/SearchList/SearchName/SearchResult/ChapterRoads/ChapterResult；`@keyword`；高级选项：简易解析、广告过滤、UA、Referer、反反爬虫）
  - API 规则开发文档：<https://kazumi.app/docs/rules/develop-api-rules>（JSONPath 子集、`@source`/`@roadIndex`/`@episodeIndex`/响应变量、完整 JSON 示例）
  - 视频嗅探原理（各平台 WebView 实现）：<https://kazumi.app/docs/architecture/video-parser>
  - 规则仓库（含 `index.json` 与 CI 自动维护 `deprecated`）：<https://github.com/Predidit/KazumiRules>
  - 关键 issue（我们对标时的"别踩同一个坑"依据）：
    - #914 —— 维护者说明 iframe 内 referer 校验/JS 挑战"基本无法解决"（解析架构的根本限制）
    - #739 / #591 —— 实时超分的 GPU 代价（低端机 100% 占用且卡顿）
    - #995 —— HDR/杜比视界受 Flutter Texture 限制不支持
    - #2133 —— Bangumi 四域名被阻断；官方承认"启用镜像后无法同步属预期行为"
    - #861 / #1280 / #2341 / #2233 —— WebDAV 同步的典型缺陷（错集数/同步失败/"显示成功但未同步"/同步把 CPU 打满）
  - 上述 issue 的核实清单见 `docs/research/kazumi_issue_mining_report.md`。
- 本项目内的支撑材料（本次调研产出，仅作证据保留，不参与打包、也不影响运行时）：
  - `docs/research/MoonTV-LunaTV-技术调研报告.md`（EE/MoonTVPlus 家族源码级事实 + **"宣传口径 vs. 代码事实"反证表** + 反模式清单；点播代理一节以 MoonTVPlus 为准）
  - `docs/research/kazumi_research_report.md`（Kazumi 9 节事实 + 可借鉴清单）
  - `docs/research/kazumi_issue_mining_report.md`（约 60 条 API 核实的 issue）
  - `docs/research/kazumi_extras_report_v2.md`（代理/防盗链注入、HLS 广告过滤与解析、下载子系统、平台差异）
  - `docs/research/kazumi_danmaku_timetable_report.md`（弹幕与时间表细节）

**Qt / 技术依据**

- Qt Multimedia 高级 FFmpeg 配置（硬解设备列表、`QT_FFMPEG_DECODING_HW_DEVICE_TYPES`、`QT_DISABLE_HW_TEXTURES_CONVERSION`、`QT_FFMPEG_DEBUG`、`QT_FFMPEG_PROTOCOL_WHITELIST`）：<https://doc.qt.io/qt-6/advanced-ffmpeg-configuration.html>
- 弹弹play 开放弹幕网络文档：<https://doc.dandanplay.com/open/>
- 本仓库既有文档：`docs/BOARDS/video.md`、`docs/BOARDS/document.md`（板块接入清单与"诚实边界"写法可照抄）

---

## 8. 一句话总结

第六大板块的问题**不在源，而在我们自己的代理/解码/选路/下载四条链路**：把代理做成"流式 + 连接复用 + 分片缓存 + 预取"、把解码交给硬解、把选路交给实测带宽、把下载改成"并发 + 续传 + 直连优先"，"卡"和"慢"这两件事就该消失了；第七大板块「墨软动漫」则在此基础上**换一套内容获取方式（规则引擎 + 嗅探）并补上弹幕与追番**，与影视板块共享播放/代理/下载，但数据与规则各自独立。


1. **播放卡、帧率低的主因不是"源不好"，而是我们自己的链路**：远程地址一律经 Python 写的本地 HTTP 代理（`services/media_server.py`），而该代理**每个分片都新开一次 TLS（无 `requests.Session`）、把整个响应体读进内存后才转发、所有响应 `Cache-Control: no-store`、失败还带 3 次 0.6s 退避**。1080p/2s 分片意味着每分钟约 30 次握手 + 30 次 Python 全量缓冲，抖动直接体现为卡顿。
2. **第二个主因是解码**：`QMediaPlayer` 用默认后端，代码里**没有任何**硬解/后端选择；`PiSide6 6.11` 同包了 `ffmpegmediaplugin.dll` 与 `windowsmediaplugin.dll`，而 Qt 官方文档明确给了 `QT_FFMPEG_DECODING_HW_DEVICE_TYPES=d3d11va,d3d12va,dxva2`、`QT_DISABLE_HW_TEXTURES_CONVERSION`、`QT_FFMPEG_HW_ALLOW_PROFILE_MISMATCH` 等开关——我们现在一个都没用，1080p H.264 基本靠软解，CPU 打满就是"帧率低"。
3. **第三个主因是选路策略**：`library._lock_best_variant()` 永远锁定**最高码率**变体（`max(rank)`），没有带宽探测、没有卡顿降档。慢网下"锁最高"＝持续 stall。
4. **首帧慢/卡死无自愈**：`player._check_native_started()` 等 20s 只打印一句提示，不会自动降清晰度、换源、或改为"先下后播"。
5. **下载慢的主因**：`download_many()` 逐条串行；HLS 分片下载（无 ffmpeg 时）**严格串行 + 每片 `requests.get` 新建连接**；走 ffmpeg 时 ffmpeg 自己也串行取分片，而且**分片仍然要过我们的 Python 代理**（`_localize_playlist()` 把分片改写成本机代理地址）。三重串行 + 一重 Python 中转。
6. **代理必须"流式 + 连接复用 + 磁盘缓存"**：改造后它从"瓶颈"变成"加速器"（本地缓存命中即零外网、Range 精确、keep-alive、并发预取）。
7. **下载改造 = 并发分片 + 断点续传 + 直连优先**：自研分片下载器（`ThreadPoolExecutor` + 每线程 `Session` + `.part` + 已完成分片索引 + 坏片重试/跳过），ffmpeg 路径改为"能把请求头直接交给 ffmpeg 时不走代理"（本地 playlist 与 `-headers` 双路探测）。
8. **渲染路径要换**：`QVideoWidget` 是原生窗口，**无法在上面可靠叠加 Qt 控件**（弹幕/字幕/统计就做不了）。改用 `QGraphicsView + QGraphicsVideoItem`：视频是场景里的一个 item，弹幕/字幕/缓冲指示/调试信息都是叠加 item —— 这是第七大板块弹幕的前置条件。
9. **对标 MoonTV/LunaTV 的可抄清单**：本地/服务端代理补 Referer+UA、HLS 清单重写、分片缓存与 Range 精确转发、多源聚合与失效降级、图片代理（海报防盗链）、弹幕、自动连播、"线路测速/延迟排序"。**不抄**：服务端转码（我们是桌面端，有本地 ffmpeg 可做按需 remux）、Vercel/Redis 那套部署形态、多用户与鉴权。
10. **第七大板块「墨软动漫」定位**：与影视板块**不重叠** —— 影视是"聚合 CMS 源 + 下载入库"，动漫是"**规则驱动的番剧采集 + 追番 + 弹幕**"（对标 Kazumi 的 XPath/JSON 规则、弹弹play 弹幕、追番更新提醒）。
11. **动漫板块的规则引擎**：JSON 规则文件（字段对齐 Kazumi 的做法：搜索/详情/剧集/播放地址/字幕），选择器**同时支持 XPath 与 CSS**（`lxml` + `soupsieve`），带**规则校验 + 规则调试器 + 导入/导出/订阅更新**；内置规则随包，用户规则存数据目录。
12. **动漫板块不做的**：实时超分辨率（Qt 单栈下需要 libmpv/Anime4K 着色器，与"Qt 单栈、不引入新播放内核"冲突）——改为**离线 ffmpeg 增强**（复用转换引擎），并如实标注边界。

---
