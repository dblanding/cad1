"""
solid_ops.py

Standalone Extrude/Revolve solid-building functions -- PHASE 3 of the
UI revision (DESIGN_BACKLOG item 33). Extracted from workplane_dialog.py
(WorkplaneDialog._extrude()/_revolve()/_register_raw_shape()) so they
can be called directly from main_app.py's Create 3D menu actions with
no dialog involved, matching KodaCAD's pure menu + status-bar flow.

Both take a WorkPlane (src/workplane.py) whose profile has already
been sketched via the sketch toolbar, and return a build123d Solid
node ready to add to the assembly tree.
"""


def register_raw_shape(raw_shape, name):
    """
    Round-trip a raw TopoDS_Shape through STEP to get a build123d
    Solid that is fully XDE-registered (same as shapes from
    import_step), so export_step() will include it correctly.
    Without this, export_step()'s _create_xde() silently skips
    freshly constructed Solid nodes.
    """
    import tempfile, os
    from build123d import import_step
    from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
    from OCP.IFSelect import IFSelect_RetDone

    tmp = tempfile.NamedTemporaryFile(suffix='.step', delete=False)
    tmp.close()
    try:
        writer = STEPControl_Writer()
        writer.Transfer(raw_shape, STEPControl_AsIs)
        status = writer.Write(tmp.name)
        if status != IFSelect_RetDone:
            raise RuntimeError(f"Temp STEP write failed: {status}")
        # Re-import to get XDE-registered shape
        imported = import_step(tmp.name)
        # import_step returns a Solid with a spurious parent Compound
        # (same bug as documented in step_export_fix.py). The solid
        # may be the imported object itself or its first child.
        children = list(imported.children)
        b3d_solid = children[0] if children else imported
        # Sever the spurious parent -- required so export_step()
        # doesn't skip it (same fix as step_export_fix.py applies to
        # the root assembly node).
        if b3d_solid.parent is not None:
            b3d_solid.parent = None
    finally:
        os.unlink(tmp.name)

    b3d_solid.label = name
    return b3d_solid


def extrude(wp, depth, name):
    """
    Extrude wp's current sketch profile along the workplane normal
    (+wDir). Returns a build123d Solid node.
    Raises RuntimeError if no profile exists or makeWire() fails.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec

    if not wp.edgeList:
        raise RuntimeError(
            "No sketch profile found.\n\n"
            "Use the sketch toolbar to draw a rectangle, circle, or "
            "other profile on the active workplane before extruding."
        )

    if not wp.makeWire():
        raise RuntimeError(
            "makeWire() failed -- the profile may not be closed.\n\n"
            "Tip: a rectangle (Rect tool) or circle (Circle tool) "
            "always forms a closed profile. Lines must form a "
            "closed loop."
        )

    face_bldr = BRepBuilderAPI_MakeFace(wp.wire)
    if not face_bldr.IsDone():
        raise RuntimeError("MakeFace failed.")

    extrude_vec = gp_Vec(wp.wDir) * depth
    prism_shape = BRepPrimAPI_MakePrism(face_bldr.Shape(), extrude_vec).Shape()

    return register_raw_shape(prism_shape, name)


def revolve(wp, p1, p2, name, angle_deg=360.0):
    """
    Revolve wp's current sketch profile about an axis defined by two
    3D points (p1 -> p2 gives the axis direction, p1 is the axis
    origin). Mirrors KodaCAD's revolve() in kodacad.py, with one
    correction: KodaCAD's version references an undefined `loc`
    variable (`loc.Transformation()`) that was clearly meant to be
    `loc = get_inv_loc_of_active_asy()`, copied from its sibling
    extrude() function but never actually added to revolve() -- a
    NameError, confirmed by testing it directly in KodaCAD. That
    transform places a new part into KodaCAD's currently-active
    ASSEMBLY's local frame, a concept BasiCAD's own extrude() above
    doesn't need either (it works correctly with no equivalent step),
    so revolve() doesn't need it -- wp.wire's edges are already built
    in world coordinates (wp.Trsf applied at edge-creation time in
    src/workplane.py).

    Returns a build123d Solid node. Raises RuntimeError if no profile
    exists, makeWire() fails, or the axis points are coincident.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
    from OCP.gp import gp_Ax1, gp_Dir, gp_Vec
    import math

    if not wp.edgeList:
        raise RuntimeError(
            "No sketch profile found.\n\n"
            "Use the sketch toolbar to draw a profile on the active "
            "workplane before revolving."
        )

    if not wp.makeWire():
        raise RuntimeError(
            "makeWire() failed -- the profile may not be closed.\n\n"
            "Tip: a rectangle (Rect tool) or circle (Circle tool) "
            "always forms a closed profile. Lines must form a "
            "closed loop."
        )

    face_bldr = BRepBuilderAPI_MakeFace(wp.wire)
    if not face_bldr.IsDone():
        raise RuntimeError("MakeFace failed.")

    axis_vec = gp_Vec(p1, p2)
    if axis_vec.Magnitude() < 1e-9:
        raise RuntimeError(
            "The two axis points are coincident -- pick two distinct "
            "points to define the revolve axis."
        )
    revolve_axis = gp_Ax1(p1, gp_Dir(axis_vec))
    angle_rad = math.radians(angle_deg)

    revolved_shape = BRepPrimAPI_MakeRevol(
        face_bldr.Shape(), revolve_axis, angle_rad).Shape()

    return register_raw_shape(revolved_shape, name)
