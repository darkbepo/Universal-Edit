// Playback maths: thumbnail / waveform counts, what sound a window needs, painting slots, resampling.
import { describe, expect, it } from "vitest";
import {
  PeakAccumulator, THUMB_HEIGHT, WAVE_H, audioNeeds, startTime, thumbCount, thumbSlots, thumbTimes, waveBars, waveBuckets,
} from "../src/ui/videoeditor/playmath";
import { toStereo48k } from "../src/videoedit/resample";
import { audioClip, videoClip, type AudioClip, type MainItem, type Project, type Source } from "../src/videoedit/project";

const src = (id: string, kind: Source["kind"], duration: number, has_audio = true): Source => ({
  id, path: `${id}.x`, kind, duration, width: 64, height: 36, fps: 25, has_audio, size: 1,
});
const SOURCES = [src("s1", "video", 60), src("m", "audio", 30), src("p", "image", 0, false)];
function project(main: MainItem[], music: AudioClip[] = []): Project {
  return {
    settings: { resolution: "custom", width: 64, height: 36, fps: "25", background: "#000000" },
    sources: SOURCES, main, text_rows: [[]], music_rows: [music],
  };
}

describe("counts and times", () => {
  it("thumbCount", () => {
    expect(thumbCount(src("a", "video", 10))).toBe(7);
    expect(thumbCount(src("a", "video", 0.1))).toBe(1);
    expect(thumbCount(src("a", "video", 1000))).toBe(40);
    expect(thumbCount(src("a", "image", 0))).toBe(1);
    expect(THUMB_HEIGHT).toBe(56);
  });
  it("waveBuckets", () => {
    expect(waveBuckets(src("a", "audio", 0.5))).toBe(50);
    expect(waveBuckets(src("a", "audio", 10))).toBe(500);
    expect(waveBuckets(src("a", "audio", 1000))).toBe(4000);
  });
  it("thumbTimes", () => {
    expect(thumbTimes(src("a", "video", 10), 4)).toEqual([1.25, 3.75, 6.25, 8.75]);
    expect(thumbTimes(src("a", "image", 0), 1)).toEqual([0]);
  });
  it("startTime restarts at the end", () => {
    expect(startTime(3.99, 4)).toBe(0);
    expect(startTime(1, 4)).toBe(1);
  });
});

describe("audioNeeds", () => {
  it("a video clip's source window", () => {
    const p = project([videoClip("a", "s1", 2, 6)]);
    expect(audioNeeds(p, 0, 24000)).toEqual([{ sourceId: "s1", from: 2, to: 2.5 }]);
  });
  it("a muted clip needs nothing", () => {
    const p = project([{ ...videoClip("a", "s1", 2, 6), mute: true }]);
    expect(audioNeeds(p, 0, 24000)).toEqual([]);
  });
  it("a 2x clip reads twice as much source", () => {
    const p = project([{ ...videoClip("a", "s1", 0, 8), speed: 2 }]);
    const [n] = audioNeeds(p, 0, 24000);
    expect(n.to - n.from).toBeCloseTo(1, 3);
  });
  it("a looping music clip is merged to one range", () => {
    const clip = { ...audioClip("c", "m", 0, 1, 3), loop_until: 7 };
    const p = project([videoClip("a", "s1", 0, 10)], [clip]);
    const needs = audioNeeds(p, 0, 7 * 48000).filter((n) => n.sourceId === "m");
    expect(needs.length).toBe(1);
    expect(needs[0].from).toBeCloseTo(1, 3);
    expect(needs[0].to).toBeCloseTo(3, 3);
  });
});

describe("thumbSlots", () => {
  it("covers the rect and skips what is outside the view", () => {
    const rect = { x: 100, y: 10, w: 500, h: 56 + WAVE_H };
    const all = thumbSlots(rect, 2, 5, 0, 10000);
    expect(all[0].rect).toEqual({ x: 100, y: 10, w: 112, h: 56 });
    expect(all.length).toBe(5);
    expect(all[4].index).toBe(4);
    const clipped = thumbSlots(rect, 2, 5, 330, 450);
    expect(clipped.map((s) => s.rect.x)).toEqual([324, 436]);
  });
});

describe("waveBars", () => {
  it("maps in..out onto the peaks", () => {
    const bars = waveBars({ x: 0, y: 0, w: 10, h: 16 }, [0, 1], 0, 5, 10, 0, 100);
    expect(bars[0]).toEqual({ x: 0, half: 1 });
    expect(bars.every((b) => b.half >= 1 && b.x % 2 === 0)).toBe(true);
    expect(bars.length).toBe(5);
  });
  it("respects the header", () => {
    const bars = waveBars({ x: 0, y: 0, w: 100, h: 16 }, [1], 0, 1, 1, 51, 100);
    expect(bars[0].x).toBe(50);
    expect(bars[0].half).toBe(8);
  });
});

describe("PeakAccumulator", () => {
  it("takes the peak per bucket across chunks", () => {
    const a = new PeakAccumulator(4, 8);
    a.add(Float32Array.from([0.1, -0.9, 0.2]));
    a.add(Float32Array.from([0.3, 0, 0, 0.5, 0]));
    const r = a.result()!;
    expect(Array.from(r).map((x) => Math.round(x * 10) / 10)).toEqual([0.9, 0.3, 0, 0.5]);
  });
  it("is null when nothing was added", () => {
    expect(new PeakAccumulator(4, 8).result()).toBeNull();
  });
});

describe("toStereo48k", () => {
  it("mono becomes two copies", () => {
    const out = toStereo48k([Float32Array.from([1, 2, 3])], 48000);
    expect(out.length).toBe(2);
    expect(Array.from(out[0])).toEqual([1, 2, 3]);
    expect(Array.from(out[1])).toEqual([1, 2, 3]);
  });
  it("more than two channels keeps the first two", () => {
    const out = toStereo48k([Float32Array.of(1), Float32Array.of(2), Float32Array.of(3)], 48000);
    expect([out[0][0], out[1][0]]).toEqual([1, 2]);
  });
  it("24 kHz doubles with interpolation", () => {
    const out = toStereo48k([Float32Array.from([0, 1])], 24000);
    expect(out[0].length).toBe(4);
    expect(out[0][0]).toBe(0);
    expect(out[0][1]).toBeCloseTo(0.5, 6);
    expect(out[0][2]).toBeCloseTo(1, 6);
  });
  it("44.1 kHz 441 -> 480", () => {
    expect(toStereo48k([new Float32Array(441)], 44100)[0].length).toBe(480);
  });
});
