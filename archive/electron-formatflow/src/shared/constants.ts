/** docstream:// 自定义协议名（主进程注册、渲染层拼 URL 共用）。 */
export const DOC_STREAM_SCHEME = "docstream";

/** 与主进程约定的文档 IPC 通道名。 */
export const DOC_IPC_CHANNELS = {
  read: "docs:read",
  save: "docs:save",
  saveAs: "docs:saveAs"
} as const;
