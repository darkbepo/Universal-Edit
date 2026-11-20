import { describe, expect, it } from "vitest";
import { actionFor, SHORTCUTS, type ActionId } from "../src/ui/videoeditor/shortcuts";

const ev = (key: string, mods: Partial<Record<"ctrlKey" | "metaKey" | "shiftKey" | "altKey", boolean>> = {}) => ({
  key, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...mods,
});

describe("video editor shortcuts", () => {
  it("gives every action a key", () => {
    const ids: ActionId[] = [
      "play", "split", "delete", "prev-frame", "next-frame", "back-second", "forward-second", "home", "end",
      "undo", "redo", "save", "save-as", "export", "zoom-in", "zoom-out", "loop",
    ];
    for (const id of ids) expect(SHORTCUTS.find((s) => s.id === id)?.keys.length, id).toBeGreaterThan(0);
  });

  it("never maps one key to two actions", () => {
    const seen = new Map<string, string>();
    for (const s of SHORTCUTS) {
      for (const k of s.keys) {
        expect(seen.has(k), `${k} twice`).toBe(false);
        seen.set(k, s.id);
      }
    }
  });

  it("matches key presses", () => {
    expect(actionFor(ev("s"), false)).toBe("split");
    expect(actionFor(ev("s", { ctrlKey: true }), false)).toBe("save");
    expect(actionFor(ev("s", { metaKey: true, shiftKey: true }), false)).toBe("save-as");
    expect(actionFor(ev("Z", { ctrlKey: true, shiftKey: true }), false)).toBe("redo");
    expect(actionFor(ev("z", { ctrlKey: true }), false)).toBe("undo");
    expect(actionFor(ev("="), false)).toBe("zoom-in");
    expect(actionFor(ev("+", { shiftKey: true }), false)).toBe("zoom-in");
    expect(actionFor(ev(" "), false)).toBe("play");
    expect(actionFor(ev("ArrowLeft", { shiftKey: true }), false)).toBe("back-second");
    expect(actionFor(ev("ArrowRight"), false)).toBe("next-frame");
    expect(actionFor(ev("s", { altKey: true }), false)).toBeNull();
  });

  it("lets only save through while typing in a field", () => {
    expect(actionFor(ev("s"), true)).toBeNull();
    expect(actionFor(ev("Delete"), true)).toBeNull();
    expect(actionFor(ev("s", { ctrlKey: true }), true)).toBe("save");
    expect(actionFor(ev("S", { ctrlKey: true, shiftKey: true }), true)).toBe("save-as");
  });
});
