// @vitest-environment jsdom
// The website's automatic settings form: the `textarea` kind (video editor texts) and `setValues`
// on plain text fields (it did nothing before, which the video inspector needs).
import { describe, expect, it } from "vitest";
import { opt, type OptionSpec } from "../src/core/options";
import { SettingsForm } from "../src/ui/settings";

const TEXTAREA: OptionSpec = { name: "text", label: "Text", kind: "textarea", default: "Text" };

describe("SettingsForm", () => {
  it("renders a textarea field and reads it", () => {
    let changes = 0;
    const form = new SettingsForm(() => changes++, "ve");
    form.render([TEXTAREA], "", { text: "Hello\nworld" });
    const area = form.el.querySelector("textarea")!;
    expect(area).not.toBeNull();
    expect(area.id).toBe("ve-text");
    expect(area.value).toBe("Hello\nworld");
    expect(form.values()).toEqual({ text: "Hello\nworld" });
    area.value = "Typed";
    area.dispatchEvent(new Event("input", { bubbles: true }));
    expect(changes).toBe(1);
    expect(form.values().text).toBe("Typed");
  });

  it("setValues writes a textarea without announcing a change", () => {
    let changes = 0;
    const form = new SettingsForm(() => changes++);
    form.render([TEXTAREA], "", { text: "a" });
    form.setValues({ text: "b\nc" });
    expect(form.el.querySelector("textarea")!.value).toBe("b\nc");
    expect(form.values().text).toBe("b\nc");
    expect(changes).toBe(0);
  });

  it("setValues writes a text field", () => {
    const form = new SettingsForm(() => {});
    form.render([opt.text("font", "Font", "DejaVu Sans")], "", { font: "Arial" });
    form.setValues({ font: "Georgia" });
    expect((form.el.querySelector("input") as HTMLInputElement).value).toBe("Georgia");
    expect(form.values().font).toBe("Georgia");
  });

  it("keeps the same elements on setValues (focus stays)", () => {
    const form = new SettingsForm(() => {});
    form.render([TEXTAREA, opt.slider("b", "B", 0, -100, 100)], "", { text: "x", b: 0 });
    const area = form.el.querySelector("textarea");
    form.setValues({ text: "y", b: 10 });
    expect(form.el.querySelector("textarea")).toBe(area);
    expect(form.values()).toEqual({ text: "y", b: 10 });
  });
});
