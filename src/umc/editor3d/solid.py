"""Logos joined into the model as one watertight body (boolean union), or cut into it
(difference), with manifold3d (Apache-2.0). Qt-free; the website does the same with the
manifold-3d wasm build in its worker (web/src/editor3d/solid.ts)."""

from __future__ import annotations

import numpy as np

from umc.editor3d.meshcheck import weld

Part = tuple[np.ndarray, np.ndarray]  # (vertices (V, 3) mm, faces (F, 3))


class SolidError(ValueError):
    """The parts couldn't be joined (a part isn't closed, or the engine failed)."""


def available() -> bool:
    try:
        import manifold3d  # noqa: F401
    except ImportError:
        return False
    return True


def _manifold(part: Part, what: str):
    import manifold3d as m3

    v, f = weld(*part)
    if not len(f):
        raise SolidError(f"{what} has no triangles.")
    mesh = m3.Mesh64(np.ascontiguousarray(v, np.float64), np.ascontiguousarray(f, np.uint64))
    solid = m3.Manifold(mesh)
    if solid.status() != m3.Error.NoError:
        raise SolidError(f"{what} isn't a closed solid.")
    return solid


def combine(base: Part | None, raised: list[Part], engraved: list[Part]) -> Part:
    """The model with the raised parts joined on and the engraved ones cut out, as one
    closed mesh. Raises SolidError in plain words."""
    try:
        import manifold3d as m3
    except ImportError as exc:  # pragma: no cover - a build without it
        raise SolidError("The merge engine isn't installed.") from exc
    solids = []
    if base is not None:
        solids.append(_manifold(base, "The model"))
    solids += [_manifold(p, "A logo") for p in raised]
    if not solids:
        raise SolidError("There is nothing to cut the engraving into.")
    body = m3.Manifold.batch_boolean(solids, m3.OpType.Add) if len(solids) > 1 else solids[0]
    if engraved:
        if base is None:
            raise SolidError("There is nothing to cut the engraving into.")
        cutters = [_manifold(p, "An engraved logo") for p in engraved]
        body = m3.Manifold.batch_boolean([body, *cutters], m3.OpType.Subtract)
    if body.status() != m3.Error.NoError or body.is_empty():
        raise SolidError("Joining the parts failed.")
    out = body.to_mesh64()
    v = np.asarray(out.vert_properties, np.float64)[:, :3]
    f = np.asarray(out.tri_verts, np.int64).reshape(-1, 3)
    return np.ascontiguousarray(v), f
