import type { ConverterAction, FileFormat, SupportedFormat } from "../types.js";

const imageFormats: SupportedFormat[] = ["jpg", "png", "webp", "bmp", "gif"];
const videoFormats: SupportedFormat[] = ["mp4", "mov", "avi"];
const audioFormats: SupportedFormat[] = ["m4a", "mp3", "wav"];

export const converterActions: ConverterAction[] = [
  { id: "word-to-pdf", label: "Word 转 PDF", sourceFormats: ["docx", "doc"], targetFormat: "pdf", category: "document", engine: "libreoffice" },
  { id: "word-to-html", label: "Word 转 HTML", sourceFormats: ["docx", "doc"], targetFormat: "html", category: "document", engine: "libreoffice" },
  { id: "word-to-markdown", label: "Word 转 Markdown", sourceFormats: ["docx", "doc"], targetFormat: "markdown", category: "document", engine: "pandoc" },
  { id: "word-to-txt", label: "Word 转 TXT", sourceFormats: ["docx", "doc"], targetFormat: "txt", category: "document", engine: "libreoffice" },
  { id: "md-to-html", label: "Markdown 转 HTML", sourceFormats: ["markdown"], targetFormat: "html", category: "document", engine: "pandoc" },
  { id: "md-to-pdf", label: "Markdown 转 PDF", sourceFormats: ["markdown"], targetFormat: "pdf", category: "document", engine: "pandoc" },
  { id: "md-to-txt", label: "Markdown 转 TXT", sourceFormats: ["markdown"], targetFormat: "txt", category: "document", engine: "pandoc" },
  { id: "html-to-pdf", label: "HTML 转 PDF", sourceFormats: ["html"], targetFormat: "pdf", category: "document", engine: "pandoc" },
  { id: "html-to-markdown", label: "HTML 转 Markdown", sourceFormats: ["html"], targetFormat: "markdown", category: "document", engine: "pandoc" },
  { id: "html-to-txt", label: "HTML 转 TXT", sourceFormats: ["html"], targetFormat: "txt", category: "document", engine: "pandoc" },
  { id: "txt-to-pdf", label: "TXT 转 PDF", sourceFormats: ["txt"], targetFormat: "pdf", category: "document", engine: "pandoc" },
  { id: "txt-to-html", label: "TXT 转 HTML", sourceFormats: ["txt"], targetFormat: "html", category: "document", engine: "pandoc" },
  { id: "txt-to-markdown", label: "TXT 转 Markdown", sourceFormats: ["txt"], targetFormat: "markdown", category: "document", engine: "pandoc" },
  { id: "excel-to-pdf", label: "Excel 转 PDF", sourceFormats: ["xlsx", "xls"], targetFormat: "pdf", category: "document", engine: "libreoffice" },
  { id: "excel-to-html", label: "Excel 转 HTML", sourceFormats: ["xlsx", "xls"], targetFormat: "html", category: "document", engine: "libreoffice" },
  { id: "excel-to-txt", label: "Excel 转 TXT", sourceFormats: ["xlsx", "xls"], targetFormat: "txt", category: "document", engine: "libreoffice" },
  { id: "excel-to-csv", label: "Excel 转 CSV", sourceFormats: ["xlsx", "xls"], targetFormat: "csv", category: "document", engine: "libreoffice" },
  { id: "pdf-to-txt", label: "PDF 转 TXT", sourceFormats: ["pdf"], targetFormat: "txt", category: "document", engine: "pdf", experimental: true },
  { id: "pdf-to-word", label: "PDF 转 Word", sourceFormats: ["pdf"], targetFormat: "docx", category: "document", engine: "pdf", experimental: true },
  { id: "pdf-to-image", label: "PDF 转 PNG", sourceFormats: ["pdf"], targetFormat: "png", category: "image", engine: "pdf", experimental: true },
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
        engine: "ffmpeg"
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
        engine: "ffmpeg"
      }))
  ),
  { id: "video-to-mp3", label: "提取 MP3", sourceFormats: videoFormats, targetFormat: "mp3", category: "audio", engine: "ffmpeg" },
  { id: "video-to-wav", label: "提取 WAV", sourceFormats: videoFormats, targetFormat: "wav", category: "audio", engine: "ffmpeg" },
  { id: "compress-to-zip", label: "压缩为 ZIP", sourceFormats: ["docx", "doc", "pdf", "markdown", "html", "txt", "xlsx", "xls", "jpg", "png", "webp", "bmp", "gif", "mp4", "mov", "avi", "m4a", "mp3", "wav"], targetFormat: "zip", category: "archive", engine: "zip" }
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
