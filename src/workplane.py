"""
workplane.py

THE WORKPLANE -- 2D coordinate system anchored to a 3D face.

Ported from Doug Blanding's kodacad project (github.com/dblanding/kodacad)
with permission. Changes from original: OCC.Core.* -> OCP.*, simplified
__init__ to accept a build123d Face directly, OCCUtils replaced inline.

WHAT A WORKPLANE IS:
  A coordinate system with:
    origin  -- face center (in 3D world coordinates)
    U axis  -- x direction of the 2D sketch
    V axis  -- y direction of the 2D sketch
    W axis  -- face normal (extrusion direction, points out of the face)
  All 2D sketch coordinates are in (U, V) space. self.Trsf converts
  them to 3D world coordinates for OCCT operations.

CREATION MODES:
  WorkPlane()              -- at origin, aligned with global XY plane
  WorkPlane(face, faceU)   -- on a picked face; faceU defines U direction
  WorkPlane(ax3=gp_Ax3())  -- fully specified (programmatic use)

2D GEOMETRY STORED:
  self.clines  -- construction lines as (a, b, c) coefficients (ax+by+c=0)
  self.ccircs  -- construction circles as ((cx, cy), r)
  self.edgeList -- profile edges as TopoDS_Edge objects

KEY METHODS:
  hcl(pnt)       -- horizontal construction line through pnt
  vcl(pnt)       -- vertical construction line through pnt
  hvcl(pnt)      -- H + V clines through pnt
  ccirc(pnt, r)  -- construction circle at pnt with radius r
  line(p1, p2)   -- profile line from p1 to p2
  rect(p1, p2)   -- profile rectangle
  circ(p1, r)    -- profile circle
  arc_3p(p1, p2, p3) -- profile arc through three points
  makeWire()     -- convert edgeList to a closed TopoDS_Wire for extrusion
  toW(uv)        -- convert 2D (u, v) point to 3D world coordinates
"""
import math

from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge,
                                BRepBuilderAPI_MakeFace,
                                BRepBuilderAPI_MakeWire)
from OCP.BRepGProp import BRepGProp
from OCP.BRepTools import BRepTools
from OCP.BRep import BRep_Tool
from OCP.TopoDS import TopoDS
from OCP.GC import GC_MakeArcOfCircle, GC_MakeSegment
from OCP.Geom import Geom_Circle, Geom_Line, Geom_Plane
from OCP.Geom2d import Geom2d_Circle, Geom2d_Line
from OCP.Geom2dAPI import Geom2dAPI_InterCurveCurve
from OCP.GeomLProp import GeomLProp_SLProps
from OCP.gp import (gp_Ax2, gp_Ax2d, gp_Ax3, gp_Circ2d, gp_Dir, gp_Dir2d,
                    gp_Lin2d, gp_Pln, gp_Pnt, gp_Pnt2d, gp_Trsf, gp_Vec)
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_REVERSED
from OCP.TopTools import TopTools_ListOfShape

INFINITY = 1e+10  # mm (on the order of Earth's diameter)
TOLERANCE = 1e-6


# ---------------------------------------------------------------------------
# face_normal: replaces OCCUtils.Construct.face_normal
# ---------------------------------------------------------------------------

def face_normal(face):
    """
    Return the surface normal of a TopoDS_Face as a gp_Dir, evaluated
    at the UV midpoint of the face. Reverses the normal if the face
    orientation is reversed (so the normal always points 'outward').
    """
    umin, umax, vmin, vmax = BRepTools.UVBounds_s(face)
    surf = BRep_Tool.Surface_s(face)
    props = GeomLProp_SLProps(surf, (umin + umax) / 2.0,
                              (vmin + vmax) / 2.0, 1, TOLERANCE)
    norm = props.Normal()
    if face.Orientation() == TopAbs_REVERSED:
        norm.Reverse()
    return norm


# ===========================================================================
#
# Math & geometry 2D utility functions (unchanged from kodacad)
#
# ===========================================================================

def intersection(cline1, cline2):
    """Return intersection (x,y) of 2 clines expressed as (a,b,c) coeff."""
    a, b, c = cline1
    d, e, f = cline2
    i = b*f - c*e
    j = c*d - a*f
    k = a*e - b*d
    if k:
        return (i/k, j/k)
    return None


def cnvrt_2pts_to_coef(pt1, pt2):
    """Return (a,b,c) coefficients of cline defined by 2 (x,y) pts."""
    x1, y1 = pt1
    x2, y2 = pt2
    a = y2 - y1
    b = x1 - x2
    c = x2*y1 - x1*y2
    return (a, b, c)


