// Find and replace for the code editor — same cases as tests/test_codeedit_search.py.
import { describe, expect, it } from "vitest";
import cases from "../../tests/fixtures/codeedit_search_cases.json";
import { SearchError, findAll, replaceAll, replacementAt } from "../src/codeedit/search";

type Opts = { case: boolean; whole_word: boolean; regex: boolean };
const opts = (c: Opts) => ({ caseSensitive: c.case, wholeWord: c.whole_word, regex: c.regex });

describe("code editor search (website)", () => {
  it.each(cases.find)("find $query in $text", (c) => {
    if ("error" in c && c.error) expect(() => findAll(c.text, c.query, opts(c))).toThrow(SearchError);
    else expect(findAll(c.text, c.query, opts(c))).toEqual(c.spans);
  });

  it.each(cases.replace)("replace $query in $text", (c) => {
    expect(replaceAll(c.text, c.query, c.with, opts(c))).toEqual([c.result, c.count]);
  });

  it.each(cases.at)("replacement at $start in $text", (c) => {
    expect(replacementAt(c.text, c.query, c.with, c.start, opts(c))).toEqual(c.result);
  });
});
