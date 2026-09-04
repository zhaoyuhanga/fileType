import { extname, basename } from "node:path";
import { getCategory, getFormatFromExtension, normalizeExtension } from "./fileTypes.js";
import type { FileFormat, FormatCategory } from "./types.js";

export interface FormatDetectionResult {
  format: FileFormat;
  category: FormatCategory;
  extension: string;
}

export function detectFormatFromPath(filePath: string): FormatDetectionResult {
  const extension = normalizeExtension(extname(filePath));
  const format = getFormatFromExtension(extension);
  return {
    format,
    category: getCategory(format),
    extension
  };
}

export function detectFormatFromName(fileName: string): FormatDetectionResult {
  return detectFormatFromPath(fileName);
}

export function getBaseNameWithoutExtension(filePath: string): string {
  const name = basename(filePath);
  const dotIndex = name.lastIndexOf(".");
  return dotIndex > 0 ? name.slice(0, dotIndex) : name;
}
