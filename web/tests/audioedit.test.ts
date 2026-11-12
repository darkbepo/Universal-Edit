// Audio editor model (same cases as tests/test_audioedit.py).
import { describe, expect, it } from "vitest";
import { AudioDoc, setHistoryBytes } from "../src/audioedit/document";
import * as ops from "../src/audioedit/ops";
import * as peaks from "../src/audioedit/peaks";
import { readWav, wavFormat, WavError, writeWav } from "../src/audioedit/wav";

const rampDoc = (n = 10, channels = 1) => new AudioDoc(8000, Array.from({ length: channels }, () => Float32Array.from({ length: n }, (_, i) => i)));
const vals = (doc: AudioDoc, ch = 0) => Array.from(doc.samples[ch]);
const near = (a: number[], b: number[], tol = 1e-4) => a.length === b.length && a.every((v, i) => Math.abs(v - b[i]) <= tol);

describe("WAV", () => {
  it.each([32, 16] as const)("round-trips %i-bit", (bits) => {
    const x = [Float32Array.from([0, 0.5, -0.5, 1]), Float32Array.from([0.25, -0.25, 0, -1])];
    const { rate, samples } = readWav(writeWav(44100, x, bits));
    expect(rate).toBe(44100);
    expect(samples.length).toBe(2);
    for (let c = 0; c < 2; c++) expect(near(Array.from(samples[c]), Array.from(x[c]), bits === 16 ? 1 / 30000 : 0)).toBe(true);
    expect(wavFormat(writeWav(22050, x))).toEqual({ rate: 22050, channels: 2 });
  });

  it("reads 24-bit and 8-bit PCM", () => {
    const wav = (bits: number, body: number[]) => {
      const w = bits / 8, out = new Uint8Array(44 + body.length), v = new DataView(out.buffer);
      const s = (at: number, t: string) => [...t].forEach((c, i) => (out[at + i] = c.charCodeAt(0)));
      s(0, "RIFF"); v.setUint32(4, 36 + body.length, true); s(8, "WAVE"); s(12, "fmt "); v.setUint32(16, 16, true);
      v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, 8000, true); v.setUint32(28, 8000 * w, true);
      v.setUint16(32, w, true); v.setUint16(34, bits, true); s(36, "data"); v.setUint32(40, body.length, true);
      out.set(body, 44);
      return out;
    };
    expect(Array.from(readWav(wav(24, [0, 0, 0x40, 0, 0, 0xc0])).samples[0])).toEqual([0.5, -0.5]);
    expect(Array.from(readWav(wav(8, [128, 192, 64])).samples[0])).toEqual([0, 0.5, -0.5]);
    expect(() => readWav(new TextEncoder().encode("not a wav file at all"))).toThrow(WavError);
  });
});

