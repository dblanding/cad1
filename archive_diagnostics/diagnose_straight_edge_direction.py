"""
diagnose_straight_edge_direction.py

Real picking just surfaced a crash: edge_direction's resolution
(pose.py) -- start=position_at(0), end=position_at(1),
(end-start).normalized() -- threw "vector has zero norm" on the
plate's ordinary straight top-front and top-right edges. For a normal
rectangular plate edge, position_at(0) and position_at(1) should be
its two distinct endpoints -- getting the SAME point for both is
unexpected and needs real data, not more guessing.

This script loads as1-oc-214.stp directly (no picking/GUI at all),
finds the plate's straight edges programmatically, and prints
position_at() at SEVERAL parameter values (not just 0 and 1) plus
other edge properties (length, geom_type, the underlying vertices)
to see exactly what's going on.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from step_assembly_poc import load_assembly  # noqa: E402

from build123d import GeomType


def main():
    if len(sys.argv) < 2:
        print("Usage: diagnose_straight_edge_direction.py <input.step>")
        return

    assembly = load_assembly(sys.argv[1])

    plate = next((c for c in assembly.descendants if c.label == "plate"), None)
    if plate is None:
        print("Could not find 'plate' in the assembly -- stopping.")
        return

    print(f"plate.edges() count: {len(plate.edges())}\n")

    straight_edges = [e for e in plate.edges() if e.geom_type == GeomType.LINE]
    print(f"Found {len(straight_edges)} straight (LINE) edges.\n")

    for i, edge in enumerate(straight_edges[:6]):  # first few are plenty
        print(f"--- Straight edge [{i}] ---")
        print(f"  geom_type = {edge.geom_type}")
        try:
            print(f"  length = {edge.length}")
        except Exception as e:
            print(f"  length: ERROR: {e}")

        for u in [0.0, 0.25, 0.5, 0.75, 1.0]:
            try:
                p = edge.position_at(u)
                print(f"  position_at({u}) = {p}")
            except Exception as e:
                print(f"  position_at({u}): ERROR: {e}")

        try:
            vertices = edge.vertices()
            print(f"  vertices() = {[v.to_tuple() for v in vertices]}")
        except Exception as e:
            print(f"  vertices(): ERROR: {e}")

        print()

    print("--- Conclusion ---")
    print("If position_at(0) == position_at(1) for these straight edges,")
    print("but the INTERMEDIATE values (0.25, 0.5, 0.75) differ and/or")
    print("vertices() shows two genuinely distinct points: this points at")
    print("position_at()'s PARAMETERIZATION being periodic/wrapped in a")
    print("way that makes 0 and 1 coincide for THIS edge specifically --")
    print("worth using vertices()[0]/[-1] or position_at(0)/position_at(0.99)")
    print("instead of exactly 0 and 1 in pose.py's edge_direction resolution.")
    print()
    print("If position_at(0) == position_at(1) AND all intermediate values")
    print("are also identical (or vertices() shows duplicate points too):")
    print("something more fundamental is wrong with how this edge was")
    print("extracted/wrapped -- worth comparing the SAME check against")
    print("edges() obtained a different way (e.g. via TopExp_Explorer")
    print("directly on the raw TopoDS_Shape) to isolate whether this is a")
    print("build123d-level issue or something about THIS shape specifically.")


if __name__ == "__main__":
    main()
