"""3D models for printing: STL ↔ OBJ ↔ 3MF ↔ PLY ↔ GLB, and STEP / IGES (CAD) → mesh.

Everything is normalised to **millimetres** internally (what slicers expect). STEP comes back
from OpenCASCADE in metres; IGES in the unit its header declares; GLB is metres by spec;
STL / OBJ / PLY / OFF carry no unit, so the "Source units" setting decides (mm by default).
"""

from __future__ import annotations

import contextlib
import io
from dataclasses import dataclass
from pathlib import Path

from umc.converters.base import Converter
from umc.core.detect import DetectedFile, MediaFamily
from umc.core.errors import ConversionError
from umc.core.job import ConversionJob, unique_path
from umc.core.options import opt

TARGETS = {"stl": "STL", "3mf": "3MF", "obj": "OBJ", "ply": "PLY", "glb": "GLB"}
UNIT_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "inch": 25.4, "ft": 304.8}
# IGES global-section unit flag → mm
IGES_UNITS = {
    1: 25.4,
    2: 1.0,
    4: 304.8,
    5: 1609344.0,
    6: 1000.0,
    7: 1e6,
    8: 0.0254,
    9: 1e-3,
    10: 10.0,
    11: 1e-6,
}
DETAIL = {
    "coarse": (0.1, 1.0),
    "normal": (0.02, 0.5),
    "fine": (0.005, 0.2),
}  # (linear mm, angular rad)


@dataclass
class ModelOptions:
    units: str = opt("auto", "Source units", choices=[
        ("auto", "Automatic (mm for STL/OBJ/PLY/3MF)"), ("mm", "Millimetres"),
        ("cm", "Centimetres"), ("m", "Metres"), ("inch", "Inches"), ("ft", "Feet")],
        help="STL, OBJ and PLY files don't store a unit. If your print comes out 25× too small, "
        "the model was made in inches.")  # fmt: skip
    scale_percent: float = opt(100.0, "Scale", kind="float", min=0.1, max=100000, decimals=1,
                               unit="%")  # fmt: skip
    center_on_bed: bool = opt(
        True,
        "Centre on the print bed",
        help="Moves the model to the middle (X/Y = 0) and onto the bed (Z = 0).",
    )
    repair: bool = opt(True, "Repair mesh",
                       help="Merges duplicate points, removes broken triangles, fixes normals and "
                       "fills small holes.")  # fmt: skip
    merge_parts: bool = opt(False, "Merge all parts into one object", targets=("3mf", "glb"))
    stl_format: str = opt("binary", "STL format", choices=[
        ("binary", "Binary (smaller)"), ("ascii", "ASCII (text)")], targets=("stl",))  # fmt: skip
    detail: str = opt("normal", "Detail for STEP / IGES", choices=[
        ("coarse", "Coarse (small file)"), ("normal", "Normal"), ("fine", "Fine (smooth curves)")],
        help="How finely curved CAD surfaces are turned into triangles.")  # fmt: skip


def iges_unit_mm(data: bytes) -> float:
    """Read the unit flag (global parameter 14) from an IGES file's global section."""
    text = data.decode("latin-1")
    lines = text.splitlines()
    glob = "".join(line[:72] for line in lines if len(line) >= 73 and line[72] == "G")
    if not glob:
        return 1.0
    delim = glob[2] if glob.startswith("1H") else ","
    params, i, cur = [], 0, ""
    while i < len(glob):
        ch = glob[i]
        if ch.isdigit() and not cur.strip():
            j = i
            while j < len(glob) and glob[j].isdigit():
                j += 1
            if j < len(glob) and glob[j] == "H":  # Hollerith string nH...
                n = int(glob[i:j])
                cur = glob[j + 1 : j + 1 + n]
                i = j + 1 + n
                continue
        if ch in (delim, ";"):
            params.append(cur.strip())
            cur = ""
        else:
            cur += ch
        i += 1
    try:
        return IGES_UNITS.get(int(params[13]), 1.0)
    except (IndexError, ValueError):
        return 1.0


def load_model(path: Path, ext: str, o: ModelOptions):
    """Return a trimesh Scene in millimetres."""
    import trimesh

    if ext in ("step", "iges"):
        import cascadio

        data = path.read_bytes()
        lin, ang = DETAIL[o.detail]
        try:
            glb = cascadio.load(data, file_type=ext, tol_linear=lin, tol_angular=ang)
        except Exception as exc:
            raise ConversionError(
                f"Couldn't read “{path.name}” as a {ext.upper()} CAD file."
            ) from exc
        scene = trimesh.load(io.BytesIO(glb), file_type="glb", force="scene")
        to_mm = 1000.0 if ext == "step" else iges_unit_mm(data)
    else:
        try:
            scene = trimesh.load(path, file_type=ext, force="scene")
        except Exception as exc:
            raise ConversionError(f"Couldn't read “{path.name}” as a 3D model.") from exc
        to_mm = 1000.0 if ext == "glb" else 1.0
    if o.units != "auto":
        to_mm = UNIT_MM[o.units]
    if not scene.geometry:
        raise ConversionError(f"“{path.name}” doesn't contain any 3D geometry.")
    scene.apply_scale(to_mm * o.scale_percent / 100.0)
    return scene


def _repair(mesh) -> None:
    mesh.merge_vertices()
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.update_faces(mesh.unique_faces())
    mesh.remove_unreferenced_vertices()
    mesh.fill_holes()
    # fix_normals needs SciPy, which we don't bundle (slicers recompute normals anyway).
    with contextlib.suppress(ImportError):
        mesh.fix_normals()


def save_model(scene, ext: str, o: ModelOptions) -> bytes:
    import trimesh

    single = ext in ("stl", "obj", "ply") or o.merge_parts
    if single:
        mesh = (
            scene.to_geometry() if hasattr(scene, "to_geometry") else scene.dump(concatenate=True)
        )
        if o.repair:
            _repair(mesh)
        out = trimesh.Scene(mesh)
    else:
        out = scene
        if o.repair:
            for g in out.geometry.values():
                if isinstance(g, trimesh.Trimesh):
                    _repair(g)
    if o.center_on_bed:
        (x0, y0, z0), (x1, y1, _z1) = out.bounds
        out.apply_translation([-(x0 + x1) / 2, -(y0 + y1) / 2, -z0])
    if ext == "glb":
        out.apply_scale(0.001)  # glTF is metres
    if ext == "stl":
        mesh = out.to_geometry() if hasattr(out, "to_geometry") else out.dump(concatenate=True)
        data = mesh.export(file_type="stl_ascii" if o.stl_format == "ascii" else "stl")
        return data.encode() if isinstance(data, str) else data
    data = out.export(file_type=ext)
    return data.encode() if isinstance(data, str) else data


class ModelConverter(Converter):
    family = MediaFamily.MODEL
    options_cls = ModelOptions

    def targets(self, files: list[DetectedFile]):
        single = files[0].ext if len(files) == 1 else None
        return [self.target(ext, label, ext, "3D model") for ext, label in TARGETS.items()
                if ext != single]  # fmt: skip

    def run(self, job: ConversionJob) -> list[Path]:
        o: ModelOptions = job.options
        ext = job.target.ext
        outputs = []
        n = len(job.files)
        for i, f in enumerate(job.files):
            job.check_cancelled()
            job.report(i / n, f"Converting {f.name} ({i + 1}/{n})")
            scene = load_model(f.path, f.ext, o)
            dest = unique_path(job.output_dir_for(f.path), f.path.stem, ext)
            dest.write_bytes(save_model(scene, ext, o))
            outputs.append(dest)
        job.report(1.0, "Done")
        return outputs
