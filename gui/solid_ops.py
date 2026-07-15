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


def cut_active_part(wp, work_shape, depth):
    """
    Mill: cut wp's current sketch profile INTO work_shape (an existing
    part's raw TopoDS_Shape), extruding in the -wDir direction.
    Mirrors KodaCAD's mill(). Returns the new raw TopoDS_Shape --
    caller is responsible for assigning it back to the node (see
    MainWindow._apply_shape_to_node in main_app.py).
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.gp import gp_Vec

    if not wp.edgeList:
        raise RuntimeError(
            "No sketch profile found.\n\n"
            "Use the sketch toolbar to draw a profile on the active "
            "workplane before milling."
        )
    if not wp.makeWire():
        raise RuntimeError(
            "makeWire() failed -- the profile may not be closed."
        )
    face_bldr = BRepBuilderAPI_MakeFace(wp.wire)
    if not face_bldr.IsDone():
        raise RuntimeError("MakeFace failed.")

    tool_vec = gp_Vec(wp.wDir) * -depth
    tool = BRepPrimAPI_MakePrism(face_bldr.Shape(), tool_vec).Shape()
    result = BRepAlgoAPI_Cut(work_shape, tool).Shape()
    return result


def pull_active_part(wp, work_shape, length):
    """
    Pull (boss): fuse wp's current sketch profile extrusion ONTO
    work_shape (an existing part's raw TopoDS_Shape), extruding in
    the +wDir direction. Mirrors KodaCAD's pull(). Returns the new
    raw TopoDS_Shape.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from OCP.gp import gp_Vec

    if not wp.edgeList:
        raise RuntimeError(
            "No sketch profile found.\n\n"
            "Use the sketch toolbar to draw a profile on the active "
            "workplane before pulling."
        )
    if not wp.makeWire():
        raise RuntimeError(
            "makeWire() failed -- the profile may not be closed."
        )
    face_bldr = BRepBuilderAPI_MakeFace(wp.wire)
    if not face_bldr.IsDone():
        raise RuntimeError("MakeFace failed.")

    tool_vec = gp_Vec(wp.wDir) * length
    tool = BRepPrimAPI_MakePrism(face_bldr.Shape(), tool_vec).Shape()
    result = BRepAlgoAPI_Fuse(work_shape, tool).Shape()
    return result


def apply_fillet(work_shape, picked_edges, radius):
    """
    Fillet (blend) the given edges of work_shape (an existing part's
    raw TopoDS_Shape) with the given radius. Ported from the retired
    FilletDialog._apply_fillet().

    picked_edges are TopoDS_Edge objects from the VIEWPORT's AIS
    display topology, which -- after a STEP round-trip -- are
    different C++ objects than the edges actually in work_shape, even
    when geometrically identical. BRepFilletAPI_MakeFillet requires
    edges that are genuinely IN the shape being filleted, so each
    picked edge is matched to the closest edge in work_shape by
    midpoint distance (1mm tolerance) before being added.

    Returns the new raw TopoDS_Shape. Raises RuntimeError if no
    picked edge could be matched, or if the fillet build fails
    (e.g. radius too large for adjacent face widths).
    """
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopoDS import TopoDS
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet

    mk = BRepFilletAPI_MakeFillet(work_shape)

    shape_edges = []
    exp = TopExp_Explorer(work_shape, TopAbs_EDGE)
    while exp.More():
        edge = TopoDS.Edge_s(exp.Current())
        try:
            curve = BRepAdaptor_Curve(edge)
            mid_param = (curve.FirstParameter() + curve.LastParameter()) / 2.0
            mid_pt = curve.Value(mid_param)
            shape_edges.append((edge, mid_pt))
        except Exception:
            pass
        exp.Next()

    matched = 0
    for picked_edge in picked_edges:
        try:
            curve = BRepAdaptor_Curve(picked_edge)
            mid_param = (curve.FirstParameter() + curve.LastParameter()) / 2.0
            picked_mid = curve.Value(mid_param)
        except Exception:
            continue

        best_edge = None
        best_dist = 1.0  # mm tolerance
        for shape_edge, shape_mid in shape_edges:
            dist = picked_mid.Distance(shape_mid)
            if dist < best_dist:
                best_dist = dist
                best_edge = shape_edge

        if best_edge is not None:
            mk.Add(radius, best_edge)
            matched += 1
        else:
            print(f"[apply_fillet] Warning: no matching edge found near "
                  f"{picked_mid.X():.2f}, {picked_mid.Y():.2f}, "
                  f"{picked_mid.Z():.2f}")

    if matched == 0:
        raise RuntimeError(
            "None of the selected edges could be matched to edges in "
            "the active part's topology."
        )

    print(f"[apply_fillet] Matched {matched}/{len(picked_edges)} edges.")
    mk.Build()
    if not mk.IsDone():
        raise RuntimeError(
            "BRepFilletAPI_MakeFillet failed. Check that the radius "
            "is not larger than adjacent face widths."
        )
    return mk.Shape()


def apply_shell(active_part_node, picked_faces, thickness):
    """
    Shell active_part_node's shape, removing picked_faces and leaving
    a wall of `thickness`. Ported from the retired
    ShellDialog._apply_shell().

    picked_faces are TopoDS_Face objects from the VIEWPORT's AIS
    display topology, which -- after a STEP round-trip -- are
    different C++ objects than the faces actually in the node's
    wrapped shape, even when geometrically identical. Each picked face
    is matched to the closest face in the node's shape by
    center-of-mass distance (1mm tolerance) before being added to the
    faces-to-remove list, same technique as apply_fillet()'s
    midpoint matching.

    Takes the build123d node (not a raw shape) because, unlike
    cut_active_part()/pull_active_part(), the face matching needs
    BOTH the node's local shape (passed to MakeThickSolid) and its
    world-space transform (for accurate center-of-mass comparison
    against the picked faces, which are in world/viewport coordinates).

    Returns the new raw TopoDS_Shape. Raises RuntimeError if no picked
    face could be matched, or if the shell build fails (e.g.
    thickness too large for the part geometry).
    """
    from OCP.TopoDS import TopoDS
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopTools import TopTools_ListOfShape
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeThickSolid

    work_shape = active_part_node.wrapped
    try:
        global_loc = active_part_node.global_location.wrapped
        world_shape = work_shape.Located(global_loc)
    except Exception:
        world_shape = work_shape

    shape_faces = []
    local_exp = TopExp_Explorer(work_shape, TopAbs_FACE)
    world_exp = TopExp_Explorer(world_shape, TopAbs_FACE)
    while local_exp.More() and world_exp.More():
        face_local = TopoDS.Face_s(local_exp.Current())
        face_world = TopoDS.Face_s(world_exp.Current())
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face_world, props)
        cog = props.CentreOfMass()
        shape_faces.append((face_local, cog))
        local_exp.Next()
        world_exp.Next()

    faces_to_remove = TopTools_ListOfShape()
    matched = 0
    for picked_face in picked_faces:
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(picked_face, props)
        picked_cog = props.CentreOfMass()

        best_face = None
        best_dist = 1.0  # mm tolerance
        for shape_face, shape_cog in shape_faces:
            dist = picked_cog.Distance(shape_cog)
            if dist < best_dist:
                best_dist = dist
                best_face = shape_face

        if best_face is not None:
            faces_to_remove.Append(best_face)
            matched += 1
        else:
            print(f"[apply_shell] Warning: no matching face found near "
                  f"{picked_cog.X():.2f}, {picked_cog.Y():.2f}, "
                  f"{picked_cog.Z():.2f}")

    if matched == 0:
        raise RuntimeError(
            "None of the selected faces could be matched to faces in "
            "the active part's topology."
        )

    print(f"[apply_shell] Matched {matched}/{len(picked_faces)} faces.")

    # Negative thickness shells inward (same as KodaCAD)
    mk = BRepOffsetAPI_MakeThickSolid()
    mk.MakeThickSolidByJoin(work_shape, faces_to_remove, -thickness, 1.0e-3)
    mk.Build()
    if not mk.IsDone():
        raise RuntimeError(
            "BRepOffsetAPI_MakeThickSolid failed. Check that the "
            "thickness is not larger than the part geometry."
        )
    return mk.Shape()
