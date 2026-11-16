// Properties mode's helper in the worker for PDF / Office / OpenDocument files (never offered on
// the convert page). Options: ``mode`` "read" → details.json (properties/document.ts
// DocumentDetails); "save" → the file with new details (``values`` = JSON {title, …}). A locked
// PDF needs ``password``; without (the right) one it fails with ``LOCKED`` / ``WRONG`` first in
// the message, so the page can show the password box. PDFs are saved incrementally by MuPDF (the
// new values are appended after the original bytes, so a signature stays valid for what it
// signed); zips are rewritten entry by entry (properties/document.ts rewriteZip).
import { ConversionError, type Converter } from "../core/types";

export const DOCUMENT_TAGS_TARGET = { id: "document-tags", label: "Document details", ext: "", group: "", converterId: "document-tags" };
export const NEEDS_PASSWORD = "[password] ";

type Doc = import("mupdf").PDFDocument;

export const documentTagsConverter: Converter = {
  id: "document-tags",
  family: "document",
  options: [],
  targets: () => [],
  async run(files, _target, o, ctx) {
    const D = await import("../properties/document");
    const f = files[0];
    const kind = D.kindOf(f.ext);
    const fail = (e: unknown): never => {
      if (e instanceof D.PasswordNeeded) throw new ConversionError(NEEDS_PASSWORD + e.message);
      if (e instanceof D.DocumentError) throw new ConversionError(e.message);
      throw e;
    };
    if (kind === null) return fail(new D.DocumentError(D.FORMAT_TEXT));
    if (f.file.size > D.MAX_BYTES) {
      throw new ConversionError(`“${f.name}” is too big to change in the browser (over 1 GB) — use the desktop app.`);
    }
    const bytes = new Uint8Array(await f.file.arrayBuffer());
    const save = o.mode === "save";
    const values = save ? (JSON.parse(String(o.values ?? "{}")) as Record<string, string>) : {};
    const json = (d: unknown) => [{ name: "details.json", data: new TextEncoder().encode(JSON.stringify(d)), type: "application/json" }];
    try {
      if (kind !== "pdf") {
        if (!save) return json(D.readZipDetails(bytes, kind, f.name));
        const data = D.saveZip(bytes, kind, values, f.name);
        return [{ name: f.name, data, type: f.file.type || "application/octet-stream" }];
      }
      const { getMuPDF } = await import("../engines/mupdf");
      const mupdf = await getMuPDF((m) => ctx.progress(0, m));
      let doc: Doc;
      try {
        const opened = mupdf.Document.openDocument(bytes, "application/pdf").asPDF();
        if (!opened) throw new Error("not a PDF");
        doc = opened;
      } catch {
        return fail(new D.DocumentError(`“${f.name}” couldn't be read.`));
      }
      try {
        const password = String(o.password ?? "");
        if (doc.needsPassword() && !(password && doc.authenticatePassword(password))) {
          return fail(new D.PasswordNeeded(password ? D.WRONG_PASSWORD_TEXT : D.LOCKED_TEXT));
        }
        if (!save) return json(pdfDetails(doc, D));
        const clean = D.cleanValues(values);
        writePdfInfo(doc, clean, D, (await import("../properties/xmp")).remove);
        const data = doc.saveToBuffer("incremental").asUint8Array().slice();
        return [{ name: f.name, data, type: "application/pdf" }];
      } finally {
        doc.destroy();
      }
    } catch (e) {
      return fail(e);
    }
  },
};

type DocModule = typeof import("../properties/document");

function pdfDetails(doc: Doc, D: DocModule) {
  const d = D.emptyDetails("pdf");
  for (const name of D.FIELDS) d[name] = (doc.getMetaData(`info:${D.PDF_KEYS[name]}`) ?? "").replace(/^[ \t\n\r\f\v]+|[ \t\n\r\f\v]+$/g, "");
  const acro = doc.getTrailer().get("Root").get("AcroForm");
  const flags = acro.isNull() ? null : acro.get("SigFlags");
  d.signed = flags !== null && flags.isNumber() && flags.asNumber() > 0;
  return d;
}

export function writePdfInfo(
  doc: Doc,
  values: import("../properties/document").Values,
  D: DocModule,
  removeXmp: (xmp: string, props: string[]) => string,
): void {
  const trailer = doc.getTrailer();
  let info = trailer.get("Info");
  if (info.isDictionary() && !info.isIndirect()) {
    // written inside the trailer itself: an incremental save keeps neither our changes nor
    // MuPDF's, so it becomes an object of its own first
    trailer.put("Info", doc.addObject(info));
    info = trailer.get("Info");
  }
  for (const name of D.FIELDS) {
    const text = values[name];
    if (text === undefined) continue;
    if (!info.isDictionary()) {
      // no Info yet: MuPDF makes one (a trailer changed by hand isn't kept by an incremental save)
      if (text) doc.setMetaData(`info:${D.PDF_KEYS[name]}`, text);
    } else if (text) info.put(D.PDF_KEYS[name], doc.newString(text));
    else info.delete(D.PDF_KEYS[name]);
  }
  // an older value in the XMP packet could win over the Info dictionary in some readers
  const meta = trailer.get("Root").get("Metadata");
  if (meta.isNull() || !meta.isStream()) return;
  const twins = D.FIELDS.filter((n) => values[n] !== undefined).map((n) => D.XMP_TWINS[n]);
  if (!twins.length) return;
  const packet = new TextDecoder().decode(meta.readStream().asUint8Array());
  const changed = removeXmp(packet, twins);
  if (changed !== packet) meta.writeStream(new TextEncoder().encode(changed));
}
