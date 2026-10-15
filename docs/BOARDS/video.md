# 板块：墨软影视（`video`）

> 多源聚合搜索 + 在线播放 + 下载入库
> 子页：search / library / history / sources / player

## 0. v1.1.0 播放/下载链路重写（先读这段）

三个用户感知最强的症状（**帧率低、播放卡、下载慢**）根因都在我们自己的链路上，
v1.1.0 按 `docs/VIDEO_ANIME_PLAN.md` 逐条改掉：

| 症状 | 原来的根因 | 现在 |
|---|---|---|
| 帧率低 | 全仓没有任何硬解/后端配置（1080p 软解 CPU 打满） | 启动时注入 `QT_MEDIA_BACKEND=ffmpeg` + `QT_FFMPEG_DECODING_HW_DEVICE_TYPES`（`ui_kit/media/decode.py`），设置项可关 |
| 播放卡 | 代理每分片新建 TLS、全量缓冲、`no-store` | `services/media_proxy/`：Session 连接池 + `iter_content` 流式转发 + 磁盘 LRU 缓存 + 清单短缓存 + 预热 |
| 播放卡（选路） | `_lock_best_variant()` 永远锁最高码率 | `core/video/abr.py`：按实测带宽选档（`quality_policy`），卡顿自动降档 → 换源 |
| 起播卡死 | 等 20s 只打印一句提示 | 三段自愈 3s 降档 / 8s 换源 / 20s 建议先下后播（`stallDetected` 信号） |
| 下载慢 | `download_many` 串行、分片串行 + 每片新连接、无续传 | `core/video/segment_downloader.py`：并发 6 + 保序落盘 + `.parts` 断点续传 + 坏片重试 + 限速；集间并发 2 |

**兼容性**：`services/media_server.py`、`boards/video/player.py` 都保留为**转发壳**
（同名同签名），旧调用方与旧测试不需要改动。

### 0.1 三个必知的边界

1. **硬解是进程级环境变量**：改设置后需重开播放窗口（必要时重启应用）才生效；
2. **代理默认拒绝访问环回/私网目标**（SSRF 收口）。本机 fixture 测试要显式设
   `MODU_MEDIA_ALLOW_PRIVATE=1`；`MODU_VIDEO_CACHE_DIR` 可覆盖缓存目录；
3. **`QVideoWidget` 无法叠加任何东西**，所以渲染路径换成了
   `QGraphicsView + QGraphicsVideoItem`（字幕/弹幕/诊断浮层的前提）；
   显卡异常时用 `video/render_path=widget` 回退。

## 1. 目录

**界面层（boards/video/）**

| 文件 | 行数 |
|---|---|
| `boards/video/board.py` | 725 |
| `boards/video/context.py` | 36 |
| `boards/video/detail.py` | 337 |
| `boards/video/history.py` | 253 |
| `boards/video/library_page.py` | 451 |
| `boards/video/player.py` | 39（转发壳 → `ui_kit/media/player.py`） |
| `boards/video/search.py` | 432 |
| `boards/video/sources.py` | 345 |
| `boards/video/widgets.py` | 553 |

**引擎层（core/video/）**

| 文件 | 行数 |
|---|---|
| `core/video/aes.py` | 142 |
| `core/video/abr.py` | 270（新增） |
| `core/video/downloader.py` | 1140 |
| `core/video/hls.py` | 268 |
| `core/video/library.py` | 470 |
| `core/video/models.py` | 425 |
| `core/video/segment_downloader.py` | 790（新增） |
| `core/video/sources/base.py` | 192 |
| `core/video/sources/http.py` | 47 |
| `core/video/sources/matcher.py` | 92 |
| `core/video/sources/providers/cms_vod.py` | 462 |
| `core/video/sources/providers/public.py` | 337 |
| `core/video/sources/registry.py` | 410 |
| `core/video/storage.py` | 532 |

**共享层（v1.1.0 新增，第六/第七板块共用）**

| 文件 | 职责 |
|---|---|
| `services/media_proxy/{server,upstream,cache,playlist,config,stats,state}.py` | 本机媒体代理：连接池/流式/磁盘缓存/预热/安全收口 |
| `ui_kit/media/{surface,player,danmaku,subtitle,decode}.py` | 视频面/播放器窗口/弹幕轨道与分级/字幕解析/硬解环境注入 |

