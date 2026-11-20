// The still preview's requests (no DOM): the newest request wins, one still is made at a time,
// and while requests come fast (scrubbing) only the last one is made, once they pause for
// DEBOUNCE_MS. A run sees `isCurrent()` turn false as soon as a newer request (or `clear`) came,
// so a decode that finishes late never paints.
export const DEBOUNCE_MS = 40;

export interface StillTimer {
  set(fn: () => void, ms: number): unknown;
  clear(h: unknown): void;
  now(): number;
}

const realTimer: StillTimer = {
  set: (fn, ms) => setTimeout(fn, ms),
  clear: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
  now: () => performance.now(),
};

export class StillQueue<J> {
  /** Bumped by every request and by `clear`: a run is current while it still matches. */
  private generation = 0;
  private job: { value: J } | null = null;
  private running = false;
  private handle: unknown = null;
  private last = -Infinity;
  private waiters: (() => void)[] = [];

  constructor(
    private run: (job: J, isCurrent: () => boolean) => Promise<void>,
    private timer: StillTimer = realTimer,
  ) {}

  /** No request within DEBOUNCE_MS before → start now (or as soon as the running one ends);
   *  else (re)arm the timer. */
  request(job: J): void {
    this.generation++;
    this.job = { value: job };
    const now = this.timer.now();
    const fast = now - this.last < DEBOUNCE_MS;
    this.last = now;
    if (this.handle !== null) {
      this.timer.clear(this.handle);
      this.handle = null;
    }
    if (fast) {
      this.handle = this.timer.set(() => {
        this.handle = null;
        this.pump();
      }, DEBOUNCE_MS);
    } else this.pump();
  }

  /** Drop what's waiting; the running job (if any) is no longer current. */
  clear(): void {
    this.generation++;
    this.job = null;
    if (this.handle !== null) this.timer.clear(this.handle);
    this.handle = null;
    this.settle();
  }

  /** Resolves when nothing is waiting or running. */
  idle(): Promise<void> {
    if (this.quiet) return Promise.resolve();
    return new Promise((res) => this.waiters.push(res));
  }

  private get quiet(): boolean {
    return !this.running && this.job === null && this.handle === null;
  }

  private pump(): void {
    if (this.running || this.handle !== null || !this.job) {
      this.settle();
      return;
    }
    const job = this.job.value;
    this.job = null;
    const generation = this.generation;
    this.running = true;
    let done: Promise<void>;
    try {
      done = this.run(job, () => generation === this.generation);
    } catch (e) {
      done = Promise.reject(e);
    }
    done
      .catch(() => undefined) // a broken frame: the next request tries again
      .then(() => {
        this.running = false;
        this.pump();
      });
  }

  private settle(): void {
    if (!this.quiet) return;
    const waiters = this.waiters;
    this.waiters = [];
    for (const w of waiters) w();
  }
}
