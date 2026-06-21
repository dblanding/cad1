"""
diagnose_step_write.py

build123d's export_step() swallows the real OCCT error and just raises
RuntimeError("Failed to write STEP file") when writer.Write() doesn't
return IFSelect_RetDone. That tells us THAT it failed, not WHY.

This script reproduces the same failing case (import_step -> immediate
export_step, no mutation at all) but talks to OCCT's STEPCAFControl
writer directly, with full message-gravity logging turned on, so OCCT
itself prints the actual diagnostic instead of build123d's generic
catch-all.

Run this exactly the same way you ran the previous script:

    uv run diagnose_step_write.py synthetic_demo.step

(or whatever the re-imported file was called -- if you still have
synthetic_demo.step on disk from the last run, point this at that.
If not, this script will generate a fresh one the same way the
original script did.)
"""

import sys
from pathlib import Path

from build123d import Box, Compound, import_step

from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.Interface import Interface_Static
from OCP.Message import Message, Message_Gravity
from OCP.STEPCAFControl import STEPCAFControl_Controller, STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_Controller, STEPControl_StepModelType
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDocStd import TDocStd_Document
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import XCAFDoc_DocumentTool


def build_minimal_xde_doc_from_compound(compound: Compound) -> TDocStd_Document:
    """
    Re-implements the relevant slice of build123d's internal
    _create_xde(), but stops right before the write call so we can
    inspect / instrument it. Mirrors what exporters3d.py does:
    create the XCAF app+document, get the shape tool, add the shape.
    """
    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    app = XCAFApp_Application.GetApplication_s()
    app.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)
    app.InitDocument(doc)

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

    if compound.wrapped is None:
        raise ValueError("Compound has no wrapped TopoDS_Shape at all -- "
                          "this would explain a write failure: there's "
                          "nothing valid to write.")

    shape_tool.AddShape(compound.wrapped, True)  # True = make assembly aware
    return doc


def enable_verbose_occt_messages():
    """
    Crank OCCT's own message system up so it prints whatever it knows
    about the failure to stdout/stderr, instead of build123d silencing
    it. build123d's exporters3d.py actually does the OPPOSITE of this
    (it raises trace level to suppress messenger output) -- here we
    deliberately leave/ set it verbose.
    """
    messenger = Message.DefaultMessenger_s()
    for printer in messenger.Printers():
        printer.SetTraceLevel(Message_Gravity.Message_Trace)


def build_full_xde_doc_like_build123d(compound: Compound) -> TDocStd_Document:
    """
    This mirrors build123d's REAL _create_xde() more closely than
    build_minimal_xde_doc_from_compound() above -- including the
    per-node name + color writing loop -- specifically to test whether
    the color-writing step is where things go wrong. If THIS fails
    where the minimal version succeeded, that confirms it.
    """
    from anytree import PreOrderIter
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDF import TDF_Label

    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    app = XCAFApp_Application.GetApplication_s()
    app.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)
    app.InitDocument(doc)

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

    is_assembly = isinstance(compound, Compound) and len(compound.children) > 0
    root_label = shape_tool.AddShape(compound.wrapped, is_assembly)

    for node in PreOrderIter(compound):
        if not node.label and node.color is None:
            continue
        print(f"  [build_full] writing label/color for node {node.label!r}, "
              f"color={node.color!r}")
        # Find this node's label in the doc the same rough way
        # build123d does -- this is the part most likely to throw if
        # something about a re-imported node's identity is unusual.
        try:
            # FindShape is an instance method on XCAFDoc_ShapeTool, NOT a
            # static one -- no "_s" suffix. (My earlier guess of
            # FindShape_s was wrong; OCP only appends _s to methods that
            # are genuinely static in the underlying OCCT C++ class.)
            found_label = shape_tool.FindShape(node.wrapped)
            found = not found_label.IsNull()
            print(f"    FindShape found={found}")
            if found and node.label:
                TDataStd_Name.Set_s(found_label, TCollection_ExtendedString(node.label))
            if found and node.color is not None:
                color_tool.SetColor(found_label, node.color.wrapped, 0)  # 0 = ColorType.GEN-ish
        except Exception as e:
            print(f"    *** EXCEPTION on node {node.label!r}: {type(e).__name__}: {e}")
            raise

    return doc


