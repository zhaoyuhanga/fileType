import { protocol } from "electron";
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { extname } from "node:path";
import { Readable } from "node:stream";
import { DOC_STREAM_SCHEME } from "../../shared/constants.js";

/**
 * docstream:// 自定义协议：把本地媒体文件以支持 Range 的流方式提供给渲染层
 * <video> 播放（拖动进度条需要 Range）。dev 模式页面为 http 源，打包后为
 * file:// 源，直接引用 file:// 本地视频会被跨源策略拦截，因此统一走本协议。
 */

export { DOC_STREAM_SCHEME };

const MEDIA_MIME_BY_EXTENSION: Record<string, string> = {
  ".mp4": "video/mp4",
  ".m4v": "video/x-m4v",
  ".mov": "video/quicktime",
  ".webm": "video/webm",
  ".mkv": "video/x-matroska"
};

/** 必须在 app ready 之前调用（协议特权声明）。 */
export function registerDocStreamScheme(): void {
  protocol.registerSchemesAsPrivileged([
    {
      scheme: DOC_STREAM_SCHEME,
      privileges: {
        standard: true,
        secure: true,
        supportFetchAPI: true,
        stream: true
      }
    }
  ]);
}

/** 在 app ready 之后注册协议处理器。 */
export function setupDocStreamProtocol(): void {
  protocol.handle(DOC_STREAM_SCHEME, async (request) => {
    try {
      const url = new URL(request.url);
      const filePath = url.searchParams.get("p");
      if (!filePath) throw new Error("missing path");

      const info = await stat(filePath);
      if (!info.isFile()) throw new Error("not a file");

      const mime = MEDIA_MIME_BY_EXTENSION[extname(filePath).toLowerCase()] ?? "application/octet-stream";
      const size = info.size;
      const range = parseRange(request.headers.get("range"), size);

      if (range) {
        const stream = createReadStream(filePath, { start: range.start, end: range.end });
        return new Response(Readable.toWeb(stream) as unknown as ReadableStream, {
          status: 206,
          headers: {
            "Content-Type": mime,
            "Accept-Ranges": "bytes",
            "Content-Range": `bytes ${range.start}-${range.end}/${size}`,
            "Content-Length": String(range.end - range.start + 1),
            "Access-Control-Allow-Origin": "*"
          }
        });
      }

      const stream = createReadStream(filePath);
      return new Response(Readable.toWeb(stream) as unknown as ReadableStream, {
        status: 200,
        headers: {
          "Content-Type": mime,
          "Accept-Ranges": "bytes",
          "Content-Length": String(size),
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Headers": "Range"
        }
      });
    } catch {
      return new Response("Not Found", { status: 404 });
    }
  });
}

interface ByteRange {
  start: number;
  end: number;
}

function parseRange(header: string | null, size: number): ByteRange | null {
  if (!header || size <= 0) return null;
  const match = /^bytes=(\d*)-(\d*)$/.exec(header.trim());
  if (!match) return null;

  const [, rawStart, rawEnd] = match;
  if (rawStart === "" && rawEnd === "") return null;

  let start: number;
  let end: number;
  if (rawStart === "") {
    // 末尾后缀请求 bytes=-N
    const suffix = Number(rawEnd);
    start = Math.max(0, size - suffix);
    end = size - 1;
  } else {
    start = Number(rawStart);
    end = rawEnd === "" ? size - 1 : Number(rawEnd);
  }

  if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || start >= size || start > end) return null;
  return { start, end: Math.min(end, size - 1) };
}
