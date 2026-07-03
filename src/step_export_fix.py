"""
step_export_fix.py

WORKAROUND for a build123d export_step() bug.

THE BUG:
  build123d's import_step() returns a Compound whose .parent attribute
  is a hidden outer wrapper -- NOT None, even though every other property
  (.children, .label, etc.) makes it look like a root node.

  build123d's export_step() -> _create_xde() walks the tree with
  anytree's PreOrderIter and checks getattr(node, "parent", None) on
  every node. Because the "root" returned by import_step() has a parent,
  _create_xde() treats it as a child needing a parent label that was
  never registered -- every node is silently skipped, producing an empty
  XCAF document. The STEP writer reports IFSelect_RetVoid with no error.

THE FIX:
  Sever the spurious parent before exporting:
    assembly.parent = None

  This module is a drop-in replacement for build123d's own export_step()
  with exactly this one-line fix applied first. Everything else (color
  modes, layer modes, STEP headers) is the real build123d exporter.

USAGE:
  from step_export_fix import export_step
  # Use exactly like build123d's export_step() -- same signature.
  # If this is ever fixed upstream, switch back to:
  # from build123d import export_step
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
