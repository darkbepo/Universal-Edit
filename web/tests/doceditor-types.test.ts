import py from "../../src/umc/doceditor/types.py?raw";
import pySubset from "../../src/umc/doceditor/html_subset.py?raw";
import { MAX_NESTING } from "../src/doceditor/subset";
import { expect, test } from "vitest";
import {
  TOO_LARGE,
  W_COMMENTS,
  W_HEADERS,
  WARNINGS,
  nextImageName,
  warningText,
  type DocImages,
} from "../src/doceditor/types";

test("warning strings match the desktop", () => {
  expect(Object.keys(WARNINGS).length).toBe(17);
  for (const [name, value] of Object.entries(WARNINGS)) {
    expect(py).toContain(`${name} = ${JSON.stringify(value)}`);
  }
  expect(py).toContain(`TOO_LARGE = ${JSON.stringify(TOO_LARGE)}`);
  const wNames = [...py.matchAll(/^(W_[A-Z_]+) = /gm)].map((m) => m[1]);
  expect(Object.keys(WARNINGS).sort()).toEqual(wNames.sort());
});

test("warningText", () => {
  expect(warningText([W_HEADERS, W_COMMENTS])).toBe(
    "This document has parts the editor can't keep: headers and footers, comments. They will be lost if you save over the original — use Save as to keep a copy.",
  );
  expect(warningText([])).toBe("");
});

test("nextImageName", () => {
  const images: DocImages = new Map();
  expect(nextImageName(images, "png")).toBe("img/1.png");
  images.set("img/1.png", new Uint8Array());
  images.set("img/2.jpg", new Uint8Array());
  expect(nextImageName(images, "png")).toBe("img/3.png");
});

test("the normaliser's nesting cap matches the desktop", () => {
  expect(pySubset.replace(/\r\n/g, "\n")).toContain(`\nMAX_NESTING = ${MAX_NESTING}\n`);
});
