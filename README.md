# 墨软·工作台 (Modu Workbench)

> 本地离线的一站式阅读、转换、音乐、影视与图库工作台 —— 板块化设计，能力可扩展。

**当前版本：v0.3.0（定版）** · 变更记录见 [CHANGELOG.md](CHANGELOG.md) · 发布产物：
`dist\ModuWorkbench\`（onedir）与 `dist\墨软工作台-Setup-0.3.0.exe`（NSIS 安装包）。

由两个历史项目合并演进而来，现为五大板块：

- **墨软书库**：承接 win-e-book —— TXT/EPUB 本地书库、章节自动解析、进度记忆、阅读主题、在线书库下载（个人学习用途，默认开启）。
- **墨软转换**：承接 fileType —— 六类本地离线转换（文档 / 表格 / 图片 / 媒体 / 归档）与 txt/md/json/mp4 查看编辑。
- **墨软乐库**：联网搜索与下载音乐、内置播放器（顺序/循环/随机）、歌单收藏与分类、播放历史、音频格式转换。
- **墨软影视**：联网搜索电影/电视剧/动漫并下载到本地、内置播放器（多清晰度/倍速/续播）、
  分类与收藏、播放历史、视频格式转换；多数据源可切换并可自行扩展。
- **墨软图库**：本地相册（网格/瀑布流/时间轴）、相册与标签收藏、批量导入与去重、
  EXIF、重复识别、图片美化（非破坏性可撤销）与本地增强优化；可选接入 DeepSeek 生成描述/标签。

旧 Electron/React 工程归档于 `archive/electron-formatflow/`：其渲染进程（React 界面）仍作为**内嵌前端**使用 ——
构建产物整理进 `src/modu_workbench/webfront/`，由 QtWebEngine 加载，经 QWebChannel 桥接到 Python 引擎；
Electron 主进程代码仅作参考，不再构建维护。

## 板块

启动进入首页，卡片/顶栏切换板块：

| 板块 | 内容 | 状态 |
|---|---|---|
| 墨软书库 | 书架（导入/搜索/卡片/历史）、阅读器（TOC/主题/字号/进度记忆/快捷键）、在线书库（00shu 整本直链下载 + 合规开关） | ✅ |
| 墨软转换 | 文本互转/PDF(Qt)/图片/Word/表格/归档/媒体，查看编辑与 JSON 美化、批量任务（进度/取消/输出防覆盖/解压防穿越） | ✅ |
| 墨软乐库 | 在线搜索（歌曲/歌手/专辑/类型）与批量下载、内置播放条（顺序/列表循环/单曲循环/随机）、我的音乐（导入/收藏/分类/筛选）、歌单收藏（含待下载项）、播放历史、批量音频格式转换 | ✅ |
| 墨软影视 | 在线搜索电影/电视剧/动漫（多源聚合/去重/换源重试）与剧集下载、内置播放器（多清晰度/倍速/全屏/续播）、我的视频（分类/收藏/筛选/导入）、播放历史、视频格式转换 | ✅ |
| 墨软图库 | 网格/瀑布流/时间轴浏览、相册标签收藏、批量导入与内容去重、EXIF、重复识别、裁剪滤镜调节文字马赛克涂鸦（可撤销）、本地增强（一键增强/超分/降噪/去模糊/抠图/消除/风格化）、可选 DeepSeek 生成描述标签 | ✅ |
| （可扩展） | 新增板块：`app/registry.py` 注册一条即可，首页与顶栏自动出现 | — |

### 墨软乐库使用说明

- **音源**（可插拔多音源，见 `core/music/sources/`，全部免费、本地直连、无需登录）：
  | 音源 | 能力 | 说明 | 密钥 |
  |---|---|---|---|
  | 网易云音乐 | 完整曲目 | 公开 Web 接口；非 VIP 曲目可下载 | 否 |
  | 酷我音乐 | 完整曲目 | 公开检索 + antiserver 直链，通常可下完整曲目 | 否 |
  | Audius | 自由授权 | 去中心化平台 API，完整播放/下载 | 否 |
  | Internet Archive | 公共领域 | 现场录音、老唱片、自由音乐 | 否 |
  | ccMixter | CC 授权 | 混音 / 器乐 / 伴奏社区 | 否 |
  | iTunes 试听 | 30 秒片段 | Apple 公开接口，元数据完整，稳定兜底 | 否 |
  | Jamendo | CC 完整曲目 | 需免费 client_id（设置里填写） | 是（免费） |
  | 音频直链 | 直链 | 自建或已授权地址 | 否 |
- **多源容错**：每个音源 HTTP 层带退避重试；某音源连续失败会被临时降级（熔断），聚合搜索自动跳过；
  解析/下载失败时自动到其他音源按「曲名 + 歌手 + 时长」匹配同一首歌继续尝试（可在搜索页关闭），
  因此单个平台接口变更或会员限制不会让功能整体不可用；实际使用的音源会在结果里标明。
- **音源管理**：设置里可启用/停用音源、填写 Jamendo ID；停用的音源不参与搜索与自动换源。
- **批量流程**：选择「歌手 / 类型」检索 → 结果列表勾选 → 一键「下载（单条或批量）」或先「加入歌单」（歌单内显示为「待下载」，之后可整单下载）。
  只选中一行时即下载该首；右键结果行还有「试听这首 / 下载这首 / 加入歌单」；
  试听按钮可再次点击停止。
- **收藏与分类**：曲目表「收藏」列可直接点击切换单曲收藏，右键菜单还可播放单曲 / 设置分类 / 加入歌单 / 移除 / 打开文件位置。
- **播放**：底部播放条常驻，切板块不中断；歌单页支持顺序 / 列表循环 / 单曲循环 / 随机。
  在线试听同样走底部播放条（可看进度/时长/音量），试听失败会在播放条与搜索页状态栏给出具体原因；
  播放失败的本地曲目会带曲名提示并自动跳到下一首；导入的本地文件在首次播放后会自动补全时长。
- **格式转换**：mp3 / m4a / wav / flac / aac / ogg / opus / wma 互转（依赖 ffmpeg，可用 `MODU_FFMPEG` 指定）。
- **合规**：抓取与下载仅限个人学习、试听与自有内容备份，界面默认勾选合规声明；请遵守各平台条款与版权要求。
  平台返回「版权受限/VIP」页面时会明确报错，不会把无效文件当成歌曲入库。

### 墨软影视使用说明

- **数据源**（可插拔多源，见 `core/video/sources/`，全部免费、本地直连、无需登录）：

  | 数据源 | 类型 | 说明 |
  |---|---|---|
  | 360资源 / 量子资源 / 如意资源 / 非凡资源 / 电影天堂 / U酷资源 | 采集源 | 苹果CMS( maccms V10 )协议，覆盖电影/电视剧/动漫/综艺；接口地址可在「源设置」里修改 |
  | Internet Archive | 自由授权 | 公共领域电影/老动画/纪录片，可自由下载与离线观看 |
  | Wikimedia Commons | 自由授权 | 自由授权的影片与纪录片段 |
  | 视频直链 | 直链 | 直接粘贴 m3u8 / mp4 地址播放或下载 |
  | 自定义采集源 | 自定义 | 填入任意苹果CMS协议接口（`api.php/provide/vod`） |

  > 采集站的域名寿命普遍很短（改版 / 换域名 / 挂 Cloudflare 是常态）。内置清单会随版本更新，
  > 也可以随时在「源设置」里直接改成新地址。部分站点返回的是 `…/share/<hash>` 分享页，
  > 已由播放解析自动展开为真实 m3u8（无需手动处理）。

- **多源容错**：每个源 HTTP 层带退避重试；某源连续失败会被临时降级（熔断），聚合搜索自动跳过；
  解析/下载失败时自动到其他源按「片名 + 年份 + 主演」匹配**同一部片、同一集**继续尝试，
  因此单个采集接口变更不会让功能整体不可用；实际使用的源会在状态栏标明。
- **源管理（第四板块内「🧩 源设置」）**：勾选启用哪些源、上/下移调整优先级、修改采集接口地址
  （站点换域名无需等版本更新）、「测试选中源」/「测试全部」立即验证连通性（全部不可用即说明是本机网络或代理问题）。
  停用的源不参与搜索与自动换源；「恢复默认顺序」会同时还原内置接口地址。
- **搜索与选集**：搜索结果按「片名 + 年份」去重合并；双击条目打开「选集 / 详情」，可看到分集列表、
  简介与清晰度；支持「在线播放」「下载选中集」「下载全部集」。
- **清晰度多选项**：优先读取 m3u8 主清单里的真实分辨率（1080P / 720P / 4K …），
  解析不到时回退到源给出的线路画质；播放中可随时切换清晰度，会尽量保留当前进度。
- **播放器**：原生解码（QtMultimedia）优先，m3u8 或原生不支持的地址自动切到内嵌网页内核（hls.js）；
  支持播放/暂停、进度拖动、上一集/下一集、倍速（0.5x–2x）、音量/静音、全屏、快捷键
  （空格播放暂停、←/→ 快退快进 10 秒、F11 全屏）与**断点续播**。
- **本地播放与下载**：只要联网即可边看边下；下载支持 HLS 分片合流（有 ffmpeg 时输出 MP4，
  无 ffmpeg 时退化为 `.ts`）与直链下载，可取消、可查看逐条失败原因。下载产物会做容器嗅探，
  避免把「版权受限/失效」的错误页当成影片入库。
- **分类与收藏**：可新建分类、把条目归类、一键收藏（★）；「我的视频」按类型/分类/关键词筛选，
  区分「本地」与「在线」条目；也可导入本地影片（可选复制进影视库目录）。
- **播放历史**：记录每一次播放与下载（含集数、画质、实际使用的源）；双击记录即可**重新解析**继续观看
  —— 在线直链会过期，所以每次播放都会重新取地址。
- **格式转换**：mp4 / mkv / mov / avi / webm / flv / ts / gif 互转，以及从视频**提取音频**
  （mp3 / m4a / wav）；优先流复制（快且无损），容器不兼容时自动回退重编码（依赖 ffmpeg，
  可用 `MODU_FFMPEG` 指定）。
- **合规**：采集类数据源来自第三方站点，仅用于个人学习与技术研究；界面默认勾选合规声明，
  请遵守相应站点条款与版权要求。

### 墨软图库使用说明

桌面端**本地相册**（不做手机/小程序端），针对个人电脑上的图片整理与轻量修图：

- **导入与本地读取**：文件/文件夹批量导入、递归扫描、截图目录一键扫描；按路径增量导入，
  并按 **sha256 内容去重**（同一张图换个文件名也不会重复入库）；自动读取 EXIF（相机、光圈、
  快门、ISO、焦距、GPS、方向）与尺寸；导入过程有进度、可取消，单张失败不影响其余。
- **展示**：网格（2/3/4 列可调）、瀑布流、时间轴（可按年-月筛选）；缩略图**磁盘缓存 + 懒加载**，
  滚动时只读几十 KB 的小图，因此万张图也能流畅滚动；**未滚到的位置也会分批补齐**，不会一直留灰色占位。
  缩略图是**卡片式**：图片在上、文件名单独一行（超长中间省略），带收藏角标与选中/悬停高亮。
  **网格分页**（默认 120 张/页，可选 60/120/240/480 或不分页）：只渲染当前页，
  300+ 张也不会因为一次创建几千个控件而卡顿；底部有页码/上一页/下一页/跳转（PgUp/PgDn）。
  双击进大图查看，支持滚轮缩放、拖拽平移、双击复位、上一张/下一张、幻灯片播放，
  下方实时显示**当前缩放比例**，**手动缩放后翻页保持同一比例**；
  大图页收起导入/整理底栏并附**右侧详情面板**，把空间让给图片（←/→/+/−/0/1/空格 快捷键）。
- **分类与检索**：**左侧分类导航树**（全部图片 / 我的收藏 / 最近导入 / 相册 / 时间 / 标签 / 来源，
  节点自带数量，点击即筛选，随时可一键回到「全部图片」）；自定义相册（**删除相册不删原图**）、
  收藏夹、多标签（可重命名/合并/删除）、本地规则自动分类（横竖图、分辨率、拍摄时段、截图等，
  结果可手动修正）；支持按文件名/相机/标签/AI 描述搜索，按拍摄时间/导入时间/名称/大小/评分排序；
  状态栏显示当前筛选范围（如「标签『风景』 · 共 30 张」），上一张/下一张只在当前结果集内翻页。
- **重复识别**：基于感知哈希（dhash）聚类，能识别「同一张图的不同版本」（例如原图与缩放副本）；
  完全相同的文件在导入阶段就直接跳过。
- **收集**：网址直链收集（会校验确实是图片）、剪贴板收集（截图后直接入库）、
  截图目录扫描；每条记录都带**来源标记**，可用于筛选。
- **图片美化**（非破坏性，全部可撤销）：裁剪（自由框选 + 1:1 / 4:3 / 16:9 / 3:4 预设）、
  90° 与任意角度旋转、水平/垂直翻转、9 组滤镜（强度可调）、亮度/对比度/饱和度/锐化/色温、
  文字（可选颜色）、涂鸦与橡皮、马赛克/模糊、边框；编辑过程记成**步骤栈**，
  支持撤销/重做/重置，按住「看原图」可对比；保存默认**另存为新图**并入库，覆盖原图需二次确认。
- **AI 图片优化**（**本地算法，不联网、不上传图片**）：一键增强、超分辨率（2x/3x/4x）、锐化、
  降噪（边缘保留平滑）、去模糊、自动白平衡、去雾、人像柔化、背景移除、AI 消除、
  老照片修复、风格化（动漫/油画/水彩/黑白/暖冷调）。
  每项都标注**保真度**：「本地算法」是真正做得好；「近似效果」（背景移除 / 消除 / 老照片 / 人像柔化）
  是本地近似，复杂画面可能不理想。处理前后会给出**画质指标对比**（清晰度/噪点/对比/亮度），
  结果另存为新图，原图不动。
- **AI 大模型（可选，仅文本）**：在「设置 → 大模型」配置后，可用于
  **生成图片描述与标签**（图片大模型看图 / 文字大模型只用元数据）、**自然语言检索**
  （如"去年海边的照片"→关键词）、**按描述推荐修图参数**；同类型可配多份并按优先级降级调用。
  ⚠️ **大模型接口只处理文本，不能生成或编辑图片** —— 超分/抠图/去噪/消除等
  像素级操作由上面的本地算法完成，不依赖也无法由它完成。
  默认**关闭**「允许把图片发送到云端」；关闭时只发送文件名/EXIF 等元数据，不发送图片本身。

## 设置（按板块分区）

顶栏「⚙ 设置」打开统一对话框，**左侧按板块分页**：通用 / 大模型 / 书库 / 转换 / 乐库 / 影视 / 图库。
内容较长的页面会自动滚动，「保存 / 关闭」固定在最底部，不会被设置项挤出去。
同时**每个板块顶栏都有「⚙ 板块设置」**，直接跳到本板块那一页：

| 板块 | 该页包含 |
|---|---|
| 通用 | 数据目录与各库路径、外部工具（ffmpeg/ffprobe/LibreOffice）状态、在线书库合规总开关 |
| **大模型** | **按类型（文字/图片/视频）管理多份模型配置**：服务商预设、接口地址、API Key、模型名、启用开关，支持上移/下移调整调用优先级、逐份连通性测试，失败自动降级 |
| 书库 | 合规开关、默认字号/行距、进度记忆、数据库位置 |
| 转换 | 默认输出目录、同名输出加序号、保留源文件、外部工具状态 |
| 乐库 | 下载目录、Jamendo client_id、自动换源、音源启用 |
| 影视 | 下载目录、自动换源、数据源启用、影视库目录 |
| 图库 | 默认导入目录、缩略图缓存（占用与清理）、默认列数、每页张数、预生成缩略图、大模型用量与隐私开关 |

音源/数据源的**顺序调整与连通性测试**仍在板块内的「源设置」里（那里更顺手）。

## 大模型能力中心

设置 → 大模型（配置存 `%APPDATA%\ModuWorkbench\llm.db`，各板块共用）：

- **按类型分开配**：文字大模型（关键词/摘要/参数建议）、图片大模型（看图生成描述与标签）、
  视频大模型（预留给后续视频理解）；
- **同一类型可加多份配置**，列表从上到下就是**调用优先级**；
- **调用失败自动降级**：鉴权失败/余额不足/限流/超时/模型不存在……都会自动换下一份，
  全部失败才报错，且错误里按顺序列出每一次的原因；
- 内置 DeepSeek / OpenAI / 通义千问 / 智谱 GLM / Kimi / 硅基流动 / **本地 Ollama** / 自定义
  （任意 OpenAI 兼容接口）预设，选服务商自动带出接口地址与常用模型名；
- 图库的「AI 描述/标签」用图片类型（失败自动退到文字类型，只发元数据）、
  「自然语言检索 / 推荐修图参数」用文字类型 —— 都走同一套优先级与降级逻辑。

## 技术栈

- Python 3.10+ / PySide6(Qt6) 单应用（**前端统一 Qt 单栈**：不依赖 Node/React/QtWebEngine）
- 内嵌前端：QtWebEngine + QWebChannel（React 产物见 `src/modu_workbench/webfront/`，桥协议见 `services/bridge_shim.js`）
- 解析：ebooklib（EPUB）、python-docx、openpyxl/xlrd、Pillow、markdown/html2text、chardet、Pygments（预览高亮）
- 持久化：SQLite（stdlib sqlite3，`library.db` 书籍进度/历史、`music.db` 曲库/歌单/播放历史）
- 媒体：ffmpeg 子进程（`MODU_FFMPEG` 指定路径）；LibreOffice 可选（`MODU_SOFFICE`）；
  WebView 内 mp4 通过本地流服务（`services/media_server.py`，支持 Range）播放，失败时回退原生播放器
- 音乐播放：QtMultimedia（QMediaPlayer + QAudioOutput）；在线音源用 requests 抓取公开接口
- 测试：pytest（offscreen 无头 Qt，`MODU_DATA_DIR` 隔离数据）
- 打包：PyInstaller（`workbench.spec`，onedir）+ NSIS（`packaging/installer.nsi`）

## 开发运行

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple
python -m modu_workbench
python -m pytest tests -q
```

