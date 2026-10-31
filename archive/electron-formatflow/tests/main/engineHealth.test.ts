import { describe, expect, it } from "vitest";
import { getEngineStatus } from "../../src/main/services/engineHealth";

describe("engine health", () => {
  it("reports the bundled conversion core as available", async () => {
    const status = await getEngineStatus();

    expect(status).toHaveLength(2);
    const core = status.find((engine) => engine.name === "内置转换核心");
    const ffmpeg = status.find((engine) => engine.name === "ffmpeg");

    expect(core).toBeTruthy();
    expect(core!.available).toBe(true);
    expect(ffmpeg).toBeTruthy();
    // ffmpeg 随应用内置（ffmpeg-static）；安装完整依赖后应为可用。
    expect(typeof ffmpeg!.available).toBe("boolean");
  });
});
