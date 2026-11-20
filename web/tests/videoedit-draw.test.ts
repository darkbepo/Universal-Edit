// draw.ts carries a compose.ts Frame (and textlayout.ts boxes) out on a Canvas 2D context. Checked
// here with a fake context that records every call, so the order of the drawing is the contract.
import { describe, expect, it } from "vitest";
import { gradeRgb, layerRect, type Frame, type Layer, type Placed } from "../src/videoedit/compose";
import type { TextBox } from "../src/videoedit/textlayout";
import {
  drawFrame,
  drawText,
  gradePixels,
  LINE_HEIGHT,
  liftPixels,
  lineTop,
  textAlign,
  type Ctx2D,
  type Picture,
  type Surfaces,
} from "../src/ui/videoeditor/draw";
import { fontAvailable } from "../src/ui/videoeditor/fonts";

type Call = { op: string; args: unknown[]; alpha: number; gco: string; fill: unknown; stroke: unknown; font: string; lineWidth: number; align: string; baseline: string };

class FakeCtx {
  calls: Call[] = [];
  globalAlpha = 1;
  globalCompositeOperation = "source-over";
  fillStyle: unknown = "#000";
  strokeStyle: unknown = "#000";
  font = "10px sans-serif";
  lineWidth = 1;
  lineJoin = "miter";
  textAlign = "start";
  textBaseline = "alphabetic";
  private stack: Array<Record<string, unknown>> = [];
  constructor(public name: string, public w: number, public h: number) {}
  private rec(op: string, args: unknown[]) {
    this.calls.push({
      op, args, alpha: this.globalAlpha, gco: this.globalCompositeOperation, fill: this.fillStyle, stroke: this.strokeStyle,
      font: this.font, lineWidth: this.lineWidth, align: this.textAlign, baseline: this.textBaseline,
    });
  }
  save() {
    this.stack.push({ a: this.globalAlpha, g: this.globalCompositeOperation, f: this.fillStyle, s: this.strokeStyle, font: this.font, lw: this.lineWidth });
    this.rec("save", []);
  }
  restore() {
    const s = this.stack.pop()!;
    this.globalAlpha = s.a as number;
    this.globalCompositeOperation = s.g as string;
    this.fillStyle = s.f;
    this.strokeStyle = s.s;
    this.font = s.font as string;
    this.lineWidth = s.lw as number;
    this.rec("restore", []);
  }
  fillRect(...a: unknown[]) { this.rec("fillRect", a); }
  drawImage(...a: unknown[]) { this.rec("drawImage", a); }
  beginPath() { this.rec("beginPath", []); }
  rect(...a: unknown[]) { this.rec("rect", a); }
  clip() { this.rec("clip", []); }
  translate(...a: unknown[]) { this.rec("translate", a); }
  rotate(...a: unknown[]) { this.rec("rotate", a); }
  scale(...a: unknown[]) { this.rec("scale", a); }
  getImageData(x: number, y: number, w: number, h: number) {
    this.rec("getImageData", [x, y, w, h]);
    return { data: new Uint8ClampedArray(w * h * 4).fill(100), width: w, height: h };
  }
  putImageData(...a: unknown[]) { this.rec("putImageData", a); }
  fillText(...a: unknown[]) { this.rec("fillText", a); }
  strokeText(...a: unknown[]) { this.rec("strokeText", a); }
  measureText(s: string) { return { width: s.length * 10 }; }
  ops() { return this.calls.map((c) => c.op); }
  of(op: string) { return this.calls.filter((c) => c.op === op); }
}

class FakeSurfaces implements Surfaces {
  made: FakeCtx[] = [];
  make(w: number, h: number) {
    const ctx = new FakeCtx(`s${this.made.length}`, w, h);
    this.made.push(ctx);
    return { canvas: ctx as unknown as CanvasImageSource, ctx: ctx as unknown as Ctx2D };
  }
}

const layer = (over: Partial<Layer> = {}): Layer => ({
  itemId: "i", sourceId: "a", kind: "picture", time: 0, crop: { left: 0, top: 0, right: 0, bottom: 0 },
  rotate: 0, flipH: false, flipV: false, fit: "letterbox", grade: null, fade: 0, ...over,
});
const placed = (l: Layer, over: Partial<Placed> = {}): Placed => ({ layer: l, weight: 1, clipX: null, offsetX: 0, ...over });
const frame = (layers: Placed[], over: Partial<Frame> = {}): Frame => ({ width: 640, height: 360, background: "#112233", mode: "over", lift: 0, layers, ...over });
const pic: Picture = { image: { tag: "picture" } as unknown as CanvasImageSource, width: 400, height: 300 };

