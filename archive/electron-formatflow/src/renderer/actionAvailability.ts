import type { ConverterAction } from "../shared/types.js";
import { isBuiltInConversionSupported } from "../shared/converterCapabilities.js";

/**
 * 动作可用性与转换服务共用同一能力清单（converterCapabilities.ts），
 * 不再在渲染层重复维护一套格式集合。
 */
export function isActionAvailable(action: ConverterAction): boolean {
  if (action.sourceFormats.length === 0) return false;
  return action.sourceFormats.every((format) =>
    isBuiltInConversionSupported(format, action.targetFormat, action.id)
  );
}

export function getActionUnavailableReason(action: ConverterAction): string | undefined {
  if (isActionAvailable(action)) return undefined;
  if (action.id === "pdf-to-image") return "PDF 转图片待增强";
  return "暂不支持此转换";
}
