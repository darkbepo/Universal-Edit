import { afterEach, describe, expect, it } from "vitest";
import * as edits from "../src/videoedit/edits";
import { layout, setIdMaker, newProject, type Project, type Source } from "../src/videoedit/project";
import * as tl from "../src/ui/videoeditor/timelinemath";

const M = tl.DESKTOP;
const src = (id: string, kind: Source["kind"], duration: number): Source => ({
  id, path: `${id}.mp4`, kind, duration, width: 1920, height: 1080, fps: 25, has_audio: true, size: 1,
});

/** Main clips a, b, c of 2 s each (ids from the id maker are overridden by ordering). */
function threeClips(): Project {
  let p = edits.addSources(newProject(), [src("s", "video", 10)]);
  for (let i = 0; i < 3; i++) p = edits.insertMain(p, i, "s")[0];
  return p;
}
function setup(p: Project, over: Partial<tl.View> = {}) {
  const spans = layout(p);
  const v: tl.View = { pps: 80, scroll: 0, width: 1000, playhead: 0, m: M, ...over };
  return { p, spans, v, rs: tl.rows(p, M) };
}
function clip2(p: Project): Project {
  // trim every main clip to 2 s
  let q = p;
  for (const m of p.main) q = edits.trim(q, m.id, "end", 2 - (layout(q).get(m.id)!.end - layout(q).get(m.id)!.start));
  return q;
}
const drag = (s: ReturnType<typeof setup>, id: string, part: tl.Part, dx: number, y?: number, alt = false): tl.Drag => {
  const span = s.spans.get(id)!;
  const r = tl.spanRect(s.v, s.rs, span);
  return { id, part, span, ox: 500, oy: r.y + 5, x: 500 + dx, y: y ?? r.y + 5, moved: true, alt };
};

afterEach(() => setIdMaker(null));

describe("geometry", () => {
  it("lays out the rows", () => {
    const rs = tl.rows(edits.addRow(newProject(), "text"), M);
    expect(rs.map((r) => r.y)).toEqual([24, 52, 80, 144]);
    expect(tl.totalHeight(rs)).toBe(184);
    expect(rs.map((r) => r.kind)).toEqual(["text", "text", "main", "music"]);
  });

  it("converts x and t", () => {
    const { v } = setup(newProject(), { scroll: 1 });
    expect(tl.xFor(v, 1)).toBe(80);
    expect(tl.tFor(v, 160)).toBe(2);
  });

  it("finds the row under a y", () => {
    const rs = tl.rows(newProject(), M);
    expect(tl.rowAt(rs, 10)).toBeNull();
    expect(tl.rowAt(rs, 24)?.kind).toBe("text");
    expect(tl.rowAt(rs, 52)?.kind).toBe("main");
  });

  it("places the add-row buttons", () => {
    const rs = tl.rows(newProject(), M);
    expect(tl.addRowRect(rs, "text", M)).toEqual({ x: 58, y: 28, w: 18, h: 18 });
  });
});

describe("hitTest", () => {
  it("prefers the earlier clip's right edge at a touching boundary", () => {
    const s = setup(clip2(threeClips()));
    const [a, b] = s.p.main;
    const bx = tl.xFor(s.v, 2);
    const y = 52 + 40;
    expect(tl.hitTest(s.v, s.p, s.spans, s.rs, bx, y)).toEqual({ itemId: a.id, part: "right" });
    expect(tl.hitTest(s.v, s.p, s.spans, s.rs, bx + 1, y)).toEqual({ itemId: b.id, part: "left" }); // 1 px inside b: left edge 1.0 beats a's right edge 1.5
  });

  it("finds the transition tab, then edges below it, and bodies", () => {
    let s = setup(clip2(threeClips()));
    const b = s.p.main[1];
    const p = edits.update(s.p, b.id, { transition_in: { kind: "crossfade", duration: 0.5 } });
    s = setup(p);
    const bx = tl.boundaryX(s.v, s.p, s.spans, 1)!;
    expect(tl.hitTest(s.v, s.p, s.spans, s.rs, bx, 52 + 10)).toEqual({ itemId: b.id, part: "transition" });
    expect(tl.hitTest(s.v, s.p, s.spans, s.rs, bx, 52 + 40)?.part).not.toBe("transition");
    const mid = tl.xFor(s.v, 3.2);
    expect(tl.hitTest(s.v, s.p, s.spans, s.rs, mid, 52 + 40)).toEqual({ itemId: b.id, part: "body" });
  });

  it("ignores the header and gaps", () => {
    const s = setup(clip2(threeClips()));
    expect(tl.hitTest(s.v, s.p, s.spans, s.rs, 40, 70)).toBeNull();
    expect(tl.hitTest(s.v, s.p, s.spans, s.rs, 900, 70)).toBeNull();
  });

  it("limits the edge zone to a third of a narrow clip", () => {
    const s = setup(clip2(threeClips()), { pps: 5 }); // 2 s = 10 px
    const a = s.p.main[0];
    const x0 = tl.xFor(s.v, 0);
    expect(tl.hitTest(s.v, s.p, s.spans, s.rs, x0 + 3, 90)).toEqual({ itemId: a.id, part: "left" });
    expect(tl.hitTest(s.v, s.p, s.spans, s.rs, x0 + 4, 90)).toEqual({ itemId: a.id, part: "body" });
  });

  it("maps a hit to a selection id", () => {
    expect(tl.selectionFor(null)).toBeNull();
    expect(tl.selectionFor({ itemId: "x", part: "transition" })).toBe("tr:x");
    expect(tl.selectionFor({ itemId: "x", part: "body" })).toBe("x");
  });
});

