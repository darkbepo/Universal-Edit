// The video editor's edits (same cases as the desktop: tests/fixtures/videoedit_cases.json).
import { afterEach, describe, expect, it } from "vitest";
import cases from "../../tests/fixtures/videoedit_cases.json";
import * as edits from "../src/videoedit/edits";
import { setIdMaker, type Project } from "../src/videoedit/project";
import { projectFromJson, projectToJson, sourceFromJson } from "../src/videoedit/projectfile";

const load = (dict: unknown) => projectFromJson(JSON.stringify(dict));

function nearlyEqual(a: unknown, b: unknown, path = "$", eps = 1e-9): void {
  if (typeof a === "number" && typeof b === "number") {
    if (Math.abs(a - b) > eps) throw new Error(`${path}: ${a} != ${b}`);
  } else if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) throw new Error(`${path}: length ${a.length} != ${b.length}`);
    a.forEach((x, i) => nearlyEqual(x, b[i], `${path}[${i}]`, eps));
  } else if (a && b && typeof a === "object" && typeof b === "object") {
    const ka = Object.keys(a).sort();
    expect(ka, path).toEqual(Object.keys(b).sort());
    for (const k of ka) nearlyEqual((a as any)[k], (b as any)[k], `${path}.${k}`, eps);
  } else {
    expect(a, path).toEqual(b);
  }
}

type Op = (p: Project, ...args: any[]) => Project | [Project, string];
const OPS: Record<string, Op> = {
  insert_main: (p, index, id) => edits.insertMain(p, index, id),
  add_at: (p, id, t, row = 0) => edits.addAt(p, id, t, row),
  add_text: (p, t, row = 0) => edits.addText(p, t, row),
  remove: (p, id) => edits.remove(p, id),
  move_main: (p, id, index) => edits.moveMain(p, id, index),
  move_item: (p, id, start, row) => edits.moveItem(p, id, start, row),
  trim: (p, id, edge, delta) => edits.trim(p, id, edge, delta),
  split: (p, id, t) => edits.split(p, id, t),
  update: (p, id, changes) => edits.update(p, id, changes),
  set_settings: (p, settings) => edits.setSettings(p, settings),
  add_row: (p, kind) => edits.addRow(p, kind),
  relink: (p, id, probed) => edits.relink(p, id, sourceFromJson(probed)),
  remove_source: (p, id) => edits.removeSource(p, id),
  apply_changes: (p, sel, changes) => edits.applyChanges(p, sel, changes),
};

afterEach(() => setIdMaker(null));

describe("edit cases", () => {
  it.each(cases.edits)("$name", (c: any) => {
    let n = 0;
    setIdMaker(() => `n${++n}`);
    const p = load(c.project);
    if (c.error) {
      let err: unknown;
      try {
        OPS[c.op](p, ...c.args);
      } catch (e) {
        err = e;
      }
      expect(err).toBeInstanceOf(edits.EditError);
      expect((err as Error).message).toBe(c.error);
      return;
    }
    const out = OPS[c.op](p, ...c.args);
    const [project, id] = Array.isArray(out) ? out : [out, null];
    // relpath is made when a file is saved (the desktop's expected dict computes it), not by an edit
    const noRel = (q: Project): Project => ({ ...q, sources: q.sources.map((x) => ({ ...x, relpath: null })) });
    nearlyEqual(noRel(project), noRel(load(c.result)));
    expect(id).toBe(c.id);
  });
});

describe("purity", () => {
  it("edits never change their input", () => {
    const withAll = (cases.edits as any[]).find(
      (x) => x.project.main.length && x.project.text_rows.flat().length && x.project.music_rows.flat().length,
    );
    const proj = load((withAll ?? cases.edits[0]).project);
    const before = structuredClone(proj);
    const ids = [...proj.main, ...proj.text_rows.flat(), ...proj.music_rows.flat()].map((i) => i.id);
    expect(ids.length).toBeGreaterThan(0);
    for (const id of ids) {
      const tries: (() => unknown)[] = [
        () => edits.split(proj, id, 0.5),
        () => edits.trim(proj, id, "end", -0.2),
        () => edits.trim(proj, id, "start", 0.2),
        () => edits.update(proj, id, { fade_in: 1 }),
        () => edits.moveItem(proj, id, 5, 0),
        () => edits.applyChanges(proj, `tr:${id}`, { kind: "fade" }),
        () => edits.applyChanges(proj, null, { background: "#ffffff" }),
      ];
      for (const t of tries) {
        try {
          t();
        } catch (e) {
          expect(e).toBeInstanceOf(edits.EditError);
        }
      }
    }
    expect(proj).toEqual(before);
  });
});

describe("relink", () => {
  it("leaves no stale relpath; saving gives the new file name", () => {
    const c = (cases.edits as any[]).find((x) => x.op === "relink");
    const p = load(c.project);
    const out = edits.relink(p, c.args[0], sourceFromJson(c.args[1]));
    const s = out.sources.find((x) => x.id === c.args[0])!;
    expect(s.relpath ?? null).toBeNull();
    const saved = JSON.parse(projectToJson(out)).sources.find((x: any) => x.id === c.args[0]);
    expect(saved.relpath).toBe(String(c.args[1].path).split(/[\/]/).pop());
  });
});
