// Song details the audio editor keeps (same cases as tests/test_audioedit.py).
import { describe, expect, it } from "vitest";
import cases from "../../tests/fixtures/audioedit_tags_cases.json";
import { carriedTags, editFor } from "../src/audioedit/tags";
import type { MusicDetails } from "../src/properties/music";

const bytes = (s: string | null) => (s === null ? null : new TextEncoder().encode(s));

describe("carriedTags", () => {
  for (const c of cases.cases) {
    it(c.name, () => {
      const got = carriedTags(c.details as (MusicDetails | null)[], c.covers.map(bytes));
      expect(got.values).toEqual(c.values);
      expect(got.cover).toEqual(bytes(c.cover));
    });
  }
});

describe("editFor", () => {
  const jpeg = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 1, 2]);
  const tags = { values: { title: "Song" }, cover: jpeg };
  it("writes tags and cover where the format holds them", () => {
    expect(editFor(tags, "mp3")).toEqual({ values: { title: "Song" }, cover: jpeg });
    expect(editFor(tags, "FLAC")?.cover).toEqual(jpeg);
  });
  it("keeps only the text in OGG / Opus and nothing in WAV", () => {
    expect(editFor(tags, "opus")).toEqual({ values: { title: "Song" }, cover: null });
    expect(editFor(tags, "wav")).toBeNull();
    expect(editFor({ values: {}, cover: jpeg }, "ogg")).toBeNull();
  });
  it("skips a cover that isn't a JPG or PNG", () => {
    expect(editFor({ values: { title: "Song" }, cover: bytes("pic") }, "mp3")).toEqual({ values: { title: "Song" }, cover: null });
    expect(editFor({ values: {}, cover: bytes("pic") }, "mp3")).toBeNull();
  });
});

describe("editFor (shared cases)", () => {
  const covers: Record<string, Uint8Array> = { jpeg: new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 1, 2]), text: bytes("pic")! };
  for (const c of cases.edit_cases) {
    it(c.name, () => {
      const edit = editFor({ values: c.values, cover: c.cover ? covers[c.cover] : null }, c.fmt);
      if (c.want === null) {
        expect(edit).toBeNull();
        return;
      }
      expect(edit?.values).toEqual(c.want.values);
      expect(edit?.cover !== null).toBe(c.want.cover);
    });
  }
});
