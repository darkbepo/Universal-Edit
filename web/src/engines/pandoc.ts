// Pandoc compiled to WebAssembly (the official pandoc-wasm build). Loaded once, on first use.
// ~16 MB gzipped, so it is only fetched when a document conversion actually needs it.
import { createPandocInstance } from "pandoc-wasm-core";
import wasmURL from "pandoc-wasm-binary?url";
import { ConversionError } from "../core/types";

type Pandoc = Awaited<ReturnType<typeof createPandocInstance>>;
let ready: Promise<Pandoc> | null = null;

export function getPandoc(onStatus?: (msg: string) => void): Promise<Pandoc> {
  if (!ready) {
    ready = (async () => {
      onStatus?.("Loading document engine (first time only)…");
      const wasm = await (await fetch(wasmURL)).arrayBuffer();
      return createPandocInstance(wasm);
    })();
    ready.catch(() => (ready = null));
  }
  return ready;
}

/** Our extension → pandoc reader / writer names. */
export const READERS: Record<string, string> = {
  md: "markdown", html: "html", epub: "epub", docx: "docx", odt: "odt", rst: "rst",
  tex: "latex", org: "org", txt: "markdown", rtf: "rtf", csv: "csv", tsv: "tsv",
  xlsx: "xlsx", pptx: "pptx", ipynb: "ipynb", fb2: "fb2",
};
export const WRITERS: Record<string, string> = {
  md: "gfm", html: "html", epub: "epub3", docx: "docx", odt: "odt", rtf: "rtf",
  txt: "plain", rst: "rst", tex: "latex", org: "org", pptx: "pptx",
};
const BINARY_OUT = new Set(["docx", "odt", "epub", "pptx"]);
const STANDALONE = new Set(["html", "rtf", "tex", "epub", "docx", "odt", "pptx"]);
const MEDIA_OUT = new Set(["md", "rst", "org", "tex"]);

export interface PandocResult {
  data: Uint8Array;
  /** Images pulled out of the document (for Markdown-like outputs), path → bytes. */
  media: Record<string, Uint8Array>;
}

export async function runPandoc(
  input: Uint8Array,
  from: string,
  to: string,
  opts: { stem: string; toc?: boolean; keepMedia?: boolean; onStatus?: (m: string) => void },
): Promise<PandocResult> {
  const pandoc = await getPandoc(opts.onStatus);
  const inName = `input.${from}`;
  const outName = `output.${to}`;
  const options: Record<string, unknown> = {
    from: READERS[from],
    to: WRITERS[to],
    "input-files": [inName],
  };
  if (BINARY_OUT.has(to)) options["output-file"] = outName;
  if (STANDALONE.has(to)) options.standalone = true;
  if (to === "html") {
    options["embed-resources"] = true;
    options.metadata = { pagetitle: opts.stem };
  }
  if (to === "epub") options.metadata = { title: opts.stem };
  if (opts.keepMedia && MEDIA_OUT.has(to)) options["extract-media"] = `${opts.stem}_media`;
  if (opts.toc) options["table-of-contents"] = true;

  const result = await pandoc.convert(options, null, { [inName]: new Blob([input as BlobPart]) });
  const errors = result.stderr
    .split("\n")
    .filter((l: string) => l.startsWith("ERROR") || l.includes("Error"))
    .join("\n");
  let data: Uint8Array;
  if (BINARY_OUT.has(to)) {
    const blob = result.files[outName] as Blob | undefined;
    if (!blob) throw new ConversionError(`Couldn't convert this ${from.toUpperCase()} file to ${to.toUpperCase()}.`, result.stderr);
    data = new Uint8Array(await blob.arrayBuffer());
  } else {
    if (errors && !result.stdout) throw new ConversionError(`Couldn't convert this ${from.toUpperCase()} file to ${to.toUpperCase()}.`, result.stderr);
    data = new TextEncoder().encode(result.stdout);
  }
  const media: Record<string, Uint8Array> = {};
  if (opts.keepMedia) {
    for (const [path, blob] of Object.entries(result.mediaFiles ?? {})) {
      media[path] = new Uint8Array(await (blob as Blob).arrayBuffer());
    }
  }
  return { data, media };
}
