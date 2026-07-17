"""
step_export_fix.py

WORKAROUND for TWO confirmed build123d export_step() bugs.

BUG 1 (original fix, still needed):
  build123d's import_step() returns a Compound whose .parent attribute
  is a hidden outer wrapper -- NOT None, even though every other property
  (.children, .label, etc.) makes it look like a root node.

  build123d's export_step() -> _create_xde() walks the tree with
  anytree's PreOrderIter and checks getattr(node, "parent", None) on
  every node. Because the "root" returned by import_step() has a parent,
  _create_xde() treats it as a child needing a parent label that was
  never registered -- every node is silently skipped, producing an empty
  XCAF document. The STEP writer reports IFSelect_RetVoid with no error.

  FIX: sever the spurious parent before exporting (assembly.parent = None).

BUG 2 (new, found investigating a 198MB import -> 1.1GB export size
explosion after RMB-deleting most of the assembly's parts):
  _create_xde()'s per-node loop ALWAYS registers the root node with
  `shape_tool.AddShape(node.wrapped, False)` -- makeAssembly is
  hard-coded False, regardless of whether the root actually is an
  assembly with children. Every CHILD is then added via
  `shape_tool.AddComponent(parent_label, node.wrapped)`, but per
  OCCT's own docs, AddComponent's target "must be IsAssembly() or
  IsSimpleShape()" -- a root registered as makeAssembly=False is a
  simple shape, not a genuine assembly, so the proper hierarchical/
  component structure AddComponent is supposed to build isn't
  established correctly. Confirmed empirically on a real exported
  file: 443 independent solid bodies (MANIFOLD_SOLID_BREP) written
  under only 3 PRODUCT_DEFINITION entries and 2
  NEXT_ASSEMBLY_USAGE_OCCURRENCE entries -- i.e. almost the entire
  multi-level part/assembly hierarchy collapsed into a handful of
  monolithic, non-decomposed geometry blobs instead of a proper
  instanced structure. Every one of those 443 solids then has to be
  written out in full, independently, with none of the size-saving
  instance/reference sharing STEP assemblies normally rely on for
  repeated parts -- which is almost certainly why the file exploded
  in size despite most of the actual parts having been deleted first.

  FIX: use a corrected XDE-document builder that computes the root's
  makeAssembly flag DYNAMICALLY (True if it's a Compound with
  children) instead of hard-coding False -- matching what an OLDER
  version of build123d's own exporter did correctly. This can't be
  fixed by calling through to the real (buggy) _create_xde(), so this
  module now builds its own XDE document and writes it directly,
  rather than delegating to build123d.export_step(). Both the
  corrected-flag logic and the writer setup below are adapted
  directly from archive_diagnostics/diagnose_root_assembly_flag.py
  and diagnose_step_write_v2.py, which already isolated and
  empirically confirmed each piece individually (the corrected flag
  fixed the RetVoid bug in that investigation; the writer setup is a
  faithful, source-confirmed copy of build123d's real one) -- this
  module combines both rather than re-deriving either from scratch.

USAGE:
  from step_export_fix import export_step
  # Use exactly like build123d's export_step() -- same signature.

CAVEATS (only matter if you ever pass non-default values -- no
current caller in this codebase does):
  - `write_pcurves=False` and non-AVERAGE `precision_mode` values are
    accepted for signature compatibility but are NOT fully wired into
    this reimplementation (see the hard-coded Interface_Static calls
    below) -- a warning is printed if you pass a non-default
    precision_mode.
  - `timestamp` is accepted but not applied to the STEP header (which
    gets whatever default APIHeaderSection_MakeHeader generates).

NOT TESTED against a running build123d/OCP install as of this write --
built directly from two already-tested diagnostic scripts in
archive_diagnostics/, combined for the first time here. Please verify
against a real large assembly (ideally the same one that surfaced
this) before relying on it: check the resulting file's PRODUCT_DEFINITION
/ NEXT_ASSEMBLY_USAGE_OCCURRENCE / MANIFOLD_SOLID_BREP counts look
proportionate now, and confirm the file size is back in a sane range.
"""

from os import PathLike
from io import BytesIO
from datetime import datetime
from typing import BinaryIO

from build123d import Shape, Compound
from build123d.build_enums import Unit, PrecisionMode


def _create_xde_corrected(to_export, unit: Unit, auto_naming: bool = True):
    """
    Builds an XCAFDoc/TDocStd_Document XDE document from `to_export`,
    same job as build123d's real (buggy) _create_xde() -- with the
    ONE fix: the root node's AddShape() call uses a DYNAMIC
    is_assembly flag instead of a hard-coded False. See BUG 2 above.
    Adapted from archive_diagnostics/diagnose_root_assembly_flag.py's
    create_xde_corrected(), which already confirmed this fixes the
    root-cause structural issue.
    """
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDF import TDF_Label
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool, XCAFDoc_ShapeTool
    from anytree import PreOrderIter

    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    application = XCAFApp_Application.GetApplication_s()
    application.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)
    application.InitDocument(doc)

    UNITS_PER_METER = {
        Unit.MM: 1000.0, Unit.M: 1.0, Unit.IN: 39.3701,
        Unit.CM: 100.0, Unit.FT: 3.28084,
    }
    try:
        XCAFDoc_DocumentTool.SetLengthUnit_s(doc, 1 / UNITS_PER_METER[unit])
    except Exception as e:
        print(f"[step_export_fix] (non-fatal: could not set length unit: {e})")

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
            # *** THE FIX (vs. the real, buggy _create_xde()) ***
            # Real build123d always passes False here. We compute it
            # from whether this root actually has children -- an
            # assembly's root should register as an assembly, not a
            # single opaque "simple shape" blob that swallows all its
            # descendants' structure. See BUG 2 in the module
            # docstring for what goes wrong if this is wrong.
            is_assembly = isinstance(node, Compound) and len(node.children) > 0
            node_label = shape_tool.AddShape(node.wrapped, is_assembly)
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


