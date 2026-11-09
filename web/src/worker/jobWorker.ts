// Runs conversions off the main thread. One long-lived worker keeps the engines warm;
// cancelling terminates it (and FFmpeg's own worker with it) and a fresh one is started.
import { converterById } from "../core/registry";
import { ConversionError, type InputFile, type Target } from "../core/types";
import type { Values } from "../core/options";

export interface RunRequest {
  type: "run";
  id: number;
  converterId: string;
  target: Target;
  files: InputFile[];
  options: Values;
}

export type WorkerMessage =
  | { type: "progress"; id: number; fraction: number; message: string }
  | { type: "done"; id: number; outputs: { name: string; data: Uint8Array; type: string }[] }
  | { type: "error"; id: number; message: string; details: string };

const post = (msg: WorkerMessage, transfer: Transferable[] = []) =>
  (self as unknown as Worker).postMessage(msg, transfer);

self.onmessage = async (ev: MessageEvent<RunRequest>) => {
  const req = ev.data;
  if (req.type !== "run") return;
  let last = 0;
  try {
    const converter = converterById(req.converterId);
    const outputs = await converter.run(req.files, req.target, req.options, {
      progress(fraction, message = "") {
        const now = performance.now();
        if (now - last < 80 && fraction < 1 && !message.startsWith("Loading")) return; // throttle
        last = now;
        post({ type: "progress", id: req.id, fraction: Math.max(0, Math.min(1, fraction)), message });
      },
    });
    post({ type: "done", id: req.id, outputs }, outputs.map((o) => o.data.buffer as ArrayBuffer));
  } catch (e) {
    const err = e instanceof ConversionError ? e : null;
    console.error(e);
    post({
      type: "error",
      id: req.id,
      message: err?.message ?? "Something went wrong during the conversion.",
      details: err?.details || String((e as Error)?.stack ?? e),
    });
  }
};
