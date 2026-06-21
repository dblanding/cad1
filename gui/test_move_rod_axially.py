"""
test_move_rod_axially.py

The first real, end-to-end test of the full chain that everything in
this project has been building toward: pick geometry -> resolve a
pose -> APPLY it to a real part -> export -> verify. Everything
before this has tested pieces in isolation (picking alone, pose math
alone, STEP export alone) -- this is the first time they're chained
together on a real assembly.

This mirrors a "Dynamic Move" / axial-translate operation: move the
rod along its own axis by a conspicuous, easy-to-verify distance, with
NO rotation change.

WHAT THIS DOES, step by step:
    1. Load as1-oc-214.stp via the proven load_assembly().
    2. Find the rod's semi-circular end edge (the SAME edge already
       proven, via real picking, to resolve correctly:
       circle_center=(190, 75, 60), circle_axis=(1,0,0)) -- but found
       PROGRAMMATICALLY here rather than via a GUI click, since this
       script has no viewport.
    3. Move the rod IN PLACE via rod.move(delta) -- a RELATIVE change
       to THIS object (confirmed via build123d's documented set of
       four location-changing methods: locate()=absolute/this,
       located()=absolute/copy, move()=relative/this,
       moved()=relative/copy). NO new object is created, NO tree
       restructuring happens -- 'rod' remains the exact same Python
       object, in the exact same place in the tree, just relocated.

       (Two earlier, WRONG attempts, both real bugs found via actual
       testing, not hypothetical: first, rod.moved(move) + remove_node()/
       add_node() tree surgery -- unnecessary complexity that also
       turned out to corrupt rod-assembly into STEP header text on
       export. Second, rod.location.position += delta -- this silently
       did NOTHING, because .location returns a DETACHED COPY of the
       shape's location (confirmed via build123d's docs), not a live
       reference -- mutating the copy never touches the original shape.
       rod.move() is the actual, correct, documented method for "relative
       change of THIS object," which is exactly what's needed here.)
       more accurate match for what "moving one part" should mean.)
    4. Export via the proven step_export_fix.export_step().
    5. Print before/after positions so the result can be sanity-
       checked numerically, in addition to visually in CAD Assistant.

Usage:
    uv run gui/test_move_rod_axially.py step/as1-oc-214.stp
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from step_assembly_poc import load_assembly, print_tree  # noqa: E402
from step_export_fix import export_step  # noqa: E402
from pose import PointRef, DirectionRef  # noqa: E402

from build123d import GeomType


def find_rod_axis_edge(rod):
    """
    Find the SAME semi-circular end edge already proven, via real
    picking in main_app.py, to resolve correctly through pose.py's
    circle-fit fallback (circle_center=(190,75,60),
    circle_axis=(1,0,0)). Found programmatically here since this
    script has no viewport -- searches rod's edges for one that
    resolves via circle_center/circle_axis (the fit fallback handles
    BSPLINE-classified-but-actually-circular edges, confirmed
    necessary for this exact file).
    """
    for edge in rod.edges():
        try:
            center = PointRef(kind="circle_center", shape=edge).resolve()
            axis = DirectionRef(kind="circle_axis", shape=edge).resolve()
            return edge, center, axis
        except ValueError:
            continue  # not circular (or fit didn't find a good match) -- try the next edge
    raise RuntimeError("Could not find any circular edge on 'rod' -- "
                        "expected at least one (the end caps).")


def main():
    if len(sys.argv) < 2:
        print("Usage: test_move_rod_axially.py <input.step>")
        return

    step_path = Path(sys.argv[1])
    print(f"Loading {step_path} ...")
    assembly = load_assembly(str(step_path))

    rod = next((c for c in assembly.descendants if c.label == "rod"), None)
    if rod is None:
        print("Could not find 'rod' in the assembly -- stopping.")
        return

    rod_parent = rod.parent
    print(f"Found 'rod', parent label = {rod_parent.label!r}")

    print("\nResolving rod's axis via the proven circle-fit picking pipeline...")
    edge, center, axis = find_rod_axis_edge(rod)
    print(f"  circle_center = {center}")
    print(f"  circle_axis   = {axis}")

    # NOTE: with direct in-place location mutation (see below), we
    # don't need pose.py's Plane/move_location_only machinery for
    # THIS test at all -- just the axis direction already resolved
    # from picking. (That machinery is still correct and still needed
    # for the GENERAL Mate/Align case, where the target pose comes
    # from a DIFFERENT part's geometry, not a simple "move along my
    # own current axis" -- this script is testing the simpler
    # axial-move case specifically.)
    MOVE_DISTANCE = 50.0
    print(f"\nWill move 'rod' {MOVE_DISTANCE}mm along axis {axis}")

    before_center = rod.center()

    # FIX (the REAL bug from the previous run, confirmed via
    # build123d's own docs): rod.location is a property that returns
    # a DETACHED COPY of the shape's location, not a live reference --
    # mutating box_location.position in the documented example only
    # ever mutates that EXTRACTED copy, never the original shape. That
    # exactly matches what happened: "rod.location.position BEFORE"
    # and "AFTER" were IDENTICAL, with no error, because the mutated
    # object was thrown away immediately.
    #
    # build123d documents FOUR methods for changing a shape's location
    # (Key Concepts -> Location): locate()=absolute+this object,
    # located()=absolute+copy, move()=RELATIVE+THIS OBJECT,
    # moved()=relative+copy. We want relative (move 50mm from current
    # position) AND in-place (mutate rod itself, not produce a copy)
    # -- that's move(), the one method in this 2x2 matrix matching
    # both requirements. Takes a Location representing the relative
    # delta to apply.
    from build123d import Location

    # Note: Vector.to_tuple() is deprecated in newer build123d
    # versions in favor of tuple(Vector) -- confirmed via build123d's
    # own changelog -- using the non-deprecated form.
    delta = Location(tuple(axis * MOVE_DISTANCE))
    print(f"\nrod.location.position BEFORE: {rod.location.position}")

    rod.move(delta)

    print(f"rod.location.position AFTER:  {rod.location.position}")

    # Sanity check: the rod's center should have shifted by
    # approximately MOVE_DISTANCE along axis, and by nothing
    # perpendicular to it. Re-measuring rod.center() AFTER the
    # in-place mutation -- same object, just relocated.
    after_center = rod.center()
    shift = after_center - before_center
    shift_along_axis = shift.dot(axis)
    shift_perpendicular = (shift - axis * shift_along_axis).length
    print(f"\nrod center BEFORE: {before_center}")
    print(f"rod center AFTER:  {after_center}")
    print(f"shift along axis:        {shift_along_axis:.4f}  (expected ~{MOVE_DISTANCE})")
    print(f"shift perpendicular:     {shift_perpendicular:.6f}  (expected ~0)")

    print(f"\nNo tree restructuring needed -- 'rod' is still the SAME")
    print(f"Python object, in the SAME place in the tree, under")
    print(f"{rod_parent.label!r}, just relocated.")

    out_path = step_path.with_name(step_path.stem + "_rod_moved.step")

    # DIAGNOSTIC: print the actual in-memory tree right before
    # export, using the SAME proven print_tree() that's been reliable
    # throughout this entire project -- confirms the tree structure
    # is genuinely untouched, not just assumed to be.
    print("\n--- In-memory tree, immediately before export ---")
    print_tree(assembly)
    print("--- end of in-memory tree ---\n")

    print(f"Exporting modified assembly to {out_path} ...")
    export_step(assembly, str(out_path))
    print(f"Done. Open {out_path} in CAD Assistant (or your normal STEP")
    print("viewer) and confirm visually: the rod should have shifted")
    print(f"{MOVE_DISTANCE}mm along its own axis, with nothing else in")
    print("the assembly disturbed -- it will likely now clash/overlap")
    print("with one of the brackets, which is fine and expected; this")
    print("is testing the MOVE mechanism, not producing a valid design.")


if __name__ == "__main__":
    main()
