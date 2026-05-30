import { access, readdir } from "node:fs/promises";
import { join } from "node:path";
import { platform } from "node:process";
import type { EngineStatus } from "../../shared/types.js";

async function probeBinary(executablePath: string, name: string): Promise<EngineStatus> {
  try {
    await access(executablePath);
    return { name, available: true, executable: executablePath };
  } catch (error) {
    return {
      name,
      available: false,
      executable: executablePath,
      details: error instanceof Error ? error.message : "missing"
    };
  }
}

export async function getEngineStatus(engineRoot: string): Promise<EngineStatus[]> {
  const suffix = platform === "win32" ? ".exe" : "";
  return Promise.all([
    probeBinary(join(engineRoot, `soffice${suffix}`), "libreoffice"),
    probeBinary(join(engineRoot, `pandoc${suffix}`), "pandoc"),
    probeBinary(join(engineRoot, `ffmpeg${suffix}`), "ffmpeg")
  ]);
}

export async function listEngineRootContents(engineRoot: string): Promise<string[]> {
  try {
    return await readdir(engineRoot);
  } catch {
    return [];
  }
}
