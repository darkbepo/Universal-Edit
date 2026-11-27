// The 3D viewer's helper worker (the desktop uses worker threads): traces logo pictures (mask → outlines
// → shape with its cap triangles, + the silhouette for the panel's preview), and checks / repairs models
// for printing, and merges logos into a model (manifold-3d, loaded on first merge). The page drops
// answers it no longer wants (ids).
import { getManifold } from "../../engines/manifold";
import { combine, SolidError } from "../../editor3d/solid";
import { LogoError, mask, prepare, shapeOfMask, type Detail, type MaskMode, type Shape } from "../../editor3d/logo";
import { check, hasProblems, isClosed, notes, repair, repairChanged, repairedNote, soup, weld, type Report } from "../../editor3d/meshcheck";

/** Check a model (a triangle soup) for printing. */
export interface CheckRequest {
  kind: "check";
  id: number;
  positions: Float32Array;
  angle: number;
}

/** Repair a model, then check it again. */
export interface RepairRequest {
  kind: "repair";
  id: number;
  positions: Float32Array;
}

/** Join the model and raised logos into one body, cutting the engraved ones out. */
export interface MergeRequest {
  kind: "merge";
  id: number;
  base: Float32Array | null;
  raised: Float32Array[];
  engraved: Float32Array[];
}

export interface MergeAnswer {
  kind: "merge";
  id: number;
  positions: Float32Array | null; // the one body (a soup), or null with ``error``
  error: string;
}

export interface CheckAnswer {
  kind: "check";
  id: number;
  report: Report | null;
  notes: string[];
  ok: boolean;
  error: string;
}

export interface RepairAnswer {
  kind: "repair";
  id: number;
  changed: boolean;
  positions: Float32Array | null; // the repaired model, when it changed
  note: string;
  after: Report | null;
  closed: boolean;
  error: string;
}

export interface TraceRequest {
  id: number;
  rgba: Uint8ClampedArray | Uint8Array;
  w: number;
  h: number;
  mode: MaskMode;
  threshold: number;
  invert: boolean;
  detail: Detail;
  width: number;
  colour: [number, number, number]; // the silhouette's colour (the page's text colour)
}

export interface TraceAnswer {
  kind?: undefined;
  id: number;
  shape: Shape | null;
  error: string; // plain words when there's no shape
  preview: Uint8ClampedArray;
}

const post = (answer: object, transfer: Transferable[] = []) => (self as unknown as Worker).postMessage(answer, transfer);

self.onmessage = (e: MessageEvent<TraceRequest | CheckRequest | RepairRequest | MergeRequest>) => {
  const d = e.data;
  if ("kind" in d && d.kind === "merge") {
    void merge(d);
    return;
  }
  if ("kind" in d && d.kind === "check") {
    try {
      const report = check(weld(d.positions), d.angle);
      post({ kind: "check", id: d.id, report, notes: notes(report), ok: !hasProblems(report), error: "" } satisfies CheckAnswer);
    } catch (err) {
      console.error(err);
      post({ kind: "check", id: d.id, report: null, notes: [], ok: false, error: "Couldn't check this model." } satisfies CheckAnswer);
    }
    return;
  }
  if ("kind" in d && d.kind === "repair") {
    try {
      const x = repair(weld(d.positions));
      const after = check(x);
      const positions = repairChanged(x) ? soup(x) : null;
      const answer: RepairAnswer = { kind: "repair", id: d.id, changed: repairChanged(x), positions, note: repairedNote(x, after), after, closed: isClosed(after) && !hasProblems(after), error: "" };
      post(answer, positions ? [positions.buffer] : []);
    } catch (err) {
      console.error(err);
      post({ kind: "repair", id: d.id, changed: false, positions: null, note: "", after: null, closed: false, error: "Couldn't repair this model." } satisfies RepairAnswer);
    }
    return;
  }
  const r = d as TraceRequest;
  const preview = new Uint8ClampedArray(r.w * r.h * 4);
  let found: Shape | null = null;
  let error = "";
  try {
    const m = mask(r.rgba, r.w, r.h, r.mode, r.threshold, r.invert);
    const [cr, cg, cb] = r.colour;
    for (let i = 0; i < m.length; i++) if (m[i]) preview.set([cr, cg, cb, 255], i * 4);
    // cap triangles made here too, so placing / dragging on the page stays quick
    found = prepare(shapeOfMask(m, r.w, r.h, r.detail, r.width));
  } catch (err) {
    error = err instanceof LogoError ? err.message : "Couldn't make a logo from this picture.";
    if (!(err instanceof LogoError)) console.error(err);
  }
  const answer: TraceAnswer = { id: r.id, shape: found, error, preview };
  (self as unknown as Worker).postMessage(answer, [preview.buffer]);
};

async function merge(d: MergeRequest): Promise<void> {
  try {
    const positions = combine(await getManifold(), d.base, d.raised, d.engraved);
    post({ kind: "merge", id: d.id, positions, error: "" } satisfies MergeAnswer, [positions.buffer]);
  } catch (err) {
    if (!(err instanceof SolidError)) console.error(err);
    post({ kind: "merge", id: d.id, positions: null, error: err instanceof Error ? err.message : "Joining the parts failed." } satisfies MergeAnswer);
  }
}
