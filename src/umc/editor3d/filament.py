"""A rough filament estimate for a watertight model (Qt-free; port:
web/src/editor3d/filament.ts): solid outer walls ``SHELL_MM`` thick, the inside at the infill."""

from __future__ import annotations

import math

# g / cm³
DENSITY = {"PLA": 1.24, "PETG": 1.27, "ABS": 1.04, "ASA": 1.07, "TPU": 1.21, "Nylon": 1.14}
SHELL_MM = 0.8  # about two 0.4 mm perimeters / top and bottom layers
DIAMETER_MM = 1.75
DEFAULT_INFILL = 0.15


def estimate(
    volume_mm3: float, area_mm2: float, material: str = "PLA", infill: float = DEFAULT_INFILL
) -> dict[str, float]:
    """{"grams", "metres", "solid_grams"} — "grams" / "metres" at the infill."""
    density = DENSITY[material]
    shell = min(volume_mm3, area_mm2 * SHELL_MM)
    used = shell + (volume_mm3 - shell) * max(0.0, min(1.0, infill))
    strand = math.pi * (DIAMETER_MM / 2) ** 2  # mm² of filament cross-section
    return {
        "grams": used / 1000 * density,
        "metres": used / strand / 1000,
        "solid_grams": volume_mm3 / 1000 * density,
    }


def rows(volume_mm3: float, area_mm2: float, material: str, infill: float) -> list[tuple[str, str]]:
    e = estimate(volume_mm3, area_mm2, material, infill)
    return [
        (
            f"Filament ({material}, {round(infill * 100)} % infill)",
            f"≈ {e['grams']:.0f} g · {e['metres']:.1f} m",
        ),
        ("Printed solid", f"≈ {e['solid_grams']:.0f} g"),
    ]
