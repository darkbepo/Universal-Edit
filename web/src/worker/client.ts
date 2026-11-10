// Page-side handle to the conversion worker.
import type { Values } from "../core/options";
import type { InputFile, OutputFile, Target } from "../core/types";
import type { RunRequest, WorkerMessage } from "./jobWorker";

export class JobCancelled extends Error {}

export class ConversionClient {
  private worker: Worker | null = null;
  private nextId = 1;
  private pending: { reject: (e: Error) => void } | null = null;

  private ensure(): Worker {
    this.worker ??= new Worker(new URL("./jobWorker.ts", import.meta.url), { type: "module" });
    return this.worker;
  }

  // The worker runs one job at a time: overlapping requests (the converter page and the image
  // editor share this client) wait their turn instead of overwriting each other's handlers.
  private tail: Promise<unknown> = Promise.resolve();

  run(
    target: Target,
    files: InputFile[],
    options: Values,
    onProgress: (fraction: number, message: string) => void,
  ): Promise<OutputFile[]> {
    const job = this.tail.then(() => this.runNow(target, files, options, onProgress));
    this.tail = job.catch(() => undefined);
    return job;
  }

  private runNow(
    target: Target,
    files: InputFile[],
    options: Values,
    onProgress: (fraction: number, message: string) => void,
  ): Promise<OutputFile[]> {
    const worker = this.ensure();
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending = { reject };
      worker.onmessage = (ev: MessageEvent<WorkerMessage>) => {
        const m = ev.data;
        if (m.id !== id) return;
        if (m.type === "progress") onProgress(m.fraction, m.message);
        else if (m.type === "done") {
          this.pending = null;
          resolve(m.outputs);
        } else {
          this.pending = null;
          const err = new Error(m.message);
          (err as Error & { details?: string }).details = m.details;
          reject(err);
        }
      };
      worker.onerror = (ev) => {
        this.pending = null;
        this.reset();
        reject(new Error(`The converter crashed (${ev.message || "out of memory?"}). Try a smaller file or the desktop app.`));
      };
      const req: RunRequest = { type: "run", id, converterId: target.converterId, target, files, options };
      worker.postMessage(req);
    });
  }

  cancel(): void {
    this.reset();
    this.pending?.reject(new JobCancelled("Cancelled."));
    this.pending = null;
  }

  private reset(): void {
    this.worker?.terminate();
    this.worker = null;
  }
}
