"""
pose.py

POSITIONING MATH -- geometry for the Mate/Align 3-2-1 workflow.

All public functions take PickResult named tuples and return build123d
Location objects representing the move to apply to the moving part.

A PickResult is created by resolve_pick() from an AIS selection:
  face        -> .point = face center, .direction = face normal
  edge        -> .point = edge midpoint, .direction = edge direction
  cylinder    -> .point = axis center, .direction = axis direction
                 .label starts with "cylinder axis"

FUNCTIONS:

  resolve_pick(shape, shape_type, node)
    Convert an AIS selection into a PickResult with point + direction.

  find_intersection_line(P1, N1, P2, N2)
    Return (point, direction) of intersection line of two planes,
    or None if planes are parallel.

  compute_step1_move(pick1, pick2, mate)
    Step 1: rotate about intersection line of face planes until flush.
    mate=True: normals become opposed. mate=False: normals become parallel.
    PARALLEL PLANES: when N1 || N2, find_intersection_line returns None.
    Four sub-cases: N1~=N2 mate needs 180-deg rotation + translation;
    N1~=-N2 mate needs only translation; opposite for align.

  compute_step2_move(pick1, pick2, mated_normal)
    Step 2: rotate within mated plane until D1_in_plane || D2_in_plane,
    then translate along D2 to close the gap. Target is parallel (not
    anti-parallel) -- align means same direction, not opposed.

  compute_step3_move(pick1, pick2, mated_normal, wall_normal,
                     step2_type, pivot)
    Step 3: remove the last remaining DOF.
    wall: translate along free_dir = mated_normal x wall_normal only.
    hole: rotate about pivot (hole center from Step 2) along mated_normal.

  compute_align_axis_move(pick1, pick2)
    Align Axis: make cylinder axes parallel and coincident.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from build123d import (
    Plane,
    Location,
    Vector,
    Axis,
    Vertex,
    Edge,
    Face,
    Shape,
    GeomType,
)


# --------------------------------------------------------------------------
# 0. Circle fitting -- a fallback for edges that are visually circular
#    but not classified as geom_type "CIRCLE"
# --------------------------------------------------------------------------
# CONFIRMED REAL, NOT HYPOTHETICAL: real-world STEP files don't
# reliably encode circular edges with geom_type "CIRCLE". Verified via
# diagnose_circle_geomtype.py -- our OWN export_step()/import_step()
# round-trip preserves CIRCLE correctly on native build123d geometry,
# but as1-oc-214.stp (a different STEP writer -- OCCT 6.1's own, per
# its file header) encodes the rod's circular end as geom_type
# "BSPLINE" despite it visually being a circle. A hard "must be
# CIRCLE" check would reject exactly the picks a user would
# reasonably expect to work (a bolt hole, a shaft end) on files from
# other CAD systems/exporters.

@dataclass(frozen=True)
class CircleFit:
    """Result of fitting a circle to a sampled edge: center, normal
    (axis direction), and radius, plus a residual error measure so
    callers can decide whether the fit is good enough to trust."""
    center: Vector
    normal: Vector
    radius: float
    max_residual: float  # largest deviation of any sample point from the fitted circle


def _fit_circle_to_edge(edge: Edge, num_samples: int = 12) -> CircleFit:
    """
    Sample points along ANY edge and fit a circle to them via least
    squares, regardless of the edge's geom_type.

    METHOD: sample `num_samples` points along the edge via
    position_at() (the same documented, normalized-arc-length method
    already used elsewhere in this module for edge_direction), fit a
    plane through them (centroid + a normal estimated from averaged
    cross products -- simple and dependency-free, no SVD needed),
    project into that plane, then fit a 2D circle via the standard
    Kasa least-squares algebraic method (a closed-form 3x3 linear
    solve, not iterative optimization).

    Returns a CircleFit with max_residual so callers can decide
    whether to trust it -- a genuinely circular edge (even if
    misclassified as BSPLINE) will have a tiny residual; a genuinely
    non-circular curve that only looks roughly circular will have a
    larger one.
    """
    points = [edge.position_at(i / num_samples) for i in range(num_samples)]

    # Centroid -- our best initial estimate of "center-ish".
    centroid = sum(points, Vector(0, 0, 0)) * (1.0 / len(points))

    # Estimate the plane normal via the average of consecutive
    # cross products around the centroid -- a simple, dependency-free
    # way to get a representative normal for a roughly-planar point
    # set without pulling in a full SVD/eigenvalue solver.
    normal_accum = Vector(0, 0, 0)
    for i in range(len(points)):
        a = points[i] - centroid
        b = points[(i + 1) % len(points)] - centroid
        normal_accum = normal_accum + a.cross(b)

    # FIX (confirmed real, not hypothetical): for COLLINEAR points --
    # i.e. this function being called on a STRAIGHT edge -- every
    # cross product above is the zero vector (cross product of
    # parallel/anti-parallel vectors is always zero), so normal_accum
    # itself ends up as the zero vector. Calling .normalized() on a
    # zero vector throws OCCT's own Standard_ConstructionError
    # ("gp_Vec::Normalized() - vector has zero norm") -- NOT a Python
    # ValueError, so it silently escaped every "except ValueError"
    # handler built around this function (confirmed via real picking
    # in main_app.py: clicking an ordinary straight plate edge crashed
    # with exactly this error, with NO debug output even printed,
    # because the crash happened HERE, inside _fit_circle_to_edge,
    # before circle_center/circle_axis's caller ever got a chance to
    # fall back to edge_direction). This is also exactly WHY the
    # self-test never caught it: every previous test only ever fed
    # this function genuinely curved input (a full circle, a half
    # circle) -- it was never tested against a straight edge, even
    # though circle_center/circle_axis gets attempted on EVERY edge
    # (circular or not) as the first try in the real picking flow.
    #
    # Detect this explicitly and raise a clean ValueError -- the
    # exception type calling code already expects and handles -- BEFORE
    # ever attempting the normalize that would otherwise crash.
    if normal_accum.length < 1e-9:
        raise ValueError(
            "Cannot fit a circle: sampled points are collinear (or "
            "otherwise produce a degenerate/zero normal) -- this edge "
            "is not circular, even approximately. (Likely a straight "
            "edge -- this is the expected, correct rejection for that "
            "case, not a bug.)"
        )
    normal = normal_accum.normalized()

    # Build an orthonormal in-plane basis (u, v) perpendicular to
    # `normal`, to project the 3D points into 2D for the circle fit.
    arbitrary = Vector(0, 0, 1) if abs(normal.dot(Vector(0, 0, 1))) < 0.9 else Vector(0, 1, 0)
    u = (arbitrary - normal * arbitrary.dot(normal)).normalized()
    v = normal.cross(u)

    # Project to 2D (x, y) in the (u, v) basis, centered on centroid.
    xy = []
    for p in points:
        rel = p - centroid
        xy.append((rel.dot(u), rel.dot(v)))

    # Kasa least-squares circle fit: minimizes algebraic residual
    # for (x-cx)^2 + (y-cy)^2 = r^2.
    n = len(xy)
    sum_x = sum(p[0] for p in xy)
    sum_y = sum(p[1] for p in xy)
    sum_xx = sum(p[0] ** 2 for p in xy)
    sum_yy = sum(p[1] ** 2 for p in xy)
    sum_xy = sum(p[0] * p[1] for p in xy)
    sum_xxx = sum(p[0] ** 3 for p in xy)
    sum_yyy = sum(p[1] ** 3 for p in xy)
    sum_xyy = sum(p[0] * p[1] ** 2 for p in xy)
    sum_xxy = sum(p[0] ** 2 * p[1] for p in xy)

    A1 = sum_xx - sum_x * sum_x / n
    B1 = sum_xy - sum_x * sum_y / n
    C1 = sum_yy - sum_y * sum_y / n
    D1 = 0.5 * (sum_xyy - sum_x * sum_yy / n + sum_xxx - sum_x * sum_xx / n)
    E1 = 0.5 * (sum_xxy - sum_y * sum_xx / n + sum_yyy - sum_y * sum_yy / n)

    denom = A1 * C1 - B1 * B1
    if abs(denom) < 1e-12:
        # Degenerate fit (e.g. points are collinear, not circular at
        # all) -- return a fit with a deliberately huge residual so
        # callers can detect and reject it.
        return CircleFit(center=centroid, normal=normal, radius=0.0, max_residual=float("inf"))

    cx = (D1 * C1 - B1 * E1) / denom
    cy = (A1 * E1 - B1 * D1) / denom
    center_2d = (cx + sum_x / n, cy + sum_y / n)
    radius = (sum(((p[0] - cx) ** 2 + (p[1] - cy) ** 2) for p in xy) / n) ** 0.5

    # Convert the fitted 2D center back into 3D.
    center_3d = centroid + u * center_2d[0] + v * center_2d[1]

    # Residual: how far each ORIGINAL 3D sample point's distance to
    # center_3d deviates from the fitted radius -- the key number for
    # deciding "is this actually circular".
    max_residual = max(abs((p - center_3d).length - radius) for p in points)

    return CircleFit(center=center_3d, normal=normal, radius=radius, max_residual=max_residual)


# Tolerance for accepting a circle-fit on a non-CIRCLE-typed edge.
# Compared against max_residual relative to the fitted radius (so it
# scales sensibly for both small holes and large shafts) rather than
# an absolute distance.
CIRCLE_FIT_RELATIVE_TOLERANCE = 0.01  # 1% of fitted radius


def _resolve_circle(edge: Edge) -> CircleFit:
    """
    Shared resolution logic for BOTH circle_center and circle_axis:
    use the edge's own arc_center/normal directly when geom_type is
    genuinely "CIRCLE" (the clean, common, fast path), otherwise fall
    back to _fit_circle_to_edge() and only accept the fit if its
    residual is small relative to the fitted radius -- so a TRULY
    non-circular edge still gets correctly rejected, rather than this
    fallback silently accepting anything vaguely curved.
    """
    # FIX: compare against the GeomType ENUM member, not a bare
    # string. Confirmed via build123d's own documented filter
    # examples (e.g. "e.geom_type == GeomType.CIRCLE",
    # "edges().filter_by(GeomType.CIRCLE)") -- geom_type returns a
    # real GeomType enum, and `geom_type == "CIRCLE"` is NOT
    # guaranteed to be True even for a genuinely circular edge unless
    # GeomType happens to mix in str (not confirmed either way). This
    # was a latent bug in the ORIGINAL hard-rejection checks too
    # (before today's circle-fit fallback was added) -- meaning the
    # "fast path" below may never have actually triggered correctly,
    # even on real CIRCLE-typed edges; everything would have silently
    # fallen through to the fit instead. Not catastrophic (the fit is
    # independently verified-correct), but worth fixing properly.
    if edge.geom_type == GeomType.CIRCLE:
        return CircleFit(
            center=edge.arc_center,
            normal=edge.normal(),
            radius=getattr(edge, "radius", 0.0),
            max_residual=0.0,
        )

    fit = _fit_circle_to_edge(edge)
    if fit.radius <= 0 or fit.max_residual > CIRCLE_FIT_RELATIVE_TOLERANCE * fit.radius:
        raise ValueError(
            f"Edge has geom_type={edge.geom_type!r} (not CIRCLE), and a "
            f"circle-fit fallback did not find a good enough match "
            f"(max_residual={fit.max_residual:.6g}, fitted radius="
            f"{fit.radius:.6g}) -- this edge does not appear to actually "
            f"be circular, even approximately."
        )
    return fit


# --------------------------------------------------------------------------
# 1. Typed references to picked geometry
# --------------------------------------------------------------------------
# These exist so the picking UI (eventually, AIS selection modes) only
# has to answer "what kind of thing did the user click, and which
# TopoDS_Shape is it" -- all the geometric resolution (turning a
# picked circular edge into a center point + axis direction, etc.)
# happens here, in one place, independent of the GUI.

PointKind = Literal[
    "vertex",
    "edge_midpoint",
    "circle_center",        # center of a circular edge (e.g. a hole)
    "face_center",          # centroid of a planar face
    "origin",                # the origin of an existing Plane/workplane
]

DirectionKind = Literal[
    "edge_direction",        # direction of a straight edge
    "face_normal",
    "circle_axis",           # axis of a circular edge (e.g. a hole axis)
    "axis_x",
    "axis_y",
    "axis_z",
]


@dataclass(frozen=True)
class PointRef:
    """A reference to a point derived from picked geometry."""
    kind: PointKind
    shape: Shape | None = None  # the picked sub-shape; None for fixed refs

    def resolve(self) -> Vector:
        """Compute the actual 3D point this reference describes."""
        if self.kind == "vertex":
            if not isinstance(self.shape, Vertex):
                raise ValueError(f"PointRef kind 'vertex' requires a Vertex shape, got {type(self.shape)}")
            return Vector(self.shape.to_tuple())

        if self.kind == "edge_midpoint":
            if not isinstance(self.shape, Edge):
                raise ValueError(f"PointRef kind 'edge_midpoint' requires an Edge, got {type(self.shape)}")
            return self.shape.position_at(0.5)

        if self.kind == "circle_center":
            if not isinstance(self.shape, Edge):
                raise ValueError(f"PointRef kind 'circle_center' requires an Edge, got {type(self.shape)}")
            # FIX: was a hard `geom_type != "CIRCLE"` rejection.
            # Confirmed real (not hypothetical) via testing against
            # as1-oc-214.stp: a visually circular edge (rod's end) can
            # come back as geom_type "BSPLINE" from real-world STEP
            # files, even though our own export/import round-trip
            # preserves CIRCLE correctly. _resolve_circle() uses the
            # fast CIRCLE path when available, falls back to a
            # circle-fit otherwise, and only accepts the fit if it's
            # actually a good match -- see the circle-fitting section
            # near the top of this file for the full reasoning.
            fit = _resolve_circle(self.shape)
            return fit.center

        if self.kind == "face_center":
            if not isinstance(self.shape, Face):
                raise ValueError(f"PointRef kind 'face_center' requires a Face, got {type(self.shape)}")
            return self.shape.center()

        if self.kind == "origin":
            return Vector(0, 0, 0)

        raise ValueError(f"Unhandled PointRef kind: {self.kind!r}")


@dataclass(frozen=True)
class DirectionRef:
    """A reference to a direction derived from picked geometry."""
    kind: DirectionKind
    shape: Shape | None = None

    def resolve(self) -> Vector:
        """Compute the actual unit direction vector this reference describes."""
        if self.kind == "edge_direction":
            if not isinstance(self.shape, Edge):
                raise ValueError(f"DirectionRef kind 'edge_direction' requires an Edge, got {type(self.shape)}")
            start = self.shape.position_at(0)
            end = self.shape.position_at(1)
            return (end - start).normalized()

        if self.kind == "face_normal":
            if not isinstance(self.shape, Face):
                raise ValueError(f"DirectionRef kind 'face_normal' requires a Face, got {type(self.shape)}")
            return self.shape.normal_at()

        if self.kind == "circle_axis":
            if not isinstance(self.shape, Edge):
                raise ValueError(f"DirectionRef kind 'circle_axis' requires an Edge, got {type(self.shape)}")
            # FIX: same as circle_center above -- was a hard
            # geom_type-must-be-CIRCLE rejection, now uses the shared
            # _resolve_circle() fallback so visually-circular edges
            # that real-world STEP files encode as BSPLINE (or
            # anything else) still resolve correctly, as long as a
            # circle-fit confirms they're actually circular.
            fit = _resolve_circle(self.shape)
            return fit.normal

        if self.kind == "axis_x":
            return Vector(1, 0, 0)
        if self.kind == "axis_y":
            return Vector(0, 1, 0)
        if self.kind == "axis_z":
            return Vector(0, 0, 1)

        raise ValueError(f"Unhandled DirectionRef kind: {self.kind!r}")


# --------------------------------------------------------------------------
# 2. Building a full 3-2-1 Plane from picks
# --------------------------------------------------------------------------

def plane_from_picks(
    origin: PointRef,
    primary: DirectionRef,
    primary_role: Literal["x_dir", "z_dir"] = "z_dir",
    secondary: DirectionRef | None = None,
) -> Plane:
    """
    Build a fully-constrained Plane (the "3-2-1") from picked geometry.

    Args:
        origin: the "3" -- fully constrains position.
        primary: the "2" -- the main direction the user picked (e.g.
            a hole axis, a face normal). By default treated as the
            plane's z_dir (normal), matching how a workplane is most
            often defined -- "put a workplane ON this face/axis."
        primary_role: whether `primary` should become the plane's
            z_dir (normal -- the common case) or x_dir.
        secondary: the "1" -- resolves the remaining rotational
            freedom (the "roll" around the primary axis). If omitted,
            build123d's Plane picks a default x_dir/y_dir
            automatically (consistent but not necessarily what the
            user expects -- prompt for this explicitly in the UI
            when precise roll matters, per Doug's HP experience: "we
            definitely need 3-2-1 defined in general move commands").

    Returns:
        A build123d Plane representing the fully-resolved frame.
    """
    origin_pt = origin.resolve()
    primary_dir = primary.resolve()

    if primary_role == "z_dir":
        if secondary is not None:
            secondary_dir = secondary.resolve()
            # Project the secondary direction into the plane perpendicular
            # to primary_dir, to get a valid x_dir (Gram-Schmidt).
            x_dir = (secondary_dir - primary_dir * secondary_dir.dot(primary_dir)).normalized()
            return Plane(origin=origin_pt, x_dir=x_dir, z_dir=primary_dir)
        else:
            return Plane(origin=origin_pt, z_dir=primary_dir)
    elif primary_role == "x_dir":
        if secondary is not None:
            secondary_dir = secondary.resolve()
            z_dir = (secondary_dir - primary_dir * secondary_dir.dot(primary_dir)).normalized()
            return Plane(origin=origin_pt, x_dir=primary_dir, z_dir=z_dir)
        else:
            # build123d's Plane needs SOME z_dir; default to global Z
            # unless that's parallel to primary_dir, in which case
            # fall back to global Y to avoid a degenerate frame.
            fallback_z = Vector(0, 0, 1)
            if abs(primary_dir.dot(fallback_z)) > 0.999:
                fallback_z = Vector(0, 1, 0)
            return Plane(origin=origin_pt, x_dir=primary_dir, z_dir=fallback_z)
    else:
        raise ValueError(f"primary_role must be 'x_dir' or 'z_dir', got {primary_role!r}")


# --------------------------------------------------------------------------
# 3. The one-shot move itself
# --------------------------------------------------------------------------

def compute_move(from_plane: Plane, to_plane: Plane) -> Location:
    """
    Compute the ONE-SHOT transform that takes geometry expressed in
    from_plane's frame and re-expresses it in to_plane's frame -- i.e.
    "move whatever was positioned at from_plane so it's now positioned
    at to_plane instead."

    This is intentionally a pure function with no side effects: it
    does NOT mutate any part. Apply the result with
    `part.locate(result)` or `part.moved(result)` at the call site,
    so the decision of WHEN to actually commit the move stays
    explicit and visible to the caller (e.g. after gizmo fine-tuning).
    """
    # build123d Planes expose `.location`, a Location representing the
    # transform from the GLOBAL frame to that plane's local frame.
    # Composing to_plane.location with the inverse of from_plane.location
    # gives exactly "go from from_plane's frame to the global frame,
    # then from the global frame to to_plane's frame" -- i.e. the
    # one-shot move.
    return to_plane.location * from_plane.location.inverse()


def move_location_only(from_plane: Plane, to_plane: Plane) -> Location:
    """
    The "move without changing rotation" case Doug specifically
    flagged: reuse from_plane's orientation entirely, only translate
    the origin to match to_plane's origin. Falls out of the same
    machinery rather than needing special-cased math -- this just
    constructs a target plane that shares from_plane's axes.
    """
    translated_only = Plane(
        origin=to_plane.origin,
        x_dir=from_plane.x_dir,
        z_dir=from_plane.z_dir,
    )
    return compute_move(from_plane, translated_only)


# --------------------------------------------------------------------------
# 4. Self-test (no GUI, no STEP file needed)
# --------------------------------------------------------------------------

def _self_test():
    """
    Minimal sanity check using only synthetic data -- same philosophy
    as step_assembly_poc.py's demo_with_synthetic_assembly(): prove
    the math works in isolation before any picking UI touches it.
    """
    from build123d import Box

    print("--- Pose module self-test ---")

    # A simple case with no picking involved at all: move a box's
    # origin-anchored plane to a new location 50mm away in X, no
    # rotation change.
    box = Box(10, 10, 10)
    from_plane = Plane(origin=(0, 0, 0), z_dir=(0, 0, 1))
    to_plane = Plane(origin=(50, 0, 0), z_dir=(0, 0, 1))

    move = compute_move(from_plane, to_plane)
    print(f"Computed move (translate only case): {move}")

    moved_box = box.located(move)
    expected_center = Vector(50, 0, 0)
    actual_center = moved_box.center()
    print(f"Expected center near {expected_center}, got {actual_center}")
    delta = (actual_center - expected_center).length
    print(f"Delta: {delta:.6f} (should be ~0)")
    assert delta < 1e-6, "Translate-only move did not land where expected!"

    # A rotation case: move from a plane normal to +Z, to a plane
    # normal to +X (a 90-degree reorientation), origin unchanged.
    from_plane2 = Plane(origin=(0, 0, 0), z_dir=(0, 0, 1))
    to_plane2 = Plane(origin=(0, 0, 0), z_dir=(1, 0, 0))
    move2 = compute_move(from_plane2, to_plane2)
    print(f"\nComputed move (rotation case): {move2}")

    # move_location_only: reuse from_plane2's orientation, only
    # translate the origin -- should NOT pick up the 90-degree
    # rotation that compute_move(from_plane2, to_plane2) would.
    move3 = move_location_only(from_plane2, Plane(origin=(20, 0, 0), z_dir=(1, 0, 0)))
    print(f"\nComputed move (location-only, ignoring to_plane's rotation): {move3}")

    print("\nSelf-test completed (see output above for sanity, not all")
    print("cases have hard assertions yet -- visually inspect the")
    print("rotation-case output once build123d is actually installed).")

    # --- Circle-fit test: verify the NEW fallback math is correct ---
    # This is the most mathematically involved code in this module
    # (a from-scratch least-squares fit, no OCCT calls at all) -- so
    # it gets its own dedicated, hard-assertion test against a KNOWN
    # circle, not just visual inspection.
    print("\n--- Circle-fit self-test ---")
    from build123d import Cylinder

    cyl = Cylinder(radius=15, height=40)
    circular_edges = [e for e in cyl.edges() if e.geom_type == GeomType.CIRCLE]
    assert len(circular_edges) >= 1, "Expected at least one circular edge on a Cylinder"
    test_edge = circular_edges[0]

    # Path 1: the FAST path (geom_type IS "CIRCLE") -- via the real
    # PointRef/DirectionRef classes, exercising the actual call path
    # the picking UI will use, not just the internal helper directly.
    fast_point = PointRef(kind="circle_center", shape=test_edge).resolve()
    fast_dir = DirectionRef(kind="circle_axis", shape=test_edge).resolve()
    print(f"Fast path (geom_type==CIRCLE): center={fast_point}  axis={fast_dir}")

    # Path 2: force the FALLBACK path by calling _fit_circle_to_edge
    # directly on the SAME edge, even though it IS geom_type CIRCLE --
    # this isolates whether the FIT MATH ITSELF is correct,
    # independent of which path normally gets taken for this edge.
    fit = _fit_circle_to_edge(test_edge)
    print(f"Fit path (forced, same edge): center={fit.center}  "
          f"normal={fit.normal}  radius={fit.radius}  "
          f"max_residual={fit.max_residual:.6g}")

    center_delta = (fit.center - fast_point).length
    radius_delta = abs(fit.radius - 15)
    print(f"center_delta={center_delta:.6f}  radius_delta={radius_delta:.6f}  "
          f"(both should be ~0)")
    assert center_delta < 1e-3, f"Circle-fit center off by {center_delta} -- math may be wrong!"
    assert radius_delta < 1e-3, f"Circle-fit radius off by {radius_delta} -- math may be wrong!"
    assert fit.max_residual < 1e-6, f"Circle-fit residual unexpectedly large: {fit.max_residual}"

    # Sanity check the normal is parallel to the FAST path's normal
    # (could point in either direction depending on sample winding
    # order -- only direction matters here, not sign).
    normal_alignment = abs(fit.normal.dot(fast_dir))
    print(f"normal_alignment (|dot product|, should be ~1.0): {normal_alignment:.6f}")
    assert normal_alignment > 0.999, "Circle-fit normal is not parallel to the known axis!"

    print("\nCircle-fit (full circle) math confirmed correct against a")
    print("known circle.")

    # --- Half-circle fit test ---
    # Real-world testing against as1-oc-214.stp revealed something
    # the full-circle test above didn't cover: the rod's circular end
    # is actually bounded by SEMI-circular edges (confirmed visually:
    # "semi-circular edge, semi-cylindrical face, flat end face" when
    # picking around it) -- a common BREP/STEP pattern where a full
    # circular boundary gets split into two (or more) arc-edges at
    # the topology level, each independently encoded (in this file)
    # as a BSPLINE rather than a circular-arc entity.
    #
    # A half-circle is GEOMETRICALLY the same shape as a full circle
    # (still genuinely circular), but it's a meaningfully HARDER case
    # for a least-squares fit: only 180 degrees of arc constrains the
    # fit less tightly than a full 360-degree loop (a shallow arc is
    # easier to confuse with a much larger circle than a closed loop
    # is) -- worth testing explicitly rather than assuming the
    # full-circle result generalizes.
    print("\n--- Half-circle fit test (matches the REAL as1-oc-214.stp case) ---")
    from build123d import CenterArc

    half_circle_edge = CenterArc(center=(5, -3, 0), radius=15, start_angle=0, arc_size=180)
    half_fit = _fit_circle_to_edge(half_circle_edge, num_samples=12)
    print(f"Half-circle fit: center={half_fit.center}  normal={half_fit.normal}  "
          f"radius={half_fit.radius}  max_residual={half_fit.max_residual:.6g}")

    expected_center = Vector(5, -3, 0)
    half_center_delta = (half_fit.center - expected_center).length
    half_radius_delta = abs(half_fit.radius - 15)
    print(f"half_center_delta={half_center_delta:.6f}  "
          f"half_radius_delta={half_radius_delta:.6f}  (both should be ~0)")
    assert half_center_delta < 1e-3, (
        f"Half-circle fit center off by {half_center_delta} -- the fit may "
        f"not generalize correctly to partial arcs, which is the ACTUAL "
        f"real-world case (as1-oc-214.stp's rod), not just a theoretical one!"
    )
    assert half_radius_delta < 1e-3, f"Half-circle fit radius off by {half_radius_delta}"
    print("\nHalf-circle fit confirmed correct -- this is the case that")
    print("actually matters for as1-oc-214.stp's rod part specifically.")

    print("\n(Still not yet tested against the REAL rod edge itself, from")
    print("the actual STEP file -- only a synthetic half-circle with known")
    print("ground truth. Worth a final check: wire this into the picking")
    print("UI and confirm clicking the rod's actual semi-circular edge")
    print("produces a sensible center/radius, not just plausible-looking")
    print("numbers.)")

    # --- Straight-edge rejection test (the ACTUAL bug found via real
    # picking, after the above tests were already passing) ---
    # circle_center/circle_axis gets attempted on EVERY edge in the
    # real picking flow (assembly_viewer.py), circular or not -- a
    # straight edge needs to be cleanly REJECTED (a Python ValueError
    # the caller can catch and fall back to edge_direction), not crash
    # with an unhandled OCCT-level exception. This exact gap (no test
    # had ever fed _fit_circle_to_edge a straight/collinear edge) is
    # what let the bug through every prior test in this file.
    print("\n--- Straight-edge rejection test (the bug found via REAL picking) ---")
    from build123d import Line

    straight_edge = Line((0, 0, 0), (100, 0, 0))
    try:
        bad_fit = _fit_circle_to_edge(straight_edge)
        print(f"UNEXPECTED: straight edge did not raise -- got {bad_fit}")
        assert False, (
            "A straight/collinear edge should raise ValueError, not "
            "silently return a (meaningless) fit!"
        )
    except ValueError as e:
        print(f"Correctly raised ValueError: {e}")
        print("(This is the FIX for the real crash found via picking --")
        print(" confirms a straight edge is now cleanly rejected instead")
        print(" of crashing with an unhandled OCCT exception.)")

    # Also confirm the FULL circle_center/circle_axis path (not just
    # the internal _fit_circle_to_edge helper) handles this correctly
    # end-to-end, matching exactly what assembly_viewer.py's real
    # picking code does.
    try:
        PointRef(kind="circle_center", shape=straight_edge).resolve()
        assert False, "circle_center should have raised ValueError on a straight edge!"
    except ValueError:
        print("PointRef(circle_center) on a straight edge: correctly raised ValueError.")


if __name__ == "__main__":
    _self_test()


# ---------------------------------------------------------------------------
# 5. Constrained 3-2-1 positioning math
#    (Step 1: rotate to flush, Step 2: translate in-plane, Step 3: last DOF)
# ---------------------------------------------------------------------------

def find_intersection_line(P1: Vector, N1: Vector, P2: Vector, N2: Vector):
    """
    Find the intersection line of two infinite planes.

    Plane 1: N1 · (X - P1) = 0
    Plane 2: N2 · (X - P2) = 0

    Returns (point_on_line, direction) where:
      - direction = N1 × N2 (normalized)
      - point_on_line = the point on L closest to the midpoint of P1, P2
                        (a well-defined unique point even though L is infinite)

    Returns None if the planes are parallel (|N1 × N2| < tol), in which
    case the caller should fall back to pure translation.

    DERIVATION of point_on_line:
      L direction: D = N1 × N2  (normalized)
      A point on L satisfies both plane equations:
        N1 · X = d1   where d1 = N1 · P1
        N2 · X = d2   where d2 = N2 · P2
      Using the formula for the intersection of two planes:
        P = ((d1 * N2 - d2 * N1) × D) / |D|²
      This gives the point on L closest to the origin. To get the point
      closest to the midpoint M of P1,P2 (more numerically stable and
      geometrically meaningful), we project M onto L:
        P_near = P + ((M - P) · D) * D
    """
    D = N1.cross(N2)
    d_len_sq = D.dot(D)

    TOL = 1e-8
    if d_len_sq < TOL:
        return None  # planes are parallel

    D_norm = D * (1.0 / d_len_sq ** 0.5)

    d1 = N1.dot(P1)
    d2 = N2.dot(P2)

    # Point on L closest to origin
    P_origin = (N2 * d1 - N1 * d2).cross(D) * (1.0 / d_len_sq)

    # Project midpoint of P1,P2 onto L for a more central reference point
    M = (P1 + P2) * 0.5
    P_near = P_origin + D_norm * (M - P_origin).dot(D_norm)

    return P_near, D_norm


def compute_step1_move(pick1, pick2, mate: bool = True):
    """
    Step 1 of the 3-2-1 workflow: rotate the moving part about the
    intersection line of the two face planes until the faces are flush.

    mate=True:  normals become OPPOSED  (N1_new = -N2)
    mate=False: normals become PARALLEL (N1_new = +N2)

    If the planes are already parallel (degenerate intersection line),
    falls back to a pure translation along the target normal to close
    the gap between the faces -- the axis is "at infinity" so rotation
    degenerates to translation.

    Returns a build123d Location to be applied with node.move(result).
    """
    from build123d import Location
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
    import math

    P1 = pick1.point
    N1 = pick1.direction
    P2 = pick2.point
    N2 = pick2.direction

    if N1 is None or N2 is None:
        print("[pose] Step 1 requires directed picks (faces).")
        return None

    # Target normal for the moving face after step 1
    target_N1 = -N2 if mate else N2

    print(f"[Step1] N1={N1}  N2={N2}  mate={mate}  target={target_N1}")
    print(f"[Step1] N1·target={N1.dot(target_N1):.6f}  N1·N2={N1.dot(N2):.6f}")

    result = find_intersection_line(P1, N1, P2, N2)

    if result is None:
        # Planes are parallel (N1 ∥ N2).
        # Two sub-cases:
        #   a) N1 ≈ -N2 (already opposed): mate = just translate to close gap.
        #                                  align = 180° rotation + translate.
        #   b) N1 ≈ +N2 (same direction):  mate = 180° rotation + translate.
        #                                  align = just translate to close gap.
        gap = (P2 - P1).dot(N2)
        tv = N2 * gap
        trans_trsf = gp_Trsf()
        trans_trsf.SetTranslation(gp_Vec(tv.X, tv.Y, tv.Z))
        trans_loc = Location(trans_trsf)

        # Check if a 180° rotation is needed
        needs_rotation = (mate and N1.dot(N2) > 0) or \
                         (not mate and N1.dot(N2) < 0)

        if not needs_rotation:
            return trans_loc

        # 180° rotation about an axis perpendicular to N1, through P1
        arbitrary = Vector(1, 0, 0) if abs(N1.dot(Vector(1, 0, 0))) < 0.9 \
            else Vector(0, 1, 0)
        rot_axis = N1.cross(arbitrary).normalized()
        ax = gp_Ax1(
            gp_Pnt(P1.X, P1.Y, P1.Z),
            gp_Dir(rot_axis.X, rot_axis.Y, rot_axis.Z)
        )
        rot_trsf = gp_Trsf()
        rot_trsf.SetRotation(ax, math.pi)
        rot_loc = Location(rot_trsf)
        # Apply rotation first, then translation
        return trans_loc * rot_loc

    L_point, L_dir = result

    # Angle to rotate: from N1 to target_N1
    cross = N1.cross(target_N1)
    sin_a = cross.length
    cos_a = N1.dot(target_N1)
    angle = math.atan2(sin_a, cos_a)

    if abs(angle) < 1e-6:
        # N1 already equals target_N1 -- just close the gap with translation
        gap = (P2 - P1).dot(target_N1)
        tv = target_N1 * gap
        t = gp_Trsf()
        t.SetTranslation(gp_Vec(tv.X, tv.Y, tv.Z))
        return Location(t)

    if sin_a < 1e-8:
        # N1 ≈ -target_N1: 180° rotation needed. L_dir from find_intersection_line
        # may be unreliable; use it if available, otherwise pick an arbitrary
        # perpendicular axis.
        if result is not None:
            L_point, L_dir = result
        else:
            # Parallel planes, 180° needed -- pick axis perpendicular to N1
            arbitrary = Vector(1, 0, 0) if abs(N1.dot(Vector(1,0,0))) < 0.9 \
                else Vector(0, 1, 0)
            L_dir = N1.cross(arbitrary).normalized()
            L_point = P1
        ax = gp_Ax1(
            gp_Pnt(L_point.X, L_point.Y, L_point.Z),
            gp_Dir(L_dir.X, L_dir.Y, L_dir.Z)
        )
        t = gp_Trsf()
        t.SetRotation(ax, math.pi)
        return Location(t)

    # Rotation axis: L_dir, but we need the sign correct so rotation
    # goes the right way. The cross product N1 × target_N1 gives the
    # axis direction; use L_dir sign-aligned to that.
    if cross.dot(L_dir) < 0:
        L_dir = -L_dir

    ax = gp_Ax1(
        gp_Pnt(L_point.X, L_point.Y, L_point.Z),
        gp_Dir(L_dir.X, L_dir.Y, L_dir.Z)
    )
    t = gp_Trsf()
    t.SetRotation(ax, angle)
    return Location(t)


def compute_step2_move(pick1, pick2, mated_normal: Vector):
    """
    Step 2: rotate within the mated plane until faces are coplanar with the
    wall (D1_in_plane anti-parallel to D2_in_plane), then translate along
    D2 to close the gap. Consumes 2 DOF; leaves 1 (along-wall translation).
    """
    from build123d import Location
    from OCP.gp import gp_Trsf, gp_Vec, gp_Ax1, gp_Dir, gp_Pnt
    import math

    P1 = pick1.point
    P2 = pick2.point
    D1 = pick1.direction
    D2 = pick2.direction
    N  = mated_normal.normalized()

    if D1 is not None and D2 is not None:
        # Project directions onto the mated plane
        d1_proj = (D1 - N * D1.dot(N))
        d2_proj = (D2 - N * D2.dot(N))

        if d1_proj.length < 1e-6 or d2_proj.length < 1e-6:
            delta = P2 - P1
            delta_in_plane = delta - N * delta.dot(N)
            t = gp_Trsf()
            t.SetTranslation(gp_Vec(delta_in_plane.X, delta_in_plane.Y,
                                    delta_in_plane.Z))
            return Location(t)

        d1_proj = d1_proj.normalized()
        d2_proj = d2_proj.normalized()

        # Target: rotating to make faces COPLANAR means d1_proj should
        # become PARALLEL to d2_proj (both pointing away from the interface).
        # Anti-parallel would Mate the faces (pull them together facing each
        # other) -- we want Align (same direction = faces become coplanar,
        # like sliding a block against a wall where both outward normals
        # point the same way once flush).
        target = d2_proj
        dot = max(-1.0, min(1.0, d1_proj.dot(target)))
        angle = math.acos(dot)
        cross = d1_proj.cross(target)
        sign = 1.0 if cross.dot(N) > 0 else -1.0

        rot_trsf = gp_Trsf()
        if abs(angle) > 1e-6:
            ax = gp_Ax1(
                gp_Pnt(P1.X, P1.Y, P1.Z),
                gp_Dir(N.X * sign, N.Y * sign, N.Z * sign)
            )
            rot_trsf.SetRotation(ax, angle)
        rot_loc = Location(rot_trsf)

        # Translate along D2 direction (wall normal in-plane) to close gap
        delta = P2 - P1
        delta_in_plane = delta - N * delta.dot(N)
        d2_in_plane = (D2 - N * D2.dot(N))
        if d2_in_plane.length > 1e-6:
            d2_in_plane = d2_in_plane.normalized()
            delta_perp = d2_in_plane * delta_in_plane.dot(d2_in_plane)
        else:
            delta_perp = delta_in_plane

        print(f"[Step2] D1={D1}  D2={D2}  N={N}")
        print(f"[Step2] d1_proj={d1_proj}  d2_proj={d2_proj}  target={target}")
        print(f"[Step2] angle={math.degrees(angle):.1f}deg  "
              f"delta_perp={delta_perp}  len={delta_perp.length:.3f}")

        trans_trsf = gp_Trsf()
        if delta_perp.length > 1e-6:
            trans_trsf.SetTranslation(
                gp_Vec(delta_perp.X, delta_perp.Y, delta_perp.Z))
        trans_loc = Location(trans_trsf)
        return trans_loc * rot_loc

    else:
        # Hole-to-hole: in-plane translation only
        delta = P2 - P1
        delta_in_plane = delta - N * delta.dot(N)
        t = gp_Trsf()
        t.SetTranslation(gp_Vec(delta_in_plane.X, delta_in_plane.Y,
                                delta_in_plane.Z))
        return Location(t)


def compute_step3_move(pick1, pick2, mated_normal: Vector,
                       wall_normal=None, step2_type: str = "wall",
                       pivot: "Optional[Vector]" = None):
    """
    Step 3: remove the last remaining DOF.

    TWO CASES depending on what Step 2 did:

    a) step2_type == "wall" (Step 2 aligned to a flat face/wall):
       After Steps 1+2, the only remaining motion is translation along
       the intersection of the mated plane and the wall plane:
           free_dir = mated_normal x wall_normal
       Translate only along free_dir. No other motion permitted.

    b) step2_type == "hole" (Step 2 aligned hole axes):
       After Steps 1+2, the only remaining DOF is rotation about the
       mated normal (spin). Pick two faces or edges; rotate about
       mated_normal through P1 until D1_in_plane || D2_in_plane.
       No translation permitted.
    """
    from build123d import Location
    from OCP.gp import gp_Trsf, gp_Vec, gp_Ax1, gp_Dir, gp_Pnt
    import math

    P1 = pick1.point
    P2 = pick2.point
    D1 = pick1.direction
    D2 = pick2.direction
    N  = mated_normal.normalized()

    if step2_type == "hole":
        # --- Rotation about mated normal to index the angle ---
        if D1 is None or D2 is None:
            # No directions: fall back to translation
            print("[Step3/hole] No directions -- cannot determine rotation angle.")
            t = gp_Trsf()
            return Location(t)

        # Project directions onto mated plane
        d1 = (D1 - N * D1.dot(N))
        d2 = (D2 - N * D2.dot(N))
        if d1.length < 1e-6 or d2.length < 1e-6:
            print("[Step3/hole] Directions perpendicular to mated plane.")
            return Location(gp_Trsf())

        d1 = d1.normalized()
        d2 = d2.normalized()

        # Rotate d1 to align with d2 (or -d2, pick the smaller angle)
        dot_pos = max(-1.0, min(1.0, d1.dot(d2)))
        dot_neg = max(-1.0, min(1.0, d1.dot(-d2)))
        if abs(dot_pos) >= abs(dot_neg):
            target = d2
            dot = dot_pos
        else:
            target = -d2
            dot = dot_neg

        angle = math.acos(dot)
        cross = d1.cross(target)
        sign = 1.0 if cross.dot(N) > 0 else -1.0

        # Rotate about mated_normal through the HOLE CENTER from Step 2.
        # Using P1 (the picked face center) would translate the part as
        # a side effect of the rotation. The hole center is the correct
        # pivot because that's the point Step 2 constrained.
        rot_pivot = pivot if pivot is not None else P1
        print(f"[Step3/hole] angle={math.degrees(angle):.1f}deg  "
              f"pivot={rot_pivot}")

        t = gp_Trsf()
        if abs(angle) > 1e-6:
            ax = gp_Ax1(
                gp_Pnt(rot_pivot.X, rot_pivot.Y, rot_pivot.Z),
                gp_Dir(N.X * sign, N.Y * sign, N.Z * sign)
            )
            t.SetRotation(ax, angle)
        return Location(t)

    else:
        # --- Translation along single free direction ---
        delta = P2 - P1

        if wall_normal is not None:
            W = wall_normal.normalized()
            free_dir = N.cross(W)
            if free_dir.length < 1e-6:
                free_dir = None
            else:
                free_dir = free_dir.normalized()
        else:
            free_dir = None

        if free_dir is not None:
            translation = free_dir * delta.dot(free_dir)
            print(f"[Step3/wall] free_dir={free_dir}  "
                  f"translation len={translation.length:.3f}")
        else:
            translation = delta - N * delta.dot(N)
            print(f"[Step3/wall] fallback in-plane translation")

        t = gp_Trsf()
        if translation.length > 1e-6:
            t.SetTranslation(gp_Vec(translation.X, translation.Y, translation.Z))
        return Location(t)

