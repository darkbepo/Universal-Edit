// The "Missing files" window (port of the desktop's LocateDialog): a saved project's sources have
// to be picked again in the browser. Each row is found by hand ("Find file…") or removed; "Pick
// several files…" matches one multi-file pick to every source it fits (videoedit/locate.ts).
import { applyPicks, type Choice } from "../../videoedit/locate";
import type { Source } from "../../videoedit/project";

const h = <K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> & { class?: string } = {}, ...children: (Node | string)[]) => {
  const el = document.createElement(tag);
  const { class: cls, ...rest } = props;
  Object.assign(el, rest);
  if (cls) el.className = cls;
  el.append(...children);
  return el;
};

const baseName = (path: string): string => path.split(/[\\/]/).pop() ?? path;

export const LOCATE_INTRO =
  "Some files of this project can't be found. Find them, or remove them (and the clips that use them) from the project.";

/** Ask for the files of `missing`: source id → the file to use, or null (remove). null = Cancel. */
export function locateWindow(missing: readonly Source[]): Promise<Map<string, File | null> | null> {
  return new Promise((resolve) => {
    const files: File[] = [];
    let choices = new Map<string, Choice>(missing.map((s) => [s.id, null]));
    const statuses = new Map<string, HTMLElement>();
    const picker = (multiple: boolean) => h("input", { type: "file", multiple, hidden: true });
    const one = picker(false);
    const several = picker(true);
    let finding: string | null = null;
    const show = () => {
      for (const s of missing) {
        const c = choices.get(s.id);
        statuses.get(s.id)!.textContent = c ? `Use “${files[c.file].name}”` : "Will be removed";
      }
    };
    const list = h("div", { class: "ve-locate-list" });
    for (const s of missing) {
      const status = h("span", { class: "ve-locate-status" }, "Will be removed");
      statuses.set(s.id, status);
      const find = h("button", { class: "btn small", type: "button" }, "Find file…");
      find.addEventListener("click", () => {
        finding = s.id;
        one.click();
      });
      const remove = h("button", { class: "btn small", type: "button" }, "Remove from project");
      remove.addEventListener("click", () => {
        choices = new Map(choices).set(s.id, null);
        show();
      });
      list.append(h("div", { class: "ve-locate-row" },
        h("span", { class: "ve-locate-name" }, `“${baseName(s.path)}”`), status, find, remove));
    }
    one.addEventListener("change", () => {
      const f = one.files?.[0];
      one.value = "";
      if (!f || finding === null) return;
      files.push(f);
      choices = new Map(choices).set(finding, { file: files.length - 1 });
      show();
    });
    several.addEventListener("change", () => {
      const picked = [...(several.files ?? [])];
      several.value = "";
      if (!picked.length) return;
      files.push(...picked);
      choices = applyPicks(missing, files.map((f) => ({ name: f.name, size: f.size })), choices);
      show();
    });
    const pick = h("button", { class: "btn small", type: "button", title: "Pick the project's files at once; each one is matched by its name and size" }, "Pick several files…");
    pick.addEventListener("click", () => several.click());
    const ok = h("button", { class: "primary small", type: "button" }, "Open project");
    const cancel = h("button", { class: "btn", type: "button" }, "Cancel");
    const dlg = h("dialog", { class: "editor-dialog ve-locate" },
      h("h2", {}, "Missing files"),
      h("p", { class: "ve-locate-intro" }, LOCATE_INTRO),
      list,
      h("div", { class: "row" }, pick, one, several),
      h("div", { class: "row" }, h("div", { class: "spacer" }), cancel, ok));
    const finish = (accepted: boolean) => {
      dlg.close();
      dlg.remove();
      if (!accepted) return resolve(null);
      const out = new Map<string, File | null>();
      for (const s of missing) {
        const c = choices.get(s.id);
        out.set(s.id, c ? files[c.file] : null);
      }
      resolve(out);
    };
    ok.addEventListener("click", () => finish(true));
    cancel.addEventListener("click", () => finish(false));
    dlg.addEventListener("cancel", (e) => {
      e.preventDefault();
      finish(false);
    });
    dlg.addEventListener("keydown", (e) => e.stopPropagation()); // the editor's shortcuts stay out
    document.body.append(dlg);
    dlg.showModal();
    ok.focus();
  });
}
