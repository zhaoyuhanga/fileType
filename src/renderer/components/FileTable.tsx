import type { FileItem } from "../../shared/types.js";

interface FileTableProps {
  files: FileItem[];
  onToggle: (fileId: string) => void;
}

export function FileTable({ files, onToggle }: FileTableProps) {
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
              <th>选中</th>
              <th>文件名</th>
              <th>格式</th>
              <th>大小</th>
              <th>状态</th>
              <th>进度</th>
            </tr>
          </thead>
          <tbody>
            {files.length === 0 ? (
              <tr>
                <td colSpan={6} className="empty-cell">
                  还没有导入文件
                </td>
              </tr>
            ) : (
              files.map((file) => (
                <tr key={file.id}>
                  <td>
                    <input
                      aria-label={`选择 ${file.name}`}
                      type="checkbox"
                      checked={file.selected}
                      onChange={() => onToggle(file.id)}
                    />
                  </td>
                  <td className="file-table__name">{file.name}</td>
                  <td>{file.format}</td>
                  <td>{formatSize(file.sizeBytes)}</td>
                  <td>
                    <span className={`status status--${file.status}`}>{formatStatus(file)}</span>
                  </td>
                  <td>{file.progress}%</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function formatStatus(file: FileItem): string {
  if (file.status === "succeeded" && file.outputFormat) return `成功 -> ${file.outputFormat.toUpperCase()}`;
  if (file.status === "failed" && file.errorMessage) return `失败：${file.errorMessage}`;
  return file.status;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
