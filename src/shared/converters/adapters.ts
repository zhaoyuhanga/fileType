import { join } from "node:path";
import { platform } from "node:process";
import type { CommandPlan, ConverterAction, FileItem } from "../types.js";

export interface BuildCommandInput {
  action: ConverterAction;
  file: FileItem;
  outputDir: string;
  engineRoot: string;
}

function resolveEngineBinary(engineRoot: string, engineName: ConverterAction["engine"]): string {
  const binaryName =
    engineName === "libreoffice"
      ? "soffice"
      : engineName === "pandoc"
        ? "pandoc"
        : engineName === "ffmpeg"
          ? "ffmpeg"
          : engineName === "zip"
            ? "zip"
            : "tool";
  return join(engineRoot, platform === "win32" ? `${binaryName}.exe` : binaryName);
}

export function buildCommandPlan(input: BuildCommandInput): CommandPlan {
  const executable = resolveEngineBinary(input.engineRoot, input.action.engine);

  switch (input.action.engine) {
    case "libreoffice":
      return {
        executable,
        args: ["--headless", "--convert-to", input.action.targetFormat, "--outdir", input.outputDir, input.file.path]
      };
    case "pandoc":
      return {
        executable,
        args: [input.file.path, "-o", join(input.outputDir, buildOutputFileName(input.file, input.action.targetFormat))]
      };
    case "pdf":
      return {
        executable,
        args: [input.file.path, join(input.outputDir, buildOutputFileName(input.file, input.action.targetFormat))]
      };
    case "image":
      return {
        executable,
        args: [input.file.path, join(input.outputDir, buildOutputFileName(input.file, input.action.targetFormat))]
      };
    case "ffmpeg":
      return {
        executable,
        args: ["-i", input.file.path, join(input.outputDir, buildOutputFileName(input.file, input.action.targetFormat))]
      };
    case "zip":
      return {
        executable,
        args: ["-r", join(input.outputDir, buildOutputFileName(input.file, input.action.targetFormat)), input.file.path]
      };
    default:
      return {
        executable,
        args: [input.file.path]
      };
  }
}

export function buildOutputFileName(file: FileItem, targetFormat: ConverterAction["targetFormat"]): string {
  const name = file.name.replace(/\.[^.]+$/, "");
  const extension = targetFormat === "markdown" ? "md" : targetFormat;
  return `${name}.${extension}`;
}
