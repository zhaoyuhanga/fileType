import { useEffect, useMemo, useReducer } from "react";
import { getCommonActionsForFormats } from "../shared/converters/registry.js";
import { ActionPanel } from "./components/ActionPanel";
import { BottomBar } from "./components/BottomBar";
import { DropZone } from "./components/DropZone";
import { FileTable } from "./components/FileTable";
import { Toolbar } from "./components/Toolbar";
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
  getActionsForFormats: async () => [],
  getEngineStatus: async () => [],
  startJobs: async () => []
};

function getFormatFlow(): Window["formatFlow"] {
  return window.formatFlow ?? fallbackFormatFlow;
}

export function App() {
  const [state, dispatch] = useReducer(appReducer, initialAppState);

  useEffect(() => {
    const formatFlow = getFormatFlow();
    if (!window.formatFlow) {
      dispatch({ type: "errorRaised", errorMessage: "桌面能力未加载，请重新安装或重启应用" });
    }

    formatFlow
      .getEngineStatus()
      .then((engineStatus) => dispatch({ type: "engineStatusLoaded", engineStatus }))
      .catch(() => dispatch({ type: "engineStatusLoaded", engineStatus: [] }));

    formatFlow
      .getDefaultOutputDir()
      .then((outputDir) => dispatch({ type: "outputDirChanged", outputDir }))
      .catch(() => dispatch({ type: "outputDirChanged", outputDir: "" }));
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
        unavailableReason: getActionUnavailableReason(action, state.engineStatus)
      })),
    [selectedFormats, state.engineStatus]
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
      dispatch({ type: "errorRaised", errorMessage: undefined });
    } catch (error) {
      dispatch({
        type: "errorRaised",
        errorMessage: error instanceof Error ? error.message : "导入失败"
      });
    } finally {
      dispatch({ type: "importBusyChanged", importBusy: false });
    }
  };

  const handlePickFiles = async () => {
    const imported = await getFormatFlow().pickFiles();
    dispatch({ type: "filesImported", files: imported });
  };

  const handlePickFolders = async () => {
    const imported = await getFormatFlow().pickFolders();
    dispatch({ type: "filesImported", files: imported });
  };

  const handlePickOutputDir = async () => {
    const result = await getFormatFlow().pickOutputDirectory();
    if (result) dispatch({ type: "outputDirChanged", outputDir: result });
  };

  const handleOpenOutputDir = async () => {
    const result = await getFormatFlow().openOutputDirectory(state.outputDir);
    if (result) dispatch({ type: "errorRaised", errorMessage: result });
  };

  const handleRun = async () => {
    const selectedAction = state.actions.find((action) => action.id === state.selectedActionId) ?? state.actions[0];
    if (!selectedAction || selectedFiles.length === 0) return;

    dispatch({ type: "runBusyChanged", runBusy: true });
    try {
      const results = await getFormatFlow().startJobs(selectedAction.id, selectedFiles, state.outputDir);
      dispatch({ type: "jobResultsApplied", results });
      if (results.some((result) => result.status === "failed")) {
        dispatch({ type: "errorRaised", errorMessage: results.find((result) => result.message)?.message });
      } else {
        dispatch({ type: "errorRaised", errorMessage: undefined });
      }
    } catch (error) {
      dispatch({
        type: "errorRaised",
        errorMessage: error instanceof Error ? error.message : "转换失败"
      });
    } finally {
      dispatch({ type: "runBusyChanged", runBusy: false });
    }
  };

  return (
    <main className="app-shell">
      <header className="title-bar">
        <div>
          <h1>万能格式转换器</h1>
          <p>完全本地离线运行</p>
        </div>
        <div className="title-bar__status">{state.errorMessage ?? "准备就绪"}</div>
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
          <FileTable files={state.files} onToggle={(fileId) => dispatch({ type: "fileSelectionToggled", fileId })} />
        </section>

        <ActionPanel
          actions={actionModels}
          selectedActionId={state.selectedActionId}
          onActionSelected={(action) => dispatch({ type: "actionSelected", actionId: action.id })}
          onRun={handleRun}
          runBusy={state.runBusy}
        />
      </div>

      <BottomBar
        outputDir={state.outputDir}
        engineStatus={state.engineStatus}
        onPickOutputDir={handlePickOutputDir}
        onOpenOutputDir={handleOpenOutputDir}
      />
    </main>
  );
}