> 为什么播放器搬到 `ui_kit/media/`：架构闸门规定「板块不得引用其他板块」，
> 而第七大板块「墨软动漫」必须复用同一个播放器 —— 共享层是唯一合规的落点（方案 4.0）。

依赖规则：本板块只能引用 `boards/video/`、`core/video/`、`core/platform`、`core/llm`、`ui_kit`、`services`；
跨板块引用会被 `tests/architecture/test_architecture.py` 拦下。

## 2. 数据

数据库：单库 `modu.db`（详见 `docs/DATABASE.md`）。

- `video_videos`
- `video_playlists`
- `video_playlist_items`
- `video_history`
- `video_play_records`

分片缓存落在 **文件系统**（`数据目录/video/cache/<前2位>/<sha1>.bin` + `.meta`），
不落库；设置页「清空分片缓存」会删掉它们，随用随重建。

应用级单例：`boards/video/context.py`（惰性创建；`app/context.py` 做跨板块聚合）。

## 3. 界面约定

- 页面骨架：`ColumnPage` + `SectionCard` + `EmptyState`（空列表必须给下一步操作）；
- 状态与进度：板块底部 `TaskBar`（空闲自动收起进度与取消）；页面用 `_report` / `_busy` / `_idle`；
- 留白/圆角/字号一律取 `ui_kit/tokens.py` 的令牌（见 `docs/UI_GUIDE.md`）；
- 播放器快捷键：空格/←→/F11/F/Esc（旧）+ `F12` 诊断、`N`/`P` 上下一集、`D` 弹幕、`[`/`]` 调速、`Ctrl+S` 复制诊断。

## 3.1 v1.1.0 新增的下载/播放配套

| 能力 | 入口 | 说明 |
|---|---|---|
| 暂停/继续下载 | 「我的视频」导航行「⏸ 暂停下载 / ▶ 继续下载」 | 已下载分片保留在 `<目标>.parts/`，继续即续传 |
| 重试失败项 | 导航行「↻ 重试失败项」 | 只重试上次失败的集，已下载分片不重下 |
| **离线画质增强** | 「我的视频」→「增强」行 | **离线 ffmpeg 重编码，不是 Anime4K 实时超分**：放大档 = `scale=1920:1080:flags=lanczos,unsharp=5:5:0.8,format=yuv420p` + `libx264 -crf 20`；重编码档 = 保持分辨率压码率。只对**已下载到本地**的条目生效，耗时以分钟计、可取消（产物命名为 `…（增强）.mp4` 并自动入库） |
| 源测速与排序 | 「🧩 源设置」 | 延迟 P50 + 实测速度 + 评分（分辨率 40% + 速度 40% + 延迟 20%）；「按测速结果排序」默认关，保持搜索顺序可预期 |
| 弹幕（只读） | 播放器「弹幕」按钮（右键可导入本地 XML） | 弹弹play 官方接口，需在「设置 → 影视 → 弹幕」填 AppId/AppSecret；未配置时给出可操作提示 |
| 广告分片跳过 | 「设置 → 影视 → 缓存与网络」 | 实验性、**默认关**：只跳"时长明显短于中位且与前一 jump 片不同目录"的分片，最多 3 片，并明确提示跳了哪些 |

## 4. 测试

| 文件 | 说明 |
|---|---|
| `tests/board_video/test_video_aes.py` | 板块引擎测试 |
| `tests/board_video/test_video_abr.py` | ABR 带宽估算/选档/降升档（纯算法） |
| `tests/board_video/test_video_core.py` | 板块引擎测试 |
| `tests/board_video/test_video_integration.py` | 板块引擎测试 |
| `tests/board_video/test_video_media_shared.py` | 共享层：弹幕分级/轨道、字幕解析、硬解注入、渲染路径 |
| `tests/board_video/test_video_nonfunctional.py` | **性能预算**：代理吞吐/缓存命中/首字节/分片并发/弹幕分配 |
| `tests/board_video/test_video_proxy.py` | 代理：缓存/流式/Range/重试/并发池/Referer 回退/SSRF |
| `tests/board_video/test_video_segment_downloader.py` | 分片下载器：并发保序/续传/坏片/暂停/限速 |
| `tests/board_video/test_video_ui.py` | 板块界面测试 |
| `tests/board_video/test_video_sources.py` | 内置源清单结构自检（离线，不探测接口存活） |
| `tests/board_video/test_video_search_budget.py` | **聚合搜索时间预算**：死源不阻塞/部分结果先回报/源顺序与去重语义不变 |
| `tests/board_video/test_video_self_heal.py` | 卡顿自愈选路：只降一档、认不出当前档就换源 |
| `tests/board_video/test_video_enhance.py` | 离线画质增强（含真 ffmpeg 端到端） |
| `tests/board_video/test_video_settings.py` | 影视设置页新增键的默认值与往返 |

