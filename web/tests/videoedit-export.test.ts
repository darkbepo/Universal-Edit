// The export maths of the website video editor (same export_size cases as the desktop).
import { describe, expect, it, vi } from "vitest";
import cases from "../../tests/fixtures/videoedit_cases.json";
import { defaults } from "../src/core/options";
import {
  EXPORT_OPTIONS, availableCodecs, chooseCodecs, evenSize, exportEstimate, exportName, exportSize, frameCount,
  gifSize, hasSound, jobFor, pictureDecodeSize, pictureLastUse, usedSourceIds, type Encoders,
} from "../src/videoedit/export";
import { outputFps, type Project } from "../src/videoedit/project";

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
    expect(await availableCodecs(enc(["avc", "av1"], []), "mp4")).toEqual(["auto", "h264", "av1"]);
  });
  it("availableCodecs leaves H.264 / H.265 out of WebM", async () => {
    expect(await availableCodecs(enc(["avc", "vp9", "av1", "hevc"], []), "webm")).toEqual(["auto", "vp9", "av1"]);
  });
  it("WebM can't hold H.264 / H.265", async () => {
    const all = enc(["avc", "vp9", "av1", "hevc"], ["opus"]);
    expect(await chooseCodecs(vals({ container: "webm", video_codec: "h264" }), true, all))
      .toEqual({ problem: "WebM can't hold H.264 — choose VP9 or AV1, or another format." });
    expect(await chooseCodecs(vals({ container: "webm", video_codec: "h265" }), true, all))
      .toEqual({ problem: "WebM can't hold H.265 / HEVC — choose VP9 or AV1, or another format." });
    expect(await chooseCodecs(vals({ container: "webm", video_codec: "vp9" }), true, all)).toEqual({ video: "vp9", audio: "opus" });
  });
});

describe("jobFor", () => {
  const odd = { ...first, settings: { ...first.settings, resolution: "custom", width: 1921, height: 1081 } } as Project;
  const codecs = { video: "avc", audio: "aac" as const };
  it("maps the export values", () => {
    const j = jobFor(first, vals({ resolution: "720p", audio_bitrate: 128 }), codecs);
    expect(j.size).toEqual(evenSize(exportSize(first, "720p")));
    expect(j.fps).toBe(outputFps(first));
    expect(j.video).toEqual({ codec: "avc", bitrate: "high" });
    expect(j.audio).toEqual({ codec: "aac", bitrate: 128000 });
    expect(j.container).toBe("mp4");
    expect(j.project).toBe(first);
  });
  it("an odd project size is even", () => {
    const [w, h] = jobFor(odd, vals({}), codecs).size;
    expect(w % 2).toBe(0);
    expect(h % 2).toBe(0);
  });
  it("a bitrate wins over the quality", () => {
    const j = jobFor(first, vals({ video_bitrate: 5000, quality: "low", container: "mkv" }), codecs);
    expect(j.video).toEqual({ codec: "avc", bitrate: 5_000_000 });
    expect(j.container).toBe("mkv");
  });
  it("no audio codec = no audio track", () => {
    expect(jobFor(first, vals({}), { video: "avc", audio: null }).audio).toBeNull();
  });
  it("GIF: WebM, gifSize, gif_fps, very high, no audio", () => {
    const j = jobFor(first, vals({ container: "gif", gif_fps: 12, gif_width: 321 }), { video: "vp9", audio: "opus" });
    expect(j).toMatchObject({ container: "webm", size: gifSize(first, 321), fps: 12, video: { codec: "vp9", bitrate: "very-high" }, audio: null });
  });
});

describe("export converter", () => {
  it("is registered, hidden, and formats m:ss", async () => {
    const { EXPORT_TARGET, clock } = await import("../src/converters/videoedit");
    const { converterById, targetsFor } = await import("../src/core/registry");
    expect(converterById(EXPORT_TARGET.converterId).id).toBe("video-edit-export");
    expect(converterById("video-edit-export").targets([])).toEqual([]);
    expect(targetsFor([{ file: new File([], "a.umcvideo"), name: "a.umcvideo", ext: "umcvideo", family: "project" }]).targets
      .some((t) => t.converterId === "video-edit-export")).toBe(false);
    expect([clock(0), clock(5.4), clock(65), clock(600.9)]).toEqual(["0:00", "0:05", "1:05", "10:00"]);
  });
});

describe("hasSound", () => {
  const src = (id: string, kind: "video" | "image" | "audio", has_audio: boolean) =>
    ({ id, path: id, kind, duration: 5, width: 640, height: 360, fps: 30, has_audio, size: 1 });
  const base = { ...first, sources: [src("v", "video", true), src("p", "image", false), src("m", "audio", true)], main: [], text_rows: [], music_rows: [] } as unknown as Project;
  const clip = (mute: boolean) => ({ type: "video", id: "c", source: "v", in_: 0, out: 2, speed: 1, volume: 100, mute });
  it("pictures + text only have none", () => {
    expect(hasSound({ ...base, main: [{ type: "picture", id: "x", source: "p", duration: 3 }] } as unknown as Project)).toBe(false);
  });
  it("a video with sound has, unless muted", () => {
    expect(hasSound({ ...base, main: [clip(false)] } as unknown as Project)).toBe(true);
    expect(hasSound({ ...base, main: [clip(true)] } as unknown as Project)).toBe(false);
  });
  it("music has", () => {
    expect(hasSound({ ...base, music_rows: [[{ type: "audio", id: "a", source: "m" }]] } as unknown as Project)).toBe(true);
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

describe("what the worker opens", () => {
  const src = (id: string, kind: "video" | "image" | "audio") =>
    ({ id, path: id, kind, duration: 5, width: 640, height: 360, fps: 30, has_audio: kind !== "image", size: 1 });
  const none = { kind: "none", duration: 0.5 };
  const pic = (id: string, source: string, duration: number) => ({ type: "picture", id, source, duration, transition_in: none });
  const p = {
    ...first,
    sources: [src("v", "video"), src("p", "image"), src("q", "image"), src("m", "audio"), src("unused", "image")],
    main: [pic("a", "p", 2), { type: "video", id: "b", source: "v", in_: 0, out: 1, speed: 1, transition_in: none }, pic("c", "q", 1), pic("d", "p", 3)],
    text_rows: [[]],
    music_rows: [[{ type: "audio", id: "m1", source: "m", start: 0, in_: 0, out: 2 }]],
  } as unknown as Project;
  it("usedSourceIds: only what the timeline uses, in the sources' order", () => {
    expect(usedSourceIds(p)).toEqual(["v", "p", "q", "m"]);
  });
  it("pictureLastUse: the end of each picture source's last main item", () => {
    expect(Object.fromEntries(pictureLastUse(p))).toEqual({ p: 7, q: 4 });
  });
  it("pictureDecodeSize: twice what covers the frame, never larger, aspect kept", () => {
    expect(pictureDecodeSize(4000, 3000, [1280, 720])).toEqual([3413, 2560]);
    expect(pictureDecodeSize(640, 360, [1920, 1080])).toEqual([640, 360]);
    expect(pictureDecodeSize(3000, 4000, [1920, 1080])).toEqual([3000, 4000]);
    expect(pictureDecodeSize(12000, 100, [320, 180])).toEqual([12000, 100]);
  });
});
