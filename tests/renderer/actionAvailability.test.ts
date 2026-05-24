import { describe, expect, it } from "vitest";
import { isActionAvailable } from "../../src/renderer/actionAvailability";

describe("action availability", () => {
  it("allows built-in text conversions without external engines", () => {
    expect(
      isActionAvailable(
        {
          id: "txt-to-html",
          label: "TXT 转 HTML",
          sourceFormats: ["txt"],
          targetFormat: "html",
          category: "document",
          engine: "pandoc"
        },
        []
      )
    ).toBe(true);
  });

  it("allows media conversions with the bundled ffmpeg runtime", () => {
    expect(
      isActionAvailable(
        {
          id: "mp4-to-mov",
          label: "MP4 转 MOV",
          sourceFormats: ["mp4"],
          targetFormat: "mov",
          category: "video",
          engine: "ffmpeg"
        },
        [{ name: "ffmpeg", available: false }]
      )
    ).toBe(true);
  });

  it("allows m4a to wav conversion with the bundled ffmpeg runtime", () => {
    expect(
      isActionAvailable(
        {
          id: "m4a-to-wav",
          label: "M4A 转 WAV",
          sourceFormats: ["m4a"],
          targetFormat: "wav",
          category: "audio",
          engine: "ffmpeg"
        },
        [{ name: "ffmpeg", available: false }]
      )
    ).toBe(true);
  });
});
