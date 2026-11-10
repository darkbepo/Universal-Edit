// Placement of the floating crop / selection bar — same cases as the desktop test
// (tests/test_gui_editor.py::test_place_bar_beside_the_release_corner).
import { describe, expect, it } from "vitest";
import { GAP, MARGIN, nearestCorner, placeBar } from "../src/ui/editor/cropbar";

describe("crop bar placement", () => {
  const rect: [number, number, number, number] = [100, 100, 300, 200];
  it("picks the corner nearest to where the drag ended", () => {
    expect(nearestCorner(rect, 290, 195)).toEqual([true, true]);
    expect(nearestCorner(rect, 105, 110)).toEqual([false, false]);
  });
  it("goes outside the rectangle, next to that corner", () => {
    expect(placeBar(rect, [true, true], [120, 30], [800, 600])).toEqual([180, 200 + GAP]);
    expect(placeBar(rect, [false, false], [120, 30], [800, 600])).toEqual([100, 100 - GAP - 30]);
  });
  it("flips, goes inside, and never leaves the canvas", () => {
    expect(placeBar(rect, [true, true], [120, 30], [800, 230])).toEqual([180, 100 - GAP - 30]);
    expect(placeBar([0, 0, 300, 200], [true, true], [120, 30], [300, 200])).toEqual([300 - 120 - MARGIN, 200 - GAP - 30]);
    expect(placeBar([-50, 10, 20, 40], [false, true], [120, 30], [800, 600])).toEqual([MARGIN, 40 + GAP]);
  });
});
