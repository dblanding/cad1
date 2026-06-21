"""
step_assembly_poc.py

Minimal proof-of-concept for the one capability that makes or breaks a
DIY CAD app: round-tripping a STEP assembly.

    1. Import a STEP file as a build123d Compound, preserving the
       assembly hierarchy (sub-assemblies / parts / names).
    2. Print the tree so you can see what survived the import.
    3. Programmatically add a new part to the assembly and remove an
       existing one, by label.
    4. Re-export the modified assembly as a new STEP file.

This is deliberately a command-line script, not a GUI, so the geometry
plumbing can be verified in isolation before any Qt code is written.

CONFIRMED WORKING as of real-world testing (see README.md for the
full diagnosis). Getting here required working around a genuine bug
in build123d's export_step() -- see step_export_fix.py. The fix is
applied transparently via the import below; no special handling
needed elsewhere in this file.

Requires:
    pip install build123d
"""

from __future__ import annotations

import sys
from pathlib import Path

from build123d import Compound, Box, import_step

# FIX: build123d's export_step() has a confirmed bug where shapes
# returned by import_step() carry a spurious, invisible .parent
# reference (NOT None, despite looking like a clean root via
# .children/.descendants). This causes _create_xde() to silently
# produce an empty document. See step_export_fix.py and README.md
# for the full root-cause diagnosis. step_export_fix.export_step()
# is a thin wrapper around the REAL build123d export_step() -- same
# features, same behavior -- with the one-line fix applied first.
from step_export_fix import export_step


# --------------------------------------------------------------------------
# 1. Tree inspection
# --------------------------------------------------------------------------

def print_tree(node: Compound, indent: int = 0) -> None:
    """
    Recursively print the assembly tree.

    build123d's Compound is built on anytree's NodeMixin (see the
    "Assemblies" docs), so `.children` and `.label` are first-class
    attributes -- no manual TDF_LabelSequence walking required at this
    layer. That walking already happened inside import_step().
    """
    label = node.label or "<unnamed>"
    kind = type(node).__name__
    # leaf compounds that wrap a single solid will report a volume;
    # pure organizational nodes (sub-assemblies) won't have meaningful
    # geometry of their own beyond the union of their children.
    try:
        vol = node.volume
        vol_str = f"  (volume={vol:.2f})"
    except Exception:
        vol_str = ""
    print("  " * indent + f"- {label} [{kind}]{vol_str}")
    for child in node.children:
        print_tree(child, indent + 1)


def flatten_named(node: Compound, path: str = "") -> dict[str, Compound]:
    """
    Build a flat {dotted_path: node} map so callers can find a part by
    name without writing their own tree-walk every time. Dotted paths
    disambiguate parts that share a label in different sub-assemblies.
    """
    label = node.label or "<unnamed>"
    full_path = f"{path}/{label}" if path else label
    result = {full_path: node}
    for child in node.children:
        result.update(flatten_named(child, full_path))
    return result


# --------------------------------------------------------------------------
# 2. Core round-trip operations
# --------------------------------------------------------------------------

def load_assembly(step_path: str | Path) -> Compound:
    """
    Import a STEP file as a build123d Compound.

    import_step() internally opens the file with OCCT's
    STEPCAFControl_Reader (not the bare STEPControl_Reader), which is
    what gives you the XCAF document with names/colors/hierarchy
    rather than a single flattened TopoDS_Shape. That distinction is
    the entire ballgame for assembly support -- it's also exactly the
    part that's tedious to get right by hand against raw OCCT, which
    is why leaning on build123d's importer here is worth it even if
    you bypass it for everything else.
    """
    path = Path(step_path)
    if not path.exists():
        raise FileNotFoundError(f"STEP file not found: {path}")
    return import_step(str(path))


def remove_node(node: Compound) -> bool:
    """
    Remove a SPECIFIC node (by Python object identity, not by label)
    from its parent's children. This is the unambiguous, correct
    primitive to use whenever you already have the exact node in hand
    -- e.g. from a tree-widget drag event, which knows exactly which
    QTreeWidgetItem (and therefore which real Compound node) the user
    dragged, regardless of how many siblings share its label.

    Returns True if the node was removed.
    """
    parent = node.parent
    if parent is None:
        return False
    parent.children = tuple(c for c in parent.children if c is not node)
    return True


def remove_part(assembly: Compound, label: str) -> bool:
    """
    Remove the FIRST child (anywhere in the tree, depth-first) whose
    label matches. Returns True if something was removed.

    AMBIGUITY WARNING: on assemblies with repeated part names (e.g.
    multiple parts named "nut" or "bolt" -- common in real STEP files,
    confirmed on as1-oc-214.stp which has 6 "nut" instances), this
    matches the FIRST occurrence found, which may not be the specific
    instance you intended. This function exists for simple
    command-line/scripting use where label ambiguity isn't a concern
    (e.g. removing a uniquely-named diagnostic part). Whenever you
    already have a specific node object in hand -- e.g. from a UI
    selection or drag event -- use remove_node(node) instead, which
    operates on object identity and has no ambiguity.

    NOTE: build123d Compounds store children as an immutable tuple
    (see Assemblies docs: "subsequently the children are stored as
    immutable tuple objects"). To remove a node you reassign its
    PARENT's .children, you don't mutate the tuple in place.
    """
    for node in assembly.descendants:  # anytree-provided
        if node.label == label:
            return remove_node(node)
    return False


