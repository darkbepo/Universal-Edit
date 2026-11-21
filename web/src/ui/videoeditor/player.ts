// The video editor's playback (port of the desktop's gui/videoedit/player.py). Frames: one
// FrameStream per visible video layer (keyed by item id, opened at the preview's height, closed
// when its item leaves the frame), pictures from the bank's cached bitmap, drawn like the still
// (draw.ts) on an offscreen canvas and handed to the preview. Sound: CHUNK_SECONDS chunks mixed
// LEAD_SECONDS ahead (mixWindow + one Limiter per run) and scheduled on an AudioContext, whose
// clock times the frames. Without a sound device the wall clock times them and it is silent.
// Every async step checks that its run is still the current one before it schedules or paints.
import { DEFAULT_VOLUME, gainFor } from "../../audioedit/volume";
import { frameAt, type Layer } from "../../videoedit/compose";
import { Limiter, SAMPLE_RATE, mixWindow } from "../../videoedit/mix";
import { length, outputFps, outputSize, type Project } from "../../videoedit/project";
import { textAt } from "../../videoedit/textlayout";
import { drawFrame, drawText, type Ctx2D, type Picture } from "./draw";
import { hasFont, loadBundledFonts } from "./fonts";
import { FrameStream } from "./framestream";
import { ARM_MS, CHUNK_SECONDS, LEAD_SECONDS, NO_SOUND, SOUND_NOT_STARTED, audioNeeds, pickClock } from "./playmath";
import { surfaces } from "./preview";
import type { SoundStore } from "./sound";
import type { SourceBank } from "./sources";

export interface PlayerEvents {
  frame(paint: (ctx: CanvasRenderingContext2D, size: [number, number]) => void): void;
  position(t: number): void;
  ended(): void;
  /** Playback broke off on an error at project second `t` (not the end: the playhead stays). */
  stopped(t: number): void;
  notice(msg: string): void;
}

/** A chunk whose peak (after the limiter) passes this counts as loud (stats, for the e2e test). */
const LOUD = 0.01;
/** The timer that ends a run and keeps the sound coming when rAF doesn't run (a hidden tab). */
const TICK_MS = 50;
/** An AudioContext that still hasn't started after this long: play silently by the wall clock. */
const SUSPENDED_MS = 1000;
/** At the start of a run, wait this long at most for every video layer's first frame before
 *  painting (until then the still stays, so playing never starts with a black flash). */
const FIRST_FRAME_MS = 500;

interface Stream { stream: FrameStream<Picture>; last: Picture | null }

interface Run {
  p: Project;
  total: number;
  size: [number, number];
  fps: number;
  clock: "audio" | "wall";
  /** Project seconds at the clock's start. */
  t0: number;
  /** The audio clock starts (`ctxStart`) only once the first chunk is mixed (or after ARM_MS),
   *  so a cold decode doesn't eat the opening sound; until then the clock stays at t0. */
  armed: boolean;
  /** performance.now() at Play (the arming cap and the first-frame wait count from it). */
  began: number;
  wallStart: number;
  ctxStart: number;
  /** The last clock time seen (where an error stops the playhead). */
  last: number;
  streams: Map<string, Stream>;
  pictures: Map<string, Picture | null | "loading">;
  limiter: Limiter;
  /** Project seconds mixed so far (the next chunk starts here). */
  scheduled: number;
  mixing: boolean;
  nodes: Set<AudioBufferSourceNode>;
  raf: number;
  timer: ReturnType<typeof setInterval> | null;
  lastPos: number;
  painted: boolean;
  off: { canvas: CanvasImageSource; ctx: Ctx2D };
}

export class Player {
  loop = false;
  private vol = DEFAULT_VOLUME;
  private ctx: AudioContext | null = null;
  private gain: GainNode | null = null;
  private run: Run | null = null;
  private forcedWall = false;
  private noticed = false;
  private counts = { chunks: 0, loudChunks: 0, lateChunks: 0, frames: 0 };
  private breakDraw = false;

