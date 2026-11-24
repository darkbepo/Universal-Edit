# Vector editor (both apps) — design

Date: 2026-11-24. Roadmap item 16.1.

## Why

SVG drawings (logos, icons, diagrams) can be converted to PDF / PNG / EPS, but changing a colour,
moving a shape or fixing a word means a second program. The start screen already shows a
"Vector editor — Coming soon" tile; this fills it.

## Goals

- **Where:** the hub tile opens an empty drawing ("New drawing": size presets); Edit opens one SVG.
  The code editor stays the second choice under Edit's ▾ (SVG is text too).
- **Layout:** like the image editor: tool rail on the left (Select, Node, Rectangle, Ellipse, Line,
  Pen, Text), canvas in the middle (zoom / pan, the page drawn as a white sheet), on the right a
  **Layers** list (the drawing's elements, groups fold open) and an **Appearance** panel for the
  selection (fill, stroke, stroke width, opacity, and the position / size boxes).
- **Editing:** select (click, Shift+click, rubber band; Tab cycles), move (drag, arrows, Shift = 10),
  resize with 8 handles (Shift keeps the ratio), delete, duplicate, copy / paste, group / ungroup,
  bring forward / send backward / to front / to back, rename (the element's `id`), hide / lock.
  Node tool: drag a path's points and curve handles; double-click a segment adds a point; Delete
  removes one. Draw rectangles, ellipses, lines, Bézier paths (Pen: click = corner, drag = curve,
  Enter / double-click ends, click the first point closes) and text (click, type).
  Undo / redo for all of it (one step per drag / typing run).
- **Saving:** Save writes SVG; untouched files save byte for byte, and only the elements that
  changed are rewritten — everything else (comments, `<defs>`, Inkscape attributes, unknown
  elements, the original indentation) is kept as written. Export to PDF / PNG / … goes through
  the vector converter (Save as).
- Same model, behaviour and shortcuts in both apps; shared cases checked by both test suites.

## Non-goals (first version)

- Gradients / patterns / filters editing (kept and drawn, not edited), boolean path operations,
  text on a path, embedded raster editing, CSS `<style>` rules editing (kept, and the canvas uses
  them as far as the renderer does), multiple pages.

## Model (`umc/vectoredit/` ↔ `web/src/vectoredit/`, line-by-line ports, no Qt / DOM)

- `xmlsrc`: our own XML reader that keeps every node's source text (start tag, children, end
  tag spans). The writer re-emits untouched nodes as their original text and rebuilds only
  changed start tags (attribute order kept, new attributes appended, `"` quoting, `&amp;`
  `&lt;` `&quot;` escaping). Same bytes in both apps, so the shared cases compare whole files.
  Refuses DOCTYPEs with entity declarations (billion laughs) and documents nested past 256.
- `geom`: 2D affine `Matrix` (`parse_transform` for matrix / translate / scale / rotate / skewX /
  skewY lists, `format`), `Box`, number formatting (`num`: at most 3 decimals, no trailing zeros,
  no `-0` — identical in both apps).
- `pathdata`: SVG path parsing (all commands, implicit repeats, relative / absolute, arcs) into
  absolute segments (M, L, C, Q, A, Z), writing back (absolute, `num`), arc → cubic for bounds,
  bounding boxes of curves (true extrema, not control points), node list for the Node tool.
- `shapes`: what the editor understands of an element: `rect`, `circle`, `ellipse`, `line`,
  `polyline`, `polygon`, `path`, `text` (one line, its `tspan`s flattened for editing), `g`,
  `use` / `image` (move only). `bounds(el)` in the parent's coordinates (own `transform` applied).
  Style = presentation attributes and the `style` attribute: reading looks at both (style wins),
  writing updates the place the value came from (else the `style` attribute if the element has
  one, else an attribute).
- `doc`: `VectorDoc` (the source tree, the canvas size from `width` / `height` / `viewBox`, the
  element list with stable ids = index paths) and pure edits: `move`, `resize` (rewrites the
  geometry attributes of simple shapes; paths get their points rewritten; groups / `use` /
  `image` / text get a `translate` / `scale` folded into their `transform`), `set_style`,
  `delete`, `duplicate`, `reorder`, `group`, `ungroup` (pushes the group transform down), `add`,
  `set_text`, `set_nodes`, `rename`, `set_hidden` (`display="none"`), lock (`umc:locked`
  isn't written: locks live in the page only).
- `history`: snapshot undo (documents are immutable; typing in one text = one step).
- `io`: bytes ↔ doc (UTF-8 / UTF-16 with BOM; a `.svgz` is gunzipped and saved gzipped again),
  `new_document(width, height)`. Limits: 20 MB, 100 000 elements.
- Shared cases `tests/fixtures/vectoredit_cases.json` (`python scripts/make_vectoredit_cases.py`,
  only on purpose).

## Drawing

Desktop: Qt's `QSvgRenderer` draws the whole document (re-rendered after an edit; elements are
drawn from cached pictures so a drag only moves one), selection outlines / handles are painted
by the canvas. Web: the SVG source goes into an `<img>` (blob URL) for the page and the selection
overlay is an SVG layer on top; during a drag only the moved element's `transform` changes.

## Steps (one plan each, ship gate after each)

1. Model: `xmlsrc`, `geom`, `pathdata` (both apps) + shared cases.
2. Model: `shapes`, `doc` edits, `history`, `io` (both apps) + shared cases.
3. Desktop page: add-on "vector" (`umc.gui.vectoreditor`), Edit + hub tile, self-test step.
4. Website page `web/src/ui/vectoreditor/`, loaded on demand; e2e; tile available in both apps.
