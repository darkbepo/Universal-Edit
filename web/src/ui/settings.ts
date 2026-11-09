// Renders an option schema as a form, automatically (the web twin of SettingsPanel).
import { type OptionSpec, type Value, type Values, visible } from "../core/options";

interface Field {
  spec: OptionSpec;
  get: () => Value;
  set: (v: Value) => void;
  nodes: HTMLElement[];
}

export class SettingsForm {
  readonly el = document.createElement("div");
  private fields: Field[] = [];
  private groups: { node: HTMLElement; members: Field[] }[] = [];
  private targetId = "";

  constructor(private onChange: () => void) {
    this.el.className = "form";
  }

  render(specs: OptionSpec[], targetId: string, values: Values): void {
    this.el.replaceChildren();
    this.fields = [];
    this.groups = [];
    this.targetId = targetId;
    let currentGroup = "";
    for (const spec of specs) {
      if (spec.group && spec.group !== currentGroup) {
        currentGroup = spec.group;
        const g = document.createElement("div");
        g.className = "group";
        g.textContent = spec.group;
        this.el.append(g);
        this.groups.push({ node: g, members: [] });
      }
      const field = this.makeField(spec, values[spec.name] ?? spec.default);
      this.fields.push(field);
      if (spec.group) this.groups[this.groups.length - 1].members.push(field);
      this.el.append(...field.nodes);
    }
    this.updateVisibility();
  }

  values(): Values {
    return Object.fromEntries(this.fields.map((f) => [f.spec.name, f.get()]));
  }

  hasVisibleFields(): boolean {
    const v = this.values();
    return this.fields.some((f) => visible(f.spec, v, this.targetId));
  }

  private changed = () => {
    this.updateVisibility();
    this.onChange();
  };

  private updateVisibility(): void {
    const v = this.values();
    for (const f of this.fields) {
      const show = visible(f.spec, v, this.targetId);
      for (const n of f.nodes) n.hidden = !show;
    }
    for (const g of this.groups) g.node.hidden = !g.members.some((f) => visible(f.spec, v, this.targetId));
  }

  private makeField(spec: OptionSpec, value: Value): Field {
    const id = `opt-${spec.name}`;
    const label = document.createElement("label");
    label.className = "lbl";
    label.htmlFor = id;
    label.textContent = spec.label;
    const cell = document.createElement("div");
    const field: Field = { spec, get: () => null, set: () => {}, nodes: [label, cell] };

    if (spec.kind === "bool") {
      const input = Object.assign(document.createElement("input"), { type: "checkbox", id, checked: Boolean(value) });
      input.addEventListener("change", this.changed);
      cell.append(input);
      field.get = () => input.checked;
    } else if (spec.kind === "choice") {
      const select = Object.assign(document.createElement("select"), { id });
      const choices = spec.choices ?? [];
      choices.forEach(([v, l], i) => select.append(new Option(l, String(i), false, v === value)));
      select.addEventListener("change", this.changed);
      cell.append(select);
      field.get = () => choices[Number(select.value)]?.[0] ?? spec.default;
    } else if (spec.kind === "slider") {
      const row = document.createElement("div");
      row.className = "slider-row";
      const step = spec.step ?? 1;
      const input = Object.assign(document.createElement("input"), {
        type: "range", id, min: String(spec.min), max: String(spec.max), step: String(step), value: String(value),
      });
      const out = document.createElement("output");
      const fmt = () => `${Number(input.value).toFixed(spec.decimals ?? 0)} ${spec.unit ?? ""}`.trim();
      out.textContent = fmt();
      input.addEventListener("input", () => {
        out.textContent = fmt();
        this.changed();
      });
      row.append(input, out);
      cell.append(row);
      field.get = () => Number(input.value);
    } else if (spec.kind === "int" || spec.kind === "float") {
      const input = Object.assign(document.createElement("input"), { type: "number", id, value: String(value) });
      if (spec.min !== undefined) input.min = String(spec.min);
      if (spec.max !== undefined) input.max = String(spec.max);
      input.step = spec.kind === "int" ? "1" : String(spec.step ?? "any");
      input.addEventListener("input", this.changed);
      cell.append(input);
      field.get = () => Number(input.value || spec.default);
    } else if (spec.kind === "color") {
      const input = Object.assign(document.createElement("input"), { type: "color", id, value: String(value) });
      input.addEventListener("input", this.changed);
      cell.append(input);
      field.get = () => input.value;
    } else if (spec.kind === "file") {
      const input = Object.assign(document.createElement("input"), { type: "file", id, accept: spec.accept ?? "" });
      input.addEventListener("change", this.changed);
      cell.append(input);
      field.get = () => input.files?.[0] ?? null;
    } else {
      const input = Object.assign(document.createElement("input"), { type: "text", id, value: String(value ?? "") });
      input.placeholder = String(spec.default ?? "");
      input.addEventListener("input", this.changed);
      cell.append(input);
      field.get = () => input.value;
    }
    if (spec.help) {
      const help = document.createElement("div");
      help.className = "help";
      help.textContent = spec.help;
      cell.append(help);
    }
    return field;
  }
}
