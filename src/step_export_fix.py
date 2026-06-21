"""
step_export_fix.py

CONFIRMED ROOT CAUSE (found via a long elimination process -- see
README.md for the full diagnosis): build123d's import_step() returns
a Compound whose `.parent` is a synthetic, invisible outer wrapper --
NOT None, even though every other property (.children, .descendants,
.label) makes it look like a clean tree root.

build123d's real export_step() -> _create_xde() walks the shape tree
with anytree's PreOrderIter and checks `getattr(node, "parent", None)`
for every node, including the very first one. Because the "root" you
get back from import_step() isn't actually parentless, _create_xde()
treats it as a child needing a parent label that was never registered
-- every node gets silently skipped via a `continue` guard, producing
an empty-but-validly-constructed XCAF document. The writer then
reports IFSelect_RetVoid ("nothing done") with no exception and no
diagnostic message anywhere in the pipeline.

THE FIX is one line: sever that spurious parent relationship before
exporting. This module wraps the REAL, FULL-FEATURED build123d
export_step() -- not a stripped-down reimplementation -- so you keep
SetColorMode/SetLayerMode/SetNameMode, proper STEP headers, and every
other feature of the real exporter. The only change is the one-line
fix applied first.

Usage:
    from step_export_fix import export_step
    # use exactly like build123d's own export_step() everywhere

This is a drop-in replacement: same signature, same behavior, plus
the fix. If/when this is patched upstream in build123d (worth filing
as a GitHub issue -- see README.md), you can switch back to
`from build123d import export_step` directly with no other code
changes needed.
"""

from os import PathLike
from io import BytesIO
from datetime import datetime
from typing import BinaryIO

from build123d import Shape, Compound
from build123d.build_enums import Unit, PrecisionMode
from build123d import export_step as _real_export_step


def export_step(
    to_export: Shape,
    file_path: PathLike | str | bytes | BytesIO | BinaryIO,
    unit: Unit = Unit.MM,
    write_pcurves: bool = True,
    precision_mode: PrecisionMode = PrecisionMode.AVERAGE,
    *,
    timestamp: str | datetime | None = None,
) -> bool:
    """
    Drop-in replacement for build123d.export_step() that works around
    a confirmed bug affecting shapes returned by import_step().

    If `to_export` has a non-None .parent (the telltale sign of the
    bug -- a node that LOOKS like a root via .children/.descendants
    but isn't actually parentless), that parent relationship is
    severed before handing off to the real, unmodified
    build123d.export_step(). Shapes built fresh (never imported) are
    unaffected either way, since they never have this spurious parent
    to begin with.
    """
    if getattr(to_export, "parent", None) is not None:
        to_export.parent = None

    return _real_export_step(
        to_export,
        file_path,
        unit=unit,
        write_pcurves=write_pcurves,
        precision_mode=precision_mode,
        timestamp=timestamp,
    )


if __name__ == "__main__":
    # Self-test: import a STEP file, export it right back out through
    # the fix, confirm success. Same shape that fails through
    # build123d's raw export_step() should now succeed cleanly.
    import sys
    from pathlib import Path
    from build123d import import_step

    if len(sys.argv) < 2:
        print("Usage: step_export_fix.py <input.step>")
        sys.exit(0)

    step_path = Path(sys.argv[1])
    print(f"Importing {step_path} ...")
    reimported = import_step(str(step_path))
    print(f"reimported.parent before fix = {reimported.parent!r}")

    out_path = Path("step_export_fix_output.step")
    print(f"Exporting to {out_path} via the fixed export_step()...")
    ok = export_step(reimported, out_path)
    print(f"Success: {ok}")
    if ok:
        print(f"Wrote {out_path.resolve()}")
        print("Open it in a STEP viewer and confirm the assembly hierarchy,")
        print("part names, and geometry all came through intact.")
