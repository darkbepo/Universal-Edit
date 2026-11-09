// Tesseract OCR (tesseract.js, Apache-2.0) running in its own nested worker.
// Language data: the same pinned tessdata_fast 4.1.0 files as the desktop app
// (src/umc/tools/tessdata.py), checked against SHA-256 and kept in IndexedDB.
import { ConversionError } from "../core/types";
import workerURL from "tesseract.js/dist/worker.min.js?url";
import coreURL from "tesseract.js-core/tesseract-core-simd-lstm.wasm.js?url";

const URL_TEMPLATE = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/{code}.traineddata";

/** [code, name, sha256] — generated from the desktop app's table; keep both in sync. */
export const LANGUAGES: [string, string, string][] = [
  ["eng", "English", "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2"],
  ["dan", "Danish", "acb1fd074487a31d1294fcdfd7d7c673467ffd8aeacb2ccd61ebcbf04eb4e2fa"],
  ["deu", "German", "19d219bbb6672c869d20a9636c6816a81eb9a71796cb93ebe0cb1530e2cdb22d"],
  ["fra", "French", "ced037562e8c80c13122dece28dd477d399af80911a28791a66a63ac1e3445ca"],
  ["spa", "Spanish", "6f2e04d02774a18f01bed44b1111f2cd7f3ba7ac9dc4373cd3f898a40ea6b464"],
  ["ita", "Italian", "b8f89e1e785118dac4d51ae042c029a64edb5c3ee42ef73027a6d412748d8827"],
  ["por", "Portuguese", "c4932b937207a9514b7514d518b931a99938c02a28a5a5a553f8599ed58b7deb"],
  ["nld", "Dutch", "ced0e5e046a84c908a6aa7accbef9a232c4a5d9a8276691b81c6ee64d02963f6"],
  ["swe", "Swedish", "f7304988d41f833efebcc2d529df54b1903ecebbc3da1faabd19a0fddd4fe586"],
  ["nor", "Norwegian", "0451eb4f8049ae78196806bf878a389a2f40f1386fe038568cf4441226ba6ef2"],
  ["fin", "Finnish", "61a04cd62b507c3d9ae0e1cda399e6715ebf49dea9df47897c8acdcd3bd3e13c"],
  ["isl", "Icelandic", "5ec828c363f3f0062c3caf08216677cf5f3c0c981b05fab445123a16b98f10a9"],
  ["pol", "Polish", "c4476cdbc0e33d898d32345122b7be1cbf85ace15f920f06c7714756e1ef79b2"],
  ["ces", "Czech", "934bcaf97ef3348413263331131c9fa7f55f30db333c711929c124fb635f7e1b"],
  ["hun", "Hungarian", "35067e7cfe102dcdc953f9a758fdfaa6296b17a1ee6d874ee780fa306430b9fb"],
  ["ron", "Romanian", "9adfde6b51ba4b97efd10ea37c3070fd3fc2bad7815e81f5c3c198cd96216cc9"],
  ["tur", "Turkish", "7393381111e1152420fc4092cb44eef4237580d21b92bf30d7d221aad192c6b7"],
  ["ell", "Greek", "4fba8a0b461038d51f1c20d043d4f2ac38c4e778f1b90830847f7bd8fa3ba726"],
  ["rus", "Russian", "e16e5e036cce1d9ec2b00063cf8b54472625b9e14d893a169e2b0dedeb4df225"],
  ["ukr", "Ukrainian", "d59e53e2bded32f4445f124b4b00240fcac7e8044c003ab822ccb94f0b3db59b"],
  ["ara", "Arabic", "e3206d3dc87fd50c24a0fb9f01838615911d25168f4e64415244b67d2bb3e729"],
  ["heb", "Hebrew", "11f9e43ab227f786352a50f75c94c2e9906f1baba86d93276da19da7ce0904db"],
  ["hin", "Hindi", "4c73ffc59d497c186b19d1e90f5d721d678ea6b2e277b719bee4e2af12271825"],
  ["chi_sim", "Chinese (Simplified)", "a5fcb6f0db1e1d6d8522f39db4e848f05984669172e584e8d76b6b3141e1f730"],
  ["chi_tra", "Chinese (Traditional)", "529c5b5797d64b126065cd55f2bb4c7fd7b15790798091b1ff259941a829330b"],
  ["jpn", "Japanese", "1f5de9236d2e85f5fdf4b3c500f2d4926f8d9449f28f5394472d9e8d83b91b4d"],
  ["kor", "Korean", "6b85e11d9bbf07863b97b3523b1b112844c43e713df8b66418a081fd1060b3b2"],
  ["vie", "Vietnamese", "79df64caf7bcfb2a27df5042ecb6121e196eada34da774956995747636d5bfa1"],
  ["tha", "Thai", "294227cc2d1292b0acb28d61d4115c88252b96d466ca90b417cf4cf0c67bf07c"],
  ["ind", "Indonesian", "69786901da87ab8766c1ea7fbb10b28f2110c14da3f6c8f2735df131fba95d88"],
];
const NAMES = Object.fromEntries(LANGUAGES.map(([c, n]) => [c, n]));
const SHA = Object.fromEntries(LANGUAGES.map(([c, , s]) => [c, s]));

