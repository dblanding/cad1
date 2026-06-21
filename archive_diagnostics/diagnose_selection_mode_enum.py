"""
diagnose_selection_mode_enum.py

Working hypothesis from the picking bug (clicking an edge precisely
returned TopAbs_SOLID instead of TopAbs_EDGE or TopAbs_FACE): there
may be a mismatch between TWO DIFFERENT integer conventions that look
similar but aren't guaranteed to be the same:

    1. TopAbs_ShapeEnum's real underlying integer values (COMPOUND,
       COMPSOLID, SOLID, SHELL, FACE, WIRE, EDGE, VERTEX, SHAPE -- in
       SOME order, not yet independently confirmed for this OCP build).
    2. AIS_Shape's OWN selection-mode integer convention, documented
       separately as "Mode 0: whole object, Mode 1: vertices, Mode 2:
       edges, Mode 3: wires, Mode 4: faces" -- which may or may not be
       defined as numerically IDENTICAL to TopAbs_ShapeEnum's values.

context.Activate(ais_shape, TopAbs_FACE) and
context.Activate(ais_shape, TopAbs_EDGE) have been passing
TopAbs_ShapeEnum members directly as if they're guaranteed to equal
the AIS_Shape selection-mode integers. If they don't actually match
up automatically, "FACE" picking might have only ever appeared to
work because TopAbs_FACE's value happens to coincide with AIS mode 4
by chance -- not because the two conventions are actually unified.

This script does NOT reason from memory or documentation snippets --
it just prints the real, actual integer values, directly from this
machine's installed OCP, settling the question with data.
"""

from OCP.TopAbs import (
    TopAbs_COMPOUND,
    TopAbs_COMPSOLID,
    TopAbs_SOLID,
    TopAbs_SHELL,
    TopAbs_FACE,
    TopAbs_WIRE,
    TopAbs_EDGE,
    TopAbs_VERTEX,
    TopAbs_SHAPE,
)


def main():
    print("--- Actual TopAbs_ShapeEnum integer values, this OCP build ---\n")
    enums = {
        "TopAbs_COMPOUND": TopAbs_COMPOUND,
        "TopAbs_COMPSOLID": TopAbs_COMPSOLID,
        "TopAbs_SOLID": TopAbs_SOLID,
        "TopAbs_SHELL": TopAbs_SHELL,
        "TopAbs_FACE": TopAbs_FACE,
        "TopAbs_WIRE": TopAbs_WIRE,
        "TopAbs_EDGE": TopAbs_EDGE,
        "TopAbs_VERTEX": TopAbs_VERTEX,
        "TopAbs_SHAPE": TopAbs_SHAPE,
    }
    for name, val in enums.items():
        # Try to get the raw integer value, however this binding
        # exposes it (could be .value, int(val), or val itself if
        # it's already plain).
        try:
            raw = int(val)
        except (TypeError, ValueError):
            raw = getattr(val, "value", repr(val))
        print(f"  {name:20s} = {val!r}   (as int: {raw})")

    print("\n--- AIS_Shape's documented selection-mode convention ---")
    print("  (from OCCT's own docs -- NOT yet confirmed these are")
    print("   guaranteed identical to the TopAbs values above):")
    print("  Mode 0 = whole object")
    print("  Mode 1 = vertices")
    print("  Mode 2 = edges")
    print("  Mode 3 = wires")
    print("  Mode 4 = faces")

    print("\n--- Conclusion ---")
    face_val = int(TopAbs_FACE) if not isinstance(TopAbs_FACE, int) else TopAbs_FACE
    edge_val = int(TopAbs_EDGE) if not isinstance(TopAbs_EDGE, int) else TopAbs_EDGE
    solid_val = int(TopAbs_SOLID) if not isinstance(TopAbs_SOLID, int) else TopAbs_SOLID

    print(f"TopAbs_FACE as int = {face_val}  (AIS mode 4 = faces -- "
          f"{'MATCHES' if face_val == 4 else 'DOES NOT MATCH'})")
    print(f"TopAbs_EDGE as int = {edge_val}  (AIS mode 2 = edges -- "
          f"{'MATCHES' if edge_val == 2 else 'DOES NOT MATCH'})")
    print(f"TopAbs_SOLID as int = {solid_val}  (no defined AIS_Shape mode "
          f"for this -- if edge_val == {solid_val}, THAT would explain "
          f"why an edge pick returned a SOLID: Activate(ais_shape, "
          f"TopAbs_EDGE) may have actually activated whatever AIS mode "
          f"shares EDGE's numeric value, which might not mean 'edges' "
          f"at all in AIS_Shape's own convention.)")

    if face_val != 4 or edge_val != 2:
        print("\n*** MISMATCH CONFIRMED: passing TopAbs_ShapeEnum members")
        print("*** directly to context.Activate() is NOT safe in general --")
        print("*** only worked for FACE by numeric coincidence, if at all.")
        print("*** Fix: use AIS_Shape's own mode integers explicitly (or")
        print("*** find the correct OCP-exposed constant/method for")
        print("*** translating TopAbs_ShapeEnum -> AIS_Shape selection mode,")
        print("*** e.g. AIS_Shape.SelectionMode(TopAbs_EDGE) if that method")
        print("*** is exposed -- worth checking instead of hardcoding magic")
        print("*** integers ourselves.)")
    else:
        print("\nFACE and EDGE values both match AIS_Shape's documented")
        print("convention numerically -- the enum-mismatch hypothesis is")
        print("WRONG, the bug must be something else. Don't trust this")
        print("printout alone though -- also check SOLID's actual value")
        print("against whatever edge_val was, since that's the most direct")
        print("explanation for the observed SOLID result.")


if __name__ == "__main__":
    main()
