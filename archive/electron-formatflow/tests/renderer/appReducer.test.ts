import { describe, expect, it } from "vitest";
import { appReducer, initialAppState } from "../../src/renderer/state/appReducer";
import type { FileItem } from "../../src/shared/types";

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

  it("replaces an existing row when the same path is imported again", () => {
    const first: FileItem = {
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
    };
    const second: FileItem = { ...first, id: "2", sizeBytes: 99, selected: false };

    const imported = appReducer(initialAppState, { type: "filesImported", files: [first] });
    const state = appReducer(imported, { type: "filesImported", files: [second] });

    expect(state.files).toHaveLength(1);
    expect(state.files[0]).toMatchObject({ id: "2", sizeBytes: 99, selected: false });
  });

  it("tracks running and cancelled states from job events", () => {
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

    const running = appReducer(withFile, {
      type: "jobEvent",
      outcome: { fileId: "1", status: "running" }
    });
    expect(running.files[0].status).toBe("running");

    const cancelled = appReducer(running, {
      type: "jobEvent",
      outcome: { fileId: "1", status: "cancelled", message: "已取消" }
    });
    expect(cancelled.files[0].status).toBe("cancelled");
    expect(cancelled.files[0].progress).toBe(0);
  });
});
