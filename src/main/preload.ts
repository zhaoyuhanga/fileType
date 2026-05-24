import { contextBridge, ipcRenderer } from "electron";
import type { ConverterAction, EngineStatus, FileItem, SupportedFormat } from "../shared/types.js";

contextBridge.exposeInMainWorld("formatFlow", {
  version: "0.1.0",
  importPaths: (paths: string[]) => ipcRenderer.invoke("files:import", paths) as Promise<FileItem[]>,
  pickFiles: () => ipcRenderer.invoke("files:pick") as Promise<FileItem[]>,
  pickFolders: () => ipcRenderer.invoke("folders:pick") as Promise<FileItem[]>,
  pickOutputDirectory: () => ipcRenderer.invoke("output:pick") as Promise<string>,
  getDefaultOutputDir: () => ipcRenderer.invoke("output:default") as Promise<string>,
  openOutputDirectory: (outputDir: string) => ipcRenderer.invoke("output:open", outputDir) as Promise<string>,
  getActionsForFormats: (formats: SupportedFormat[]) =>
    ipcRenderer.invoke("actions:for-formats", formats) as Promise<ConverterAction[]>,
  getEngineStatus: () => ipcRenderer.invoke("engines:status") as Promise<EngineStatus[]>,
  startJobs: (actionId: string, files: FileItem[], outputDir: string) =>
    ipcRenderer.invoke("jobs:start", actionId, files, outputDir) as Promise<
      Array<{ fileId: string; status: "succeeded" | "failed"; outputPath?: string; message?: string }>
    >
});