function run(f: Frame, pictureOf: (l: Layer) => Picture | null = () => pic) {
  const ctx = new FakeCtx("main", f.width, f.height);
  const s = new FakeSurfaces();
  drawFrame(ctx as unknown as Ctx2D, f, pictureOf, s);
  return { ctx, s };
}
/** The frame-sized scratch surfaces (one per layer). */
const frameScratches = (s: FakeSurfaces, f: Frame) => s.made.filter((m) => m.w === f.width && m.h === f.height);
/** drawImage calls on the main context = the composites. */
const composites = (ctx: FakeCtx) => ctx.of("drawImage");

describe("drawFrame", () => {
  it("one layer", () => {
    const l = layer();
    const f = frame([placed(l)]);
    const { ctx, s } = run(f);
    expect(ctx.calls[0].op).toBe("fillRect");
    expect(ctx.calls[0].args).toEqual([0, 0, 640, 360]);
    expect(ctx.calls[0].fill).toBe("#112233");
    const scratches = frameScratches(s, f);
    expect(scratches).toHaveLength(1);
    const scratch = scratches[0];
    expect(scratch.calls[0].op).toBe("fillRect");
    expect(scratch.calls[0].fill).toBe("#112233");
    const r = layerRect(l, 400, 300, 640, 360);
    const draws = scratch.of("drawImage");
    expect(draws).toHaveLength(1);
    expect(draws[0].args.slice(5)).toEqual([r.dx, r.dy, r.dw, r.dh]);
    const comp = composites(ctx);
    expect(comp).toHaveLength(1);
    expect(comp[0].args[0]).toBe(scratch);
    expect(comp[0].alpha).toBe(1);
    expect(comp[0].gco).toBe("source-over");
    expect(ctx.of("getImageData")).toHaveLength(0);
  });

  it("crossfade", () => {
    const a = layer({ sourceId: "a" });
    const b = layer({ sourceId: "b" });
    const f = frame([placed(a), placed(b, { weight: 0.3 })]);
    const { ctx, s } = run(f);
    const [sa, sb] = frameScratches(s, f);
    const comp = composites(ctx);
    expect(comp.map((c) => c.args[0])).toEqual([sa, sb]);
    expect(comp[0].alpha).toBe(1);
    expect(comp[1].alpha).toBeCloseTo(0.3);
    expect(comp.every((c) => c.gco === "source-over")).toBe(true);
  });

  it("fadeblack", () => {
    const f = frame([placed(layer(), { weight: 0.4 }), placed(layer({ sourceId: "b" }), { weight: 0.2 })], { mode: "add", lift: 7 });
    const { ctx } = run(f);
    expect(ctx.calls[0].op).toBe("fillRect");
    expect(ctx.calls[0].fill).toBe("#000");
    const comp = composites(ctx);
    expect(comp.map((c) => c.gco)).toEqual(["lighter", "lighter"]);
    expect(comp.map((c) => c.alpha)).toEqual([0.4, 0.2]);
    const ops = ctx.ops();
    expect(ctx.of("getImageData")).toHaveLength(1);
    expect(ctx.of("putImageData")).toHaveLength(1);
    expect(ops.indexOf("getImageData")).toBeGreaterThan(ops.lastIndexOf("drawImage"));
    expect(ctx.of("getImageData")[0].args).toEqual([0, 0, 640, 360]);
  });

  it("wipe", () => {
    const f = frame([placed(layer()), placed(layer({ sourceId: "b" }), { clipX: [200, 640] })]);
    const { ctx } = run(f);
    const ops = ctx.ops();
    const second = ops.lastIndexOf("drawImage");
    const rects = ctx.calls.map((c, i) => ({ c, i })).filter(({ c }) => c.op === "rect");
    expect(rects).toHaveLength(1);
    expect(rects[0].c.args).toEqual([200, 0, 440, 360]);
    expect(rects[0].i).toBeGreaterThan(ops.indexOf("drawImage"));
    expect(ops.slice(rects[0].i, second)).toContain("clip");
  });

  it("slide", () => {
    const f = frame([placed(layer(), { offsetX: -100 }), placed(layer({ sourceId: "b" }), { offsetX: 540 })]);
    const { ctx } = run(f);
    const tr = ctx.of("translate").map((c) => c.args);
    expect(tr).toEqual([[-100, 0], [540, 0]]);
    expect(ctx.of("clip")).toHaveLength(0);
  });

  it("fade", () => {
    const f = frame([placed(layer({ fade: 0.25 }))]);
    const { s } = run(f);
    const scratch = frameScratches(s, f)[0];
    const ops = scratch.ops();
    const lastFill = scratch.calls[ops.lastIndexOf("fillRect")];
    expect(ops.lastIndexOf("fillRect")).toBeGreaterThan(ops.indexOf("drawImage"));
    expect(lastFill.alpha).toBe(0.25);
    expect(lastFill.fill).toBe("#112233");
    expect(lastFill.args).toEqual([0, 0, 640, 360]);
  });

  it("missing picture leaves the background", () => {
    const f = frame([placed(layer())]);
    const { s } = run(f, () => null);
    const scratch = frameScratches(s, f)[0];
    expect(scratch.of("drawImage")).toHaveLength(0);
    expect(scratch.of("fillRect")).toHaveLength(1);
  });

  it("grade only on the picture", () => {
    const l = layer({ grade: { brightness: 0.1, contrast: 1.2, saturation: 1 } });
    // 640 × 480 into 640 × 360: letterboxed to 480 × 360
    const f = frame([placed(l)]);
    const { s, ctx } = run(f, () => ({ ...pic, width: 640, height: 480 }));
    const scratch = frameScratches(s, f)[0];
    expect(scratch.of("getImageData")).toHaveLength(0);
    const pics = s.made.filter((m) => m !== scratch);
    expect(pics).toHaveLength(1);
    expect([pics[0].w, pics[0].h]).toEqual([480, 360]);
    expect(pics[0].of("getImageData")[0].args).toEqual([0, 0, 480, 360]);
    expect(pics[0].of("putImageData")).toHaveLength(1);
    expect(ctx.of("getImageData")).toHaveLength(0);
  });

  it("rotate 90", () => {
    // 360 × 640 portrait turned → 640 × 360: fills the frame at scale 1
    const l = layer({ rotate: 90 });
    const f = frame([placed(l)]);
    const { s } = run(f, () => ({ ...pic, width: 360, height: 640 }));
    const scratch = frameScratches(s, f)[0];
    const pics = s.made.filter((m) => m !== scratch);
    expect(pics).toHaveLength(1);
    expect([pics[0].w, pics[0].h]).toEqual([640, 360]);
    expect(pics[0].of("rotate")[0].args[0]).toBeCloseTo(Math.PI / 2);
    // the picture is drawn unturned (360 × 640) around the centre
    const d = pics[0].of("drawImage")[0].args;
    expect(d.slice(1)).toEqual([0, 0, 360, 640, -180, -320, 360, 640]);
  });

  it("crop and flip", () => {
    const l = layer({ crop: { left: 0.25, top: 0, right: 0.25, bottom: 0.5 }, flipH: true });
    const f = frame([placed(l)]);
    const { s } = run(f, () => ({ ...pic, width: 1280, height: 720 })); // cropped to 640 × 360
    const scratch = frameScratches(s, f)[0];
    const p = s.made.filter((m) => m !== scratch)[0];
    expect([p.w, p.h]).toEqual([640, 360]);
    expect(p.of("scale")[0].args).toEqual([-1, 1]);
    expect(p.of("drawImage")[0].args.slice(1, 5)).toEqual([320, 0, 640, 360]);
  });
});

