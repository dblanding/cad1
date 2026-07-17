"""
diagnose_nested_assembly_addcomponent.py

Reported (after step_export_fix.py's root-assembly-flag fix, item 41):
importing as1-oc-214.stp fresh, importing a car-model sub-assembly
(3209-0004-0001.step) as a NEW CHILD under it, deleting 4 gray parts
(tires/wheels/motors/brackets) from that imported car model, then
exporting the WHOLE SESSION -- the deleted parts' geometry is STILL
visible in CAD Assistant, but with NO corresponding tree entry at all
(no name, no label, nothing to select). Concretely different from the
already-fixed bug: this isn't "everything collapsed into 3 blobs",
it's specific unlabeled geometry with zero XDE presence.

KEY CLUE: exporting 3209-0004-0001.step STANDALONE (self-tested
directly, see item 41's confirmation) already produced a healthy
101 PRODUCT_DEFINITION / 647 NEXT_ASSEMBLY_USAGE_OCCURRENCE / 60
MANIFOLD_SOLID_BREP result. The only thing that changed between that
successful standalone export and this broken one is that the car
model is no longer the ROOT of the export -- it's now a CHILD, added
to as1 via add_node(), reached through _create_xde_corrected()'s
AddComponent() branch instead of its (fixed) root AddShape() branch.

HYPOTHESIS: step_export_fix.py's fix only corrects the makeAssembly
flag for the absolute ROOT node (the one true `parent is None` case).
Every other node -- including a compound-assembly component nested
under a different root -- goes through
`shape_tool.AddComponent(parent_label, node.wrapped)`, a 2-argument
call with NO explicit assembly/simple-shape flag at all. If
AddComponent() does NOT automatically recognize a compound-shaped
component as something to decompose into its own sub-components
(unlike AddShape(), which we now explicitly tell), then a whole
imported sub-assembly, DEEP inside a bigger session, could silently
fail to register its own children -- while the underlying TopoDS
geometry (which is still structurally present in node.wrapped) still
gets serialized by the STEP geometry writer regardless, exactly
matching "geometry visible, no tree entry."

THIS SCRIPT traces _create_xde_corrected()'s loop with logging added
at every node, specifically to answer:
  1. Does the car-model node itself (3209-0004-0001, or whatever its
     import label is) get a non-null label when added via
     AddComponent (i.e. does IT get registered at all)?
  2. For each of ITS children in turn, does label_map correctly find
     a non-null parent_label for them (or does resolve_component_parent_label
     return something IsNull(), silently dropping the whole subtree
     per the `if parent_label.IsNull(): continue` line)?
  3. Does shape_tool.IsAssembly_s(car_model_label) report True or
     False after AddComponent -- i.e. did OCCT even recognize it as
     a decomposable assembly, or treat it as one opaque simple shape?

Usage:
    python diagnose_nested_assembly_addcomponent.py <as1_step> <car_step>

Loads as1_step as the "session" root, imports car_step as a NEW child
under it (mirroring add_node() in step_assembly_poc.py), then runs
the traced XDE build -- no deletion needed to see the effect; if the
car model's children don't register at all, ALL of its children
(gray or not) would show this problem, deletion isn't actually
required to reproduce it.
"""

import sys
from pathlib import Path

from build123d import import_step, Compound


