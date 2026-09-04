const { contextBridge, ipcRenderer, webUtils } = require("electron");

contextBridge.exposeInMainWorld("formatFlow", {
  version: "0.2.0",
  importPaths: (paths) => ipcRenderer.invoke("files:import", paths),
  pickFiles: () => ipcRenderer.invoke("files:pick"),
  pickFolders: () => ipcRenderer.invoke("folders:pick"),
  pickOutputDirectory: () => ipcRenderer.invoke("output:pick"),
  getDefaultOutputDir: () => ipcRenderer.invoke("output:default"),
  openOutputDirectory: (outputDir) => ipcRenderer.invoke("output:open", outputDir),
  getEngineStatus: () => ipcRenderer.invoke("engines:status"),
  // Electron 32+ 移除了 File.path，拖放文件需经 webUtils 取真实路径。
  getPathForFile: (file) => webUtils.getPathForFile(file),
  // 返回取消订阅函数。
  onJobsEvent: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on("jobs:event", listener);
    return () => {
      ipcRenderer.removeListener("jobs:event", listener);
    };
  },
  cancelJobs: (batchId) => ipcRenderer.send("jobs:cancel", batchId),
  startJobs: (batchId, actionId, files, outputDir) =>
    ipcRenderer.invoke("jobs:start", batchId, actionId, files, outputDir),
  // 本地文档查看/编辑
  readDoc: (filePath) => ipcRenderer.invoke("docs:read", filePath),
  saveDoc: (filePath, content) => ipcRenderer.invoke("docs:save", filePath, content),
  saveDocAs: (sourcePath, suggestedName, content) =>
    ipcRenderer.invoke("docs:saveAs", sourcePath, suggestedName, content)
});
