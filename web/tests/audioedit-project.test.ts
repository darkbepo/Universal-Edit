// Audio editor projects (.umcaudio): the desktop's layout (src/umc/audioedit/project.py), kept
// exactly; damaged files refused in the same words.
import { strToU8, zipSync } from "fflate";
import { describe, expect, it } from "vitest";
import { AudioDoc } from "../src/audioedit/document";
import { FORMAT, fromBytes, ProjectError, toBytes } from "../src/audioedit/project";
import * as tr from "../src/audioedit/tracks";
import { writeWav } from "../src/audioedit/wav";

const MAX = 1_000_000;
const noise = (n: number) => Float32Array.from({ length: n }, (_v, i) => Math.sin(i * 0.37) * 0.8);

describe("audio projects", () => {
  it("round-trips the tracks exactly", () => {
    const a: tr.Track = { ...tr.track(new AudioDoc(48000, [noise(300), noise(300)]), "Voice"), gainDb: -3, pan: -0.25 };
    const b: tr.Track = { ...tr.track(new AudioDoc(48000, [noise(200)]), "Pad"), mute: true, offset: 99 };
    const { tracks, active } = fromBytes(toBytes([a, b], 1), MAX);
    expect(active).toBe(1);
    expect(tracks.map((t) => [t.name, t.gainDb, t.pan, t.mute, t.solo, t.offset])).toEqual([["Voice", -3, -0.25, false, false, 0], ["Pad", 0, 0, true, false, 99]]);
    expect(tracks[0].doc.samples[1]).toEqual(a.doc.samples[1]);
    expect(tracks[1].doc.rate).toBe(48000);
  });

  it("reads the desktop's file layout and clamps odd settings", () => {
    const meta = { format: FORMAT, version: 1, rate: 8000, active: 7, tracks: [{ file: "t.wav", gain_db: 99, pan: -5, offset: -3, name: " " }] };
    const data = zipSync({ "project.json": strToU8(JSON.stringify(meta)), "t.wav": writeWav(8000, [new Float32Array(10)]) });
    const { tracks, active } = fromBytes(data, MAX);
    expect([tracks[0].gainDb, tracks[0].pan, tracks[0].offset, tracks[0].name, active]).toEqual([tr.MAX_GAIN_DB, -1, 0, "Track 1", 0]);
  });

  it.each([
    [new Uint8Array([1, 2, 3]), "isn't an audio editor project"],
    [zipSync({ "project.json": strToU8(JSON.stringify({ format: "other" })) }), "isn't an audio editor project"],
    [zipSync({ "project.json": strToU8(JSON.stringify({ format: FORMAT, version: 99, tracks: [] })) }), "newer version"],
    [zipSync({ "project.json": strToU8(JSON.stringify({ format: FORMAT, version: 1, tracks: [] })) }), "no tracks"],
    [zipSync({ "project.json": strToU8(JSON.stringify({ format: FORMAT, version: 1, tracks: [{ file: "gone.wav" }] })) }), "missing"],
  ])("refuses damaged projects (%#)", (data, message) => {
    expect(() => fromBytes(data, MAX)).toThrow(ProjectError);
    expect(() => fromBytes(data, MAX)).toThrow(message);
  });

  it("refuses a project too long for the editor before unpacking it", () => {
    const meta = { format: FORMAT, version: 1, tracks: [{ file: "t.wav" }] };
    const data = zipSync({ "project.json": strToU8(JSON.stringify(meta)), "t.wav": writeWav(8000, [new Float32Array(5000)]) });
    expect(() => fromBytes(data, 100)).toThrow("too long");
  });
});
