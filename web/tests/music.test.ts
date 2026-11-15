// Changing music tags: the same rules as the desktop's properties/music.py (shared case file),
// and the FFmpeg arguments that write them.
import { describe, expect, it } from "vitest";
import casesText from "../../tests/fixtures/music_cases.json?raw";
import * as music from "../src/properties/music";
import { FIELDS as FORM_FIELDS } from "../src/ui/musicform";

// read as text: one case holds a lone surrogate, which Vite's JSON loader refuses
type Case = { values: Record<string, string>; expect?: Record<string, string>; error?: string };
const cases = JSON.parse(casesText) as {
  clean: Case[];
  common: { details: Omit<music.MusicDetails, "has_cover">[]; expect: Record<string, string | null> }[];
  cover: { hex: string; expect?: string; error?: string }[];
};
const bytes = (h: string) => Uint8Array.from(h.match(/../g) ?? [], (x) => parseInt(x, 16));

describe("music tags: same rules as the desktop", () => {
  for (const c of cases.clean) {
    it(`clean ${JSON.stringify(c.values)}`, () => {
      if (c.error) expect(() => music.cleanValues(c.values)).toThrow(c.error);
      else expect(music.cleanValues(c.values)).toEqual(c.expect);
    });
  }
  it("common", () => {
    for (const c of cases.common) {
      const details = c.details.map((d) => ({ ...d, has_cover: false }));
      expect(music.common(details)).toEqual(c.expect);
    }
  });
  it("cover pictures", () => {
    for (const c of cases.cover) {
      if (c.error) expect(() => music.coverMime(bytes(c.hex))).toThrow(c.error);
      else expect(music.coverMime(bytes(c.hex))).toBe(c.expect);
    }
  });
  it("the form's field list matches", () => {
    expect([...FORM_FIELDS]).toEqual([...music.FIELDS]);
  });
});

describe("reading Mediabunny's tags", () => {
  it("numbers, year and cover", () => {
    expect(music.detailsFromTags({ title: "A", trackNumber: 3, tracksTotal: 12, discNumber: 1, date: new Date(Date.UTC(1959, 0, 1)), images: [{}] }))
      .toEqual({ title: "A", artist: "", album: "", album_artist: "", track: "3/12", disc: "1", year: "1959", genre: "", has_cover: true });
    expect(music.detailsFromTags({ raw: { TYER: "2001" } }).year).toBe("2001");
    expect(music.detailsFromTags({}).has_cover).toBe(false);
  });
});

describe("FFmpeg arguments", () => {
  const meta = (args: string[], flag = "-metadata") => args.flatMap((a, i) => (a === flag ? [args[i + 1]] : []));

  it("MP3: ID3v2.3, the old cover kept, the sound copied", () => {
    const args = music.ffmpegArgs("mp3", "/in.mp3", "/out.mp3", { values: { title: "New", track: "03 / 12", year: "" } }, null, true);
    expect(args.slice(0, 2)).toEqual(["-i", "/in.mp3"]);
    expect(args.join(" ")).toContain("-map 0:a -map 0:v? -c copy -map_metadata 0");
    expect(args.join(" ")).toContain("-id3v2_version 3");
    expect(meta(args)).toEqual(["title=New", "track=3/12", "date="]);
    expect(args.at(-1)).toBe("/out.mp3");
  });

  it("a new cover replaces the old one", () => {
    const jpeg = bytes("ffd8ffe000104a464946");
    const args = music.ffmpegArgs("m4a", "/in.m4a", "/o.m4a", { values: {}, cover: jpeg }, "/c.jpg", true);
    expect(args.join(" ")).toContain("-i /in.m4a -i /c.jpg -map 0:a -map 1 -disposition:v:0 attached_pic");
    expect(args).not.toContain("0:v?");
  });

  it("removing the cover", () => {
    const args = music.ffmpegArgs("flac", "/i", "/o.flac", { values: {}, remove_cover: true }, null, true);
    expect(args).not.toContain("0:v?");
    expect(args).not.toContain("1");
  });

  it("FLAC: Vorbis numbers and totals", () => {
    const args = music.ffmpegArgs("flac", "/i", "/o.flac", { values: { track: "3/12", disc: "2", album_artist: "AA" } }, null, false);
    expect(meta(args)).toEqual(["album_artist=AA", "ALBUM ARTIST=", "track=3", "TRACKTOTAL=12", "TOTALTRACKS=", "disc=2", "DISCTOTAL=", "TOTALDISCS="]);
  });

  it("OGG: tags on the sound stream, no cover changes", () => {
    const args = music.ffmpegArgs("ogg", "/i", "/o.ogg", { values: { title: "T" } }, null, false);
    expect(meta(args, "-metadata:s:a:0")).toEqual(["title=T"]);
    expect(args).not.toContain("0:v?");
    expect(() => music.ffmpegArgs("opus", "/i", "/o", { values: { title: "T" } }, null, true)).toThrow(/cover picture would be lost/);
    expect(music.ffmpegArgs("opus", "/i", "/o", { values: {}, remove_cover: true }, null, true)).toContain("0:a");
    expect(() => music.ffmpegArgs("ogg", "/i", "/o", { values: {}, cover: bytes("ffd8ff00") }, "/c", false)).toThrow(/desktop app can/);
  });

  it("bad values and formats are refused", () => {
    expect(() => music.ffmpegArgs("mp3", "/i", "/o", { values: { track: "13/12" } }, null, false)).toThrow(/bigger/);
    expect(() => music.ffmpegArgs("wav", "/i", "/o", { values: { title: "x" } }, null, false)).toThrow(/Only MP3/);
  });
});
