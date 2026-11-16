// Changing video details: the same rules as the desktop's properties/video.py (shared case file),
// and reading FFmpeg's -i output into the same JSON ffprobe gives.
import { describe, expect, it } from "vitest";
import casesText from "../../tests/fixtures/video_cases.json?raw";
import * as video from "../src/properties/video";

// read as text: one case holds a lone surrogate, which Vite's JSON loader refuses
type Edit = { values: Record<string, string>; languages: Record<string, string>; defaults: Record<string, number | null> };
const cases = JSON.parse(casesText) as {
  languages: [string, string][];
  clean: { values: Record<string, string>; offset: number; expect?: video.Clean; error?: string }[];
  local: { iso: string; offset: number; expect: string }[];
  stored: { stamp: string; expect: string }[];
  common: { details: Pick<video.VideoDetails, video.Field>[]; expect: Record<string, string | null> }[];
  probe: { name: string; ext: string; probe: video.Probe; expect?: video.VideoDetails; error?: string }[];
  args: { file: string; edit: Edit; major: number | null; offset: number; expect?: string[]; error?: string }[];
  log: { name: string; log: string; expect: video.Probe }[];
};
const probes = Object.fromEntries(cases.probe.filter((c) => c.expect).map((c) => [c.name, c.probe]));

describe("video details: same rules as the desktop", () => {
  it("languages", () => expect(video.LANGUAGES).toEqual(cases.languages));
  for (const c of cases.clean) {
    it(`clean ${JSON.stringify(c.values)}`, () => {
      const run = () => video.cleanValues(c.values, () => c.offset);
      if (c.error) expect(run).toThrow(c.error);
      else expect(run()).toEqual(c.expect);
    });
  }
  it("dates", () => {
    for (const c of cases.local) expect(video.localText(c.iso, c.offset)).toBe(c.expect);
    for (const c of cases.stored) expect(video.storedDate(c.stamp)).toBe(c.expect);
  });
  it("common", () => {
    for (const c of cases.common) expect(video.common(c.details)).toEqual(c.expect);
  });
  for (const c of cases.probe) {
    it(`reads ${c.name}`, () => {
      if (c.error) expect(() => video.detailsFromProbe(c.probe, c.ext)).toThrow(c.error);
      else expect(video.detailsFromProbe(c.probe, c.ext)).toEqual(c.expect);
    });
  }
  for (const c of cases.args) {
    it(`args ${c.file} ${JSON.stringify(c.edit)} ${c.major}`, () => {
      const ext = c.file.split(".").pop()!;
      const details = video.detailsFromProbe(probes[c.file], ext);
      const languages = Object.fromEntries(Object.entries(c.edit.languages).map(([k, v]) => [Number(k), v]));
      const edit: video.VideoEdit = { values: c.edit.values, languages, defaults: c.edit.defaults };
      const run = () => {
        video.checkEdit(details, edit);
        const clean = video.cleanValues(edit.values, () => c.offset);
        return video.ffmpegArgs(ext, `/in/${c.file}`, `/out/${c.file}`, details, edit, clean, c.major);
      };
      if (c.error) expect(run).toThrow(c.error);
      else expect(run()).toEqual(c.expect);
    });
  }
});

describe("FFmpeg's -i output", () => {
  for (const c of cases.log) {
    it(`reads like ffprobe: ${c.name}`, () => expect(video.probeFromLog(c.log)).toEqual(c.expect));
  }
  it("FFmpeg 5 style, a data track, a cover and a two-line comment", () => {
    const log = [
      "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from '/in1/a.mp4':",
      "  Metadata:",
      "    major_brand     : isom",
      "    comment         : Two",
      "                    : lines",
      "    creation_time   : 2021-05-06T07:08:09.000000Z",
      "  Duration: 00:00:01.00, start: 0.000000, bitrate: 192 kb/s",
      "  Stream #0:0[0x1](und): Video: h264 (avc1 / 0x31637661), yuv420p, 64x48, 25 fps (default)",
      "    Side data:",
      "      displaymatrix: rotation of -90.00 degrees",
      "  Stream #0:1[0x2](eng): Data: none (gpmd / 0x646D7067), 0 kb/s (default)",
      "    Metadata:",
      "      handler_name    : GoPro MET",
      "  Stream #0:2[0x0]: Video: mjpeg (Baseline), yuvj420p, 16x16, 90k tbn (attached pic)",
      "At least one output file must be specified",
    ].join("\n");
    const p = video.probeFromLog(log);
    expect(p.format?.tags).toEqual({ comment: "Two\nlines", creation_time: "2021-05-06T07:08:09.000000Z" });
    const d = video.detailsFromProbe(p, "mp4");
    expect(d.rotation).toBe(90);
    expect(d.description).toBe("Two\nlines");
    expect(d.lost).toEqual(["a data track (like GPS from an action camera)"]);
    expect(d.streams.map((s) => [s.index, s.cover, s.keep])).toEqual([[0, false, true], [1, false, false], [2, true, true]]);
  });

  it("reads the longer stream lines ffmpeg.wasm prints at a chatty log level", () => {
    const log = [
      "Input #0, matroska,webm, from '/in4/tracks.mkv':",
      "  Stream #0:0, 4, 1/1000: Video: h264 (High), yuv420p(progressive), 320x240, 30 fps, 1k tbn (default)",
      "  Stream #0:1(eng), 3, 1/1000: Audio: aac (LC), 44100 Hz, mono, fltp (default)",
      "  Stream #0:2, 3, 1/1000: Subtitle: subrip",
    ].join("\n");
    const d = video.detailsFromProbe(video.probeFromLog(log), "mkv");
    expect(d.tracks.map((t) => [t.index, t.kind, t.language, t.default])).toEqual([[1, "audio", "eng", true], [2, "subtitle", "", false]]);
  });
});
