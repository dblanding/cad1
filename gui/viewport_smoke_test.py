"""
viewport_smoke_test.py

The riskiest unknown in the GUI plan: does OCCT's own native 3D
viewer (the same AIS_InteractiveContext/V3d_View machinery OCCT uses
everywhere, including in CAD Assistant) actually open inside a
PySide6 widget on this machine, render a shape, and respond to mouse
orbit/pan/zoom?

This script answers EXACTLY that question and nothing else. No tree,
no menus, no show/hide, no file loading. One window, one box,
orbit/pan/zoom with the mouse, close the window.

WHY THIS IS THE RISKY LAYER (read before running):
Unlike the STEP round-trip work, failures here often do NOT show up
as clean Python exceptions. Expect possibilities like:
  - A blank/black window (OpenGL context created but nothing rendered)
  - A window that opens then immediately crashes/segfaults (no Python
    traceback at all -- this is a known failure mode when the native
    window handle isn't wired up correctly)
  - A window that renders the shape but never responds to mouse input
  - Platform-specific issues: this code uses Xw_Window, the Linux/X11
    binding. If you're running under Wayland specifically, X11 apps
    often still work via XWayland, but if this fails strangely, that
    environment variable distinction is one of the first things to
    check (see README notes -- TODO: confirms whether this matters
    once we know the failure mode, if any).

This script has NOT been executed -- this sandbox has no display, no
GPU, no windowing system at all, so there was no way to test it
before handing it to you. Treat the first run as a debugging session,
exactly like the first STEP round-trip attempt was.

Controls (once/if it's working):
    Left mouse drag    -> rotate/orbit
    Middle mouse drag   -> pan
    Scroll wheel        -> zoom
    Close the window to exit cleanly.
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

        self.display_connection = Aspect_DisplayConnection()
        self.graphic_driver = OpenGl_GraphicDriver(self.display_connection)

        self.viewer = V3d_Viewer(self.graphic_driver)
        self.viewer.SetDefaultLights()
        self.viewer.SetLightOn()

        self.view = self.viewer.CreateView()
        self.context = AIS_InteractiveContext(self.viewer)

        self._occt_window = None  # created lazily once we have a real winId()
        self._last_mouse_pos = None

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

    # --- Mouse handling: orbit / pan / zoom -----------------------
    # This is intentionally simple -- just enough to prove
    # interaction works at all. A real implementation will want
    # smoother/inertial controls later.

    def mousePressEvent(self, event):
        self._last_mouse_pos = event.position()
        if event.button() == Qt.MouseButton.LeftButton:
            self.view.StartRotation(int(event.position().x()), int(event.position().y()))

    def mouseMoveEvent(self, event):
        pos = event.position()
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.view.Rotation(int(pos.x()), int(pos.y()))
        elif event.buttons() & Qt.MouseButton.MiddleButton and self._last_mouse_pos is not None:
            dx = pos.x() - self._last_mouse_pos.x()
            dy = pos.y() - self._last_mouse_pos.y()
            self.view.Pan(int(dx), int(-dy))
        self._last_mouse_pos = pos
        self.update()  # redraw after every mouse-driven view change

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
    window.setWindowTitle("Viewport smoke test -- one box, mouse orbit/pan/zoom")
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
