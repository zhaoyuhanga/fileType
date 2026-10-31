import type {
  ConverterAction,
  DocumentReadResult,
  DocumentSaveResult,
  EngineStatus,
  FileItem,
  JobProgressEvent,
  SupportedFormat
} from "../shared/types";

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
      getEngineStatus: () => Promise<EngineStatus[]>;
      getPathForFile: (file: File) => string;
      onJobsEvent: (callback: (event: JobProgressEvent) => void) => () => void;
      cancelJobs: (batchId: string) => void;
      startJobs: (
        batchId: string,
        actionId: string,
        files: FileItem[],
        outputDir: string
      ) => Promise<
        Array<{
          fileId: string;
          status: "succeeded" | "failed" | "cancelled";
          targetFormat?: SupportedFormat;
          outputPath?: string;
          message?: string;
        }>
      >;
      readDoc: (filePath: string) => Promise<DocumentReadResult>;
      saveDoc: (filePath: string, content: string) => Promise<DocumentSaveResult>;
      saveDocAs: (
        sourcePath: string,
        suggestedName: string,
        content: string | null
      ) => Promise<DocumentSaveResult>;
    };
  }
}

export {};
