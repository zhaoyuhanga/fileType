import { DOC_STREAM_SCHEME } from "../shared/constants";
import type { FileFormat } from "../shared/types";

/** 文档查看/编辑模块当前支持的格式。 */
const DOC_VIEW_FORMATS = new Set<FileFormat>(["txt", "markdown", "json", "mp4"]);

export function supportsDocumentView(format: FileFormat): boolean {
  return DOC_VIEW_FORMATS.has(format);
}

/** 表格/徽章使用的展示名：md → MD，json → JSON，unknown 保持小写。 */
export function formatLabel(format: FileFormat | string): string {
  if (format === "unknown") return format;
  const short = format === "markdown" ? "md" : format;
  return short.toUpperCase();
}

/** 文件大小的人类可读格式。 */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

/** 把本地文件绝对路径拼成 docstream:// 协议 URL，供 <video> 播放。 */
export function toDocStreamUrl(filePath: string): string {
  return `${DOC_STREAM_SCHEME}://file/?p=${encodeURIComponent(filePath)}`;
}
