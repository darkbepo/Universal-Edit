import { describe, expect, it } from "vitest";
import { History, MAX_STEPS } from "../src/videoedit/history";
import { matchSources } from "../src/videoedit/match";
import type { Project, Source } from "../src/videoedit/project";

const proj = (n = 0): Project =>
  ({
    format: "umcvideo",
    version: 1,
    settings: { resolution: "first", width: 1920, height: 1080, fps: "first", background: "#000000" },
    sources: [],
    main: [],
    text_rows: [[]],
    music_rows: [[]],
    n,
  }) as unknown as Project;

describe("History", () => {
  it("undo, redo, labels, version, dirty", () => {
    const p0 = proj(0);
    const h = new History(p0);
    expect(!h.dirty && !h.canUndo && !h.canRedo).toBe(true);
    const p1 = proj(1);
    const p2 = proj(2);
    const v = h.version;
    h.push(p1, "Add text");
    h.push(p2, "Add more text");
    expect(h.version).toBe(v + 2);
    expect(h.dirty).toBe(true);
    expect(h.undoLabel).toBe("Add more text");
    h.undo();
    expect(h.project).toBe(p1);
    expect(h.version).toBe(v + 3);
    expect(h.dirty).toBe(true);
    expect(h.redoLabel).toBe("Add more text");
    h.markSaved();
    expect(h.dirty).toBe(false);
    h.redo();
    expect(h.project).toBe(p2);
    expect(h.dirty).toBe(true);
    expect(h.version).toBe(v + 4);
    h.push(p2, "noop");
    expect(h.version).toBe(v + 4);
  });

  it("is clean again after undoing back to the save point", () => {
    const h = new History(proj(0));
    h.push(proj(1), "x");
    h.markSaved();
    h.push(proj(2), "y");
    expect(h.dirty).toBe(true);
    h.undo();
    expect(h.dirty).toBe(false);
  });

  it("keeps 300 steps", () => {
    const h = new History(proj(0));
    for (let i = 0; i < 301; i++) h.push(proj(i + 1), `step ${i}`);
    let n = 0;
    while (h.canUndo) {
      h.undo();
      n++;
    }
    expect(MAX_STEPS).toBe(300);
    expect(n).toBe(300);
  });
});

const src = (id: string, path: string, size: number): Source => ({
  id,
  path,
  kind: "video",
  duration: 1,
  width: 10,
  height: 10,
  fps: 30,
  has_audio: false,
  size,
});
const entries = (m: Map<string, number>) => [...m.entries()].sort();

describe("matchSources", () => {
  it("matches name and size", () => {
    const m = matchSources(
      [src("a", "C:\\a\\clip.mp4", 20), src("b", "/b/clip.mp4", 10)],
      [
        { name: "clip.mp4", size: 10 },
        { name: "clip.mp4", size: 20 },
      ],
    );
    expect(entries(m)).toEqual([
      ["a", 1],
      ["b", 0],
    ]);
  });
  it("ignores case", () => {
    expect(matchSources([src("a", "/x/clip.mp4", 5)], [{ name: "CLIP.MP4", size: 5 }]).get("a")).toBe(0);
  });
  it("size 0: ambiguous names stay unmatched, a single one matches", () => {
    const two = [
      { name: "clip.mp4", size: 1 },
      { name: "clip.mp4", size: 2 },
    ];
    expect(matchSources([src("a", "/x/clip.mp4", 0)], two).size).toBe(0);
    expect(matchSources([src("a", "/x/clip.mp4", 0)], [two[0]]).get("a")).toBe(0);
  });
  it("one picked file serves one source", () => {
    const m = matchSources(
      [src("a", "/x/clip.mp4", 0), src("b", "/y/clip.mp4", 0)],
      [{ name: "clip.mp4", size: 3 }],
    );
    expect(m.size).toBe(1);
  });
  it("a different size does not match", () => {
    expect(matchSources([src("a", "/x/clip.mp4", 5)], [{ name: "clip.mp4", size: 6 }]).size).toBe(0);
  });
});
