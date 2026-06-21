"""
diagnose_writer_construction.py

The mode sweep (diagnose_mode_sweep.py) proved SetColorMode/
SetLayerMode/SetNameMode are NOT the trigger -- all 8 True/False
combinations failed identically (RetVoid) on the re-imported shape,
even (False, False, False), which is functionally close to the
"minimal" writer that succeeded in an earlier test.

That leaves the writer CONSTRUCTOR FORM itself as a suspect:

    session = XSControl_WorkSession()
    writer = STEPCAFControl_Writer(session, False)   <- REAL, has failed
                                                          every time we've
                                                          tried it on a
                                                          re-imported shape

  versus

    writer = STEPCAFControl_Writer()                  <- MINIMAL, has
                                                          succeeded every
                                                          time we've tried
                                                          it on the SAME
                                                          re-imported shape

This script isolates EXACTLY that one line as the only variable.
Everything else (header construction, Controller.Init_s() calls,
Interface_Static calls, all Set*Mode flags set True) is kept
identical between the two attempts, on the SAME re-imported shape,
in the SAME process. If the parameterless constructor succeeds here
too, that conclusively identifies the (session, False) two-argument
constructor as the trigger.
"""

import sys
from pathlib import Path

from build123d import import_step
from build123d.exporters3d import _create_xde
from build123d.build_enums import Unit

from OCP.APIHeaderSection import APIHeaderSection_MakeHeader
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.IGESControl import IGESControl_Controller
from OCP.Interface import Interface_Static
from OCP.STEPCAFControl import STEPCAFControl_Controller, STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_Controller, STEPControl_StepModelType
from OCP.TCollection import TCollection_HAsciiString
from OCP.XSControl import XSControl_WorkSession


def attempt(to_export, file_path, writer, label):
    """
    Shared tail end of the export sequence -- identical regardless of
    how `writer` was constructed. Only the writer construction differs
    between the two calls in main().
    """
    doc = _create_xde(to_export, Unit.MM, auto_naming=True)

    writer.SetColorMode(True)
    writer.SetLayerMode(True)
    writer.SetNameMode(True)

    header = APIHeaderSection_MakeHeader(writer.Writer().Model())
    if not header.IsDone():
        header = APIHeaderSection_MakeHeader(0)
        header.Apply(writer.Writer().Model())
    if to_export.label:
        header.SetName(TCollection_HAsciiString(to_export.label))
    header.SetOriginatingSystem(TCollection_HAsciiString("build123d"))

    STEPCAFControl_Controller.Init_s()
    STEPControl_Controller.Init_s()
    IGESControl_Controller.Init_s()
    Interface_Static.SetIVal_s("write.surfacecurve.mode", 1)
    Interface_Static.SetIVal_s("write.precision.mode", 0)

    writer.Transfer(doc, STEPControl_StepModelType.STEPControl_AsIs)
    status = writer.Write(str(file_path))
    success = status == IFSelect_ReturnStatus.IFSelect_RetDone
    print(f"[{label}] status={status}  success={success}")
    return success


def main():
    if len(sys.argv) < 2:
        print("Usage: diagnose_writer_construction.py <input.step>")
        return

    step_path = Path(sys.argv[1])
    reimported = import_step(str(step_path))
    print(f"Imported {step_path}.\n")

    print("--- Attempt A: writer = STEPCAFControl_Writer(session, False) ---")
    print("(the REAL constructor form used by build123d's actual export_step)")
    session = XSControl_WorkSession()
    writer_a = STEPCAFControl_Writer(session, False)
    ok_a = attempt(reimported, "writer_construction_A.step", writer_a, "A: session,False")

    print("\n--- Attempt B: writer = STEPCAFControl_Writer() ---")
    print("(the parameterless constructor -- everything else identical)")
    writer_b = STEPCAFControl_Writer()
    ok_b = attempt(reimported, "writer_construction_B.step", writer_b, "B: parameterless")

    print("\n--- Conclusion ---")
    if ok_a and ok_b:
        print("Both succeeded -- the constructor form is NOT the trigger.")
        print("(This would be surprising given prior results -- worth noting")
        print(" if it happens, since it doesn't match earlier runs.)")
    elif not ok_a and ok_b:
        print("CONFIRMED: STEPCAFControl_Writer(session, False) is the trigger.")
        print("The parameterless constructor succeeds on the exact same")
        print("re-imported shape, with every other line identical.")
        print("\nThis points at the explicitly-constructed XSControl_WorkSession")
        print("not being fully equivalent to whatever internal session the")
        print("parameterless constructor sets up on its own -- and that gap")
        print("only matters for documents built from import_step()-derived")
        print("shapes, not fresh ones (per the earlier double_export test).")
    elif ok_a and not ok_b:
        print("Unexpected: A succeeded but B failed -- inverse of what prior")
        print("tests would predict. Worth re-running to rule out a fluke.")
    else:
        print("Both failed -- the constructor form alone isn't the full story,")
        print("something else in the shared `attempt()` tail is implicated.")


if __name__ == "__main__":
    main()