describe("edits and undo", () => {
  it("cut, paste, undo, redo", () => {
    const doc = rampDoc();
    doc.setSelection(2, 5);
    const clip = doc.cut()!;
    expect(Array.from(clip[0])).toEqual([2, 3, 4]);
    expect(vals(doc)).toEqual([0, 1, 5, 6, 7, 8, 9]);
    expect(doc.selection).toEqual([2, 2]);
    expect(doc.dirty).toBe(true);
    doc.setSelection(7);
    expect(doc.paste(clip)).toBe(true);
    expect(vals(doc)).toEqual([0, 1, 5, 6, 7, 8, 9, 2, 3, 4]);
    expect(doc.selection).toEqual([7, 10]);
    expect(doc.undo()).toBe(true);
    expect(vals(doc)).toEqual([0, 1, 5, 6, 7, 8, 9]);
    expect(doc.selection).toEqual([7, 7]);
    expect(doc.undo()).toBe(true);
    expect(vals(doc)).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
    expect(doc.selection).toEqual([2, 5]);
    expect(doc.dirty).toBe(false);
    expect(doc.redo() && doc.redo()).toBe(true);
    expect(vals(doc)).toEqual([0, 1, 5, 6, 7, 8, 9, 2, 3, 4]);
    expect(doc.undoLabel()).toBe("Paste");
  });

  it("paste replaces the selection and fits channels", () => {
    const doc = rampDoc(6, 2);
    doc.setSelection(1, 4);
    expect(doc.paste([Float32Array.from([9])])).toBe(true);
    expect(vals(doc, 0)).toEqual([0, 9, 4, 5]);
    expect(vals(doc, 1)).toEqual([0, 9, 4, 5]);
    const mono = rampDoc(3);
    mono.setSelection(0);
    mono.paste([Float32Array.from([2]), Float32Array.from([4])]);
    expect(vals(mono)).toEqual([3, 0, 1, 2]);
  });

  it("trim, delete, silence", () => {
    const doc = rampDoc();
    doc.setSelection(3, 6);
    expect(doc.trim()).toBe(true);
    expect(vals(doc)).toEqual([3, 4, 5]);
    expect(doc.selection).toEqual([0, 3]);
    expect(doc.undo()).toBe(true);
    doc.setSelection(0, 10);
    expect(doc.trim()).toBe(false);
    doc.setSelection(8, 99);
    expect(doc.selection).toEqual([8, 10]);
    expect(doc.delete()).toBe(true);
    expect(vals(doc)).toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
    doc.setSelection(1, 3);
    expect(doc.silence()).toBe(true);
    expect(vals(doc)).toEqual([0, 0, 0, 3, 4, 5, 6, 7]);
    doc.setSelection(4);
    expect(doc.delete() || doc.silence() || doc.fadeIn()).toBe(false);
  });

  it("fades, amplify, normalize, reverse", () => {
    const doc = new AudioDoc(8000, [new Float32Array(5).fill(1)]);
    doc.setSelection(0, 5);
    expect(doc.fadeIn()).toBe(true);
    expect(vals(doc)).toEqual([0, 0.25, 0.5, 0.75, 1]);
    doc.undo();
    expect(doc.fadeOut()).toBe(true);
    expect(vals(doc)).toEqual([1, 0.75, 0.5, 0.25, 0]);
    doc.undo();
    doc.setSelection(2);
    expect(doc.amplify(-6.0206)).toBe(true);
    expect(near(vals(doc), [0.5, 0.5, 0.5, 0.5, 0.5])).toBe(true);
    expect(doc.normalize()).toBe(true);
    expect(Math.abs(ops.peak(doc.samples) - ops.dbToGain(-1))).toBeLessThan(1e-6);
    expect(doc.normalize()).toBe(false);
    const r = rampDoc(4);
    expect(r.reverse()).toBe(true);
    expect(vals(r)).toEqual([3, 2, 1, 0]);
    const silent = new AudioDoc(8000, [new Float32Array(4)]);
    expect(silent.normalize() || silent.amplify(0)).toBe(false);
  });

  it("caps the history", () => {
    setHistoryBytes(100);
    try {
      const doc = new AudioDoc(8000, [new Float32Array(20)]);
      for (let i = 0; i < 3; i++) {
        doc.setSelection(0, 10);
        doc.reverse();
      }
      expect(doc.historySize).toBe(1);
      expect(doc.canUndo()).toBe(true);
    } finally {
      setHistoryBytes(300 * 1024 * 1024);
    }
  });

  it("formats times", () => {
    expect(ops.formatTime(65.25)).toBe("1:05.250");
    expect(ops.formatTime(3723)).toBe("1:02:03.000");
    expect(ops.formatTime(0)).toBe("0:00.000");
  });
});

describe("waveform columns", () => {
  it("zoomed in and out", () => {
    const x = [Float32Array.from([0, 1, -1, 2, -2, 3, -3, 4, -4, 5])];
    const p = peaks.build(x);
    let c = peaks.columns(x, p, 0, 2, 10);
    expect(Array.from(c.mins[0])).toEqual([0, -1, -2, -3, -4]);
    expect(Array.from(c.maxs[0])).toEqual([1, 2, 3, 4, 5]);
    c = peaks.columns(x, p, 8, 0.5, 4);
    expect(Array.from(c.maxs[0])).toEqual([-4, -4, 5, 5]);
    const big = [Float32Array.from({ length: 10_000 }, (_, i) => Math.sin(i))];
    const pb = peaks.build(big);
    c = peaks.columns(big, pb, 0, 1000, 20);
    expect(c.mins[0].length).toBe(10);
    for (let i = 0; i < 10; i++) {
      const part = Array.from(big[0].subarray(i * 1000, (i + 1) * 1000));
      expect(c.mins[0][i]).toBeLessThanOrEqual(Math.min(...part));
      expect(c.maxs[0][i]).toBeGreaterThanOrEqual(Math.max(...part));
    }
    expect(peaks.columns(big, pb, 20_000, 10, 5).mins[0].length).toBe(0);
  });
});
