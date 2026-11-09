import { describe, expect, it } from "vitest";
import { canonicalExt, familyOfExt } from "../src/core/detect";
import { defaults } from "../src/core/options";
import { targetsFor } from "../src/core/registry";
import type { InputFile } from "../src/core/types";
import { decode, guessLanguage, ISO639_2, restyle, subtitleConverter } from "../src/converters/subtitles";
import py from "../../src/umc/converters/subtitles.py?raw";

const file = (name: string): InputFile => {
  const ext = canonicalExt(name);
  return { file: new File([], name), name, ext, family: familyOfExt(ext)! };
};

describe("subtitles", () => {
  it("decodes UTF-8, BOMs and old Windows files", () => {
    expect(decode(new Uint8Array([0xc6, 0xd8, 0xc5]), "windows-1252")).toBe("ÆØÅ");
    expect(decode(new Uint8Array([0xef, 0xbb, 0xbf, 0x68, 0x69]), "windows-1252")).toBe("hi");
    expect(decode(new TextEncoder().encode("ÆØÅ"), "windows-1252")).toBe("ÆØÅ");
  });

  it("guesses languages like the desktop app", () => {
    expect(guessLanguage("movie.da.srt")).toBe("dan");
    expect(guessLanguage("Movie.eng.forced.srt")).toBe("eng");
    expect(guessLanguage("movie.srt")).toBe("und");
    const desktop = Object.fromEntries([...py.matchAll(/"([a-z]{2})": "([a-z]{3})"/g)].map((m) => [m[1], m[2]]));
    expect(desktop).toEqual(ISO639_2);
  });

  it("restyles only the Default style", () => {
    const ass = "[Script Info]\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour\nStyle: Sign,Arial,20,&H00FFFFFF\nStyle: Default,Arial,20,&H00FFFFFF\n";
    const out = restyle(ass, { ...defaults(subtitleConverter.options), font_size: 33, colour: "#ffcc00" });
    expect(out).toContain("Style: Sign,Arial,20,&H00FFFFFF");
    expect(out).toContain("Style: Default,DejaVu Sans,33,&H0000CCFF");
    expect(out).toContain("PlayResY: 1080");
  });

  it("offers burn / tracks only for one video + subtitles", () => {
    const ids = (names: string[]) => targetsFor(names.map(file)).targets.map((t) => t.id);
    expect(ids(["a.mp4", "a.srt"])).toEqual(["burn:mp4", "subtrack:mkv", "subtrack:mp4", "pack:zip", "pack:tar.gz"]);
    expect(ids(["a.mp4", "a.srt", "b.vtt"])).not.toContain("burn:mp4");
    expect(ids(["a.mp4", "b.mp4", "a.srt"])).toEqual(["pack:zip", "pack:tar.gz"]);
    expect(ids(["a.srt"])).toEqual(["vtt", "ass", "ttml", "lrc", "pack:zip", "pack:tar.gz"]);
  });
});
