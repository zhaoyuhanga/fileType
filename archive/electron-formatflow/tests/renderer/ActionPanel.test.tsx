import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ActionPanel } from "../../src/renderer/components/ActionPanel";

describe("ActionPanel", () => {
  it("renders available actions", () => {
    render(
      <ActionPanel
        actions={[
          {
            action: {
              id: "word-to-pdf",
              label: "Word 转 PDF",
              sourceFormats: ["docx"],
              targetFormat: "pdf",
              category: "document",
              engine: "word"
            }
          }
        ]}
        selectedActionId="word-to-pdf"
        onActionSelected={() => undefined}
        onRun={() => undefined}
        onCancel={() => undefined}
      />
    );

    expect(screen.getByText("Word 转 PDF")).toBeInTheDocument();
  });

  it("shows a cancel button while a run is busy", () => {
    const onCancel = vi.fn();
    render(
      <ActionPanel
        actions={[]}
        selectedActionId=""
        onActionSelected={() => undefined}
        onRun={() => undefined}
        onCancel={onCancel}
        runBusy
      />
    );

    const cancelButton = screen.getByRole("button", { name: "取消" });
    cancelButton.click();
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
