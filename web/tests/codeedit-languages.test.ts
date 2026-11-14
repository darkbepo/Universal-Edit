// Every language in the shared table loads a CodeMirror language (or none for plain text).
import { LanguageSupport, StreamLanguage } from "@codemirror/language";
import { describe, expect, it } from "vitest";
import table from "../src/codeedit/languages.json";
import { LANGUAGE_IDS, hasColours, loadLanguage } from "../src/codeedit/cmlanguages";

const flat = (ext: unknown): unknown[] => (Array.isArray(ext) ? ext.flatMap(flat) : [ext]);

describe("code editor languages (website)", () => {
  it("lists every language of the table", () => {
    expect(LANGUAGE_IDS).toEqual(table.languages.map((l) => l.id));
  });

  it.each(table.languages.map((l) => l.id))("loads %s", async (id) => {
    const ext = await loadLanguage(id);
    const parts = flat(ext);
    const language = parts.some((p) => p instanceof LanguageSupport || p instanceof StreamLanguage);
    expect(language).toBe(hasColours(id));
  });

  it("colours CSS with a real parser", async () => {
    expect(flat(await loadLanguage("css")).some((p) => p instanceof LanguageSupport)).toBe(true);
    expect(hasColours("text")).toBe(false);
  });
});