  constructor(private bank: () => SourceBank, private sound: () => SoundStore, private on: PlayerEvents) {}

  get playing(): boolean {
    return this.run !== null;
  }

  get volume(): number {
    return this.vol;
  }

  set volume(v: number) {
    this.vol = Math.min(1, Math.max(0, v));
    if (this.gain && this.ctx) this.gain.gain.setTargetAtTime(gainFor(this.vol), this.ctx.currentTime, 0.015);
  }

  /** Play `p` from `t` (clamped to it), frames made at `size`. */
  play(p: Project, t: number, size: [number, number]): void {
    this.stop();
    this.counts = { chunks: 0, loudChunks: 0, lateChunks: 0, frames: 0 };
    this.start(p, t, size);
  }

  private start(p: Project, t: number, size: [number, number]): void {
    const total = length(p);
    if (!(total > 0)) return;
    const ctx = this.context();
    if (!ctx) this.silent();
    else if (ctx.state !== "running") void ctx.resume().catch(() => undefined);
    if (p.text_rows.some((r) => r.length)) void loadBundledFonts().catch(() => undefined);
    const run: Run = {
      p, total, size, fps: outputFps(p),
      clock: ctx ? "audio" : "wall",
      t0: Math.max(0, Math.min(t, total)),
      armed: !ctx,
      began: performance.now(),
      wallStart: performance.now(),
      ctxStart: 0,
      last: Math.max(0, Math.min(t, total)),
      streams: new Map(), pictures: new Map(),
      limiter: new Limiter(),
      scheduled: Math.max(0, Math.min(t, total)),
      mixing: false,
      nodes: new Set(),
      raf: 0, timer: null, lastPos: -Infinity, painted: false,
      off: surfaces.make(size[0], size[1]),
    };
    this.run = run;
    for (const s of p.sources) if (s.kind === "image") this.pictureOf(run, s.id);
    const frameLoop = () => {
      if (run !== this.run) return;
      this.tick(run, true);
      if (run === this.run) run.raf = requestAnimationFrame(frameLoop);
    };
    run.raf = requestAnimationFrame(frameLoop);
    run.timer = setInterval(() => this.tick(run, false), TICK_MS);
    this.tick(run, false); // the first sound chunk starts mixing now
  }

  stop(): void {
    const run = this.run;
    this.run = null;
    if (!run) return;
    cancelAnimationFrame(run.raf);
    if (run.timer !== null) clearInterval(run.timer);
    this.silence(run);
    for (const s of run.streams.values()) s.stream.close();
    run.streams.clear();
  }

  /** Leaving: stop and close the AudioContext (a later play makes a new one). */
  dispose(): void {
    this.stop();
    const ctx = this.ctx;
    this.ctx = null;
    this.gain = null;
    if (ctx) void ctx.close().catch(() => undefined);
  }

  /** Tests: act as if there were no sound device. */
  forceWallClock(): void {
    this.forcedWall = true;
    this.dispose();
  }

  /** `lateChunks` = chunks that started late (cut at the front, or dropped). */
  stats(): { chunks: number; loudChunks: number; lateChunks: number; frames: number } {
    return { ...this.counts };
  }

  /** Tests: the next frame drawn throws. */
  breakNextDraw(): void {
    this.breakDraw = true;
  }

  private context(): AudioContext | null {
    if (this.ctx) return this.ctx;
    const has = !this.forcedWall && typeof AudioContext !== "undefined";
    if (pickClock(has) === "wall") return null;
    try {
      let ctx: AudioContext;
      try {
        ctx = new AudioContext({ sampleRate: SAMPLE_RATE }); // 48 kHz chunks then stitch sample-accurately
      } catch {
        ctx = new AudioContext();
      }
      const gain = ctx.createGain();
      gain.gain.value = gainFor(this.vol);
      gain.connect(ctx.destination);
      this.ctx = ctx;
      this.gain = gain;
      return ctx;
    } catch {
      return null;
    }
  }

