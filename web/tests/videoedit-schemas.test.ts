// The inspector's option schemas match the desktop's (tests/fixtures/videoedit_cases.json "schemas"),
// and a project can be started from files without probing.
import { describe, expect, it } from "vitest";
import cases from "../../tests/fixtures/videoedit_cases.json";
import { visible } from "../src/core/options";
import { addSources } from "../src/videoedit/edits";
import { newProject, setIdMaker, videoClip, type Source } from "../src/videoedit/project";
import { changeLabel, changedFields, schemaFor, targetOf, valuesOf, type TargetKind } from "../src/videoedit/schemas";
import { nextSourceId, projectFrom } from "../src/videoedit/start";

const KINDS: Record<string, TargetKind> = {
  ProjectSettings: "project",
  Transition: "transition",
  VideoClip: "video",
  PictureClip: "picture",
  AudioClip: "audio",
  TextItem: "text",
};

const src = (id: string, kind: Source["kind"], duration = 5): Source => ({
  id, path: `${id}.x`, kind, duration, width: kind === "audio" ? 0 : 640, height: kind === "audio" ? 0 : 360,
  fps: kind === "video" ? 30 : 0, has_audio: kind !== "image", size: 1,
});

describe("schemas", () => {
  it("matches the desktop schemas", () => {
    for (const [cls, specs] of Object.entries(cases.schemas as Record<string, any[]>)) {
      const mine = schemaFor(KINDS[cls]);
      expect(mine.map((s) => s.name), cls).toEqual(specs.map((s) => s.name));
      specs.forEach((want, i) => {
        const got = mine[i];
        const where = `${cls}.${want.name}`;
        expect(got.label, where).toBe(want.label);
        expect(got.kind, where).toBe(want.kind);
        expect(got.default, where).toBe(want.default);
        expect(got.min ?? null, where).toBe(want.min);
        expect(got.max ?? null, where).toBe(want.max);
        expect(got.step ?? null, where).toBe(want.step);
        expect(got.decimals ?? 0, where).toBe(want.decimals);
        expect(got.unit ?? "", where).toBe(want.unit);
        expect(got.choices ?? [], where).toEqual(want.choices);
      });
    }
  });

  it("custom size shows only for custom", () => {
    const width = schemaFor("project").find((s) => s.name === "width")!;
    expect(visible(width, { resolution: "custom" }, "")).toBe(true);
    expect(visible(width, { resolution: "1080p" }, "")).toBe(false);
  });

  it("targetOf", () => {
    const base = addSources(newProject(), [src("s1", "video")]);
    const clip = { ...videoClip("c1", "s1", 0, 2), transition_in: { kind: "fade", duration: 1 } };
    const p = { ...base, main: [clip] };
    expect(targetOf(p, null)).toEqual({ kind: "project", target: p.settings });
    expect(targetOf(p, "tr:c1")).toEqual({ kind: "transition", target: clip.transition_in });
    expect(targetOf(p, "c1").kind).toBe("video");
    expect(() => targetOf(p, "nope")).toThrow();
    expect(valuesOf("video", clip).speed).toBe(1);
  });

  it("changeLabel", () => {
    expect(changeLabel("video", { volume: 50 })).toBe("Change volume");
    expect(changeLabel("video", { volume: 50, speed: 2 })).toBe("Change settings");
    expect(changeLabel("transition", { kind: "fade" })).toBe("Change transition");
  });

  it("changedFields", () => {
    expect(changedFields({ a: 1, b: 2 }, { a: 1, b: 3, z: 9 })).toEqual({ b: 3 });
  });
});

describe("start", () => {
  it("projectFrom puts videos and pictures in main and audio end to end", () => {
    let n = 0;
    setIdMaker(() => `i${++n}`);
    const p = projectFrom([src("s1", "video", 4), src("s2", "audio", 7), src("s3", "image"), src("s4", "audio", 3)]);
    setIdMaker(null);
    expect(p.main.map((m) => m.source)).toEqual(["s1", "s3"]);
    expect(p.music_rows[0].map((a) => [a.source, a.start])).toEqual([["s2", 0], ["s4", 7]]);
  });

  it("nextSourceId takes the first free number", () => {
    const mk = (ids: string[]) => ({ ...newProject(), sources: ids.map((i) => src(i, "video")) });
    expect(nextSourceId(mk(["s1", "s2"]))).toBe("s3");
    expect(nextSourceId(mk(["s1", "s3"]))).toBe("s2");
  });

  it("nextSourceId skips ids used before (freed ones too)", () => {
    const mk = (ids: string[]) => ({ ...newProject(), sources: ids.map((i) => src(i, "video")) });
    expect(nextSourceId(mk(["s1", "s3"]), new Set(["s1", "s2", "s3"]))).toBe("s4");
    expect(nextSourceId(mk([]), new Set(["s1", "s3"]))).toBe("s2");
    expect(nextSourceId(mk(["s2"]), new Set(["s1"]))).toBe("s3");
    expect(nextSourceId(mk(["s1"]), new Set())).toBe("s2");
  });
});
