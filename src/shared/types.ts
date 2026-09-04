export type FormatCategory = "document" | "image" | "audio" | "video" | "archive" | "unknown";

export type SupportedFormat =
  | "docx"
  | "doc"
  | "pdf"
  | "markdown"
  | "html"
  | "json"
  | "txt"
  | "xlsx"
  | "xls"
  | "csv"
  | "jpg"
  | "png"
  | "webp"
  | "bmp"
  | "gif"
  | "mp4"
  | "m4a"
  | "mp3"
  | "wav"
  | "mov"
  | "avi"
  | "rar"
  | "tar"
  | "zip";

export type UnknownFormat = "unknown";
export type FileFormat = SupportedFormat | UnknownFormat;

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface FileItem {
  id: string;
  path: string;
  name: string;
  extension: string;
  format: FileFormat;
  category: FormatCategory;
  sizeBytes: number;
  selected: boolean;
  status: JobStatus;
  progress: number;
  errorMessage?: string;
  outputPath?: string;
  outputFormat?: SupportedFormat;
}

/**
 * engine 只是动作的"内置能力族"分组标识（同源文件家族共享同一值，用于求公共动作，
 * 同时用于把"可执行动作"与转换服务的分发保持一致）。它不再对应任何外部可执行程序——
 * 转换一律由内置转换器按文件格式分发，见 shared/converterCapabilities.ts。
 */
export type ConverterEngine = "word" | "sheet" | "text" | "pdf" | "image" | "media" | "archive";

export interface ConverterAction {
  id: string;
  label: string;
  sourceFormats: SupportedFormat[];
  targetFormat: SupportedFormat;
  category: FormatCategory;
  engine: ConverterEngine;
}

export interface CommandPlan {
  executable: string;
  args: string[];
  cwd?: string;
  env?: Record<string, string>;
}

export interface EngineStatus {
  name: string;
  available: boolean;
  executable?: string;
  details?: string;
}

/** 主进程在批处理每个文件时通过 jobs:event 推送的实时进度事件。 */
export interface JobProgressEvent {
  batchId: string;
  fileId: string;
  status: "running" | "succeeded" | "failed" | "cancelled";
  targetFormat?: SupportedFormat;
  outputPath?: string;
  message?: string;
}

/** 本地文档查看/编辑模块：文档类别。 */
export type DocumentKind = "text" | "media";

/** 主进程 docs:read 返回的本地文档快照。 */
export interface LocalDocument {
  path: string;
  name: string;
  extension: string;
  format: FileFormat;
  category: FormatCategory;
  sizeBytes: number;
  kind: DocumentKind;
  /** 文本文档解码后的正文（媒体文档无此字段）。 */
  content?: string;
  /** 文本解码识别出的编码（如 UTF-8 / GB18030）。 */
  encoding?: string;
}

/** 文档 IPC 统一结果约定：{ ok, error? }，失败时 error 为面向用户的中文提示。 */
export type DocumentReadResult = { ok: true; doc: LocalDocument } | { ok: false; error: string };

export type DocumentSaveResult =
  | { ok: true; path?: string }
  | { ok: false; canceled?: boolean; error?: string };