describe("pixels", () => {
  it("gradePixels equals gradeRgb", () => {
    const g = { brightness: -0.1, contrast: 1.3, saturation: 0.6 };
    let seed = 7;
    const rnd = () => ((seed = (seed * 1103515245 + 12345) % 2147483648), seed % 256);
    const data = new Uint8ClampedArray(50 * 4);
    for (let i = 0; i < data.length; i++) data[i] = rnd();
    const before = data.slice();
    gradePixels(data, g);
    for (let p = 0; p < 50; p++) {
      const want = gradeRgb([before[p * 4], before[p * 4 + 1], before[p * 4 + 2]], g);
      expect([data[p * 4], data[p * 4 + 1], data[p * 4 + 2]]).toEqual(want);
      expect(data[p * 4 + 3]).toBe(before[p * 4 + 3]);
    }
  });

  it("liftPixels", () => {
    const data = new Uint8ClampedArray([10, 200, 10, 123, 200, 10, 200, 50]);
    liftPixels(data, 18.6);
    expect(Array.from(data)).toEqual([0, 181, 0, 123, 181, 0, 181, 50]);
  });
});

const tbox = (over: Partial<TextBox> = {}): TextBox => ({
  itemId: "t", lines: ["Hello", "World!"], family: "Arial", bold: false, italic: false, fontPx: 50,
  x: 320, y: 340, column: 1, row: 2, colour: "#ffffff", outlineColour: "#000000", outlineWidth: 3, box: null, alpha: 1, ...over,
});

