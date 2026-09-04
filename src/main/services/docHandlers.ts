import { dialog, ipcMain } from "electron";
import { basename, dirname, join } from "node:path";
import type { DocumentReadResult, DocumentSaveResult } from "../../shared/types.js";
import { copyDocument, readDocument, toUserMessage, writeTextDocument } from "./documentService.js";

/**
 * 本地文档查看/编辑的 IPC 处理器。
 * 统一返回 { ok, ... } 信封（见 shared/types.ts 的 Document*Result），
 * 失败时 error 为面向用户的中文提示。
 */
export function registerDocumentIpc(): void {
  ipcMain.handle("docs:read", async (_event, filePath: string): Promise<DocumentReadResult> => {
    try {
      const doc = await readDocument(filePath);
      return { ok: true, doc };
    } catch (error) {
      return { ok: false, error: toUserMessage(error) };
    }
  });

  ipcMain.handle("docs:save", async (_event, filePath: string, content: string): Promise<DocumentSaveResult> => {
    try {
      if (typeof filePath !== "string" || !filePath) throw new Error("保存路径无效");
      await writeTextDocument(filePath, content);
      return { ok: true, path: filePath };
    } catch (error) {
      return { ok: false, error: toUserMessage(error) };
    }
  });

  ipcMain.handle(
    "docs:saveAs",
    async (_event, sourcePath: string, suggestedName: string, content: string | null): Promise<DocumentSaveResult> => {
      try {
        if (typeof sourcePath !== "string" || !sourcePath) throw new Error("源文件路径无效");

        const result = await dialog.showSaveDialog({
          title: "另存为",
          defaultPath: join(dirname(sourcePath), suggestedName || basename(sourcePath))
        });
        if (result.canceled || !result.filePath) {
          return { ok: false, canceled: true };
        }

        // 文本：写入新内容；媒体/二进制：整体复制原文件。
        if (typeof content === "string") {
          await writeTextDocument(result.filePath, content);
        } else {
          await copyDocument(sourcePath, result.filePath);
        }
        return { ok: true, path: result.filePath };
      } catch (error) {
        return { ok: false, error: toUserMessage(error) };
      }
    }
  );
}
