import { describe, expect, it } from "vitest";
import { POOL_SIZE, limitThreads, threadBudget } from "../src/engines/threads";

const count = (args: string[], flag: string) => args.filter((a) => a === flag).length;
const valueAfter = (args: string[], flag: string) => args[args.indexOf(flag) + 1];

describe("FFmpeg thread limits (multi-threaded ffmpeg.wasm)", () => {
  // ffmpeg.wasm-mt pre-starts 32 workers; a thread beyond that can't start while FFmpeg waits
  // for it, so a 12-core PC re-encoding a 1080x1920 phone video hung at 0 %.
  const video = ["-i", "/in1/v.mp4", "-map", "0:v:0", "-c:v", "libx264", "/out.mkv"];

  it("caps decoder, filter and encoder threads on a many-core PC", () => {
    const out = limitThreads(video, 12);
    expect(out.at(-1)).toBe("/out.mkv");
    expect(out.slice(0, 4)).toEqual(["-filter_threads", "4", "-filter_complex_threads", "4"]);
    expect(out[out.indexOf("-i") - 2]).toBe("-threads"); // decoder threads right before -i
    expect(out.at(-3)).toBe("-threads"); // encoder threads right before the output
    expect(out.at(-2)).toBe("4");
    expect(threadBudget(out)).toBeLessThan(POOL_SIZE);
  });

  it("stays under the pool on any core count", () => {
    const three = ["-i", "a.mp4", "-i", "b.srt", "-i", "c.srt", "/out.mkv"];
    for (const cores of [1, 2, 4, 8, 12, 16, 32, 64, 128]) {
      for (const args of [video, ["-i", "a.mp4", "-i", "b.srt", "/out.mp4"], three]) {
        expect(threadBudget(limitThreads(args, cores)), `${cores} cores`).toBeLessThan(POOL_SIZE);
      }
    }
  });

  it("never asks for more threads than cores", () => {
    const out = limitThreads(video, 2);
    expect(valueAfter(out, "-filter_threads")).toBe("2");
    expect(out.at(-2)).toBe("2");
  });

  it("uses one decoder thread per input when there are many inputs", () => {
    const out = limitThreads(["-i", "a.png", "-i", "b.png", "-i", "c.png", "/o.mp4"], 12);
    expect(count(out, "-threads")).toBe(4);
    expect(out.slice(4, 8)).toEqual(["-threads", "1", "-i", "a.png"]);
  });

  it("leaves the output path last (two-pass null output too)", () => {
    const out = limitThreads(["-i", "x.mp4", "-pass:v", "1", "-f", "null", "-"], 8);
    expect(out.at(-1)).toBe("-");
    expect(out.slice(-4)).toEqual(["null", "-threads", "4", "-"]);
  });
});
