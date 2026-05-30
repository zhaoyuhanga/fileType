# 万能格式转换器 (FormatFlow)

完全本地离线运行的桌面文件格式转换工具，支持 Windows 和 macOS。

## 功能

- **文档转换**：Word / PDF / Markdown / HTML / TXT 互转
- **表格转换**：Excel / CSV → PDF / HTML / TXT
- **图片互转**：JPG / PNG / WebP / BMP / GIF 任意互转
- **音视频转换**：MP4 / MOV / AVI 互转，M4A / MP3 / WAV 互转，视频提取音频
- **ZIP 压缩**：将任意文件打包为 ZIP 归档
- **ZIP 解压**：将 ZIP 归档解压到指定目录
- **批量处理**：支持多文件同时导入和转换
- **离线运行**：无需网络，所有转换在本地完成

## 技术栈

| 层 | 技术 |
|---|---|
| 桌面框架 | Electron 35 |
| 前端 | React 18 + TypeScript |
| 构建 | Vite 6 + tsc |
| 测试 | Vitest + Testing Library |
| 打包 | electron-builder (NSIS / DMG) |

## 内置转换引擎

| 引擎 | 用途 | 依赖库 |
|------|------|--------|
| 文档 | DOCX/DOC → PDF/HTML/MD/TXT | mammoth, pdf-parse, pdfkit, turndown, marked, docx |
| 表格 | XLSX/XLS/CSV → PDF/HTML/TXT/CSV | SheetJS (xlsx) |
| 图片 | JPG/PNG/WebP/BMP/GIF 互转 | sharp, bmp-js |
| 音视频 | MP4/MOV/AVI/M4A/MP3/WAV | ffmpeg-static |
| 压缩 | ZIP 压缩/解压 | jszip |

## 外部引擎（可选）

将对应二进制放入 `resources/engines/<platform>/` 即可启用更多转换能力：

- `soffice` (LibreOffice) — 增强 Word/Excel 原生转换
- `pandoc` — 增强标记语言转换
- `ffmpeg` — 增强音视频转换

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
│   ├── main.ts               # 窗口创建、生命周期
│   ├── ipc.ts                # IPC 处理器
│   ├── preload.ts / preload.cjs  # contextBridge API
│   └── services/
│       ├── conversionService.ts  # 核心转换逻辑
│       ├── engineHealth.ts       # 外部引擎检测
│       ├── fileImport.ts         # 文件导入
│       ├── jobRunner.ts          # 子进程管理
│       └── shellOpen.ts          # 系统文件夹
├── renderer/                 # React 渲染进程
│   ├── App.tsx               # 主组件
│   ├── state/appReducer.ts   # 状态管理
│   ├── actionAvailability.ts # 动作可用性
│   └── components/           # UI 组件
└── shared/                   # 共享代码
    ├── types.ts              # 类型定义
    ├── fileTypes.ts          # 格式映射
    ├── converters/
    │   ├── registry.ts       # 转换动作注册表
    │   └── adapters.ts       # 外部引擎命令适配
    ├── formatDetection.ts    # 格式检测
    ├── outputPaths.ts        # 输出路径
    ├── jobs.ts               # 作业队列
    └── errors.ts             # 错误码
```

## 协议

内部项目，仅供学习使用。