const hex = (buf: ArrayBuffer) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");

// tesseract.js reads language data from IndexedDB (idb-keyval's default "keyval-store"/"keyval",
// key "<cachePath>/<code>.traineddata"). We put verified files there and start it with
// cacheMethod "readOnly", so it never downloads anything itself. (Passing the bytes directly,
// as {code, data}, is broken in tesseract.js 7.0.0: initialize() joins the data, not the codes.)
const CACHE_PATH = "umc-tessdata-4.1.0";

function idb<T>(mode: IDBTransactionMode, op: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open("keyval-store");
    open.onupgradeneeded = () => open.result.createObjectStore("keyval");
    open.onerror = () => reject(open.error);
    open.onsuccess = () => {
      const req = op(open.result.transaction("keyval", mode).objectStore("keyval"));
      req.onsuccess = () => { resolve(req.result); open.result.close(); };
      req.onerror = () => { reject(req.error); open.result.close(); };
    };
  });
}

async function ensureLanguage(code: string, onStatus: (m: string) => void): Promise<void> {
  const key = `${CACHE_PATH}/${code}.traineddata`;
  if (await idb("readonly", (s) => s.count(key))) return;
  const url = URL_TEMPLATE.replace("{code}", code);
  onStatus(`Downloading ${NAMES[code]} language data (first time only)…`);
  let resp: Response;
  try {
    resp = await fetch(url);
  } catch (e) {
    throw new ConversionError(`Couldn't download the ${NAMES[code]} language data needed for OCR. Check your internet connection.`, String(e));
  }
  if (!resp.ok) throw new ConversionError(`Couldn't download the ${NAMES[code]} language data (HTTP ${resp.status}).`);
  const bytes = new Uint8Array(await resp.arrayBuffer());
  if (hex(await crypto.subtle.digest("SHA-256", bytes)) !== SHA[code]) {
    throw new ConversionError(`The ${NAMES[code]} language data download was damaged (checksum mismatch). Please try again.`);
  }
  await idb("readwrite", (s) => s.put(bytes, key));
}

type TWorker = import("tesseract.js").Worker;
let current: { langs: string; worker: Promise<TWorker> } | null = null;
let progressCb: ((p: number) => void) | undefined;

/** A Tesseract worker for ``codes`` (reused while the languages stay the same). */
export async function getTesseract(codes: string[], onStatus: (m: string) => void, onProgress?: (p: number) => void): Promise<TWorker> {
  const langs = codes.join("+");
  progressCb = onProgress;
  if (current?.langs === langs) return current.worker;
  const old = current;
  current = {
    langs,
    worker: (async () => {
      if (old) (await old.worker.catch(() => null))?.terminate();
      for (const code of codes) {
        if (!SHA[code]) throw new ConversionError(`OCR language “${code}” isn't supported.`);
        await ensureLanguage(code, onStatus);
      }
      onStatus("Loading OCR engine (first time only)…");
      const { createWorker } = await import("tesseract.js");
      return createWorker(langs, 1 /* LSTM only */, {
        workerPath: workerURL,
        corePath: coreURL,
        workerBlobURL: false,
        cacheMethod: "readOnly",
        cachePath: CACHE_PATH,
        langPath: URL_TEMPLATE.replace("/{code}.traineddata", ""),
        gzip: false,
        logger: (m) => { if (m.status === "recognizing text") progressCb?.(m.progress); },
      });
    })(),
  };
  current.worker.catch(() => (current = null));
  return current.worker;
}
