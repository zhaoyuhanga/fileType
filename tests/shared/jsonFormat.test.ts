import { describe, expect, it } from "vitest";
import { formatJsonText } from "../../src/shared/jsonFormat";

describe("json formatting", () => {
  it("pretty-prints valid json with two-space indent", () => {
    const result = formatJsonText('{"b":1,"a":{"c":true}}');

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.text).toBe('{\n  "b": 1,\n  "a": {\n    "c": true\n  }\n}');
    }
  });

  it("keeps an empty document as empty text", () => {
    const result = formatJsonText("   ");

    expect(result.ok).toBe(true);
    if (result.ok) expect(result.text).toBe("");
  });

  it("reports a friendly error for invalid json", () => {
    const result = formatJsonText('{\n  "a": 1,\n  "b": ,\n}');

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toContain("JSON 语法错误");
      expect(result.error).toContain("请检查引号、逗号与括号");
    }
  });

  it("adds line and column when the engine reports a position", () => {
    const result = formatJsonText('{"a": 1, }');

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toMatch(/JSON 语法错误(（第 \d+ 行，第 \d+ 列）)?：/);
    }
  });
});
