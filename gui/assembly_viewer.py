"""
assembly_viewer.py

Builds on the PROVEN-WORKING picking_smoke_test.py (hover-highlight
and click-to-select both confirmed working) to display a REAL STEP
assembly instead of a synthetic box -- one AIS_Shape per LEAF SOLID
(not one big AIS_Shape for the whole assembly), so picking can answer
"which part" as well as "which face."

Each leaf gets:
    - Its own AIS_Shape (so a click resolves to a specific part)
    - Shaded display mode (not the wireframe used in earlier scripts)
    - Its real STEP color if import_step() recovered one, otherwise a
      deterministic fallback color from a small palette, cycled by
      tree position -- most STEP files (including the as1-oc-214.stp
      sample used to first test this) carry no embedded color data,
      so the fallback path is the common case in practice, not an
      edge case.

Usage:
    uv run gui/assembly_viewer.py step/as1-oc-214.stp
"""

import sys
import os

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import Qt

from OCP.Aspect import Aspect_DisplayConnection
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

        # Click-vs-drag tracking: LMB is used for BOTH rotation (drag)
        # and picking (click-no-drag). We distinguish them by how far
        # the mouse moved between press and release.
        self._press_pos = None
        self._drag_distance = 0.0
        self._click_drag_threshold_px = 4  # movement below this = treat as a click

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
        self.view.TriedronDisplay()  # small axis indicator, like the
                                       # one visible in your CAD
                                       # Assistant screenshot

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

    def _display_leaf(self, node, path, palette_index):
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
        global_loc = node.global_location
        shape_to_display = node.wrapped.Located(global_loc.wrapped)

        ais_shape = AIS_Shape(shape_to_display)

        if node.color is not None:
            try:
                r, g, b = node.color.to_tuple()[:3]
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
    #   - MoveTo() on every mouse move -> hover-highlight under cursor
    #   - Select() on a LEFT CLICK (press+release with minimal
    #     movement) -> actually pick whatever's highlighted
    # The existing LMB-drag-to-rotate behavior is preserved; click vs.
    # drag is distinguished by total movement between press and
    # release.

    def mousePressEvent(self, event):
        self._last_mouse_pos = event.position()
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position()
            self._drag_distance = 0.0
            self.view.StartRotation(int(event.position().x()), int(event.position().y()))

    def mouseMoveEvent(self, event):
        pos = event.position()

        if event.buttons() & Qt.MouseButton.LeftButton:
            self.view.Rotation(int(pos.x()), int(pos.y()))
            if self._press_pos is not None:
                dx = pos.x() - self._press_pos.x()
                dy = pos.y() - self._press_pos.y()
                self._drag_distance = (dx ** 2 + dy ** 2) ** 0.5
        elif event.buttons() & Qt.MouseButton.MiddleButton and self._last_mouse_pos is not None:
            dx = pos.x() - self._last_mouse_pos.x()
            dy = pos.y() - self._last_mouse_pos.y()
            self.view.Pan(int(dx), int(-dy))
        else:
            # No buttons held: this is a hover move. Ask OCCT what's
            # under the cursor right now, so it can highlight it
            # (visually, before any click commits a selection).
            self.context.MoveTo(int(pos.x()), int(pos.y()), self.view, True)

        self._last_mouse_pos = pos
        self.update()  # redraw after every mouse-driven view change

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._press_pos is not None:
            if self._drag_distance < self._click_drag_threshold_px:
                # Treated as a CLICK, not a drag/rotate -- attempt an
                # actual selection of whatever was last highlighted by
                # MoveTo(). Using the plain Select() form here (rather
                # than newer scheme-based variants like SelectDetected/
                # AIS_SelectionScheme_*) since OCCT's own forums show
                # those newer names aren't present in every installed
                # version -- Select() with no scheme argument is the
                # older, more broadly-available form.
                self.context.Select(True)
                self._report_selection()
            self._press_pos = None
            self._drag_distance = 0.0
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

            # For EDGES specifically: report whether it's straight or
            # circular, using the SAME build123d Edge wrapper (and the
            # same .geom_type check) that pose.py's PointRef/
            # DirectionRef resolution relies on -- so what prints here
            # tells you directly which pose.py "kind" this pick would
            # resolve to (e.g. a circular edge -> circle_center /
            # circle_axis; a straight edge -> edge_direction only, no
            # circle_center).
            geom_detail = ""
            if shape_type == TopAbs_EDGE:
                try:
                    from build123d import Edge as B123Edge, GeomType
                    wrapped_edge = B123Edge(shape)
                    geom_type = wrapped_edge.geom_type
                    # FIX: compare against the GeomType ENUM member,
                    # not the bare string "CIRCLE" -- confirmed via
                    # build123d's own docs that geom_type returns a
                    # real GeomType enum (e.g. "e.geom_type ==
                    # GeomType.CIRCLE" in their documented examples).
                    # This bug meant a genuinely CIRCLE-typed edge
                    # would NEVER have hit this branch before -- worth
                    # remembering when interpreting any PRIOR terminal
                    # output that showed "GeomType.BSPLINE"/etc. here,
                    # since a true circle would have been misreported
                    # too, not just the rod's edge specifically.
                    if geom_type == GeomType.CIRCLE:
                        center = wrapped_edge.arc_center
                        geom_detail = f"  [CIRCULAR edge, center={center}]"
                    else:
                        geom_detail = f"  [{geom_type} edge]"
                except Exception as e:
                    geom_detail = f"  [could not classify edge: {e}]"

            print(f"Selected #{count}: {type_name}{geom_detail}  |  {part_info}")
            self.context.NextSelected()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        factor = 1.1 if delta > 0 else (1 / 1.1)
        self.view.SetZoom(factor)
        self.update()

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
