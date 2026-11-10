import { unzipSync } from "fflate";
import { describe, expect, it } from "vitest";
import type { EditorDoc } from "../src/editor/document";
import { makeLayer, solid } from "../src/editor/layer";
import { openFiles, type OpenSource } from "../src/editor/open";
import { readOra, writeOra } from "../src/editor/ora";
import type { Decoded, PngCodec } from "../src/editor/raster";

/** Fake PNG codec for node: [width, height] as uint32 + raw RGBA. */
export const fakePng: PngCodec = {
  async encode(data, width, height) {
    const out = new Uint8Array(8 + data.length);
    new DataView(out.buffer).setUint32(0, width);
    new DataView(out.buffer).setUint32(4, height);
    out.set(data, 8);
    return out;
  },
  async decode(bytes) {
    const v = new DataView(bytes.buffer, bytes.byteOffset);
    const width = v.getUint32(0);
    const height = v.getUint32(4);
    if (bytes.length !== 8 + width * height * 4) throw new Error("not a fake png");
    return { width, height, data: new Uint8ClampedArray(bytes.slice(8)) };
  },
};

function doc(): EditorDoc {
  return {
    width: 20,
    height: 10,
    layers: [
      makeLayer("Bottom", 20, 10, solid(20, 10, [255, 0, 0, 255])),
      makeLayer("Top & <co>", 5, 5, solid(5, 5, [0, 0, 255, 255]), { x: 3, y: 4, opacity: 0.5 }),
      makeLayer("Hidden", 20, 10, solid(20, 10, [0, 255, 0, 255]), { visible: false }),
    ],
    active: 1,
    selection: null,
    dirty: false,
  };
}

describe(".ora", () => {
  it("round-trips layers", async () => {
    const back = await readOra(await writeOra(doc(), fakePng), fakePng);
    expect([back.width, back.height]).toEqual([20, 10]);
    expect(back.layers.map((l) => l.name)).toEqual(["Bottom", "Top & <co>", "Hidden"]);
    const top = back.layers[1];
    expect([top.x, top.y, top.width, top.height]).toEqual([3, 4, 5, 5]);
    expect(Math.abs(top.opacity - 0.5)).toBeLessThan(1e-3);
    expect(top.visible).toBe(true);
    expect(back.layers[2].visible).toBe(false);
    expect(Array.from(top.data.subarray(0, 4))).toEqual([0, 0, 255, 255]);
    expect(back.active).toBe(1);
  });

  it("stores the mimetype first and uncompressed", async () => {
    const bytes = await writeOra(doc(), fakePng);
    expect(new TextDecoder().decode(bytes.subarray(30, 38))).toBe("mimetype");
    expect(new DataView(bytes.buffer, bytes.byteOffset).getUint16(8, true)).toBe(0); // method: stored
    const files = unzipSync(bytes);
    expect(new TextDecoder().decode(files.mimetype)).toBe("image/openraster");
    for (const name of ["stack.xml", "mergedimage.png", "Thumbnails/thumbnail.png"]) expect(files[name]).toBeDefined();
    const thumb = await fakePng.decode(files["Thumbnails/thumbnail.png"]);
    expect(Math.max(thumb.width, thumb.height)).toBeLessThanOrEqual(256);
  });

  it("refuses a file without layers", async () => {
    await expect(readOra(new Uint8Array([1, 2, 3]), fakePng)).rejects.toThrow(/OpenRaster/);
  });
});

const src = (name: string, w: number, h: number): OpenSource => ({
  name,
  read: async () => new Uint8Array(),
  decode: async (): Promise<Decoded> => ({ width: w, height: h, data: solid(w, h, [1, 2, 3, 255]) }),
});

describe("openFiles", () => {
  it("makes one layer per image", async () => {
    const { doc: d, errors } = await openFiles([src("wide.png", 100, 50), src("tall.png", 30, 80)], fakePng);
    expect(errors).toEqual([]);
    expect([d.width, d.height]).toEqual([100, 80]);
    expect(d.layers.map((l) => l.name)).toEqual(["wide", "tall"]);
    expect(d.active).toBe(1);
    expect(d.dirty).toBe(false);
  });

  it("keeps the layers of an .ora", async () => {
    const bytes = await writeOra(doc(), fakePng);
    const ora: OpenSource = { name: "a.ora", read: async () => bytes, decode: async () => Promise.reject(new Error("no")) };
    const { doc: d } = await openFiles([ora], fakePng);
    expect(d.layers.length).toBe(3);
    expect(d.layers[1].x).toBe(3);
  });

  it("reports files that don't open", async () => {
    const bad: OpenSource = { name: "broken.png", read: async () => new Uint8Array(), decode: async () => Promise.reject(new Error("bad")) };
    const { doc: d, errors } = await openFiles([bad, src("good.png", 8, 8)], fakePng);
    expect(d.layers.length).toBe(1);
    expect(errors.length).toBe(1);
    expect(errors[0]).toContain("broken.png");
    await expect(openFiles([bad], fakePng)).rejects.toThrow(/None of the files/);
  });

  it("refuses images over the size limit", async () => {
    const { errors } = await openFiles([src("huge.png", 16_001, 1), src("ok.png", 2, 2)], fakePng);
    expect(errors[0]).toMatch(/too large/);
  });
});
