import { FilePlus, FolderOpen, Upload } from "lucide-react";

interface DropZoneProps {
  onPickFiles: () => void;
  onPickFolders: () => void;
  onDropPaths: (paths: string[]) => void;
}

function collectDroppedPaths(files: FileList): string[] {
  const getPathForFile = window.formatFlow?.getPathForFile;
  if (!getPathForFile) return [];
  return Array.from(files)
    .map((file) => getPathForFile(file))
    .filter((path): path is string => Boolean(path));
}

export function DropZone({ onPickFiles, onPickFolders, onDropPaths }: DropZoneProps) {
  return (
    <section
      className="drop-zone"
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => {
        event.preventDefault();
        const paths = collectDroppedPaths(event.dataTransfer.files);
        if (paths.length > 0) onDropPaths(paths);
      }}
    >
      <div className="drop-zone__icon">
        <FolderOpen size={28} />
      </div>
      <div className="drop-zone__copy">
        <strong>拖入文件或文件夹</strong>
        <span>支持批量转换；双击或行内按钮可查看 / 编辑 txt、md、json、mp4</span>
      </div>
      <div className="drop-zone__actions">
        <button type="button" className="primary-button" onClick={onPickFiles}>
          <FilePlus size={16} />
          选择文件
        </button>
        <button type="button" onClick={onPickFolders}>
          <Upload size={16} />
          选择文件夹
        </button>
      </div>
    </section>
  );
}
