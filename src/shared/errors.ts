export type AppErrorCode =
  | "FILE_DAMAGED"
  | "FORMAT_UNSUPPORTED"
  | "OUTPUT_NOT_WRITABLE"
  | "DISK_FULL"
  | "ENGINE_MISSING"
  | "ENGINE_NOT_EXECUTABLE"
  | "FILE_BUSY"
  | "USER_CANCELLED"
  | "CONVERSION_TIMEOUT"
  | "OUTPUT_FAILED";

export interface AppError {
  code: AppErrorCode;
  message: string;
  details?: string;
}

export function toUserMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return "转换失败";
}
