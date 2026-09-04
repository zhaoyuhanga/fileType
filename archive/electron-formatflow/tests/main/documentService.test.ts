import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { createRequire } from "node:module";
import { describe, expect, it } from "vitest";
import {
  copyDocument,
  readDocument,
  writeTextDocument
} from "../../src/main/services/documentService";

const require = createRequire(import.meta.url);
const iconv = require("iconv-lite") as typeof import("iconv-lite");

async function tempRoot(): Promise<string> {
  return mkdtemp(join(tmpdir(), "docsvc-"));
}

describe("document service", () => {
  it("reads a utf-8 text document", async () => {
    const root = await tempRoot();
    const filePath = join(root, "note.txt");
    await writeFile(filePath, "你好，world", "utf8");

    const doc = await readDocument(filePath);

    expect(doc).toMatchObject({
      kind: "text",
      format: "txt",
      category: "document",
      name: "note.txt"
    });
    expect(doc.content).toBe("你好，world");
    expect(doc.encoding).toContain("UTF-8");
  });

  it("strips a utf-8 BOM when reading", async () => {
    const root = await tempRoot();
    const filePath = join(root, "bom.md");
    await writeFile(filePath, "\uFEFF# 标题", "utf8");

    const doc = await readDocument(filePath);

    expect(doc.format).toBe("markdown");
    expect(doc.content).toBe("# 标题");
    expect(doc.encoding).toContain("BOM");
  });

  it("falls back to GB18030 when bytes are not valid utf-8", async () => {
    const root = await tempRoot();
    const filePath = join(root, "gbk.txt");
    await writeFile(filePath, iconv.encode("中文旧编码", "gb18030"));

    const doc = await readDocument(filePath);

    expect(doc.content).toBe("中文旧编码");
    expect(doc.encoding).toContain("GB18030");
  });

  it("returns media documents without text content", async () => {
    const root = await tempRoot();
    const filePath = join(root, "clip.mp4");
    await writeFile(filePath, Buffer.from([0, 0, 0, 0]));

    const doc = await readDocument(filePath);

    expect(doc).toMatchObject({ kind: "media", format: "mp4", category: "video" });
    expect(doc.content).toBeUndefined();
  });

  it("rejects unsupported formats with a friendly message", async () => {
    const root = await tempRoot();
    const filePath = join(root, "data.xyz");
    await writeFile(filePath, "x", "utf8");

    await expect(readDocument(filePath)).rejects.toThrow(/暂不支持预览/);
  });

  it("writes text documents as utf-8", async () => {
    const root = await tempRoot();
    const filePath = join(root, "out.txt");

    await writeTextDocument(filePath, "已保存内容");

    await expect(readFile(filePath, "utf8")).resolves.toBe("已保存内容");
  });

  it("copies binary documents byte for byte", async () => {
    const root = await tempRoot();
    const source = join(root, "a.mp4");
    const target = join(root, "b.mp4");
    const bytes = Buffer.from([0, 1, 2, 3, 4, 5]);

    await writeFile(source, bytes);
    await copyDocument(source, target);

    await expect(readFile(target)).resolves.toEqual(bytes);
  });
});
