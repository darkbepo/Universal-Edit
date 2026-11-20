// The subtitle editor model against the shared cases (tests/test_subedit.py checks the same file).
import { describe, expect, it } from "vitest";
import cases from "../../tests/fixtures/subedit_cases.json";
import { check } from "../src/subedit/checks";
import * as edits from "../src/subedit/edits";
import { clock, convertMarkup, lostParts, parse, saveFormats, SubtitleError, write, type Cue } from "../src/subedit/formats";
import { History } from "../src/subedit/history";
import { CHARSETS, MAX_OPEN_BYTES, newDocument, openBytes, toBytes } from "../src/subedit/io";

type J = { start: number; end: number; text: string; extra: string[][] };
const fromJson = (c: J): Cue => ({ start: c.start, end: c.end, text: c.text, extra: c.extra.map(([k, v]) => [k, v]) });
const base = () => (cases.base as J[]).map(fromJson);
const enc = (s: string) => new TextEncoder().encode(s);

describe("file cases", () => {
  for (const c of cases.files) {
    it(c.name, () => {
      const doc = parse(c.text, c.format);
      expect(doc).toEqual(c.doc);
      for (const [fmt, want] of Object.entries(c.write)) {
        if (typeof want === "string") expect(write(doc, fmt), fmt).toBe(want);
        else expect(() => write(doc, fmt)).toThrow(want.error);
        expect(lostParts(doc, fmt)).toEqual((c.lost as Record<string, string[]>)[fmt]);
      }
      expect(saveFormats(doc)).toEqual(c.save_formats);
      expect(check(doc.cues, c.format)).toEqual(c.problems);
    });
  }
});

function run(op: string, cues: Cue[], a: Record<string, unknown>): unknown {
  const n = (k: string) => a[k] as number;
  const o = { caseSensitive: a.case as boolean, wholeWord: a.whole_word as boolean, regex: a.regex as boolean };
  switch (op) {
    case "set_text": return edits.setText(cues, n("i"), a.text as string);
    case "set_times": return edits.setTimes(cues, n("i"), (a.start as number) ?? null, (a.end as number) ?? null);
    case "set_length": return edits.setLength(cues, n("i"), n("length"));
    case "shift": return edits.shift(cues, n("delta"), (a.indices as number[]) ?? null);
    case "sync": return edits.sync(cues, n("a"), n("a_time"), n("b"), n("b_time"));
    case "add": return edits.add(cues, n("after"), (a.length as number) ?? edits.NEW_CUE_MS);
    case "delete": return edits.remove(cues, a.indices as number[]);
    case "split": return edits.split(cues, n("i"), (a.at as number) ?? null);
    case "join": return edits.join(cues, a.indices as number[]);
    case "sort": return edits.sort(cues);
    case "replace_all": return edits.replaceAll(cues, a.query as string, a.replacement as string, o);
    case "cue_at": return edits.cueAt(cues, n("ms"));
  }
  throw new Error(op);
} // prettier-ignore

describe("edit cases", () => {
  for (const c of cases.edits) {
    it(`${c.op} ${JSON.stringify(c.args)}`, () => {
      const got = run(c.op, base(), c.args as Record<string, unknown>);
      const want = c.result as unknown;
      if (Array.isArray(want)) expect(got).toEqual(want);
      else if ("value" in (want as object)) expect({ cues: (got as [Cue[], number])[0], value: (got as [Cue[], number])[1] }).toEqual(want);
      else expect({ cues: got }).toEqual(want);
    });
  }

  it("never changes the cues it was given", () => {
    const cues = base();
    const before = JSON.stringify(cues);
    for (const c of cases.edits) run(c.op, cues, c.args as Record<string, unknown>);
    expect(JSON.stringify(cues)).toBe(before);
  });
});

it("base problems, markup and clock", () => {
  expect(check(base(), "srt")).toEqual(cases.base_problems);
  for (const m of cases.markup) expect(convertMarkup(m.text, m.src, m.dst)).toBe(m.out);
  for (const [ms, text] of cases.clock) expect(clock(ms as number)).toBe(text);
});

it("sync refuses cues that start together or would turn round", () => {
  const cues = [fromJson({ start: 1000, end: 2000, text: "a", extra: [] }), fromJson({ start: 1000, end: 3000, text: "b", extra: [] }), fromJson({ start: 5000, end: 6000, text: "c", extra: [] })];
  expect(() => edits.sync(cues, 0, 0, 1, 500)).toThrow("same");
  expect(() => edits.sync(cues, 0, 5000, 2, 1000)).toThrow("order");
});

