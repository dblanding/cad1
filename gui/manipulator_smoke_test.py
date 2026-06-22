"""
manipulator_smoke_test.py

Minimal proof-of-concept for AIS_Manipulator in our existing viewport.
Loads as1-oc-214.stp, displays it as normal, then attaches an
AIS_Manipulator to the plate shape.

Key challenge: AIS_Manipulator needs to intercept LMB drag events
before the viewport's orbit handler gets them. We subclass
OcctViewportWidget and override the mouse event handlers to check
whether a manipulator part is under the cursor first.

Usage:
    uv run gui/manipulator_smoke_test.py step/as1-oc-214.stp
"""

import sys
import os
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton, QVBoxLayout, QWidget
from PySide6.QtCore import QTimer, Qt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from assembly_viewer import OcctViewportWidget


class ManipulatorViewport(OcctViewportWidget):
    """
    Subclass that intercepts mouse events when a manipulator is active.
    When LMB is pressed over a manipulator part, the manipulator takes
    over the drag entirely -- suppressing the normal orbit behavior.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._manipulator = None
        self._manip_dragging = False

    def set_manipulator(self, manip):
        self._manipulator = manip

    def mousePressEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton
                and self._manipulator is not None):
            x, y = int(event.position().x()), int(event.position().y())
            # Ask OCCT what's under the cursor -- this updates the
            # detected object, which AIS_Manipulator watches.
            self.context.MoveTo(x, y, self.view, True)
            # Check if the manipulator has an active mode (i.e. a
            # manipulator part is highlighted/detected).
            if self._manipulator.HasActiveMode():
                print(f"[manip] Drag started on manipulator part")
                try:
                    self._manipulator.StartTransform(x, y, self.view)
                    self._manip_dragging = True
                    return  # don't pass to orbit handler
                except Exception as e:
                    print(f"[manip] StartTransform failed: {e}")

        self._manip_dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._manip_dragging and self._manipulator is not None:
            x, y = int(event.position().x()), int(event.position().y())
            try:
                self._manipulator.Transform(x, y, self.view)
                self.context.UpdateCurrentViewer()
                self.update()
                return  # don't orbit/pan
            except Exception as e:
                print(f"[manip] Transform failed: {e}")
                self._manip_dragging = False

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton
                and self._manip_dragging
                and self._manipulator is not None):
            try:
                self._manipulator.StopTransform()
                print(f"[manip] Drag ended")
            except Exception as e:
                print(f"[manip] StopTransform failed: {e}")
            self._manip_dragging = False
            self.update()
            return  # don't trigger selection

        super().mouseReleaseEvent(event)


class ManipulatorTestWindow(QMainWindow):
    def __init__(self, step_path):
        super().__init__()
        self.step_path = step_path
        self.setWindowTitle("AIS_Manipulator smoke test")
        self.resize(1200, 800)
        self._manipulator = None
        self._target_ais = None
        self._assembly = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.viewport = ManipulatorViewport(self)
        layout.addWidget(self.viewport, stretch=1)

        btn_row = QWidget()
        btn_layout = QVBoxLayout(btn_row)
        btn_layout.setContentsMargins(4, 4, 4, 4)

        self._attach_btn = QPushButton("Attach manipulator to plate")
        self._attach_btn.setEnabled(False)
        self._attach_btn.clicked.connect(self._attach_manipulator)
        btn_layout.addWidget(self._attach_btn)

        self._detach_btn = QPushButton("Detach manipulator")
        self._detach_btn.setEnabled(False)
        self._detach_btn.clicked.connect(self._detach_manipulator)
        btn_layout.addWidget(self._detach_btn)

        layout.addWidget(btn_row)

    def load(self):
        print(f"Loading {self.step_path} ...")
        self._assembly = self.viewport.load_and_display_assembly(self.step_path)
        print("Loaded. Click 'Attach manipulator to plate' to test.")

        # Find the plate's AIS_Shape
        for ais_id, info in self.viewport._ais_shape_to_node.items():
            if info.get("label") == "plate":
                for ais in self.viewport._ais_shapes:
                    if id(ais) == ais_id:
                        self._target_ais = ais
                        print(f"Found plate AIS_Shape")
                        break
                break

        self._attach_btn.setEnabled(self._target_ais is not None)

    def _attach_manipulator(self):
        if self._target_ais is None:
            print("No target AIS_Shape found")
            return

        try:
            from OCP.AIS import AIS_Manipulator
        except ImportError as e:
            print(f"FAILED: AIS_Manipulator not in OCP bindings: {e}")
            return

        try:
            manip = AIS_Manipulator()

            # Disable scaling -- translate + rotate only.
            # Try different attribute name conventions.
            for attr in ["Scaling", "Scale", "AIS_MM_Scaling"]:
                try:
                    part_type = getattr(AIS_Manipulator, attr)
                    for axis in range(3):
                        manip.SetPart(axis, part_type, False)
                    print(f"Scaling disabled via AIS_Manipulator.{attr}")
                    break
                except AttributeError:
                    continue
            else:
                print("(Could not disable scaling -- attribute name unknown)")

            # Enable hover-to-activate
            manip.SetModeActivationOnDetection(True)

            # Attach to target shape
            manip.Attach(self._target_ais)

            # Display
            self.viewport.context.Display(manip, False)
            self.viewport.context.UpdateCurrentViewer()
            self.viewport.update()

            self._manipulator = manip
            self.viewport.set_manipulator(manip)

            print("Manipulator attached and displayed.")
            print("Hover over an arrow or ring -- it should highlight.")
            print("Then LMB-drag to move/rotate the plate.")

            self._attach_btn.setEnabled(False)
            self._detach_btn.setEnabled(True)

        except Exception as e:
            print(f"FAILED: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

    def _detach_manipulator(self):
        if self._manipulator is None:
            return
        try:
            self.viewport.context.Erase(self._manipulator, False)
            self._manipulator.Detach()
            self.viewport.context.UpdateCurrentViewer()
            self.viewport.update()
            self._manipulator = None
            self.viewport.set_manipulator(None)
            print("Manipulator detached")
            self._attach_btn.setEnabled(True)
            self._detach_btn.setEnabled(False)
        except Exception as e:
            print(f"Detach failed: {e}")


def main():
    if len(sys.argv) < 2:
        print("Usage: manipulator_smoke_test.py <path/to/assembly.step>")
        sys.exit(1)

    app = QApplication(sys.argv)
    window = ManipulatorTestWindow(sys.argv[1])
    window.show()
    QTimer.singleShot(0, window.load)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

