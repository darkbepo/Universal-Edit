// Reads a clip's frames ahead of the play head. DOM-free: it wraps any async
// iterator of timed frames (Mediabunny's CanvasSink.canvases(from) in the page).

export interface Timed<T> {
  timestamp: number;
  duration: number;
  value: T;
}

const BEHIND = 1; // seconds behind the shown frame that count as a seek
const AHEAD_JUMP = 2; // seconds past the read position that count as a seek

export class FrameStream<T> {
  private iterator: AsyncIterator<Timed<T>> | null = null;
  private buffer: Timed<T>[] = [];
  private shown: Timed<T> | null = null;
  private generation = 0;
  private reading = false;
  private finished = false; // iterator ended or threw: stop reading until a reopen
  private closed = false;
  private readPos = 0;
  private wanted = 0;

  constructor(
    private readonly open: (from: number) => AsyncIterator<Timed<T>>,
    private readonly ahead = 3,
  ) {}

  at(t: number): T | null {
    if (this.closed) return null;
    this.wanted = t;
    const behind = this.shown !== null && t < this.shown.timestamp - BEHIND;
    if (this.iterator === null || behind || t > this.readPos + AHEAD_JUMP) this.reopen(t);

    // Promote the newest buffered frame at or before t; drop older ones.
    let k = 0;
    while (k < this.buffer.length && this.buffer[k].timestamp <= t) k++;
    if (k > 0) {
      this.shown = this.buffer[k - 1];
      this.buffer.splice(0, k);
    }
    this.pump();
    return this.shown ? this.shown.value : null;
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.generation++;
    this.release();
    this.buffer = [];
    this.shown = null;
  }

  private release(): void {
    const it = this.iterator;
    this.iterator = null;
    if (it && it.return) {
      try {
        Promise.resolve(it.return()).catch(() => {});
      } catch {
        // ignore
      }
    }
  }

  private reopen(t: number): void {
    this.generation++;
    this.release();
    this.buffer = [];
    this.shown = null;
    this.reading = false;
    this.finished = false;
    this.readPos = t;
    try {
      this.iterator = this.open(t);
    } catch {
      this.iterator = null;
      this.finished = true;
    }
  }

  private pump(): void {
    if (this.closed || this.reading || this.finished || !this.iterator) return;
    let past = 0;
    for (const f of this.buffer) if (f.timestamp > this.wanted) past++;
    if (past >= this.ahead) return;
    this.reading = true;
    const gen = this.generation;
    let next: Promise<IteratorResult<Timed<T>>>;
    try {
      next = Promise.resolve(this.iterator.next());
    } catch {
      this.reading = false;
      this.finished = true;
      return;
    }
    next.then(
      (r) => {
        if (gen !== this.generation) return;
        this.reading = false;
        if (r.done) {
          this.finished = true;
          return;
        }
        this.buffer.push(r.value);
        this.readPos = r.value.timestamp;
        this.pump();
      },
      () => {
        if (gen !== this.generation) return;
        this.reading = false;
        this.finished = true;
      },
    );
  }
}
