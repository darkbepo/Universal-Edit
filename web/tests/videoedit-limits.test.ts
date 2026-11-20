// The browser limits of the website video editor, in one place.
import { afterEach, describe, expect, it } from "vitest";
import {
  DIRECT_VIDEO, LIMITS, LIMITS_NOTE, MAX_ITEMS, MAX_LENGTH, estimateBytes, exportProblem, needsCopy,
  setLimitsForTests, sourceProblem,
} from "../src/videoedit/limits";

const GB = 1024 ** 3;
afterEach(() => setLimitsForTests(null));

describe("needsCopy", () => {
  it("is false for a direct type that decodes", () => {
    expect(needsCopy("mp4", true)).toBe(false);
    expect(needsCopy("MP4", true)).toBe(false);
    expect(needsCopy(".webm", true)).toBe(false);
  });
  it("is true when the decode check fails or the type isn't direct", () => {
    expect(needsCopy("mp4", false)).toBe(true);
    expect(needsCopy("avi", true)).toBe(true);
  });
  it("has the sets", () => {
    expect(DIRECT_VIDEO.has("mkv")).toBe(true);
    expect(MAX_ITEMS).toBe(500);
    expect(MAX_LENGTH).toBe(4 * 3600);
  });
});

describe("sourceProblem", () => {
  it("passes exactly at the limit and fails one byte over", () => {
    expect(sourceProblem({ name: "a.avi", size: GB, copy: true })).toBeNull();
    expect(sourceProblem({ name: "a.avi", size: GB + 1, copy: true })).toBe(
      "a.avi is too big for the browser (over 1 GB for this kind of file) — use the desktop app.",
    );
  });
  it("doesn't limit size when no copy is needed", () => {
    expect(sourceProblem({ name: "a.mp4", size: 5 * GB, copy: false })).toBeNull();
  });
  it("limits the picture side", () => {
    expect(sourceProblem({ name: "p.png", size: 1, copy: false, width: 16000, height: 10 })).toBeNull();
    expect(sourceProblem({ name: "p.png", size: 1, copy: false, width: 10, height: 16001 })).toBe(
      "p.png is too big for the browser (over 16,000 pixels on a side) — use the desktop app.",
    );
  });
});

describe("estimateBytes", () => {
  const base = { seconds: 10, width: 1000, height: 1000, fps: 10, audioBitrate: 0, audio: false };
  it.each([["very-high", 0.2], ["high", 0.1], ["medium", 0.05], ["low", 0.025]] as const)("%s", (q, bpp) => {
    expect(estimateBytes({ ...base, quality: q })).toBeCloseTo((bpp * 1000 * 1000 * 10 * 10 / 8) * 1.02, 3);
  });
  it("uses a bitrate and audio", () => {
    expect(estimateBytes({ ...base, quality: null, videoBitrate: 8_000_000, audioBitrate: 128_000, audio: true })).toBeCloseTo(
      ((8_000_000 + 128_000) * 10 / 8) * 1.02, 3,
    );
  });
  it("ignores audio bitrate without audio", () => {
    expect(estimateBytes({ ...base, quality: "low", audioBitrate: 128_000 })).toBe(estimateBytes({ ...base, quality: "low" }));
  });
});

describe("exportProblem", () => {
  it("never refuses when streaming", () => expect(exportProblem(50 * GB, true)).toBeNull());
  it("refuses a download over 2 GB", () => {
    expect(exportProblem(2 * GB, false)).toBeNull();
    expect(exportProblem(3 * GB, false)).toBe(
      "This video would be about 3 GB — too big for this browser (2 GB without saving straight to disk). Use Chrome or Edge, a lower quality, or the desktop app.",
    );
    expect(exportProblem(2.5 * GB, false)).toContain("about 2.5 GB");
  });
});

describe("setLimitsForTests", () => {
  it("lowers the numbers shown, null restores", () => {
    setLimitsForTests({ copyBytes: GB / 2, pictureSide: 1000 });
    expect(LIMITS.copyBytes).toBe(GB / 2);
    expect(sourceProblem({ name: "a.avi", size: GB, copy: true })).toContain("over 0.5 GB");
    expect(sourceProblem({ name: "p.png", size: 1, copy: false, width: 1001, height: 1 })).toContain("over 1,000 pixels");
    setLimitsForTests(null);
    expect(LIMITS).toEqual({ pictureSide: 16000, copyBytes: GB, downloadBytes: 2 * GB });
  });
  it("has the note", () => expect(LIMITS_NOTE).toContain("under 1 GB"));
});