def print_actual_installed_source():
    """
    Stop guessing at what _create_xde() does from documentation
    fragments -- print the REAL installed source from this machine's
    venv. This is ground truth; everything above this function in this
    file is reconstruction-from-memory and should be treated with more
    suspicion than this.
    """
    import build123d.exporters3d as mod
    import inspect
    print(f"build123d.exporters3d loaded from: {mod.__file__}\n")
    try:
        src = inspect.getsource(mod)
    except OSError as e:
        print(f"Could not get source: {e}")
        return
    # Print just the _create_xde function and export_step function,
    # not the whole file.
    lines = src.splitlines()
    capture = False
    depth_marker = None
    out_lines = []
    for line in lines:
        if line.strip().startswith("def _create_xde") or line.strip().startswith("def export_step"):
            capture = True
            out_lines.append(line)
            continue
        if capture:
            if line.strip().startswith("def ") and not line.startswith(" "):
                capture = False
                continue
            out_lines.append(line)
    print("\n".join(out_lines))


def main():
    print("=" * 70)
    print("STEP 0: Printing the ACTUAL installed build123d source for")
    print("_create_xde and export_step -- ground truth, not reconstruction.")
    print("=" * 70)
    print_actual_installed_source()
    print("=" * 70)
    print("END of actual source. Everything below this is the diagnostic")
    print("script's own (possibly imperfect) reconstruction -- treat the")
    print("source above as authoritative if they disagree.")
    print("=" * 70)
    print()

    enable_verbose_occt_messages()

    if len(sys.argv) > 1:
        step_path = Path(sys.argv[1])
        if not step_path.exists():
            print(f"File not found: {step_path}")
            return
    else:
        # Build the same synthetic assembly + export it fresh, exactly
        # like the original script's demo path, so we have a known
        # input even if synthetic_demo.step isn't lying around.
        print("No input file given -- building a fresh synthetic assembly first...")
        from build123d import export_step
        base = Box(20, 20, 5); base.label = "base_plate"
        post = Box(5, 5, 30); post.label = "support_post"
        cap = Box(8, 8, 3); cap.label = "top_cap"
        assembly = Compound(label="demo_assembly", children=[base, post, cap])
        step_path = Path("diag_input.step")
        export_step(assembly, str(step_path))
        print(f"Wrote {step_path}")

    print(f"\nImporting {step_path} via build123d.import_step() ...")
    reimported = import_step(str(step_path))

    print("Imported. Inspecting the Compound and its children for anything")
    print("unusual before we even try to write it back out:\n")

    def inspect(node, depth=0):
        prefix = "  " * depth
        wrapped_ok = node.wrapped is not None
        loc = node.location
        print(f"{prefix}- label={node.label!r} type={type(node).__name__} "
              f"wrapped_is_None={not wrapped_ok} location={loc}")
        if wrapped_ok:
            try:
                # IsNull() on the underlying TopoDS_Shape catches the
                # case where wrapped is a Python object but the C++
                # side TopoDS_Shape it holds is null/empty.
                is_null = node.wrapped.IsNull()
                print(f"{prefix}  wrapped.IsNull() = {is_null}")
            except Exception as e:
                print(f"{prefix}  could not call .IsNull(): {e}")
        # Prime suspect, per build123d's exporters3d.py: _create_xde
        # skips a node only `if not node.label and node.color is None`.
        # All our nodes HAVE labels, so every single one will hit the
        # color-writing branch regardless of whether color is None.
        # If .color is some malformed/unexpected object (rather than
        # cleanly None or a valid Color), that's likely our bug.
        try:
            color_val = node.color
            print(f"{prefix}  color = {color_val!r} (type={type(color_val).__name__})")
        except Exception as e:
            print(f"{prefix}  could not read .color: {type(e).__name__}: {e}")
        for child in node.children:
            inspect(child, depth + 1)

    inspect(reimported)

    print("\nNow attempting a direct OCCT STEPCAFControl write, bypassing")
    print("build123d's export_step() wrapper, with verbose messaging on...\n")

    try:
        doc = build_minimal_xde_doc_from_compound(reimported)
    except Exception as e:
        print(f"FAILED while building the XDE document: {type(e).__name__}: {e}")
        print("\n--> This means the problem is in the re-imported shape ITSELF")
        print("    (e.g. wrapped is None, or the TopoDS_Shape is null/invalid),")
        print("    not in the STEP-writing step. That would point at import_step()")
        print("    producing a Compound whose .wrapped doesn't survive intact.")
        return

    STEPCAFControl_Controller.Init_s()
    STEPControl_Controller.Init_s()
    Interface_Static.SetIVal_s("write.surfacecurve.mode", 1)
    Interface_Static.SetIVal_s("write.precision.mode", 0)  # PrecisionMode.AVERAGE

    writer = STEPCAFControl_Writer()
    writer.Transfer(doc, STEPControl_StepModelType.STEPControl_AsIs)

    out_path = Path("diag_direct_write.step")
    status = writer.Write(str(out_path))

    print(f"\nwriter.Write() raw status = {status}")
    print(f"IFSelect_RetDone           = {IFSelect_ReturnStatus.IFSelect_RetDone}")
    print(f"Match (success)?           = {status == IFSelect_ReturnStatus.IFSelect_RetDone}")

    if status != IFSelect_ReturnStatus.IFSelect_RetDone:
        status_names = {
            IFSelect_ReturnStatus.IFSelect_RetVoid: "RetVoid (nothing done)",
            IFSelect_ReturnStatus.IFSelect_RetDone: "RetDone (success)",
            IFSelect_ReturnStatus.IFSelect_RetError: "RetError",
            IFSelect_ReturnStatus.IFSelect_RetFail: "RetFail",
            IFSelect_ReturnStatus.IFSelect_RetStop: "RetStop",
        }
        print(f"Status meaning: {status_names.get(status, 'unknown')}")
        print("\nIf OCCT printed anything above this line (warnings, errors,")
        print("'** Exception **', entity counts, etc.) -- that's the real")
        print("cause. Please copy EVERYTHING printed by this script, not just")
        print("the final status line.")
        return

    print(f"\nSucceeded! Wrote {out_path.resolve()}")
    print("This means the direct minimal OCCT path works even though")
    print("build123d's export_step() wrapper failed on identical data.")

    print("\n--- Round 2: mirroring build123d's name+color writing loop ---")
    print("(this is the part most likely to be the actual trigger)\n")
    try:
        doc2 = build_full_xde_doc_like_build123d(reimported)
    except Exception as e:
        print(f"\nFAILED while replicating build123d's name/color loop: "
              f"{type(e).__name__}: {e}")
        print("--> This is almost certainly the bug. See the node printed")
        print("    just above the exception -- that's the specific node")
        print("    whose label/color writing breaks.")
        return

    writer2 = STEPCAFControl_Writer()
    writer2.Transfer(doc2, STEPControl_StepModelType.STEPControl_AsIs)
    out_path2 = Path("diag_full_write.step")
    status2 = writer2.Write(str(out_path2))
    print(f"\nRound 2 writer.Write() status = {status2}")
    print(f"Round 2 success? = {status2 == IFSelect_ReturnStatus.IFSelect_RetDone}")
    if status2 != IFSelect_ReturnStatus.IFSelect_RetDone:
        print("--> Confirmed: the name/color writing loop produces a doc")
        print("    that fails to write, even though the bare AddShape-only")
        print("    version succeeds. The bug is in how color/name gets")
        print("    attached to re-imported nodes specifically.")
    else:
        print(f"Succeeded! Wrote {out_path2.resolve()}")
        print("--> Surprising: this means even the full name+color replica")
        print("    works, so the bug must be somewhere else in build123d's")
        print("    actual _create_xde() that this script doesn't replicate")
        print("    exactly (worth diffing against the real source next).")


if __name__ == "__main__":
    main()
