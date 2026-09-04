import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { getCommonActionsForFormats } from "../shared/converters/registry.js";
import { getCategory, getFormatFromExtension } from "../shared/fileTypes.js";
import type { FileItem } from "../shared/types";
import { ActionPanel } from "./components/ActionPanel";
import { BottomBar } from "./components/BottomBar";
import { DocumentViewer, type DocumentViewerRequest, type ViewerMode } from "./components/DocumentViewer";
import { DropZone } from "./components/DropZone";
import { FileTable } from "./components/FileTable";
import { Toolbar } from "./components/Toolbar";
import { useToast } from "./components/Toast";
import { appReducer, initialAppState } from "./state/appReducer";
import { getActionUnavailableReason } from "./actionAvailability";

const fallbackFormatFlow: Window["formatFlow"] = {
  version: "fallback",
  importPaths: async () => [],
  pickFiles: async () => [],
  pickFolders: async () => [],
  pickOutputDirectory: async () => "",
  getDefaultOutputDir: async () => "",
  openOutputDirectory: async () => "桌面能力不可用",
  getEngineStatus: async () => [],
  getPathForFile: () => "",
  onJobsEvent: () => () => undefined,
  cancelJobs: () => undefined,
  startJobs: async () => [],
  readDoc: async () => ({ ok: false, error: "桌面能力不可用，无法读取本地文件" }),
  saveDoc: async () => ({ ok: false, error: "桌面能力不可用，无法保存文件" }),
  saveDocAs: async () => ({ ok: false, error: "桌面能力不可用，无法另存文件" })
};

function getFormatFlow(): Window["formatFlow"] {
  return window.formatFlow ?? fallbackFormatFlow;
}

