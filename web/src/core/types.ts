// Shared types. Nothing in core/ touches the DOM or WebAssembly, so it runs in the page, the
// worker and in unit tests alike.
import type { OptionSpec, Values } from "./options";

export type Family = "image" | "video" | "audio" | "document" | "model" | "archive";

export interface InputFile {
  file: File;
  name: string;
  ext: string; // canonical, lowercase, no dot
  family: Family;
}

export interface OutputFile {
  /** Path inside the result set; may contain one folder level, e.g. "clip_frames/clip_00001.png". */
  name: string;
  data: Uint8Array;
  type: string;
}

export interface Target {
  id: string; // unique per input set: "png", "slideshow:mp4", "frames:png", "pages:png", …
  label: string; // button text
  ext: string;
  group: string; // heading the target is listed under
  converterId: string;
  hint?: string;
}

export interface RunContext {
  progress(fraction: number, message?: string): void;
}

export interface Converter {
  id: string;
  family: Family;
  /** Offered for inputs of any family — also mixed ones (e.g. "pack into a ZIP"). */
  anyFamily?: boolean;
  options: OptionSpec[];
  targets(files: InputFile[]): Target[];
  /** Runs inside the worker. Engines must be imported dynamically to keep the page bundle small. */
  run(files: InputFile[], target: Target, options: Values, ctx: RunContext): Promise<OutputFile[]>;
}

/** A failure with a message that is safe and useful to show to the user. */
export class ConversionError extends Error {
  constructor(
    message: string,
    public details = "",
  ) {
    super(message);
    this.name = "ConversionError";
  }
}
