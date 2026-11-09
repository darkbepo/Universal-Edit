// FFmpeg compiled to WebAssembly (ffmpeg.wasm). Loaded once per worker, on first use.
// Multi-threaded core when the page is cross-origin isolated, single-threaded otherwise.
import { FFFSType, FFmpeg } from "@ffmpeg/ffmpeg";
import coreURL from "@ffmpeg/core?url";
import wasmURL from "@ffmpeg/core/wasm?url";
import mtCoreURL from "@ffmpeg/core-mt?url";
import mtWasmURL from "@ffmpeg/core-mt/wasm?url";
import mtWorkerURL from "@ffmpeg/core-mt/worker?url";
import { ConversionError } from "../core/types";
import { limitThreads } from "./threads";

export interface MediaInfo {
  duration: number;
  hasVideo: boolean;
  hasAudio: boolean;
  width: number;
  height: number;
  fps: number;
}

let instance: Promise<Engine> | null = null;

export function getFFmpeg(onStatus?: (msg: string) => void): Promise<Engine> {
  if (!instance) {
    instance = (async () => {
      onStatus?.("Loading video engine…");
      const ff = new FFmpeg();
      const threaded = (globalThis as { crossOriginIsolated?: boolean }).crossOriginIsolated === true;
      if (threaded) {
        await ff.load({ coreURL: mtCoreURL, wasmURL: mtWasmURL, workerURL: mtWorkerURL });
      } else {
        await ff.load({ coreURL, wasmURL });
      }
      const engine = new Engine(ff, threaded);
      await engine.loadEncoders();
      return engine;
    })();
    instance.catch(() => (instance = null));
  }
  return instance;
}

let mountCounter = 0;

export class Engine {
  private log: string[] = [];
  encoders = new Set<string>();

  constructor(
    readonly ff: FFmpeg,
    readonly threaded: boolean,
  ) {
    ff.on("log", ({ message }) => {
      this.log.push(message);
      if (this.log.length > 400) this.log.splice(0, this.log.length - 400);
    });
  }

  async loadEncoders(): Promise<void> {
    this.log = [];
    await this.ff.exec(["-hide_banner", "-encoders"]);
    for (const line of this.log) {
      const m = /^\s*[VAS][A-Z.]{5}\s+(\S+)/.exec(line);
      if (m) this.encoders.add(m[1]);
    }
  }

  pick(candidates: string[]): string | null {
    return candidates.find((c) => this.encoders.has(c)) ?? null;
  }

  /** Make a File readable by FFmpeg without copying it into memory. Returns its path. */
  async mount(file: File): Promise<{ path: string; release: () => Promise<void> }> {
    const dir = `/in${++mountCounter}`;
    await this.ff.createDir(dir);
    await this.ff.mount(FFFSType.WORKERFS, { files: [file] }, dir);
    return {
      path: `${dir}/${file.name}`,
      release: async () => {
        await this.ff.unmount(dir).catch(() => {});
        await this.ff.deleteDir(dir).catch(() => {});
      },
    };
  }

  async probe(path: string): Promise<MediaInfo> {
    this.log = [];
    await this.ff.exec(["-hide_banner", "-i", path]); // exits non-zero (no output); that's fine
    const text = this.log.join("\n");
    const dur = /Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)/.exec(text);
    const video = /Stream #\d+:\d+.*?: Video: (?!.*attached pic).*/.exec(text)?.[0] ?? "";
    const size = /\b(\d{2,5})x(\d{2,5})\b/.exec(video);
    const fps = /([\d.]+) fps/.exec(video);
    return {
      duration: dur ? Number(dur[1]) * 3600 + Number(dur[2]) * 60 + Number(dur[3]) : 0,
      hasVideo: Boolean(video),
      hasAudio: /Stream #\d+:\d+.*?: Audio:/.test(text),
      width: size ? Number(size[1]) : 0,
      height: size ? Number(size[2]) : 0,
      fps: fps ? Number(fps[1]) : 0,
    };
  }

  /** Subtitle tracks of a file, in order (index = N for -map 0:s:N). */
  async subtitleStreams(path: string): Promise<{ index: number; codec: string; language: string }[]> {
    this.log = [];
    await this.ff.exec(["-hide_banner", "-i", path]);
    const out: { index: number; codec: string; language: string }[] = [];
    for (const line of this.log) {
      const m = /Stream #\d+:\d+(?:\[\w+\])?(?:\((\w+)\))?: Subtitle: (\w+)/.exec(line);
      if (m) out.push({ index: out.length, codec: m[2], language: m[1] && m[1] !== "und" ? m[1] : "" });
    }
    return out;
  }

  /** Run ffmpeg; reports 0..1 progress based on output time vs ``totalSeconds``. */
  async run(args: string[], totalSeconds: number, progress: (f: number) => void, error: string) {
    this.log = [];
    const onProgress = ({ time }: { time: number }) => {
      if (totalSeconds > 0 && time > 0) progress(Math.min(1, time / 1e6 / totalSeconds));
    };
    this.ff.on("progress", onProgress);
    let code: number;
    try {
      const cmd = this.threaded ? limitThreads(args, navigator.hardwareConcurrency) : args;
      code = await this.ff.exec(["-hide_banner", "-nostdin", "-y", ...cmd]);
    } finally {
      this.ff.off("progress", onProgress);
    }
    if (code !== 0) {
      const tail = this.log.slice(-25).join("\n");
      throw new ConversionError(`${error} ${friendlyHint(tail)}`.trim(), tail);
    }
    progress(1);
  }

  async readAndDelete(path: string): Promise<Uint8Array> {
    const data = (await this.ff.readFile(path)) as Uint8Array;
    await this.ff.deleteFile(path).catch(() => {});
    return data;
  }

  async listFiles(dir: string): Promise<string[]> {
    const nodes = await this.ff.listDir(dir);
    return nodes.filter((n) => !n.isDir).map((n) => n.name).sort();
  }
}

function friendlyHint(log: string): string {
  const s = log.toLowerCase();
  if (s.includes("unknown encoder") || s.includes("encoder not found")) {
    return "The chosen codec isn't available in the browser version — try “Auto”.";
  }
  if (s.includes("could not find tag for codec") || s.includes("not supported in container")) {
    return "That codec can't be stored in this container — try “Auto”.";
  }
  if (s.includes("invalid data found")) return "The source file looks damaged or isn't a real video.";
  if (s.includes("out of memory") || s.includes("cannot enlarge memory")) {
    return "The file is too large for the browser version — try the desktop app.";
  }
  return "";
}
