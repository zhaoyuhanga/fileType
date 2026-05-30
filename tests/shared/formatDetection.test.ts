import { describe, expect, it } from "vitest";
import { detectFormatFromPath } from "../../src/shared/formatDetection";

describe("format detection", () => {
  it("detects markdown from md extension", () => {
    expect(detectFormatFromPath("C:/docs/readme.md")).toMatchObject({
      format: "markdown",
      category: "document"
    });
  });

  it("detects image formats", () => {
    expect(detectFormatFromPath("photo.png")).toMatchObject({
      format: "png",
      category: "image"
    });
  });

  it("detects m4a audio", () => {
    expect(detectFormatFromPath("voice.m4a")).toMatchObject({
      format: "m4a",
      category: "audio"
    });
  });

  it("falls back to unknown", () => {
    expect(detectFormatFromPath("archive.xyz")).toMatchObject({
      format: "unknown",
      category: "unknown"
    });
  });

  it("detects rar archive", () => {
    expect(detectFormatFromPath("data.rar")).toMatchObject({
      format: "rar",
      category: "archive"
    });
  });

  it("detects tar archive", () => {
    expect(detectFormatFromPath("data.tar")).toMatchObject({
      format: "tar",
      category: "archive"
    });
  });
});
