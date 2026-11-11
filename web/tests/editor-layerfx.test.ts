import { describe, expect, it } from "vitest";
import { composite, pasteboard, type EditorDoc } from "../src/editor/document";
import { Rect } from "../src/editor/geometry";
import { History } from "../src/editor/history";
import { makeLayer, solid } from "../src/editor/layer";
import { NO_FX, windowValues, withValues, fxFromDict, type LayerFx } from "../src/editor/layerfx";
import * as ops from "../src/editor/ops";
import { readOra, writeOra } from "../src/editor/ora";
import { fakePng } from "./editor-ora.test";

// mirrors tests/test_editor_layerfx.py
const fx = (patch: Partial<LayerFx>): LayerFx => ({ ...NO_FX, ...patch });

/** A 60×60 transparent canvas with a 20×20 white square layer at (20, 20). */
function doc(effects: LayerFx | null = null): EditorDoc {
  const square = makeLayer("Square", 20, 20, solid(20, 20, [255, 255, 255, 255]), { x: 20, y: 20, fx: effects });
  return { width: 60, height: 60, layers: [makeLayer("Background", 60, 60), square], active: 1, selection: null, dirty: false };
}

const px = (data: Uint8ClampedArray, x: number, y: number, w = 60) => Array.from(data.subarray((y * w + x) * 4, (y * w + x) * 4 + 4));

describe("layer effects", () => {
  it("no effects draws the layer as before", () => {
    expect(composite(doc(NO_FX))).toEqual(composite(doc()));
    expect(px(composite(doc()), 30, 30)).toEqual([255, 255, 255, 255]);
  });

  it("an outline surrounds the layer by its width", () => {
    const img = composite(doc(fx({ outline: true, outline_color: "#ff0000", outline_width: 3 })));
    expect(px(img, 30, 30)).toEqual([255, 255, 255, 255]);
    expect(px(img, 18, 30)).toEqual([255, 0, 0, 255]);
    expect(px(img, 17, 30)).toEqual([255, 0, 0, 255]);
    expect(px(img, 16, 30)[3]).toBe(0);
    expect(px(img, 42, 30)).toEqual([255, 0, 0, 255]);
  });

  it("a shadow falls in its direction", () => {
    const hard = fx({ shadow: true, shadow_angle: 0, shadow_distance: 10, shadow_blur: 0, shadow_opacity: 100 });
    const img = composite(doc(hard));
    expect(px(img, 45, 30)).toEqual([0, 0, 0, 255]);
    expect(px(img, 15, 30)[3]).toBe(0);
    const soft = composite(doc({ ...hard, shadow_angle: 90, shadow_blur: 8, shadow_opacity: 60 }));
    const a = px(soft, 30, 45)[3];
    expect(a).toBeGreaterThan(0);
    expect(a).toBeLessThanOrEqual(153);
    expect(px(soft, 30, 15)[3]).toBe(0);
  });

  it("a glow fades outwards", () => {
    const img = composite(doc(fx({ glow: true, glow_color: "#00ff00", glow_size: 12 })));
    const near = px(img, 19, 30), far = px(img, 12, 30);
    expect([near[0], near[1]]).toEqual([0, 255]);
    expect(near[3]).toBeGreaterThan(far[3]);
    expect(far[3]).toBeGreaterThan(0);
  });

  it("a colour overlay tints only the layer's pixels", () => {
    const img = composite(doc(fx({ overlay: true, overlay_color: "#0000ff", overlay_opacity: 50 })));
    expect(px(img, 30, 30)).toEqual([128, 128, 255, 255]);
    expect(px(img, 10, 10)[3]).toBe(0);
  });

  it("effects follow the mask and the layer opacity", () => {
    const d = doc(fx({ outline: true, outline_color: "#ff0000", outline_width: 2 }));
    const mask = solid(20, 20, [0, 0, 0, 255]);
    for (let y = 0; y < 20; y++) for (let x = 0; x < 10; x++) mask.set([255, 255, 255, 255], (y * 20 + x) * 4);
    d.layers[1] = { ...d.layers[1], mask, opacity: 0.5 };
    const img = composite(d);
    expect(px(img, 31, 30)).toEqual([255, 0, 0, 128]);
    expect(px(img, 35, 30)[3]).toBe(0);
  });

  it("a part of the picture matches the whole", () => {
    const d = doc(fx({ shadow: true, shadow_blur: 6, glow: true, outline: true, overlay: true }));
    const whole = composite(d);
    const part = composite(d, new Rect(35, 35, 55, 50));
    for (let y = 0; y < 15; y++) for (let x = 0; x < 20; x++) expect(px(part, x, y, 20)).toEqual(px(whole, x + 35, y + 35));
  });

  it("the pasteboard includes the effects", () => {
    expect(pasteboard(doc(fx({ shadow: true, shadow_distance: 40, shadow_angle: 0, shadow_blur: 0 }))).x1).toBeGreaterThan(60);
  });

  it("merging bakes the effects into pixels", () => {
    const d = doc(fx({ outline: true, outline_color: "#ff0000", outline_width: 3 }));
    const before = composite(d);
    const cmd = ops.mergeDown(d);
    expect(cmd).not.toBeNull();
    expect(d.layers.length).toBe(1);
    expect(d.layers[0].fx ?? null).toBeNull();
    expect(composite(d)).toEqual(before);
    cmd?.undo(d);
    expect(d.layers[1].fx).toBeTruthy();
  });

  it("rasterize bakes, duplicate keeps, transforms keep", () => {
    const d = doc(fx({ outline: true, outline_width: 3 }));
    const before = composite(d);
    ops.duplicateLayers(d, [1]);
    expect(d.layers[2].fx).toEqual(d.layers[1].fx);
    d.layers.pop();
    d.active = 1;
    expect(ops.rasterizeLayer(d)).not.toBeNull();
    expect(d.layers[1].fx ?? null).toBeNull();
    expect(d.layers[1].width).toBe(26);
    expect(composite(d)).toEqual(before);
    const g = doc(fx({ glow: true }));
    ops.flip(g, true, true);
    expect(g.layers[1].fx).toEqual(fx({ glow: true }));
  });

  it("set and clear", () => {
    const d = doc();
    expect(ops.setLayerFx(d, 1, NO_FX)).toBeNull();
    const cmd = ops.setLayerFx(d, 1, fx({ glow: true }), "Glow");
    expect(cmd?.label).toBe("Glow");
    const clear = ops.clearLayerFx(d);
    expect(d.layers[1].fx ?? null).toBeNull();
    clear?.undo(d);
    expect(d.layers[1].fx?.glow).toBe(true);
  });

  it("a run of window changes is one undo step", () => {
    const d = doc();
    const h = new History(d);
    for (const w of [2, 4, 6]) expect(ops.tweakLayerFx(d, h, 1, fx({ outline: true, outline_width: w }), "Outline")).toBe(true);
    expect(d.layers[1].fx?.outline_width).toBe(6);
    h.undo();
    expect(d.layers[1].fx ?? null).toBeNull();
    expect(h.canUndo).toBe(false);
    h.redo();
    ops.tweakLayerFx(d, h, 1, { ...(d.layers[1].fx as LayerFx), glow: true }, "Glow");
    h.undo();
    expect(d.layers[1].fx).toEqual(fx({ outline: true, outline_width: 6 }));
  });

  it("window values round trip", () => {
    const fresh = windowValues(null, "shadow");
    expect(fresh.shadow).toBe(false);
    expect(fresh.shadow_distance).toBe(8);
    const made = withValues(null, { ...fresh, shadow: true });
    expect(made).toEqual(fx({ shadow: true }));
    expect(withValues(made, { shadow: false })).toBeNull();
  });

  it(".ora keeps the effects", async () => {
    const effects = fx({ shadow: true, shadow_color: "#123456", outline: true, outline_width: 5 });
    const back = await readOra(await writeOra(doc(effects), fakePng), fakePng);
    expect(back.layers[1].fx).toEqual(effects);
    expect([back.layers[1].width, back.layers[1].height]).toEqual([20, 20]);
    expect(fxFromDict({ glow: true, glow_size: "7", junk: 1, glow_color: "bad" })).toEqual(fx({ glow: true, glow_size: 7 }));
  });
});