def proj_pt_on_line(cline, pt):
    """Return point which is the projection of pt on cline."""
    a, b, c = cline
    x, y = pt
    denom = a**2 + b**2
    if not denom:
        return pt
    xp = (b**2*x - a*b*y - a*c)/denom
    yp = (a**2*y - a*b*x - b*c)/denom
    return (xp, yp)


def pnt_in_box_p(pnt, box):
    """Point in box predicate: Return True if pnt is in box."""
    x, y = pnt
    x1, y1, x2, y2 = box
    if x1 < x < x2 and y1 < y < y2:
        return True


def midpoint(p1, p2, f=.5):
    """Return point part way (f=.5 by def) between points p1 and p2."""
    return (((p2[0]-p1[0])*f)+p1[0], ((p2[1]-p1[1])*f)+p1[1])


def p2p_dist(p1, p2):
    """Return the distance between two points"""
    x, y = p1
    u, v = p2
    return math.sqrt((x-u)**2 + (y-v)**2)


def p2p_angle(p0, p1):
    """Return angle (degrees) from p0 to p1."""
    return math.atan2(p1[1]-p0[1], p1[0]-p0[0])*180/math.pi


def add_pt(p0, p1):
    return (p0[0]+p1[0], p0[1]+p1[1])


def sub_pt(p0, p1):
    return (p0[0]-p1[0], p0[1]-p1[1])


def seg_circ_inters(x1, y1, x2, y2, xc, yc, r):
    """Return list of intersection pts of line segment and circle."""
    intpnts = []
    num = (xc - x1)*(x2 - x1) + (yc - y1)*(y2 - y1)
    denom = (x2 - x1)*(x2 - x1) + (y2 - y1)*(y2 - y1)
    if denom == 0:
        return
    u = num / denom
    xp = x1 + u*(x2-x1)
    yp = y1 + u*(y2-y1)
    a = (x2 - x1)**2 + (y2 - y1)**2
    b = 2*((x2-x1)*(x1-xc) + (y2-y1)*(y1-yc))
    c = xc**2+yc**2+x1**2+y1**2-2*(xc*x1+yc*y1)-r**2
    q = b**2 - 4*a*c
    if q == 0:
        intpnts.append((xp, yp))
    elif q:
        u1 = (-b+math.sqrt(abs(q)))/(2*a)
        u2 = (-b-math.sqrt(abs(q)))/(2*a)
        intpnts.append(((x1 + u1*(x2-x1)), (y1 + u1*(y2-y1))))
        intpnts.append(((x1 + u2*(x2-x1)), (y1 + u2*(y2-y1))))
    return intpnts


def circ_circ_inters(circ1, circ2):
    """Return list of intersection pts of 2 circles."""
    (x1, y1), r1 = circ1
    (x2, y2), r2 = circ2
    pts = []
    D = (x2-x1)**2 + (y2-y1)**2
    if not D:
        return pts
    try:
        q = math.sqrt(abs(((r1+r2)**2-D)*(D-(r2-r1)**2)))
    except Exception:
        return pts
    pts = [((x2+x1)/2+(x2-x1)*(r1**2-r2**2)/(2*D)+(y2-y1)*q/(2*D),
            (y2+y1)/2+(y2-y1)*(r1**2-r2**2)/(2*D)-(x2-x1)*q/(2*D)),
           ((x2+x1)/2+(x2-x1)*(r1**2-r2**2)/(2*D)-(y2-y1)*q/(2*D),
            (y2+y1)/2+(y2-y1)*(r1**2-r2**2)/(2*D)+(x2-x1)*q/(2*D))]
    if same_pt_p(pts[0], pts[1]):
        pts.pop()
    return pts


def same_pt_p(p1, p2):
    """Return True if p1 and p2 are within 1e-6 of each other."""
    if p2p_dist(p1, p2) < 1e-6:
        return True


def cline_box_intrsctn(cline, box):
    """Return tuple of pts where line intersects edges of box."""
    x0, y0, x1, y1 = box
    pts = []
    segments = [((x0, y0), (x1, y0)),
                ((x1, y0), (x1, y1)),
                ((x1, y1), (x0, y1)),
                ((x0, y1), (x0, y0))]
    for seg in segments:
        pt = intersection(cline, cnvrt_2pts_to_coef(seg[0], seg[1]))
        if pt:
            if p2p_dist(pt, seg[0]) <= p2p_dist(seg[0], seg[1]) and \
               p2p_dist(pt, seg[1]) <= p2p_dist(seg[0], seg[1]):
                if pt not in pts:
                    pts.append(pt)
    return tuple(pts)


