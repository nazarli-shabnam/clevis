import { describe, expect, it } from "vitest";
import { parseSseBlock, readSse, type SseMessage } from "@/lib/sse";

function streamOf(chunks: string[]): Response {
  const enc = new TextEncoder();
  return new Response(
    new ReadableStream({
      start(controller) {
        for (const c of chunks) controller.enqueue(enc.encode(c));
        controller.close();
      },
    }),
  );
}

describe("parseSseBlock", () => {
  it("reads event and data, defaulting the event name", () => {
    expect(parseSseBlock('event: activity_summary\ndata: {"a":1}')).toEqual({ event: "activity_summary", data: '{"a":1}' });
    expect(parseSseBlock("data: hi")).toEqual({ event: "message", data: "hi" });
  });

  it("joins multiple data lines and ignores comments", () => {
    expect(parseSseBlock(": heartbeat\ndata: a\ndata: b")).toEqual({ event: "message", data: "a\nb" });
  });

  it("returns null for a heartbeat-only block", () => {
    expect(parseSseBlock(": heartbeat")).toBeNull();
  });
});

describe("readSse", () => {
  it("emits messages split across chunk boundaries and skips heartbeats", async () => {
    const got: SseMessage[] = [];
    await readSse(streamOf(["event: a\nda", 'ta: {"n":1}\n\n: heartbeat\n\nevent: a\ndata: 2\r\n', "\r\n"]), (m) => got.push(m));
    expect(got).toEqual([
      { event: "a", data: '{"n":1}' },
      { event: "a", data: "2" },
    ]);
  });
});
