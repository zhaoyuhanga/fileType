import type { ConverterAction, EngineStatus, FileItem } from "../../shared/types";

export interface AppState {
  files: FileItem[];
  actions: ConverterAction[];
  outputDir: string;
  selectedActionId: string;
  engineStatus: EngineStatus[];
  importBusy: boolean;
  runBusy: boolean;
  errorMessage?: string;
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
  | { type: "errorRaised"; errorMessage?: string }
  | { type: "jobResultsApplied"; results: Array<{ fileId: string; status: "succeeded" | "failed"; targetFormat?: FileItem["outputFormat"]; outputPath?: string; message?: string }> }
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

export function appReducer(state: AppState, action: AppAction): AppState {
  switch (action.type) {
    case "filesImported":
      return { ...state, files: [...state.files, ...action.files] };
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
    case "errorRaised":
      return { ...state, errorMessage: action.errorMessage };
    case "jobResultsApplied":
      return {
        ...state,
        files: state.files.map((file) => {
          const result = action.results.find((item) => item.fileId === file.id);
          if (!result) return file;
          return {
            ...file,
            status: result.status,
            progress: result.status === "succeeded" ? 100 : file.progress,
            outputPath: result.outputPath,
            outputFormat: result.targetFormat,
            errorMessage: result.message
          };
        })
      };
    default:
      return state;
  }
}
