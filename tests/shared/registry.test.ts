import { describe, expect, it } from "vitest";
import { getCommonActionsForFormats, getConverterActionsForFormat } from "../../src/shared/converters/registry";

describe("converter registry", () => {
  it("returns document actions for docx", () => {
    const actions = getConverterActionsForFormat("docx").map((action) => action.targetFormat);

    expect(actions).toEqual(expect.arrayContaining(["pdf", "html", "markdown", "txt"]));
  });

  it("returns image actions for png", () => {
    const actions = getConverterActionsForFormat("png").map((action) => action.targetFormat);

    expect(actions).toEqual(expect.arrayContaining(["jpg", "webp", "bmp", "gif"]));
  });

  it("returns video extraction actions for mp4", () => {
    const actions = getConverterActionsForFormat("mp4").map((action) => action.id);

    expect(actions).toContain("video-to-mp3");
    expect(actions).toContain("video-to-wav");
  });

  it("returns audio actions for m4a", () => {
    const actions = getConverterActionsForFormat("m4a").map((action) => action.targetFormat);

    expect(actions).toEqual(expect.arrayContaining(["mp3", "wav"]));
  });

  it("returns only shared actions for mixed image selection", () => {
    const actions = getCommonActionsForFormats(["jpg", "png"]).map((action) => action.targetFormat);

    expect(actions).toEqual(expect.arrayContaining(["webp", "bmp", "gif"]));
    expect(actions).not.toContain("jpg");
    expect(actions).not.toContain("png");
  });

  it("returns zip-extract action for zip source format", () => {
    const actions = getConverterActionsForFormat("zip").map((action) => action.id);

    expect(actions).toContain("zip-extract");
    expect(actions).not.toContain("compress-to-zip");
  });
});
