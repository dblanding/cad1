"""
assembly_viewer.py

THE OCCT 3D VIEWPORT -- base class for the cad1 application window.

Wraps an OCCT V3d_View inside a Qt widget and provides:
  - Load and display a build123d Compound assembly as individual AIS_Shape
    objects, one per LEAF SOLID, so clicking a shape resolves to a specific
    part (not just "somewhere in the assembly").
  - Hover highlighting and click-to-select via AIS_ViewController --
    OCCT's own input serialization layer. Replaces direct MoveTo()/
    Select() calls which caused C++ segfaults on rapid/double clicks.
    See DESIGN_BACKLOG item 20.
  - STEP color reading from node.color.wrapped (Quantity_ColorRGBA).
    Falls back to a small deterministic palette when no color is stored.
  - Black face boundary edges on all shaded parts (SetFaceBoundaryDraw).
  - AIS ViewCube in the corner for orientation reference.
  - Orbit/pan/zoom via left/middle/right mouse buttons.
  - show/hide per part, highlight_node() for tree-to-viewport sync.

SUBCLASSING PATTERN:
  SyncedViewportWidget in main_app.py subclasses this to add:
    - _node_id_to_ais_shape dict (for fast node-to-AIS lookup)
    - Edge/vertex selection mode management
    - Active-part orange overlay
    - All signal connections to the tree widget and dialogs
  This base class stays clean and independently runnable:
    uv run gui/assembly_viewer.py step/as1-oc-214.stp

LOCATION INVARIANT (critical -- read before touching _display_leaf):
  node._wrapped.Location() encodes this node's OWN local transform.
  node.global_location is computed by build123d as the product of all
  ancestor node.location values from root to this node. _display_leaf
  positions the AIS_Shape via node.wrapped.Located(global_loc.wrapped),
  which REPLACES (not compounds) the shape's location tag with the full
  world transform.

  Boolean operations (MakeFillet, BRepAlgoAPI_Cut, etc.) STRIP the
  location from their result -- mk.Shape() always has IsIdentity=True.
  After storing a stripped result as node._wrapped, node.global_location
  computes incorrectly (missing the node's own rotation). Fix: capture
  global_location and node.location BEFORE replacing _wrapped, then
  display with override_location=parent_global. See _apply_shape_to_node
  in main_app.py and DESIGN_BACKLOG item 24.
"""
import sys
import os

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import Qt

from OCP.Aspect import Aspect_DisplayConnection, Aspect_TypeOfTriedronPosition
from OCP.OpenGl import OpenGl_GraphicDriver
from OCP.V3d import V3d_Viewer
from OCP.AIS import AIS_InteractiveContext, AIS_Shape, AIS_DisplayMode
from OCP.Xw import Xw_Window
from OCP.Quantity import Quantity_Color, Quantity_TypeOfColor
from OCP.Graphic3d import Graphic3d_Camera
from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX

from build123d import import_step

# Reuse the existing, proven assembly-tree walking logic rather than
# reimplementing it -- src/step_assembly_poc.py already has
# print_tree()-style recursion that's been validated against this
# exact file (as1-oc-214.stp) in the previous step of this project.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from step_assembly_poc import load_assembly  # noqa: E402
from pose import PointRef, DirectionRef  # noqa: E402


# A small, deterministic fallback palette (RGB 0-1 floats) for parts
# with no embedded STEP color -- cycled by leaf index so siblings are
# at least visually distinguishable even without real color data.
# Saturated/mid-value on purpose: the first version of this palette
# used pale, washed-out tones (e.g. 0.75,0.75,0.78) which combined
# with the light gray background to look "hazy" -- richer colors here
# should give more visible contrast and shading definition.
FALLBACK_PALETTE = [
    (0.75, 0.20, 0.15),  # brick red
    (0.20, 0.45, 0.75),  # steel blue
    (0.85, 0.65, 0.10),  # amber/brass
    (0.30, 0.60, 0.30),  # forest green
    (0.55, 0.30, 0.65),  # plum
    (0.80, 0.45, 0.15),  # copper/orange
]




