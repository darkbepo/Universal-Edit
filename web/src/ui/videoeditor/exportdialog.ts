// The video editor's "Export video" window (port of the desktop's ExportDialog, without the file
// name: the browser asks for that afterwards). The settings come from EXPORT_OPTIONS on the shared
// SettingsForm; the codec list shows only what this browser can encode for the chosen format.
// While a problem shows (missing files, too big for a download, no encoder) Export is disabled.
import type { OptionSpec, Values } from "../../core/options";
import {
  availableCodecs, chooseCodecs, EXPORT_OPTIONS, evenSize, exportEstimate, exportSize, gifSize, hasSound, type Encoders,
} from "../../videoedit/export";
import { exportProblem, LIMITS_NOTE } from "../../videoedit/limits";
import type { Project } from "../../videoedit/project";
import { SettingsForm } from "../settings";

const h = <K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> & { class?: string } = {}, ...children: (Node | string)[]) => {
  const el = document.createElement(tag);
  const { class: cls, ...rest } = props;
  Object.assign(el, rest);
  if (cls) el.className = cls;
  el.append(...children);
  return el;
};

export const MISSING_SOURCES = "Find the missing files first — Export needs every file.";
const DESKTOP_URL = "https://github.com/darkbepo/Universal-Edit/releases";
const H265_LABEL = "H.265 (if your device supports it)";

/** "About 12 MB" (at least 1 MB; GB from 1 GB on). */
export function aboutSize(bytes: number): string {
  const mb = bytes / (1 << 20);
  if (mb >= 1024) return `About ${(mb / 1024).toFixed(1)} GB`;
  return `About ${Math.max(1, Math.round(mb))} MB`;
}

/** EXPORT_OPTIONS with only these codec choices (labels as the window shows them). */
export function exportSpecs(codecs: readonly string[]): OptionSpec[] {
  return EXPORT_OPTIONS.map((s) => s.name !== "video_codec" ? s : {
    ...s,
    choices: (s.choices ?? []).filter(([v]) => codecs.includes(String(v))).map(([v, l]) => [v, v === "h265" ? H265_LABEL : l]),
  });
}

/** The export defaults with `values` over them (unknown names dropped). */
export function exportValues(values: Values = {}): Values {
  const out: Values = {};
  for (const s of EXPORT_OPTIONS) out[s.name] = s.name in values ? values[s.name] : s.default;
  return out;
}

/** The encoder check for this project's export at `values`' size. */
export type EncodersFor = (size: [number, number]) => Encoders;

export function exportSizeFor(p: Project, values: Values): [number, number] {
  return values.container === "gif"
    ? gifSize(p, Number(values.gif_width ?? 480))
    : evenSize(exportSize(p, String(values.resolution ?? "project")));
}

/** Why these values can't be exported (null: they can). `streams` = the result goes straight to disk. */
export async function problemFor(
  p: Project, values: Values, o: { missing: boolean; streams: boolean; encoders: EncodersFor },
): Promise<string | null> {
  if (o.missing) return MISSING_SOURCES;
  const gif = values.container === "gif"; // a GIF is always downloaded
  const big = exportProblem(exportEstimate(p, values), o.streams && !gif);
  if (big) return big;
  const chosen = await chooseCodecs(values, hasSound(p), o.encoders(exportSizeFor(p, values)));
  return "problem" in chosen ? chosen.problem : null;
}

/** Show the window; the chosen values, or null (Cancel / Esc). */
export function exportWindow(
  p: Project, start: Values, o: { missing: boolean; streams: boolean; encoders: EncodersFor },
): Promise<Values | null> {
  return new Promise((resolve) => {
    const form = new SettingsForm(() => void changed(), "vex");
    const estimate = h("div", { class: "ve-export-estimate" });
    estimate.setAttribute("aria-live", "polite");
    const problem = h("div", { class: "ve-export-problem", hidden: true });
    problem.setAttribute("role", "alert");
    const ok = h("button", { class: "primary small", type: "button", disabled: true }, "Export");
    const cancel = h("button", { class: "btn", type: "button" }, "Cancel");
    const dlg = h("dialog", { class: "editor-dialog video-export" },
      h("h2", { id: "ve-export-title" }, "Export video"),
      form.el,
      estimate,
      problem,
      h("p", { class: "hint ve-export-limits" }, LIMITS_NOTE),
      h("p", { class: "hint speed-tip ve-export-slow" },
        "Video is slower in the browser. The ",
        h("a", { href: DESKTOP_URL, target: "_blank", rel: "noopener" }, "Windows desktop app"),
        " uses your whole processor and exports video much faster."),
      h("div", { class: "row" }, h("div", { class: "spacer" }), cancel, ok));
    const probeSize: [number, number] = [1280, 720]; // as engines/webcodecs.ts asks; the chosen size is checked below
    const lists = new Map<string, string[]>(); // container → codecs (webm differs from the rest)
    const codecsFor = async (container: string): Promise<string[]> => {
      const key = container === "webm" ? "webm" : "other";
      if (!lists.has(key)) lists.set(key, await availableCodecs(o.encoders(probeSize), key === "webm" ? "webm" : "mp4"));
      return lists.get(key)!;
    };
    let container = "";
    let check = 0;
    let done = false;
    const build = async (values: Values) => {
      container = String(values.container);
      const codecs = await codecsFor(container);
      if (done) return;
      const v = { ...values, video_codec: codecs.includes(String(values.video_codec)) ? values.video_codec : "auto" };
      form.render(exportSpecs(codecs), "export", v);
    };
    const changed = async () => {
      let values = form.values();
      if (String(values.container) !== container) {
        await build(values);
        values = form.values();
      }
      const mine = ++check;
      estimate.textContent = aboutSize(exportEstimate(p, values));
      ok.disabled = true;
      const why = await problemFor(p, values, o).catch((e: unknown) => (e instanceof Error ? e.message : String(e)));
      if (mine !== check || done) return;
      problem.textContent = why ?? "";
      problem.hidden = !why;
      ok.disabled = !!why;
    };
    const finish = (values: Values | null) => {
      if (done) return;
      done = true;
      dlg.close();
      dlg.remove();
      resolve(values);
    };
    ok.addEventListener("click", () => finish(form.values()));
    cancel.addEventListener("click", () => finish(null));
    dlg.addEventListener("cancel", (e) => {
      e.preventDefault();
      finish(null);
    });
    dlg.addEventListener("keydown", (e) => e.stopPropagation()); // the editor's shortcuts stay out
    dlg.setAttribute("aria-labelledby", "ve-export-title");
    document.body.append(dlg);
    dlg.showModal();
    void build(exportValues(start)).then(() => changed()).then(() => ok.focus());
  });
}
