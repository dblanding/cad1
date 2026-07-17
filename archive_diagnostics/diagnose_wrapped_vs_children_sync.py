"""
diagnose_wrapped_vs_children_sync.py

Testing a specific hypothesis raised by a very telling piece of new
evidence: after deleting parts via RMB Delete (remove_node()) and
exporting, CAD Assistant shows BLANK rows exactly where the deleted
parts' names used to be, and clicking the still-visible geometry in
the viewport doesn't highlight ANY tree row. That combination -- geometry
present, name gone, not independently selectable -- doesn't match
"deletion didn't work" (the names really are gone from the exported
text, confirmed by grep with line-wrap ruled out) OR "export drops
data" (a small flat synthetic repro came back completely clean).

NEW HYPOTHESIS: remove_node() does this:
    parent.children = tuple(c for c in parent.children if c is not node)
That's PYTHON-level tree bookkeeping only (anytree's parent/children
links). It does NOT touch parent.wrapped -- the actual TopoDS_Compound
shape data. If parent.wrapped was built ONCE (e.g. at import_step()
time) and is a STATIC snapshot of the original topology rather than
something dynamically recomputed from .children, then a "deleted"
child's geometry could still be structurally embedded inside its
PARENT's raw shape, even though the Python tree correctly no longer
lists it as a child. On export, _create_xde_corrected() calls
AddComponent(parent_label, node.wrapped) using exactly that
POTENTIALLY-STALE parent.wrapped -- if it still contains the deleted
geometry, that geometry gets written out as an undifferentiated part
of the parent's own shape, with no separate name (nothing calls
set_name_and_color() on it individually, since PreOrderIter no longer
visits it as its own node) -- matching "blank row, geometry present,
not independently selectable" exactly.

The earlier small synthetic test (diagnose_small_delete_export.py)
deleted FLAT, single-level leaf children directly from their immediate
parent. This test instead builds a MULTI-LEVEL nested structure
(mirroring car_model -> wheel_assembly -> wheel/tire, several levels
deep) and deletes at the deeper level, then checks -- BEFORE any
export is even involved -- whether the intermediate parent's OWN
.wrapped still structurally contains the deleted child's geometry via
direct TopExp_Explorer solid-counting. This isolates the hypothesis
completely from export-side code.

Usage:
    python diagnose_wrapped_vs_children_sync.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from build123d import Box, Compound, Location
from step_assembly_poc import add_node, remove_node
from step_export_fix import export_step


def count_solids(shape):
    """Count TopoDS_Solid sub-shapes structurally present in `shape`
    (a raw TopoDS_Shape/wrapped), independent of any Python-level tree
    bookkeeping -- the ground truth for 'is this geometry actually
    still in here.'"""
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_SOLID
    count = 0
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    while exp.More():
        count += 1
        exp.Next()
    return count


def make_part(label, position):
    box = Box(5, 5, 5)
    box = box.moved(Location(position))
    box.label = label
    return box


def main():
    print("--- Building a multi-level nested structure ---")
    print("    root -> car_model -> wheel_assembly -> [wheel, tire, KEEP_ME]\n")

    wheel = make_part("WHEEL_DEEP", (0, 0, 0))
    tire = make_part("TIRE_DEEP", (10, 0, 0))
    keep = make_part("KEEP_ME_DEEP", (20, 0, 0))

    wheel_assembly = Compound(label="wheel_assembly", children=[wheel, tire, keep])
    wheel_assembly.label = "wheel_assembly"

    other_part = make_part("car_body", (0, 20, 0))
    car_model = Compound(label="car_model", children=[wheel_assembly, other_part])
    car_model.label = "car_model"

    root_part = make_part("root_part_1", (0, 0, 0))
    root = Compound(label="test_root", children=[root_part])
    root.label = "test_root"

    print(f"  wheel_assembly.wrapped solid count BEFORE deletion: "
          f"{count_solids(wheel_assembly.wrapped)}")
    print(f"  car_model.wrapped solid count BEFORE deletion:      "
          f"{count_solids(car_model.wrapped)}\n")

    print("--- add_node(car_model, root) -- mirrors STEP import ---")
    add_node(car_model, root)
    print(f"  root children: {[c.label for c in root.children]}\n")

    print("--- Deleting WHEEL_DEEP and TIRE_DEEP via remove_node() "
          "-- mirrors RMB Delete at a nested level ---")
    for part in (wheel, tire):
        ok = remove_node(part)
        print(f"  remove_node({part.label!r}) -> {ok}")

    remaining = [c.label for c in wheel_assembly.children]
    print(f"\n  wheel_assembly's Python .children after deletion: {remaining}")

    print("\n" + "=" * 78)
    print("THE ACTUAL TEST: does wheel_assembly.wrapped (the raw shape data)")
    print("still structurally contain WHEEL_DEEP/TIRE_DEEP's geometry, even")
    print("though the Python .children list correctly no longer lists them?")
    print("=" * 78)
    solids_after = count_solids(wheel_assembly.wrapped)
    print(f"\n  wheel_assembly.wrapped solid count AFTER deletion:  "
          f"{solids_after}")
    print(f"  (Before deletion it was 3 -- wheel, tire, keep_me. "
          f"If it's STILL 3, .wrapped did NOT get updated when "
          f".children changed -- CONFIRMED: this is the bug.")
    print(f"  If it's now 1 -- just keep_me -- .wrapped correctly "
          f"tracks .children, and this hypothesis is WRONG, needs "
          f"to look elsewhere.)")

    if solids_after >= 3:
        print("\n  !!! CONFIRMED: wheel_assembly.wrapped still contains all "
              "3 original solids' geometry despite .children correctly "
              "showing only 1 remaining. remove_node() updates the Python "
              "tree but NOT the underlying shape data -- deleted "
              "geometry survives inside its former parent's raw shape.")
    elif solids_after == 1:
        print("\n  wheel_assembly.wrapped correctly shows only 1 solid -- "
              ".wrapped DOES track .children changes for this case. This "
              "specific hypothesis doesn't hold; the real bug must be "
              "somewhere else.")
    else:
        print(f"\n  Unexpected count ({solids_after}) -- worth a closer "
              f"look either way.")

    print("\n--- For completeness: exporting the whole thing and checking "
          "the exported text too ---")
    out_path = Path("wrapped_sync_test.step")
    ok = export_step(root, str(out_path))
    print(f"  export success: {ok}")
    if ok:
        text = out_path.read_text(errors="replace")
        for name in ("WHEEL_DEEP", "TIRE_DEEP", "KEEP_ME_DEEP"):
            print(f"  {name:15s}: appears {text.count(name)} time(s) in "
                  f"exported text")
        print(f"\n  Solid count in the WHOLE exported root.wrapped: "
              f"{count_solids(root.wrapped)}")


if __name__ == "__main__":
    main()
