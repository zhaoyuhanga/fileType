import { copyFile, readFile, stat, writeFile } from "node:fs/promises";
import { basename, extname } from "node:path";
import { createRequire } from "node:module";
import { TextDecoder } from "node:util";
import { getCategory, getFormatFromExtension } from "../../shared/fileTypes.js";
import type { DocumentKind, FileFormat, LocalDocument } from "../../shared/types.js";

const require = createRequire(import.meta.url);
const iconv = require("iconv-lite") as typeof import("iconv-lite");

/** 可读写编辑的纯文本格式（txt / markdown / json）。 */
const EDITABLE_TEXT_FORMATS = new Set<FileFormat>(["txt", "markdown", "json"]);
/** 可直接播放预览的媒体格式（暂只开放 mp4）。 */
const VIEWABLE_MEDIA_FORMATS = new Set<FileFormat>(["mp4"]);

/** 文本预览大小上限，防止大文件把渲染进程拖垮。 */
export const MAX_TEXT_PREVIEW_BYTES = 8 * 1024 * 1024;

export function isEditableTextFormat(format: FileFormat): boolean {
  return EDITABLE_TEXT_FORMATS.has(format);
}

export function isViewableMediaFormat(format: FileFormat): boolean {
  return VIEWABLE_MEDIA_FORMATS.has(format);
}

/**
 * 读取本地文档：
 * - 文本类（txt/markdown/json）：自动识别 UTF-8 / UTF-16 BOM / GBK，返回解码内容；
 * - 媒体类（mp4）：不读正文，仅返回元信息（供渲染层走 docstream 协议播放）。
 */
export async function readDocument(filePath: string): Promise<LocalDocument> {
  const info = await stat(filePath);
  if (!info.isFile()) throw new Error("目标不是可读取的文件");

  const extension = extname(filePath).replace(/^\./, "").toLowerCase();
  const format = getFormatFromExtension(extension);

  let kind: DocumentKind;
  if (isEditableTextFormat(format)) {
    kind = "text";
  } else if (isViewableMediaFormat(format)) {
    kind = "media";
  } else {
    throw new Error(`暂不支持预览 ${extension || "未知"} 格式的文件`);
  }

  const doc: LocalDocument = {
    path: filePath,
    name: basename(filePath),
    extension,
    format,
    category: getCategory(format),
    sizeBytes: info.size,
    kind
  };

  if (kind === "media") {
    return doc;
  }

  const buffer = await readFile(filePath);
  if (buffer.length > MAX_TEXT_PREVIEW_BYTES) {
    throw new Error(`文本文件超过 ${Math.floor(MAX_TEXT_PREVIEW_BYTES / 1024 / 1024)} MB，暂不支持预览`);
  }

  const { text, encoding } = decodeTextBuffer(buffer);
  return { ...doc, content: text, encoding };
}

/**
 * 智能解码文本：优先 BOM，其次严格 UTF-8，失败回退 GB18030（兼容常见中文旧文件）。
 */
function decodeTextBuffer(buffer: Buffer): { text: string; encoding: string } {
  if (buffer.length >= 3 && buffer[0] === 0xef && buffer[1] === 0xbb && buffer[2] === 0xbf) {
    return { text: buffer.subarray(3).toString("utf8"), encoding: "UTF-8 (BOM)" };
  }
  if (buffer.length >= 2 && buffer[0] === 0xff && buffer[1] === 0xfe) {
    return { text: buffer.subarray(2).toString("utf16le"), encoding: "UTF-16 LE (BOM)" };
  }
  if (buffer.length >= 2 && buffer[0] === 0xfe && buffer[1] === 0xff) {
    return { text: buffer.subarray(2).swap16().toString("utf16le"), encoding: "UTF-16 BE (BOM)" };
  }

  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(buffer);
    return { text, encoding: "UTF-8" };
  } catch {
    return { text: iconv.decode(buffer, "gb18030"), encoding: "GB18030" };
  }
}

/** 保存纯文本文档（统一写 UTF-8）。 */
export async function writeTextDocument(filePath: string, content: string): Promise<void> {
  if (typeof content !== "string") throw new Error("保存内容无效");
  await writeFile(filePath, content, "utf8");
}

/** 按字节复制媒体/二进制文档（用于"另存为"）。 */
export async function copyDocument(sourcePath: string, targetPath: string): Promise<void> {
  await copyFile(sourcePath, targetPath);
}

export function toUserMessage(error: unknown): string {
  return error instanceof Error ? error.message : "未知错误";
}