def export_step(
    to_export: Shape,
    file_path: PathLike | str | bytes | BytesIO | BinaryIO,
    unit: Unit = Unit.MM,
    write_pcurves: bool = True,
    precision_mode: PrecisionMode = PrecisionMode.AVERAGE,
    *,
    timestamp: str | datetime | None = None,
) -> bool:
    """
    Drop-in replacement for build123d.export_step() that works around
    two confirmed bugs (see module docstring) -- the spurious-parent
    bug (fixed by severing it before export, as before) AND the
    root-assembly-flag bug (fixed by using our own corrected XDE
    document builder and writer, rather than delegating to
    build123d's real export_step()/_create_xde()).
    """
    from OCP.APIHeaderSection import APIHeaderSection_MakeHeader
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.IGESControl import IGESControl_Controller
    from OCP.Interface import Interface_Static
    from OCP.Message import Message, Message_Gravity
    from OCP.STEPCAFControl import STEPCAFControl_Controller, STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_Controller, STEPControl_StepModelType
    from OCP.TCollection import TCollection_HAsciiString
    from OCP.XSControl import XSControl_WorkSession

    # BUG 1 fix: sever the spurious parent from import_step(), same as before.
    if getattr(to_export, "parent", None) is not None:
        to_export.parent = None

    if precision_mode != PrecisionMode.AVERAGE:
        print(f"[step_export_fix] WARNING: precision_mode={precision_mode!r} "
              f"was requested, but this reimplementation only honors the "
              f"default (AVERAGE) -- no current caller in this codebase "
              f"passes a non-default value, so this wasn't wired up. "
              f"Proceeding with the default behavior.")

    # BUG 2 fix: build the XDE document ourselves, with the corrected
    # root-assembly flag, instead of build123d's real _create_xde().
    doc = _create_xde_corrected(to_export, unit, auto_naming=True)

    # From here down: a faithful copy of build123d's real writer setup
    # (confirmed against source in archive_diagnostics/diagnose_step_write_v2.py),
    # with messenger suppression restored (the diagnostic deliberately
    # left it off to reveal hidden OCCT errors during debugging; normal
    # operation should suppress it, same as the real export_step()).
    messenger = Message.DefaultMessenger_s()
    for printer in messenger.Printers():
        printer.SetTraceLevel(Message_Gravity.Message_Fail)

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
    Interface_Static.SetIVal_s("write.surfacecurve.mode", 1 if write_pcurves else 0)
    Interface_Static.SetIVal_s("write.precision.mode", 0)

    writer.Transfer(doc, STEPControl_StepModelType.STEPControl_AsIs)
    status = writer.Write(str(file_path))

    return status == IFSelect_ReturnStatus.IFSelect_RetDone


if __name__ == "__main__":
    # Self-test: import a STEP file, export it right back out through
    # the fix, confirm success AND sanity-check the resulting
    # PRODUCT_DEFINITION / NEXT_ASSEMBLY_USAGE_OCCURRENCE /
    # MANIFOLD_SOLID_BREP counts look proportionate (the specific
    # regression this rewrite targets).
    import sys
    from pathlib import Path
    from build123d import import_step

    if len(sys.argv) < 2:
        print("Usage: step_export_fix.py <input.step>")
        sys.exit(0)

    step_path = Path(sys.argv[1])
    print(f"Importing {step_path} ...")
    reimported = import_step(str(step_path))
    print(f"reimported.parent before fix = {reimported.parent!r}")

    out_path = Path("step_export_fix_output.step")
    print(f"Exporting to {out_path} via the fixed export_step()...")
    ok = export_step(reimported, out_path)
    print(f"Success: {ok}")
    if ok:
        text = out_path.read_text(errors="replace")
        n_prod = text.count("PRODUCT_DEFINITION(")
        n_nauo = text.count("NEXT_ASSEMBLY_USAGE_OCCURRENCE(")
        n_solid = text.count("MANIFOLD_SOLID_BREP(")
        size_mb = out_path.stat().st_size / (1024 * 1024)
        print(f"Wrote {out_path.resolve()}  ({size_mb:.1f} MB)")
        print(f"PRODUCT_DEFINITION: {n_prod}   "
              f"NEXT_ASSEMBLY_USAGE_OCCURRENCE: {n_nauo}   "
              f"MANIFOLD_SOLID_BREP: {n_solid}")
        print("If PRODUCT_DEFINITION is still tiny relative to "
              "MANIFOLD_SOLID_BREP, the fix didn't fully take -- "
              "worth re-checking against archive_diagnostics/ before "
              "concluding this is solved.")
