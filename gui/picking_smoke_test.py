"""
picking_smoke_test.py

Builds on the PROVEN-WORKING viewport_smoke_test.py (render + orbit +
zoom confirmed working) to test the next, separate unknown: can we
click on the box and find out WHICH face/edge/vertex got clicked?

This is the foundation pose.py's PointRef/DirectionRef resolution
will eventually need -- "user clicks a circular edge" only works if
we can first reliably get "user clicked, here is the TopoDS_Shape
that was under the cursor" out of OCCT.

WHY THIS IS ITS OWN SEPARATE SCRIPT rather than a modification of
viewport_smoke_test.py: OCCT's selection API has a genuinely messy,
version-fragmented history (confirmed via OCCT's own forums -- one
person's working code uses SetSelectionModeActive/SelectDetected,
another person on a different OCCT version reports neither method
exists in their library, only an older Select()). Expect this to need
more iteration than the render/redraw fixes did. Keeping this
separate means the known-good baseline stays untouched and easy to
fall back to if picking turns out messy.

WHAT THIS DOES:
    - Same box, same orbit/pan/zoom as the smoke test.
    - Activates face-level (TopAbs_FACE) selection on the displayed
      shape.
    - On left-click (without dragging -- see note below), attempts to
      select whatever's under the cursor and print which face index
      got hit.
    - Hover-highlights the face under the cursor as the mouse moves
      (this is what MoveTo() is for, independent of click-select).

NOTE ON LEFT-CLICK CONFLICT: viewport_smoke_test.py uses LMB-drag for
rotation. A real app needs to distinguish "LMB click, no drag" (pick)
from "LMB press-drag-release" (orbit) -- this script does that with a
simple movement-distance threshold: if the mouse moved less than a
few pixels between press and release, treat it as a click/pick;
otherwise treat it as a completed rotation and don't pick. This is a
common, simple heuristic, not a particularly elegant one -- worth
revisiting once we know picking itself works at all.
"""

import sys

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import Qt

from OCP.Aspect import Aspect_DisplayConnection
from OCP.OpenGl import OpenGl_GraphicDriver
from OCP.V3d import V3d_Viewer
from OCP.AIS import AIS_InteractiveContext, AIS_Shape
from OCP.Xw import Xw_Window
from OCP.Quantity import Quantity_Color, Quantity_TypeOfColor
from OCP.Graphic3d import Graphic3d_Camera
from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX
from OCP.TopoDS import TopoDS

from build123d import Box


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
        self._ais_shape = None  # set once display_shape() is called

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

    def display_shape(self, shape_wrapped):
        """Add a build123d/OCP shape to the view and fit the camera to it."""
        ais_shape = AIS_Shape(shape_wrapped)
        self.context.Display(ais_shape, True)
        self._ais_shape = ais_shape  # keep a reference for selection queries

        # Activate FACE-level selection specifically (not vertex/edge
        # yet -- one selection granularity at a time, same "isolate
        # one variable" approach as the rest of this project). The
        # selection mode integer for AIS_Shape corresponds to
        # TopAbs_ShapeEnum values; passing the TopAbs_FACE enum value
        # directly is the documented pattern (AIS_Shape::SelectionMode
        # in C++ just wraps the TopAbs enum value, faces = 4).
        self.context.Activate(ais_shape, TopAbs_FACE)

        # CRITICAL FIX: force the view to recompute its size from the
        # widget's CURRENT actual dimensions immediately before
        # fitting. The previous version relied on a fixed 100ms
        # QTimer delay before calling display_shape() at all, gambling
        # that the window/layout would have settled into its final
        # size by then -- on this run it apparently hadn't, so FitAll()
        # computed a fit against a stale/transient small viewport size,
        # producing the small-box-in-the-corner symptom. Explicitly
        # resizing right here removes the guesswork.
        self.view.MustBeResized()
        self.view.FitAll()
        self.view.ZFitAll()
        self.update()  # same reason as above: nothing redraws unless told to

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
        Print whatever is currently selected in the context. This is
        deliberately just printing to the terminal for now -- the
        real consumer of this (pose.py's PointRef/DirectionRef) comes
        later, once we know picking itself actually works.
        """
        self.context.InitSelected()
        if not self.context.MoreSelected():
            print("Click registered, but nothing was selected (missed the shape?)")
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
            print(f"Selected #{count}: {type_name}  (TopoDS shape: {shape})")
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
    app = QApplication(sys.argv)

    window = QWidget()
    window.setWindowTitle(
        "Picking smoke test -- click a face (no drag) to select it, "
        "watch the terminal"
    )
    window.resize(800, 600)

    from PySide6.QtWidgets import QVBoxLayout
    layout = QVBoxLayout(window)
    layout.setContentsMargins(0, 0, 0, 0)

    viewport = OcctViewportWidget(window)
    layout.addWidget(viewport)

    window.show()

    # Display one box, the same way step_assembly_poc.py builds
    # synthetic geometry -- but this time the goal is just "does it
    # render", not "does the tree logic work".
    box = Box(20, 20, 20)
    # Queue this to run after the CURRENT event loop iteration
    # finishes, rather than guessing a fixed delay (the previous
    # 100ms guess raced the window/layout settling into its final
    # size, which was the actual cause of the small-box-in-the-corner
    # bug). A 0ms singleShot still defers to the next event loop pass,
    # by which point show() and the initial resize have been
    # processed -- and display_shape() itself now also forces a fresh
    # MustBeResized() right before fitting, as a second safety net.
    from PySide6.QtCore import QTimer
    QTimer.singleShot(0, lambda: viewport.display_shape(box.wrapped))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
