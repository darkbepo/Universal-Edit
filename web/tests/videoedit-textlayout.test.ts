import { describe, expect, it } from "vitest";
import cases from "../../tests/fixtures/videoedit_cases.json";
import { textItem, type Project, type TextItem } from "../src/videoedit/project";
import { FALLBACK_FONT, fontFamily, textAt, textFades } from "../src/videoedit/textlayout";

const project = (rows: TextItem[][]): Project => ({
  settings: { resolution: "custom", width: 1280, height: 720, fps: "25", background: "#000000" },
  sources: [],
  main: [],
  text_rows: rows,
  music_rows: [[]],
});
const has = () => true;
const T = (id: string, start: number, end: number, extra: Partial<TextItem> = {}): TextItem => ({ ...textItem(id, start, end), text: id, ...extra });

describe("fixture layout", () => {
  for (const [i, c] of (cases as any).text.entries()) {
    it(`case ${i}`, () => {
      const item = c.item as TextItem;
      const [fin, fout] = textFades(item);
      expect(fin).toBe(c.fade_in);
      expect(fout).toBe(c.fade_out);
      const [b] = textAt(project([[item]]), item.start, c.size, has);
      expect(b.x).toBe(c.x);
      expect(b.y).toBe(c.y);
      expect(b.column).toBe(c.column);
      expect(b.row).toBe(c.row);
      expect(b.fontPx).toBe(c.font_px);
    });
  }
});

describe("visible window", () => {
  it("start inclusive, end exclusive, empty skipped, row order", () => {
    const p = project([[T("a", 1, 2), T("e", 0, 5, { text: "" })], [T("b", 0, 5)]]);
    const ids = (t: number) => textAt(p, t, [1280, 720], has).map((b) => b.itemId);
    expect(ids(0.5)).toEqual(["b"]);
    expect(ids(1)).toEqual(["a", "b"]);
    expect(ids(2)).toEqual(["b"]);
  });
  it("splits lines", () => {
    const [b] = textAt(project([[T("a", 0, 1, { text: "x\r\ny\rz\nw" })]]), 0, [1280, 720], has);
    expect(b.lines).toEqual(["x", "y", "z", "w"]);
  });
});

describe("alpha", () => {
  const p = project([[T("a", 0, 4, { fade_in: 1, fade_out: 2 })]]);
  const a = (t: number) => textAt(p, t, [1280, 720], has)[0].alpha;
  it("fades", () => {
    expect(a(0)).toBe(0);
    expect(a(0.5)).toBeCloseTo(0.5);
    expect(a(1.5)).toBe(1);
    expect(a(3)).toBeCloseTo(0.5);
  });
  it("box", () => {
    const [b] = textAt(project([[T("a", 0, 1, { box: true, box_opacity: 40, outline: 3 })]]), 0, [1280, 720], has);
    expect(b.box).toEqual({ colour: "#000000", opacity: 0.4, padding: 3 });
  });
});

describe("font fallback", () => {
  it("uses the font only if available", () => {
    const t = T("a", 0, 1, { font: "Foo" });
    expect(fontFamily(t, () => true)).toBe("Foo");
    expect(fontFamily(t, () => false)).toBe(FALLBACK_FONT);
    expect(fontFamily({ ...t, font: FALLBACK_FONT }, () => false)).toBe(FALLBACK_FONT);
  });
});
