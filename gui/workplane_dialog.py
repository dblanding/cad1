"""
workplane_dialog.py

Floating dialog that drives the "Create Workplane → Sketch → Extrude"
workflow for making new parts.

WORKFLOW
--------
1.  User clicks "Create Part..." button in main_app.
2.  This dialog opens and enters FACE PICK MODE -- the viewport's
    existing geometry_picked signal routes face picks here.
3.  User clicks a face in the viewport.  A WorkPlane is constructed
    on that face and its border is displayed as a translucent blue
    rectangle in the viewport.
4.  The "Sketch" section becomes active.  For now this is
    dimension-driven only (width × height rectangle, no freehand
    sketching) -- the 90% use case per the design backlog.
5.  User enters: width, height, extrusion depth, and a part name.
6.  "Create Part" button calls _extrude():
        - rect() + makeWire() on the WorkPlane
        - BRepBuilderAPI_MakeFace + BRepPrimAPI_MakePrism → new solid
        - Wrapped in a build123d Solid, then added to the assembly tree
          and displayed in the viewport.
7.  Dialog returns to idle.

SIGNALS (emitted to main_app)
------------------------------
  part_created(node)   -- new assembly leaf node, ready to display
  request_pick_mode()  -- tell the viewport to activate face selection
  cancel_pick_mode()   -- tell the viewport to cancel / restore normal mode
"""

import sys
import os

from PySide6.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QGroupBox,
    QMessageBox, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal

from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_Transform
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.AIS import AIS_Shape, AIS_DisplayMode
from OCP.Quantity import Quantity_Color, Quantity_TypeOfColor
from OCP.Graphic3d import Graphic3d_MaterialAspect, Graphic3d_NameOfMaterial
from OCP.TopAbs import TopAbs_FACE

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from workplane import WorkPlane  # noqa: E402


# Semi-transparent blue for the workplane border display
_WP_COLOR = Quantity_Color(0.3, 0.5, 0.9, Quantity_TypeOfColor.Quantity_TOC_RGB)
# Solid steel-blue for new parts
_PART_COLOR = Quantity_Color(0.25, 0.45, 0.75, Quantity_TypeOfColor.Quantity_TOC_RGB)


