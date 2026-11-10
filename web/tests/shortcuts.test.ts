// Key matching for the editor's shortcut table, across keyboard layouts.
import { describe, expect, it } from "vitest";
import { SHORTCUTS, shortcutFor } from "../src/ui/editor/shortcuts";

type Press = { key: string; code: string; ctrlKey?: boolean; metaKey?: boolean; shiftKey?: boolean; altKey?: boolean };
const id = (p: Press) => shortcutFor({ ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...p })?.id ?? null;

describe("editor shortcuts", () => {
  it("US layout basics", () => {
    expect(id({ key: "z", code: "KeyZ", ctrlKey: true })).toBe("undo");
    expect(id({ key: "y", code: "KeyY", ctrlKey: true })).toBe("redo");
    expect(id({ key: "Z", code: "KeyZ", ctrlKey: true, shiftKey: true })).toBe("redo");
    expect(id({ key: "c", code: "KeyC", ctrlKey: true })).toBe("copy");
    expect(id({ key: "C", code: "KeyC", ctrlKey: true, shiftKey: true })).toBe("copy_merged");
    expect(id({ key: "v", code: "KeyV", metaKey: true })).toBe("paste"); // ⌘ counts as Ctrl
  });

  it("follows the character typed, not the key position (QWERTZ, Dvorak)", () => {
    // German: the key labelled Y sits where US has Z
    expect(id({ key: "y", code: "KeyZ", ctrlKey: true })).toBe("redo");
    expect(id({ key: "z", code: "KeyY", ctrlKey: true })).toBe("undo");
    // Dvorak: J is typed on the US C key, E on the US D key, G on the US U key
    expect(id({ key: "j", code: "KeyC", ctrlKey: true })).toBe("duplicate");
    expect(id({ key: "e", code: "KeyD", ctrlKey: true })).toBe("merge");
    expect(id({ key: "g", code: "KeyU" })).toBeNull(); // the Fill tool's letter, not "U"
  });

  it("falls back to the key position when a modifier changed the character", () => {
    expect(id({ key: "å", code: "KeyA", metaKey: true, altKey: true })).toBe("select_all_layers"); // ⌘⌥A on a Mac
    expect(id({ key: "a", code: "KeyA", ctrlKey: true, altKey: true })).toBe("select_all_layers");
    expect(id({ key: "n", code: "KeyN", ctrlKey: true, altKey: true })).toBe("new_layer");
  });

  it("symbols: Shift variants, numpad, ?", () => {
    expect(id({ key: "]", code: "BracketRight", ctrlKey: true })).toBe("layer_up");
    expect(id({ key: "}", code: "BracketRight", ctrlKey: true, shiftKey: true })).toBe("layer_top");
    expect(id({ key: "{", code: "BracketLeft", ctrlKey: true, shiftKey: true })).toBe("layer_bottom");
    expect(id({ key: "+", code: "Equal", ctrlKey: true, shiftKey: true })).toBe("zoom_in");
    expect(id({ key: "=", code: "Equal", ctrlKey: true })).toBe("zoom_in");
    expect(id({ key: "+", code: "NumpadAdd", ctrlKey: true })).toBe("zoom_in");
    expect(id({ key: "-", code: "NumpadSubtract", ctrlKey: true })).toBe("zoom_out");
    expect(id({ key: "?", code: "Slash", shiftKey: true })).toBe("help");
    expect(id({ key: "?", code: "Minus", shiftKey: true })).toBe("help"); // Danish / German layouts
    expect(id({ key: "[", code: "BracketLeft" })).toBe("smaller");
  });

  it("no two entries share a key", () => {
    const keys = SHORTCUTS.flatMap((s) => s.keys);
    expect(new Set(keys).size).toBe(keys.length);
  });
});
