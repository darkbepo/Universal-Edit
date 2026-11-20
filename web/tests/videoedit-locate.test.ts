import { describe, expect, it } from "vitest";
import { applyPicks, defaultProjectName, DURATION_SLACK, keepsDuration, relinked, type Choice } from "../src/videoedit/locate";
import { newProject, type Source } from "../src/videoedit/project";

const src = (id: string, path: string, size: number, duration = 2, kind: Source["kind"] = "video"): Source => ({
  id, path, kind, duration, width: 320, height: 240, fps: 30, has_audio: true, size, relpath: null,
});

describe("applyPicks", () => {
  const sources = [src("s1", "C:\\v\\clip.mp4", 100), src("s2", "img0.png", 50, 0, "image"), src("s3", "/x/tone.wav", 70, 3, "audio")];
  const none = () => new Map<string, Choice>(sources.map((s) => [s.id, null]));

  it("one pick fills every source it matches and leaves the rest", () => {
    const got = applyPicks(sources, [{ name: "img0.png", size: 50 }, { name: "clip.mp4", size: 100 }, { name: "other.wav", size: 70 }], none());
    expect(got.get("s1")).toEqual({ file: 1 });
    expect(got.get("s2")).toEqual({ file: 0 });
    expect(got.get("s3")).toBeNull();
  });

  it("doesn't overwrite a source chosen by hand", () => {
    const choices = none().set("s1", { file: 0 });
    const got = applyPicks(sources, [{ name: "mine.mp4", size: 1 }, { name: "clip.mp4", size: 100 }], choices);
    expect(got.get("s1")).toEqual({ file: 0 });
    expect(choices.get("s1")).toEqual({ file: 0 });
  });

  it("a file chosen by hand isn't given to another source", () => {
    const twins = [src("s1", "a.mp4", 100), src("s2", "b/a.mp4", 100)];
    const choices = new Map<string, Choice>([["s1", { file: 0 }], ["s2", null]]);
    expect(applyPicks(twins, [{ name: "a.mp4", size: 100 }], choices).get("s2")).toBeNull();
  });
});

describe("keepsDuration", () => {
  it("a size match needs no duration check", () => {
    expect(keepsDuration(src("s1", "a.mp4", 10, 10), src("x", "a.mp4", 10, 99))).toBe(true);
  });
  it("name only: the duration must agree", () => {
    expect(DURATION_SLACK).toBe(0.5);
    expect(keepsDuration(src("s1", "a.mp4", 0, 10), src("x", "a.mp4", 5, 10.3))).toBe(true);
    expect(keepsDuration(src("s1", "a.mp4", 0, 10), src("x", "a.mp4", 5, 12))).toBe(false);
    expect(keepsDuration(src("s1", "a.png", 0, 0, "image"), src("x", "a.png", 5, 0, "image"))).toBe(true);
  });
});

describe("relinked", () => {
  it("keeps id, path and relpath, takes the probe's facts", () => {
    const original = { ...src("s4", "C:\\v\\clip.mp4", 0, 10), relpath: "v/clip.mp4" };
    const probed = { ...src("s1", "clip.mp4", 1234, 10.2), width: 640 };
    expect(relinked(original, probed)).toEqual({ ...probed, id: "s4", path: "C:\\v\\clip.mp4", relpath: "v/clip.mp4" });
  });
});

describe("defaultProjectName", () => {
  it("is the first source's name", () => {
    expect(defaultProjectName(newProject([src("s1", "C:\\v\\holiday.mp4", 1)]))).toBe("holiday.umcvideo");
    expect(defaultProjectName(newProject([src("s1", "/home/a/my.clip.mov", 1)]))).toBe("my.clip.umcvideo");
    expect(defaultProjectName(newProject())).toBe("video.umcvideo");
  });
});