function createBatchId(): string {
  return typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `batch-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** 由新路径推导文件行需要的元信息（渲染进程内纯计算，避免引入 node:path）。 */
function fileMetaFromPath(filePath: string, byteLength?: number): Partial<FileItem> {
  const normalized = filePath.replace(/\\/g, "/");
  const name = normalized.split("/").filter(Boolean).at(-1) ?? filePath;
  const dot = name.lastIndexOf(".");
  const extension = dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
  const format = extension ? getFormatFromExtension(extension) : "unknown";
  return {
    path: filePath,
    name,
    extension,
    format,
    category: getCategory(format),
    ...(typeof byteLength === "number" ? { sizeBytes: byteLength } : {})
  };
}

export function App() {
  const [state, dispatch] = useReducer(appReducer, initialAppState);
  const toast = useToast();
  const batchIdRef = useRef<string | undefined>(undefined);
  const bridgeWarnedRef = useRef(false);
  const [viewerRequest, setViewerRequest] = useState<DocumentViewerRequest | null>(null);

  useEffect(() => {
    const formatFlow = getFormatFlow();
    if (!window.formatFlow && !bridgeWarnedRef.current) {
      bridgeWarnedRef.current = true;
      toast.error("桌面能力未加载，请重新安装或重启应用");
    }

    formatFlow
      .getEngineStatus()
      .then((engineStatus) => dispatch({ type: "engineStatusLoaded", engineStatus }))
      .catch(() => dispatch({ type: "engineStatusLoaded", engineStatus: [] }));

    formatFlow
      .getDefaultOutputDir()
      .then((outputDir) => dispatch({ type: "outputDirChanged", outputDir }))
      .catch(() => dispatch({ type: "outputDirChanged", outputDir: "" }));
  }, [toast]);

  useEffect(() => {
    return getFormatFlow().onJobsEvent((event) => {
      dispatch({
        type: "jobEvent",
        outcome: {
          fileId: event.fileId,
          status: event.status,
          targetFormat: event.targetFormat,
          outputPath: event.outputPath,
          message: event.message
        }
      });
    });
  }, []);

  const selectedFormats = useMemo(
    () =>
      state.files
        .filter((file) => file.selected && file.format !== "unknown")
        .map((file) => file.format),
    [state.files]
  );

  const actionModels = useMemo(
    () =>
      getCommonActionsForFormats(selectedFormats).map((action) => ({
        action,
        unavailableReason: getActionUnavailableReason(action)
      })),
    [selectedFormats]
  );

  const availableActions = useMemo(
    () => actionModels.filter((item) => !item.unavailableReason).map((item) => item.action),
    [actionModels]
  );

  useEffect(() => {
    dispatch({ type: "actionsLoaded", actions: availableActions });
  }, [availableActions]);

  const selectedFiles = state.files.filter((file) => file.selected);

  const handleImportPaths = async (paths: string[]) => {
    dispatch({ type: "importBusyChanged", importBusy: true });
    try {
      const imported = await getFormatFlow().importPaths(paths);
      dispatch({ type: "filesImported", files: imported });
      if (imported.length > 0) toast.success(`已导入 ${imported.length} 个文件`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "导入失败");
    } finally {
      dispatch({ type: "importBusyChanged", importBusy: false });
    }
  };

  const handlePickFiles = async () => {
    try {
      const imported = await getFormatFlow().pickFiles();
      dispatch({ type: "filesImported", files: imported });
      if (imported.length > 0) toast.success(`已导入 ${imported.length} 个文件`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "选择文件失败");
    }
  };

  const handlePickFolders = async () => {
    try {
      const imported = await getFormatFlow().pickFolders();
      dispatch({ type: "filesImported", files: imported });
      if (imported.length > 0) toast.success(`已导入 ${imported.length} 个文件`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "选择文件夹失败");
    }
  };

  const handlePickOutputDir = async () => {
    try {
      const result = await getFormatFlow().pickOutputDirectory();
      if (result) dispatch({ type: "outputDirChanged", outputDir: result });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "选择输出目录失败");
    }
  };

  const handleOpenOutputDir = async () => {
    try {
      const result = await getFormatFlow().openOutputDirectory(state.outputDir);
      if (result) toast.error(result);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "打开输出目录失败");
    }
  };

  const handleRun = async () => {
    const selectedAction = state.actions.find((action) => action.id === state.selectedActionId) ?? state.actions[0];
    if (!selectedAction) {
      toast.info("没有可用的转换动作，请先选中文件");
      return;
    }
    if (selectedFiles.length === 0) {
      toast.info("请先勾选要转换的文件");
      return;
    }

    const batchId = createBatchId();
    batchIdRef.current = batchId;
    dispatch({ type: "runBusyChanged", runBusy: true });

    try {
      const results = await getFormatFlow().startJobs(batchId, selectedAction.id, selectedFiles, state.outputDir);
      // 实时结果已通过 jobEvent 到达；此处兜底应用最终结果，保证状态一致。
      dispatch({ type: "jobResultsApplied", results });

      const succeeded = results.filter((result) => result.status === "succeeded").length;
      const failed = results.filter((result) => result.status === "failed");
      const cancelled = results.filter((result) => result.status === "cancelled").length;

      if (failed.length > 0) {
        const first = failed.find((result) => result.message);
        toast.error(
          `有 ${failed.length} 个文件转换失败${cancelled > 0 ? `，${cancelled} 个已取消` : ""}${
            first?.message ? `：${first.message}` : ""
          }`
        );
      } else if (results.length > 0) {
        toast.success(
          `转换完成：${succeeded} 个文件成功${cancelled > 0 ? `，${cancelled} 个已取消` : ""}`
        );
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "转换失败");
    } finally {
      batchIdRef.current = undefined;
      dispatch({ type: "runBusyChanged", runBusy: false });
    }
  };

  const handleCancel = () => {
    const batchId = batchIdRef.current;
    if (batchId) {
      getFormatFlow().cancelJobs(batchId);
      toast.info("正在取消剩余转换…");
    }
  };

  const openFile = (file: FileItem, mode: ViewerMode) => {
    setViewerRequest({ file, mode });
  };

  const handleViewerPathChanged = (fileId: string, filePath: string, byteLength?: number) => {
    dispatch({ type: "fileUpdated", fileId, update: fileMetaFromPath(filePath, byteLength) });
  };

  return (
    <main className="app-shell">
      <header className="title-bar">
        <div className="title-bar__brand">
          <span className="title-bar__logo" aria-hidden="true">
            ⇄
          </span>
          <div>
            <h1>万能格式转换器</h1>
            <p>本地离线 · 转换 / 预览 / 编辑</p>
          </div>
        </div>
      </header>

      <div className="workbench">
        <section className="left-column">
          <DropZone onPickFiles={handlePickFiles} onPickFolders={handlePickFolders} onDropPaths={handleImportPaths} />
          <Toolbar
            onSelectAll={() => dispatch({ type: "allSelected" })}
            onClearSelection={() => dispatch({ type: "allCleared" })}
            onClearFiles={() => dispatch({ type: "filesCleared" })}
            onPickOutputDir={handlePickOutputDir}
            importBusy={state.importBusy}
          />
          <FileTable
            files={state.files}
            onToggle={(fileId) => dispatch({ type: "fileSelectionToggled", fileId })}
            onOpenFile={openFile}
          />
        </section>

        <ActionPanel
          actions={actionModels}
          selectedActionId={state.selectedActionId}
          onActionSelected={(action) => dispatch({ type: "actionSelected", actionId: action.id })}
          onRun={handleRun}
          onCancel={handleCancel}
          runBusy={state.runBusy}
        />
      </div>

      <BottomBar
        outputDir={state.outputDir}
        engineStatus={state.engineStatus}
        onPickOutputDir={handlePickOutputDir}
        onOpenOutputDir={handleOpenOutputDir}
      />

      {viewerRequest ? (
        <DocumentViewer
          request={viewerRequest}
          onClose={() => setViewerRequest(null)}
          onPathChanged={handleViewerPathChanged}
        />
      ) : null}
    </main>
  );
}
