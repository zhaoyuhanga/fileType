import { extname, join, parse } from "node:path";
import type { SupportedFormat } from "./types.js";

export interface OutputPathOptions {
  sourcePath: string;
  outputDir: string;
  targetFormat: SupportedFormat;
  collisionIndex?: number;
}

export function buildOutputPath({
  sourcePath,
  outputDir,
  targetFormat,
  collisionIndex = 0
}: OutputPathOptions): string {
  const parsed = parse(sourcePath);
  const suffix = collisionIndex > 0 ? ` (${collisionIndex + 1})` : "";
  const extension = targetFormat === "markdown" ? ".md" : `.${targetFormat}`;
  const fileName = `${parsed.name}${suffix}${extension}`;
  return join(outputDir, fileName);
}

export function replaceExtension(filePath: string, targetFormat: SupportedFormat): string {
  const parsed = parse(filePath);
  const extension = targetFormat === "markdown" ? ".md" : `.${targetFormat}`;
  return join(parsed.dir, `${parsed.name}${extension}`);
}

export function hasSameExtension(filePath: string, targetFormat: SupportedFormat): boolean {
  return extname(filePath).toLowerCase() === (targetFormat === "markdown" ? ".md" : `.${targetFormat}`);
}
