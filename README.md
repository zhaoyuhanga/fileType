# 万能格式转换器 (FormatFlow)

完全本地离线运行的桌面文件格式转换工具，支持 Windows 和 macOS。所有转换由应用内置的转换库完成，不依赖任何外部可执行程序，也无需网络。v0.2.0 起内置**本地文档查看 / 编辑**模块。

## 功能

### 格式转换
- **文档转换**：Word / PDF / Markdown / HTML / TXT 互转（Word 转 PDF 在 Windows 上优先调用本机 Word/WPS COM 原生导出）
- **表格转换**：Excel / CSV → PDF / HTML / TXT / CSV
- **图片互转**：JPG / PNG / WebP / BMP / GIF 任意互转
- **音视频转换**：MP4 / MOV / AVI 互转，M4A / MP3 / WAV 互转，视频提取音频（内置 ffmpeg）
- **归档处理**：ZIP / TAR 压缩与解压、RAR 解压
- **批量处理**：多文件导入与转换；转换过程实时上报进度，支持一键取消整批任务
- **输出安全**：同名输出自动追加序号避免覆盖；解压对 zip-slip 路径穿越做了防护

### 本地文档查看 / 编辑（v0.2.0）
双击文件行或点击行内"预览 / 编辑"按钮打开本地文档，**文件在本地直接读写，无需导出/导入**：

| 格式 | 预览 | 编辑 | 说明 |
|---|---|---|---|
| TXT | 纯文本只读 | ✅ | 编辑 + 保存 / 另存为 |
| Markdown | 排版渲染（已消毒） | ✅ | 支持保存 / 另存为 |
| JSON | 自动美化缩进预览 | ✅ | 提供"美化"按钮（解析失败给出中文行/列提示） |
| MP4 | 本地视频播放 | — | 走 docstream 流协议，支持拖动进度条；可"另存为"复制 |

- **预览期间不可修改**：界面明确的只读提示；切换到"编辑"后出现修改区。
- **保存**：写回原文件（`Ctrl+S`）；**另存为**：弹出系统对话框写为新文件，文件行自动更新为新路径。
- **未保存保护**：存在未保存修改时关闭会弹出"保存并关闭 / 不保存 / 取消"确认。
- **编码自适应**：文本自动识别 UTF-8 / UTF-16 BOM / GBK(GB18030)，中文旧文件可直接查看编辑（保存统一为 UTF-8）。
- **媒体流**：mp4 通过自定义 `docstream://` 协议提供带 Range 的字节流，开发与打包环境均可用。

## 交互与提示
- 统一 **Toast 提示**（成功 / 错误 / 信息）替代零散状态条，导入、转换、保存、另存为、取消等操作均有明确反馈。
- 状态徽章本地化：排队中 / 转换中 / 成功 / 失败 / 已取消；格式徽章按类别着色。
- 错误文案统一为面向用户的中文（含建议），IPC 文档接口统一返回 `{ ok, error? }` 信封。

## 技术栈

| 层 | 技术 |
|---|---|
| 桌面框架 | Electron 35（自定义流协议 docstream） |
| 前端 | React 18 + TypeScript |
| 构建 | Vite 6 + tsc |
| 测试 | Vitest + Testing Library |
| Markdown 预览 | marked + DOMPurify（防 XSS） |
| 打包 | electron-builder (NSIS / DMG) |

## 内置转换能力

| 能力族 | 覆盖格式 | 依赖库 |
|------|------|--------|
| 文档-文字 | DOCX/DOC/MD/HTML/TXT/PDF | mammoth, pdf-parse, pdfkit, turndown, marked, docx, word-extractor |
| 文档-表格 | XLSX/XLS/CSV | SheetJS (xlsx) |
| 图片 | JPG/PNG/WebP/BMP/GIF | sharp, bmp-js |
| 媒体 | MP4/MOV/AVI/M4A/MP3/WAV | ffmpeg-static（随应用打包） |
| 归档 | ZIP/TAR/RAR | jszip, tar-stream, node-unrar-js |

> 说明：项目不依赖外部可执行程序；格式清单的唯一事实来源是 `src/shared/converterCapabilities.ts`（转换分发与界面可用性共用）。

## 开发

```bash
# 安装依赖
npm install

# 启动开发模式
npm run dev

# 运行测试
npm test

# 构建
npm run build

# 打包 Windows
npm run pack:win

# 打包 macOS
npm run pack:mac
```

## 项目结构

```
src/
├── main/                     # Electron 主进程
│   ├── main.ts               # 窗口创建、协议注册、生命周期
│   ├── ipc.ts                # IPC 处理器（含批处理实时事件与取消）
│   ├── preload.cjs           # contextBridge API（唯一 preload，开发/打包共用）
│   └── services/
│       ├── conversionService.ts  # 内置转换分发（输出防覆盖、解压防穿越）
│       ├── documentService.ts    # 文档读取（编码识别）/写入/复制
│       ├── docHandlers.ts        # 文档 IPC（read/save/saveAs，统一 {ok} 信封）
│       ├── docStream.ts          # docstream:// 流协议（支持 Range）
│       ├── engineHealth.ts       # 内置引擎状态探测
│       ├── fileImport.ts         # 文件导入（递归目录扫描）
│       ├── jobRunner.ts          # 子进程封装（超时/取消支持）
│       └── shellOpen.ts          # 系统文件夹打开
├── renderer/                 # React 渲染进程
│   ├── App.tsx               # 主组件（订阅 jobEvent 实时刷新、查看器接线）
│   ├── actionAvailability.ts # 动作可用性（引用 shared 能力清单）
│   ├── state/appReducer.ts   # 状态管理
│   ├── utils.ts              # 展示/文档工具（格式徽章、docstream URL）
│   └── components/           # UI 组件
│       ├── DocumentViewer.tsx    # 文档查看/编辑器（预览只读/编辑/保存/另存为/JSON 美化）
│       ├── Toast.tsx / ConfirmDialog.tsx  # 统一提示与确认
│       └── ...
└── shared/                   # 共享代码（主/渲染双端）
    ├── types.ts / constants.ts   # 类型与通道常量
    ├── fileTypes.ts / formatDetection.ts
    ├── converterCapabilities.ts  # 内置转换能力唯一事实来源
    ├── jsonFormat.ts             # JSON 美化/校验（行、列定位）
    ├── outputPaths.ts
    └── converters/registry.ts    # 转换动作注册表
```

## 协议

内部项目，仅供学习使用。
