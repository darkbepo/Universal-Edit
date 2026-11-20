// What one frame of a video project shows, checked against colours FFmpeg itself made
// (tests/fixtures/videoedit_cases.json: grade / transition / fade, see scripts/make_videoedit_cases.py).
import { describe, expect, it } from "vitest";
import cases from "../../tests/fixtures/videoedit_cases.json";
import { fitRect, frameAt, gradeGrade, gradeRgb, layerRect, mixWeights, type Frame } from "../src/videoedit/compose";
import { normalize } from "../src/videoedit/edits";
import { pictureClip, videoClip, type MainItem, type Project, type Source } from "../src/videoedit/project";

type RGB = [number, number, number];
const FPS = 25;

const picture = (id: string): Source => ({ id, path: `${id}.png`, kind: "image", duration: 0, width: 64, height: 36, fps: 0, has_audio: false, size: 1 });
const video: Source = { id: "v", path: "v.mp4", kind: "video", duration: 10, width: 1920, height: 1080, fps: 25, has_audio: false, size: 1 };

function project(main: MainItem[], sources: Source[] = [picture("a"), picture("b")], background = "#000000"): Project {
  return {
    settings: { resolution: "custom", width: 64, height: 36, fps: String(FPS), background },
    sources,
    main,
    text_rows: [[]],
    music_rows: [[]],
  };
}

const hexRgb = (hex: string): RGB => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) as RGB;

/** The colour at column `x` when every layer is a solid colour filling the frame (no fades, no grade). */
function resolve(frame: Frame, x: number, colours: Record<string, RGB>): RGB {
  let out: RGB = frame.mode === "add" ? [0, 0, 0] : hexRgb(frame.background);
  for (const { layer, weight, clipX, offsetX } of frame.layers) {
    if (clipX && (x < clipX[0] || x >= clipX[1])) continue;
    if (x < offsetX || x >= offsetX + frame.width) continue;
    const c = colours[layer.sourceId];
    out = out.map((v, i) => (frame.mode === "add" ? v + weight * c[i] : v * (1 - weight) + weight * c[i])) as RGB;
  }
  return out.map((v) => Math.max(0, Math.min(255, v - frame.lift))) as RGB;
}

const near = (got: readonly number[], want: readonly number[], tol: number) =>
  expect(Math.max(...got.map((v, i) => Math.abs(v - want[i]))), `${got.map((v) => v.toFixed(1))} vs ${want}`).toBeLessThanOrEqual(tol);

/** The case script samples frame round(t · 25) with Python's round (halves to even). */
const frameTime = (t: number) => {
  const n = t * FPS;
  const r = Math.round(n);
  return (Math.abs(n - Math.trunc(n)) === 0.5 && r % 2 ? r - 1 : r) / FPS;
};

describe("fixture transitions", () => {
  it.each(cases.transition)("$kind $duration s at $t", (c) => {
    const second = { ...pictureClip("q", "b", 2), transition_in: { kind: c.kind, duration: c.duration } };
    const p = project([pictureClip("p", "a", 2), second]);
    const frame = frameAt(p, c.t);
    expect(frame.layers.map((l) => l.layer.itemId)).toEqual(["p", "q"]);
    for (const [x, want] of Object.entries(c.columns)) near(resolve(frame, Number(x), { a: c.a as RGB, b: c.b as RGB }), want, 6);
  });
});

describe("fixture grades", () => {
  it.each(cases.grade)("$rgb b $brightness c $contrast s $saturation", (c) => {
    const item = { ...pictureClip("g", "a"), brightness: c.brightness, contrast: c.contrast, saturation: c.saturation };
    near(gradeRgb(c.rgb as RGB, gradeGrade(item)!), c.out, 4);
  });
});

describe("fixture fades", () => {
  it.each(cases.fade)("in $fade_in out $fade_out at $t", (c) => {
    const item = { ...pictureClip("f", "a", c.duration), fade_in: c.fade_in, fade_out: c.fade_out };
    const fade = frameAt(project([item]), frameTime(c.t)).layers[0].layer.fade;
    near(c.background.map((b, i) => b * fade + c.rgb[i] * (1 - fade)), c.out, 4);
  });
});

