import { access, readdir, stat } from "node:fs/promises";
import { extname, join, basename } from "node:path";
import { detectFormatFromPath } from "../../shared/formatDetection.js";
import { getCategory } from "../../shared/fileTypes.js";
import type { FileItem } from "../../shared/types.js";

const FILE_ACCESS = 0;

async function walkPath(targetPath: string): Promise<string[]> {
  const result = await stat(targetPath);
  if (result.isFile()) return [targetPath];
  if (!result.isDirectory()) return [];

  const entries = await readdir(targetPath, { withFileTypes: true });
  const children = await Promise.all(
    entries.map((entry) => walkPath(join(targetPath, entry.name)).catch(() => [] as string[]))
  );
  return children.flat();
}

export async function importPaths(paths: string[]): Promise<FileItem[]> {
  const fileGroups = await Promise.all(paths.map((targetPath) => walkPath(targetPath)));
  const files = fileGroups.flat();
  const unique = Array.from(new Set(files));

  const items = await Promise.all(
    unique.map(async (filePath, index) => {
      const info = await stat(filePath);
      const detection = detectFormatFromPath(filePath);
      const extension = extname(filePath).replace(/^\./, "").toLowerCase();
      return {
        id: `${Date.now()}-${index}-${basename(filePath)}`,
        path: filePath,
        name: basename(filePath),
        extension,
        format: detection.format,
        category: detection.category ?? getCategory(detection.format),
        sizeBytes: info.size,
        selected: true,
        status: "queued" as const,
        progress: 0
      } satisfies FileItem;
    })
  );

  return items.sort((a, b) => a.name.localeCompare(b.name, "zh-Hans-CN"));
}

export async function canAccessPath(targetPath: string): Promise<boolean> {
  try {
    await access(targetPath, FILE_ACCESS);
    return true;
  } catch {
    return false;
  }
}
