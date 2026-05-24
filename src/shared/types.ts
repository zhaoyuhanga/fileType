export type FormatCategory = "document" | "image" | "audio" | "video" | "archive" | "unknown";

export type SupportedFormat =
  | "docx"
  | "doc"
  | "pdf"
  | "markdown"
  | "html"
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

export interface ConverterAction {
  id: string;
  label: string;
  sourceFormats: SupportedFormat[];
  targetFormat: SupportedFormat;
  category: FormatCategory;
  engine: "libreoffice" | "pandoc" | "pdf" | "image" | "ffmpeg" | "zip";
  experimental?: boolean;
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
