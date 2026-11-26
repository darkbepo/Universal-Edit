// A rough filament estimate (port of the desktop's umc/editor3d/filament.py).

export const DENSITY: Record<string, number> = { PLA: 1.24, PETG: 1.27, ABS: 1.04, ASA: 1.07, TPU: 1.21, Nylon: 1.14 };
export const SHELL_MM = 0.8;
export const DIAMETER_MM = 1.75;
export const DEFAULT_INFILL = 0.15;

export function estimate(volumeMm3: number, areaMm2: number, material = "PLA", infill = DEFAULT_INFILL): { grams: number; metres: number; solid_grams: number } {
  const density = DENSITY[material];
  const shell = Math.min(volumeMm3, areaMm2 * SHELL_MM);
  const used = shell + (volumeMm3 - shell) * Math.max(0, Math.min(1, infill));
  const strand = Math.PI * (DIAMETER_MM / 2) ** 2;
  return { grams: (used / 1000) * density, metres: used / strand / 1000, solid_grams: (volumeMm3 / 1000) * density };
}

export function rows(volumeMm3: number, areaMm2: number, material: string, infill: number): [string, string][] {
  const e = estimate(volumeMm3, areaMm2, material, infill);
  return [
    [`Filament (${material}, ${Math.round(infill * 100)} % infill)`, `≈ ${e.grams.toFixed(0)} g · ${e.metres.toFixed(1)} m`],
    ["Printed solid", `≈ ${e.solid_grams.toFixed(0)} g`],
  ];
}