def para_line(cline, pt):
    """Return coeff of newline thru pt and parallel to cline."""
    a, b, c = cline
    x, y = pt
    cnew = -(a*x + b*y)
    return (a, b, cnew)


def para_lines(cline, d):
    """Return 2 parallel lines straddling line, offset d."""
    a, b, c = cline
    c1 = math.sqrt(a**2 + b**2)*d
    return (a, b, c + c1), (a, b, c - c1)


def perp_line(cline, pt):
    """Return coeff of newline thru pt and perpend to cline."""
    a, b, c = cline
    x, y = pt
    cnew = a*y - b*x
    return (b, -a, cnew)


def closer(p0, p1, p2):
    """Return closer of p1 or p2 to point p0."""
    d1 = (p1[0] - p0[0])**2 + (p1[1] - p0[1])**2
    d2 = (p2[0] - p0[0])**2 + (p2[1] - p0[1])**2
    if d1 < d2:
        return p1
    return p2


def farther(p0, p1, p2):
    """Return farther of p1 or p2 from point p0."""
    d1 = (p1[0] - p0[0])**2 + (p1[1] - p0[1])**2
    d2 = (p2[0] - p0[0])**2 + (p2[1] - p0[1])**2
    if d1 > d2:
        return p1
    return p2


def angled_cline(pt, angle):
    """Return cline through pt at angle (degrees)"""
    ang = angle * math.pi / 180
    dx = math.cos(ang)
    dy = math.sin(ang)
    p2 = (pt[0]+dx, pt[1]+dy)
    return cnvrt_2pts_to_coef(pt, p2)


def cr_from_3p(p1, p2, p3):
    """Return ctr pt and radius of circle on which 3 pts reside."""
    chord1 = cnvrt_2pts_to_coef(p1, p2)
    chord2 = cnvrt_2pts_to_coef(p2, p3)
    radial_line1 = perp_line(chord1, midpoint(p1, p2))
    radial_line2 = perp_line(chord2, midpoint(p2, p3))
    ctr = intersection(radial_line1, radial_line2)
    if ctr:
        radius = p2p_dist(p1, ctr)
        return (ctr, radius)


def extendline(p0, p1, d):
    """Return point on extension of p0-p1 beyond p1 by distance d."""
    pts = seg_circ_inters(p0[0], p0[1], p1[0], p1[1], p1[0], p1[1], d)
    if pts:
        return farther(p0, pts[0], pts[1])


def rotate_pt(pt, ang, ctr):
    """Return pt rotated ang (deg) CCW about ctr."""
    x, y = sub_pt(pt, ctr)
    A = ang * math.pi / 180
    u = x * math.cos(A) - y * math.sin(A)
    v = y * math.cos(A) + x * math.sin(A)
    return add_pt((u, v), ctr)


# ===========================================================================
#
# WorkPlane class
#
# ===========================================================================

