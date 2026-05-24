import { describe, expect, it } from "vitest";
import { buildOutputPath, replaceExtension } from "../../src/shared/outputPaths";

describe("output paths", () => {
  it("builds an output path in the requested folder", () => {
    expect(
      buildOutputPath({
        sourcePath: "C:/docs/example.docx",
        outputDir: "C:/out",
        targetFormat: "pdf"
      })
    ).toBe("C:\\out\\example.pdf");
  });

  it("adds a collision suffix", () => {
    expect(
      buildOutputPath({
        sourcePath: "C:/docs/example.docx",
        outputDir: "C:/out",
        targetFormat: "pdf",
        collisionIndex: 1
      })
    ).toBe("C:\\out\\example (2).pdf");
  });

  it("replaces the extension in place", () => {
    expect(replaceExtension("C:/docs/example.docx", "txt")).toBe("C:\\docs\\example.txt");
  });
});