class OcctViewportWidget(QWidget):
    """
    The minimum possible Qt widget that hosts an OCCT 3D view.

    This is deliberately NOT trying to be a polished/reusable
    component yet -- it's scaffolding to answer one yes/no question.
    Once we know this works (or what specifically breaks), this is
    the piece to harden into something step_assembly_poc-style tools
    can build on.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        # A native (non-Qt-painted) window is required: OCCT draws
        # directly into the window's native surface via OpenGL, not
        # through Qt's paint system. WA_PaintOnScreen + WA_NativeWindow
        # tell Qt "don't try to manage painting here yourself."
        self.setAttribute(Qt.WidgetAttribute.WA_PaintOnScreen)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        # REQUIRED for hover-highlight: by default Qt only sends
        # mouseMoveEvent while a button is held. MoveTo()-based
        # highlighting needs move events on EVERY mouse movement,
        # button or no button.
        self.setMouseTracking(True)

        self.display_connection = Aspect_DisplayConnection()
        self.graphic_driver = OpenGl_GraphicDriver(self.display_connection)

        self.viewer = V3d_Viewer(self.graphic_driver)
        self.viewer.SetDefaultLights()
        self.viewer.SetLightOn()

        self.view = self.viewer.CreateView()
        self.context = AIS_InteractiveContext(self.viewer)

        self._occt_window = None  # created lazily once we have a real winId()
        self._last_mouse_pos = None

        # Multi-part tracking: one AIS_Shape per LEAF SOLID, plus a
        # reverse map so that when something gets picked, we can look
        # up which assembly-tree node (and therefore which part name/
        # path) it corresponds to -- not just which face.
        self._ais_shapes = []                # all displayed AIS_Shape objects
        self._ais_shape_to_node = {}          # id(AIS_Shape) -> tree node + label info
        self._view_cube = None               # AIS_ViewCube, set in _init_native_window

        # Click-vs-drag tracking: LMB is used for BOTH rotation (drag)
        # and picking (click-no-drag). We distinguish them by how far
        # the mouse moved between press and release.
        self._press_pos = None
        self._drag_distance = 0.0
        self._click_drag_threshold_px = 4

        # AIS_ViewController -- replaces direct context.MoveTo()/Select()
        # calls. Serializes all mouse input to prevent re-entrancy crashes.
        from OCP.AIS import AIS_ViewController
        self._vc = AIS_ViewController()  # movement below this = treat as a click

    def showEvent(self, event):
        super().showEvent(event)
        if self._occt_window is None:
            self._init_native_window()

    def _init_native_window(self):
        """
        Wire the OCCT view to THIS widget's actual native window
        handle. Must happen after the widget is shown (winId() isn't
        meaningful before then on some platforms).
        """
        # Xw_Window is the X11/Linux binding for OCCT's window
        # abstraction (the Windows equivalent is WNT_Window, macOS
        # uses Cocoa_Window -- this script assumes Linux/X11, which
        # matches the environment this is being tested in).
        self._occt_window = Xw_Window(self.display_connection, int(self.winId()))

        if not self._occt_window.IsMapped():
            self._occt_window.Map()

        self.view.SetWindow(self._occt_window)
        # Quantity_Color must be constructed explicitly -- passing the
        # raw Quantity_NameOfColor enum member directly to
        # SetBackgroundColor() doesn't match either of its overloads
        # (confirmed against the real error message: it wants either
        # a Quantity_Color object, or a type-enum + 3 float channels).
        # Using a medium gray (0.5, 0.5, 0.5) here as a simple,
        # unambiguous stand-in for "GRAY50".
        background = Quantity_Color(0.5, 0.5, 0.5, Quantity_TypeOfColor.Quantity_TOC_RGB)
        self.view.SetBackgroundColor(background)
        self.view.MustBeResized()

        # Replace the useless center trihedron with an AIS_ViewCube
        # in the corner -- same as CAD Assistant uses. Clickable faces
        # (6), edges (12), and corners (8) animate the camera to the
        # corresponding standard view.
        self._view_cube = None
        try:
            from OCP.AIS import AIS_ViewCube
            vc = AIS_ViewCube()
            # Place in bottom-right corner with a reasonable size.
            vc.SetSize(80)
            vc.SetBoxFacetExtension(8)
            vc.SetAxesPadding(5)
            vc.SetFontHeight(12)
            # Use transform persistence to keep it fixed in the corner.
            from OCP.Graphic3d import (
                Graphic3d_TransformPers,
                Graphic3d_TransModeFlags,
            )
            from OCP.Graphic3d import Graphic3d_Vec2i
            trsf_pers = Graphic3d_TransformPers(
                Graphic3d_TransModeFlags.Graphic3d_TMF_TriedronPers,
                Aspect_TypeOfTriedronPosition.Aspect_TOTP_RIGHT_LOWER,
                Graphic3d_Vec2i(100, 100)
            )
            vc.SetTransformPersistence(trsf_pers)
            self.context.Display(vc, False)
            self._view_cube = vc
            print("AIS_ViewCube added to corner.")
        except Exception as e:
            print(f"(AIS_ViewCube not available: {e} -- falling back to trihedron)")
            self.view.TriedronDisplay()

        # CRITICAL: OCCT's View does NOT redraw itself on any kind of
        # automatic schedule. Setting WA_PaintOnScreen above tells Qt
        # "don't manage painting yourself" -- correct, since OCCT
        # draws directly to the native surface -- but it ALSO means
        # nothing was previously telling OCCT when to actually render
        # a frame. Confirmed against OCCT's own forum: redraws need
        # to be triggered explicitly, typically from paintEvent().
        # Without this, the window exists and has a valid OpenGL
        # context, but nothing ever gets drawn into it -- which
        # matches the solid-black-window symptom exactly (a known,
        # documented Qt+OpenGL gotcha, not specific to OCCT).
        self.update()

    def display_node(self, node, path_prefix="", override_location=None):
        """Display a single leaf node, optionally with an overridden location."""
        palette_index = len(self._ais_shapes)
        if not node.children and node.wrapped is not None:
            self._display_leaf(node, path_prefix, palette_index,
                               override_location=override_location)
            self.context.UpdateCurrentViewer()
            self.update()
            return 1
        return self.display_subtree(node, path_prefix)

    def display_subtree(self, node, path_prefix=""):
        """
        Walk a node's subtree and display all leaf solids, adding them
        to the existing viewport without clearing what's already there.
        Used by the Import STEP workflow to add newly-imported geometry
        alongside what's already displayed.
        """
        leaf_count = 0
        palette_index = len(self._ais_shapes)  # continue from current count

        def walk(n, path):
            nonlocal leaf_count, palette_index
            current_path = f"{path}/{n.label}" if n.label else path
            if not n.children:
                if n.wrapped is None:
                    return
                self._display_leaf(n, current_path, palette_index)
                leaf_count += 1
                palette_index += 1
            else:
                for child in n.children:
                    walk(child, current_path)

        walk(node, path_prefix)
        self.context.UpdateCurrentViewer()
        self.update()
        print(f"Displayed {leaf_count} new leaf solids.")
        return leaf_count

    def load_and_display_assembly(self, step_path):
        """
        Load a STEP assembly via the proven load_assembly() (same
        function validated against this exact file in
        step_assembly_poc.py) and display each LEAF SOLID as its own
        AIS_Shape, shaded, colored, with face-level selection active.
        """
        assembly = load_assembly(step_path)

        leaf_count = 0
        palette_index = 0

        def walk(node, path):
            nonlocal leaf_count, palette_index
            current_path = f"{path}/{node.label}" if node.label else path

            if not node.children:
                # Leaf node -- display it.
                if node.wrapped is None:
                    return
                self._display_leaf(node, current_path, palette_index)
                leaf_count += 1
                palette_index += 1
            else:
                for child in node.children:
                    walk(child, current_path)

        walk(assembly, "")

        print(f"Displayed {leaf_count} leaf solids.")

        self.view.MustBeResized()
        self.view.FitAll()
        self.view.ZFitAll()
        self.update()

        return assembly

    def _display_leaf(self, node, path, palette_index, override_location=None):
        """
        Create one shaded, colored AIS_Shape for a single leaf solid,
        and record it in the pick -> part lookup map.

        CRITICAL FIX: use node.global_location, not node.wrapped
        directly. build123d's own changelog confirms this exact
        problem is real: "Added a new Shape property - global_location
        which will provide the location of a part relative to the
        global coordinate system when it's deep within an assembly."
        Without this, each leaf displays at its PARENT-RELATIVE local
        position rather than its assembled world position -- which
        matches exactly what showed up in testing: repeated instances
        (multiple nuts, multiple l-bracket-assemblies) collapsing onto
        each other instead of appearing at their distinct assembled
        locations. The tree STRUCTURE was always correct (proven
        extensively during the STEP export investigation); only the
        WORLD POSITION resolution was untested before this.

        Color priority: use the part's real STEP color if
        import_step() recovered one (node.color), otherwise fall back
        to a deterministic palette entry so siblings are at least
        visually distinguishable. Most STEP files -- including the
        as1-oc-214.stp sample this was first tested against -- carry
        no embedded color data, so the fallback path is the common
        case in practice, not a rare edge case.
        """
        # FIX (v2): diagnose_global_location.py's output revealed that
        # node.wrapped ALREADY carries a non-identity Location baked
        # in for EVERY leaf (confirmed: IsIdentity() == False on all
        # of them) -- almost certainly the same parent-relative
        # transform as node.location, put there by import_step()'s
        # own tree-reconstruction logic. The previous version's
        # node.moved(node.global_location) COMPOSED global_location
        # on top of that already-baked-in local transform, double-
        # applying each leaf's own local offset/rotation. That matches
        # the symptom exactly: parts with zero local transform (rod,
        # plate) looked fine, while parts with real local transforms
        # (bolts, nuts) were scattered -- proportional to their own
        # local Location values.
        #
        # Fix: bypass build123d's .moved()/.located() naming ambiguity
        # entirely and use OCP's TopoDS_Shape.Located() directly --
        # this REPLACES a shape's location outright (confirmed,
        # unambiguous contract; this is the same .Located() call used
        # successfully in the manual-fallback path below already).
        global_loc = override_location if override_location is not None \
            else node.global_location
        shape_to_display = node.wrapped.Located(global_loc.wrapped)

        ais_shape = AIS_Shape(shape_to_display)

        if node.color is not None:
            # build123d Color.wrapped is Quantity_ColorRGBA in this OCP build.
            # GetRGB() returns a Quantity_Color with Red/Green/Blue methods.
            try:
                rgba = node.color.wrapped
                rgb = rgba.GetRGB()
                r, g, b = rgb.Red(), rgb.Green(), rgb.Blue()
            except Exception:
                r, g, b = FALLBACK_PALETTE[palette_index % len(FALLBACK_PALETTE)]
        else:
            r, g, b = FALLBACK_PALETTE[palette_index % len(FALLBACK_PALETTE)]

        color = Quantity_Color(r, g, b, Quantity_TypeOfColor.Quantity_TOC_RGB)

        # Set color and display mode on the AIS_Shape BEFORE calling
        # context.Display() -- confirmed against multiple OCCT forum
        # threads that setting these AFTER Display() can silently
        # produce a wireframe result instead of shaded in some
        # context configurations.
        ais_shape.SetColor(color)
        ais_shape.SetDisplayMode(AIS_DisplayMode.AIS_Shaded)

        # Crisp black face boundary edges over the shaded display.
        from OCP.Quantity import Quantity_NOC_BLACK
        from OCP.Aspect import Aspect_TOL_SOLID
        drawer = ais_shape.Attributes()
        drawer.SetFaceBoundaryDraw(True)
        drawer.FaceBoundaryAspect().SetColor(Quantity_Color(Quantity_NOC_BLACK))
        drawer.FaceBoundaryAspect().SetWidth(1.0)
        drawer.FaceBoundaryAspect().SetTypeOfLine(Aspect_TOL_SOLID)

        self.context.Display(ais_shape, True)
        # FIX: AIS_Shape's selection-mode integers are NOT guaranteed
        # to be numerically identical to TopAbs_ShapeEnum's own values
        # -- confirmed via OCCT's own docs: "The Selection Mode for a
        # specific shape type (TopAbs_ShapeEnum) is returned by method
        # AIS_Shape::SelectionMode()". Passing TopAbs_FACE directly
        # here "worked" only because its value happened to coincide
        # with what SelectionMode() would have returned -- not because
        # it's a valid general pattern. Using the documented
        # translation method instead, so this is correct for EVERY
        # shape type, not just FACE by coincidence.
        #
        # NOTE: this OCP build exposes the method as the STATIC form
        # SelectionMode_s (called on the CLASS, AIS_Shape, not on the
        # instance ais_shape) -- confirmed directly from the runtime
        # AttributeError's own suggestion ("Did you mean:
        # 'SelectionMode_s'?"). Same "_s" convention bit us once
        # before during the STEP export investigation
        # (FindShape/FindShape_s) -- OCP appends _s to methods that
        # are genuinely static in the underlying OCCT C++ class.
        self.context.Activate(ais_shape, AIS_Shape.SelectionMode_s(TopAbs_FACE))

        self._ais_shapes.append(ais_shape)
        self._ais_shape_to_node[id(ais_shape)] = {
            "label": node.label,
            "path": path,
            "node": node,
            "color_rgb": (r, g, b),
        }

    def paintEvent(self, event):
        # This is the actual redraw trigger. Even with WA_PaintOnScreen
        # set, Qt still calls paintEvent() -- it just won't use its
        # own QPainter machinery to do anything with the result, which
        # is exactly what we want: we use this purely as "now is a
        # good time to ask OCCT to render a frame."
        if self._occt_window is not None:
            self.view.Redraw()

    # --- Mouse handling: orbit / pan / zoom / PICKING ---------------
    # Builds on the proven-working rotate/pan/zoom from
    # viewport_smoke_test.py, adding:
    # ----------------------------------------------------------------
    # Mouse event handling via AIS_ViewController
    #
    # AIS_ViewController serializes all mouse input internally, which
    # prevents the context.MoveTo()/Select() re-entrancy crashes that
    # occurred with direct OCCT calls. The pattern:
    #   1. Qt mouse events feed data to the controller via
    #      UpdateMousePosition() / UpdateMouseButtons() / UpdateMouseScroll()
    #   2. FlushViewEvents(ctx, view, True) processes the buffered
    #      events -- OCCT handles navigation (rotate/pan/zoom) AND
    #      selection internally.
    #   3. OnSelectionChanged() is called by OCCT when a selection
    #      event completes; we override it to call _report_selection().
    #
    # Button constants (plain ints, not an enum in this OCP build):
    #   Left   = Aspect_VKeyMouse_LeftButton   = 8192
    #   Middle = Aspect_VKeyMouse_MiddleButton  = 16384
    #   Right  = Aspect_VKeyMouse_RightButton   = 32768

    def _qt_buttons_to_occt(self, qt_buttons):
        """Convert Qt MouseButtons flags to OCCT VKeyMouse int."""
        result = 0
        if qt_buttons & Qt.MouseButton.LeftButton:
            result |= 8192
        if qt_buttons & Qt.MouseButton.MiddleButton:
            result |= 16384
        if qt_buttons & Qt.MouseButton.RightButton:
            result |= 32768
        return result

    def _qt_pos_to_vec2i(self, pos):
        """Convert Qt position to Graphic3d_Vec2i."""
        from OCP.Graphic3d import Graphic3d_Vec2i
        return Graphic3d_Vec2i(int(pos.x()), int(pos.y()))

    def mousePressEvent(self, event):
        self._last_mouse_pos = event.position()
        pt = self._qt_pos_to_vec2i(event.position())
        btns = self._qt_buttons_to_occt(event.buttons())
        self._vc.UpdateMouseButtons(pt, btns, 0, False)
        self._flush()

    def mouseMoveEvent(self, event):
        pos = event.position()
        self._last_mouse_pos = pos
        pt = self._qt_pos_to_vec2i(pos)
        btns = self._qt_buttons_to_occt(event.buttons())
        self._vc.UpdateMousePosition(pt, btns, 0, False)
        self._flush()

    def mouseReleaseEvent(self, event):
        self._last_mouse_pos = event.position()
        pt = self._qt_pos_to_vec2i(event.position())
        # Release: buttons AFTER release (so released button is absent)
        btns = self._qt_buttons_to_occt(event.buttons())
        self._vc.UpdateMouseButtons(pt, btns, 0, False)
        self._flush()

        # RMB: show context menu (AIS_ViewController handles navigation,
        # we handle the application-level menu ourselves)
        if event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())

    def mouseDoubleClickEvent(self, event):
        # Swallow double-clicks -- the view cube handles its own
        # animation; we don't want a double-click to trigger a second
        # selection on top of the first.
        event.accept()

    def _animate_view_cube(self):
        """Run the view cube camera animation to completion."""
        if self._view_cube is None:
            return
        try:
            from PySide6.QtCore import QCoreApplication
            while self._view_cube.HasAnimation():
                self._view_cube.UpdateAnimation(False)
                self.context.UpdateCurrentViewer()
                QCoreApplication.processEvents()
            self.view.FitAll()
            self.view.ZFitAll()
        except Exception as e:
            print(f"(view cube animation: {e})")
        self.update()

    def _flush(self):
        """Process buffered OCCT view events and redraw."""
        if self._occt_window is None:
            return
        try:
            self._vc.FlushViewEvents(self.context, self.view, True)
        except Exception:
            pass
        self.update()

    def _show_context_menu(self, global_pos):
        """RMB context menu with common view commands."""
        from PySide6.QtWidgets import QMenu
        from PySide6.QtGui import QAction

        menu = QMenu(self)

        fit_all = QAction("Fit All", self)
        fit_all.triggered.connect(self._fit_all)
        menu.addAction(fit_all)

        fit_selected = QAction("Fit Selected", self)
        fit_selected.triggered.connect(self._fit_selected)
        menu.addAction(fit_selected)

        reset_view = QAction("Reset View (isometric)", self)
        reset_view.triggered.connect(self._reset_view)
        menu.addAction(reset_view)

        menu.addSeparator()

        view_top = QAction("View Top", self)
        view_top.triggered.connect(lambda: self._set_view_direction(0, 0, -1, 0, 1, 0))
        menu.addAction(view_top)

        view_front = QAction("View Front", self)
        view_front.triggered.connect(lambda: self._set_view_direction(0, -1, 0, 0, 0, 1))
        menu.addAction(view_front)

        view_right = QAction("View Right", self)
        view_right.triggered.connect(lambda: self._set_view_direction(1, 0, 0, 0, 0, 1))
        menu.addAction(view_right)

        menu.exec(global_pos)

    def _fit_all(self):
        self.view.FitAll()
        self.update()

    def _fit_selected(self):
        try:
            self.context.InitSelected()
            if self.context.MoreSelected():
                self.view.FitAll()
        except Exception:
            self.view.FitAll()
        self.update()

    def _reset_view(self):
        try:
            from OCP.V3d import V3d_TypeOfOrientation
            self.view.SetProj(V3d_TypeOfOrientation.V3d_XposYnegZpos)
            self.view.FitAll()
        except Exception as e:
            print(f"(reset view: {e})")
            self.view.FitAll()
        self.update()

    def _set_view_direction(self, vx, vy, vz, ux, uy, uz):
        try:
            self.view.SetProj(vx, vy, vz)
            self.view.SetUp(ux, uy, uz)
            self.view.FitAll()
        except Exception as e:
            print(f"(set view direction: {e})")
        self.update()

    def _report_selection(self):
        """
        Print whatever is currently selected in the context, AND which
        assembly part it belongs to. This is deliberately just
        printing to the terminal for now -- the real consumer of this
        (pose.py's PointRef/DirectionRef) comes later, once we know
        picking itself actually works correctly on a real multi-part
        assembly, not just a single box.
        """
        self.context.InitSelected()
        if not self.context.MoreSelected():
            print("Click registered, but nothing was selected (missed a part?)")
            return

        count = 0
        while self.context.MoreSelected():
            count += 1
            shape = self.context.SelectedShape()
            shape_type = shape.ShapeType()
            type_name = {
                TopAbs_FACE: "FACE",
                TopAbs_EDGE: "EDGE",
                TopAbs_VERTEX: "VERTEX",
            }.get(shape_type, f"OTHER({shape_type})")

            # Identify WHICH PART this face/edge/vertex belongs to, by
            # looking up the owning AIS_Shape in our pick->node map.
            # SelectedInteractive() returns the AIS_InteractiveObject
            # that owns whatever sub-shape got picked.
            part_info = "unknown part"
            try:
                owner_ais = self.context.SelectedInteractive()
                node_info = self._ais_shape_to_node.get(id(owner_ais))
                if node_info is not None:
                    part_info = f"part={node_info['label']!r}  path={node_info['path']!r}"
            except Exception as e:
                part_info = f"(could not resolve owning part: {e})"

            # THE REAL INTEGRATION TEST: resolve this pick through
            # pose.py's actual PointRef/DirectionRef classes, not just
            # a manual geom_type print. This is the first time picked
            # geometry from a REAL STEP file (not synthetic test data)
            # flows through the full chain: OCCT pick -> raw
            # TopoDS_Shape -> build123d wrapper -> PointRef/
            # DirectionRef.resolve() -> (for circular edges) the
            # circle-fit fallback, verified on synthetic geometry but
            # never yet exercised against a real picked edge.
            pose_detail = ""
            try:
                from build123d import Edge as B123Edge, Face as B123Face

                if shape_type == TopAbs_EDGE:
                    wrapped_edge = B123Edge(shape)
                    # Try circle_center/circle_axis FIRST -- this now
                    # includes the verified circle-fit fallback, so it
                    # correctly handles both genuine geom_type==CIRCLE
                    # edges AND edges like as1-oc-214.stp's rod (BSPLINE-
                    # classified but geometrically circular/semi-
                    # circular, per Doug's own topology analysis: the
                    # rod is 4 shells, 2 flat circular ends + 2
                    # semi-cylindrical sides).
                    try:
                        center = PointRef(kind="circle_center", shape=wrapped_edge).resolve()
                        axis = DirectionRef(kind="circle_axis", shape=wrapped_edge).resolve()
                        pose_detail = (
                            f"  |  pose.py: circle_center={center}  "
                            f"circle_axis={axis}"
                        )
                    except ValueError:
                        # Genuinely not circular (even the fit fallback
                        # rejected it) -- fall back to edge_direction,
                        # the right resolution for a straight edge.
                        #
                        # DIAGNOSTIC ADDITION: this exact fallback path
                        # crashed twice in testing ("vector has zero
                        # norm" from position_at(0)==position_at(1)),
                        # but a standalone re-check of several plate
                        # edges via the SAME position_at() calls found
                        # nothing wrong -- meaning the failure is
                        # specific to particular edges not yet directly
                        # inspected. Printing full diagnostic data
                        # HERE, in the live failing path, so the next
                        # reproduction captures everything about the
                        # ACTUAL failing edge rather than a guess at a
                        # similar one.
                        print(f"    [debug] edge_direction about to resolve. "
                              f"geom_type={wrapped_edge.geom_type}  "
                              f"length={getattr(wrapped_edge, 'length', '?')}")
                        try:
                            p0 = wrapped_edge.position_at(0)
                            p1 = wrapped_edge.position_at(1)
                            print(f"    [debug] position_at(0)={p0}  position_at(1)={p1}")
                            verts = wrapped_edge.vertices()
                            print(f"    [debug] vertices()={[tuple(v) for v in verts]}")
                        except Exception as debug_e:
                            print(f"    [debug] (debug inspection itself failed: {debug_e})")

                        direction = DirectionRef(kind="edge_direction", shape=wrapped_edge).resolve()
                        midpoint = PointRef(kind="edge_midpoint", shape=wrapped_edge).resolve()
                        pose_detail = (
                            f"  |  pose.py: edge_midpoint={midpoint}  "
                            f"edge_direction={direction}"
                        )

                elif shape_type == TopAbs_FACE:
                    wrapped_face = B123Face(shape)
                    center = PointRef(kind="face_center", shape=wrapped_face).resolve()
                    normal = DirectionRef(kind="face_normal", shape=wrapped_face).resolve()
                    pose_detail = (
                        f"  |  pose.py: face_center={center}  face_normal={normal}"
                    )
            except Exception as e:
                pose_detail = f"  |  pose.py resolution FAILED: {type(e).__name__}: {e}"

            print(f"Selected #{count}: {type_name}{pose_detail}  |  {part_info}")
            self.context.NextSelected()

    def wheelEvent(self, event):
        from OCP.Aspect import Aspect_ScrollDelta
        from OCP.Graphic3d import Graphic3d_Vec2i
        pt = Graphic3d_Vec2i(int(event.position().x()),
                             int(event.position().y()))
        delta = Aspect_ScrollDelta(pt, event.angleDelta().y() / 120.0)
        self._vc.UpdateMouseScroll(delta)
        self._flush()

    def OnSelectionChanged(self, theCtx, theView):
        """
        Called by AIS_ViewController when a selection event completes.
        Override to route into our _report_selection() logic.
        """
        self._report_selection()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._occt_window is not None:
            self.view.MustBeResized()
            self.update()

    def paintEngine(self):
        # Returning None tells Qt "don't use your own paint engine
        # here" -- required alongside WA_PaintOnScreen for OpenGL
        # surfaces OCCT manages directly.
        return None


def main():
    if len(sys.argv) < 2:
        print("Usage: assembly_viewer.py <path/to/assembly.step>")
        sys.exit(1)

    step_path = sys.argv[1]

    app = QApplication(sys.argv)

    window = QWidget()
    window.setWindowTitle(
        f"Assembly viewer -- {step_path} -- click a face (no drag) to "
        "select it, watch the terminal"
    )
    window.resize(1000, 700)

    from PySide6.QtWidgets import QVBoxLayout
    layout = QVBoxLayout(window)
    layout.setContentsMargins(0, 0, 0, 0)

    viewport = OcctViewportWidget(window)
    layout.addWidget(viewport)

    window.show()

    # Queue this to run after the CURRENT event loop iteration
    # finishes, rather than guessing a fixed delay (an earlier version
    # of this code used a 100ms guess that raced the window/layout
    # settling into its final size, causing a small-shape-in-the-
    # corner bug -- fixed by both this 0ms deferral AND an explicit
    # MustBeResized() call inside load_and_display_assembly() itself,
    # as a second safety net).
    from PySide6.QtCore import QTimer
    QTimer.singleShot(0, lambda: viewport.load_and_display_assembly(step_path))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
