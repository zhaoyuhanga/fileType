import { CheckSquare, Square, FolderOpen, LoaderCircle } from "lucide-react";

interface ToolbarProps {
  onSelectAll: () => void;
  onClearSelection: () => void;
  onClearFiles: () => void;
  onPickOutputDir: () => void;
  importBusy?: boolean;
}

export function Toolbar({ onSelectAll, onClearSelection, onClearFiles, onPickOutputDir, importBusy }: ToolbarProps) {
  return (
    <div className="toolbar">
      <button type="button" onClick={onSelectAll}>
        <CheckSquare size={16} />
        全选
      </button>
      <button type="button" onClick={onClearSelection}>
        <Square size={16} />
        取消选择
      </button>
      <button type="button" onClick={onClearFiles}>
        <Square size={16} />
        清空列表
      </button>
      <button type="button" onClick={onPickOutputDir}>
        <FolderOpen size={16} />
        输出目录
      </button>
      {importBusy ? (
        <span className="toolbar__busy">
          <LoaderCircle size={16} className="spin" />
          导入中
        </span>
      ) : null}
    </div>
  );
}
