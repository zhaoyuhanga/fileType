import { Eye, Pencil } from "lucide-react";
import type { FileItem, JobStatus } from "../../shared/types";
import type { ViewerMode } from "./DocumentViewer";
import { formatBytes, formatLabel, supportsDocumentView } from "../utils";

interface FileTableProps {
  files: FileItem[];
  onToggle: (fileId: string) => void;
  onOpenFile: (file: FileItem, mode: ViewerMode) => void;
}

const STATUS_LABELS: Record<JobStatus, string> = {
  queued: "排队中",
  running: "转换中",
  succeeded: "成功",
  failed: "失败",
  cancelled: "已取消"
};

export function FileTable({ files, onToggle, onOpenFile }: FileTableProps) {
  return (
    <section className="panel file-table">
      <div className="panel__header">
        <h2>待处理文件</h2>
        <span>{files.length} 项</span>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th className="col-check">选中</th>
              <th>文件名</th>
              <th className="col-format">格式</th>
              <th className="col-size">大小</th>
              <th>状态</th>
              <th className="col-progress">进度</th>
              <th className="col-actions">操作</th>
            </tr>
          </thead>
          <tbody>
            {files.length === 0 ? (
              <tr>
                <td colSpan={7} className="empty-cell">
                  还没有导入文件，拖入文件 / 文件夹即可开始
                </td>
              </tr>
            ) : (
              files.map((file) => {
                const viewable = supportsDocumentView(file.format);
                return (
                  <tr
                    key={file.id}
                    className={file.status === "running" ? "is-running" : ""}
                    onDoubleClick={() => viewable && onOpenFile(file, "preview")}
                  >
                    <td className="col-check">
                      <input
                        aria-label={`选择 ${file.name}`}
                        type="checkbox"
                        checked={file.selected}
                        onChange={() => onToggle(file.id)}
                      />
                    </td>
                    <td className="file-table__name" title={file.path}>
                      {file.name}
                    </td>
                    <td className="col-format">
                      <span className={`format-badge format-badge--${file.format}`}>
                        {formatLabel(file.format)}
                      </span>
                    </td>
                    <td className="col-size">{formatBytes(file.sizeBytes)}</td>
                    <td>
                      <span className={`status status--${file.status}`}>{formatStatus(file)}</span>
                    </td>
                    <td className="col-progress">
                      {file.status === "succeeded" ? "100%" : `${file.progress}%`}
                    </td>
                    <td className="col-actions">
                      <div className="row-actions">
                        <button
                          type="button"
                          className="row-action"
                          title={viewable ? `预览 ${file.name}` : "当前格式暂不支持预览"}
                          disabled={!viewable}
                          onClick={() => onOpenFile(file, "preview")}
                        >
                          <Eye size={15} />
                        </button>
                        <button
                          type="button"
                          className="row-action"
                          title={viewable ? `编辑 ${file.name}` : "当前格式暂不支持编辑"}
                          disabled={!viewable}
                          onClick={() => onOpenFile(file, "edit")}
                        >
                          <Pencil size={15} />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function formatStatus(file: FileItem): string {
  if (file.status === "failed" && file.errorMessage) return `失败：${file.errorMessage}`;
  if (file.status === "succeeded" && file.outputFormat) {
    return `成功 -> ${formatLabel(file.outputFormat)}`;
  }
  return STATUS_LABELS[file.status] ?? file.status;
}
