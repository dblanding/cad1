"""
diagnose_strip_parent.py

ROOT CAUSE FOUND: diagnose_preorderiter.py's output showed something
easy to miss but decisive:

    reimported.parent = Compound at 0x..., label(), #children(1)
    reimported.is_root (if available) = False

`reimported` -- the object returned by import_step(), the thing
print_tree()/.descendants/.children all correctly showed as a clean
4-node assembly -- is NOT actually a root node. It has a parent: an
invisible, unlabeled outer Compound with exactly 1 child (reimported
itself), almost certainly a synthetic wrapper import_step() creates
internally (STEP documents can have multiple "free shapes"; wrapping
them in one outer Compound lets import_step() return a single object
either way).

_create_xde()'s loop checks `parent = getattr(node, "parent", None)`
for EVERY node it visits, including whatever node it starts iterating
from. Since reimported.parent is NOT None, _create_xde() treats it as
a non-root node needing an AddComponent() call against a parent_label
looked up from label_map -- but label_map is empty (this is the very
first node), so parent_label comes back null, the
`if parent_label.IsNull(): continue` guard fires, and EVERY node
(reimported and all its descendants) gets silently skipped. This
produces an empty-but-validly-constructed XCAF document, which is
exactly why the writer reports IFSelect_RetVoid ("nothing done")
with no exception and no error message anywhere in the pipeline.

This explains every test result in this entire investigation:
- Why .descendants/print_tree always looked correct (they only walk
  DOWNWARD via .children, never check the node's own .parent)
- Why fresh build123d geometry never hit this (Compound(children=...)
  has parent=None correctly -- nothing wraps it)
- Why every writer-configuration test failed identically regardless
  of settings (the document was empty before the writer ever got
  involved)
- Why the hand-rolled diagnostic succeeded (it called
  shape_tool.AddShape(compound.wrapped, True) directly -- bypassing
  the parent-chain check entirely)

THE FIX: sever reimported's parent relationship before passing it to
export_step(), so _create_xde()'s root-detection logic correctly
identifies it as a genuine root.
"""

import sys
from pathlib import Path

from build123d import import_step
from build123d.exporters3d import _create_xde
from build123d.build_enums import Unit

from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.IGESControl import IGESControl_Controller
from OCP.Interface import Interface_Static
from OCP.STEPCAFControl import STEPCAFControl_Controller, STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_Controller, STEPControl_StepModelType


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
        print("Usage: diagnose_strip_parent.py <input.step>")
        return

    step_path = Path(sys.argv[1])
    reimported = import_step(str(step_path))
    print(f"Imported {step_path}.")
    print(f"reimported.parent BEFORE fix = {reimported.parent!r}\n")

    print("--- Attempt 1: REAL _create_xde(), parent left as-is (expect FAIL) ---")
    doc_unfixed = _create_xde(reimported, Unit.MM, auto_naming=True)
    write_minimal(doc_unfixed, "strip_parent_unfixed.step", "unfixed")

    print("\nSevering reimported.parent (setting it to None)...")
    reimported.parent = None
    print(f"reimported.parent AFTER fix = {reimported.parent!r}\n")

    print("--- Attempt 2: REAL _create_xde(), parent stripped (expect SUCCESS) ---")
    doc_fixed = _create_xde(reimported, Unit.MM, auto_naming=True)
    ok = write_minimal(doc_fixed, "strip_parent_fixed.step", "fixed")

    print("\n--- Conclusion ---")
    if ok:
        print("CONFIRMED AND FIXED. Stripping the parent relationship before")
        print("calling export_step()/_create_xde() resolves the bug.")
        print("\nROOT CAUSE: import_step() returns a Compound whose .parent")
        print("is a synthetic, invisible outer wrapper -- NOT actually the")
        print("root of its own tree, despite every other property (.children,")
        print(".descendants, .label) making it look like a clean root.")
        print("_create_xde() silently produces an empty document when handed")
        print("a 'root' that isn't actually parentless, rather than raising")
        print("an error -- which is the real upstream bug worth reporting.")
        print("\nPRACTICAL FIX for your code: after import_step(), before")
        print("export_step(), do:")
        print("    assembly = import_step(path)")
        print("    assembly.parent = None    # work around the import_step")
        print("                               # wrapper-parent bug")
        print("    export_step(assembly, out_path)")
    else:
        print("Did NOT fix it -- worth checking whether setting .parent on a")
        print("build123d Shape actually rewires anytree's internal structure")
        print("the way we'd expect, or whether a different decoupling method")
        print("is needed (e.g. constructing a fresh Compound with the same")
        print("children rather than mutating the existing one's parent).")


if __name__ == "__main__":
    main()