describe("snapping", () => {
  it("snaps within 8 px to the nearest point", () => {
    const s = setup(clip2(threeClips()), { playhead: 5 });
    expect(tl.snap(s.v, s.spans, 0.05, "none", false)).toBe(0);
    expect(tl.snap(s.v, s.spans, 4.95, "none", false)).toBe(5);
    expect(tl.snap(s.v, s.spans, 2.04, "none", false)).toBe(2);
    expect(tl.snap(s.v, s.spans, 3.5, "none", false)).toBe(3.5);
    expect(tl.snap(s.v, s.spans, 2.04, "none", true)).toBe(2.04);
  });

  it("ignores the dragged item's own edges", () => {
    const s = setup(clip2(threeClips()));
    const a = s.p.main[0];
    expect(tl.snap(s.v, s.spans, 2.04, a.id, false)).toBe(2); // b's start is also 2
    expect(tl.snap(s.v, s.spans, 6.04, a.id, false)).toBe(6); // c's end
    const solo = setup(edits.insertMain(edits.addSources(newProject(), [src("s", "video", 10)]), 0, "s")[0]);
    const only = solo.p.main[0];
    expect(tl.snap(solo.v, solo.spans, 10.04, only.id, false)).toBe(10.04);
  });

  it("snaps a move by whichever of start and end is nearer", () => {
    const s = setup(clip2(threeClips()), { playhead: 20 });
    const c = s.p.main[2];
    expect(tl.snapMove(s.v, s.spans, 0.04, 3, c.id, false)).toBe(0);
    expect(tl.snapMove(s.v, s.spans, 3.06, 2.98, "none", false)).toBeCloseTo(3.02, 9);
    expect(tl.snapMove(s.v, s.spans, 3.01, 3.0, c.id, true)).toBe(3.01);
  });
});

describe("dragResult", () => {
  it("trims a main clip's left edge", () => {
    const s = setup(clip2(threeClips()));
    const a = s.p.main[0];
    const r = tl.dragResult(s.v, s.p, s.spans, s.rs, drag(s, a.id, "left", 80, undefined, true))!;
    expect(r.label).toBe("Trim clip");
    expect((r.project.main[0] as any).in_).toBeCloseTo(1, 9);
  });

  it("moves a main clip past the third's centre", () => {
    const s = setup(clip2(threeClips()));
    const [a, b, c] = s.p.main;
    const r = tl.dragResult(s.v, s.p, s.spans, s.rs, drag(s, a.id, "body", tl.xFor(s.v, 5.5) - 500))!;
    expect(r.label).toBe("Move clip");
    expect(r.project.main.map((m) => m.id)).toEqual([b.id, c.id, a.id]);
  });

  it("moves a text item to another row", () => {
    let p = edits.addRow(threeClips(), "text");
    const [p2, id] = edits.addText(p, 1, 0);
    const s = setup(p2);
    const d = drag(s, id, "body", 0, s.rs[1].y + 5, true);
    const r = tl.dragResult(s.v, s.p, s.spans, s.rs, d)!;
    expect(r.label).toBe("Move text");
    expect(r.project.text_rows[1].map((t) => t.id)).toEqual([id]);
  });

  it("returns null when nothing changes or the edit is refused", () => {
    const s = setup(clip2(threeClips()));
    const a = s.p.main[0];
    expect(tl.dragResult(s.v, s.p, s.spans, s.rs, drag(s, a.id, "body", tl.xFor(s.v, 1) - 500))).toBeNull();
    const [p2, id] = edits.addText(s.p, 1, 0);
    const t = setup(p2);
    expect(tl.dragResult(t.v, t.p, t.spans, t.rs, drag(t, id, "body", 0, undefined, true))).toBeNull();
    // a trim below MIN_DURATION is clamped by the edit, as on the desktop
    const cl = tl.dragResult(t.v, t.p, t.spans, t.rs, drag(t, id, "right", -400, undefined, true))!;
    expect(cl.project.text_rows[0][0].end - cl.project.text_rows[0][0].start).toBeGreaterThanOrEqual(edits.MIN_DURATION - 1e-9);
    // dropping onto a taken spot is refused (EditError) and cancels silently
    const [p3, other] = edits.addText(p2, 5, 0);
    const u = setup(p3);
    expect(tl.dragResult(u.v, u.p, u.spans, u.rs, drag(u, other, "body", tl.xFor(u.v, 2) - tl.xFor(u.v, 5), undefined, true))).toBeNull();
  });

  it("counts a drag only past 3 px", () => {
    const base = { id: "x", part: "body" as const, span: { start: 0, end: 1, row_kind: "main" as const, row: 0, index: 0 }, ox: 10, oy: 10, moved: false, alt: false };
    expect(tl.dragMoved({ ...base, x: 12, y: 10 })).toBe(false);
    expect(tl.dragMoved({ ...base, x: 12, y: 11 })).toBe(true);
  });
});

