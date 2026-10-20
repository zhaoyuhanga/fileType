# 影视板块（第六）体验改造方案

> **状态**：已实施完成（v1.1.0 落地 A/B/C 阶段，v2.0.0 收尾）。
> **基线**：v1.0.5（含第六大板块墨软文档）。
> **参考项目**：[MoonTV](https://github.com/samqin123/MoonTV.git)、[LunaTV](https://github.com/MoonTechLab/LunaTV.git)（影视聚合播放站）。
> **技术栈约束（不引入新体系）**：PySide6 / Qt 6.11 单栈（**不引入 QtWebEngine**）、`requests`、随包 `ffmpeg`（`tools/ffmpeg`）、单库 `modu.db` + 版本化迁移、`boards/<key>/` + `core/<key>/` 分包、`ui_kit` 设计令牌、pytest、PyInstaller onedir + NSIS。
> 新增依赖：**零**。
> **事实来源与取证方式**：对标结论来自**实读源码 / 官方文档 / issue 核实**，支撑材料在 `docs/research/`。
> 引用到的对标数据都标了出处编号（M*=MoonTV、L*=LunaTV 家族）；**凡 README 宣传语与源码不一致的，一律以源码为准**——别照抄不存在的功能。
>
> **⚠️ v2.0.0 变更**：原第七大板块「墨软动漫」（Kazumi 式规则采集 + 追番 + 弹幕）
> 已在 v2.0.0 **整体移除**，本文档随之收窄为「影视板块」方案（原文件
> `VIDEO_ANIME_PLAN.md` 更名为 `VIDEO_PLAN.md`）。移除原因与替代路径见
> `CHANGELOG.md` 的 v2.0.0 小节与 `docs/BOARDS/video.md`。

---

## 0. TL;DR（先看这 8 条）

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
10. **弹幕能力留在影视板块**：v2.0.0 移除动漫板块后，弹弹play 只读弹幕客户端迁到
    `services/dandanplay.py`，由影视板块的播放器使用（设置项在「设置 → 影视 → 弹幕」）；
    **只读、不发送**，没有 AppId/AppSecret 时给出可操作提示。

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
| 弹幕用户级屏蔽 | ❌ 只有服务端硬编码过滤 | ❌ | **新增**（关键词 + `/正则/` 语法） |
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

#### 3.3.3 弹幕（影视板块内实现，v2.0.0 后是唯一使用方）

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

## 5. 里程碑与任务清单（可勾选）

### 5.1 第六大板块（影视改造）

> **v1.1.0 落地情况**：V1-a ~ V1-h、V2-a ~ V2-c、V3-a ~ V3-f 均已落地并有测试；
> V1-d 的独立 `PlaybackSession` 对象未做（预热/预取已内建在代理里）、V3-c 的弹幕取数走
> 弹弹play 官方接口（只读），V3-g/V3-h 的测试与自检 `video_playback` 已完成并全绿。

- [x] **V1-a** 媒体代理：`services/media_proxy/{server,upstream,cache,playlist}.py` + `media_server.py` 兼容壳
- [x] **V1-b** 分片磁盘缓存（LRU + 配额 + Range 索引）+ 设置页「缓存」组
- [x] **V1-c** 连接池/流式转发/分层超时与重试/并发上限
- [~] **V1-d** `PlaybackSession`（warmup + 预取 + stall 看门狗）：**预热与预取已做**（清单改写时后台预热前 N 片 + 缓存身份键）；独立会话对象未做（播放器有 `_on_prefetch_tick` 挂点）
- [x] **V1-e** 硬解环境变量注入（`app/main.py`）+ 设置项 + 自动降级提示
- [x] **V1-f** `QGraphicsView + QGraphicsVideoItem` 渲染路径（含 `QVideoWidget` 回退开关）
- [x] **V1-g** `core/video/abr.py`（带宽估算 + 档位选择 + 降档/升档）
- [x] **V1-h** 起播加速与自愈（3s/8s/20s 三段策略，position 前进判定）
- [x] **V2-a** `core/video/segment_downloader.py`（并发 + 续传 + 重试/坏片 + 限速 + 合并）
- [x] **V2-b** ffmpeg 路径：直连优先 + `-rw_timeout/-reconnect*/-http_persistent` 探测注入
- [x] **V2-c** `download_many` 并发化 + 暂停/继续（`DownloadWorker` 的 `pause_event` + 检查点门闸）+ 失败重试队列 + 速度/ETA
- [x] **V3-a** 自动连播 + 播放队列（连播倒计时 + N/P 上下一集）
- [x] **V3-b** 字幕层（外挂 srt/vtt）
- [x] **V3-c** 弹幕层（`ui_kit/media/danmaku.py`）+ 影视板块接线（弹弹play 只读 + 本地 XML 导入导出）
- [x] **V3-d** 播放诊断面板（F12，含导出/复制）
- [x] **V3-e** 源测速与排序（`SourceHealth.latency_ms` + 打分公式 + 测速按钮）
- [x] **V3-f** 图片代理（海报防盗链：搜索/详情/番剧库/历史全部过 `image_url()`）
- [x] **V3-g** 测试：3.5 全部用例 + `test_video_nonfunctional.py` 性能预算
- [x] **V3-h** 自检 `video_playback`；文档/CHANGELOG 同步


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

### B. 历史参考（动漫板块已于 v2.0.0 移除）

原第七板块的规则引擎与嗅探实现参考过 Kazumi 的设计，相关调研报告保留在
`docs/research/`（`kazumi_*`）作为**决策留痕**：它们解释了"为什么当初要做"
以及"为什么最终移除"（站点地址普遍依赖网页 JS 或站点私有加密，在没有浏览器
内核的前提下无法稳定播放）。这些文档不再描述当前代码，读的时候请以此为前提。

**Qt / 技术依据**

- Qt Multimedia 高级 FFmpeg 配置（硬解设备列表、`QT_FFMPEG_DECODING_HW_DEVICE_TYPES`、`QT_DISABLE_HW_TEXTURES_CONVERSION`、`QT_FFMPEG_DEBUG`、`QT_FFMPEG_PROTOCOL_WHITELIST`）：<https://doc.qt.io/qt-6/advanced-ffmpeg-configuration.html>
- 弹弹play 开放弹幕网络文档：<https://doc.dandanplay.com/open/>
- 本仓库既有文档：`docs/BOARDS/video.md`、`docs/BOARDS/document.md`（板块接入清单与"诚实边界"写法可照抄）

---

