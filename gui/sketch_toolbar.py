"""
sketch_toolbar.py

A QToolBar that drives interactive 2D sketching on an active WorkPlane.

WORKFLOW
--------
1.  A WorkPlane has been created (via workplane_dialog.py) and is
    stored as self._workplane.
2.  The user clicks a toolbar button (e.g. H Cline).
3.  A small QInputDialog asks for the required value(s) (e.g. Y value).
4.  The corresponding WorkPlane method is called (e.g. wp.hcl((0, y))).
5.  The new geometry is displayed as AIS objects in the viewport.
6.  Repeat until the profile is complete.
7.  The existing "Create Part" button calls wp.makeWire() and extrudes.

TOOLS IMPLEMENTED
-----------------
Construction lines (clines):
  hcl   -- horizontal cline at Y
  vcl   -- vertical cline at X
  hvcl  -- H + V clines through (X, Y)
  acl   -- angled cline through (X, Y) at angle (degrees)
  lbcl  -- linear bisector between two points

Construction circles (ccirc):
  ccirc -- circle at center (X, Y) with radius R

Profile geometry:
  line  -- line from (X1, Y1) to (X2, Y2)
  rect  -- rectangle corner1 (X1, Y1) to corner2 (X2, Y2)
  circ  -- circle at center (X, Y) with radius R
  arcc2p -- arc: center (X,Y), start point, end point
  arc3p  -- arc through three points

Edit:
  del_el -- delete last profile element
  clear  -- clear all sketch geometry

AIS DISPLAY
-----------
Each element is displayed immediately after it is added:
  - Construction lines: magenta dashed infinite lines clipped to
    workplane border (same color as the U/V crosshairs).
  - Construction circles: magenta dashed circles.
  - Profile geometry: white solid edges.

All sketch AIS objects are stored in self._sketch_ais so they can be
erased cleanly when the workplane is reset or the dialog is closed.
"""

import os
import math

from PySide6.QtWidgets import QToolBar, QInputDialog, QMessageBox
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtCore import Qt

from OCP.AIS import AIS_Shape, AIS_DisplayMode
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
from OCP.GC import GC_MakeSegment, GC_MakeCircle, GC_MakeArcOfCircle
from OCP.gp import gp_Pnt, gp_Dir, gp_Ax2, gp_Circ, gp_Vec
from OCP.Quantity import Quantity_Color, Quantity_TypeOfColor
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge

# Construction geometry color: magenta (same as U/V crosshairs)
_CLINE_COLOR = Quantity_Color(0.85, 0.2, 0.55, Quantity_TypeOfColor.Quantity_TOC_RGB)
# Profile geometry color: white
_PROFILE_COLOR = Quantity_Color(1.0, 1.0, 1.0, Quantity_TypeOfColor.Quantity_TOC_RGB)

# Icon directory — prefer PNG versions (color, transparent background)
# over original GIFs if available.
_ICON_DIR_PNG = os.path.join(os.path.dirname(__file__), "icons_png")
_ICON_DIR_GIF = os.path.join(os.path.dirname(__file__), "icons")


def _icon(name):
    """Load an icon, preferring PNG over GIF."""
    for d, ext in [(_ICON_DIR_PNG, ".png"), (_ICON_DIR_GIF, ".gif")]:
        path = os.path.join(d, f"{name}{ext}")
        if os.path.exists(path):
            return QIcon(QPixmap(path))
    return QIcon()


