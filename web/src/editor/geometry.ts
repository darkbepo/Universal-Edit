// Integer rectangles; x1 / y1 are exclusive. Port of src/umc/editor/geometry.py.

export class Rect {
  constructor(
    readonly x0: number,
    readonly y0: number,
    readonly x1: number,
    readonly y1: number,
  ) {}

  static fromPoints(x0: number, y0: number, x1: number, y1: number): Rect {
    return new Rect(Math.floor(Math.min(x0, x1)), Math.floor(Math.min(y0, y1)), Math.ceil(Math.max(x0, x1)), Math.ceil(Math.max(y0, y1)));
  }

  /** The pixels a circle of `radius` around (x, y) can touch. */
  static around(x: number, y: number, radius: number): Rect {
    return new Rect(Math.floor(x - radius) - 1, Math.floor(y - radius) - 1, Math.ceil(x + radius) + 1, Math.ceil(y + radius) + 1);
  }

  get width(): number {
    return Math.max(0, this.x1 - this.x0);
  }

  get height(): number {
    return Math.max(0, this.y1 - this.y0);
  }

  get empty(): boolean {
    return this.width === 0 || this.height === 0;
  }

  union(o: Rect | null | undefined): Rect {
    if (!o || o.empty) return this;
    if (this.empty) return o;
    return new Rect(Math.min(this.x0, o.x0), Math.min(this.y0, o.y0), Math.max(this.x1, o.x1), Math.max(this.y1, o.y1));
  }

  intersect(o: Rect): Rect {
    const r = new Rect(Math.max(this.x0, o.x0), Math.max(this.y0, o.y0), Math.min(this.x1, o.x1), Math.min(this.y1, o.y1));
    return r.empty ? EMPTY : r;
  }

  offset(dx: number, dy: number): Rect {
    return new Rect(this.x0 + dx, this.y0 + dy, this.x1 + dx, this.y1 + dy);
  }

  contains(x: number, y: number): boolean {
    return this.x0 <= x && x < this.x1 && this.y0 <= y && y < this.y1;
  }

  equals(o: Rect | null | undefined): boolean {
    return !!o && o.x0 === this.x0 && o.y0 === this.y0 && o.x1 === this.x1 && o.y1 === this.y1;
  }
}

export const EMPTY = new Rect(0, 0, 0, 0);
