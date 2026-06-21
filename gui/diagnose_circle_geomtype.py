"""
diagnose_circle_geomtype.py

Real STEP picking just surfaced something pose.py's circle_center/
circle_axis resolution wasn't built to handle: a visually circular
edge (the round end of 'rod' in as1-oc-214.stp) reports
geom_type == "BSPLINE", not "CIRCLE". pose.py currently hard-rejects
anything that isn't exactly "CIRCLE".

Before deciding how to fix pose.py, isolate WHERE the BSPLINE
representation comes from: is this a STEP-specific artifact (the
exporting CAD system, or OCCT's STEP reader, approximating circles as
B-splines), or does even NATIVE build123d geometry -- a Cylinder,
never touching STEP at all -- report the same thing? Different root
causes need different fixes.
"""

from build123d import Cylinder, export_step, import_step
from pathlib import Path


def report_edge_geom_types(label, part):
    print(f"\n--- {label} ---")
    for i, edge in enumerate(part.edges()):
        try:
            gt = edge.geom_type
        except Exception as e:
            gt = f"<error: {e}>"
        length = None
        try:
            length = edge.length
        except Exception:
            pass
        print(f"  edge[{i}]: geom_type={gt}  length={length}")


def main():
    print("=== Part 1: NATIVE build123d Cylinder (no STEP involved) ===")
    cyl = Cylinder(radius=10, height=30)
    report_edge_geom_types("Native Cylinder, fresh from build123d", cyl)

    print("\n=== Part 2: SAME cylinder, round-tripped through STEP ===")
    tmp_path = Path("diag_cylinder.step")
    export_step(cyl, tmp_path)
    reimported = import_step(str(tmp_path))
    report_edge_geom_types("Re-imported Cylinder, after STEP round-trip", reimported)

    print("\n--- Conclusion ---")
    print("If Part 1 shows CIRCLE but Part 2 shows BSPLINE (or anything")
    print("else non-circular): the round-trip itself (our export_step or")
    print("OCCT's STEP reader) is responsible -- worth comparing against")
    print("a DIRECT import of as1-oc-214.stp too, since that file came")
    print("from a different STEP writer than ours (the original AP214")
    print("file, not something build123d exported).")
    print()
    print("If BOTH show CIRCLE: the rod's BSPLINE result is specific to")
    print("as1-oc-214.stp's own STEP encoding (or to a feature of 'rod'")
    print("specifically, e.g. a fillet/blend rather than a pure")
    print("cylindrical end) -- not a general STEP round-trip problem.")
    print()
    print("If BOTH show BSPLINE: something more fundamental about how")
    print("geom_type is read needs investigation -- unlikely given how")
    print("central CIRCLE classification is to build123d/OCCT, but worth")
    print("ruling out rather than assuming.")


if __name__ == "__main__":
    main()