class SketchToolBar(QToolBar):
    """
    Toolbar for 2D sketch operations on an active WorkPlane.
    Must be given a workplane and viewport before use via set_workplane().
    """

    def __init__(self, parent=None):
        super().__init__("Sketch", parent)
        self.setOrientation(Qt.Orientation.Vertical)

        self._workplane = None   # WorkPlane instance
        self._viewport = None    # OcctViewportWidget (for AIS display)
        self._sketch_ais = []    # All AIS objects added during this sketch

        self._build_toolbar()
        self.setEnabled(False)   # disabled until a workplane is set

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_workplane(self, workplane, viewport):
        """Activate the toolbar for a given WorkPlane and viewport."""
        self._workplane = workplane
        self._viewport = viewport
        self.setEnabled(True)

    def clear_sketch(self):
        """Erase all sketch AIS objects and reset the WorkPlane geometry."""
        self._erase_sketch_ais()
        if self._workplane is not None:
            self._workplane.edgeList = []
            self._workplane.wire = None

    def deactivate(self):
        """Called when the workplane dialog is closed or reset."""
        self._erase_sketch_ais()
        self._workplane = None
        self._viewport = None
        self.setEnabled(False)

    # ------------------------------------------------------------------
    # Toolbar construction
    # ------------------------------------------------------------------

    def _build_toolbar(self):
        self.addAction(_icon("hcl"),   "H Cline",          self._do_hcl)
        self.addAction(_icon("vcl"),   "V Cline",          self._do_vcl)
        self.addAction(_icon("hvcl"),  "H+V Cline",        self._do_hvcl)
        self.addAction(_icon("acl"),   "Angled Cline",     self._do_acl)
        self.addAction(_icon("lbcl"),  "Linear Bisector",  self._do_lbcl)
        self.addSeparator()
        self.addAction(_icon("ccirc"), "Constr Circle",    self._do_ccirc)
        self.addSeparator()
        self.addAction(_icon("line"),  "Line",             self._do_line)
        self.addAction(_icon("rect"),  "Rectangle",        self._do_rect)
        self.addAction(_icon("circ"),  "Circle",           self._do_circ)
        self.addAction(_icon("arcc2p"),"Arc Ctr-2Pts",     self._do_arcc2p)
        self.addAction(_icon("arc3p"), "Arc 3Pts",         self._do_arc3p)
        self.addSeparator()
        self.addAction(_icon("del_el"),"Delete Last",      self._do_del_el)
        self.addAction(_icon("del_g"), "Clear All",        self._do_clear)

    # ------------------------------------------------------------------
    # Input helpers
    # ------------------------------------------------------------------

    def _get_float(self, title, label, default=0.0):
        """Prompt for a single float. Returns (value, ok)."""
        val, ok = QInputDialog.getDouble(
            self, title, label, default, decimals=3
        )
        return val, ok

    def _get_point(self, title):
        """Prompt for X, Y coordinates. Returns ((x, y), ok)."""
        x, ok = QInputDialog.getDouble(self, title, "X:", 0.0, decimals=3)
        if not ok:
            return None, False
        y, ok = QInputDialog.getDouble(self, title, "Y:", 0.0, decimals=3)
        if not ok:
            return None, False
        return (x, y), True

    def _guard(self):
        """Return False and warn if no workplane is set."""
        if self._workplane is None or self._viewport is None:
            QMessageBox.warning(self, "No workplane",
                                "Pick a face first to create a workplane.")
            return False
        return True

    # ------------------------------------------------------------------
    # Construction line tools
    # ------------------------------------------------------------------

    def _do_hcl(self):
        """Horizontal construction line at Y."""
        if not self._guard():
            return
        y, ok = self._get_float("H Cline", "Y value (mm):")
        if not ok:
            return
        self._workplane.hcl((0, y))
        self._display_clines()

    def _do_vcl(self):
        """Vertical construction line at X."""
        if not self._guard():
            return
        x, ok = self._get_float("V Cline", "X value (mm):")
        if not ok:
            return
        self._workplane.vcl((x, 0))
        self._display_clines()

    def _do_hvcl(self):
        """H + V construction lines through (X, Y)."""
        if not self._guard():
            return
        pt, ok = self._get_point("H+V Cline")
        if not ok:
            return
        self._workplane.hvcl(pt)
        self._display_clines()

    def _do_acl(self):
        """Angled construction line through (X, Y) at angle (degrees)."""
        if not self._guard():
            return
        pt, ok = self._get_point("Angled Cline — point")
        if not ok:
            return
        ang, ok = self._get_float("Angled Cline", "Angle (degrees):", 45.0)
        if not ok:
            return
        self._workplane.acl(pt, ang=ang)
        self._display_clines()

    def _do_lbcl(self):
        """Linear bisector construction line between two points."""
        if not self._guard():
            return
        p1, ok = self._get_point("Linear Bisector — point 1")
        if not ok:
            return
        p2, ok = self._get_point("Linear Bisector — point 2")
        if not ok:
            return
        self._workplane.lbcl(p1, p2)
        self._display_clines()

    def _do_ccirc(self):
        """Construction circle at center (X, Y) with radius R."""
        if not self._guard():
            return
        center, ok = self._get_point("Constr Circle — center")
        if not ok:
            return
        r, ok = self._get_float("Constr Circle", "Radius (mm):", 10.0)
        if not ok:
            return
        self._workplane.circle(center, r, constr=True)
        self._display_clines()

    # ------------------------------------------------------------------
    # Profile geometry tools
    # ------------------------------------------------------------------

    def _do_line(self):
        """Profile line from (X1, Y1) to (X2, Y2)."""
        if not self._guard():
            return
        p1, ok = self._get_point("Line — start point")
        if not ok:
            return
        p2, ok = self._get_point("Line — end point")
        if not ok:
            return
        self._workplane.line(p1, p2)
        self._display_profile(n_new=1)

    def _do_rect(self):
        """Profile rectangle: corner1 (X1,Y1) to corner2 (X2,Y2)."""
        if not self._guard():
            return
        p1, ok = self._get_point("Rectangle — corner 1")
        if not ok:
            return
        p2, ok = self._get_point("Rectangle — corner 2")
        if not ok:
            return
        self._workplane.rect(p1, p2)
        self._display_profile(n_new=4)

    def _do_circ(self):
        """Profile circle at center (X,Y) with radius R."""
        if not self._guard():
            return
        center, ok = self._get_point("Circle — center")
        if not ok:
            return
        r, ok = self._get_float("Circle", "Radius (mm):", 10.0)
        if not ok:
            return
        self._workplane.circle(center, r, constr=False)
        self._display_profile(n_new=1)

    def _do_arcc2p(self):
        """Arc: center (X,Y), start point, end point."""
        if not self._guard():
            return
        center, ok = self._get_point("Arc Ctr-2Pts — center")
        if not ok:
            return
        p1, ok = self._get_point("Arc Ctr-2Pts — start point")
        if not ok:
            return
        p2, ok = self._get_point("Arc Ctr-2Pts — end point")
        if not ok:
            return
        self._workplane.arcc2p(center, p1, p2)
        self._display_profile(n_new=1)

    def _do_arc3p(self):
        """Arc through three points."""
        if not self._guard():
            return
        p1, ok = self._get_point("Arc 3Pts — point 1")
        if not ok:
            return
        p2, ok = self._get_point("Arc 3Pts — point 2 (on arc)")
        if not ok:
            return
        p3, ok = self._get_point("Arc 3Pts — point 3")
        if not ok:
            return
        self._workplane.arc3p(p1, p2, p3)
        self._display_profile(n_new=1)

    # ------------------------------------------------------------------
    # Edit tools
    # ------------------------------------------------------------------

    def _do_del_el(self):
        """Delete the last profile element."""
        if not self._guard():
            return
        if not self._workplane.edgeList:
            QMessageBox.information(self, "Delete Last",
                                    "No profile elements to delete.")
            return
        self._workplane.edgeList.pop()
        self._workplane.wire = None
        # Redisplay all profile elements from scratch
        self._redisplay_profile()

    def _do_clear(self):
        """Clear all sketch geometry."""
        if not self._guard():
            return
        reply = QMessageBox.question(
            self, "Clear Sketch",
            "Clear all construction and profile geometry?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.clear_sketch()

    # ------------------------------------------------------------------
    # AIS display
    # ------------------------------------------------------------------

    def _display_clines(self):
        """
        Redisplay all construction lines and circles from the workplane.
        Called after any cline/ccirc is added. We erase all existing
        cline AIS objects and redraw from scratch (simpler than
        tracking individual additions).
        """
        wp = self._workplane
        ctx = self._viewport.context
        size = wp.size

        # Erase old cline AIS objects (keep profile AIS objects)
        still_active = []
        for ais, kind in self._sketch_ais:
            if kind == "cline":
                ctx.Erase(ais, False)
            else:
                still_active.append((ais, kind))
        self._sketch_ais = still_active

        # Draw clines (a, b, c) → ax + by + c = 0 in workplane coords
        for cline in wp.clines:
            a, b, c = cline
            ais = self._make_cline_ais(a, b, c, size)
            if ais:
                ctx.Display(ais, False)
                ctx.Deactivate(ais)
                self._sketch_ais.append((ais, "cline"))

        # Draw construction circles ((cx, cy), r)
        for cc in getattr(wp, 'ccircs', []):
            (cx, cy), r = cc
            ais = self._make_ccirc_ais(cx, cy, r)
            if ais:
                ctx.Display(ais, False)
                ctx.Deactivate(ais)
                self._sketch_ais.append((ais, "cline"))

        ctx.UpdateCurrentViewer()
        self._viewport.update()

    def _display_profile(self, n_new=1):
        """
        Display the most recently added profile edge(s).
        n_new: how many edges were just added (1 for line/circle/arc,
               4 for rect). Displays the last n_new edges in edgeList.
        """
        wp = self._workplane
        if not wp.edgeList:
            return
        ctx = self._viewport.context

        for edge in wp.edgeList[-n_new:]:
            ais = AIS_Shape(edge)
            ais.SetColor(_PROFILE_COLOR)
            ais.SetWidth(2.0)
            ctx.Display(ais, False)
            ctx.Deactivate(ais)
            self._sketch_ais.append((ais, "profile"))

        ctx.UpdateCurrentViewer()
        self._viewport.update()

    def _redisplay_profile(self):
        """Erase all profile AIS and redisplay from edgeList (used after delete)."""
        wp = self._workplane
        ctx = self._viewport.context

        still_active = []
        for ais, kind in self._sketch_ais:
            if kind == "profile":
                ctx.Erase(ais, False)
            else:
                still_active.append((ais, kind))
        self._sketch_ais = still_active

        for edge in wp.edgeList:
            ais = AIS_Shape(edge)
            ais.SetColor(_PROFILE_COLOR)
            ais.SetWidth(2.0)
            ctx.Display(ais, False)
            ctx.Deactivate(ais)
            self._sketch_ais.append((ais, "profile"))

        ctx.UpdateCurrentViewer()
        self._viewport.update()

    def _erase_sketch_ais(self):
        """Erase all sketch AIS objects from the viewport."""
        if self._viewport is None:
            self._sketch_ais = []
            return
        ctx = self._viewport.context
        for ais, _ in self._sketch_ais:
            ctx.Erase(ais, False)
        ctx.UpdateCurrentViewer()
        self._viewport.update()
        self._sketch_ais = []

    # ------------------------------------------------------------------
    # AIS geometry builders
    # ------------------------------------------------------------------

    def _make_cline_ais(self, a, b, c, size):
        """
        Build an AIS_Shape edge for a construction line ax + by + c = 0,
        clipped to ±size in workplane coordinates.
        Returns None if the line can't be built.
        """
        wp = self._workplane
        try:
            if abs(b) > 1e-10:
                # Not vertical: sample at x = -size and x = +size
                y1 = (-a * (-size) - c) / b
                y2 = (-a * size - c) / b
                p1_2d, p2_2d = (-size, y1), (size, y2)
            else:
                # Vertical line: x = -c/a, sample at y = ±size
                x = -c / a
                p1_2d, p2_2d = (x, -size), (x, size)

            p1 = gp_Pnt(p1_2d[0], p1_2d[1], 0).Transformed(wp.Trsf)
            p2 = gp_Pnt(p2_2d[0], p2_2d[1], 0).Transformed(wp.Trsf)

            seg = GC_MakeSegment(p1, p2)
            if not seg.IsDone():
                return None
            edge = BRepBuilderAPI_MakeEdge(seg.Value()).Edge()
            ais = AIS_Shape(edge)
            ais.SetColor(_CLINE_COLOR)
            ais.SetWidth(1.0)
            return ais
        except Exception as e:
            print(f"[SketchToolBar] cline display error: {e}")
            return None

    def _make_ccirc_ais(self, cx, cy, r):
        """
        Build an AIS_Shape edge for a construction circle at (cx, cy)
        with radius r in workplane coordinates.
        Returns None if the circle can't be built.
        """
        wp = self._workplane
        try:
            center_3d = gp_Pnt(cx, cy, 0).Transformed(wp.Trsf)
            # The workplane normal (wDir) becomes the circle axis
            from OCP.gp import gp_Ax2, gp_Dir
            normal = gp_Dir(wp.wDir.X(), wp.wDir.Y(), wp.wDir.Z())
            ax2 = gp_Ax2(center_3d, normal)
            circle = gp_Circ(ax2, r)
            edge = BRepBuilderAPI_MakeEdge(circle).Edge()
            ais = AIS_Shape(edge)
            ais.SetColor(_CLINE_COLOR)
            ais.SetWidth(1.0)
            return ais
        except Exception as e:
            print(f"[SketchToolBar] ccirc display error: {e}")
            return None
