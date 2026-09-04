/** JSON 美化工具：解析并格式化为缩进文本，失败时给出可读的中文错误。 */

export type JsonFormatResult = { ok: true; text: string } | { ok: false; error: string };

export function formatJsonText(source: string, indent = 2): JsonFormatResult {
  const trimmed = source.replace(/^\uFEFF/, "");
  if (!trimmed.trim()) return { ok: true, text: "" };

  try {
    const parsed: unknown = JSON.parse(trimmed);
    return { ok: true, text: JSON.stringify(parsed, null, indent) };
  } catch (error) {
    const raw = error instanceof Error ? error.message : String(error);
    const position = extractPosition(raw);
    const location = position == null ? "" : `（${locateAt(trimmed, position)}）`;
    return {
      ok: false,
      error: `JSON 语法错误${location}：${raw}。请检查引号、逗号与括号是否完整匹配。`
    };
  }
}

function extractPosition(message: string): number | null {
  const match = /position (\d+)/.exec(message);
  return match ? Number(match[1]) : null;
}

function locateAt(text: string, index: number): string {
  const before = text.slice(0, Math.min(index, text.length));
  const line = before.split("\n").length;
  const lastLineBreak = before.lastIndexOf("\n");
  const column = before.length - lastLineBreak;
  return `第 ${line} 行，第 ${column} 列`;
}
