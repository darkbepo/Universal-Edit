// Layer blend modes: the desktop's pixels (tests/fixtures/blend_cases.json, made by
// scripts/make_blend_cases.py; tests/test_editor_blend.py checks the same), merging, ORA, undo.
import { strFromU8, unzipSync } from "fflate";
import { describe, expect, it } from "vitest";
import cases from "../../tests/fixtures/blend_cases.json";
import { fromOra, IDS, oraOp } from "../src/editor/blend";
import { composite, type EditorDoc } from "../src/editor/document";
import { makeLayer } from "../src/editor/layer";
import * as ops from "../src/editor/ops";
import { readOra, writeOra } from "../src/editor/ora";
import { fakePng } from "./editor-ora.test";

const pixels = (rows: number[][]) => new Uint8ClampedArray(rows.flat());

function docFor(mode: string, opacity: number): EditorDoc {
  const n = cases.back.length;
  return {
    width: n,
    height: 1,
    layers: [makeLayer("back", n, 1, pixels(cases.back)), makeLayer("top", n, 1, pixels(cases.source), { opacity, blend: mode })],
    active: 1,
    selection: null,
    dirty: false,
  };
}

const close = (got: Uint8ClampedArray, want: number[][]) => {
  const flat = want.flat();
  return flat.every((v, i) => Math.abs(v - got[i]) <= 1) || `${[...got]} vs ${flat}`;
};

describe("blend modes", () => {
  it("lists the same modes as the desktop", () => {
    expect(cases.cases.filter((_c, i) => i % 2 === 0).map((c) => c.mode)).toEqual(IDS);
    expect(IDS.map((m) => [m, oraOp(m)])).toEqual(cases.ora);
    expect([fromOra("svg:hue"), fromOra(undefined), fromOra("svg:multiply")]).toEqual(["normal", "normal", "multiply"]);
  });

  it.each(cases.cases.map((c) => [c.mode, c.opacity, c] as const))("%s at %s", (_m, _o, c) => {
    expect(close(composite(docFor(c.mode, c.opacity)), c.out)).toBe(true);
  });

  it.each(cases.cases.map((c) => [c.mode, c.opacity, c] as const))("merging %s at %s keeps the look", (_m, _o, c) => {
    const doc = docFor(c.mode, c.opacity);
    expect(ops.mergeDown(doc)).not.toBeNull();
    expect(doc.layers.length).toBe(1);
    expect(doc.layers[0].blend ?? "normal").toBe("normal");
    expect(close(composite(doc), c.out)).toBe(true);
  });

  it("is one undo step, and saved in ORA files", async () => {
    const doc = docFor("normal", 1);
    const cmd = ops.setBlendLayers(doc, [1], "multiply");
    expect(cmd).not.toBeNull();
    expect(doc.layers[1].blend).toBe("multiply");
    expect(ops.setBlendLayers(doc, [1], "multiply")).toBeNull();
    expect(ops.setBlendLayers(doc, [1], "nonsense")).toBeNull();
    const bytes = await writeOra(doc, fakePng);
    expect(strFromU8(unzipSync(bytes)["stack.xml"])).toContain('composite-op="svg:multiply"');
    const back = await readOra(bytes, fakePng);
    expect(back.layers.map((l) => l.blend ?? "normal")).toEqual(["normal", "multiply"]);
    cmd!.undo(doc);
    expect(doc.layers[1].blend ?? "normal").toBe("normal");
  });
});
