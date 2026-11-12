// Editor tiles on the empty start screen (open an editor without a file) and the "New picture"
// window the image editor tile opens. Same tiles as src/umc/gui/widgets/editor_hub.py.
import { COMING_SOON, EDITORS } from "../core/editors";
import { BACKGROUNDS, PRESETS, type Background } from "../editor/presets";
import { icon } from "./icons";

const h = <K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> & { class?: string } = {}, ...children: (Node | string)[]) => {
  const el = document.createElement(tag);
  const { class: cls, ...rest } = props;
  Object.assign(el, rest);
  if (cls) el.className = cls;
  el.append(...children);
  return el;
};

/** "Or start in an editor" + one tile per editor; editors not ready are greyed out. */
export function editorHub(onChoose: (id: string) => void): HTMLElement {
  const tiles = EDITORS.map((e) => {
    const b = h("button", {
      class: "editor-tile", type: "button", disabled: !e.available,
      title: e.available ? `Open the ${e.title.toLowerCase()} without a file` : `The ${e.title.toLowerCase()} is coming soon`,
    },
    h("span", { class: "tile-top" }, icon(e.icon, 26), e.available ? "" : h("span", { class: "badge-pill" }, COMING_SOON)),
    h("span", { class: "tile-title" }, e.title),
    h("span", { class: "tile-blurb" }, e.blurb));
    b.dataset.editor = e.id;
    b.addEventListener("click", () => onChoose(e.id));
    return b;
  });
  return h("section", { class: "editor-hub", ariaLabel: "Editors" },
    h("h2", {}, "Or start in an editor"), h("div", { class: "editor-tiles" }, ...tiles));
}

export interface NewPicture {
  width: number;
  height: number;
  background: Background;
}

/** Size (a preset or custom) and background of a new picture; null when cancelled. */
export function newPictureDialog(): Promise<NewPicture | null> {
  const CUSTOM = "custom";
  const preset = h("select", { id: "np-size" },
    ...PRESETS.map(([label], i) => h("option", { value: String(i) }, label)), h("option", { value: CUSTOM }, "Custom size"));
  const num = (id: string, v: number) => h("input", { id, type: "number", min: "1", max: "16000", step: "1", value: String(v) });
  const w = num("np-width", PRESETS[0][1]);
  const hgt = num("np-height", PRESETS[0][2]);
  preset.addEventListener("change", () => {
    const p = PRESETS[+preset.value];
    if (p) [w.value, hgt.value] = [String(p[1]), String(p[2])];
  });
  const typed = () => {
    const i = PRESETS.findIndex((p) => p[1] === +w.value && p[2] === +hgt.value);
    preset.value = i < 0 ? CUSTOM : String(i);
  };
  w.addEventListener("input", typed);
  hgt.addEventListener("input", typed);
  const bg = h("div", { class: "row bg-choice", role: "radiogroup", ariaLabel: "Background" },
    ...(Object.keys(BACKGROUNDS) as Background[]).map((k) =>
      h("label", { class: "check" }, h("input", { type: "radio", name: "np-bg", value: k, checked: k === "white" }), ` ${BACKGROUNDS[k][0]}`)));
  const field = (label: string, id: string, el: HTMLElement) => h("div", { class: "field" }, h("label", { htmlFor: id }, label), el);
  const create = h("button", { class: "primary small", type: "submit" }, "Create");
  const cancel = h("button", { class: "btn", type: "button" }, "Cancel");
  const form = h("form", { method: "dialog" },
    h("h2", {}, "New picture"),
    field("Size", "np-size", preset), field("Width (px)", "np-width", w), field("Height (px)", "np-height", hgt),
    h("div", { class: "field" }, h("span", {}, "Background"), bg),
    h("div", { class: "row" }, h("div", { class: "spacer" }), cancel, create));
  const dlg = h("dialog", { class: "editor-dialog new-picture" }, form);
  document.body.append(dlg);
  return new Promise((resolve) => {
    let result: NewPicture | null = null;
    form.addEventListener("submit", (e) => {
      if (!w.reportValidity() || !hgt.reportValidity()) {
        e.preventDefault();
        return;
      }
      const chosen = form.querySelector<HTMLInputElement>("input[name=np-bg]:checked");
      result = { width: Math.round(+w.value), height: Math.round(+hgt.value), background: (chosen?.value ?? "white") as Background };
    });
    cancel.addEventListener("click", () => dlg.close());
    dlg.addEventListener("close", () => {
      dlg.remove();
      resolve(result);
    });
    dlg.showModal();
    preset.focus();
  });
}
