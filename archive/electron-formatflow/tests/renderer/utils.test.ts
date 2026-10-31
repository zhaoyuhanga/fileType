import { describe, expect, it } from "vitest";
import { formatLabel, supportsDocumentView, toDocStreamUrl } from "../../src/renderer/utils";

describe("renderer document utils", () => {
  it("supports the doc viewer formats", () => {
    expect(supportsDocumentView("txt")).toBe(true);
    expect(supportsDocumentView("markdown")).toBe(true);
    expect(supportsDocumentView("json")).toBe(true);
    expect(supportsDocumentView("mp4")).toBe(true);
    expect(supportsDocumentView("pdf")).toBe(false);
    expect(supportsDocumentView("unknown")).toBe(false);
  });

  it("formats labels for badges", () => {
    expect(formatLabel("markdown")).toBe("MD");
    expect(formatLabel("json")).toBe("JSON");
    expect(formatLabel("mp4")).toBe("MP4");
    expect(formatLabel("unknown")).toBe("unknown");
  });

  it("builds docstream urls that survive special characters", () => {
    const url = toDocStreamUrl("C:\\我的 视频\\clip (1).mp4");
    expect(url.startsWith("docstream://file/?p=")).toBe(true);
    const pathPart = decodeURIComponent(url.replace("docstream://file/?p=", ""));
    expect(pathPart).toBe("C:\\我的 视频\\clip (1).mp4");
  });
});