it("untouched files save the same bytes", () => {
  for (const c of cases.files) {
    if (c.format === "smi") continue;
    for (const raw of [enc(c.text.replaceAll("\n", "\r\n")), new Uint8Array([0xef, 0xbb, 0xbf, ...enc(c.text)])]) {
      const o = openBytes(raw, c.format);
      expect(toBytes(o, o.doc, c.format).data).toEqual(raw);
    }
  }
});

it("an edited file keeps its encoding and line endings", () => {
  const raw = new Uint8Array([...enc("1\r\n00:00:01,000 --> 00:00:02,000\r\nCaf"), 0xe9, 0x0d, 0x0a]);
  const o = openBytes(raw, "srt");
  expect(o.doc.cues[0].text).toBe("Café");
  const saved = toBytes(o, { ...o.doc, cues: edits.shift(o.doc.cues, 500) }, "srt");
  expect(saved.data).toEqual(new Uint8Array([...enc("1\r\n00:00:01,500 --> 00:00:02,500\r\nCaf"), 0xe9, 0x0d, 0x0a]));
  expect(saved.utf8).toBe(false);
  const wide = toBytes(o, { ...o.doc, cues: edits.setText(o.doc.cues, 0, "日本") }, "srt");
  expect(wide.utf8).toBe(true);
  expect(new TextDecoder().decode(wide.data)).toContain("日本");
  const vtt = toBytes(o, o.doc, "vtt");
  expect(vtt.utf8).toBe(true);
  expect(new TextDecoder().decode(vtt.data)).toMatch(/^WEBVTT\r\n\r\n[\s\S]*Café/);
});

it("reopening with a charset saves UTF-8", () => {
  const cyr = [0xcf, 0xf0, 0xe8, 0xe2, 0xe5, 0xf2]; // "Привет" in windows-1251
  const raw = new Uint8Array([...enc("1\n00:00:01,000 --> 00:00:02,000\n"), ...cyr, 0x0a]);
  expect(openBytes(raw, "srt").doc.cues[0].text).not.toBe("Привет");
  const o = openBytes(raw, "srt", "windows-1251");
  expect(o.doc.cues[0].text).toBe("Привет");
  const saved = toBytes(o, o.doc, "srt");
  expect(saved.utf8).toBe(true);
  expect(new TextDecoder().decode(saved.data)).toBe("1\n00:00:01,000 --> 00:00:02,000\nПривет\n");
  for (const [label] of CHARSETS) expect(() => new TextDecoder(label)).not.toThrow();
});

it("bad files are refused in plain words", () => {
  expect(() => openBytes(enc("00:01.000 --> 00:02.000\nx\n"), "vtt")).toThrow(/WEBVTT/);
  expect(() => openBytes(enc("[Script Info]\n"), "ass")).toThrow(/Events/);
  expect(() => openBytes(new Uint8Array([0, 1, 2]), "srt")).toThrow(SubtitleError);
  expect(() => openBytes(new Uint8Array(MAX_OPEN_BYTES + 1), "srt")).toThrow(/too big/);
});

it("a new document and its history", () => {
  const o = newDocument("srt");
  expect(o.doc.cues).toEqual([]);
  const h = new History(o.doc);
  const [cues, at] = edits.add(h.doc.cues, -1);
  h.push({ ...h.doc, cues: edits.setText(cues, at, "hi") }, "Add");
  expect(h.dirty && h.canUndo).toBe(true);
  expect(h.undo()).toBe("Add");
  expect(h.dirty).toBe(false);
  expect(h.redo()).toBe("Add");
  h.markSaved();
  expect(h.dirty).toBe(false);
  expect(new TextDecoder().decode(toBytes(o, h.doc, "srt").data)).toBe("1\n00:00:00,000 --> 00:00:02,000\nhi\n");
});

it("typing in one cue is one undo step", () => {
  const doc = { format: "srt", cues: [fromJson({ start: 0, end: 1000, text: "", extra: [] }), fromJson({ start: 1000, end: 2000, text: "", extra: [] })], header: "", footer: "", fields: [] };
  const h = new History(doc);
  const k0 = "cue-0", k1 = "cue-1";
  for (const text of ["h", "hi", "hi!"]) h.push({ ...h.doc, cues: edits.setText(h.doc.cues, 0, text) }, "Edit text", k0);
  h.push({ ...h.doc, cues: edits.setText(h.doc.cues, 1, "x") }, "Edit text", k1);
  h.undo();
  expect([h.doc.cues[0].text, h.doc.cues[1].text]).toEqual(["hi!", ""]);
  h.undo();
  expect(h.doc).toBe(doc);
  expect(h.canUndo).toBe(false);
  h.redo();
  h.push({ ...h.doc, cues: edits.setText(h.doc.cues, 0, "again") }, "Edit text", k0);
  h.undo();
  expect(h.doc.cues[0].text).toBe("hi!");
});
