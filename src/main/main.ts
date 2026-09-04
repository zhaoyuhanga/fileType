import { app, BrowserWindow, Menu } from "electron";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { registerIpcHandlers } from "./ipc.js";
import { registerDocumentIpc } from "./services/docHandlers.js";
import { registerDocStreamScheme, setupDocStreamProtocol } from "./services/docStream.js";

const isDev = !app.isPackaged;

// 必须在 app ready 前声明自定义协议特权。
registerDocStreamScheme();

function resolvePreloadPath(): string {
  return fileURLToPath(new URL("./preload.cjs", import.meta.url));
}

async function createWindow(): Promise<void> {
  const win = new BrowserWindow({
    width: 1360,
    height: 860,
    minWidth: 1024,
    minHeight: 720,
    title: "万能格式转换器",
    backgroundColor: "#f4f6fb",
    webPreferences: {
      preload: resolvePreloadPath(),
      contextIsolation: true,
      nodeIntegration: false,
      devTools: isDev
    }
  });

  win.webContents.on("console-message", (_event, _level, message) => {
    console.log(`[renderer] ${message}`);
  });

  win.webContents.on("render-process-gone", (_event, details) => {
    console.error(`[renderer-gone] ${details.reason}`);
  });

  if (isDev) {
    await win.loadURL("http://127.0.0.1:5173");
  } else {
    await win.loadFile(path.join(app.getAppPath(), "dist/renderer/index.html"));
  }
}

app.whenReady().then(() => {
  if (!isDev) Menu.setApplicationMenu(null);
  setupDocStreamProtocol();
  registerIpcHandlers();
  registerDocumentIpc();
  void createWindow();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    void createWindow();
  }
});
