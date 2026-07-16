# BasiCAD — DIY CAD app

A from-scratch 3D CAD application
* Built on `build123d`/OCP (OpenCascade)
* Patterned after HP's dynamic modeling lineage (ME-30 → SolidDesigner)
* Intended to meet the basic requirements in a typical CAD workflow:
    * Start a new session or load a previous one (saved in STEP format)
    * Import components in STEP format
    * Position them within an assembly structure
    * Create simple mounting plates & brackets
    * Save session in STEP format
* Part creation workflow: workplane → sketch → extrude/revolve → modify 
* Based on the original [KodaCAD](https://github.com/dblanding/kodacad)

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

## Usage:
* Run *main_app.py* in terminal (optional STEP file to open as argument):
    * `uv run gui/main_app.py` 
    * `uv run gui/main_app.py step/as1-oc-214.stp`

    
## Docs index

- [`docs/STEP_NOTES.md`](docs/STEP_NOTES.md) — STEP import/export
  round-trip: setup, usage, the `import_step()` parent-bug diagnosis
  and fix, the shared-vs-copied-instances investigation, known edge
  cases to expect (repeated parts, units, large assemblies).
- [`docs/VIEWPORT_NOTES.md`](docs/VIEWPORT_NOTES.md) — embedding
  OCCT's native viewer in PySide6: setup, what "success" looks like,
  how to report a failure usefully.
- [`docs/DESIGN_BACKLOG.md`](docs/DESIGN_BACKLOG.md) — running list of
  designed-but-not-yet-built threads (the HP/CoCreate-style
  Position/Mate-Align workflow, copy-vs-share UI pattern, undo/redo,
  file storage format) plus §5: a full account of picking → pose →
  move → export now proven end to end, including every real bug found
  and fixed along the way — worth checking before assuming something
  new is broken.