## 重建内嵌前端（仅在改动 `archive/electron-formatflow/` 界面时需要）

```powershell
pwsh -ExecutionPolicy Bypass -File packaging\build_webfront.ps1   # 首次会 npm install 到 .webfront-build\
# macOS / Linux: packaging/build_webfront.sh
```

脚本流程：安装渲染进程最小依赖 → 目录联接 `node_modules` → `vite build` →
`python -m modu_workbench.services.web_prepare`（清理旧产物、写入 `qtwebchannel.js`/`bridge_shim.js`、注入版本号）。
纯 Python 改动无需重建前端。

## 品牌图形（应用图标 / 安装包图形）

图标由脚本生成，可随时重出：

```powershell
python packaging\make_icons.py          # 生成 src/modu_workbench/assets/ 下的图标与安装包图形
python packaging\icon_preview.py        # 生成 .icon_preview.png（各尺寸放大预览，便于检查小图标可读性）
```

产物与用途：

| 文件 | 用途 |
|---|---|
| `src/modu_workbench/assets/app.ico` | Windows 应用图标（16/24/32/48/64/128/256）→ PyInstaller `icon=`、NSIS `MUI_ICON` |
| `src/modu_workbench/assets/app.png` | 512×512 源图（文档 / 其他平台） |
| `src/modu_workbench/assets/installer_header.bmp` | 安装向导页眉图 150×57 |
| `src/modu_workbench/assets/installer_welcome.bmp` | 安装向导欢迎页左图 164×314 |

