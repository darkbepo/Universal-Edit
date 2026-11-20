// The video project model, layout maths and file format (same cases as the desktop: tests/fixtures/videoedit_cases.json).
import { describe, expect, it } from "vitest";
import cases from "../../tests/fixtures/videoedit_cases.json";
import { layout, length, newId, newProject, outputFps, outputSize, setIdMaker, videoClip, type Project } from "../src/videoedit/project";
import { projectFromJson, projectToJson, sourceFromJson } from "../src/videoedit/projectfile";

const nearlyEqual = (a: number, b: number, eps = 1e-9) => Math.abs(a - b) <= eps;
const load = (dict: unknown) => projectFromJson(JSON.stringify(dict));

describe("layout cases", () => {
  it.each(cases.layout)("$name", (c) => {
    const p = load(c.project);
    const spans = layout(p);
    expect([...spans.keys()].sort()).toEqual(Object.keys(c.spans).sort());
    for (const [id, [start, end, kind, row, index]] of Object.entries(c.spans) as [string, [number, number, string, number, number]][]) {
      const s = spans.get(id)!;
      expect(nearlyEqual(s.start, start)).toBe(true);
      expect(nearlyEqual(s.end, end)).toBe(true);
      expect([s.row_kind, s.row, s.index]).toEqual([kind, row, index]);
    }
    expect(nearlyEqual(length(p), c.length)).toBe(true);
    expect(outputSize(p)).toEqual(c.output_size);
    expect(nearlyEqual(outputFps(p), c.output_fps)).toBe(true);
  });
});

describe("project file", () => {
  const file = cases.file as { text: string; project: any };
  it("reads a desktop-written file", () => {
    const p = projectFromJson(file.text);
    expect(p).toEqual(load(file.project));
    expect(p.sources.every((s) => typeof s.relpath === "string")).toBe(true);
  });

  it("writes what the desktop reads", () => {
    const out = JSON.parse(projectToJson(projectFromJson(file.text)));
    expect([out.format, out.version]).toEqual(["umcvideo", 1]);
    expect(out.sources.map((s: any) => [s.path, s.relpath, s.size])).toEqual(
      file.project.sources.map((s: any) => [s.path, s.relpath, s.size]),
    );
    expect(out.main.map((m: any) => m.type)).toEqual(file.project.main.map((m: any) => m.type));
    expect(out.text_rows.flat().map((m: any) => m.type)).toEqual(file.project.text_rows.flat().map((m: any) => m.type));
    expect(out.music_rows.flat().map((m: any) => m.type)).toEqual(file.project.music_rows.flat().map((m: any) => m.type));
  });

  it("writes no trailing newline and a browser source's file name as relpath", () => {
    const p: Project = { ...newProject(), sources: [{ id: "a", path: "clip.mp4", kind: "video", duration: 1, width: 2, height: 2, fps: 30, has_audio: false, size: 5 }] };
    const text = projectToJson(p);
    expect(text.endsWith("}")).toBe(true);
    expect(JSON.parse(text).sources[0].relpath).toBe("clip.mp4");
  });

  it("round-trips", () => {
    const p = projectFromJson(file.text);
    expect(projectFromJson(projectToJson(p))).toEqual(p);
  });

  it("ignores unknown keys and fills missing ones", () => {
    const d = structuredClone(file.project);
    const { fade_in: _gone, ...rest } = d.main[0];
    d.main[0] = { ...rest, web_only: 1 };
    const item = load(d).main[0] as any;
    expect(item.fade_in).toBe(0);
    expect("web_only" in item).toBe(false);
  });

  it("rejects damaged files", () => {
    const msg = "This isn't a valid video project file.";
    const good = file.project;
    const badItem = { ...good, main: [{ ...good.main[0], type: "sticker" }] };
    for (const text of ["{", "[]", '{"format":"other"}', JSON.stringify({ ...good, version: "1" }), JSON.stringify(badItem), JSON.stringify({ ...good, main: 5 })]) {
      expect(() => projectFromJson(text)).toThrow(msg);
    }
    const m0 = good.main[0];
    const { source: _s, ...noSource } = m0;
    const variants = [
      { ...good, main: [{ ...m0, in_: "0" }] },
      { ...good, main: [{ ...m0, transition_in: "fade" }] },
      { ...good, main: [{ ...m0, mute: 1 }] },
      { ...good, main: [noSource] },
      { ...good, settings: { ...good.settings, width: "5" } },
    ];
    for (const v of variants) expect(() => load(v)).toThrow(msg);
    const a = { type: "audio", id: "x", start: 0, in_: 0, out: 1 };
    expect(() => load({ ...good, music_rows: [[a]] })).toThrow(msg);
    expect(() => load({ ...good, music_rows: [[{ ...a, source: "m1" }]] })).not.toThrow();
    expect(() => load({ ...good, main: [{ ...m0, in_: 3 }] })).not.toThrow();
    expect(() => load({ ...good, version: 2 })).toThrow("This project was made by a newer version of the app.");
  });
});

describe("ids and fps", () => {
  it("newId is 8 hex chars unless a maker is set", () => {
    expect(newId()).toMatch(/^[0-9a-f]{8}$/);
    let n = 0;
    setIdMaker(() => `n${++n}`);
    expect([newId(), newId()]).toEqual(["n1", "n2"]);
    setIdMaker(null);
    expect(newId()).toMatch(/^[0-9a-f]{8}$/);
  });

  it("rounds a first video's fps like Python", () => {
    for (const [fps, want] of [[29.97002997, 29.97], [23.976023976, 23.976]]) {
      const p: Project = {
        ...newProject([{ id: "v", path: "v.mp4", kind: "video", duration: 5, width: 4, height: 4, fps, has_audio: false, size: 0 }]),
        main: [{ ...(cases.file.project.main[0] as any), source: "v", type: "video" }],
      };
      expect(outputFps(p)).toBe(want);
    }
  });
});

describe("hardening", () => {
  it("sourceFromJson reads, defaults size and rejects", () => {
    const e = { id: "a", path: "a.mp4", kind: "video", duration: 2, width: 4, height: 4, fps: 30, has_audio: true, size: 77 };
    expect(sourceFromJson(e).size).toBe(77);
    const { size: _z, ...noSize } = e;
    expect(sourceFromJson(noSize).size).toBe(0);
    expect(() => sourceFromJson({ ...e, kind: "x" })).toThrow("This isn't a valid video project file.");
    expect(() => sourceFromJson(5)).toThrow("This isn't a valid video project file.");
  });

  it("an unknown resolution name falls back instead of matching prototype keys", () => {
    const p: Project = { ...newProject(), settings: { ...newProject().settings, resolution: "constructor" } };
    expect(outputSize(p)).toEqual([1920, 1080]);
  });

  it("default settings are frozen", () => {
    expect(Object.isFrozen(newProject().settings)).toBe(true);
    expect(Object.isFrozen(videoClip("a", "b", 0, 1).transition_in)).toBe(true);
  });
});
