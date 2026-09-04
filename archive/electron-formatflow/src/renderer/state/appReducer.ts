import type { ConverterAction, EngineStatus, FileItem, JobStatus, SupportedFormat } from "../../shared/types";

export interface AppState {
  files: FileItem[];
  actions: ConverterAction[];
  outputDir: string;
  selectedActionId: string;
  engineStatus: EngineStatus[];
  importBusy: boolean;
  runBusy: boolean;
}

export type FileOutcomeStatus = Exclude<JobStatus, "queued">;

export interface FileOutcome {
  fileId: string;
  status: FileOutcomeStatus;
  targetFormat?: SupportedFormat;
  outputPath?: string;
  message?: string;
}

export type AppAction =
  | { type: "filesImported"; files: FileItem[] }
  | { type: "fileSelectionToggled"; fileId: string }
  | { type: "allSelected" }
  | { type: "allCleared" }
  | { type: "actionsLoaded"; actions: ConverterAction[] }
  | { type: "actionSelected"; actionId: string }
  | { type: "outputDirChanged"; outputDir: string }
  | { type: "engineStatusLoaded"; engineStatus: EngineStatus[] }
  | { type: "importBusyChanged"; importBusy: boolean }
  | { type: "runBusyChanged"; runBusy: boolean }
  | { type: "fileUpdated"; fileId: string; update: Partial<FileItem> }
  | { type: "jobEvent"; outcome: FileOutcome }
  | { type: "jobResultsApplied"; results: FileOutcome[] }
  | { type: "filesCleared" };

export const initialAppState: AppState = {
  files: [],
  actions: [],
  outputDir: "",
  selectedActionId: "",
  engineStatus: [],
  importBusy: false,
  runBusy: false
};

function applyOutcome(file: FileItem, outcome: FileOutcome): FileItem {
  const { status, targetFormat, outputPath, message } = outcome;

  if (status === "running") {
    return {
      ...file,
      status,
      errorMessage: undefined
    };
  }

  if (status === "succeeded") {
    return {
      ...file,
      status,
      progress: 100,
      outputPath,
      outputFormat: targetFormat,
      errorMessage: undefined
    };
  }

  if (status === "cancelled") {
    return { ...file, status, errorMessage: undefined };
  }

  return {
    ...file,
    status,
    outputPath,
    outputFormat: targetFormat,
    errorMessage: message ?? "转换失败"
  };
}

export function appReducer(state: AppState, action: AppAction): AppState {
  switch (action.type) {
    case "filesImported": {
      // 相同路径重复导入时以最新条目覆盖旧条目，避免列表堆积重复行。
      const byPath = new Map(state.files.map((file) => [file.path, file]));
      for (const file of action.files) byPath.set(file.path, file);
      return { ...state, files: [...byPath.values()] };
    }
    case "filesCleared":
      return { ...state, files: [], actions: [], selectedActionId: "" };
    case "fileSelectionToggled":
      return {
        ...state,
        files: state.files.map((file) =>
          file.id === action.fileId ? { ...file, selected: !file.selected } : file
        )
      };
    case "allSelected":
      return { ...state, files: state.files.map((file) => ({ ...file, selected: true })) };
    case "allCleared":
      return { ...state, files: state.files.map((file) => ({ ...file, selected: false })) };
    case "actionsLoaded":
      return {
        ...state,
        actions: action.actions,
        selectedActionId: action.actions.some((item) => item.id === state.selectedActionId)
          ? state.selectedActionId
          : action.actions[0]?.id ?? ""
      };
    case "actionSelected":
      return { ...state, selectedActionId: action.actionId };
    case "outputDirChanged":
      return { ...state, outputDir: action.outputDir };
    case "engineStatusLoaded":
      return { ...state, engineStatus: action.engineStatus };
    case "importBusyChanged":
      return { ...state, importBusy: action.importBusy };
    case "runBusyChanged":
      return { ...state, runBusy: action.runBusy };
    case "fileUpdated":
      return {
        ...state,
        files: state.files.map((file) =>
          file.id === action.fileId ? { ...file, ...action.update } : file
        )
      };
    case "jobEvent":
      return {
        ...state,
        files: state.files.map((file) =>
          file.id === action.outcome.fileId ? applyOutcome(file, action.outcome) : file
        )
      };
    case "jobResultsApplied": {
      const resultsByFile = new Map(action.results.map((result) => [result.fileId, result]));
      if (resultsByFile.size === 0) return state;
      return {
        ...state,
        files: state.files.map((file) => {
          const outcome = resultsByFile.get(file.id);
          return outcome ? applyOutcome(file, outcome) : file;
        })
      };
    }
    default:
      return state;
  }
}
