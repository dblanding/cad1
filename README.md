# cad1 — DIY CAD app (working name)

A from-scratch CAD application built on `build123d`/OCP (OpenCascade),
patterned after HP's dynamic/direct modeling lineage (ME-30 →
SolidDesigner → CoCreate), with a workplane → sketch → extrude/revolve
→ modify workflow and a native assembly tree + 3D viewport, in the
spirit of the original [KodaCAD](https://dblanding.github.io/kodacad/).

## Status

- ✅ **STEP round-trip** (import assembly → inspect hierarchy → add/
  remove parts → export) — confirmed working on real-world files from
  multiple CAD systems. See `docs/STEP_NOTES.md` for the full story,
  including a real upstream `build123d` bug found and worked around.
- ✅ **GUI / 3D viewport** — native OCCT viewer embedded in PySide6:
  render, orbit/pan/zoom, click-to-select picking (face-level),
  assembly tree with show/hide and drag-and-drop reparenting,
  bidirectional selection sync between tree and viewport. See
  `docs/VIEWPORT_NOTES.md`.
- ✅ **Pose math foundation** (`src/pose.py`) — `PointRef`/`DirectionRef`
  picking-target resolution, `Plane`-based frame construction, one-shot
  `from_plane`/`to_plane` transform composition. Proven via self-test.
- 🚧 **Position/Mate-Align workflow, undo/redo, file format** — designed
  in conversation, not yet built. See `docs/DESIGN_BACKLOG.md`.

## Layout

```
src/        Core logic: STEP import/export, assembly tree manipulation,
            pose/positioning math. No GUI dependencies — usable from a
            script or a notebook.

gui/        Qt (PySide6) application code: 3D viewport, tree widget,
            menus. Imports from src/ for all the actual CAD logic.

step/       Sample/test STEP files (e.g. as1-oc-214.stp, the canonical
            OCCT sample assembly used throughout testing).

docs/       Detailed notes per area — diagnosis write-ups, setup
            instructions, known issues, and the running design
            backlog. Start with the file relevant to what you're
            touching; this top-level README is just an index.
docs/imgs/  Reference screenshots (e.g. HP/CoCreate UI patterns being
            used as design references) linked from the docs.

archive_diagnostics/
            Standalone debugging scripts from working through several
            real bugs (a build123d export bug, an assembly-position
            bug, shared-instance investigation). Not part of the app;
            kept as reference and as near-ready bug repros if any are
            worth filing upstream.
```

## Setup

```bash
uv sync          # installs everything from pyproject.toml / uv.lock
```

## Running things

```bash
uv run src/step_assembly_poc.py [path/to/assembly.step]
uv run gui/main_app.py [path/to/assembly.step]   # the real, merged app
```

## Docs index

- [`docs/STEP_NOTES.md`](docs/STEP_NOTES.md) — STEP import/export
  round-trip: setup, usage, the `import_step()` parent-bug diagnosis
  and fix, the shared-vs-copied-instances investigation, known edge
  cases to expect (repeated parts, units, large assemblies).
- [`docs/VIEWPORT_NOTES.md`](docs/VIEWPORT_NOTES.md) — embedding
  OCCT's native viewer in PySide6: setup, what "success" looks like,
  how to report a failure usefully.
- [`docs/DESIGN_BACKLOG.md`](docs/DESIGN_BACKLOG.md) — running list of
  designed-but-not-yet-built threads: the HP/CoCreate-style
  Position/Mate-Align workflow, copy-vs-share UI pattern, undo/redo
  (including an open, unverified question about whether it can even
  apply to our build123d-based data model), file storage format.
