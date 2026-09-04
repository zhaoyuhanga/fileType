import type { ConverterAction, FileFormat, SupportedFormat } from "../types.js";

const imageFormats: SupportedFormat[] = ["jpg", "png", "webp", "bmp", "gif"];
const videoFormats: SupportedFormat[] = ["mp4", "mov", "avi"];
const audioFormats: SupportedFormat[] = ["m4a", "mp3", "wav"];

/**
 * engine 字段为能力族分组标识（见 types.ts），实际执行一律走内置转换器，
 * 由 shared/converterCapabilities.ts 判定支持范围。
 */
export const converterActions: ConverterAction[] = [
  { id: "word-to-pdf", label: "Word 转 PDF", sourceFormats: ["docx", "doc"], targetFormat: "pdf", category: "document", engine: "word" },
  { id: "word-to-html", label: "Word 转 HTML", sourceFormats: ["docx", "doc"], targetFormat: "html", category: "document", engine: "word" },
  { id: "word-to-markdown", label: "Word 转 Markdown", sourceFormats: ["docx", "doc"], targetFormat: "markdown", category: "document", engine: "word" },
  { id: "word-to-txt", label: "Word 转 TXT", sourceFormats: ["docx", "doc"], targetFormat: "txt", category: "document", engine: "word" },
  { id: "md-to-html", label: "Markdown 转 HTML", sourceFormats: ["markdown"], targetFormat: "html", category: "document", engine: "text" },
  { id: "md-to-pdf", label: "Markdown 转 PDF", sourceFormats: ["markdown"], targetFormat: "pdf", category: "document", engine: "text" },
  { id: "md-to-txt", label: "Markdown 转 TXT", sourceFormats: ["markdown"], targetFormat: "txt", category: "document", engine: "text" },
  { id: "html-to-pdf", label: "HTML 转 PDF", sourceFormats: ["html"], targetFormat: "pdf", category: "document", engine: "text" },
  { id: "html-to-markdown", label: "HTML 转 Markdown", sourceFormats: ["html"], targetFormat: "markdown", category: "document", engine: "text" },
  { id: "html-to-txt", label: "HTML 转 TXT", sourceFormats: ["html"], targetFormat: "txt", category: "document", engine: "text" },
  { id: "txt-to-pdf", label: "TXT 转 PDF", sourceFormats: ["txt"], targetFormat: "pdf", category: "document", engine: "text" },
  { id: "txt-to-html", label: "TXT 转 HTML", sourceFormats: ["txt"], targetFormat: "html", category: "document", engine: "text" },
  { id: "txt-to-markdown", label: "TXT 转 Markdown", sourceFormats: ["txt"], targetFormat: "markdown", category: "document", engine: "text" },
  { id: "excel-to-pdf", label: "Excel 转 PDF", sourceFormats: ["xlsx", "xls"], targetFormat: "pdf", category: "document", engine: "sheet" },
  { id: "excel-to-html", label: "Excel 转 HTML", sourceFormats: ["xlsx", "xls"], targetFormat: "html", category: "document", engine: "sheet" },
  { id: "excel-to-txt", label: "Excel 转 TXT", sourceFormats: ["xlsx", "xls"], targetFormat: "txt", category: "document", engine: "sheet" },
  { id: "excel-to-csv", label: "Excel 转 CSV", sourceFormats: ["xlsx", "xls"], targetFormat: "csv", category: "document", engine: "sheet" },
  { id: "pdf-to-txt", label: "PDF 转 TXT", sourceFormats: ["pdf"], targetFormat: "txt", category: "document", engine: "pdf" },
  { id: "pdf-to-word", label: "PDF 转 Word", sourceFormats: ["pdf"], targetFormat: "docx", category: "document", engine: "pdf" },
  { id: "pdf-to-image", label: "PDF 转 PNG", sourceFormats: ["pdf"], targetFormat: "png", category: "image", engine: "pdf" },
  ...imageFormats.flatMap((sourceFormat) =>
    imageFormats
      .filter((targetFormat) => targetFormat !== sourceFormat)
      .map<ConverterAction>((targetFormat) => ({
        id: `${sourceFormat}-to-${targetFormat}`,
        label: `${sourceFormat.toUpperCase()} 转 ${targetFormat.toUpperCase()}`,
        sourceFormats: [sourceFormat],
        targetFormat,
        category: "image",
        engine: "image"
      }))
  ),
  ...videoFormats.flatMap((sourceFormat) =>
    videoFormats
      .filter((targetFormat) => targetFormat !== sourceFormat)
      .map<ConverterAction>((targetFormat) => ({
        id: `${sourceFormat}-to-${targetFormat}`,
        label: `${sourceFormat.toUpperCase()} 转 ${targetFormat.toUpperCase()}`,
        sourceFormats: [sourceFormat],
        targetFormat,
        category: "video",
        engine: "media"
      }))
  ),
  ...audioFormats.flatMap((sourceFormat) =>
    audioFormats
      .filter((targetFormat) => targetFormat !== sourceFormat)
      .map<ConverterAction>((targetFormat) => ({
        id: `${sourceFormat}-to-${targetFormat}`,
        label: `${sourceFormat.toUpperCase()} 转 ${targetFormat.toUpperCase()}`,
        sourceFormats: [sourceFormat],
        targetFormat,
        category: "audio",
        engine: "media"
      }))
  ),
  { id: "video-to-mp3", label: "提取 MP3", sourceFormats: videoFormats, targetFormat: "mp3", category: "audio", engine: "media" },
  { id: "video-to-wav", label: "提取 WAV", sourceFormats: videoFormats, targetFormat: "wav", category: "audio", engine: "media" },
  { id: "compress-to-zip", label: "压缩为 ZIP", sourceFormats: ["docx", "doc", "pdf", "markdown", "html", "txt", "xlsx", "xls", "jpg", "png", "webp", "bmp", "gif", "mp4", "mov", "avi", "m4a", "mp3", "wav", "rar", "tar"], targetFormat: "zip", category: "archive", engine: "archive" },
  { id: "zip-extract", label: "ZIP 解压", sourceFormats: ["zip"], targetFormat: "zip", category: "archive", engine: "archive" },
  { id: "compress-to-tar", label: "压缩为 TAR", sourceFormats: ["docx", "doc", "pdf", "markdown", "html", "txt", "xlsx", "xls", "jpg", "png", "webp", "bmp", "gif", "mp4", "mov", "avi", "m4a", "mp3", "wav"], targetFormat: "tar", category: "archive", engine: "archive" },
  { id: "tar-extract", label: "TAR 解压", sourceFormats: ["tar"], targetFormat: "tar", category: "archive", engine: "archive" },
  { id: "rar-extract", label: "RAR 解压", sourceFormats: ["rar"], targetFormat: "rar", category: "archive", engine: "archive" }
];

export function getConverterActionsForFormat(format: FileFormat): ConverterAction[] {
  if (format === "unknown") return [];
  return converterActions.filter((action) => action.sourceFormats.includes(format));
}

export function getCommonActionsForFormats(formats: FileFormat[]): ConverterAction[] {
  const selectable = formats.filter((format): format is SupportedFormat => format !== "unknown");
  if (selectable.length === 0) return [];

  return getConverterActionsForFormat(selectable[0]).filter((action) =>
    selectable.slice(1).every((format) =>
      getConverterActionsForFormat(format).some(
        (candidate) =>
          candidate.targetFormat === action.targetFormat &&
          candidate.engine === action.engine &&
          candidate.category === action.category
      )
    )
  );
}

export function getActionById(actionId: string): ConverterAction | undefined {
  return converterActions.find((action) => action.id === actionId);
}

export function getActionsForCategory(category: ConverterAction["category"]): ConverterAction[] {
  return converterActions.filter((action) => action.category === category);
}