describe("frameAt edges", () => {
  const two = (kind: string, d = 1) =>
    project([pictureClip("p", "a", 2), { ...pictureClip("q", "b", 2), transition_in: { kind, duration: d } }]);

  it("starts with the first clip", () => {
    const f = frameAt(two("none"), 0);
    expect([f.width, f.height, f.background, f.mode, f.lift]).toEqual([64, 36, "#000000", "over", 0]);
    expect(f.layers.map((l) => [l.layer.itemId, l.weight, l.clipX, l.offsetX])).toEqual([["p", 1, null, 0]]);
  });

  it("shows only the second clip from its start when there is no transition", () => {
    expect(frameAt(two("none"), 2).layers.map((l) => l.layer.itemId)).toEqual(["q"]);
  });

  it("keeps both clips at the transition's last instant, the first one gone", () => {
    const f = frameAt(two("fade"), 2);
    expect(f.layers.map((l) => [l.layer.itemId, l.weight])).toEqual([["p", 1], ["q", 1]]);
    expect(frameAt(two("fade"), 2.01).layers.map((l) => l.layer.itemId)).toEqual(["q"]);
  });

  it("shows only the background from the main row's end", () => {
    expect(frameAt(two("fade"), 3).layers).toEqual([]);
    expect(frameAt(two("none"), 4).layers).toEqual([]);
    expect(frameAt(project([]), 0).layers).toEqual([]);
  });

  it("uses the capped transition of a 1 s clip followed by a 3 s transition", () => {
    const p = normalize(project([pictureClip("p", "a", 1), { ...pictureClip("q", "b", 4), transition_in: { kind: "fade", duration: 3 } }]));
    expect(p.main[1].transition_in.duration).toBe(0.5);
    expect(frameAt(p, 0.4).layers.map((l) => l.layer.itemId)).toEqual(["p"]);
    const mid = frameAt(p, 0.76);
    expect(mid.layers.map((l) => l.layer.itemId)).toEqual(["p", "q"]);
    expect(mid.layers[1].weight).toBeCloseTo(0.24 / 0.52, 6); // FFmpeg counts 0.5 s as 13 frames from frame 13
    expect(frameAt(p, 1.1).layers.map((l) => l.layer.itemId)).toEqual(["q"]);
  });

  it("defaults to the output size and takes another", () => {
    const p = { ...project([videoClip("v1", "v", 0, 4)], [video]), settings: { ...project([]).settings, resolution: "first" } };
    expect([frameAt(p, 1).width, frameAt(p, 1).height]).toEqual([1920, 1080]);
    expect([frameAt(p, 1, [640, 360]).width, frameAt(p, 1, [640, 360]).height]).toEqual([640, 360]);
  });

  it("places wipes and slides", () => {
    const at = (kind: string) => frameAt(two(kind), 1.5).layers.map((l) => [l.clipX, l.offsetX]);
    expect(at("wipeleft")).toEqual([[null, 0], [[32, 64], 0]]);
    expect(at("wiperight")).toEqual([[null, 0], [[0, 32], 0]]);
    expect(at("slideleft")).toEqual([[null, -32], [null, 32]]);
    expect(at("slideright")).toEqual([[null, 32], [null, -32]]);
  });

  it("mixes through black", () => {
    expect(mixWeights("fadeblack", 1)).toEqual({ mode: "add", a: 1, b: 0, lift: 0 });
    expect(mixWeights("fadeblack", 0)).toEqual({ mode: "add", a: 0, b: 1, lift: 0 });
    const half = mixWeights("fadeblack", 0.5);
    expect(half.a).toBe(0);
    expect(half.b).toBeCloseTo(0.341796875, 9); // (1 − P)(1 − smoothstep(0.2, 1, P))
    expect(half.lift).toBeCloseTo(16 * 1.164383 * (1 - half.b), 4);
    expect(mixWeights("fade", 0.3)).toEqual({ mode: "over", a: 1, b: 0.7, lift: 0 });
  });
});

