import type { ConverterAction, FileItem, JobStatus } from "./types.js";

export interface JobItem {
  id: string;
  fileId: string;
  actionId: string;
  status: JobStatus;
  progress: number;
  startedAt?: number;
  finishedAt?: number;
  errorMessage?: string;
}

export interface JobQueueState {
  jobs: JobItem[];
}

export const initialJobQueueState: JobQueueState = {
  jobs: []
};

export function createJobsForSelection(files: FileItem[], action: ConverterAction): JobItem[] {
  return files
    .filter((file) => file.selected)
    .map((file) => ({
      id: `${action.id}:${file.id}`,
      fileId: file.id,
      actionId: action.id,
      status: "queued" as const,
      progress: 0
    }));
}

export function startJob(job: JobItem): JobItem {
  return {
    ...job,
    status: "running",
    startedAt: Date.now(),
    progress: 0
  };
}

export function succeedJob(job: JobItem): JobItem {
  return {
    ...job,
    status: "succeeded",
    progress: 100,
    finishedAt: Date.now()
  };
}

export function failJob(job: JobItem, errorMessage: string): JobItem {
  return {
    ...job,
    status: "failed",
    errorMessage,
    finishedAt: Date.now()
  };
}

export function cancelJob(job: JobItem): JobItem {
  return {
    ...job,
    status: "cancelled",
    finishedAt: Date.now()
  };
}

export function updateJobProgress(job: JobItem, progress: number): JobItem {
  return {
    ...job,
    progress: Math.max(0, Math.min(100, progress))
  };
}
