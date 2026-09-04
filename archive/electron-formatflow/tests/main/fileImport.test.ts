import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { describe, expect, it } from "vitest";
import { importPaths } from "../../src/main/services/fileImport";

describe("file import", () => {
  it("recursively imports files from folders", async () => {
    const root = await mkdtemp(join(tmpdir(), "filetype-"));
    const nested = join(root, "nested");
    await mkdir(nested);
    await writeFile(join(root, "a.txt"), "hello");
    await writeFile(join(nested, "b.md"), "# hi");

    const items = await importPaths([root]);

    expect(items).toHaveLength(2);
    expect(items.map((item) => item.format)).toEqual(expect.arrayContaining(["txt", "markdown"]));
    expect(items.every((item) => item.selected)).toBe(true);
  });
});
