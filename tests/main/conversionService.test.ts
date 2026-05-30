import { mkdtemp, readFile, stat, writeFile } from "node:fs/promises";
import { spawn } from "node:child_process";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { describe, expect, it } from "vitest";
import ffmpegPath from "ffmpeg-static";
import JSZip from "jszip";
import { runConversion } from "../../src/main/services/conversionService";

describe("conversion service", () => {
  it("converts txt to html with the built-in converter", async () => {
    const root = await mkdtemp(join(tmpdir(), "convert-"));
    const sourcePath = join(root, "note.txt");
    const outputDir = join(root, "out");
    await writeFile(sourcePath, "hello\nworld", "utf8");

    const result = await runConversion({
      outputDir,
      engineRoot: root,
      file: {
        id: "note",
        path: sourcePath,
        name: "note.txt",
        extension: "txt",
        format: "txt",
        category: "document",
        sizeBytes: 11,
        selected: true,
        status: "queued",
        progress: 0
      },
      action: {
        id: "txt-to-html",
        label: "TXT 转 HTML",
        sourceFormats: ["txt"],
        targetFormat: "html",
        category: "document",
        engine: "pandoc"
      }
    });

    expect(result.status).toBe("succeeded");
    expect(result.outputPath).toBeTruthy();
    await expect(readFile(result.outputPath!, "utf8")).resolves.toContain("<p>hello</p>");
  });

  it("converts txt to pdf with the built-in converter", async () => {
    const root = await mkdtemp(join(tmpdir(), "convert-"));
    const sourcePath = join(root, "note.txt");
    const outputDir = join(root, "out");
    await writeFile(sourcePath, "hello pdf", "utf8");

    const result = await runConversion({
      outputDir,
      engineRoot: root,
      file: {
        id: "note",
        path: sourcePath,
        name: "note.txt",
        extension: "txt",
        format: "txt",
        category: "document",
        sizeBytes: 9,
        selected: true,
        status: "queued",
        progress: 0
      },
      action: {
        id: "txt-to-pdf",
        label: "TXT 转 PDF",
        sourceFormats: ["txt"],
        targetFormat: "pdf",
        category: "document",
        engine: "pandoc"
      }
    });

    expect(result.status).toBe("succeeded");
    expect(result.targetFormat).toBe("pdf");
    expect(result.outputPath).toMatch(/\.pdf$/);
  });

  it("extracts text from html documents saved with a doc extension", async () => {
    const root = await mkdtemp(join(tmpdir(), "convert-"));
    const sourcePath = join(root, "legacy.doc");
    const outputDir = join(root, "out");
    await writeFile(sourcePath, "<html><body><p>legacy word text</p></body></html>", "utf8");

    const result = await runConversion({
      outputDir,
      engineRoot: root,
      file: {
        id: "legacy",
        path: sourcePath,
        name: "legacy.doc",
        extension: "doc",
        format: "doc",
        category: "document",
        sizeBytes: 51,
        selected: true,
        status: "queued",
        progress: 0
      },
      action: {
        id: "doc-to-txt",
        label: "DOC 杞?TXT",
        sourceFormats: ["doc"],
        targetFormat: "txt",
        category: "document",
        engine: "libreoffice"
      }
    });

    expect(result.status).toBe("succeeded");
    await expect(readFile(result.outputPath!, "utf8")).resolves.toContain("legacy word text");
  });

  it("cleans mhtml documents saved with a doc extension", async () => {
    const root = await mkdtemp(join(tmpdir(), "convert-"));
    const sourcePath = join(root, "mhtml.doc");
    const outputDir = join(root, "out");
    await writeFile(
      sourcePath,
      [
        "MIME-Version: 1.0",
        'Content-Type: multipart/related; boundary="----=_NextPart_000"',
        "",
        "------=_NextPart_000",
        'Content-Type: text/html; charset="utf-8"',
        "Content-Transfer-Encoding: quoted-printable",
        "",
        "<html><body><p>MHTML word text</p></body></html>",
        "------=_NextPart_000--"
      ].join("\r\n"),
      "utf8"
    );

    const result = await runConversion({
      outputDir,
      engineRoot: root,
      file: {
        id: "mhtml",
        path: sourcePath,
        name: "mhtml.doc",
        extension: "doc",
        format: "doc",
        category: "document",
        sizeBytes: 200,
        selected: true,
        status: "queued",
        progress: 0
      },
      action: {
        id: "doc-to-txt",
        label: "DOC 转 TXT",
        sourceFormats: ["doc"],
        targetFormat: "txt",
        category: "document",
        engine: "libreoffice"
      }
    });

    expect(result.status).toBe("succeeded");
    const output = await readFile(result.outputPath!, "utf8");
    expect(output).toContain("MHTML word text");
    expect(output).not.toContain("MIME-Version");
  });

  it("converts pdf text to docx", async () => {
    const root = await mkdtemp(join(tmpdir(), "convert-"));
    const sourcePath = join(root, "note.txt");
    const outputDir = join(root, "out");
    await writeFile(sourcePath, "pdf to word", "utf8");

    const pdf = await runConversion({
      outputDir,
      engineRoot: root,
      file: {
        id: "note",
        path: sourcePath,
        name: "note.txt",
        extension: "txt",
        format: "txt",
        category: "document",
        sizeBytes: 11,
        selected: true,
        status: "queued",
        progress: 0
      },
      action: {
        id: "txt-to-pdf",
        label: "TXT 转 PDF",
        sourceFormats: ["txt"],
        targetFormat: "pdf",
        category: "document",
        engine: "pandoc"
      }
    });

    const result = await runConversion({
      outputDir,
      engineRoot: root,
      file: {
        id: "pdf",
        path: pdf.outputPath!,
        name: "note.pdf",
        extension: "pdf",
        format: "pdf",
        category: "document",
        sizeBytes: 100,
        selected: true,
        status: "queued",
        progress: 0
      },
      action: {
        id: "pdf-to-word",
        label: "PDF 转 Word",
        sourceFormats: ["pdf"],
        targetFormat: "docx",
        category: "document",
        engine: "pdf"
      }
    });

    expect(result.status).toBe("succeeded");
    expect(result.targetFormat).toBe("docx");
    expect(result.outputPath).toMatch(/\.docx$/);
    await expect(stat(result.outputPath!)).resolves.toMatchObject({ size: expect.any(Number) });
  });

  it("converts m4a to wav with the bundled ffmpeg runtime", async () => {
    if (!ffmpegPath) throw new Error("ffmpeg-static is unavailable");

    const root = await mkdtemp(join(tmpdir(), "convert-"));
    const sourcePath = join(root, "tone.m4a");
    const outputDir = join(root, "out");
    await runFfmpeg(["-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.1", "-c:a", "aac", sourcePath]);

    const result = await runConversion({
      outputDir,
      engineRoot: root,
      file: {
        id: "tone",
        path: sourcePath,
        name: "tone.m4a",
        extension: "m4a",
        format: "m4a",
        category: "audio",
        sizeBytes: 1,
        selected: true,
        status: "queued",
        progress: 0
      },
      action: {
        id: "m4a-to-wav",
        label: "M4A 转 WAV",
        sourceFormats: ["m4a"],
        targetFormat: "wav",
        category: "audio",
        engine: "ffmpeg"
      }
    });

    expect(result.status).toBe("succeeded");
    expect(result.targetFormat).toBe("wav");
    expect(result.outputPath).toMatch(/\.wav$/);
    await expect(stat(result.outputPath!)).resolves.toMatchObject({ size: expect.any(Number) });
  });
});

  it("compresses a single file into a zip archive", async () => {
    const root = await mkdtemp(join(tmpdir(), "convert-"));
    const sourcePath = join(root, "note.txt");
    const outputDir = join(root, "out");
    await writeFile(sourcePath, "hello zip", "utf8");

    const result = await runConversion({
      outputDir,
      engineRoot: root,
      file: {
        id: "note",
        path: sourcePath,
        name: "note.txt",
        extension: "txt",
        format: "txt",
        category: "document",
        sizeBytes: 9,
        selected: true,
        status: "queued",
        progress: 0
      },
      action: {
        id: "compress-to-zip",
        label: "压缩为 ZIP",
        sourceFormats: ["txt"],
        targetFormat: "zip",
        category: "archive",
        engine: "zip"
      }
    });

    expect(result.status).toBe("succeeded");
    expect(result.targetFormat).toBe("zip");
    expect(result.outputPath).toMatch(/\.zip$/);

    const zipData = await readFile(result.outputPath!);
    const zip = await JSZip.loadAsync(zipData);
    expect(zip.file("note.txt")).toBeTruthy();
    const content = await zip.file("note.txt")!.async("string");
    expect(content).toBe("hello zip");
  });

  it("extracts files from a valid zip archive", async () => {
    const root = await mkdtemp(join(tmpdir(), "convert-"));
    const sourcePath = join(root, "bundle.zip");
    const outputDir = join(root, "out");

    const zip = new JSZip();
    zip.file("readme.txt", "hello from zip");
    zip.file("sub/note.txt", "nested file");
    const zipBuffer = await zip.generateAsync({ type: "nodebuffer" });
    await writeFile(sourcePath, zipBuffer);

    const result = await runConversion({
      outputDir,
      engineRoot: root,
      file: {
        id: "bundle",
        path: sourcePath,
        name: "bundle.zip",
        extension: "zip",
        format: "zip",
        category: "archive",
        sizeBytes: zipBuffer.length,
        selected: true,
        status: "queued",
        progress: 0
      },
      action: {
        id: "zip-extract",
        label: "ZIP 解压",
        sourceFormats: ["zip"],
        targetFormat: "zip",
        category: "archive",
        engine: "zip"
      }
    });

    expect(result.status).toBe("succeeded");
    expect(result.outputPath).toBeTruthy();
    await expect(readFile(join(result.outputPath!, "readme.txt"), "utf8")).resolves.toBe("hello from zip");
    await expect(readFile(join(result.outputPath!, "sub", "note.txt"), "utf8")).resolves.toBe("nested file");
  });

  it("rejects a corrupt zip archive for extraction", async () => {
    const root = await mkdtemp(join(tmpdir(), "convert-"));
    const sourcePath = join(root, "bad.zip");
    const outputDir = join(root, "out");
    await writeFile(sourcePath, "not a zip file at all", "utf8");

    const result = await runConversion({
      outputDir,
      engineRoot: root,
      file: {
        id: "bad",
        path: sourcePath,
        name: "bad.zip",
        extension: "zip",
        format: "zip",
        category: "archive",
        sizeBytes: 22,
        selected: true,
        status: "queued",
        progress: 0
      },
      action: {
        id: "zip-extract",
        label: "ZIP 解压",
        sourceFormats: ["zip"],
        targetFormat: "zip",
        category: "archive",
        engine: "zip"
      }
    });

    expect(result.status).toBe("failed");
    expect(result.message).toContain("不是有效的 ZIP");
  });

  it("reports an empty zip extraction as failed", async () => {
    const root = await mkdtemp(join(tmpdir(), "convert-"));
    const sourcePath = join(root, "empty.zip");
    const outputDir = join(root, "out");

    const zip = new JSZip();
    const zipBuffer = await zip.generateAsync({ type: "nodebuffer" });
    await writeFile(sourcePath, zipBuffer);

    const result = await runConversion({
      outputDir,
      engineRoot: root,
      file: {
        id: "empty",
        path: sourcePath,
        name: "empty.zip",
        extension: "zip",
        format: "zip",
        category: "archive",
        sizeBytes: zipBuffer.length,
        selected: true,
        status: "queued",
        progress: 0
      },
      action: {
        id: "zip-extract",
        label: "ZIP 解压",
        sourceFormats: ["zip"],
        targetFormat: "zip",
        category: "archive",
        engine: "zip"
      }
    });

    expect(result.status).toBe("failed");
    expect(result.message).toContain("空");
  });

function runFfmpeg(args: string[]): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn(ffmpegPath as string, args, { stdio: "ignore", windowsHide: true });
    child.on("error", reject);
    child.on("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`ffmpeg exited with ${code}`));
    });
  });
}
