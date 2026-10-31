import { FileOutput, LoaderCircle, X } from "lucide-react";
import type { ConverterAction } from "../../shared/types.js";

export interface ActionViewModel {
  action: ConverterAction;
  unavailableReason?: string;
}

interface ActionPanelProps {
  actions: ActionViewModel[];
  selectedActionId: string;
  onActionSelected: (action: ConverterAction) => void;
  onRun: () => void;
  onCancel: () => void;
  runBusy?: boolean;
}

export function ActionPanel({ actions, selectedActionId, onActionSelected, onRun, onCancel, runBusy }: ActionPanelProps) {
  return (
    <aside className="panel action-panel">
      <div className="panel__header">
        <h2>可执行动作</h2>
        <span>{actions.length} 项</span>
      </div>
      <p className="muted">根据当前选中文件自动匹配。</p>
      <div className="action-list">
        {actions.length === 0 ? (
          <div className="empty-state">选中文件后会显示可用转换动作</div>
        ) : (
          actions.map(({ action, unavailableReason }) => (
            <button
              type="button"
              key={action.id}
              className={`action-button ${selectedActionId === action.id ? "action-button--active" : ""}`}
              disabled={Boolean(unavailableReason)}
              title={unavailableReason}
              onClick={() => onActionSelected(action)}
            >
              <FileOutput size={18} />
              <span>{action.label}</span>
              {unavailableReason ? <small>{unavailableReason}</small> : null}
            </button>
          ))
        )}
      </div>
      <div className="action-panel__actions">
        {runBusy ? (
          <button type="button" className="action-panel__cancel" onClick={onCancel}>
            <X size={16} />
            取消
          </button>
        ) : null}
        <button
          type="button"
          className={`primary-button action-panel__run${runBusy ? " action-panel__run--busy" : ""}`}
          onClick={onRun}
          disabled={runBusy || !selectedActionId}
        >
          {runBusy ? (
            <>
              <LoaderCircle size={14} className="spin" />
              转换中...
            </>
          ) : (
            "开始转换"
          )}
        </button>
      </div>
    </aside>
  );
}
