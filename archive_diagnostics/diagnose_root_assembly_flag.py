"""
diagnose_root_assembly_flag.py

Sharpest hypothesis yet, found by re-reading _create_xde()'s per-node
loop carefully:

    for node in PreOrderIter(to_export):
        ...
        parent = getattr(node, "parent", None)
        if parent is None:
            node_label = shape_tool.AddShape(node.wrapped, False)
            #                                               ^^^^^
            # The ROOT node (to_export itself, e.g. your whole
            # assembly Compound) is ALWAYS added with makeAssembly=False,
            # regardless of whether it actually has children.
        else:
            ...
            node_label = shape_tool.AddComponent(parent_label, node.wrapped)
            # children are then added as COMPONENTS of that root label

Per OCCT's own documentation: "Adds a component ... to the assembly.
Note: assembly must be IsAssembly() or IsSimpleShape()". If the root
was added with makeAssembly=False, it may not satisfy that
precondition cleanly once child AddComponent() calls follow it --
producing a document that *looks* fine (no exceptions, every label
non-null) but that the STEP writer silently refuses to write
(RetVoid).

This matters because an OLDER version of build123d's exporter (seen
during earlier research in this conversation) computed this flag
DYNAMICALLY:

    is_assembly = isinstance(to_export, Compound) and len(to_export.children) > 0
    _root_label = shape_tool.AddShape(to_export.wrapped, is_assembly)

...which is exactly what the hand-rolled diagnostic builder did too
(AddShape(compound.wrapped, True) -- hard-coded True, but Doug's
synthetic assembly always has children, so it's equivalent here).

This script builds a CORRECTED _create_xde() -- a copy of the real
one with ONLY that one line changed -- and tests it against the same
re-imported shape and same minimal writer used in every other test,
to see if that one flag is the actual root cause.
"""

import sys
from pathlib import Path

from build123d import import_step, Compound, Shape
from build123d.build_enums import Unit

from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.IGESControl import IGESControl_Controller
from OCP.Interface import Interface_Static
from OCP.STEPCAFControl import STEPCAFControl_Controller, STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_Controller, STEPControl_StepModelType
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDF import TDF_Label
from OCP.TDocStd import TDocStd_Document
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool, XCAFDoc_ShapeTool

from anytree import PreOrderIter


def create_xde_corrected(to_export: Shape, unit: Unit = Unit.MM, auto_naming: bool = False) -> TDocStd_Document:
    """
    A copy of build123d's real _create_xde(), with exactly ONE change:
    the root node's AddShape() call uses a DYNAMIC is_assembly flag
    (True if it's a Compound with children, matching the older
    build123d version's logic) instead of always passing False.

    Everything else -- the per-node loop, AddComponent for children,
    name/color writing, resolve_component_parent_label,
    UpdateAssemblies -- is copied as closely as possible to the real
    installed source to keep this a true single-variable test.
    """
    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    application = XCAFApp_Application.GetApplication_s()
    application.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)
    application.InitDocument(doc)

    from build123d.build_enums import Unit as UnitEnum
    UNITS_PER_METER = {UnitEnum.MM: 1000.0, UnitEnum.M: 1.0, UnitEnum.IN: 39.3701, UnitEnum.CM: 100.0, UnitEnum.FT: 3.28084}
    try:
        XCAFDoc_DocumentTool.SetLengthUnit_s(doc, 1 / UNITS_PER_METER[unit])
    except Exception as e:
        print(f"  (non-fatal: could not set length unit: {e})")

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
            if XCAFDoc_ShapeTool.IsReference_s(node_label):
                referred = TDF_Label()
                if XCAFDoc_ShapeTool.GetReferredShape_s(node_label, referred) and not referred.IsNull():
                    color_tool.SetColor(referred, node.color.wrapped, node_color_type)

    for node in PreOrderIter(to_export):
        if node.wrapped is None:
            continue

        parent = getattr(node, "parent", None)
        if parent is None:
            # --- THE ONLY CHANGE vs. the real _create_xde(): ---
            is_assembly = isinstance(node, Compound) and len(node.children) > 0
            print(f"  [corrected] root node {node.label!r}: "
                  f"AddShape(..., makeAssembly={is_assembly}) "
                  f"[real _create_xde() always uses False here]")
            node_label = shape_tool.AddShape(node.wrapped, is_assembly)
            # --- end of the only change ---
        else:
            parent_label = label_map.get(parent, TDF_Label())
            parent_label = resolve_component_parent_label(parent_label)
            if parent_label.IsNull():
                continue
            node_label = shape_tool.AddComponent(parent_label, node.wrapped)

        if node_label.IsNull():
            continue

        label_map[node] = node_label
        if node.label or node.color is not None:
            set_name_and_color(node, node_label)

    shape_tool.UpdateAssemblies()
    return doc


def write_minimal(doc, file_path, label):
    STEPCAFControl_Controller.Init_s()
    STEPControl_Controller.Init_s()
    IGESControl_Controller.Init_s()
    Interface_Static.SetIVal_s("write.surfacecurve.mode", 1)
    Interface_Static.SetIVal_s("write.precision.mode", 0)

    writer = STEPCAFControl_Writer()
    writer.Transfer(doc, STEPControl_StepModelType.STEPControl_AsIs)
    status = writer.Write(str(file_path))
    success = status == IFSelect_ReturnStatus.IFSelect_RetDone
    print(f"[{label}] status={status}  success={success}")
    return success


def main():
    if len(sys.argv) < 2:
        print("Usage: diagnose_root_assembly_flag.py <input.step>")
        return

    step_path = Path(sys.argv[1])
    reimported = import_step(str(step_path))
    print(f"Imported {step_path}.\n")

    print("--- Building doc with CORRECTED root AddShape flag ---")
    doc_corrected = create_xde_corrected(reimported, Unit.MM, auto_naming=True)
    ok = write_minimal(doc_corrected, "root_flag_corrected.step", "corrected")

    print("\n--- Conclusion ---")
    if ok:
        print("CONFIRMED: the root node's hard-coded makeAssembly=False")
        print("in the real _create_xde() is the bug. Passing True (or")
        print("computing it dynamically based on whether the root has")
        print("children) fixes the write.")
        print("\nThis is a clean, specific, reportable bug: one boolean")
        print("literal in build123d's exporters3d.py, in the per-node")
        print("loop's root-node branch, should be a dynamic")
        print("`isinstance(node, Compound) and len(node.children) > 0`")
        print("check (matching what an older version of the same function")
        print("did) instead of a hard-coded False.")
    else:
        print("Did NOT fix it -- the root assembly flag wasn't the (sole)")
        print("cause. Worth looking at AddComponent's interaction with")
        print("resolve_component_parent_label next, or UpdateAssemblies().")


if __name__ == "__main__":
    main()
