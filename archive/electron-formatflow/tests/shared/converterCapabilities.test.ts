import { describe, expect, it } from "vitest";
import {
  isBuiltInConversionSupported,
  isArchiveAction
} from "../../src/shared/converterCapabilities";

describe("converter capabilities", () => {
  it("supports the built-in text matrix", () => {
    expect(isBuiltInConversionSupported("txt", "html", "txt-to-html")).toBe(true);
    expect(isBuiltInConversionSupported("markdown", "pdf", "md-to-pdf")).toBe(true);
    expect(isBuiltInConversionSupported("html", "txt", "html-to-txt")).toBe(true);
  });

  it("supports word and spreadsheet families", () => {
    expect(isBuiltInConversionSupported("docx", "pdf", "word-to-pdf")).toBe(true);
    expect(isBuiltInConversionSupported("doc", "txt", "doc-to-txt")).toBe(true);
    expect(isBuiltInConversionSupported("xlsx", "csv", "excel-to-csv")).toBe(true);
    expect(isBuiltInConversionSupported("csv", "pdf", "csv-to-pdf")).toBe(true);
  });

  it("supports pdf text extraction but not pdf to png", () => {
    expect(isBuiltInConversionSupported("pdf", "txt", "pdf-to-txt")).toBe(true);
    expect(isBuiltInConversionSupported("pdf", "docx", "pdf-to-word")).toBe(true);
    expect(isBuiltInConversionSupported("pdf", "png", "pdf-to-image")).toBe(false);
  });

  it("supports image and media families", () => {
    expect(isBuiltInConversionSupported("jpg", "png", "jpg-to-png")).toBe(true);
    expect(isBuiltInConversionSupported("gif", "webp", "gif-to-webp")).toBe(true);
    expect(isBuiltInConversionSupported("mp4", "mov", "mp4-to-mov")).toBe(true);
    expect(isBuiltInConversionSupported("mp4", "mp3", "video-to-mp3")).toBe(true);
    expect(isBuiltInConversionSupported("m4a", "wav", "m4a-to-wav")).toBe(true);
  });

  it("rejects unsupported combinations", () => {
    expect(isBuiltInConversionSupported("docx", "png", "x")).toBe(false);
    expect(isBuiltInConversionSupported("mp3", "pdf", "x")).toBe(false);
    expect(isBuiltInConversionSupported("zip", "txt", "x")).toBe(false);
  });

  it("treats archive actions as supported regardless of target format", () => {
    expect(isArchiveAction("compress-to-zip")).toBe(true);
    expect(isArchiveAction("zip-extract")).toBe(true);
    expect(isArchiveAction("rar-extract")).toBe(true);
    expect(isArchiveAction("not-an-action")).toBe(false);
  });
});
