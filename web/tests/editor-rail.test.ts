import { describe, expect, it } from "vitest";
import RAIL from "../src/editor/rail.json";
import { makeTools } from "../src/editor/tools/index";
import { ICONS } from "../src/ui/icon_data";
import { stubRaster } from "./editor-shapes.test";

// mirrors tests/test_editor_rail.py (which also checks rail.json against rail.py)
describe("tool rail", () => {
  const items = RAIL.groups.flatMap((g) => g.items);
  it("has every tool once, selection tools first", () => {
    const tools = items.filter((i) => i.kind === "tool").map((i) => i.id);
    expect([...tools].sort()).toEqual(makeTools(stubRaster).map((t) => t.id).sort());
    expect(new Set(tools).size).toBe(tools.length);
    expect(RAIL.groups[0].group).toBe("Select");
    expect(tools[0]).toBe("select");
  });

  it("has an icon for every item and a list for every menu", () => {
    for (const i of items) expect(i.icon in ICONS, i.icon).toBe(true);
    const menus = items.filter((i) => i.kind === "menu").map((i) => i.id);
    expect(Object.keys(RAIL.menus).sort()).toEqual(menus.sort());
  });
});
