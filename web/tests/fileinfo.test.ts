// File names the website's Properties accepts or refuses: the desktop's cases
// (scripts/make_fileinfo_cases.py; tests/test_file_rename.py checks the same file).
import { describe, expect, it } from "vitest";
import cases from "../../tests/fixtures/fileinfo_cases.json";
import { cleanName, FileError } from "../src/properties/fileinfo";

describe("file names", () => {
  it.each(cases as [string, string, string | null, string | null][])("%j → %s", (text, old, name, error) => {
    if (error === null) expect(cleanName(text, old)).toBe(name);
    else {
      expect(() => cleanName(text, old)).toThrow(FileError);
      expect(() => cleanName(text, old)).toThrow(error);
    }
  });
});