describe("drawText", () => {
  it("helpers", () => {
    expect([0, 1, 2].map((c) => textAlign(c as 0 | 1 | 2))).toEqual(["left", "center", "right"]);
    expect(lineTop(0, 100, 2, 30)).toBe(100);
    expect(lineTop(1, 100, 2, 30)).toBe(70);
    expect(lineTop(2, 100, 2, 30)).toBe(40);
    expect(LINE_HEIGHT).toBe(1.164);
  });

  it("bottom-centre two lines with an outline", () => {
    const ctx = new FakeCtx("main", 640, 360);
    drawText(ctx as unknown as Ctx2D, [tbox()], 0.5);
    const fills = ctx.of("fillText");
    expect(fills).toHaveLength(2);
    expect(fills.every((c) => c.align === "center" && c.baseline === "top")).toBe(true);
    expect(fills[0].font).toBe('50px "Arial", "DejaVu Sans"');
    const step = Math.round(50 * LINE_HEIGHT);
    expect(fills[0].args).toEqual(["Hello", 320, 340 - 2 * step]);
    expect(fills[1].args).toEqual(["World!", 320, 340 - step]);
    const strokes = ctx.of("strokeText");
    expect(strokes).toHaveLength(2);
    expect(strokes[0].lineWidth).toBe(2 * 3 * 0.5);
    expect(strokes[0].stroke).toBe("#000000");
    const ops = ctx.ops();
    expect(ops.indexOf("strokeText")).toBeLessThan(ops.indexOf("fillText"));
    expect(fills[0].fill).toBe("#ffffff");
  });

  it("italic bold and alpha", () => {
    const ctx = new FakeCtx("main", 640, 360);
    drawText(ctx as unknown as Ctx2D, [tbox({ italic: true, bold: true, alpha: 0.5 })], 1);
    const f = ctx.of("fillText")[0];
    expect(f.font).toBe('italic bold 50px "Arial", "DejaVu Sans"');
    expect(f.alpha).toBe(0.5);
  });

  it("box: one rectangle per line, no stroke", () => {
    const ctx = new FakeCtx("main", 640, 360);
    drawText(ctx as unknown as Ctx2D, [tbox({ box: { colour: "#ff0000", opacity: 0.8, padding: 4 }, alpha: 0.5 })], 0.5);
    expect(ctx.of("strokeText")).toHaveLength(0);
    const rects = ctx.of("fillRect");
    expect(rects).toHaveLength(2);
    expect(rects[0].fill).toBe("#ff0000");
    expect(rects[0].alpha).toBeCloseTo(0.4);
    const step = Math.round(50 * LINE_HEIGHT);
    // "Hello" is 50 wide (fake measureText), centred on 320, padded by 4 · 0.5
    expect(rects[0].args).toEqual([320 - 25 - 2, 340 - 2 * step - 2, 50 + 4, step + 4]);
    const ops = ctx.ops();
    expect(ops.lastIndexOf("fillRect")).toBeLessThan(ops.indexOf("fillText"));
    expect(ctx.of("fillText")[0].alpha).toBe(0.5);
  });
});

describe("fontAvailable", () => {
  it("same width everywhere → missing", () => {
    expect(fontAvailable("Nope", () => 100)).toBe(false);
  });
  it("a different width for the family → present", () => {
    expect(fontAvailable("Yes", (font) => (font.includes('"Yes"') ? 120 : 100))).toBe(true);
    expect(fontAvailable("Serifish", (font) => (font === '"Serifish", serif' ? 90 : 100))).toBe(true);
  });
});
