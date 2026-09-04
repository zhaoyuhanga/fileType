import type { ReactNode } from "react";
import { AlertTriangle } from "lucide-react";

interface ConfirmDialogProps {
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  /** 额外按钮（如"不保存"），点击后不关闭弹窗由调用方决定。 */
  extraLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onExtra?: () => void;
  onCancel: () => void;
}

/** 通用确认弹窗：支持 确认 / 取消 / 可选的第三种操作。 */
export function ConfirmDialog({
  title,
  message,
  confirmLabel = "确定",
  extraLabel,
  danger = false,
  onConfirm,
  onExtra,
  onCancel
}: ConfirmDialogProps) {
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onCancel}>
      <div
        className="confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="confirm-dialog__icon confirm-dialog__icon--warn">
          <AlertTriangle size={22} />
        </div>
        <div className="confirm-dialog__body">
          <h3>{title}</h3>
          <p>{message}</p>
        </div>
        <div className="confirm-dialog__actions">
          <button type="button" onClick={onCancel}>
            取消
          </button>
          {extraLabel && onExtra ? (
            <button type="button" className="confirm-dialog__ghost" onClick={onExtra}>
              {extraLabel}
            </button>
          ) : null}
          <button
            type="button"
            className={`primary-button${danger ? " primary-button--danger" : ""}`}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