冒烟清单见 `docs/TESTING.md` 第 5 节。

## 4.0 搜索为什么会"一直搜索中"（真实故障与修法）

用户反馈"墨软影视搜索一直搜索中，没有反馈"。根因**不是界面**：

- 13 个内置源里 `archive.org` 从本机完全不可达，它自己的客户端要
  "15s × 3 次 + 退避"才放弃（实测单源 >45 秒）；
- 原来的 `search_all` 是**顺序遍历且没有单源超时** → 整条搜索被它拖住约 50 秒；
- 而且结果要等**所有**源结束才上屏 → 界面只有"搜索中…"，一条都不出。

修法（`registry.py`）：

1. **并发跑**所有源（默认 8 线程）；
2. **单源时间预算** `DEFAULT_SEARCH_TIMEOUT = 9s`（依据：11/13 个源实测 ≤5 秒返回，
   含最慢的采集站 4.9 秒），超时的源记一次失败并跳过；
3. **显式 `shutdown(wait=False)`** —— 不能用 `with ThreadPoolExecutor(...)`：
   `__exit__` 会 `shutdown(wait=True)`，把那个卡住的源**又 join 回来**。
   实测：9 秒判完，但 `with` 退出时又等了 84 秒，总耗时 93.8s，用户端依旧卡住。
   改成不等待后 **93.8s → 9.0s**；
4. **边搜边显示**：`on_partial` 每完成一个源回报一次 → `SearchWorker.progressed` /
   `partialResults` → 状态栏显示"已查 4/11 个源，已找到 12 条…"，表格即时上屏
   （实测第一个源 **0.7 秒**就有结果）；
5. **顺序语义不变**：结果按源顺序归并（不是完成先后），
   所以 `sort_by_speed` 与"同名保留高优先级源"的行为与改前完全一致。

## 4.1 内置采集源维护

采集源清单写在 `core/video/sources/providers/__init__.py` 的 `CMS_SITES`，顺序即聚合优先级
（`registry.search_all` 按「标题+年份」去重、保留先出现的源），`DEFAULT_PROVIDER_ORDER` 必须与它一一对应。

- **加源前必须人工实测**：`GET <base>/api.php/provide/vod?ac=detail&wd=关键词` 要返回 200 + JSON + 非空 `list`，
  且 `vod_play_url` 里的地址能真的拉到 m3u8/mp4（分享页要能解析出真实地址）；
  至少测两个关键词（一个电影 + 一部剧集），只测一个关键词不足以判死。
- **排序按实测码率**：同一集的分片大小 ÷ 分片时长可用于估算码率，
  码率高、线路全的站排前面，避免「只有 540P/很模糊」的线路抢占同名结果。
- **同一上游换域名的站只留一个**：这类站共用一套 CDN 与同名分片（分片数、分片大小完全相同），
  都留着只会让搜索重复请求同名结果。
- **失效即替换**：搜索接口返回 JSON 但播放地址全 404 的站点同样算失效（用户点播必失败），直接换成实测可用的站。
- 改完清单要同步 `tests/board_video/test_video_sources.py` 的结构约束，并递增 `core/video/sources/registry.py`
  的 `SOURCES_VERSION`（否则老用户的「启用集合」会把新源默认关掉）。

## 5. 变更须知

- 改数据结构：加迁移模块（`core/platform/migrations/`）并同步 `tests/platform/test_db_schema.py` 与 `docs/DATABASE.md`；
- 改界面：跑 `packaging/ui_snapshot.py --only video` 出图对比，再跑 `pytest tests/board_video tests/architecture -q`；
- 新增数据源：只在 `core/video/sources/providers/__init__.py` 注册（板块业务代码不需要改），见 4.1。