  /** No sound this run: said once per page. */
  private silent(): void {
    if (this.noticed) return;
    this.noticed = true;
    this.on.notice(NO_SOUND);
  }

  private silence(run: Run): void {
    for (const n of run.nodes) {
      try {
        n.stop();
        n.disconnect();
      } catch {
        // already ended
      }
    }
    run.nodes.clear();
  }

  /** Start the audio clock now (a little ahead, so the first chunk isn't late). */
  private arm(run: Run): void {
    if (run.armed) return;
    run.armed = true;
    run.ctxStart = (this.ctx?.currentTime ?? 0) + 0.02;
    run.wallStart = performance.now();
  }

  /** Context seconds since the run's start, less what the speakers haven't played yet (frames
   *  follow what is heard). */
  private heard(run: Run, ctx: AudioContext): number {
    const latency = ctx.outputLatency ?? ctx.baseLatency ?? 0;
    return Math.max(0, ctx.currentTime - (Number.isFinite(latency) ? latency : 0) - run.ctxStart);
  }

  private clock(run: Run): number {
    if (run.clock === "audio") {
      if (!run.armed) {
        if (performance.now() - run.began < ARM_MS) return run.t0;
        this.arm(run); // the first chunk is slow: start anyway
      }
      const ctx = this.ctx;
      if (ctx && ctx.state === "running") return run.t0 + this.heard(run, ctx);
      const elapsed = performance.now() - run.wallStart;
      if (ctx && elapsed < SUSPENDED_MS) return run.t0 + this.heard(run, ctx);
      // the context never started (or is gone): carry on silently by the wall clock
      const at = run.t0 + (ctx ? this.heard(run, ctx) : 0);
      run.clock = "wall";
      run.t0 = at;
      run.wallStart = performance.now();
      this.silence(run);
      if (ctx) this.on.notice(SOUND_NOT_STARTED); // not "no device": Play may well work next time
      else this.silent();
      return at;
    }
    return run.t0 + (performance.now() - run.wallStart) / 1000;
  }

  private tick(run: Run, draw: boolean): void {
    if (run !== this.run) return;
    try {
      const t = this.clock(run);
      run.last = t;
      if (t >= run.total) {
        this.finish(run);
        return;
      }
      this.pumpSound(run, t);
      if (draw) this.draw(run, t);
      if (t - run.lastPos >= 1 / run.fps - 1e-9) {
        run.lastPos = t;
        this.on.position(t);
      }
    } catch (e) {
      console.error("Playback stopped", e);
      this.stop();
      this.on.stopped(run.last);
    }
  }

  /** The end of the project (player._ended): again from 0 with Loop on, else stop there. */
  private finish(run: Run): void {
    this.stop();
    if (this.loop && run.total > 0) {
      this.start(run.p, 0, run.size);
      return;
    }
    this.on.position(run.total);
    this.on.ended();
  }

  // ---------------------------------------------------------------- sound
  private pumpSound(run: Run, t: number): void {
    if (run.clock !== "audio" || run.mixing || !this.ctx) return;
    if (run.scheduled >= run.total - 1e-9 || run.scheduled >= t + LEAD_SECONDS) return;
    run.mixing = true;
    void this.mixChunk(run)
      .catch((e) => console.error("A sound chunk couldn't be mixed", e))
      .finally(() => {
        run.mixing = false;
        if (run === this.run) this.pumpSound(run, this.clock(run));
      });
  }

