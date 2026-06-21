"""
diagnose_real_create_xde_traced.py

STRATEGY CHANGE: every hand-retyped reconstruction of _create_xde()
tried so far has been a source of risk in itself -- one already had a
typo (FindShape_s), and the "corrected root flag" version didn't fix
anything, which could mean the theory was wrong OR that some other
transcription detail was off. Either way, hand-copying the function's
body has been an unreliable technique.

This script takes a different approach: it calls build123d's REAL,
COMPLETELY UNMODIFIED _create_xde() function -- no retyping, no
reconstruction -- but with the underlying OCP shape_tool/color_tool
methods monkey-patched to log every call, its arguments, and its
return value, before delegating to the real implementation.

This sidesteps transcription risk entirely. We are now watching the
REAL function execute, call by call, exactly as build123d wrote it on
your installed version, with zero risk of me having mistyped
something. Whatever differs between a successful write and a failed
one should be visible directly in this log.
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
from OCP.XCAFDoc import XCAFDoc_ShapeTool, XCAFDoc_ColorTool


def patch_method(cls, name):
    """
    Wrap cls.<name> so every call prints its arguments and return
    value, then delegates to the ORIGINAL unpatched method. Works on
    OCP-bound classes as long as the method is accessible as a class
    attribute (true for the XCAFDoc_ShapeTool/ColorTool methods we
    care about, which are typically static-ish bound methods).
    """
    original = getattr(cls, name)

    def wrapper(*args, **kwargs):
        # args[0] is `self` for instance methods bound this way in OCP
        printable_args = args[1:] if args else args
        result = original(*args, **kwargs)
        try:
            result_repr = repr(result)
            if hasattr(result, "IsNull"):
                result_repr += f" (IsNull={result.IsNull()})"
        except Exception:
            result_repr = "<unrepr-able>"
        print(f"    CALL {cls.__name__}.{name}(args={printable_args!r}) "
              f"-> {result_repr}")
        return result

    try:
        setattr(cls, name, wrapper)
        return original
    except (AttributeError, TypeError) as e:
        print(f"  (could not patch {cls.__name__}.{name}: {e})")
        return None


def main():
    if len(sys.argv) < 2:
        print("Usage: diagnose_real_create_xde_traced.py <input.step>")
        return

    step_path = Path(sys.argv[1])
    reimported = import_step(str(step_path))
    print(f"Imported {step_path}.\n")

    print("Patching XCAFDoc_ShapeTool / XCAFDoc_ColorTool methods to log calls...")
    methods_to_trace = [
        (XCAFDoc_ShapeTool, "AddShape"),
        (XCAFDoc_ShapeTool, "AddComponent"),
        (XCAFDoc_ShapeTool, "UpdateAssemblies"),
        (XCAFDoc_ColorTool, "SetColor"),
    ]
    originals = {}
    for cls, name in methods_to_trace:
        originals[(cls, name)] = patch_method(cls, name)

    print("\nCalling the REAL, UNMODIFIED build123d._create_xde()...\n")
    print("-" * 70)
    try:
        doc = _create_xde(reimported, Unit.MM, auto_naming=True)
        print("-" * 70)
        print("\n_create_xde() returned without raising. Now attempting to")
        print("write the resulting doc with the known-good minimal writer...\n")
    except Exception as e:
        print("-" * 70)
        print(f"\n_create_xde() ITSELF raised: {type(e).__name__}: {e}")
        print("(this would be new information -- it hasn't thrown before)")
        return
    finally:
        # Restore originals so the write step below isn't affected by
        # the logging wrappers (keeps this test as clean as possible).
        for (cls, name), original in originals.items():
            if original is not None:
                setattr(cls, name, original)

    STEPCAFControl_Controller.Init_s()
    STEPControl_Controller.Init_s()
    IGESControl_Controller.Init_s()
    Interface_Static.SetIVal_s("write.surfacecurve.mode", 1)
    Interface_Static.SetIVal_s("write.precision.mode", 0)

    writer = STEPCAFControl_Writer()
    writer.Transfer(doc, STEPControl_StepModelType.STEPControl_AsIs)
    status = writer.Write("traced_output.step")
    success = status == IFSelect_ReturnStatus.IFSelect_RetDone

    print(f"Write status = {status}")
    print(f"Success = {success}")
    print("\nLook at the CALL log above. In particular:")
    print("  - Does AddComponent get called with a parent label that")
    print("    came from an AddShape call, and if so what did that")
    print("    AddShape call's makeAssembly argument look like?")
    print("  - Are there fewer AddShape/AddComponent calls than you'd")
    print("    expect for a 3-child assembly (4 total: 1 root + 3")
    print("    children)? A missing call would point at the PreOrderIter")
    print("    walk or the `if node.wrapped is None: continue` /")
    print("    `if parent_label.IsNull(): continue` guards silently")
    print("    skipping a node it shouldn't.")


if __name__ == "__main__":
    main()
