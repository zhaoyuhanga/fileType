import { app, dialog, ipcMain } from "electron";
import { join } from "node:path";
import { getActionById } from "../shared/converters/registry.js";
import type { FileItem, JobProgressEvent, SupportedFormat } from "../shared/types.js";
import { openFolderInShell } from "./services/shellOpen.js";
import { runConversion } from "./services/conversionService.js";
import { getEngineStatus } from "./services/engineHealth.js";
import { importPaths } from "./services/fileImport.js";

type JobOutcomeStatus = "succeeded" | "failed" | "cancelled";

interface JobOutcome {
  fileId: string;
  status: JobOutcomeStatus;
  targetFormat?: SupportedFormat;
  outputPath?: string;
  message?: string;
}

const activeJobs = new Map<string, AbortController>();

function abortActiveJob(batchId: string): void {
  activeJobs.get(batchId)?.abort();
}

function cancelOutcome(fileId: string, message = "已取消"): JobOutcome {
  return { fileId, status: "cancelled", message };
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
  ipcMain.handle("engines:status", async () => {
    return getEngineStatus();
  });
  ipcMain.handle(
    "jobs:start",
    async (
      event,
      batchId: string,
      actionId: string,
      files: FileItem[],
      outputDir: string
    ): Promise<JobOutcome[]> => {
      const controller = new AbortController();
      activeJobs.set(batchId, controller);

      const sender = event.sender;
      const emit = (payload: JobProgressEvent): void => {
        if (!sender.isDestroyed()) sender.send("jobs:event", payload);
      };

      const results: JobOutcome[] = [];
      const selected = files.filter((item) => item.selected);

      try {
        const action = getActionById(actionId);
        if (!action) {
          for (const file of selected) {
            const outcome = { fileId: file.id, status: "failed" as const, message: "未找到转换动作" };
            results.push(outcome);
            emit({ batchId, fileId: file.id, status: "failed", message: "未找到转换动作" });
          }
          return results;
        }

        for (const file of selected) {
          if (controller.signal.aborted) {
            const outcome = cancelOutcome(file.id);
            results.push(outcome);
            emit({ ...outcome, batchId });
            continue;
          }

          emit({ batchId, fileId: file.id, status: "running" });

          try {
            const converted = await runConversion({ action, file, outputDir, signal: controller.signal });
            // 若在收尾瞬间收到取消，仍如实记录已完成的转换结果。
            const outcome: JobOutcome = {
              fileId: file.id,
              status: converted.status,
              targetFormat: converted.targetFormat,
              outputPath: converted.outputPath,
              message: converted.message
            };
            results.push(outcome);
            emit({ batchId, ...outcome });
          } catch (error) {
            const outcome: JobOutcome = controller.signal.aborted
              ? cancelOutcome(file.id)
              : {
                  fileId: file.id,
                  status: "failed",
                  message: error instanceof Error ? error.message : "转换失败"
                };
            results.push(outcome);
            emit({ batchId, ...outcome });
          }
        }

        return results;
      } finally {
        activeJobs.delete(batchId);
      }
    }
  );
  ipcMain.on("jobs:cancel", (_event, batchId: string) => {
    abortActiveJob(batchId);
  });
}
