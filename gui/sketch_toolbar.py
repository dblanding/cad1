"""
sketch_toolbar.py

THE SKETCH TOOLBAR -- 2D drawing tools for the active workplane.

Lives inside WorkplaneDialog. Activated after a workplane face is picked.
Each button calls a WorkPlane method (src/workplane.py) and displays the
resulting geometry as AIS objects in the 3D viewport.

TOOLS:
  Construction geometry (magenta dashed, not part of extruded profile):
    H Cline   -- horizontal construction line at Y
    V Cline   -- vertical construction line at X
    HV Clines -- H + V clines through a point
    Ang Cline -- angled construction line
    Lin Bisec -- linear bisector between two points
    Ccirc     -- construction circle at center with radius

  Profile geometry (white solid, forms the extruded cross-section):
    Line      -- straight line segment
    Rect      -- axis-aligned rectangle
    Circle    -- full circle
    Arc C2P   -- arc by center, start point, end point
    Arc 3P    -- arc through three points

  Intersection Snap (yellow dot markers):
    Snaps clicks to cline/ccirc intersections, queued in _pending_uvs.
    Typed/calculator numeric values queue in _pending_floats.

  Edit:
    Del Last    -- removes the most recently added profile element
    Clear All   -- removes all sketch geometry and AIS objects
    Cancel Tool -- abandons whichever tool is currently waiting for input

INPUT MODEL (PHASE 2 FIX, DESIGN_BACKLOG item 33 -- no tool EVER pops a
blocking dialog; a modal popup freezes the whole app until answered,
so the user can't pick a point, type a value, or use the calculator to
get past it):
  Every _do_xxx() tool method calls _start_tool(name, prompt), which
  tries to complete immediately from whatever's already queued
  (_pending_uvs / _pending_floats -- preserves "click points before
  clicking the tool"). If that's not enough, it arms `name` as
  self._active_tool and prompts via the status bar, then RETURNS --
  the app stays fully interactive. Each subsequent pick
  (receive_vertex_pick) or numeric value (push_pending_float) calls
  _retry_active_tool(), which re-attempts completion. "Cancel Tool"
  abandons an armed tool and clears both queues.

AIS LIFECYCLE:
  All sketch AIS objects are stored in _sketch_ais and _isect_ais.
  _erase_isect_ais() calls Deactivate() before Remove() on each marker --
  required to prevent context.Select() crashes when vertex-mode AIS objects
  are removed while still active in the selection index.
"""

import os
import math

