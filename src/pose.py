"""
pose.py

The "move a part from here to there" system, designed around the
3-2-1 workplane-pair idea: you define a `from_plane` (a coordinate
frame anchored to the part being moved, derived from picked geometry)
and a `to_plane` (a coordinate frame anchored to the target location,
also derived from picked geometry), and the move is simply "make
from_plane coincide with to_plane."

KEY DESIGN DECISION: this builds on build123d's own `Plane` and
`Location` classes rather than reinventing coordinate-frame math.
build123d's Plane is already exactly the "workplane" abstraction
(origin + x_dir + y_dir(implicit) + z_dir, right-handed -- see
build123d's own "Understanding Planes" discussion #569), and
Location already supports composition via the `*` operator. We are
NOT rebuilding gp_Trsf composition by hand; we're building the layer
ABOVE that -- resolving picked geometry (vertices, edges, faces) into
a Plane, and computing the one-shot transform between two Planes.

This module has ZERO GUI dependency on purpose, same philosophy as
step_assembly_poc.py: the pose math should be fully testable and
debuggable before any picking UI or AIS_Manipulator gizmo touches it.

NOT YET TESTED (no execution environment available here) -- written
carefully against build123d's documented Plane/Location API, but
treat the first run as a debugging session, same as every other piece
of this project so far.
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


if __name__ == "__main__":
    _self_test()
