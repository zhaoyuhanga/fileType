const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("formatFlow", {
  version: "0.1.0",
  importPaths: (paths) => ipcRenderer.invoke("files:import", paths),
  pickFiles: () => ipcRenderer.invoke("files:pick"),
  pickFolders: () => ipcRenderer.invoke("folders:pick"),
  pickOutputDirectory: () => ipcRenderer.invoke("output:pick"),
  getDefaultOutputDir: () => ipcRenderer.invoke("output:default"),
  openOutputDirectory: (outputDir) => ipcRenderer.invoke("output:open", outputDir),
  getActionsForFormats: (formats) => ipcRenderer.invoke("actions:for-formats", formats),
  getEngineStatus: () => ipcRenderer.invoke("engines:status"),
  startJobs: (actionId, files, outputDir) => ipcRenderer.invoke("jobs:start", actionId, files, outputDir)
});
