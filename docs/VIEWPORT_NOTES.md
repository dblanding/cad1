# Viewport Smoke Test

## What this answers

One question only: **does OCCT's native 3D viewer open inside a
PySide6 widget on your machine, render a box, and respond to mouse
orbit/pan/zoom?**

No tree, no menus, no STEP loading. Deliberately the smallest
possible test of the riskiest unknown in the GUI plan.

## Honesty about what's been verified

This has NOT been run. This sandbox has no display, no GPU, no
windowing system — there is no way to execute or screenshot GUI code
here. The code is written carefully against the documented OCCT
Qt-embedding pattern (the same `Aspect_DisplayConnection` →
`OpenGl_GraphicDriver` → `V3d_Viewer` → `V3d_View` →
`AIS_InteractiveContext` chain used by classic PythonOCC's
`qtDisplay` module and referenced in OCCT's own forum examples), but
"matches the documented pattern" and "actually works on your specific
Linux/Qt/OpenGL driver combination" are different claims. Treat this
exactly like the first STEP round-trip attempt: budget time for it
not working on the first try, and that won't mean something is wrong
with the approach.

## Setup

```bash
uv add pyside6
```

(`build123d` and `OCP` should already be installed from the earlier
STEP work — this reuses that environment.)

## Running it

```bash
uv run viewport_smoke_test.py
```

## What "success" looks like

A window opens, titled "Viewport smoke test...", showing a gray
background with a small axis triadron in the corner (like the one in
your CAD Assistant screenshot) and a white-ish box roughly centered.
Left-drag rotates it, middle-drag pans, scroll wheel zooms.

## What to do if it fails — and how to report back usefully

Given how the STEP investigation went, expect this to need at least
one round of debugging. A few specific things to capture if it
doesn't work cleanly, since they narrow down the cause fast:

1. **Does a window open at all, even blank/black?**
   - If NO window appears and the process exits immediately: copy
     the full terminal output, including any traceback. This is
     likely an import error or an exception during
     `OcctViewportWidget.__init__`.
   - If a window opens but crashes shortly after (no traceback, just
     the process dying): this is the classic native-window-handle
     failure mode mentioned in OCCT's own forums. Note whether it
     crashes immediately on open, or specifically when the box gets
     displayed (the `QTimer.singleShot` callback) -- that timing
     tells us whether the problem is window creation or shape
     display.

2. **Window opens and stays open, but shows nothing (blank gray, no
   box)?**
   - Try resizing the window -- sometimes a missing initial
     `MustBeResized()`/`FitAll()` call means the view just needs a
     resize event to kick it into rendering.
   - Note whether the gray background itself is visible (proves the
     OpenGL context + window binding works) vs. the whole window
     being black/garbage (proves it doesn't).

3. **Window shows the box, but mouse interaction does nothing?**
   - Note which specific interaction fails -- rotate, pan, or zoom --
     since they're three independent code paths
     (`StartRotation`/`Rotation`, `Pan`, `SetZoom`).

4. **Wayland-specific note:** this script uses `Xw_Window`, the
   X11/Linux binding. If you're running a Wayland session
   specifically (not X11), this usually still works via XWayland
   transparently, but if you hit a window-creation failure
   specifically, that's worth checking: run `echo $XDG_SESSION_TYPE`
   in your terminal to see which one you're on.

Whatever happens, paste the terminal output (or a screenshot/photo of
what the window shows, if it's a rendering rather than a crash issue)
and we'll debug it the same way we worked through the STEP export
bug -- one isolated hypothesis at a time, verified against your
actual results rather than assumed.

## Once this works

This script's `OcctViewportWidget` class is the seed of the real
viewport widget. Next layers, in order:

1. Harden this widget (clean up resource lifecycle, handle window
   resize/close properly).
2. Wire it to `step_assembly_poc.py`'s `load_assembly()` so a real
   STEP file's shapes display, not just a synthetic box.
3. Add the `QTreeWidget` assembly tree alongside it, wired to
   `remove_part()`/`add_part()`.
4. Add per-row show/hide checkboxes that call
   `context.Erase()`/`context.Display()` on the corresponding
   `AIS_Shape`.

Each of those is its own small, verifiable step -- same philosophy as
the STEP work.
