import { createRequire } from "node:module";
import type { EngineStatus } from "../../shared/types.js";

const require = createRequire(import.meta.url);

/**
 * 真实引擎状态：本项目不依赖任何外部可执行程序，转换全部由随应用打包的
 * 内置库完成。这里如实探测内置依赖是否可用，供界面显示"转换引擎"状态。
 */
export async function getEngineStatus(): Promise<EngineStatus[]> {
  const coreAvailable = probeCoreLibraries();
  const ffmpegExecutable = probeFfmpeg();

  return [
    {
      name: "内置转换核心",
      available: coreAvailable,
      details: "文档 / 表格 / 图片 / PDF / 归档转换库"
    },
    {
      name: "ffmpeg",
      available: Boolean(ffmpegExecutable),
      executable: ffmpegExecutable ?? undefined,
      details: "音视频转换（内置二进制）"
    }
  ];
}

function probeCoreLibraries(): boolean {
  // 只解析模块入口，避免仅为探测就初始化 sharp 等原生库。
  for (const library of ["sharp", "pdfkit", "mammoth", "xlsx", "jszip"]) {
    try {
      require.resolve(library);
    } catch {
      return false;
    }
  }
  return true;
}

function probeFfmpeg(): string | null {
  try {
    const resolved = require("ffmpeg-static");
    return typeof resolved === "string" && resolved.length > 0 ? resolved : null;
  } catch {
    return null;
  }
}
