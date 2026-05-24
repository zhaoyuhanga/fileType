import { app, dialog, ipcMain } from "electron";
import { join } from "node:path";
import { getActionById, getCommonActionsForFormats } from "../shared/converters/registry.js";
import type { FileItem, SupportedFormat } from "../shared/types.js";
import { openFolderInShell } from "./services/shellOpen.js";
import { runConversion } from "./services/conversionService.js";
import { getEngineStatus } from "./services/engineHealth.js";
import { importPaths } from "./services/fileImport.js";

function getEngineRoot(): string {
  return app.isPackaged ? join(process.resourcesPath, "engines") : join(app.getAppPath(), "resources", "engines");
}

export function registerIpcHandlers(): void {
  ipcMain.handle("files:import", async (_event, paths: string[]) => importPaths(paths));
  ipcMain.handle("files:pick", async () => {
    const result = await dialog.showOpenDialog({
      properties: ["openFile", "multiSelections"]
    });

    if (result.canceled) return [];
    return importPaths(result.filePaths);
  });
  ipcMain.handle("folders:pick", async () => {
    const result = await dialog.showOpenDialog({
      properties: ["openDirectory", "multiSelections"]
    });

    if (result.canceled) return [];
    return importPaths(result.filePaths);
  });
  ipcMain.handle("output:pick", async () => {
    const result = await dialog.showOpenDialog({
      properties: ["openDirectory", "createDirectory"]
    });

    if (result.canceled) return "";
    return result.filePaths[0] ?? "";
  });
  ipcMain.handle("output:default", async () => {
    return join(app.getPath("desktop"), "FormatFlow_Output");
  });
  ipcMain.handle("output:open", async (_event, outputDir: string) => {
    return openFolderInShell(outputDir || join(app.getPath("desktop"), "FormatFlow_Output"));
  });
  ipcMain.handle("actions:for-formats", async (_event, formats: SupportedFormat[]) => {
    return getCommonActionsForFormats(formats);
  });
  ipcMain.handle("engines:status", async () => {
    return getEngineStatus(getEngineRoot());
  });
  ipcMain.handle(
    "jobs:start",
    async (
      _event,
      actionId: string,
      files: FileItem[],
      outputDir: string
    ): Promise<Array<{ fileId: string; status: "succeeded" | "failed"; targetFormat?: SupportedFormat; outputPath?: string; message?: string }>> => {
      const action = getActionById(actionId);
      if (!action) {
        return files.map((file) => ({
          fileId: file.id,
          status: "failed" as const,
          message: "未找到转换动作"
        }));
      }

      const engineRoot = getEngineRoot();
      const results: Array<{ fileId: string; status: "succeeded" | "failed"; targetFormat?: SupportedFormat; outputPath?: string; message?: string }> = [];

      for (const file of files.filter((item) => item.selected)) {
        try {
          results.push(await runConversion({ action, file, outputDir, engineRoot }));
        } catch (error) {
          results.push({
            fileId: file.id,
            status: "failed",
            message: error instanceof Error ? error.message : "转换失败"
          });
        }
      }

      return results;
    }
  );
}