def add_node(new_part: Compound, target_parent: Compound) -> None:
    """
    Add `new_part` as a child of `target_parent` (a SPECIFIC node
    object, not a label match). The unambiguous counterpart to
    add_part() -- use this whenever you already have the exact target
    parent node in hand, e.g. from a tree-widget drag event.
    """
    target_parent.children = (*target_parent.children, new_part)


def add_part(assembly: Compound, new_part: Compound, parent_label: str | None = None) -> None:
    """
    Add `new_part` as a child of the FIRST node found (depth-first)
    matching `parent_label`, or of the assembly root if parent_label
    is None.

    AMBIGUITY WARNING: same caveat as remove_part() -- on assemblies
    with repeated labels (e.g. multiple "l-bracket-assembly" nodes),
    this targets the first match, which may not be the specific
    parent you intended. Use add_node(new_part, target_parent)
    instead whenever you already have the exact target node object in
    hand.
    """
    target = assembly
    if parent_label is not None:
        for node in [assembly, *assembly.descendants]:
            if node.label == parent_label:
                target = node
                break
        else:
            raise ValueError(f"No node found with label {parent_label!r}")
    add_node(new_part, target)


def save_assembly(assembly: Compound, out_path: str | Path) -> None:
    """
    Export the (possibly modified) assembly back to STEP.

    export_step() builds a fresh XCAFDoc/TDocStd_Document XDE document
    and writes labels + colors + hierarchy with STEPCAFControl_Writer.
    This is the inverse of load_assembly() and is what makes the
    round-trip meaningful instead of just "flatten to one shape".
    """
    export_step(assembly, str(out_path))


# --------------------------------------------------------------------------
# 3. Demo driver
# --------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print(f"Usage: python {Path(__file__).name} <input.step> [output.step]")
        print()
        print("If no input file is given, a synthetic test assembly is")
        print("built in memory instead, so you can verify the add/remove/")
        print("export logic even before you have a real STEP file to test.")
        demo_with_synthetic_assembly()
        return

    in_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else in_path.with_name(
        in_path.stem + "_modified.step"
    )

    print(f"Loading {in_path} ...")
    assembly = load_assembly(in_path)

    print("\n--- Imported assembly tree ---")
    print_tree(assembly)

    named = flatten_named(assembly)
    print(f"\nFound {len(named)} named nodes.")
    print("(Use these dotted paths as labels if you want to target a")
    print(" specific part for removal in a sub-assembly.)")

    # Example mutation: add a small reference cube at the top level,
    # and try to remove a part called "REMOVE_ME" if one happens to
    # exist (it won't, on a real-world file -- this just demonstrates
    # the call, swap in a real label from the printed tree above).
    new_box = Box(5, 5, 5)
    new_box.label = "diagnostic_cube"
    add_part(assembly, new_box)
    print("\nAdded 'diagnostic_cube' at the assembly root.")

    removed = remove_part(assembly, "REMOVE_ME")
    print(f"Attempted removal of 'REMOVE_ME': {'removed' if removed else 'not found (expected on most files)'}")

    print(f"\nExporting modified assembly to {out_path} ...")
    save_assembly(assembly, out_path)
    print("Done. Open both STEP files in your normal CAD viewer and diff them visually.")


def demo_with_synthetic_assembly():
    """
    No input STEP file? Build a tiny 3-part assembly from scratch,
    export it, re-import it, mutate it (remove one part, add another),
    and export again -- a full round-trip using only synthetic
    geometry. This isolates "does my build123d/OCP install work at
    all" from "does it work on THIS specific STEP file", which
    matters because real-world STEP files from different CAD systems
    vary a lot in how cleanly they encode assembly structure.
    """
    base = Box(20, 20, 5)
    base.label = "base_plate"

    post = Box(5, 5, 30)
    post.label = "support_post"

    cap = Box(8, 8, 3)
    cap.label = "top_cap"

    assembly = Compound(label="demo_assembly", children=[base, post, cap])

    print("\n--- Synthetic assembly (before export) ---")
    print_tree(assembly)

    tmp_path = Path("synthetic_demo.step")
    save_assembly(assembly, tmp_path)
    print(f"\nExported synthetic assembly to {tmp_path.resolve()}")

    print("\nRe-importing it to verify round-trip...")
    reimported = load_assembly(tmp_path)
    print("\n--- Re-imported assembly tree ---")
    print_tree(reimported)

    removed = remove_part(reimported, "support_post")
    print(f"\nRemoved 'support_post': {removed}")

    bracket = Box(3, 3, 3)
    bracket.label = "added_bracket"
    add_part(reimported, bracket)
    print("Added 'added_bracket'.")

    out_path = Path("synthetic_demo_modified.step")
    save_assembly(reimported, out_path)
    print(f"\nExported modified assembly to {out_path.resolve()}")
    print("\n--- Final tree ---")
    print_tree(reimported)
    print("\nFull round-trip succeeded: import -> inspect -> remove -> add -> export.")


if __name__ == "__main__":
    main()
