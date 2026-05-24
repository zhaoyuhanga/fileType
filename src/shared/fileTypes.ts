import type { FileFormat, FormatCategory, SupportedFormat } from "./types.js";

export const formatCategories: Record<SupportedFormat, FormatCategory> = {
  docx: "document",
  doc: "document",
  pdf: "document",
  markdown: "document",
  html: "document",
  txt: "document",
  xlsx: "document",
  xls: "document",
  csv: "document",
  jpg: "image",
  png: "image",
  webp: "image",
  bmp: "image",
  gif: "image",
  mp4: "video",
  mov: "video",
  avi: "video",
  m4a: "audio",
  mp3: "audio",
  wav: "audio",
  zip: "archive"
};

const extensionAliases: Record<string, SupportedFormat> = {
  md: "markdown",
  htm: "html",
  jpeg: "jpg"
};

export function normalizeExtension(extension: string): string {
  return extension.trim().replace(/^\./, "").toLowerCase();
}

export function getCategory(format: FileFormat): FormatCategory {
  if (format === "unknown") return "unknown";
  return formatCategories[format];
}

export function getFormatFromExtension(extension: string): FileFormat {
  const normalized = normalizeExtension(extension);
  if (!normalized) return "unknown";
  return extensionAliases[normalized] ?? (normalized in formatCategories ? (normalized as SupportedFormat) : "unknown");
}

export function getDisplayFormat(format: FileFormat): string {
  return format === "markdown" ? "md" : format;
}
