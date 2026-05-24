import type { ConverterAction, EngineStatus, FileFormat } from "../shared/types.js";

const builtInTextFormats = new Set<FileFormat>(["txt", "markdown", "html"]);
const builtInWordFormats = new Set<FileFormat>(["doc", "docx"]);
const builtInSpreadsheetFormats = new Set<FileFormat>(["xls", "xlsx", "csv"]);
const builtInImageFormats = new Set<FileFormat>(["jpg", "png", "webp", "bmp", "gif"]);
const builtInMediaFormats = new Set<FileFormat>(["m4a", "mp3", "wav", "mp4", "mov", "avi"]);

export function isActionAvailable(action: ConverterAction, _engineStatus: EngineStatus[]): boolean {
  if (action.id === "compress-to-zip" || action.id === "pdf-to-image") return false;
  if (isBuiltInAction(action)) return true;

  return false;
}

export function getActionUnavailableReason(action: ConverterAction, engineStatus: EngineStatus[]): string | undefined {
  if (isActionAvailable(action, engineStatus)) return undefined;

  if (action.id === "pdf-to-image") return "PDF 转图片待增强";
  if (action.id === "compress-to-zip") return "ZIP 压缩待增强";
  return `暂不支持 ${getEngineLabel(action.engine)}`;
}

export function isBuiltInTextAction(action: ConverterAction): boolean {
  return (
    (builtInTextFormats.has(action.targetFormat) || action.targetFormat === "pdf") &&
    action.sourceFormats.every((format) => builtInTextFormats.has(format))
  );
}

export function isBuiltInAction(action: ConverterAction): boolean {
  if (isBuiltInTextAction(action)) return true;

  if (action.sourceFormats.every((format) => builtInWordFormats.has(format))) {
    return ["pdf", "html", "markdown", "txt"].includes(action.targetFormat);
  }

  if (action.sourceFormats.every((format) => builtInSpreadsheetFormats.has(format))) {
    return ["pdf", "html", "txt", "csv"].includes(action.targetFormat);
  }

  if (action.sourceFormats.every((format) => builtInImageFormats.has(format))) {
    return builtInImageFormats.has(action.targetFormat);
  }

  if (action.sourceFormats.every((format) => builtInMediaFormats.has(format))) {
    return builtInMediaFormats.has(action.targetFormat);
  }

  if (action.id === "pdf-to-txt" || action.id === "pdf-to-word") return true;

  return false;
}

function getEngineLabel(engine: ConverterAction["engine"]): string {
  switch (engine) {
    case "libreoffice":
      return "LibreOffice";
    case "pandoc":
      return "pandoc";
    case "ffmpeg":
      return "ffmpeg";
    case "pdf":
      return "PDF 引擎";
    case "image":
      return "图片引擎";
    case "zip":
      return "ZIP 引擎";
    default:
      return engine;
  }
}
