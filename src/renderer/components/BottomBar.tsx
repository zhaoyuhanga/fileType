import { FolderOpen } from "lucide-react";
import type { EngineStatus } from "../../shared/types.js";

interface BottomBarProps {
  outputDir: string;
  engineStatus: EngineStatus[];
  onPickOutputDir: () => void;
  onOpenOutputDir: () => void;
}

export function BottomBar({ outputDir, engineStatus, onPickOutputDir, onOpenOutputDir }: BottomBarProps) {
  const availableCount = engineStatus.filter((engine) => engine.available).length;

  return (
    <footer className="bottom-bar">
      <div className="bottom-bar__group">
        <span className="bottom-bar__label">输出目录</span>
        <span className="bottom-bar__value" title={outputDir || "尚未选择"}>
          {outputDir || "尚未选择"}
        </span>
      </div>
      <button type="button" onClick={onPickOutputDir}>
        <FolderOpen size={16} />
        选择目录
      </button>
      <button type="button" onClick={onOpenOutputDir}>
        <FolderOpen size={16} />
        打开目录
      </button>
      <div className="bottom-bar__group bottom-bar__group--right">
        <span className="bottom-bar__label">转换引擎</span>
        <span className="bottom-bar__value" title={engineStatus.map((engine) => engine.name).join("、")}>
          {availableCount}/{engineStatus.length} 可用
        </span>
      </div>
    </footer>
  );
}