设计说明：白色圆角底 + 蓝色双向箭头（浅蓝指向右上、深蓝指向左下），
呼应「阅读 / 转换 / 音乐 / 影视」的双向流动语义；`assets/` 随包分发，
运行时由 `services/assets.py` 定位并设置窗口/任务栏图标。

## 打包 Windows

### 一键打包（推荐）

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_app.ps1
# 默认 --clean，并会做「源码自检 → 打包 → 验证影视/图库模块是否随包 → 产物自检」
# 需要一并生成安装包：加 -Installer
```

脚本参数：`-NoClean`（保留 PyInstaller 缓存，默认会 `--clean`）、`-SkipDeps`、`-Installer`、`-Python <路径>`。

> **注意**：新增了 Python 包（例如 `core/video`）时**必须** `--clean`，
> 否则 PyInstaller 复用旧的分析缓存会导致新模块漏打包 —— 表现就是「首页少一个板块」。

### 手动打包

```powershell
pip install pyinstaller
pyinstaller workbench.spec --noconfirm --clean
# 产物：dist\ModuWorkbench\ModuWorkbench.exe（随包依赖在同目录）

# 可选：生成 NSIS 安装包（需 makensis；脚本自动读取版本号）
powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1
```

### 打包自检（含音频解码与影视核心）

```powershell
$env:MODU_CHECK_DEPS = "$env:TEMP\modu-check.json"
.\dist\ModuWorkbench\ModuWorkbench.exe   # 检查完自动退出，结果写入该 json
Get-Content $env:MODU_CHECK_DEPS
```

共 15 项：markdown 渲染 / 代码块高亮 / JSON 高亮 / Pygments 词法器 / WebEngine 导入与渲染 /
webfront 产物 / QtMultimedia / **真实音频解码播放**（播放一段静音 WAV 并确认播放位置前进）/
音乐库建表读写（含 8 个音源与匹配器）/
**影视核心**（影视库建表读写、m3u8 多清晰度解析、数据源注册表齐全、换源定位到同一集）/
**图库核心**（建表读写、缩略图生成、感知哈希区分图片、编辑管线、增强算法、大模型未配置时明确报错）/
**大模型核心**（多类型配置、优先级排序、失败自动降级、旧配置迁移）/
**ffmpeg 工具链**（随包 ffmpeg/ffprobe 可执行）/ 品牌图标。全部为 `true` 才算打包正常。
播放自检需要真实桌面与音频设备；设置 `QT_QPA_PLATFORM=offscreen` 时会跳过解码检查。

### 随包 ffmpeg

`tools/ffmpeg/{ffmpeg,ffprobe}.exe` 会打进 `_MEIPASS/tools/ffmpeg`，用户**无需自行安装**即可：
音视频格式转换、把 HLS 下载合流为 MP4、探测媒体时长。定位优先级：
`MODU_FFMPEG` 环境变量 → 随包目录 → `PATH`。

> 说明：HLS 的 **AES-128 加密流不依赖 ffmpeg** —— 内置纯标准库解密实现
> （`core/video/aes.py`，用 FIPS-197 / NIST SP 800-38A 官方向量做正确性验证），
> 没装 ffmpeg 也能下载，只是输出 `.ts` 而非 `.mp4`。仅 SAMPLE-AES 等加密方式必须用 ffmpeg。

可选环境变量：`MODU_DATA_DIR`（数据目录，默认 `%APPDATA%\ModuWorkbench`）、`MODU_FFMPEG`、`MODU_SOFFICE`。
首次启动会自动迁移 win-e-book 旧库（`%APPDATA%\WinEBook\library.db`）。

### 命名说明（显示名 vs 内部标识）

- **显示名称**（界面、安装包、快捷方式、文档）统一为「墨软」系列：
  墨软·工作台 / 墨软书库 / 墨软转换 / 墨软乐库 / 墨软影视；安装包输出为 `dist\墨软工作台-Setup-<版本>.exe`。
- **内部标识刻意保持不变**，以便已有数据与脚本继续可用：
  Python 包名 `modu_workbench`、可执行文件 `ModuWorkbench.exe`、
  环境变量 `MODU_*`、数据目录 `%APPDATA%\ModuWorkbench`（书库 `library.db`、曲库 `music.db` 原地沿用）。

## 目录结构

```
src/modu_workbench/
├── app/main.py / __main__.py      # 入口
├── app/shell.py               # 主壳：顶栏（首页/板块/设置）+ 页面栈
├── boards/                    # ★ 板块注册与页面
│   ├── registry.py / base.py  # 板块注册表（可扩展）
│   ├── home_board.py          # 首页：介绍 + 板块入口
│   ├── book_board.py          # 墨软书库
│   ├── book_shelf.py          # 书架
│   ├── book_reader.py         # 阅读器
│   ├── book_online.py         # 在线书库
│   ├── convert_board.py       # 墨软转换（Qt 回退实现）
│   ├── convert_web.py         # 墨软转换（内嵌前端 + QWebChannel）
│   ├── doc_viewer.py          # 文档查看/编辑器（Qt 回退实现）
│   ├── music_board.py         # 墨软乐库（板块外壳 + 底部播放条）
│   ├── music_search.py        # 墨软乐库：在线搜索与批量下载
│   ├── music_library_page.py  # 墨软乐库：我的音乐 + 格式转换
│   ├── music_playlist.py      # 墨软乐库：歌单收藏
│   ├── music_history.py       # 墨软乐库：播放历史
│   ├── music_widgets.py       # 墨软乐库：表格/对话框/后台线程
│   ├── video_board.py         # 墨软影视（板块外壳 + 播放/下载编排）
│   ├── video_search.py        # 墨软影视：在线搜索与下载
│   ├── video_detail.py        # 墨软影视：详情/选集/清晰度对话框
│   ├── video_library_page.py  # 墨软影视：我的视频 + 分类收藏 + 格式转换
│   ├── video_history.py       # 墨软影视：播放历史
│   ├── video_sources.py       # 墨软影视：源设置（启用/优先级/接口地址/连通性测试）
│   ├── video_player.py        # 墨软影视：播放器（原生优先 + hls.js 兜底）
│   ├── video_widgets.py       # 墨软影视：表格/对话框/后台线程
│   ├── gallery_board.py       # 墨软图库（板块外壳 + 网格/瀑布流/时间轴 + 大图查看）
│   ├── gallery_widgets.py     # 墨软图库：缩略图网格/查看器/对话框/后台线程
│   ├── gallery_editor.py      # 墨软图库：编辑美化（裁剪/滤镜/调节/文字/马赛克/涂鸦/边框）
│   └── gallery_enhance.py     # 墨软图库：AI 优化（本地增强 + 前后对比）
├── core/book/               # 书库引擎（迁移自 win-e-book）
│   ├── parser.py / storage.py / library.py / online.py
├── core/music/                # 音乐引擎
│   ├── models.py              # 曲目/在线结果/歌单/历史模型
│   ├── storage.py             # SQLite：曲库/歌单/待下载项/历史/设置
│   ├── sources/               # ★ 音源层（多源 + 重试 + 熔断 + 跨源兜底）
│   │   ├── base.py            # MusicSource 基类 / SourceInfo / 健康度
│   │   ├── http.py            # 带退避重试的 HTTP 客户端 + 网络错误翻译
│   │   ├── matcher.py         # 曲名/歌手/时长匹配（跨源找同一首歌）
│   │   ├── registry.py        # 注册表：聚合搜索 / 熔断 / 换源解析 / 配置持久化
│   │   └── providers/         # netease / kuwo / audius / archive / ccmixter / itunes / jamendo / direct
│   ├── downloader.py          # 流式下载（进度/取消/封面/歌词/换源重试/音频校验）
│   ├── library.py             # 本地曲库：导入/时长探测/转换入口
│   └── player.py              # 播放器：队列/循环/随机/进度/音量/在线试听
├── core/video/                # 影视引擎
│   ├── models.py              # 片目/剧集/画质/收藏/历史模型
│   ├── storage.py             # SQLite：影视库/分类收藏/历史/播放记录/设置
│   ├── hls.py                 # m3u8 解析（主清单多清晰度 + 媒体清单分片）
│   ├── sources/               # ★ 视频源层（多源 + 重试 + 熔断 + 跨源兜底）
│   │   ├── base.py            # VideoSource 基类 / SourceInfo / 健康度
│   │   ├── http.py            # 带退避重试的 HTTP 客户端 + 网络错误翻译
│   │   ├── matcher.py         # 片名/年份/主演匹配（跨源找同一部、同一集）
│   │   ├── registry.py        # 注册表：聚合搜索 / 熔断 / 换源解析 / 配置持久化
│   │   └── providers/         # cms_vod（苹果CMS 采集）/ public（Archive·Wikimedia·直链·自定义）
│   ├── downloader.py          # 下载（HLS 合流 / 直链；进度/取消/换源重试/容器嗅探）
│   └── library.py             # 影视库：导入/在线解析/下载入库/转换入口
├── core/gallery/                # 图库引擎
│   ├── models.py              # 图片/相册/标签/编辑步骤/AI 任务模型
│   ├── storage.py             # SQLite：图片/相册/标签/编辑历史/AI 任务/设置
│   ├── hashing.py             # 内容哈希(sha256) + 感知哈希(dhash) 去重与相似识别
│   ├── exif.py                # EXIF 解析（拍摄时间/相机/光圈快门 ISO/GPS/方向）
│   ├── thumbs.py              # 缩略图生成与磁盘缓存（懒加载的基础）
│   ├── edits.py               # 非破坏性编辑管线（裁剪/滤镜/调节/文字/贴纸/涂鸦/马赛克/边框）
│   ├── enhance.py             # 本地增强算法（numpy+Pillow）：超分/降噪/去模糊/抠图/消除/风格化
│   ├── ai.py                  # DeepSeek 集成（仅文本：描述/标签/检索/参数建议）
│   └── library.py             # 图库业务层：导入/收集/分类/编辑导出/增强/AI 编排
├── core/convert/              # 转换引擎
│   ├── registry.py / engine.py / formats.py / text_io.py
│   ├── pdf_out.py / image_io.py / archive_io.py
│   ├── office_io.py / sheet_io.py / media_io.py
├── services/                  # 桥接与运行时服务
│   ├── web_bridge.py          # QWebChannel 桥（前端 RPC → Python 引擎）
│   ├── bridge_shim.js         # 注入前端的 window.formatFlow 兼容层
│   ├── web_prepare.py         # 整理 Vite 产物为 webfront
│   ├── media_server.py        # 本地媒体流（Range）+ 远程直链/HLS 清单代理（补 Referer/UA）
│   ├── media_player.py        # 原生播放器回退
│   └── file_scan.py           # 导入文件扫描（唯一实现）
├── webfront/                  # 内嵌前端产物（由 build_webfront 生成）
├── ui_kit/                    # 统一设计规范（theme/toast/组件/设置）
│   └── settings/              # 分板块设置页（通用/书库/转换/乐库/影视/图库）
tests/                         # pytest（无头冒烟 + 单元）
workbench.spec                 # PyInstaller 配置（Windows）
workbench_mac.spec             # PyInstaller 配置（macOS，BUNDLE）
packaging/                     # 一键打包脚本 + NSIS 安装脚本 + 前端构建脚本
archive/electron-formatflow/   # 旧 Electron 版归档（前端源码 + 主进程参考）
docs/ARCHITECTURE.md           # 架构与合并方案
```

## 里程碑

| 阶段 | 内容 | 状态 |
|---|---|---|
| M0 | Python 骨架 / 首页 / 板块注册 / ui_kit 主题 | ✅ |
| M1 | 墨软书库（书库/阅读/在线书库，旧库迁移） | ✅ |
| M2 | 转换基础（文本/PDF(Qt)/图片/归档/查看编辑/任务队列） | ✅ |
| M3 | 转换高级（Word/表格/媒体，LibreOffice 高保真） | ✅ |
| M4 | 跨板块联动 / 设置 / PyInstaller 打包 / 推送 | ✅ |
| M5 | 内嵌 main 分支前端（QtWebEngine + QWebChannel）/ mp4 内联预览 / NSIS 安装包 | ✅ |
| M6 | 墨软乐库板块（在线搜索下载 / 播放器 / 歌单收藏分类 / 播放历史 / 音频格式转换） | ✅ |
| M7 | 墨软影视板块（多源搜索下载 / 多清晰度播放器 / 分类收藏 / 播放历史 / 视频格式转换） | ✅ |
| M8 | 墨软图库板块（本地相册 / 分类标签收藏 / 导入去重 / 美化编辑 / 本地增强）+ 设置按板块分区 | ✅ |

## 协议

MIT，见 [LICENSE](LICENSE)。第三方依赖：PySide6(LGPL)、ebooklib(AGPL，仅本地阅读解析)、lxml(BSD)、bs4(MIT)、Pillow(MIT-C)、ffmpeg(GPL 构建，随部署另行声明) 等。
在线书源抓取仅限个人学习、试读与自有内容备份，默认需勾选合规声明。