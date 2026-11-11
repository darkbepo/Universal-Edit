// Orbit camera for the 3D viewer, a line-by-line port of the desktop's umc/editor3d/camera.py
// (Z is up; yaw turns around Z, 0° = looking from +X; pitch 90° = straight down). No DOM.

export type Vec3 = [number, number, number];
export const FOV = 35; // vertical, degrees
const PITCH_LIMIT = 90;
export const VIEWS: Record<string, [number, number]> = {
  front: [-90, 0],
  back: [90, 0],
  left: [180, 0],
  right: [0, 0],
  top: [-90, 90],
  bottom: [-90, -90],
  iso: [-45, 35.264],
};

const rad = (d: number) => (d * Math.PI) / 180;
const add = (a: Vec3, b: Vec3, s = 1): Vec3 => [a[0] + b[0] * s, a[1] + b[1] * s, a[2] + b[2] * s];
const cross = (a: Vec3, b: Vec3): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];

export class OrbitCamera {
  target: Vec3 = [0, 0, 0];
  distance = 100;
  yaw = VIEWS.iso[0];
  pitch = VIEWS.iso[1];
  radius = 50;

  direction(): Vec3 {
    const y = rad(this.yaw), p = rad(this.pitch);
    return [Math.cos(p) * Math.cos(y), Math.cos(p) * Math.sin(y), Math.sin(p)];
  }

  up(): Vec3 {
    const y = rad(this.yaw), p = rad(this.pitch);
    return [-Math.sin(p) * Math.cos(y), -Math.sin(p) * Math.sin(y), Math.cos(p)];
  }

  right(): Vec3 {
    const d = this.direction();
    return cross([-d[0], -d[1], -d[2]], this.up());
  }

  eye(): Vec3 {
    return add(this.target, this.direction(), this.distance);
  }

  clipPlanes(): [number, number] {
    const near = Math.max(this.distance - this.radius * 4, this.distance * 0.01, 1e-3);
    return [near, this.distance + this.radius * 4];
  }

  orbit(dYaw: number, dPitch: number): void {
    this.yaw = ((((this.yaw + dYaw + 180) % 360) + 360) % 360) - 180;
    this.pitch = Math.max(-PITCH_LIMIT, Math.min(PITCH_LIMIT, this.pitch + dPitch));
  }

  pan(dxPx: number, dyPx: number, viewportH: number): void {
    const perPx = (2 * this.distance * Math.tan(rad(FOV) / 2)) / Math.max(viewportH, 1);
    this.target = add(add(this.target, this.right(), -dxPx * perPx), this.up(), dyPx * perPx);
  }

  zoom(factor: number): void {
    this.distance = Math.max(this.radius * 0.05, Math.min(this.radius * 50, this.distance * factor));
  }

  setView(name: string): void {
    [this.yaw, this.pitch] = VIEWS[name];
  }

  fit(lo: Vec3, hi: Vec3, aspect = 1): void {
    this.target = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];
    this.radius = Math.max(Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) / 2, 1e-3);
    let half = rad(FOV) / 2;
    if (aspect < 1) half = Math.atan(Math.tan(half) * aspect);
    this.distance = (this.radius / Math.sin(half)) * 1.05;
  }
}
