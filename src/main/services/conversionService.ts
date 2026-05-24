import { copyFile, mkdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import { createWriteStream, existsSync } from "node:fs";
import { dirname, extname, join } from "node:path";
import { tmpdir } from "node:os";
import { createRequire } from "node:module";
import type { ConverterAction, FileItem, SupportedFormat } from "../../shared/types.js";
import { buildOutputPath } from "../../shared/outputPaths.js";
import { runCommand } from "./jobRunner.js";

const require = createRequire(import.meta.url);
const mammoth = require("mammoth") as typeof import("mammoth");
const marked = require("marked") as typeof import("marked");
const TurndownService = require("turndown") as new () => { turndown: (html: string) => string };
const PDFDocument = require("pdfkit") as new (options?: Record<string, unknown>) => any;
const sharp = require("sharp") as typeof import("sharp");
const xlsx = require("xlsx") as typeof import("xlsx");
const iconv = require("iconv-lite") as typeof import("iconv-lite");
const docx = require("docx") as typeof import("docx");
const ffmpegPath = require("ffmpeg-static") as string | null;
const bmp = require("bmp-js") as { encode: (bitmap: { data: Buffer; width: number; height: number }) => { data: Buffer } };
const WordExtractor = require("word-extractor") as new () => {
  extract: (path: string) => Promise<{ getBody: () => string }>;
};

export interface ConversionInput {
  action: ConverterAction;
  file: FileItem;
  outputDir: string;
  engineRoot: string;
}

export interface ConversionResult {
  fileId: string;
  status: "succeeded" | "failed";
  targetFormat?: SupportedFormat;
  outputPath?: string;
  message?: string;
}

type PdfParserConstructor = new (options: { data: Buffer | Uint8Array }) => {
  getText: (params?: unknown) => Promise<{ text: string }>;
  destroy: () => Promise<void>;
};

const builtInTextFormats = new Set(["txt", "markdown", "html"]);
const wordFormats = new Set(["doc", "docx"]);
const spreadsheetFormats = new Set(["xls", "xlsx", "csv"]);
const imageFormats = new Set(["jpg", "png", "webp", "bmp", "gif"]);
const mediaFormats = new Set(["m4a", "mp3", "wav", "mp4", "mov", "avi"]);

export async function runConversion(input: ConversionInput): Promise<ConversionResult> {
  const outputPath = buildOutputPath({
    sourcePath: input.file.path,
    outputDir: input.outputDir,
    targetFormat: input.action.targetFormat
  });

  await mkdir(dirname(outputPath), { recursive: true });

  if (canRunBuiltInWordConversion(input)) {
    if (input.action.targetFormat === "pdf") {
      if (await tryOfficeWordToPdf(input.file.path, outputPath)) {
        return { fileId: input.file.id, status: "succeeded", targetFormat: input.action.targetFormat, outputPath };
      }

      if (input.file.format === "doc") {
        throw new Error("DOC 转 PDF 需要 Microsoft Word 或 WPS 原生导出；为避免生成乱码 PDF，已停止使用文本兜底。");
      }
    }

    await runTextualDocumentConversion(await extractWordText(input.file), input.file.name, input.action.targetFormat, outputPath);
    return { fileId: input.file.id, status: "succeeded", targetFormat: input.action.targetFormat, outputPath };
  }

  if (canRunBuiltInSpreadsheetConversion(input)) {
    await runSpreadsheetConversion(input.file, input.action.targetFormat, outputPath);
    return { fileId: input.file.id, status: "succeeded", targetFormat: input.action.targetFormat, outputPath };
  }

  if (input.file.format === "pdf" && ["txt", "docx"].includes(input.action.targetFormat)) {
    const text = await extractPdfText(input.file.path);
    if (input.action.targetFormat === "docx") await writeDocx(outputPath, cleanPdfTextForWord(text), input.file.name);
    else await writeFile(outputPath, text, "utf8");
    return { fileId: input.file.id, status: "succeeded", targetFormat: input.action.targetFormat, outputPath };
  }

  if (canRunBuiltInImageConversion(input)) {
    await runImageConversion(input.file.path, input.action.targetFormat, outputPath);
    return { fileId: input.file.id, status: "succeeded", targetFormat: input.action.targetFormat, outputPath };
  }

  if (canRunBuiltInMediaConversion(input)) {
    await runMediaConversion(input.file.path, outputPath, input.action.targetFormat);
    return { fileId: input.file.id, status: "succeeded", targetFormat: input.action.targetFormat, outputPath };
  }

  if (canRunBuiltInTextConversion(input)) {
    await runBuiltInTextConversion(input.file, input.action, outputPath);
    return { fileId: input.file.id, status: "succeeded", targetFormat: input.action.targetFormat, outputPath };
  }

  if (input.action.id === "compress-to-zip") {
    return {
      fileId: input.file.id,
      status: "failed",
      message: "ZIP 压缩需要内置压缩模块，当前版本尚未启用"
    };
  }

  return {
    fileId: input.file.id,
    status: "failed",
    outputPath,
    message: "当前格式组合尚未内置转换器"
  };
}

function canRunBuiltInTextConversion(input: ConversionInput): boolean {
  return builtInTextFormats.has(input.file.format) && [...builtInTextFormats, "pdf"].includes(input.action.targetFormat);
}

function canRunBuiltInWordConversion(input: ConversionInput): boolean {
  return wordFormats.has(input.file.format) && ["pdf", "html", "markdown", "txt"].includes(input.action.targetFormat);
}

function canRunBuiltInSpreadsheetConversion(input: ConversionInput): boolean {
  return spreadsheetFormats.has(input.file.format) && ["pdf", "html", "txt", "csv"].includes(input.action.targetFormat);
}

function canRunBuiltInImageConversion(input: ConversionInput): boolean {
  return imageFormats.has(input.file.format) && imageFormats.has(input.action.targetFormat);
}

function canRunBuiltInMediaConversion(input: ConversionInput): boolean {
  return Boolean(ffmpegPath) && mediaFormats.has(input.file.format) && mediaFormats.has(input.action.targetFormat);
}

async function runBuiltInTextConversion(file: FileItem, action: ConverterAction, outputPath: string): Promise<void> {
  const text = await readFile(file.path, "utf8");

  switch (action.targetFormat) {
    case "html":
      await writeFile(outputPath, file.format === "markdown" ? markdownToHtml(text, file.name) : textToHtml(text, file.name), "utf8");
      return;
    case "markdown":
      await writeFile(outputPath, file.format === "html" ? new TurndownService().turndown(text) : htmlToPlainText(text), "utf8");
      return;
    case "pdf":
      await writePdf(outputPath, htmlToPlainText(text));
      return;
    case "txt":
      await writeFile(outputPath, htmlToPlainText(text), "utf8");
      return;
    default:
      await copyFile(file.path, outputPath);
  }
}

async function extractWordText(file: FileItem): Promise<string> {
  if (file.format === "docx" || (await isZipBasedOfficeFile(file.path))) {
    const result = await mammoth.extractRawText({ path: file.path });
    return cleanWordText(result.value);
  }

  const fallback = await extractDocLikeText(file.path);
  if (fallback.trim()) return cleanWordText(fallback);

  try {
    const extractor = new WordExtractor();
    const document = await extractor.extract(file.path);
    const body = document.getBody();
    return cleanWordText(cleanupExtractedWordText(body));
  } catch (error) {
    const message = error instanceof Error ? error.message : "未知错误";
    throw new Error(`无法读取该 Word 文件：${message}。旧版二进制 DOC 兼容性有限，建议另存为 DOCX 后再转换。`);
  }
}

async function extractPdfText(filePath: string): Promise<string> {
  const buffer = await readFile(filePath);
  const PDFParse = loadPdfParser();
  const parser = new PDFParse({ data: buffer });

  try {
    const result = await parser.getText({ pageJoiner: "\n" } as never);
    return result.text;
  } finally {
    await parser.destroy();
  }
}

async function tryOfficeWordToPdf(sourcePath: string, outputPath: string): Promise<boolean> {
  if (process.platform !== "win32") return false;

  const tempWorkDir = join(tmpdir(), `filetype-office-${process.pid}-${Date.now()}`);
  const tempSourcePath = join(tempWorkDir, `source${extname(sourcePath) || ".doc"}`);
  const tempOutputPath = join(tempWorkDir, "result.pdf");
  const scriptPath = join(tempWorkDir, "export.ps1");
  const script = [
    "$ErrorActionPreference = 'Stop'",
    `$source = '${escapePowerShellString(tempSourcePath)}'`,
    `$target = '${escapePowerShellString(tempOutputPath)}'`,
    "$progIds = @('KWPS.Application', 'Word.Application')",
    "$word = $null",
    "$doc = $null",
    "try {",
    "  foreach ($progId in $progIds) {",
    "    try { $word = New-Object -ComObject $progId; break } catch { $word = $null }",
    "  }",
    "  if ($null -eq $word) { throw 'No Word/WPS COM application found' }",
    "  if ($word -eq $null) { throw '未找到 Microsoft Word 或 WPS 的本机转换接口' }",
    "  $word.Visible = $false",
    "  try { $word.DisplayAlerts = 0 } catch {}",
    "  $doc = $word.Documents.Open($source)",
    "  try { $doc.ExportAsFixedFormat($target, 17) } catch { $doc.SaveAs($target, 17) }",
    "  exit 0",
    "} catch {",
    "  Write-Error $_.Exception.Message",
    "  exit 1",
    "} finally {",
    "  if ($doc -ne $null) { $doc.Close($false) | Out-Null }",
    "  if ($word -ne $null) { $word.Quit() | Out-Null }",
    "}"
  ].filter((line) => !line.includes("$word -eq $null")).join("\r\n");

  try {
    await rm(tempWorkDir, { recursive: true, force: true });
    await mkdir(tempWorkDir, { recursive: true });
    await copyFile(sourcePath, tempSourcePath);
    await writeFile(scriptPath, `\uFEFF${script}`, "utf8");
    const result = await runCommand({
      plan: {
        executable: "powershell.exe",
        args: ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", scriptPath]
      },
      timeoutMs: 2 * 60 * 1000
    });

    if (result.exitCode !== 0) return false;
    if (!(await waitForStablePdf(tempOutputPath))) return false;

    await copyFile(tempOutputPath, outputPath);
    return true;
  } catch {
    return false;
  } finally {
    await rm(tempWorkDir, { recursive: true, force: true }).catch(() => undefined);
  }
}

function escapePowerShellString(value: string): string {
  return value.replace(/'/g, "''");
}

async function waitForStablePdf(filePath: string): Promise<boolean> {
  let lastSize = -1;
  let stableCount = 0;
  const deadline = Date.now() + 15000;

  while (Date.now() < deadline) {
    try {
      const info = await stat(filePath);
      const header = await readFile(filePath);
      const isPdf = header.subarray(0, 5).toString("ascii") === "%PDF-";

      if (isPdf && info.size > 1000 && info.size === lastSize) {
        stableCount += 1;
        if (stableCount >= 2) return true;
      } else {
        stableCount = 0;
      }

      lastSize = info.size;
    } catch {
      stableCount = 0;
    }

    await delay(500);
  }

  return false;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function loadPdfParser(): PdfParserConstructor {
  ensurePdfPolyfills();
  const pdfParse = require("pdf-parse") as {
    PDFParse?: PdfParserConstructor;
    default?: unknown;
  };

  if (!pdfParse.PDFParse) throw new Error("PDF parser failed to load");
  return pdfParse.PDFParse;
}

function ensurePdfPolyfills(): void {
  const globalScope = globalThis as Record<string, unknown>;

  if (globalScope.DOMMatrix && globalScope.ImageData && globalScope.Path2D) return;

  const canvas = require("@napi-rs/canvas") as {
    DOMMatrix: unknown;
    ImageData: unknown;
    Path2D: unknown;
  };

  globalScope.DOMMatrix ??= canvas.DOMMatrix;
  globalScope.ImageData ??= canvas.ImageData;
  globalScope.Path2D ??= canvas.Path2D;
}

async function isZipBasedOfficeFile(filePath: string): Promise<boolean> {
  const buffer = await readFile(filePath);
  return buffer[0] === 0x50 && buffer[1] === 0x4b;
}

async function extractDocLikeText(filePath: string): Promise<string> {
  const buffer = await readFile(filePath);
  const sample = decodeBuffer(buffer, "utf8").trimStart();

  if (/^MIME-Version:/i.test(sample) || /^Content-Type:\s*multipart\//i.test(sample)) {
    return extractMhtmlText(sample);
  }

  if (/^<(!doctype\s+html|html|body|meta|head)\b/i.test(sample)) {
    return htmlToPlainText(sample);
  }

  if (sample.startsWith("{\\rtf")) {
    return rtfToPlainText(sample);
  }

  return "";
}

function cleanupExtractedWordText(text: string): string {
  const trimmed = text.trimStart();
  if (/^MIME-Version:/i.test(trimmed) || /^Content-Type:\s*multipart\//i.test(trimmed)) {
    const cleaned = extractMhtmlText(trimmed);
    if (cleaned.trim()) return cleaned;
  }

  if (/^<(!doctype\s+html|html|body|meta|head)\b/i.test(trimmed)) {
    return htmlToPlainText(trimmed);
  }

  return text;
}

function cleanWordText(text: string): string {
  return text
    .replace(/\u0000/g, "")
    .split(/\r?\n/)
    .map((line) => line.replace(/[\u0001-\u0008\u000b\u000c\u000e-\u001f]/g, "").trimEnd())
    .filter((line) => !isMostlyUnreadable(line))
    .join("\n")
    .replace(/\n{4,}/g, "\n\n\n")
    .trim();
}

function isMostlyUnreadable(line: string): boolean {
  if (line.length < 12) return false;
  const bad = (line.match(/[□�]/g) ?? []).length;
  return bad / line.length > 0.25;
}

function extractMhtmlText(content: string): string {
  const parts = content.split(/\r?\n--[^\r\n]+/g);
  const htmlPart =
    parts.find((part) => /Content-Type:\s*text\/html/i.test(part)) ??
    parts.find((part) => /<html[\s>]/i.test(part)) ??
    "";

  if (!htmlPart) return "";

  const headerEnd = htmlPart.search(/\r?\n\r?\n/);
  const headers = headerEnd >= 0 ? htmlPart.slice(0, headerEnd) : "";
  const body = headerEnd >= 0 ? htmlPart.slice(headerEnd).trim() : htmlPart;
  const charset = headers.match(/charset="?([^";\r\n]+)"?/i)?.[1] ?? "utf8";
  const encoding = headers.match(/Content-Transfer-Encoding:\s*([^\r\n]+)/i)?.[1]?.trim().toLowerCase();
  const decoded = encoding === "quoted-printable" ? decodeQuotedPrintable(body, charset) : body;

  return htmlToPlainText(decoded)
    .replace(/^\s*MIME-Version:[\s\S]*?This is a multi-part message in MIME format\./i, "")
    .trim();
}

function decodeQuotedPrintable(input: string, charset: string): string {
  const normalized = input
    .replace(/=\r?\n/g, "")
    .replace(/\r\n/g, "\n");
  const bytes: number[] = [];

  for (let index = 0; index < normalized.length; index += 1) {
    const char = normalized[index];
    const hex = normalized.slice(index + 1, index + 3);

    if (char === "=" && /^[0-9a-fA-F]{2}$/.test(hex)) {
      bytes.push(Number.parseInt(hex, 16));
      index += 2;
      continue;
    }

    bytes.push(...Buffer.from(char));
  }

  return decodeBuffer(Buffer.from(bytes), charset);
}

function cleanPdfTextForWord(text: string): string {
  const lines = text
    .replace(/\r/g, "")
    .split("\n")
    .map((line) => normalizeExtractedLine(line))
    .filter((line) => line.length > 0)
    .filter((line) => !isPdfPageNoise(line))
    .filter((line) => !isTableOfContentsLine(line));

  return collapseRepeatedLines(lines).join("\n");
}

function normalizeExtractedLine(line: string): string {
  return line
    .replace(/--\s*\d+\s+of\s+\d+\s*--/gi, "")
    .replace(/[•●▪▫■□]/g, "")
    .replace(/\s{3,}/g, "  ")
    .trim();
}

function isPdfPageNoise(line: string): boolean {
  return (
    /^\d{1,4}$/.test(line) ||
    /^page\s+\d+\s+(of|\/)\s+\d+$/i.test(line) ||
    /^\d{1,2}\/\d{1,2}\/\d{2,4}\s+page\s+\d+\s+of\s+\d+/i.test(line) ||
    /^[-–—]?\s*\d+\s*[-–—]?$/.test(line)
  );
}

function isTableOfContentsLine(line: string): boolean {
  return (
    /\.{4,}\s*\d{1,4}$/.test(line) ||
    /…{2,}\s*\d{1,4}$/.test(line) ||
    /^[\d.]+\s+.+\s{2,}\d{1,4}$/.test(line)
  );
}

function collapseRepeatedLines(lines: string[]): string[] {
  const result: string[] = [];
  for (const line of lines) {
    if (result.at(-1) === line) continue;
    result.push(line);
  }
  return result;
}

function decodeBuffer(buffer: Buffer, charset: string): string {
  const normalized = charset.toLowerCase().replace(/[-_]/g, "");
  const encoding = normalized === "gb2312" || normalized === "gbk" || normalized === "gb18030" ? "gb18030" : charset;
  return iconv.decode(buffer, encoding);
}

function rtfToPlainText(rtf: string): string {
  return rtf
    .replace(/\\par[d]?/g, "\n")
    .replace(/\\'[0-9a-fA-F]{2}/g, "")
    .replace(/\\[a-zA-Z]+\d* ?/g, "")
    .replace(/[{}]/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

async function runTextualDocumentConversion(
  text: string,
  title: string,
  targetFormat: SupportedFormat,
  outputPath: string
): Promise<void> {
  switch (targetFormat) {
    case "html":
      await writeFile(outputPath, textToHtml(text, title), "utf8");
      return;
    case "markdown":
    case "txt":
      await writeFile(outputPath, text, "utf8");
      return;
    case "pdf":
      await writePdf(outputPath, text);
      return;
    default:
      await writeFile(outputPath, text, "utf8");
  }
}

async function runSpreadsheetConversion(file: FileItem, targetFormat: SupportedFormat, outputPath: string): Promise<void> {
  const workbook = xlsx.readFile(file.path);
  const firstSheet = workbook.Sheets[workbook.SheetNames[0]];
  const csv = xlsx.utils.sheet_to_csv(firstSheet);

  switch (targetFormat) {
    case "csv":
    case "txt":
      await writeFile(outputPath, csv, "utf8");
      return;
    case "html":
      await writeFile(outputPath, xlsx.utils.sheet_to_html(firstSheet), "utf8");
      return;
    case "pdf":
      await writePdf(outputPath, csv);
      return;
    default:
      await writeFile(outputPath, csv, "utf8");
  }
}

async function runImageConversion(sourcePath: string, targetFormat: SupportedFormat, outputPath: string): Promise<void> {
  if (targetFormat === "bmp") {
    const { data, info } = await sharp(sourcePath).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
    await writeFile(outputPath, bmp.encode({ data, width: info.width, height: info.height }).data);
    return;
  }

  const pipeline = sharp(sourcePath, { animated: false });
  if (targetFormat === "jpg") await pipeline.jpeg().toFile(outputPath);
  else if (targetFormat === "png") await pipeline.png().toFile(outputPath);
  else if (targetFormat === "webp") await pipeline.webp().toFile(outputPath);
  else if (targetFormat === "gif") await pipeline.gif().toFile(outputPath);
  else await pipeline.toFile(outputPath);
}

async function runMediaConversion(sourcePath: string, outputPath: string, targetFormat: SupportedFormat): Promise<void> {
  if (!ffmpegPath) throw new Error("ffmpeg 内置二进制缺失");

  const executable = ffmpegPath.replace("app.asar", "app.asar.unpacked");
  const audioTargets = new Set(["m4a", "mp3", "wav"]);
  const args = ["-y", "-i", sourcePath];
  if (audioTargets.has(targetFormat)) args.push("-vn");
  if (targetFormat === "m4a") args.push("-c:a", "aac");
  args.push(outputPath);

  const result = await runCommand({ plan: { executable, args }, timeoutMs: 10 * 60 * 1000 });
  if (result.exitCode !== 0) throw new Error(result.stderr || `ffmpeg 退出码 ${result.exitCode}`);
}

async function writePdf(outputPath: string, text: string): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const doc = new PDFDocument({ margin: 48 });
    const stream = createWriteStream(outputPath);
    stream.on("finish", resolve);
    stream.on("error", reject);
    doc.pipe(stream);
    applyPdfFont(doc);
    doc.fontSize(11).text(text || " ", { width: 500 });
    doc.end();
  });
}

async function writeDocx(outputPath: string, text: string, title: string): Promise<void> {
  const paragraphs = (text.trim() || title)
    .split(/\r?\n/)
    .map((line) => new docx.Paragraph({ children: [new docx.TextRun(line || " ")] }));

  const document = new docx.Document({
    creator: "万能格式转换器",
    title,
    sections: [{ children: paragraphs }]
  });

  await writeFile(outputPath, await docx.Packer.toBuffer(document));
}

function applyPdfFont(doc: any): void {
  for (const font of findPdfFontCandidates()) {
    if (!existsSync(font)) continue;

    try {
      doc.font(font);
      return;
    } catch {
      // Some Windows font files, especially variable fonts, are not accepted by PDFKit.
    }
  }

  doc.font("Helvetica");
}

function findPdfFontCandidates(): string[] {
  const candidates = [
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/Deng.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/arial.ttf"
  ];

  return candidates;
}

function textToHtml(text: string, title: string): string {
  const body = text.trim().startsWith("<") ? text : escapeHtml(text);
  const escaped = escapeHtml(text)
    .split(/\r?\n/)
    .map((line) => `<p>${line || "&nbsp;"}</p>`)
    .join("\n");

  return `<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <title>${escapeHtml(title)}</title>
  </head>
  <body>
${body === escapeHtml(text) ? escaped : body}
  </body>
</html>
`;
}

function markdownToHtml(text: string, title: string): string {
  return `<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <title>${escapeHtml(title)}</title>
  </head>
  <body>
${marked.parse(text)}
  </body>
</html>
`;
}

function htmlToPlainText(text: string): string {
  return text
    .replace(/<script[\s\S]*?<\/script>/gi, "")
    .replace(/<style[\s\S]*?<\/style>/gi, "")
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/g, " ")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&")
    .trim();
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