def create_xde_traced(to_export, unit_str="MM", auto_naming: bool = True):
    """
    Same corrected root-flag logic as step_export_fix.py's
    _create_xde_corrected(), with verbose per-node tracing added so we
    can see exactly where a nested assembly's children stop getting
    registered.
    """
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDF import TDF_Label
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool, XCAFDoc_ShapeTool
    from anytree import PreOrderIter

    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    application = XCAFApp_Application.GetApplication_s()
    application.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)
    application.InitDocument(doc)

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    shape_tool.SetAutoNaming_s(auto_naming)

    label_map = {}

    def resolve_component_parent_label(label):
        if label.IsNull():
            return label
        if XCAFDoc_ShapeTool.IsReference_s(label):
            referred = TDF_Label()
            if XCAFDoc_ShapeTool.GetReferredShape_s(label, referred) and not referred.IsNull():
                return referred
        return label

    def set_name_and_color(node, node_label):
        if node_label.IsNull():
            return
        if node.label:
            TDataStd_Name.Set_s(node_label, TCollection_ExtendedString(node.label))
            if XCAFDoc_ShapeTool.IsReference_s(node_label):
                referred = TDF_Label()
                if XCAFDoc_ShapeTool.GetReferredShape_s(node_label, referred) and not referred.IsNull():
                    TDataStd_Name.Set_s(referred, TCollection_ExtendedString(node.label))
        if node.color is not None:
            node_color_type = XCAFDoc_ColorType.XCAFDoc_ColorGen
            color_tool.SetColor(node_label, node.color.wrapped, node_color_type)

    node_count = 0
    skipped_count = 0

    for node in PreOrderIter(to_export):
        node_count += 1
        if node.wrapped is None:
            print(f"  [{node_count}] {node.label!r}: SKIPPED (wrapped is None)")
            skipped_count += 1
            continue

        parent = getattr(node, "parent", None)
        n_children = len(node.children) if hasattr(node, "children") else 0
        is_compound = isinstance(node, Compound)

        if parent is None:
            is_assembly = is_compound and n_children > 0
            node_label = shape_tool.AddShape(node.wrapped, is_assembly)
            print(f"  [{node_count}] ROOT {node.label!r}: "
                  f"AddShape(makeAssembly={is_assembly}), "
                  f"is_compound={is_compound}, n_children={n_children} "
                  f"-> label.IsNull()={node_label.IsNull()}")
        else:
            parent_label_raw = label_map.get(parent, None)
            if parent_label_raw is None:
                print(f"  [{node_count}] {node.label!r}: parent "
                      f"{getattr(parent, 'label', '?')!r} NOT in label_map "
                      f"at all (parent itself must have failed/been "
                      f"skipped) -- SKIPPING this node and its whole subtree")
                skipped_count += 1
                continue
            parent_label = resolve_component_parent_label(parent_label_raw)
            if parent_label.IsNull():
                print(f"  [{node_count}] {node.label!r}: parent_label "
                      f"IsNull() after resolve -- SKIPPING this node and "
                      f"its whole subtree")
                skipped_count += 1
                continue
            node_label = shape_tool.AddComponent(parent_label, node.wrapped)
            marker = " <-- COMPOUND WITH CHILDREN, added via AddComponent " \
                     "(no explicit assembly flag available here)" \
                     if (is_compound and n_children > 0) else ""
            print(f"  [{node_count}] {node.label!r} (parent="
                  f"{parent.label!r}): AddComponent -> "
                  f"label.IsNull()={node_label.IsNull()}  "
                  f"is_compound={is_compound}  n_children={n_children}"
                  f"{marker}")
            if not node_label.IsNull():
                try:
                    is_asm_now = XCAFDoc_ShapeTool.IsAssembly_s(node_label)
                    is_simple_now = XCAFDoc_ShapeTool.IsSimpleShape_s(node_label)
                    print(f"        -> after AddComponent: "
                          f"IsAssembly_s={is_asm_now}  "
                          f"IsSimpleShape_s={is_simple_now}")
                except Exception as e:
                    print(f"        -> (could not query IsAssembly_s/"
                          f"IsSimpleShape_s: {e})")

        if node_label.IsNull():
            print(f"        -> node_label IS NULL -- this node will NOT "
                  f"appear in the exported file at all")
            skipped_count += 1
            continue

        label_map[node] = node_label
        if node.label or node.color is not None:
            set_name_and_color(node, node_label)

    shape_tool.UpdateAssemblies()
    print(f"\nTotal nodes visited: {node_count}   Skipped/failed: {skipped_count}")
    return doc


def main():
    if len(sys.argv) < 3:
        print("Usage: diagnose_nested_assembly_addcomponent.py <as1_step> <car_step>")
        return

    as1_path = Path(sys.argv[1])
    car_path = Path(sys.argv[2])

    print(f"Importing session root: {as1_path} ...")
    root = import_step(str(as1_path))
    if getattr(root, "parent", None) is not None:
        root.parent = None
    print(f"  root: {root.label!r}, {len(list(root.descendants))} descendants\n")

    print(f"Importing car model: {car_path} ...")
    car = import_step(str(car_path))
    print(f"  car model root: {car.label!r}, "
          f"{len(list(car.descendants))} descendants, "
          f"{len(car.children)} direct children\n")

    print("Attaching car model as a new child of the session root "
          "(mirroring add_node() in step_assembly_poc.py) ...\n")
    root.children = (*root.children, car)

    print("=" * 78)
    print("Tracing _create_xde_corrected() over the COMBINED tree:")
    print("=" * 78)
    create_xde_traced(root)

    print("\n" + "=" * 78)
    print("Look above for:")
    print("  - Does the car model's own line say 'COMPOUND WITH CHILDREN, "
          "added via AddComponent'? If IsAssembly_s comes back False right")
    print("    after that, that's the smoking gun -- AddComponent did NOT")
    print("    register it as a decomposable assembly, same bug class as")
    print("    the root, just uncorrected at this level.")
    print("  - Any 'SKIPPING this node and its whole subtree' lines --")
    print("    those are children that get silently dropped entirely,")
    print("    which would explain 'geometry visible, nothing in the tree'")
    print("    if the STEP geometry writer still serializes their raw")
    print("    shape data despite the XDE label never being created.")


if __name__ == "__main__":
    main()
