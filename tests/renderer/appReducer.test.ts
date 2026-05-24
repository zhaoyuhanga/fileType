import { describe, expect, it } from "vitest";
import { appReducer, initialAppState } from "../../src/renderer/state/appReducer";

describe("app reducer", () => {
  it("adds imported files selected by default", () => {
    const state = appReducer(initialAppState, {
      type: "filesImported",
      files: [
        {
          id: "1",
          path: "C:/a.txt",
          name: "a.txt",
          extension: "txt",
          format: "txt",
          category: "document",
          sizeBytes: 1,
          selected: true,
          status: "queued",
          progress: 0
        }
      ]
    });

    expect(state.files).toHaveLength(1);
    expect(state.files[0].selected).toBe(true);
  });

  it("toggles one file selection", () => {
    const withFile = appReducer(initialAppState, {
      type: "filesImported",
      files: [
        {
          id: "1",
          path: "C:/a.txt",
          name: "a.txt",
          extension: "txt",
          format: "txt",
          category: "document",
          sizeBytes: 1,
          selected: true,
          status: "queued",
          progress: 0
        }
      ]
    });
    const state = appReducer(withFile, { type: "fileSelectionToggled", fileId: "1" });

    expect(state.files[0].selected).toBe(false);
  });

  it("stores the converted target format on success", () => {
    const withFile = appReducer(initialAppState, {
      type: "filesImported",
      files: [
        {
          id: "1",
          path: "C:/a.txt",
          name: "a.txt",
          extension: "txt",
          format: "txt",
          category: "document",
          sizeBytes: 1,
          selected: true,
          status: "queued",
          progress: 0
        }
      ]
    });

    const state = appReducer(withFile, {
      type: "jobResultsApplied",
      results: [{ fileId: "1", status: "succeeded", targetFormat: "pdf", outputPath: "C:/out/a.pdf" }]
    });

    expect(state.files[0]).toMatchObject({
      status: "succeeded",
      progress: 100,
      outputFormat: "pdf",
      outputPath: "C:/out/a.pdf"
    });
  });
});
