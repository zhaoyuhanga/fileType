import { describe, expect, it } from "vitest";
import { getActionUnavailableReason, isActionAvailable } from "../../src/renderer/actionAvailability";
import type { ConverterAction } from "../../src/shared/types";

function action(partial: Partial<ConverterAction>): ConverterAction {
  return {
    id: "x",
    label: "X",
    sourceFormats: ["txt"],
    targetFormat: "pdf",
    category: "document",
    engine: "text",
    ...partial
  };
}

describe("action availability", () => {
  it("allows built-in text conversions", () => {
    expect(isActionAvailable(action({ id: "txt-to-html", targetFormat: "html" }))).toBe(true);
    expect(isActionAvailable(action({ id: "txt-to-pdf" }))).toBe(true);
  });

  it("allows word and spreadsheet conversions", () => {
    expect(
      isActionAvailable(action({ id: "doc-to-pdf", sourceFormats: ["doc"], targetFormat: "pdf", engine: "word" }))
    ).toBe(true);
    expect(
      isActionAvailable(
        action({ id: "xls-to-csv", sourceFormats: ["xls"], targetFormat: "csv", engine: "sheet" })
      )
    ).toBe(true);
  });

  it("allows media conversions with the bundled ffmpeg runtime", () => {
    expect(
      isActionAvailable(action({ id: "mp4-to-mov", sourceFormats: ["mp4"], targetFormat: "mov", engine: "media" }))
    ).toBe(true);
    expect(
      isActionAvailable(action({ id: "m4a-to-wav", sourceFormats: ["m4a"], targetFormat: "wav", engine: "media" }))
    ).toBe(true);
    expect(
      isActionAvailable(action({ id: "video-to-mp3", sourceFormats: ["mp4", "mov", "avi"], targetFormat: "mp3", engine: "media" }))
    ).toBe(true);
  });

  it("allows built-in archive actions without any external engine", () => {
    expect(
      isActionAvailable(
        action({ id: "compress-to-zip", sourceFormats: ["txt"], targetFormat: "zip", engine: "archive" })
      )
    ).toBe(true);
    expect(
      isActionAvailable(action({ id: "zip-extract", sourceFormats: ["zip"], targetFormat: "zip", engine: "archive" }))
    ).toBe(true);
    expect(
      isActionAvailable(action({ id: "tar-extract", sourceFormats: ["tar"], targetFormat: "tar", engine: "archive" }))
    ).toBe(true);
    expect(
      isActionAvailable(action({ id: "rar-extract", sourceFormats: ["rar"], targetFormat: "rar", engine: "archive" }))
    ).toBe(true);
  });

  it("marks pdf to png as not yet supported", () => {
    expect(
      isActionAvailable(action({ id: "pdf-to-image", sourceFormats: ["pdf"], targetFormat: "png", engine: "pdf" }))
    ).toBe(false);
    expect(getActionUnavailableReason(action({ id: "pdf-to-image", sourceFormats: ["pdf"], targetFormat: "png", engine: "pdf" }))).toBe(
      "PDF 转图片待增强"
    );
  });

  it("returns no reason for supported actions", () => {
    expect(getActionUnavailableReason(action({ id: "txt-to-pdf" }))).toBeUndefined();
  });
});