  private async mixChunk(run: Run): Promise<void> {
    const ctx = this.ctx!;
    const start = run.scheduled;
    const end = Math.min(start + CHUNK_SECONDS, run.total);
    const frames = Math.max(0, Math.round((end - start) * SAMPLE_RATE));
    run.scheduled = start + CHUNK_SECONDS;
    if (frames === 0) return;
    const dur = frames / SAMPLE_RATE;
    const due = () => run.ctxStart + (start - run.t0);
    const sound = this.sound();
    if (!run.armed || ctx.currentTime < due() + dur) {
      try {
        await sound.ensure(audioNeeds(run.p, start, frames));
      } catch (e) {
        console.error("Sound couldn't be decoded", e); // that part stays silent
      }
      if (run !== this.run || run.clock !== "audio" || this.ctx !== ctx) return;
    } // else its time has passed: mixed from what's there (the limiter must see every chunk)
    const chs = mixWindow(run.p, start, frames, sound.lookup);
    run.limiter.process(chs);
    this.arm(run); // the first chunk is ready: the clock starts now (after ARM_MS it already has)
    const when = due();
    const now = ctx.currentTime;
    if (now > when) this.counts.lateChunks++;
    if (now >= when + dur || !this.gain) return; // too late: silence
    const buffer = ctx.createBuffer(2, frames, SAMPLE_RATE);
    buffer.copyToChannel(chs[0] as Float32Array<ArrayBuffer>, 0);
    buffer.copyToChannel(chs[1] as Float32Array<ArrayBuffer>, 1);
    const node = ctx.createBufferSource();
    node.buffer = buffer;
    node.connect(this.gain);
    if (now <= when) node.start(when);
    else node.start(now, now - when);
    run.nodes.add(node);
    node.onended = () => run.nodes.delete(node);
    let peak = 0;
    for (const ch of chs) for (let i = 0; i < ch.length; i++) peak = Math.max(peak, Math.abs(ch[i]));
    this.counts.chunks++;
    if (peak > LOUD) this.counts.loudChunks++;
  }

  // ---------------------------------------------------------------- frames
  private pictureOf(run: Run, id: string): Picture | null {
    const have = run.pictures.get(id);
    if (have !== undefined) return have === "loading" ? null : have;
    run.pictures.set(id, "loading");
    this.bank().picture(id, 0, run.size[1]).then(
      (got) => {
        if (run === this.run) run.pictures.set(id, got);
      },
      () => {
        if (run === this.run) run.pictures.set(id, null);
      },
    );
    return null;
  }

  private draw(run: Run, t: number): void {
    const frame = frameAt(run.p, t, run.size);
    const seen = new Set<string>();
    const pictures = new Map<Layer, Picture | null>();
    let missing = false;
    for (const pl of frame.layers) {
      const l = pl.layer;
      let got: Picture | null;
      if (l.kind === "picture") got = this.pictureOf(run, l.sourceId);
      else {
        seen.add(l.itemId);
        let s = run.streams.get(l.itemId);
        if (!s) {
          const id = l.sourceId;
          const height = run.size[1];
          s = { stream: new FrameStream<Picture>((from) => this.bank().frames(id, from, height)), last: null };
          run.streams.set(l.itemId, s);
        }
        const now = s.stream.at(l.time);
        if (now) s.last = now;
        got = s.last; // briefly null after a reopen: keep the frame we had
      }
      if (!got) missing = true;
      pictures.set(l, got);
    }
    for (const [id, s] of run.streams) {
      if (seen.has(id)) continue;
      s.stream.close();
      run.streams.delete(id);
    }
    if (missing && !run.painted && performance.now() - run.began < FIRST_FRAME_MS) return;
    if (this.breakDraw) {
      this.breakDraw = false;
      throw new Error("A frame broken on purpose (test)");
    }
    const { canvas, ctx } = run.off;
    drawFrame(ctx, frame, (l) => pictures.get(l) ?? null, surfaces);
    drawText(ctx, textAt(run.p, t, run.size, hasFont), run.size[1] / outputSize(run.p)[1]);
    run.painted = true;
    this.counts.frames++;
    this.on.frame((target, size) => target.drawImage(canvas, 0, 0, size[0], size[1]));
  }
}
