import type { SupportedFormat } from "./types.js";

/**
 * 内置转换能力的唯一事实来源。
 *
 * 转换服务（main/services/conversionService.ts）的格式分发，以及渲染层
 * （renderer/actionAvailability.ts）的"动作是否可用"判定，都只依赖这里的
 * 集合与 isBuiltInConversionSupported()，避免两处各维护一套格式清单导致漂移。
 */

export const textFormats = new Set<SupportedFormat>(["txt", "markdown", "html"]);
export const wordFormats = new Set<SupportedFormat>(["doc", "docx"]);
export const spreadsheetFormats = new Set<SupportedFormat>(["xls", "xlsx", "csv"]);
export const imageFormats = new Set<SupportedFormat>(["jpg", "png", "webp", "bmp", "gif"]);
/** 媒体统一集合：音视频互转、视频提取音频、音频转码都由 ffmpeg 处理。 */
export const mediaFormats = new Set<SupportedFormat>(["m4a", "mp3", "wav", "mp4", "mov", "avi"]);

export const wordTargets = new Set<SupportedFormat>(["pdf", "html", "markdown", "txt"]);
export const spreadsheetTargets = new Set<SupportedFormat>(["pdf", "html", "txt", "csv"]);
export const textTargets = new Set<SupportedFormat>([...textFormats, "pdf"]);

export const archiveActionIds = new Set<string>([
  "compress-to-zip",
  "zip-extract",
  "compress-to-tar",
  "tar-extract",
  "rar-extract"
]);

/**
 * 判断某个 源格式 + 目标格式 + 动作 id 组合是否由内置转换器支持。
 * 该判定 = 转换服务实际能处理的集合，渲染层据此决定是否展示动作。
 */
export function isBuiltInConversionSupported(
  sourceFormat: SupportedFormat,
  targetFormat: SupportedFormat,
  actionId: string
): boolean {
  if (textFormats.has(sourceFormat) && textTargets.has(targetFormat)) return true;
  if (wordFormats.has(sourceFormat) && wordTargets.has(targetFormat)) return true;
  if (spreadsheetFormats.has(sourceFormat) && spreadsheetTargets.has(targetFormat)) return true;
  if (sourceFormat === "pdf" && (targetFormat === "txt" || targetFormat === "docx")) return true;
  if (imageFormats.has(sourceFormat) && imageFormats.has(targetFormat)) return true;
  if (mediaFormats.has(sourceFormat) && mediaFormats.has(targetFormat)) return true;
  if (archiveActionIds.has(actionId)) return true;
  return false;
}

export function isArchiveAction(actionId: string): boolean {
  return archiveActionIds.has(actionId);
}