describe("ghost", () => {
  it("shows the landing boundary for a main clip", () => {
    const s = setup(clip2(threeClips()));
    const a = s.p.main[0];
    const g = tl.ghost(s.v, s.p, s.spans, s.rs, drag(s, a.id, "body", tl.xFor(s.v, 3.5) - 500));
    expect(g.insertX).toBe(tl.xFor(s.v, 4)); // after b, before c: c's start in the layout
    expect(g.rect.x).toBe(tl.xFor(s.v, 0) + tl.xFor(s.v, 3.5) - 500);
  });

  it("shows the snapped start for music", () => {
    let p = edits.addSources(clip2(threeClips()), [src("m", "audio", 4)]);
    const [p2, id] = edits.addAt(p, "m", 1.9, 0);
    const s = setup(p2);
    const r = s.rs[s.rs.length - 1];
    const d = { ...drag(s, id, "body", 0.04 * 80, r.y + 5), alt: false };
    const g = tl.ghost(s.v, s.p, s.spans, s.rs, d);
    expect(g.insertX).toBeNull();
    expect(g.rect.x).toBe(tl.xFor(s.v, 2)); // 1.94 snaps to the clip edge at 2 s
  });
});

describe("zoom and scroll", () => {
  it("keeps the anchor and clamps", () => {
    const { v } = setup(newProject(), { scroll: 2 });
    const z = tl.zoom(v, 2, 400);
    expect(tl.tFor({ ...v, ...z }, 400)).toBeCloseTo(tl.tFor(v, 400), 9);
    expect(tl.zoom(v, 100).pps).toBe(800);
    expect(tl.zoom(v, 0.001).pps).toBe(5);
  });

  it("zooms with ctrl and scrolls without", () => {
    const { v } = setup(newProject(), { scroll: 2 });
    expect(tl.wheel(v, -120, true, 400).pps).toBeCloseTo(100, 9);
    expect(tl.wheel(v, 120, true, 400).pps).toBeCloseTo(64, 9);
    expect(tl.wheel(v, 120, false, 400)).toEqual({ pps: 80, scroll: 2 + 60 / 80 });
    expect(tl.wheel(v, -1200, false, 400).scroll).toBe(0);
  });

  it("pages the view for playback", () => {
    const { v } = setup(newProject()); // 920 px / 80 = 11.5 s
    expect(tl.follow(v, 5)).toBe(0);
    expect(tl.follow(v, 20)).toBeCloseTo(20 - 11.5 * 0.05, 9);
  });
});

describe("ruler and time text", () => {
  it("steps by seconds, then tenths", () => {
    const a = tl.rulerTicks(setup(newProject()).v);
    expect(a.slice(0, 3).map((t) => t.label)).toEqual(["0:00", "0:01", "0:02"]);
    const b = tl.rulerTicks(setup(newProject(), { pps: 600 }).v);
    expect(b.slice(0, 3).map((t) => t.label)).toEqual(["0:00.0", "0:00.1", "0:00.2"]);
  });

  it("formats time and frames", () => {
    expect(tl.formatTime(65.4)).toBe("1:05.40");
    expect(tl.formatTime(-1)).toBe("0:00.00");
    const p = edits.insertMain(edits.addSources(newProject(), [src("s", "video", 10)]), 0, "s")[0];
    expect(tl.frameNumber(p, 0.9999999)).toBe(24);
    expect(tl.frameNumber(p, 1.0)).toBe(25);
  });
});
