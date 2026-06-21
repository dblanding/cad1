"""
diagnose_mode_sweep.py

Established so far:
- A re-imported shape fails (RetVoid) ONLY with the real writer config:
  STEPCAFControl_Writer(session, False) + SetColorMode(True) +
  SetLayerMode(True) + SetNameMode(True).
- The exact same re-imported shape succeeds with a bare-bones writer
  that skips all three Set*Mode calls.
- Fresh (never-imported) geometry succeeds with the FULL real writer
  config too -- so it's not the writer config alone, and not the
  shape alone, it's specifically (real config) + (imported shape).
- Twice now we've seen "Warning: Cannot find RI for TopoDS_TSolid"
  (once per solid) on the successful minimal-writer runs. RI most
  likely = "Representation Item", a STEP/AP214 concept. SetLayerMode
  is the one mode most plausibly tied to that warning.

This script sweeps SetColorMode/SetLayerMode/SetNameMode independently
-- all 8 on/off combinations -- on the SAME re-imported shape, using
the real writer construction otherwise. Whichever combination(s) flip
from RetVoid back to RetDone tell us exactly which mode is the
trigger.
"""

import sys
import itertools
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


def try_export(to_export, file_path, color_mode, layer_mode, name_mode):
    """One real export_step()-shaped attempt, with the three Set*Mode
    flags as variables instead of all hard-coded True."""
    doc = _create_xde(to_export, Unit.MM, auto_naming=True)

    session = XSControl_WorkSession()
    writer = STEPCAFControl_Writer(session, False)
    writer.SetColorMode(color_mode)
    writer.SetLayerMode(layer_mode)
    writer.SetNameMode(name_mode)

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
    return status == IFSelect_ReturnStatus.IFSelect_RetDone, status


def main():
    if len(sys.argv) < 2:
        print("Usage: diagnose_mode_sweep.py <input.step>")
        return

    step_path = Path(sys.argv[1])
    print(f"Importing {step_path} ...\n")
    reimported = import_step(str(step_path))
    print("Imported. Sweeping SetColorMode / SetLayerMode / SetNameMode")
    print("combinations on this SAME re-imported shape:\n")

    print(f"{'color':>6} {'layer':>6} {'name':>6}  ->  result")
    print("-" * 40)

    results = []
    for color_mode, layer_mode, name_mode in itertools.product([True, False], repeat=3):
        out_path = Path(
            f"sweep_c{int(color_mode)}_l{int(layer_mode)}_n{int(name_mode)}.step"
        )
        try:
            success, status = try_export(
                reimported, out_path, color_mode, layer_mode, name_mode
            )
        except Exception as e:
            success = False
            status = f"EXCEPTION: {type(e).__name__}: {e}"

        flag = "OK  " if success else "FAIL"
        print(f"{str(color_mode):>6} {str(layer_mode):>6} {str(name_mode):>6}  ->  {flag}  ({status})")
        results.append((color_mode, layer_mode, name_mode, success))

    print("\n--- Summary ---")
    successes = [r for r in results if r[3]]
    failures = [r for r in results if not r[3]]
    print(f"{len(successes)}/8 combinations succeeded, {len(failures)}/8 failed.")

    if successes and failures:
        # Find what's common to all successes that's absent from all failures
        for i, mode_name in enumerate(["color_mode", "layer_mode", "name_mode"]):
            success_vals = {r[i] for r in successes}
            failure_vals = {r[i] for r in failures}
            if len(success_vals) == 1 and success_vals != failure_vals:
                print(f"\n*** {mode_name} = {list(success_vals)[0]} appears necessary")
                print(f"    for success (all successes have it, failures don't).")
        # Also just print the simplest diagnosis directly
        all_true_result = next((r for r in results if r[:3] == (True, True, True)), None)
        if all_true_result and not all_true_result[3]:
            print("\nConfirmed: the REAL config (all three True) fails on this shape,")
            print("matching what diagnose_step_write_v2.py found. Look at the table")
            print("above -- whichever single flag, when flipped to False, turns FAIL")
            print("into OK is the specific trigger.")
    elif not failures:
        print("\nAll 8 combinations succeeded?! That would mean this run's")
        print("re-imported shape behaved differently than the earlier ones --")
        print("worth re-running diagnose_step_write_v2.py again to confirm")
        print("the failure is still reproducible before trusting this result.")
    else:
        print("\nAll 8 combinations failed -- the bug isn't in these three")
        print("flags at all. It's something else common to every real-writer")
        print("config (the session/header construction, or the Controller")
        print("Init_s() calls themselves).")


if __name__ == "__main__":
    main()