class WorkplaneDialog(QDockWidget):
    """
    Floating dock that manages: pick a face → show workplane →
    enter dimensions → extrude new part → add to assembly.
    """

    # Emitted when a new part node has been created and should be added
    # to the assembly tree + displayed in the viewport.
    part_created = Signal(object)  # build123d Compound node

    def __init__(self, parent=None, viewport=None):
        super().__init__("Create Part", parent)
        self.setAllowedAreas(Qt.DockWidgetArea.NoDockWidgetArea)
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetFloatable |
            QDockWidget.DockWidgetFeature.DockWidgetClosable
        )

        self._viewport = viewport       # OcctViewportWidget (or subclass)
        self._workplane = None          # WorkPlane instance, set after face pick
        self._wp_ais = None             # AIS_Shape for the workplane border
        self._picking_face = False      # True while waiting for a face click

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        container = QWidget()
        self.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ---- Step 1: pick a face -------------------------------------
        step1 = QGroupBox("Step 1 — Pick a face")
        s1_layout = QVBoxLayout(step1)

        self._pick_status = QLabel("No face selected yet.")
        self._pick_status.setWordWrap(True)
        s1_layout.addWidget(self._pick_status)

        self._pick_btn = QPushButton("Click face in viewport…")
        self._pick_btn.setCheckable(True)
        self._pick_btn.clicked.connect(self._on_pick_btn_clicked)
        s1_layout.addWidget(self._pick_btn)

        layout.addWidget(step1)

        # ---- Step 2: sketch dimensions -------------------------------
        step2 = QGroupBox("Step 2 — Rectangle sketch")
        s2_layout = QVBoxLayout(step2)

        row_w = QHBoxLayout()
        row_w.addWidget(QLabel("Width:"))
        self._width_edit = QLineEdit("50")
        self._width_edit.setMaximumWidth(80)
        row_w.addWidget(self._width_edit)
        row_w.addWidget(QLabel("mm"))
        row_w.addStretch()
        s2_layout.addLayout(row_w)

        row_h = QHBoxLayout()
        row_h.addWidget(QLabel("Height:"))
        self._height_edit = QLineEdit("50")
        self._height_edit.setMaximumWidth(80)
        row_h.addWidget(self._height_edit)
        row_h.addWidget(QLabel("mm"))
        row_h.addStretch()
        s2_layout.addLayout(row_h)

        row_d = QHBoxLayout()
        row_d.addWidget(QLabel("Depth:"))
        self._depth_edit = QLineEdit("10")
        self._depth_edit.setMaximumWidth(80)
        row_d.addWidget(self._depth_edit)
        row_d.addWidget(QLabel("mm"))
        row_d.addStretch()
        s2_layout.addLayout(row_d)

        layout.addWidget(step2)

        # ---- Step 3: name + create -----------------------------------
        step3 = QGroupBox("Step 3 — Create part")
        s3_layout = QVBoxLayout(step3)

        row_n = QHBoxLayout()
        row_n.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit("new_part")
        row_n.addWidget(self._name_edit)
        s3_layout.addLayout(row_n)

        self._create_btn = QPushButton("✚  Create Part")
        self._create_btn.setEnabled(False)
        self._create_btn.clicked.connect(self._on_create_clicked)
        s3_layout.addWidget(self._create_btn)

        layout.addWidget(step3)

        layout.addStretch()

        # Cancel / close
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self._on_cancel)
        layout.addWidget(self._cancel_btn)

        self._set_sketch_enabled(False)

    def _set_sketch_enabled(self, enabled):
        """Enable/disable step 2+3 controls depending on whether we have a WP."""
        for w in [self._width_edit, self._height_edit, self._depth_edit,
                  self._name_edit, self._create_btn]:
            w.setEnabled(enabled)

    # ------------------------------------------------------------------
    # Pick mode
    # ------------------------------------------------------------------

    def enter_pick_mode(self):
        """Public entry point: activate pick mode (called by main_app on dialog open)."""
        self._pick_btn.setChecked(True)
        self._start_pick_mode()

    def _on_pick_btn_clicked(self, checked):
        if checked:
            self._start_pick_mode()
        else:
            self._cancel_pick_mode()

    def _start_pick_mode(self):
        """Activate face-selection mode in the viewport."""
        self._picking_face = True
        self._pick_btn.setText("Cancel pick…")
        self._pick_status.setText("Click a face in the viewport.")
        print("[WorkplaneDialog] Pick mode ON")
        if self._viewport is not None:
            from OCP.TopAbs import TopAbs_FACE
            ctx = self._viewport.context
            for ais in self._viewport._ais_shapes:
                ctx.Activate(ais, AIS_Shape.SelectionMode_s(TopAbs_FACE))
            ctx.UpdateCurrentViewer()

    def _cancel_pick_mode(self):
        self._picking_face = False
        self._pick_btn.setChecked(False)
        self._pick_btn.setText("Click face in viewport…")
        self._pick_status.setText("No face selected yet.")

    def receive_pick(self, raw_shape, shape_type):
        """
        Called by main_app when a viewport pick fires while this dialog
        is open and in pick mode.  Only face picks are consumed here.
        """
        print(f"[WorkplaneDialog] receive_pick called: picking_face={self._picking_face}, shape_type={shape_type}")
        if not self._picking_face:
            return
        if shape_type != TopAbs_FACE:
            self._pick_status.setText("That wasn't a face — try again.")
            return

        self._picking_face = False
        self._pick_btn.setChecked(False)
        self._pick_btn.setText("Re-pick face…")

        # Build the WorkPlane on the picked face
        try:
            self._workplane = WorkPlane(size=80, face=raw_shape)
        except Exception as e:
            QMessageBox.critical(self, "WorkPlane error",
                                 f"Could not create workplane on that face:\n{e}")
            self._pick_status.setText("Error — try another face.")
            return

        self._pick_status.setText("✓ Face selected.  Workplane created.")
        self._display_workplane()
        self._set_sketch_enabled(True)
        self._create_btn.setEnabled(True)

    # ------------------------------------------------------------------
    # Workplane display
    # ------------------------------------------------------------------

    def _display_workplane(self):
        """Show the workplane border as a semi-transparent blue face."""
        if self._viewport is None or self._workplane is None:
            return

        # Remove old workplane display if re-picking
        self._erase_workplane()

        border = self._workplane.border
        if border is None:
            return

        ais = AIS_Shape(border)
        ais.SetColor(_WP_COLOR)
        ais.SetDisplayMode(AIS_DisplayMode.AIS_Shaded)
        # Make it translucent (0=opaque, 1=fully transparent)
        ais.SetTransparency(0.6)

        ctx = self._viewport.context
        ctx.Display(ais, True)
        # Don't make the workplane itself selectable
        ctx.Deactivate(ais)
        ctx.UpdateCurrentViewer()
        self._viewport.update()

        self._wp_ais = ais

    def _erase_workplane(self):
        """Remove the displayed workplane border from the viewport."""
        if self._wp_ais is not None and self._viewport is not None:
            self._viewport.context.Erase(self._wp_ais, True)
            self._viewport.update()
            self._wp_ais = None

    # ------------------------------------------------------------------
    # Extrusion
    # ------------------------------------------------------------------

    def _on_create_clicked(self):
        if self._workplane is None:
            QMessageBox.warning(self, "No workplane", "Please pick a face first.")
            return

        # Parse inputs
        try:
            w = float(self._width_edit.text())
            h = float(self._height_edit.text())
            d = float(self._depth_edit.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid input",
                                "Width, height, and depth must be numbers.")
            return

        name = self._name_edit.text().strip() or "new_part"

        if w <= 0 or h <= 0 or d <= 0:
            QMessageBox.warning(self, "Invalid input",
                                "Width, height, and depth must be positive.")
            return

        try:
            node = self._extrude(w, h, d, name)
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "Extrusion failed",
                                 f"Could not create part:\n{e}")
            return

        # Clean up workplane display
        self._erase_workplane()
        self._workplane = None
        self._set_sketch_enabled(False)
        self._create_btn.setEnabled(False)
        self._pick_status.setText("Part created!  Pick another face to continue.")
        self._pick_btn.setText("Click face in viewport…")

        # Tell main_app about the new node
        self.part_created.emit(node)

    def _extrude(self, width, height, depth, name):
        """
        Add a rectangle to the workplane, make a wire, extrude it, and
        return a build123d Compound node ready to join the assembly tree.
        """
        from build123d import Compound, Solid

        wp = self._workplane

        # Clear any previous sketch geometry on this workplane
        wp.edgeList = []
        wp.wire = None

        # Centre the rectangle on the workplane origin
        hw, hh = width / 2.0, height / 2.0
        wp.rect((-hw, -hh), (hw, hh))

        if not wp.makeWire():
            raise RuntimeError("makeWire() failed -- profile may not be closed.")

        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
        from OCP.gp import gp_Vec

        face_bldr = BRepBuilderAPI_MakeFace(wp.wire)
        if not face_bldr.IsDone():
            raise RuntimeError("MakeFace failed.")

        extrude_vec = gp_Vec(wp.wDir) * depth
        solid_shape = BRepPrimAPI_MakePrism(face_bldr.Shape(), extrude_vec).Shape()

        # Wrap in a build123d Solid, then a Compound — same type the
        # assembly tree expects (plain anytree.Node is rejected).
        b3d_solid = Solid(solid_shape)
        new_node = Compound(label=name, children=[b3d_solid])

        return new_node

    # ------------------------------------------------------------------
    # Cancel / close
    # ------------------------------------------------------------------

    def _on_cancel(self):
        self._cancel_pick_mode()
        self._erase_workplane()
        self._workplane = None
        self._set_sketch_enabled(False)
        self._create_btn.setEnabled(False)
        self.hide()

    def closeEvent(self, event):
        self._cancel_pick_mode()
        self._erase_workplane()
        super().closeEvent(event)

    def is_in_pick_mode(self):
        return self._picking_face