from PySide6.QtWidgets import QToolBar, QMessageBox
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtCore import Qt, Signal

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

    # Emits the armed tool's name (e.g. "circ") whenever it changes, or
    # "" when no tool is armed. MainWindow listens to this to drive the
    # status-bar "Current Operation" label, KodaCAD-style.
    tool_armed = Signal(str)

    def __init__(self, parent=None):
        super().__init__("Sketch", parent)
        self.setOrientation(Qt.Orientation.Vertical)

        self._workplane = None   # WorkPlane instance
        self._viewport = None    # OcctViewportWidget (for AIS display)
        self._sketch_ais = []    # All AIS objects added during this sketch
        self._isect_ais = []     # AIS objects for intersection point markers
        self._isect_pts = {}     # id(AIS_Shape) -> (u, v) workplane coords
        self._pending_uvs = []   # queue of snapped (u,v) points
        self._pending_floats = []  # queue of typed/calculator numeric values
        # Name of the tool currently waiting for more input (e.g. "circ"),
        # or None. See _start_tool()/_retry_active_tool() -- this is what
        # lets a tool button be clicked FIRST and picks/typed values come
        # AFTER, non-blockingly, instead of popping a modal dialog that
        # freezes the whole app until answered. STAYS armed after each
        # successful completion ("sticky tool") -- see _start_tool()/
        # _retry_active_tool() -- so the user can place several circles
        # (etc.) in a row without re-clicking the toolbar button each
        # time. Only cleared by Cancel Tool or starting a different tool.
        self._active_tool = None
        self._active_prompt = None   # prompt text to re-show after each repeat

        self._build_toolbar()
        self.setEnabled(False)   # disabled until a workplane is set

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_workplane(self, workplane, viewport):
        """
        Activate the toolbar for a given WorkPlane and viewport.

        FIX (was the root cause of "intersection points aren't
        clickable"): WorkPlane.__init__ already adds H+V construction
        lines through the origin via self.hvcl((0, 0)), but nothing
        displayed them (or the intersection point they define) until
        the user manually added their OWN construction line via one
        of the toolbar buttons, at which point _display_clines() was
        called for the first time. A freshly-activated workplane had
        no visible geometry and no clickable markers at all, and there
        was no indication that adding a construction line first was
        required. Calling _display_clines() here renders the initial
        clines/markers immediately, matching what the user actually
        sees when they open the sketch toolbar.

        Also suspends the active part's overlay/edge-vertex picking
        (see MainWindow._suspend_active_part_overlay) -- reported as
        "unable to pick center point for circle when part was
        showing," since that overlay and the active part's own vertex
        selection were directly competing with the sketch's
        intersection markers when a workplane sits on that same part.
        """
        self._workplane = workplane
        self._viewport = viewport
        self._set_active_tool(None)
        self._active_prompt = None
        self._pending_uvs = []
        self._pending_floats = []
        self.setEnabled(True)
        self._display_clines()
        win = self.window()
        if hasattr(win, "_suspend_active_part_overlay"):
            win._suspend_active_part_overlay()

    def clear_sketch(self):
        """Erase all sketch AIS objects and reset the WorkPlane geometry."""
        self._erase_sketch_ais()
        self._erase_isect_ais()
        self._pending_uvs = []
        self._pending_floats = []
        self._set_active_tool(None)
        self._active_prompt = None
        if self._workplane is not None:
            self._workplane.edgeList = []
            self._workplane.wire = None

    def deactivate(self):
        """Called when the workplane dialog is closed or reset."""
        self._erase_sketch_ais()
        self._erase_isect_ais()
        self._workplane = None
        self._viewport = None
        self._pending_uvs = []
        self._pending_floats = []
        self._set_active_tool(None)
        self._active_prompt = None
        self.setEnabled(False)
        win = self.window()
        if hasattr(win, "_restore_active_part_overlay"):
            win._restore_active_part_overlay()

    def receive_vertex_pick(self, raw_shape):
        """
        Called by main_app when geometry_picked fires with TopAbs_VERTEX
        while the sketch toolbar is active. Checks if the picked vertex
        is one of the intersection markers and queues its (u,v) if so.
        Returns True if consumed.
        """
        from OCP.BRep import BRep_Tool
        from OCP.TopoDS import TopoDS
        try:
            vertex = TopoDS.Vertex_s(raw_shape)
            pnt = BRep_Tool.Pnt_s(vertex)
        except Exception:
            return False

        # Find the nearest stored intersection point in 3D
        wp = self._workplane
        if wp is None:
            return False

        best_uv = None
        best_dist = 1.0  # mm tolerance

        for uv in self._isect_pts.values():
            from OCP.gp import gp_Pnt
            p3d = gp_Pnt(uv[0], uv[1], 0).Transformed(wp.Trsf)
            dist = pnt.Distance(p3d)
            if dist < best_dist:
                best_dist = dist
                best_uv = uv

        if best_uv is not None:
            self._pending_uvs.append(best_uv)
            print(f"[SketchToolBar] Snapped: U={best_uv[0]:.3f}, "
                  f"V={best_uv[1]:.3f}  ({len(self._pending_uvs)} queued)")
            self._retry_active_tool()
            return True
        print(f"[SketchToolBar] receive_vertex_pick: got a vertex pick at "
              f"{pnt.X():.3f},{pnt.Y():.3f},{pnt.Z():.3f} but it didn't "
              f"match any of the {len(self._isect_pts)} known intersection "
              f"point(s) within {best_dist:.3f}mm -- likely picked a "
              f"different vertex (e.g. on a part), not an intersection marker.")
        return False

    def check_intersection_snap(self, screen_x, screen_y, view):
        """Legacy screen-space snap -- not used when OCCT vertex picking works."""
        return False

    def pop_pending_uv(self):
        """Pop the next snapped (u, v) from the queue, or None if empty."""
        if self._pending_uvs:
            return self._pending_uvs.pop(0)
        return None

    def push_pending_float(self, value):
        """
        Queue a numeric value entered via the shared status-bar line
        edit or sent directly from the RPN calculator's register
        buttons (see MainWindow.valueFromCalc in main_app.py). Consumed
        by whichever tool is currently waiting (see _active_tool /
        _retry_active_tool), or held for the next tool button click if
        none is currently waiting.
        """
        self._pending_floats.append(value)
        print(f"[SketchToolBar] Queued value: {value}  "
              f"({len(self._pending_floats)} queued)")
        self._retry_active_tool()

    def pop_pending_float(self):
        """Pop the next queued numeric value, or None if empty."""
        if self._pending_floats:
            return self._pending_floats.pop(0)
        return None

    def _available_points(self):
        """
        How many complete points can currently be assembled from the
        queues -- each click is one point, each PAIR of queued floats
        (typed X then Y, or sent from the calculator) is also one
        point. Integer division, so a stray unpaired float doesn't
        count. Used to check BEFORE calling _take_point() the needed
        number of times, so a tool's completion check stays atomic
        (all-or-nothing) even though a "point" can come from either
        queue.
        """
        return len(self._pending_uvs) + len(self._pending_floats) // 2

    def _take_point(self):
        """
        Pop one point: a clicked/snapped (u, v) if available, else two
        queued floats consumed as (x, y) in the order they were
        entered. Returns None if neither is available -- callers
        should check _available_points() first for atomicity.
        """
        if self._pending_uvs:
            return self._pending_uvs.pop(0)
        if len(self._pending_floats) >= 2:
            x = self._pending_floats.pop(0)
            y = self._pending_floats.pop(0)
            return (x, y)
        return None

    def _notify_status(self, text):
        """Show a prompt in the main window's status bar, and echo to
        the console (useful when no display is available to see it)."""
        print(f"[SketchToolBar] {text}")
        win = self.window()
        if hasattr(win, "statusBar"):
            win.statusBar().showMessage(text)

    def _set_active_tool(self, name):
        """
        Set self._active_tool and emit tool_armed(name or "") -- the
        single place that changes it, so MainWindow's "Current
        Operation" status-bar label (the KodaCAD-style "End Operation"
        button's companion) always stays in sync.
        """
        self._active_tool = name
        self.tool_armed.emit(name or "")

    def _start_tool(self, name, prompt):
        """
        Entry point for every _do_xxx tool method (PHASE 2 FIX,
        DESIGN_BACKLOG item 33): tries to complete immediately from
        whatever is already queued (preserves the existing "pick
        points before clicking the tool" convention). If that's not
        enough, arms `name` as the active tool and prompts via the
        status bar -- and stops there. It does NOT fall back to a
        blocking QInputDialog. That blocking fallback was the actual
        bug being fixed: a modal popup steals the whole app's input
        focus, so the user can't pick a point, type a value, or use
        the calculator to get past it -- the tool becomes genuinely
        stuck. Now, clicking a tool button always leaves the app fully
        interactive; picks and typed/calculator values arrive later,
        asynchronously, via _retry_active_tool().

        STICKY: the tool STAYS armed after it completes (whether
        immediately here or later via _retry_active_tool), so placing
        several circles/lines/etc. in a row doesn't need re-clicking
        the toolbar button each time -- only Cancel Tool / End
        Operation, Clear All, or clicking a DIFFERENT tool button ends
        the repeat.
        """
        self._set_active_tool(name)
        self._active_prompt = prompt
        if self._try_complete(name):
            self._notify_status(f"{prompt}  (placed -- pick/type another, "
                                f"or Cancel Tool to stop.)")
            return
        self._notify_status(prompt)

    def _retry_active_tool(self):
        """Called after a new pick or numeric value is queued. If a
        tool is currently waiting and now has enough input, complete
        it -- and STAY armed for another repeat (see _start_tool)."""
        if self._active_tool is None:
            return
        if self._try_complete(self._active_tool):
            self._notify_status(f"{self._active_prompt}  (placed -- "
                                f"pick/type another, or Cancel Tool to stop.)")

    def _do_cancel_tool(self):
        """Abandon whichever tool is currently waiting for input, and
        clear anything queued for it -- the non-blocking equivalent of
        clicking Cancel on the old popup dialogs, and what MainWindow's
        status-bar "End Operation" button calls when a sketch tool is
        the active operation."""
        self._set_active_tool(None)
        self._active_prompt = None
        self._pending_uvs = []
        self._pending_floats = []
        self._notify_status("Tool cancelled.")

    def _try_complete(self, name):
        """Dispatch to the matching _try_complete_* method. Each of
        those pops exactly what it needs from the pending queues and
        returns True ONLY if it actually completed the tool -- they
        never partially consume a queue and then fail."""
        return {
            "hcl": self._try_complete_hcl,
            "vcl": self._try_complete_vcl,
            "hvcl": self._try_complete_hvcl,
            "acl": self._try_complete_acl,
            "lbcl": self._try_complete_lbcl,
            "ccirc": lambda: self._try_complete_circle(constr=True),
            "circ": lambda: self._try_complete_circle(constr=False),
            "line": self._try_complete_line,
            "rect": self._try_complete_rect,
            "arcc2p": self._try_complete_arcc2p,
            "arc3p": self._try_complete_arc3p,
        }.get(name, lambda: False)()

    # ------------------------------------------------------------------
    # Tool completion -- each pops exactly what it needs and returns
    # True, or leaves the queues untouched and returns False.
    # ------------------------------------------------------------------

    def _try_complete_hcl(self):
        """
        H Cline needs 1 point -- a click, OR two typed/calculator
        floats (X then Y; only Y is actually used). Falls back to a
        single typed/calculator float as a direct Y-value shortcut if
        a full point isn't available (e.g. just one number typed).
        """
        pt = self._take_point()
        if pt is None:
            if not self._pending_floats:
                return False
            y = self._pending_floats.pop(0)
            pt = (0, y)
        self._workplane.hcl(pt)
        self._display_clines()
        return True

    def _try_complete_vcl(self):
        """V Cline: same as H Cline above, but for X."""
        pt = self._take_point()
        if pt is None:
            if not self._pending_floats:
                return False
            x = self._pending_floats.pop(0)
            pt = (x, 0)
        self._workplane.vcl(pt)
        self._display_clines()
        return True

    def _try_complete_hvcl(self):
        """H+V Cline needs 1 point -- a click, or two typed/calculator
        floats (X then Y)."""
        pt = self._take_point()
        if pt is None:
            return False
        self._workplane.hvcl(pt)
        self._display_clines()
        return True

    def _try_complete_acl(self):
        """
        Angled Cline needs 1 point (click or typed X,Y) + EITHER a 2nd
        point (direction; click or typed X,Y) OR a single leftover
        float (angle in degrees).
        """
        if self._available_points() >= 2:
            p1 = self._take_point()
            p2 = self._take_point()
            self._workplane.acl(p1, pnt2=p2)
        elif self._available_points() >= 1 and self._pending_floats:
            p1 = self._take_point()
            ang = self._pending_floats.pop(0)
            self._workplane.acl(p1, ang=ang)
        else:
            return False
        self._display_clines()
        return True

    def _try_complete_lbcl(self):
        """Linear Bisector needs 2 points (click, or typed X,Y each)."""
        if self._available_points() < 2:
            return False
        p1 = self._take_point()
        p2 = self._take_point()
        self._workplane.lbcl(p1, p2)
        self._display_clines()
        return True

    def _try_complete_circle(self, constr):
        """
        Circle / Constr Circle: TWO ways to specify it (mirrors
        KodaCAD) -- 2 points (center + point-on-circle, radius by
        distance; either point can be a click or typed X,Y), or 1
        point + a single leftover float (center + typed/calculator
        radius).
        """
        if self._available_points() >= 2:
            p1 = self._take_point()
            p2 = self._take_point()
            r = self._workplane.p2p_dist(p1, p2)
            self._workplane.circle(p1, r, constr=constr)
        elif self._available_points() >= 1 and self._pending_floats:
            p1 = self._take_point()
            r = self._pending_floats.pop(0)
            self._workplane.circle(p1, r, constr=constr)
        else:
            return False
        if constr:
            self._display_clines()
        else:
            self._display_profile(n_new=1)
        return True

    def _try_complete_line(self):
        """Line needs 2 points (click, or typed X,Y each)."""
        if self._available_points() < 2:
            return False
        p1 = self._take_point()
        p2 = self._take_point()
        self._workplane.line(p1, p2)
        self._display_profile(n_new=1)
        return True

    def _try_complete_rect(self):
        """Rectangle needs 2 points (click, or typed X,Y each)."""
        if self._available_points() < 2:
            return False
        p1 = self._take_point()
        p2 = self._take_point()
        self._workplane.rect(p1, p2)
        self._display_profile(n_new=4)
        return True

    def _try_complete_arcc2p(self):
        """Arc Ctr-2Pts needs 3 points: center, start, end (click, or
        typed X,Y each)."""
        if self._available_points() < 3:
            return False
        center = self._take_point()
        p1 = self._take_point()
        p2 = self._take_point()
        self._workplane.arcc2p(center, p1, p2)
        self._display_profile(n_new=1)
        return True

    def _try_complete_arc3p(self):
        """Arc 3Pts needs 3 points (click, or typed X,Y each)."""
        if self._available_points() < 3:
            return False
        p1 = self._take_point()
        p2 = self._take_point()
        p3 = self._take_point()
        self._workplane.arc3p(p1, p2, p3)
        self._display_profile(n_new=1)
        return True

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
        # "Cancel Tool" removed from the toolbar (PHASE 2 follow-up,
        # DESIGN_BACKLOG item 33) -- redundant with the status bar's
        # "End Operation" button, which does the same thing for sketch
        # tools AND also covers By-3-Points/On-Face picking. Toolbar
        # was getting crowded with more tools planned; _do_cancel_tool
        # itself stays, still called by MainWindow._on_end_operation.

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
        """Horizontal construction line through a point (or typed Y)."""
        if not self._guard():
            return
        self._start_tool(
            "hcl", "H Cline: pick a point, or type a Y value and press "
            "Enter (or send one from the calculator).")

    def _do_vcl(self):
        """Vertical construction line through a point (or typed X)."""
        if not self._guard():
            return
        self._start_tool(
            "vcl", "V Cline: pick a point, or type an X value and press "
            "Enter (or send one from the calculator).")

    def _do_hvcl(self):
        """H + V construction lines through a picked point."""
        if not self._guard():
            return
        self._start_tool("hvcl", "H+V Cline: pick a point.")

    def _do_acl(self):
        """Angled construction line through a point, toward a 2nd point
        or at a typed angle."""
        if not self._guard():
            return
        self._start_tool(
            "acl", "Angled Cline: pick a point, then either pick a 2nd "
            "point (sets direction) or type an angle in degrees and "
            "press Enter.")

    def _do_lbcl(self):
        """Linear bisector construction line between two picked points."""
        if not self._guard():
            return
        self._start_tool(
            "lbcl", "Linear Bisector: pick point 1, then point 2.")

    def _do_ccirc(self):
        """
        Construction circle. Two ways to specify it (mirrors KodaCAD):
          1. Pick center, then pick a point on the circle -- radius =
             distance between them.
          2. Pick center, then type a radius and press Enter (or send
             one from the calculator).
        """
        if not self._guard():
            return
        self._start_tool(
            "ccirc", "Constr Circle: pick a center, then pick a point "
            "on the circle, or type a radius and press Enter.")

    # ------------------------------------------------------------------
    # Profile geometry tools
    # ------------------------------------------------------------------

    def _do_line(self):
        """Profile line between two picked points."""
        if not self._guard():
            return
        self._start_tool(
            "line", "Line: pick the start point, then the end point.")

    def _do_rect(self):
        """Profile rectangle between two picked corner points."""
        if not self._guard():
            return
        self._start_tool(
            "rect", "Rectangle: pick corner 1, then corner 2.")

    def _do_circ(self):
        """Profile circle. Same two entry paths as _do_ccirc above."""
        if not self._guard():
            return
        self._start_tool(
            "circ", "Circle: pick a center, then pick a point on the "
            "circle, or type a radius and press Enter.")

    def _do_arcc2p(self):
        """Arc: pick center, then start point, then end point."""
        if not self._guard():
            return
        self._start_tool(
            "arcc2p", "Arc Ctr-2Pts: pick center, then start point, "
            "then end point.")

    def _do_arc3p(self):
        """Arc through three picked points."""
        if not self._guard():
            return
        self._start_tool(
            "arc3p", "Arc 3Pts: pick point 1, point 2, then point 3.")

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

        # Recompute and display intersection point markers
        self._display_intersections()

    def _display_intersections(self):
        """
        Compute all intersection points, store them for snap picking,
        and display them as yellow vertex markers using AIS.

        The markers are displayed AND activated for OCCT selection so
        they show a cyan hover highlight. Clicking them fires
        geometry_picked with TopAbs_VERTEX, which main_app routes to
        check_intersection_snap().
        """
        self._erase_isect_ais()
        wp = self._workplane
        if wp is None:
            return

        pts = wp.intersectPts()
        if not pts:
            print("[SketchToolBar] _display_intersections: no intersection "
                  "points found (no construction geometry yet?).")
            return

        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
        from OCP.Quantity import Quantity_Color, Quantity_TypeOfColor
        from OCP.TopAbs import TopAbs_VERTEX

        ctx = self._viewport.context
        yellow = Quantity_Color(1.0, 1.0, 0.0,
                                Quantity_TypeOfColor.Quantity_TOC_RGB)
        vertex_mode = AIS_Shape.SelectionMode_s(TopAbs_VERTEX)

        for pnt in pts:
            # Compute (u, v) by inverse-transforming back to workplane coords
            inv_trsf = wp.Trsf.Inverted()
            local_pnt = pnt.Transformed(inv_trsf)
            uv = (local_pnt.X(), local_pnt.Y())

            # Build a TopoDS_Vertex at the intersection 3D point
            vertex_shape = BRepBuilderAPI_MakeVertex(pnt).Vertex()
            ais = AIS_Shape(vertex_shape)
            ais.SetColor(yellow)
            ais.SetWidth(8.0)

            ctx.Display(ais, False)
            # Activate for vertex selection -- gives hover highlight and
            # fires geometry_picked on click. Widen the hit-test radius
            # (OCCT's default vertex sensitivity is only a couple of
            # pixels, easy to miss) -- matches the sensitivity already
            # used for active-part vertex picking in main_app.py.
            ctx.Activate(ais, vertex_mode)
            ctx.SetSelectionSensitivity(ais, vertex_mode, 10)

            self._isect_ais.append(ais)
            self._isect_pts[id(ais)] = uv

        ctx.UpdateCurrentViewer()
        self._viewport.update()
        print(f"[SketchToolBar] _display_intersections: displayed "
              f"{len(pts)} clickable intersection marker(s).")

    def find_nearest_intersection_uv(self, screen_x, screen_y, view,
                                     snap_radius_px=15):
        """
        Find the intersection point nearest to (screen_x, screen_y) within
        snap_radius_px pixels. Returns (u, v) if found, None otherwise.
        Uses screen-space projection rather than OCCT selection so it
        doesn't interfere with the viewport's normal navigation.
        """
        if not self._isect_pts or self._workplane is None:
            return None

        from OCP.gp import gp_Pnt
        best_uv = None
        best_dist_sq = snap_radius_px ** 2

        for pnt_uv in self._isect_pts.values():
            pnt3d, uv = pnt_uv
            # Project 3D point to screen coordinates
            try:
                sx, sy, sz = view.Project(pnt3d.X(), pnt3d.Y(), pnt3d.Z())
                # view.Project returns normalized device coords [-1,1],
                # need to convert to pixel coords
                vx, vy, vw, vh = view.Window().Size() if hasattr(view.Window(), 'Size') else (0, 0, 800, 600)
                # Use Convert instead
                px, py = view.Convert(pnt3d.X(), pnt3d.Y(), pnt3d.Z())
                dist_sq = (px - screen_x) ** 2 + (py - screen_y) ** 2
                if dist_sq < best_dist_sq:
                    best_dist_sq = dist_sq
                    best_uv = uv
            except Exception:
                pass

        return best_uv

    def _erase_isect_ais(self):
        """Remove all intersection point markers from the viewport context."""
        if not self._isect_ais or self._viewport is None:
            self._isect_ais = []
            self._isect_pts = {}
            return
        ctx = self._viewport.context
        ctx.ClearSelected(False)
        for ais in self._isect_ais:
            try:
                # Deactivate ALL selection modes before Remove -- leaving
                # activated AIS objects in the selection index after Remove
                # causes context.Select() to crash on background clicks.
                ctx.Deactivate(ais)
                ctx.Remove(ais, False)
            except Exception:
                pass
        ctx.UpdateCurrentViewer()
        self._viewport.update()
        self._isect_ais = []
        self._isect_pts = {}

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
