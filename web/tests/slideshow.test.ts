import { describe, expect, it } from "vitest";
import { XFADE_GROUP, xfadeGraph, xfadePasses, type XfadeItem } from "../src/converters/slideshow";

const pics = (n: number, d: number, td: number): XfadeItem[] =>
  [...Array(n).keys()].map((i) => ({ path: `/slides/img${i}.png`, dur: d + td, image: true }));

describe("crossfade slideshow batching", () => {
  it("one pass up to the group size, then groups + joins", () => {
    expect(xfadePasses(2)).toBe(1);
    expect(xfadePasses(XFADE_GROUP)).toBe(1);
    expect(xfadePasses(13)).toBe(2); // 12 rendered + 1 carried over, then the join
    expect(xfadePasses(40)).toBe(5); // 4 groups, then the join
    expect(xfadePasses(200)).toBe(20); // 17 groups → 2 joins of those → final
  });

  it("pictures get the classic offsets: transition i starts at i × d", () => {
    const x = xfadeGraph(pics(4, 2, 0.5), 30, "fade", 0.5);
    expect(x.graph).toContain("offset=2.000[x1]");
    expect(x.graph).toContain("offset=4.000[x2]");
    expect(x.graph).toContain("offset=6.000[x3]");
    expect(x.total).toBeCloseTo(4 * 2 + 0.5);
    expect(x.inputs.filter((a) => a === "-loop")).toHaveLength(4);
  });

  it("joining rendered groups gives the same total length as one pass", () => {
    const d = 2, td = 0.5;
    const whole = xfadeGraph(pics(30, d, td), 30, "fade", td).total;
    const groups = [12, 12, 6].map((k) => xfadeGraph(pics(k, d, td), 30, "fade", td).total);
    const clips: XfadeItem[] = groups.map((dur, i) => ({ path: `/slides/clip0_${i}.mkv`, dur, image: false }));
    const joined = xfadeGraph(clips, 30, "fade", td);
    expect(joined.total).toBeCloseTo(whole);
    expect(joined.graph).toContain("offset=24.000[x1]"); // picture 12 starts at 12 × d, as before
    expect(joined.inputs).not.toContain("-loop"); // clips are videos, not looped stills
  });
});
