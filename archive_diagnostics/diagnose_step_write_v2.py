"""
diagnose_step_write_v2.py

The first diagnostic script tested an OVER-SIMPLIFIED writer setup that
didn't match build123d's real export_step() -- it used a bare
STEPCAFControl_Writer() with defaults, while the real code constructs
it as STEPCAFControl_Writer(session, False) and explicitly turns on
SetColorMode/SetLayerMode/SetNameMode. That's almost certainly why the
earlier diagnostic "succeeded" when the real export_step() fails on
identical data -- it wasn't actually testing the same thing.

This script is a near-exact copy of the REAL installed export_step(),
confirmed against the source printed from your venv, with exactly one
change: it does NOT suppress OCCT's messenger output. build123d sets
SetTraceLevel(Message_Gravity.Message_Fail) specifically to silence
OCCT's warnings/errors during write -- which means whatever OCCT knows
about the real failure has been getting hidden from you the whole
time. This script leaves that channel open so OCCT can tell us why.
"""

import sys
from pathlib import Path

from build123d import import_step

from OCP.APIHeaderSection import APIHeaderSection_MakeHeader
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.IGESControl import IGESControl_Controller
from OCP.Interface import Interface_Static
from OCP.Message import Message, Message_Gravity
from OCP.STEPCAFControl import STEPCAFControl_Controller, STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_Controller, STEPControl_StepModelType
from OCP.TCollection import TCollection_HAsciiString
from OCP.XSControl import XSControl_WorkSession

# Re-use build123d's own _create_xde rather than reimplementing it --
# it's already proven correct by the source dump, no need to guess at
# it a third time.
from build123d.exporters3d import _create_xde


def export_step_verbose(to_export, file_path, unit=None):
    """
    A faithful copy of build123d.exporters3d.export_step(), with the
    messenger-suppression step removed (left commented, for clarity)
    so OCCT's real diagnostic output isn't hidden during writer.Write().
    """
    from build123d.build_enums import Unit
    if unit is None:
        unit = Unit.MM

    doc = _create_xde(to_export, unit, auto_naming=True)

    # --- THE ONLY INTENTIONAL CHANGE vs. the real export_step(): ---
    # messenger = Message.DefaultMessenger_s()
    # for printer in messenger.Printers():
    #     printer.SetTraceLevel(Message_Gravity.Message_Fail)
    # We deliberately do NOT suppress messages here, so OCCT prints
    # whatever it knows about the failure.
    print("[v2] (messenger suppression intentionally skipped -- watch for")
    print("[v2]  OCCT warnings/errors below that build123d normally hides)\n")

    session = XSControl_WorkSession()
    writer = STEPCAFControl_Writer(session, False)
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

    print("[v2] Calling writer.Transfer() ...")
    writer.Transfer(doc, STEPControl_StepModelType.STEPControl_AsIs)

    print("[v2] Calling writer.Write() ...\n")
    status = writer.Write(str(file_path))

    success = status == IFSelect_ReturnStatus.IFSelect_RetDone
    print(f"\n[v2] raw status = {status}")
    print(f"[v2] success = {success}")
    return success


def main():
    if len(sys.argv) < 2:
        print("Usage: diagnose_step_write_v2.py <input.step>")
        return

    step_path = Path(sys.argv[1])
    print(f"Importing {step_path} ...")
    reimported = import_step(str(step_path))
    print("Imported OK.\n")

    print("Attempting export using a faithful copy of the REAL")
    print("export_step(), with messenger suppression turned off...\n")
    print("=" * 70)

    out_path = Path("diag_v2_output.step")
    try:
        success = export_step_verbose(reimported, out_path)
        if success:
            print("\n" + "=" * 70)
            print(f"[v2] SUCCEEDED. Wrote {out_path.resolve()}")
            print("[v2] This would mean the real export_step() SHOULD have")
            print("[v2] worked too -- worth re-running the ORIGINAL")
            print("[v2] step_assembly_poc.py once more to rule out a fluke")
            print("[v2] or environment difference between runs.")
        else:
            print("\n" + "=" * 70)
            print("[v2] FAILED, as expected/reproduced.")
            print("[v2] Look at whatever OCCT printed directly above this")
            print("[v2] line -- with suppression off, the real reason should")
            print("[v2] be visible now (look for 'Fail', '** Exception',")
            print("[v2] or any message mentioning Assembly/Color/Compound).")
    except Exception as e:
        print("\n" + "=" * 70)
        print(f"[v2] Threw an exception rather than a status failure: ")
        print(f"[v2] {type(e).__name__}: {e}")
        print("[v2] This is actually MORE useful than a bare RuntimeError --")
        print("[v2] it likely points at the exact OCCT call that's unhappy.")


if __name__ == "__main__":
    main()
