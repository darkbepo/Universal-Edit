// @vitest-environment jsdom
// The System / Light / Dark switch (mirrors tests/test_theme.py) and the colour tokens.
import { beforeAll, beforeEach, describe, expect, it } from "vitest";
import { applyChoice, currentChoice, nextChoice, savedChoice, setChoice, STORAGE_KEY, themeButton } from "../src/ui/theme";

type Fs = { readFileSync(p: URL, encoding: "utf8"): string };
let fs: Fs;
beforeAll(async () => {
  fs = (await import(/* @vite-ignore */ `node:${"fs"}`)) as Fs; // tsconfig has no node types
});
const read = (p: string) => fs.readFileSync(new URL(p, import.meta.url), "utf8");

beforeEach(() => {
  localStorage.clear();
  applyChoice("system");
});

describe("theme choice", () => {
  it("cycles System → Light → Dark", () => {
    expect(nextChoice("system")).toBe("light");
    expect(nextChoice("light")).toBe("dark");
    expect(nextChoice("dark")).toBe("system");
    expect(nextChoice("nonsense")).toBe("system");
  });

  it("reads what was saved, falling back to system", () => {
    expect(savedChoice()).toBe("system");
    localStorage.setItem(STORAGE_KEY, "dark");
    expect(savedChoice()).toBe("dark");
    localStorage.setItem(STORAGE_KEY, "purple");
    expect(savedChoice()).toBe("system");
    const blocked = { getItem: () => { throw new Error("SecurityError"); } };
    expect(savedChoice(blocked)).toBe("system");
    expect(savedChoice(null)).toBe("system");
  });

  it("sets data-theme and remembers it; system clears both", () => {
    setChoice("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("dark");
    setChoice("system");
    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
    const refusing = { setItem: () => { throw new Error("QuotaExceeded"); }, removeItem: () => {} };
    setChoice("light", refusing); // still applied
    expect(currentChoice()).toBe("light");
  });

  it("the top bar button cycles and names the mode", () => {
    const b = themeButton();
    expect(b.getAttribute("aria-label")).toBe("Theme: Same as the computer");
    b.click();
    expect(currentChoice()).toBe("light");
    expect(b.getAttribute("aria-label")).toBe("Theme: Light");
    expect(b.title).toBe("Theme: Light — click for dark");
    b.click();
    expect(localStorage.getItem(STORAGE_KEY)).toBe("dark");
    b.click();
    expect(currentChoice()).toBe("system");
  });
});

describe("tokens", () => {
  let css = "";
  beforeAll(() => {
    css = read("../src/styles.css");
  });
  const block = (selector: string) => {
    const start = css.indexOf(`${selector} {`);
    expect(start).toBeGreaterThanOrEqual(0);
    const body = css.slice(start, css.indexOf("}", start));
    return Object.fromEntries([...body.matchAll(/--([a-z-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]));
  };

  it("the chosen dark look is the same as the OS dark look", () => {
    expect(block(':root[data-theme="dark"]')).toEqual(block(':root:not([data-theme="light"])'));
  });

  it("matches the desktop palettes", () => {
    const py = read("../../src/umc/gui/theme.py");
    const palette = (name: string) => {
      const body = py.slice(py.indexOf(`${name} = {`), py.indexOf("}", py.indexOf(`${name} = {`)));
      return Object.fromEntries([...body.matchAll(/"([a-z_]+)": "(#[0-9a-f]+)"/g)].map((m) => [m[1].replace(/_/g, "-"), m[2]]));
    };
    for (const [web, desk] of [[block(":root"), palette("LIGHT")], [block(':root[data-theme="dark"]'), palette("DARK")]]) {
      for (const [k, v] of Object.entries(web)) if (k in desk) expect(v, k).toBe(desk[k]);
    }
  });

  it("index.html applies the same key before the first paint", () => {
    const html = read("../index.html");
    expect(html).toContain(`localStorage.getItem("${STORAGE_KEY}")`);
    expect(html.indexOf("umc-theme")).toBeLessThan(html.indexOf("coi-serviceworker"));
  });
});