describe("layer effects: review fixes", () => {
  it("an effect switched off keeps its settings", () => {
    const d = doc();
    const h = new History(d);
    ops.tweakLayerFx(d, h, 1, fx({ shadow: true, shadow_distance: 50 }), "Drop shadow");
    const off = withValues(d.layers[1].fx, { shadow: false });
    expect(off?.shadow_distance).toBe(50);
    ops.tweakLayerFx(d, h, 1, off, "Drop shadow");
    expect(windowValues(d.layers[1].fx, "shadow").shadow_distance).toBe(50);
    expect(composite(d)).toEqual(composite(doc()));
  });

  it("a run back to its start leaves nothing to redo", () => {
    const d = doc();
    const h = new History(d);
    ops.tweakLayerFx(d, h, 1, fx({ glow: true }), "Glow");
    ops.tweakLayerFx(d, h, 1, null, "Glow");
    expect(d.layers[1].fx ?? null).toBeNull();
    expect(h.canRedo).toBe(false);
  });

  it("files can't ask for huge effects", () => {
    const f = fxFromDict({ outline: true, outline_width: 10_000_000, shadow_blur: -5 });
    expect([f?.outline_width, f?.shadow_blur]).toEqual([50, 0]);
  });

  it("a full repaint reuses the render and notices strokes", () => {
    const d = doc(fx({ outline: true, outline_color: "#ff0000", outline_width: 2 }));
    composite(d);
    d.layers[1].data.set([0, 0, 255, 255], 0); // painted in place
    expect(px(composite(d), 20, 20)).toEqual([0, 0, 255, 255]);
  });
});
