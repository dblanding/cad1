"""
diagnose_small_delete_export.py

Doug's call: stop forensically poking at a 306MB file we can't fully
read, and instead build a SMALL, fully human-inspectable repro of the
exact same workflow -- import a sub-assembly as a child of a root,
delete some of its leaf parts via the same remove_node() the GUI's
RMB Delete uses, export via the same (now-fixed) step_export_fix, and
then just... read the output file directly. No greps, no guessing at
line-wrap, no relying on a third-party viewer's own rendering/caching
behavior -- just the text.

This deliberately uses build123d primitives (no STEP file needed to
create the test data) so the WHOLE pipeline under test is exactly:
  1. Build a small root "assembly" (Compound) with a couple of parts.
  2. Build a small "sub-assembly" (Compound) with several DISTINCTLY
     NAMED leaf parts -- standing in for the imported car model.
  3. add_node(sub_assembly, root) -- exactly what _on_import_clicked()
     does when you import a STEP file into a running session.
  4. remove_node() on a few of the sub-assembly's leaf children --
     exactly what _on_node_delete_requested() does for RMB Delete.
  5. step_export_fix.export_step() -- the fixed exporter.
  6. Read the output file as plain text and report, per deleted part
     name, whether it appears ANYWHERE in the file at all.

If a deleted part's name shows up in the output here, we have a
small, complete, shareable repro of a real bug -- worth reading the
.step file by hand at that point, since it'll be short enough to
actually do that. If nothing shows up, the core pipeline is clean on
this synthetic case, and whatever's happening with the real 306MB
file is specific to something about IT (a data quirk in the real STEP
file, or something in CAD Assistant's own handling) rather than a
general bug in BasiCAD's delete+export path.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from build123d import Box, Compound, Location
from step_assembly_poc import add_node, remove_node
from step_export_fix import export_step


def make_part(label, position):
    box = Box(5, 5, 5)
    box = box.moved(Location(position))
    box.label = label
    return box


def main():
    print("--- Building a small root assembly ---")
    root_part_1 = make_part("root_part_1", (0, 0, 0))
    root_part_2 = make_part("root_part_2", (10, 0, 0))
    root = Compound(label="test_root", children=[root_part_1, root_part_2])
    root.label = "test_root"
    print(f"  root: {root.label!r}, children: "
          f"{[c.label for c in root.children]}\n")

    print("--- Building a small 'imported' sub-assembly ---")
    # Names deliberately distinctive and easy to grep for by hand.
    wheel_1 = make_part("WHEEL_FRONT_LEFT", (0, 0, 0))
    wheel_2 = make_part("WHEEL_FRONT_RIGHT", (20, 0, 0))
    tire_1 = make_part("TIRE_FRONT_LEFT", (0, 20, 0))
    tire_2 = make_part("TIRE_FRONT_RIGHT", (20, 20, 0))
    motor = make_part("MOTOR_MAIN", (0, 40, 0))
    bracket = make_part("MOUNTING_BRACKET", (20, 40, 0))
    keep_part = make_part("CHASSIS_KEEP_ME", (40, 0, 0))

    car_model = Compound(
        label="car_model",
        children=[wheel_1, wheel_2, tire_1, tire_2, motor, bracket, keep_part],
    )
    car_model.label = "car_model"
    print(f"  car_model: {car_model.label!r}, children: "
          f"{[c.label for c in car_model.children]}\n")

    print("--- add_node(car_model, root) -- mirrors _on_import_clicked() ---")
    add_node(car_model, root)
    print(f"  root now has children: {[c.label for c in root.children]}\n")

    to_delete = [wheel_1, wheel_2, tire_1, tire_2, motor, bracket]
    print(f"--- Deleting {len(to_delete)} parts via remove_node() "
          f"-- mirrors RMB Delete (_on_node_delete_requested()) ---")
    for part in to_delete:
        ok = remove_node(part)
        print(f"  remove_node({part.label!r}) -> {ok}")

    remaining = [c.label for c in car_model.children]
    print(f"\n  car_model's remaining children after deletion: {remaining}")
    if remaining != ["CHASSIS_KEEP_ME"]:
        print("  !! UNEXPECTED: car_model still has children other than "
              "CHASSIS_KEEP_ME in the Python tree itself, BEFORE export "
              "even happens. If this line prints, the bug is in "
              "remove_node()/deletion, not export -- stop here, this IS "
              "the smoking gun.")
    else:
        print("  OK: car_model's Python tree correctly shows only "
              "CHASSIS_KEEP_ME remaining -- deletion worked at the data "
              "level. If the deleted names still show up in the exported "
              "file below, the bug is specifically in export, not deletion.")

    out_path = Path("small_delete_export_test.step")
    print(f"\n--- Exporting via step_export_fix.export_step() to "
          f"{out_path} ---")
    ok = export_step(root, str(out_path))
    print(f"  export success: {ok}")
    if not ok:
        print("  Export itself failed -- nothing further to check.")
        return

    text = out_path.read_text(errors="replace")
    print(f"\n--- Checking each deleted part's name against the exported "
          f"file's text ({len(text)} chars) ---")
    all_clean = True
    for part in to_delete:
        count = text.count(part.label)
        status = "STILL PRESENT" if count > 0 else "correctly absent"
        print(f"  {part.label:20s}: appears {count} time(s) -- {status}")
        if count > 0:
            all_clean = False

    print(f"\n  CHASSIS_KEEP_ME (should be present): "
          f"appears {text.count('CHASSIS_KEEP_ME')} time(s)")

    print("\n" + "=" * 70)
    if all_clean:
        print("RESULT: all 6 deleted parts are correctly absent from the "
              "exported file. This small, complete repro of the real "
              "workflow (import as child -> delete leaves -> export) is "
              "CLEAN. Whatever's happening with the 306MB file is likely "
              "specific to something about that particular STEP file or "
              "how CAD Assistant is displaying it, not a general bug in "
              "BasiCAD's delete+export pipeline.")
        print(f"\nWorth opening {out_path.resolve()} directly in a text "
              f"editor anyway ({len(text)} chars, small enough to read "
              f"start to finish) to visually confirm nothing looks off.")
    else:
        print("RESULT: at least one deleted part's name IS still present "
              "in the exported file. This is a small, complete, shareable "
              "repro of a real bug -- worth reading "
              f"{out_path.resolve()} by hand from here, since it's short "
              "enough to actually do that.")


if __name__ == "__main__":
    main()