describe("fades multiply", () => {
  it("stays within 0…1 and is lowest mid-clip", () => {
    const item = { ...pictureClip("f", "a", 1), fade_in: 2, fade_out: 2 };
    const p = project([item]);
    const fades = Array.from({ length: 21 }, (_, i) => frameAt(p, i * 0.05 * 0.999).layers[0].layer.fade);
    for (const f of fades) {
      expect(f).toBeGreaterThanOrEqual(0);
      expect(f).toBeLessThanOrEqual(1);
    }
    const least = Math.min(...fades);
    expect(fades.indexOf(least)).toBeGreaterThanOrEqual(9);
    expect(fades.indexOf(least)).toBeLessThanOrEqual(11);
    expect(fades[0]).toBe(1);
  });
});

describe("source time", () => {
  it("follows in_ and speed and clamps to out", () => {
    const clip = { ...videoClip("v1", "v", 1, 5), speed: 2 };
    const p = project([clip], [video]);
    const layer = frameAt(p, 0.5).layers[0].layer;
    expect([layer.kind, layer.sourceId, layer.time]).toEqual(["video", "v", 2]);
    expect(frameAt(p, 1.999).layers[0].layer.time).toBeCloseTo(4.998, 9);
    // a 0.5 s crossfade from 1.5 s runs from frame 38 for 13 frames in FFmpeg, so past the clip's end
    const tail = project([clip, { ...pictureClip("q", "a", 2), transition_in: { kind: "fade", duration: 0.5 } }], [video, picture("a")]);
    const late = frameAt(tail, 2.03).layers;
    expect(late.map((l) => l.layer.itemId)).toEqual(["v1", "q"]);
    expect(late[0].layer.time).toBe(5);
  });

  it("gives pictures time 0, no crop, and copies the geometry", () => {
    const item = { ...pictureClip("p", "a"), rotate: 90, flip_h: true, fit: "crop" };
    const layer = frameAt(project([item]), 1).layers[0].layer;
    expect(layer).toEqual({
      itemId: "p", sourceId: "a", kind: "picture", time: 0, crop: { left: 0, top: 0, right: 0, bottom: 0 },
      rotate: 90, flipH: true, flipV: false, fit: "crop", grade: null, fade: 0,
    });
    const clip = { ...videoClip("v1", "v", 0, 4), crop_left: 10, crop_bottom: 25, brightness: 20 };
    const vl = frameAt(project([clip], [video]), 1).layers[0].layer;
    expect(vl.crop).toEqual({ left: 0.1, top: 0, right: 0, bottom: 0.25 });
    expect(vl.grade).toEqual({ brightness: 0.1, contrast: 1, saturation: 1 });
  });
});

describe("fitRect", () => {
  it("letterboxes, crops and stretches 1920×1080 into 1080×1920", () => {
    expect(fitRect(1920, 1080, 1080, 1920, "letterbox")).toEqual({ sx: 0, sy: 0, sw: 1920, sh: 1080, dx: 0, dy: 656, dw: 1080, dh: 608 });
    expect(fitRect(1920, 1080, 1080, 1920, "crop")).toEqual({ sx: 656.25, sy: 0, sw: 607.5, sh: 1080, dx: 0, dy: 0, dw: 1080, dh: 1920 });
    expect(fitRect(1920, 1080, 1080, 1920, "stretch")).toEqual({ sx: 0, sy: 0, sw: 1920, sh: 1080, dx: 0, dy: 0, dw: 1080, dh: 1920 });
  });

  it("swaps the source size for a 90° rotation and cuts the crop first", () => {
    const clip = { ...videoClip("v1", "v", 0, 4), rotate: 90 };
    const layer = frameAt(project([clip], [video]), 1).layers[0].layer;
    expect(layerRect(layer, 1920, 1080, 1080, 1920)).toEqual({ sx: 0, sy: 0, sw: 1080, sh: 1920, dx: 0, dy: 0, dw: 1080, dh: 1920 });
    const cropped = frameAt(project([{ ...clip, rotate: 0, crop_left: 25, crop_right: 25 }], [video]), 1).layers[0].layer;
    expect(layerRect(cropped, 1920, 1080, 960, 1080)).toEqual({ sx: 0, sy: 0, sw: 960, sh: 1080, dx: 0, dy: 0, dw: 960, dh: 1080 });
  });
});
