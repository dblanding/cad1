"""
diagnose_header_isolation.py

Status check: we've now ruled out remove_part/add_part, null/invalid
shapes, color, "second export in a process" (on fresh geometry),
all 8 SetColorMode/SetLayerMode/SetNameMode combinations, and the
writer constructor form itself (both (session, False) and the
parameterless form failed once a second _create_xde-based attempt was
made in the same process on the same object -- though v2 already
proved it's not really about "second attempt", since v2 failed on its
one and only attempt too).

The one piece of the real export_step() pipeline that has NEVER been
tested in isolation, on its own, with everything else kept at the
ORIGINAL known-good minimal configuration, is the header construction
block:

    header = APIHeaderSection_MakeHeader(writer.Writer().Model())
    if not header.IsDone():
        header = APIHeaderSection_MakeHeader(0)
        header.Apply(writer.Writer().Model())
    if to_export.label:
        header.SetName(...)
    header.SetOriginatingSystem(...)

This script starts from the EXACT original minimal writer (the one
that succeeded in the very first diagnostic) and adds ONLY this header
block -- no session, no Set*Mode calls -- as the single variable.
This is a FRESH PROCESS, ONE import, ONE export attempt: as clean an
isolation as we've run.
"""

import sys
from pathlib import Path

from build123d import import_step
from build123d.exporters3d import _create_xde
from build123d.build_enums import Unit

from OCP.APIHeaderSection import APIHeaderSection_MakeHeader
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.Interface import Interface_Static
from OCP.STEPCAFControl import STEPCAFControl_Controller, STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_Controller, STEPControl_StepModelType
from OCP.TCollection import TCollection_HAsciiString


def main():
    if len(sys.argv) < 2:
        print("Usage: diagnose_header_isolation.py <input.step>")
        return

    step_path = Path(sys.argv[1])
    reimported = import_step(str(step_path))
    print(f"Imported {step_path}.\n")

    doc = _create_xde(reimported, Unit.MM, auto_naming=True)

    # Exactly the ORIGINAL minimal writer -- no session, no Set*Mode.
    STEPCAFControl_Controller.Init_s()
    STEPControl_Controller.Init_s()
    Interface_Static.SetIVal_s("write.surfacecurve.mode", 1)
    Interface_Static.SetIVal_s("write.precision.mode", 0)

    writer = STEPCAFControl_Writer()

    # --- THE ONLY ADDITION vs. the known-good minimal writer: ---
    print("Constructing header (the one untested variable)...")
    header = APIHeaderSection_MakeHeader(writer.Writer().Model())
    print(f"  header.IsDone() = {header.IsDone()}")
    if not header.IsDone():
        print("  IsDone() was False -- rebuilding header via MakeHeader(0).Apply(...)")
        header = APIHeaderSection_MakeHeader(0)
        header.Apply(writer.Writer().Model())
        print(f"  after rebuild, header.IsDone() = {header.IsDone()}")
    if reimported.label:
        header.SetName(TCollection_HAsciiString(reimported.label))
        print(f"  header.SetName({reimported.label!r})")
    header.SetOriginatingSystem(TCollection_HAsciiString("build123d"))
    print("  header.SetOriginatingSystem('build123d')")
    # --- end of the only addition ---

    writer.Transfer(doc, STEPControl_StepModelType.STEPControl_AsIs)
    status = writer.Write("diag_header_isolation.step")
    success = status == IFSelect_ReturnStatus.IFSelect_RetDone

    print(f"\nstatus = {status}")
    print(f"success = {success}")

    if success:
        print("\nHeader construction alone does NOT cause the failure.")
        print("That's now everything in the real pipeline ruled out")
        print("individually except the EXACT combination/order of all")
        print("pieces together -- worth trying the real export_step()")
        print("itself one more time, completely fresh, no diagnostics")
        print("around it at all, to confirm the failure still reproduces")
        print("outside of any instrumented script.")
    else:
        print("\nCONFIRMED: header construction is the trigger.")
        print("Specifically, look at the header.IsDone() values printed")
        print("above -- if IsDone() was False and the rebuild path ran,")
        print("that rebuild ('# As in OCCT 7.9.x' in build123d's own")
        print("source comment) may itself be incomplete/buggy for this")
        print("OCCT version, OR the rebuilt header may end up in a state")
        print("the writer doesn't accept once Transfer()/Write() runs.")


if __name__ == "__main__":
    main()
