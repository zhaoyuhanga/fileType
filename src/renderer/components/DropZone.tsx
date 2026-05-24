import { FilePlus, FolderOpen, Upload } from "lucide-react";

interface DropZoneProps {
  onPickFiles: () => void;
  onPickFolders: () => void;
  onDropPaths: (paths: string[]) => void;
}

export function DropZone({ onPickFiles, onPickFolders, onDropPaths }: DropZoneProps) {
  return (
    <section
      className="drop-zone"
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => {
        event.preventDefault();
        const paths = Array.from(event.dataTransfer.files)
          .map((file) => (file as File & { path?: string }).path)
          .filter((path): path is string => Boolean(path));
        if (paths.length > 0) onDropPaths(paths);
      }}
    >
      <div className="drop-zone__icon">
        <FolderOpen size={28} />
      </div>
      <div className="drop-zone__copy">
        <strong>拖入文件或文件夹</strong>
        <span>支持批量导入，识别后会自动生成可执行动作</span>
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