class WorkPlane:
    """
    A 2D plane for creating 2D profiles for building or modifying 3D geometry.

    Construction modes:
      1. Default (no args): workplane at origin, aligned with XY plane.
      2. On Face: face defines the plane (W = face normal),
                  faceU defines the U direction (U = faceU normal).
      3. By gp_Ax3: fully specified axis system.

    The workplane stores:
      - self.Trsf: gp_Trsf for converting 2D (U,V,0) → 3D world coords
      - self.clines: set of construction lines as (a,b,c) coefficients
      - self.ccircs: set of construction circles as ((cx,cy), r)
      - self.edgeList: list of TopoDS_Edge profile geometry
      - self.border: TopoDS_Face (translucent square for display)
    """

    def __init__(self, size, face=None, faceU=None, ax3=None):
        # Default: XYZ coordinate system
        origin = gp_Pnt(0, 0, 0)
        wDir = gp_Dir(0, 0, 1)
        uDir = gp_Dir(1, 0, 0)
        xyzAx3 = gp_Ax3(origin, wDir, uDir)

        if face is None and ax3 is None:
            # Mode 1: default workplane in XY plane at origin
            axis3 = xyzAx3
            gpPlane = gp_Pln(xyzAx3)
            self.gpPlane = gpPlane
            self.plane = Geom_Plane(gpPlane)

        elif face is not None:
            # Mode 2: workplane on a face
            # face and faceU may be TopoDS_Face, TopoDS_Shape, or build123d Face wrappers.
            # Unwrap build123d wrappers first, then downcast any bare TopoDS_Shape to
            # TopoDS_Face so BRepTools.UVBounds_s and BRepGProp accept it.
            raw_face = getattr(face, 'wrapped', face)
            raw_faceU = getattr(faceU, 'wrapped', faceU) if faceU else None

            from OCP.TopoDS import TopoDS_Face
            if not isinstance(raw_face, TopoDS_Face):
                raw_face = TopoDS.Face_s(raw_face)
            if raw_faceU is not None and not isinstance(raw_faceU, TopoDS_Face):
                raw_faceU = TopoDS.Face_s(raw_faceU)

            wDir = face_normal(raw_face)
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(raw_face, props)
            origin = props.CentreOfMass()

            if raw_faceU is not None:
                uDir = face_normal(raw_faceU)
            else:
                # No U-face given -- auto-compute a reasonable U direction
                # perpendicular to wDir. Pick whichever world axis is least
                # parallel to wDir as the reference.
                wx, wy, wz = wDir.X(), wDir.Y(), wDir.Z()
                if abs(wx) <= abs(wy) and abs(wx) <= abs(wz):
                    ref = gp_Dir(1, 0, 0)
                elif abs(wy) <= abs(wz):
                    ref = gp_Dir(0, 1, 0)
                else:
                    ref = gp_Dir(0, 0, 1)
                # uDir = wDir × ref × wDir (Gram-Schmidt)
                cross = gp_Vec(wDir).Crossed(gp_Vec(ref))
                if cross.Magnitude() > 1e-9:
                    uDir = gp_Dir(cross)
                else:
                    uDir = gp_Dir(1, 0, 0)

            axis3 = gp_Ax3(origin, wDir, uDir)
            self.gpPlane = gp_Pln(axis3)
            self.plane = Geom_Plane(self.gpPlane)

        elif ax3 is not None:
            # Mode 3: fully specified axis system
            axis3 = ax3
            uDir = axis3.XDirection()
            wDir = axis3.Axis().Direction()
            origin = axis3.Location()
            self.gpPlane = gp_Pln(axis3)
            self.plane = Geom_Plane(self.gpPlane)

        self.Trsf = gp_Trsf()
        self.Trsf.SetTransformation(axis3)
        self.Trsf.Invert()
        self.origin = origin
        self.uDir = uDir
        self.vDir = axis3.YDirection() if face or ax3 else gp_Dir(0, 1, 0)
        self.wDir = wDir
        self.wVec = gp_Vec(wDir)
        self.face = face
        self.size = size
        self.border = self._make_wp_border(size)
        self.clines = set()
        self.ccircs = set()
        self.edgeList = []
        self.wire = None
        self.accuracy = 1e-6
        self.hvcl((0, 0))  # H+V construction lines through origin

    # -----------------------------------------------------------------------
    # Border (visual extent of the workplane)
    # -----------------------------------------------------------------------

    def _make_sq_profile(self, size):
        """Make a square wire of side 2*size centered on origin."""
        p1 = gp_Pnt(-size,  size, 0).Transformed(self.Trsf)
        p2 = gp_Pnt( size,  size, 0).Transformed(self.Trsf)
        p3 = gp_Pnt( size, -size, 0).Transformed(self.Trsf)
        p4 = gp_Pnt(-size, -size, 0).Transformed(self.Trsf)
        e1 = BRepBuilderAPI_MakeEdge(GC_MakeSegment(p1, p2).Value()).Edge()
        e2 = BRepBuilderAPI_MakeEdge(GC_MakeSegment(p2, p3).Value()).Edge()
        e3 = BRepBuilderAPI_MakeEdge(GC_MakeSegment(p3, p4).Value()).Edge()
        e4 = BRepBuilderAPI_MakeEdge(GC_MakeSegment(p4, p1).Value()).Edge()
        wire = BRepBuilderAPI_MakeWire(e1, e2, e3, e4).Wire()
        return wire

    def _make_wp_border(self, size):
        """Make a face (TopoDS_Face) representing the workplane extent."""
        wire = self._make_sq_profile(size)
        face_bldr = BRepBuilderAPI_MakeFace(wire)
        if face_bldr.IsDone():
            return face_bldr.Face()
        return None

    # -----------------------------------------------------------------------
    # Construction Geometry
    # -----------------------------------------------------------------------

    def _cline_gen(self, cline):
        """Add a construction line if it's not already present."""
        a, b, c = cline
        for d, e, f in self.clines:
            if (abs(a-d) < self.accuracy and
                    abs(b-e) < self.accuracy and
                    abs(c-f) < self.accuracy):
                return
        self.clines.add(cline)

    def geomLineBldr(self, cline):
        """Convert native cline (a,b,c) to Geom_Line in 3D world space."""
        a, b, c = cline
        gpLin2d = gp_Lin2d(a, b, c)
        gpDir2d = gpLin2d.Direction()
        gpPnt2d = gpLin2d.Location()
        gpPnt = gp_Pnt(gpPnt2d.X(), gpPnt2d.Y(), 0).Transformed(self.Trsf)
        gpDir = gp_Dir(gpDir2d.X(), gpDir2d.Y(), 0).Transformed(self.Trsf)
        return Geom_Line(gpPnt, gpDir)

    def geomLines(self):
        """Return self.clines as list of Geom_Line (3D world space)."""
        return [self.geomLineBldr(cline) for cline in self.clines]

    def geom2dLines(self):
        """Return self.clines as list of Geom2d_Line."""
        return [Geom2d_Line(gp_Lin2d(*cline)) for cline in self.clines]

    def hcl(self, pnt):
        """Horizontal construction line through pnt (x,y)."""
        self._cline_gen(angled_cline(pnt, 0))

    def vcl(self, pnt):
        """Vertical construction line through pnt (x,y)."""
        self._cline_gen(angled_cline(pnt, 90))

    def hvcl(self, pnt):
        """Horizontal + vertical construction lines through pnt."""
        self._cline_gen(angled_cline(pnt, 0))
        self._cline_gen(angled_cline(pnt, 90))

    def acl(self, pnt1, pnt2=None, ang=None):
        """Construction line through pnt1, toward pnt2 or at angle."""
        if pnt2:
            self._cline_gen(cnvrt_2pts_to_coef(pnt1, pnt2))
        elif ang is not None:
            self._cline_gen(angled_cline(pnt1, ang))

    def lbcl(self, p1, p2, f=.5):
        """Linear bisector construction line between p1 and p2."""
        p0 = midpoint(p1, p2, f)
        baseline = cnvrt_2pts_to_coef(p1, p2)
        self._cline_gen(perp_line(baseline, p0))

    # -----------------------------------------------------------------------
    # Construction Circles
    # -----------------------------------------------------------------------

    def convert_circ_to_geomCirc(self, circ):
        """Convert 2D circle ((cx,cy), r) to Geom_Circle in world space."""
        (cx, cy), rad = circ
        cntrPt = gp_Pnt(cx, cy, 0)
        ax2 = gp_Ax2(cntrPt, gp_Dir(0, 0, 1))
        geomCirc = Geom_Circle(ax2, rad)
        geomCirc.Transform(self.Trsf)
        return geomCirc

    def convert_circ_to_geom2dCirc(self, circ):
        (cx, cy), r = circ
        return Geom2d_Circle(gp_Circ2d(gp_Ax2d(gp_Pnt2d(cx, cy),
                                                gp_Dir2d(1, 0)), r))

    def geom2dCircs(self):
        """Return self.ccircs as Geom2d_Circle list."""
        return [self.convert_circ_to_geom2dCirc(c) for c in self.ccircs]

    # -----------------------------------------------------------------------
    # Intersection Points
    # -----------------------------------------------------------------------

    def unique(self, point, points):
        x0, y0 = point
        for x, y in points:
            if abs(x-x0) < self.accuracy and abs(y-y0) < self.accuracy:
                return False
        return True

    def intersectPts(self):
        """List of 3D intersection points among construction geometry."""
        points = set()

        # clines × ccircs
        for ccirc in self.geom2dCircs():
            for cline in self.geom2dLines():
                inters = Geom2dAPI_InterCurveCurve(ccirc, cline)
                for i in range(inters.NbPoints()):
                    pnt2d = inters.Point(i+1)
                    point = (pnt2d.X(), pnt2d.Y())
                    if self.unique(point, points):
                        points.add(point)

        # ccircs × ccircs
        ccirc_list = list(self.ccircs)
        for i in range(len(ccirc_list)):
            circ0 = ccirc_list[i]
            for circ in ccirc_list[i+1:]:
                for pnt in circ_circ_inters(circ0, circ):
                    if self.unique(pnt, points):
                        points.add(pnt)

        # clines × clines
        cl_list = list(self.clines)
        for i in range(len(cl_list)):
            line0 = cl_list[i]
            for line in cl_list[i+1:]:
                P = intersection(line0, line)
                if P and abs(P[0]) < INFINITY and abs(P[1]) < INFINITY:
                    if self.unique(P, points):
                        points.add(P)

        # Convert to 3D gp_Pnt
        result = []
        for point in points:
            if point:
                x, y = point
                pnt = gp_Pnt(x, y, 0)
                pnt.Transform(self.Trsf)
                result.append(pnt)
        return result

    # -----------------------------------------------------------------------
    # Profile Geometry (sketch elements → edgeList)
    # -----------------------------------------------------------------------

    def p2p_dist(self, p1, p2):
        return p2p_dist(p1, p2)

    def line(self, pnt1, pnt2):
        """Profile line between two 2D end points."""
        x1, y1 = pnt1
        x2, y2 = pnt2
        p1 = gp_Pnt(x1, y1, 0).Transformed(self.Trsf)
        p2 = gp_Pnt(x2, y2, 0).Transformed(self.Trsf)
        seg = GC_MakeSegment(p1, p2).Value()
        self.edgeList.append(BRepBuilderAPI_MakeEdge(seg).Edge())

    def rect(self, pnt1, pnt2):
        """Profile rectangle from two diagonally opposite 2D corners."""
        x1, y1 = pnt1
        x2, y2 = pnt2
        p1 = gp_Pnt(x1, y1, 0).Transformed(self.Trsf)
        p2 = gp_Pnt(x2, y1, 0).Transformed(self.Trsf)
        p3 = gp_Pnt(x2, y2, 0).Transformed(self.Trsf)
        p4 = gp_Pnt(x1, y2, 0).Transformed(self.Trsf)
        for seg in [GC_MakeSegment(p1, p2).Value(),
                    GC_MakeSegment(p2, p3).Value(),
                    GC_MakeSegment(p3, p4).Value(),
                    GC_MakeSegment(p4, p1).Value()]:
            self.edgeList.append(BRepBuilderAPI_MakeEdge(seg).Edge())

    def circle(self, cntr, rad, constr=False):
        """Profile circle (or construction circle if constr=True)."""
        if constr:
            self.ccircs.add((cntr, rad))
            self.hvcl(cntr)
        else:
            edge = BRepBuilderAPI_MakeEdge(
                self.convert_circ_to_geomCirc((cntr, rad))).Edge()
            self.edgeList.append(edge)

    def arcc2p(self, pc, ps, pe):
        """Profile arc: center, start, end points (2D)."""
        rad = p2p_dist(pc, ps)
        geom_circ = self.convert_circ_to_geomCirc((pc, rad))
        gp_circ = geom_circ.Circ()
        gp_ps = gp_Pnt(ps[0], ps[1], 0).Transformed(self.Trsf)
        gp_pe = gp_Pnt(pe[0], pe[1], 0).Transformed(self.Trsf)
        geom_arc = GC_MakeArcOfCircle(gp_circ, gp_ps, gp_pe, True).Value()
        self.edgeList.append(BRepBuilderAPI_MakeEdge(geom_arc).Edge())

    def arc3p(self, ps, pe, p3):
        """Profile arc through three 2D points (start, end, third-on-arc)."""
        gp_ps = gp_Pnt(ps[0], ps[1], 0).Transformed(self.Trsf)
        gp_pe = gp_Pnt(pe[0], pe[1], 0).Transformed(self.Trsf)
        gp_p3 = gp_Pnt(p3[0], p3[1], 0).Transformed(self.Trsf)
        geom_arc = GC_MakeArcOfCircle(gp_ps, gp_pe, gp_p3).Value()
        self.edgeList.append(BRepBuilderAPI_MakeEdge(geom_arc).Edge())

    # -----------------------------------------------------------------------
    # Wire assembly (final step before extrude/cut)
    # -----------------------------------------------------------------------

    def makeWire(self):
        """Assemble edgeList into a closed TopoDS_Wire. Returns True if OK."""
        wireBldr = BRepBuilderAPI_MakeWire()
        occ_seq = TopTools_ListOfShape()
        for edge in self.edgeList:
            occ_seq.Append(edge)
        wireBldr.Add(occ_seq)
        if wireBldr.IsDone():
            self.wire = wireBldr.Wire()
            return True
        return False
