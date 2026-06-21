"""
diagnose_double_export.py

The most important new clue: diagnose_step_write_v2.py reproduced the
real failure using the REAL export_step() writer setup, and got
IFSelect_RetVoid ("nothing done") rather than RetFail/RetError. RetVoid
usually means a precondition wasn't met BEFORE real write work started
-- which smells like global/static OCCT state (the various
*Controller.Init_s() calls configure process-wide state, not
per-call state).

This script tests a sharp, decisive hypothesis: does export_step()
simply fail on the SECOND call within the same Python process,
regardless of STEP import being involved at all? If calling
export_step() twice on completely fresh, never-imported geometry
ALSO fails the second time, that proves this is a process-state bug
in build123d itself -- nothing to do with your STEP files, your data,
or anything specific to re-imported shapes. That would be the
simplest possible explanation, and very easy to work around (e.g.
always export in a fresh subprocess) once confirmed.

No STEP import happens in this script at all -- pure, fresh geometry,
twice, in one process.
"""

from pathlib import Path
from build123d import Box, Compound, export_step


def main():
    box1 = Box(10, 10, 10)
    box1.label = "first_box"

    print("--- Export #1 (completely fresh geometry, first call in process) ---")
    try:
        export_step(box1, "double_export_1.step")
        print("Export #1: SUCCESS\n")
    except RuntimeError as e:
        print(f"Export #1: FAILED unexpectedly: {e}")
        print("(if even the FIRST export in a fresh process fails here,")
        print(" that changes the picture entirely -- stop and report this)")
        return

    box2 = Box(5, 5, 5)
    box2.label = "second_box"

    print("--- Export #2 (different fresh geometry, SECOND call in same process) ---")
    try:
        export_step(box2, "double_export_2.step")
        print("Export #2: SUCCESS")
        print("\n=> Both exports succeeded. The 'second export in a process'")
        print("   hypothesis is WRONG. The bug must be specific to shapes")
        print("   that came from import_step(), not just 'export called twice'.")
    except RuntimeError as e:
        print(f"Export #2: FAILED: {e}")
        print("\n=> CONFIRMED: export_step() fails on the second call within")
        print("   the same process, even on completely fresh geometry that")
        print("   was never imported from STEP. This is a process-global-state")
        print("   bug in build123d itself (almost certainly in the repeated")
        print("   STEPCAFControl_Controller.Init_s()/STEPControl_Controller.Init_s()")
        print("   calls, or in re-using/re-initializing something that OCCT")
        print("   only expects to be set up once per process).")
        print("\n   PRACTICAL IMPLICATION: in your real app, you likely need to")
        print("   either (a) only call export_step() once per process and do")
        print("   subsequent exports in a fresh subprocess, or (b) find the")
        print("   specific piece of global state that needs resetting between")
        print("   calls (worth filing a build123d GitHub issue with this exact")
        print("   repro -- it's about as minimal as a repro gets).")


if __name__ == "__main__":
    main()
