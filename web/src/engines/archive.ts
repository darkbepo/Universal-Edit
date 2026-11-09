// Archive reading via libarchive.js (libarchive compiled to WebAssembly, MIT/BSD): ZIP, 7Z, RAR,
// TAR(.gz/.xz/.bz2) and more. Writing: ZIP with fflate; TAR flavours with libarchive.js.
// Its worker + wasm are emitted by vite.config.ts under assets/libarchive/ (fixed names).
import { ConversionError } from "../core/types";

export interface Entry { path: string; data: Uint8Array }

type LA = typeof import("libarchive.js");
let ready: Promise<LA> | null = null;

async function lib(onStatus?: (m: string) => void): Promise<LA> {
  if (!ready) {
    onStatus?.("Loading archive engine…");
    ready = import("libarchive.js").then((m) => {
      m.Archive.init({ workerUrl: new URL("libarchive/worker-bundle.js", self.location.href).href });
      return m;
    });
    ready.catch(() => (ready = null));
  }
  return ready;
}

/** Strip anything that could escape the output folder: absolute paths, "..", drive letters. */
export function safePath(path: string): string | null {
  const parts = path.replace(/\\/g, "/").split("/").filter((p) => p && p !== ".");
  if (!parts.length || parts.some((p) => p === ".." || p.includes(":"))) return null;
  return parts.join("/");
}

export async function readArchive(file: File, password: string, onStatus: (m: string) => void): Promise<Entry[]> {
  const { Archive } = await lib(onStatus);
  let archive;
  try {
    archive = await Archive.open(file);
  } catch (e) {
    throw new ConversionError(`Couldn't open “${file.name}”. Is it a complete, valid archive?`, String(e));
  }
  const encrypted = await archive.hasEncryptedData();
  if (encrypted && !password) throw new ConversionError(`“${file.name}” is password-protected. Enter the right password in Settings.`);
  if (password) await archive.usePassword(password);
  try {
    await archive.extractFiles();
  } catch (e) {
    const msg = String(e);
    if (encrypted) {
      throw new ConversionError(/rar/i.test(file.name) ? `“${file.name}” is encrypted. Password-protected RAR files can't be opened yet.` : `Couldn't open “${file.name}” — wrong password?`, msg);
    }
    throw new ConversionError(`Couldn't open “${file.name}”. Is it a complete, valid archive?`, msg);
  }
  const out: Entry[] = [];
  for (const { file: f, path } of await archive.getFilesArray()) {
    if (!(f instanceof File)) continue; // not extracted (e.g. symlinks)
    const safe = safePath(`${path}${f.name}`);
    if (safe) out.push({ path: safe, data: new Uint8Array(await f.arrayBuffer()) });
  }
  return out;
}

const LEVEL: Record<string, number> = { store: 0, fast: 1, normal: 6, maximum: 9 };
const TAR: Record<string, string> = { tar: "none", "tar.gz": "gzip", "tar.xz": "xz", "tar.bz2": "bzip2" };

export async function writeArchive(entries: Entry[], ext: string, level: string, onStatus: (m: string) => void): Promise<Uint8Array> {
  if (ext === "zip") {
    const { zipSync } = await import("fflate");
    const tree: Record<string, [Uint8Array, { level: 0 | 1 | 6 | 9 }]> = {};
    for (const e of entries) tree[e.path] = [e.data, { level: (LEVEL[level] ?? 6) as 0 | 1 | 6 | 9 }];
    return zipSync(tree);
  }
  if (!(ext in TAR)) throw new ConversionError(`${ext.toUpperCase()} archives can only be created in the desktop app.`);
  const { Archive } = await lib(onStatus);
  const out = await Archive.write({
    // libarchive.js types say file: ArchiveEntryFile (a typo for File)
    files: entries.map((e) => ({ file: new File([e.data as BlobPart], e.path.split("/").pop()!), pathname: e.path })) as never,
    outputFileName: `archive.${ext}`,
    compression: TAR[ext] as never,
    format: "pax" as never,
    passphrase: null,
  });
  return new Uint8Array(await out.arrayBuffer());
}
