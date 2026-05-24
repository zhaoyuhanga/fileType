import type { ConverterAction, EngineStatus, FileItem, SupportedFormat } from "../shared/types";

declare global {
  interface Window {
    formatFlow: {
      version: string;
      importPaths: (paths: string[]) => Promise<FileItem[]>;
      pickFiles: () => Promise<FileItem[]>;
      pickFolders: () => Promise<FileItem[]>;
      pickOutputDirectory: () => Promise<string>;
      getDefaultOutputDir: () => Promise<string>;
      openOutputDirectory: (outputDir: string) => Promise<string>;
      getActionsForFormats: (formats: SupportedFormat[]) => Promise<ConverterAction[]>;
      getEngineStatus: () => Promise<EngineStatus[]>;
      startJobs: (
        actionId: string,
        files: FileItem[],
        outputDir: string
      ) => Promise<Array<{ fileId: string; status: "succeeded" | "failed"; targetFormat?: SupportedFormat; outputPath?: string; message?: string }>>;
    };
  }
}

export {};
