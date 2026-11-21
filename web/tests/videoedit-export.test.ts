// The export maths of the website video editor (same export_size cases as the desktop).
import { describe, expect, it, vi } from "vitest";
import cases from "../../tests/fixtures/videoedit_cases.json";
import { defaults } from "../src/core/options";
import {
  EXPORT_OPTIONS, availableCodecs, chooseCodecs, evenSize, exportEstimate, exportName, exportSize, frameCount,
  gifSize, type Encoders,
} from "../src/videoedit/export";
import type { Project } from "../src/videoedit/project";

const NO_SOUND = "Export with sound needs Chrome or Edge — export without sound, or use the desktop app.";
const enc = (video: string[], audio: string[]): Encoders => ({
  video: async (c) => video.includes(c),
  audio: async (c) => audio.includes(c),
});
const vals = (o: Record<string, string | boolean | number>) => ({ ...defaults(EXPORT_OPTIONS), ...o });
const first = cases.export_size[0].project as unknown as Project;

describe("exportSize", () => {
  it.each(cases.export_size)("$name", (c) => {
    expect(exportSize(c.project as unknown as Project, c.resolution)).toEqual(c.size);
  });
});

describe("sizes and names", () => {
  it("evenSize rounds down", () => {
    expect(evenSize([1921, 1081])).toEqual([1920, 1080]);
    expect(evenSize([1, 0])).toEqual([2, 2]);
  });
  it("gifSize keeps the aspect, even", () => {
    expect(gifSize(first, 481)).toEqual([480, 270]);
  });
  it("exportName", () => {
    const p = { ...first, sources: [{ ...first.sources[0], path: "C:\\x\\Holiday.mov" }] };
    expect(exportName(p, "mp4")).toBe("Holiday edit.mp4");
    expect(exportName({ ...first, sources: [] }, "webm")).toBe("video edit.webm");
  });
  it("frameCount", () => {
    expect(frameCount(10, 30)).toBe(300);
    expect(frameCount(0, 30)).toBe(1);
  });
});

describe("chooseCodecs", () => {
  it("auto mp4 prefers avc, then hevc", async () => {
    expect(await chooseCodecs(vals({}), true, enc(["avc", "hevc"], ["aac"]))).toEqual({ video: "avc", audio: "aac" });
    expect(await chooseCodecs(vals({}), true, enc(["hevc"], ["aac"]))).toEqual({ video: "hevc", audio: "aac" });
  });
  it("auto webm falls to av1", async () => {
    expect(await chooseCodecs(vals({ container: "webm" }), true, enc(["av1"], ["opus"]))).toEqual({ video: "av1", audio: "opus" });
  });
  it("aac falls back to opus", async () => {
    expect(await chooseCodecs(vals({}), true, enc(["avc"], ["opus"]))).toEqual({ video: "avc", audio: "opus" });
  });
  it("no audio encoder is a problem", async () => {
    expect(await chooseCodecs(vals({}), true, enc(["avc"], []))).toEqual({ problem: NO_SOUND });
  });
  it("remove_audio never asks the audio encoder", async () => {
    const audio = vi.fn(async () => false);
    const r = await chooseCodecs(vals({ remove_audio: true }), true, { video: async () => true, audio });
    expect(r).toEqual({ video: "avc", audio: null });
    expect(audio).not.toHaveBeenCalled();
  });
  it("no sound means no audio", async () => {
    expect(await chooseCodecs(vals({}), false, enc(["avc"], []))).toEqual({ video: "avc", audio: null });
  });
  it("explicit h265 missing is a problem", async () => {
    const r = await chooseCodecs(vals({ video_codec: "h265" }), true, enc(["avc"], ["aac"]));
    expect(r).toEqual({ problem: "This browser or device can't encode H.265 / HEVC. Try another codec, or use the desktop app." });
  });
  it("gif picks vp9 then vp8", async () => {
    expect(await chooseCodecs(vals({ container: "gif" }), true, enc(["vp8"], []))).toEqual({ video: "vp8", audio: null });
    expect(await chooseCodecs(vals({ container: "gif" }), true, enc(["vp9", "vp8"], []))).toEqual({ video: "vp9", audio: null });
  });
  it("availableCodecs", async () => {
    expect(await availableCodecs(enc(["avc", "av1"], []))).toEqual(["auto", "h264", "av1"]);
  });
});

describe("exportEstimate", () => {
  it("grows with quality and is positive for gif", () => {
    const q = (quality: string) => exportEstimate(first, vals({ quality }));
    expect(q("low")).toBeLessThan(q("medium"));
    expect(q("medium")).toBeLessThan(q("high"));
    expect(q("high")).toBeLessThan(q("very-high"));
    expect(exportEstimate(first, vals({ container: "gif" }))).toBeGreaterThan(0);
  });
});
