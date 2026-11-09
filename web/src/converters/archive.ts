// Archives: convert ZIP / 7Z / RAR / TAR(.gz/.xz/.bz2) to ZIP or TAR flavours, extract, and pack any
// files. Same target ids as the desktop app (converters/archive.py); writing 7Z and
// password-protecting are desktop-only (no browser encoder for them).
import { opt, type Values } from "../core/options";
import { ConversionError, type Converter, type InputFile, type OutputFile, type RunContext, type Target } from "../core/types";
import { baseName } from "./util";

const LABELS: Record<string, string> = { zip: "ZIP", tar: "TAR", "tar.gz": "TAR.GZ", "tar.xz": "TAR.XZ", "tar.bz2": "TAR.BZ2" };
const WRITABLE = Object.keys(LABELS);
const PACK = ["zip", "tar.gz"];
const MIME: Record<string, string> = { zip: "application/zip", tar: "application/x-tar", "tar.gz": "application/gzip", "tar.xz": "application/x-xz", "tar.bz2": "application/x-bzip2" };

/** "a.tar.gz" → "a" (baseName only strips the last extension). */
const stemOf = (f: InputFile) => (f.name.toLowerCase().endsWith("." + f.ext) ? f.name.slice(0, -f.ext.length - 1) : baseName(f.name));

const options = [
  opt.choice("level", "Compression", "normal", [["store", "None (just store the files)"], ["fast", "Fast"], ["normal", "Normal"], ["maximum", "Maximum (slowest, smallest)"]], { targets: ["zip", "pack:zip"], help: "TAR.GZ / .XZ / .BZ2 always use the normal level in the browser." }),
  opt.password("source_password", "Password of the archive you added", { targets: ["archive:extract", ...WRITABLE], help: "Only needed when the archive is password-protected (ZIP or 7Z)." }),
  opt.text("archive_name", "Archive name", "", { targets: PACK.map((e) => `pack:${e}`), help: "Without extension. Empty = named after the first file." }),
];

export const archiveConverter: Converter = {
  id: "archive",
  family: "archive",
  options,
  targets(files) {
    const single = files.length === 1 ? files[0].ext : "";
    const t = (id: string, label: string, ext: string, hint: string) => ({ id, label, ext, hint, group: "Archive", converterId: "archive" });
    return [t("archive:extract", "Extract files", "", "Unpack everything (download all, or save to a folder)"),
      ...WRITABLE.filter((e) => e !== single).map((e) => t(e, LABELS[e], e, `Repack as ${LABELS[e]}`))];
  },
  async run(files: InputFile[], target: Target, o: Values, ctx: RunContext): Promise<OutputFile[]> {
    const { readArchive, writeArchive } = await import("../engines/archive");
    const outputs: OutputFile[] = [];
    const used = new Set<string>();
    for (const [i, f] of files.entries()) {
      ctx.progress(i / files.length, `Opening ${f.name} (${i + 1}/${files.length})`);
      const entries = await readArchive(f.file, String(o.source_password ?? ""), (m) => ctx.progress(i / files.length, m));
      if (!entries.length) throw new ConversionError(`“${f.name}” doesn't contain any files.`);
      let stem = stemOf(f);
      for (let k = 1; used.has(stem); k++) stem = `${stemOf(f)} (${k})`;
      used.add(stem);
      if (target.id === "archive:extract") {
        for (const e of entries) outputs.push({ name: `${stem}/${e.path}`, data: e.data, type: "application/octet-stream" });
      } else {
        ctx.progress((i + 0.5) / files.length, `Writing ${stem}.${target.ext}`);
        outputs.push({ name: `${stem}.${target.ext}`, data: await writeArchive(entries, target.ext, String(o.level), (m) => ctx.progress(i / files.length, m)), type: MIME[target.ext] });
      }
    }
    ctx.progress(1, "Done");
    return outputs;
  },
};

export const packConverter: Converter = {
  id: "pack",
  family: "document", // unused: anyFamily offers it for every kind of input
  anyFamily: true,
  options,
  targets(files) {
    if (files.every((f) => f.family === "archive")) return [];
    return PACK.map((e) => ({ id: `pack:${e}`, label: LABELS[e], ext: e, group: "Archive", converterId: "pack", hint: `Put the files into one ${LABELS[e]} archive` }));
  },
  async run(files, target, o, ctx) {
    const { writeArchive } = await import("../engines/archive");
    const names = new Set<string>();
    const entries = [];
    for (const f of files) {
      let name = f.name;
      for (let k = 1; names.has(name.toLowerCase()); k++) name = `${baseName(f.name)} (${k})${f.name.slice(baseName(f.name).length)}`;
      names.add(name.toLowerCase());
      entries.push({ path: name, data: new Uint8Array(await f.file.arrayBuffer()) });
    }
    const first = baseName(files[0].name);
    let stem = String(o.archive_name ?? "").trim() || (files.length === 1 ? first : `${first} + ${files.length - 1} more`);
    stem = stem.replace(/[<>:"/\\|?*]/g, "").trim() || "archive";
    ctx.progress(0.1, `Writing ${stem}.${target.ext}`);
    const data = await writeArchive(entries, target.ext, String(o.level), (m) => ctx.progress(0.1, m));
    ctx.progress(1, "Done");
    return [{ name: `${stem}.${target.ext}`, data, type: MIME[target.ext] }];
  },
};
