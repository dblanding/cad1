"""
diagnose_create_xde_isolation.py

Re-auditing every prior test in this investigation surfaced something
I'd missed: the ONLY test that ever succeeded in writing a re-imported
shape used a hand-rolled, simplified document builder that skipped
build123d's real _create_xde() function entirely (it just called
shape_tool.AddShape() once, with none of _create_xde()'s per-node
name/color/AddComponent logic). EVERY test that used the real
_create_xde() has failed, regardless of which writer configuration
was paired with it.

That's a different, more specific hypothesis than anything tested so
far: the bug may be inside _create_xde() itself -- it may build an
XCAF document that looks fine under inspection (no exceptions, labels
get set, AddShape/AddComponent all report success) but is actually
malformed in a way the STEP writer correctly refuses to write,
specifically when the input shapes came from import_step().

This script tests EXACTLY that, with everything else held constant:
the SAME minimal writer, the SAME re-imported shape, in the SAME
process -- the only variable is which document-builder produced the
`doc` object being written.
"""

import sys
from pathlib import Path

from build123d import import_step, Compound
from build123d.exporters3d import _create_xde
from build123d.build_enums import Unit

from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.IGESControl import IGESControl_Controller
from OCP.Interface import Interface_Static
from OCP.Message import Message, Message_Gravity
from OCP.STEPCAFControl import STEPCAFControl_Controller, STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_Controller, STEPControl_StepModelType
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDocStd import TDocStd_Document
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import XCAFDoc_DocumentTool


def build_doc_handrolled(compound: Compound) -> TDocStd_Document:
    """
    The simplified builder from the very first diagnostic -- the ONLY
    thing that has ever successfully written a re-imported shape so
    far. Deliberately does NOT replicate _create_xde()'s per-node
    name/color/AddComponent logic -- just one AddShape() call for the
    whole compound, treating it as a single (possibly nested) shape.
    """
    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    app = XCAFApp_Application.GetApplication_s()
    app.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)
    app.InitDocument(doc)
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    shape_tool.AddShape(compound.wrapped, True)
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
        print("Usage: diagnose_create_xde_isolation.py <input.step>")
        return

    step_path = Path(sys.argv[1])
    reimported = import_step(str(step_path))
    print(f"Imported {step_path}.\n")

    print("--- Attempt 1: doc built by the REAL _create_xde() ---")
    doc_real = _create_xde(reimported, Unit.MM, auto_naming=True)
    ok_real = write_minimal(doc_real, "isolation_real_create_xde.step", "real _create_xde")

    print("\n--- Attempt 2: doc built by the hand-rolled simplified builder ---")
    doc_simple = build_doc_handrolled(reimported)
    ok_simple = write_minimal(doc_simple, "isolation_handrolled.step", "hand-rolled")

    print("\n--- Conclusion ---")
    if ok_simple and not ok_real:
        print("CONFIRMED: the bug is inside _create_xde() itself.")
        print("The hand-rolled doc, built from the EXACT SAME re-imported")
        print("shape, writes successfully with the identical minimal writer.")
        print("The real _create_xde() produces a document that LOOKS valid")
        print("(no exceptions during construction) but the STEP writer")
        print("refuses to write it -- something in _create_xde()'s")
        print("per-node loop (AddComponent, TDataStd_Name.Set_s, the")
        print("resolve_component_parent_label logic, or UpdateAssemblies())")
        print("is producing an inconsistent XCAF structure specifically")
        print("for import_step()-derived shapes.")
        print("\nPRACTICAL PATH FORWARD: since the hand-rolled builder only")
        print("does ONE AddShape() call (no per-node names/colors/hierarchy")
        print("preservation), it's not usable as-is for your real app -- but")
        print("it proves the underlying OCCT write path works fine. The fix")
        print("is to build a CORRECTED version of _create_xde() that adds")
        print("names/hierarchy WITHOUT whatever specific call is breaking")
        print("things -- worth testing AddComponent vs the resolve/referred-")
        print("shape logic next, since that's the main structural difference")
        print("between the two builders.")
    elif ok_real and ok_simple:
        print("Both succeeded?! That would be a surprising change from prior")
        print("results -- worth re-running step_assembly_poc.py once more to")
        print("see if this was a fluke or if something upstream changed.")
    elif not ok_real and not ok_simple:
        print("Both failed -- even the hand-rolled minimal builder, which")
        print("succeeded in the very first diagnostic test of this entire")
        print("investigation, now fails too. That would point at something")
        print("about THIS PARTICULAR run/file/environment rather than a")
        print("specific code path -- worth checking whether synthetic_demo.step")
        print("on disk is the same file as in earlier runs, or got")
        print("overwritten/corrupted by an intervening failed export attempt.")
    else:
        print("Real succeeded, hand-rolled failed -- inverse of the working")
        print("hypothesis. Worth a careful re-read before trusting this.")


if __name__ == "__main__":
    main()
