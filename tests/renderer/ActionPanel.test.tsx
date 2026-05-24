import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
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
              engine: "libreoffice"
            }
          }
        ]}
        selectedActionId="word-to-pdf"
        onActionSelected={() => undefined}
        onRun={() => undefined}
      />
    );

    expect(screen.getByText("Word 转 PDF")).toBeInTheDocument();
  });
});
