import { mkdtemp } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { describe, expect, it } from "vitest";
import { getEngineStatus } from "../../src/main/services/engineHealth";

describe("engine health", () => {
  it("marks missing engines as unavailable", async () => {
    const root = await mkdtemp(join(tmpdir(), "engines-"));
    const status = await getEngineStatus(root);

    expect(status).toHaveLength(4);
    expect(status.every((engine) => engine.available === false)).toBe(true);
  });
});
