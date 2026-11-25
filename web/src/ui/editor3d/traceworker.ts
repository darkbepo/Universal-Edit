// Traces a logo picture off the page (the desktop does it on a worker thread): mask → outlines
// → shape, plus the silhouette for the panel's preview. One request at a time per page; the page
// drops answers to requests it no longer wants.
import { LogoError, mask, shape, trace, type Detail, type MaskMode, type Shape } from "../../editor3d/logo";

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
  id: number;
  shape: Shape | null;
  error: string; // plain words when there's no shape
  preview: Uint8ClampedArray;
}

self.onmessage = (e: MessageEvent<TraceRequest>) => {
  const r = e.data;
  const preview = new Uint8ClampedArray(r.w * r.h * 4);
  let found: Shape | null = null;
  let error = "";
  try {
    const m = mask(r.rgba, r.w, r.h, r.mode, r.threshold, r.invert);
    const [cr, cg, cb] = r.colour;
    for (let i = 0; i < m.length; i++) if (m[i]) preview.set([cr, cg, cb, 255], i * 4);
    found = shape(trace(m, r.w, r.h, r.detail), r.width);
  } catch (err) {
    error = err instanceof LogoError ? err.message : "Couldn't make a logo from this picture.";
    if (!(err instanceof LogoError)) console.error(err);
  }
  const answer: TraceAnswer = { id: r.id, shape: found, error, preview };
  (self as unknown as Worker).postMessage(answer, [preview.buffer]);
};
