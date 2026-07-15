"""
workplane_dialog.py

THE WORKPLANE / SKETCH / EXTRUDE DIALOG -- creates new solid parts.

WORKFLOW:
  1. User clicks a face in the 3D viewport to set the workplane's
     plane, then a second face to set the U direction (or uses
     "At Origin" / "By 3 Points" from the Workplane menu in
     main_app.py -- see DESIGN_BACKLOG item 33, Phase 2).
  2. A green translucent rectangle is displayed on that face.
  3. The sketch toolbar (sketch_toolbar.py) becomes active. It now
     lives on MainWindow as a persistent QToolBar (KodaCAD-style) --
     this dialog is just handed a reference to it (constructor param
     `sketch_toolbar`) rather than owning/embedding it.
  4. User draws construction geometry and profile edges on the workplane.
  5. Buttons at the bottom create a solid from the sketch:
       [+] Create Part     -- extrude in +wDir, adds a new Solid node
       [cut] Cut Into Active -- extrude in -wDir, boolean-cuts active part
       [fuse] Add To Active  -- extrude in +wDir, boolean-fuses (pull/boss)

WORKPLANE COORDINATE SYSTEM:
  U axis = x direction of the 2D sketch (horizontal by default)
  V axis = y direction of the 2D sketch (vertical by default)
  W axis = face normal (extrusion direction)
  Origin = face center

PART CREATION PATTERN:
  All three operations (Create/Cut/Add) use the same flow:
    1. wp.makeWire() converts the sketch profile to a TopoDS_Wire
    2. BRepBuilderAPI_MakeFace creates a planar face from the wire
    3. BRepPrimAPI_MakePrism extrudes to a TopoDS_Shape
    4. For Cut/Add: BRepAlgoAPI_Cut/Fuse operates on the active part
    5. The result is stored as node._wrapped and displayed

SIGNALS:
  part_created(node)      -- new Solid node added to the assembly tree
  part_cut(node, shape)   -- active part replaced with cut result
  part_fused(node, shape) -- active part replaced with fused result
  request_face_pick()     -- tell viewport to route face clicks here
  cancel_face_pick()      -- restore normal viewport click behavior
"""
import sys
import os

from PySide6.QtWidgets import (
    QDialog, QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
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


# CoCreate-inspired green for the workplane border display
_WP_COLOR = Quantity_Color(0.3, 0.75, 0.4, Quantity_TypeOfColor.Quantity_TOC_RGB)
# Magenta/pink for the U/V construction line crosshairs (CoCreate uses pink)
_WP_AXIS_COLOR = Quantity_Color(0.85, 0.2, 0.55, Quantity_TypeOfColor.Quantity_TOC_RGB)
# Solid steel-blue for new parts
_PART_COLOR = Quantity_Color(0.25, 0.45, 0.75, Quantity_TypeOfColor.Quantity_TOC_RGB)


class WorkplaneDialog(QDialog):
    """
    Floating dock that manages: pick a face → show workplane →
    enter dimensions → extrude new part → add to assembly.
    """

    # Emitted when a new part node has been created and should be added
    # to the assembly tree + displayed in the viewport.
    part_created = Signal(object)

    # Emitted when an existing part's geometry was replaced by a Cut op.
    # Carries (node, new_TopoDS_Shape).
    part_cut = Signal(object, object)

    def __init__(self, parent=None, viewport=None, sketch_toolbar=None):
        super().__init__(parent)
        self.setWindowTitle("Workplane / Sketch / Extrude")
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.resize(320, 420)
        self.setMinimumWidth(280)

        self._viewport = viewport       # OcctViewportWidget (or subclass)
        self._workplane = None          # WorkPlane instance, set after face pick
        self._wp_ais = None             # AIS_Shape for the workplane border
        self._picking_face = False      # True while waiting for a face click
        # On Face now takes 2 picks, matching KodaCAD's wpOnFace: the
        # first face sets the workplane's plane, the second sets the
        # U direction. Collected here, in pick order.
        self._face_stack = []
        self._active_part = None        # Part node to cut into (set by main_app)
        # PHASE 2 OF THE UI REVISION (DESIGN_BACKLOG item 33): the sketch
        # toolbar used to be built and embedded here (Step 2, a QGroupBox
        # inside this dialog). It now lives on MainWindow as a real,
        # persistent QToolBar (KodaCAD-style), and is just handed to us so
        # this dialog's remaining Extrude/Cut/Add controls (Step 3, now
        # renumbered Step 2 below) can enable/disable and query it.
        self._sketch_toolbar = sketch_toolbar

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ---- Step 1: pick 2 faces (plane, then U direction) ----------
        step1 = QGroupBox("Step 1 — Pick 2 faces (plane, then U dir.)")
        s1_layout = QVBoxLayout(step1)

        self._pick_status = QLabel("No face selected yet.")
        self._pick_status.setWordWrap(True)
        s1_layout.addWidget(self._pick_status)

        self._pick_btn = QPushButton("Click face in viewport…")
        self._pick_btn.setCheckable(True)
        self._pick_btn.setToolTip(
            "Click a face to set the workplane's plane, then click a\n"
            "second face to set the U direction.")
        self._pick_btn.clicked.connect(self._on_pick_btn_clicked)
        s1_layout.addWidget(self._pick_btn)

        layout.addWidget(step1)

        # ---- Step 2: sketch on the toolbar ----------------------------
        # (The sketch toolbar itself now lives on MainWindow -- see
        # DESIGN_BACKLOG item 33, Phase 2. This dialog just points at it.)
        hint2 = QLabel(
            "Step 2 — Sketch using the toolbar on the right edge\n"
            "of the main window."
        )
        hint2.setWordWrap(True)
        hint2.setStyleSheet("color: gray; font-style: italic;")
        layout.addWidget(hint2)

        # ---- Step 3: extrude depth + name + create -------------------
        step3 = QGroupBox("Step 3 — Extrude / Cut")
        s3_layout = QVBoxLayout(step3)

        row_d = QHBoxLayout()
        row_d.addWidget(QLabel("Depth:"))
        self._depth_edit = QLineEdit("10")
        self._depth_edit.setMaximumWidth(80)
        row_d.addWidget(self._depth_edit)
        row_d.addWidget(QLabel("mm"))
        row_d.addStretch()
        s3_layout.addLayout(row_d)

        row_n = QHBoxLayout()
        row_n.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit("new_part")
        row_n.addWidget(self._name_edit)
        s3_layout.addLayout(row_n)

        self._create_btn = QPushButton("✚  Create Part")
        self._create_btn.setEnabled(False)
        self._create_btn.clicked.connect(self._on_create_clicked)
        s3_layout.addWidget(self._create_btn)

        # Active part label -- shows which part will be cut into
        self._active_part_label = QLabel("Active part: (none)")
        self._active_part_label.setWordWrap(True)
        self._active_part_label.setStyleSheet("color: gray; font-style: italic;")
        s3_layout.addWidget(self._active_part_label)

        self._cut_btn = QPushButton("✂  Cut Into Active Part")
        self._cut_btn.setEnabled(False)
        self._cut_btn.clicked.connect(self._on_cut_clicked)
        s3_layout.addWidget(self._cut_btn)

        self._pull_btn = QPushButton("⊕  Add To Active Part")
        self._pull_btn.setEnabled(False)
        self._pull_btn.setToolTip("Extrude profile in +wDir and fuse onto active part (Pull/Boss)")
        self._pull_btn.clicked.connect(self._on_pull_clicked)
        s3_layout.addWidget(self._pull_btn)

        layout.addWidget(step3)

        layout.addStretch()

        # Cancel / close
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self._on_cancel)
        layout.addWidget(self._cancel_btn)

        self._set_sketch_enabled(False)

    def _set_sketch_enabled(self, enabled):
        """Enable/disable step 2+3 controls depending on whether we have a WP."""
        if self._sketch_toolbar is not None:
            self._sketch_toolbar.setEnabled(enabled)
        for w in [self._depth_edit, self._name_edit, self._create_btn]:
            w.setEnabled(enabled)
        # Cut and Pull buttons only enabled if there's also an active part
        has_part = self._active_part is not None
        self._cut_btn.setEnabled(enabled and has_part)
        self._pull_btn.setEnabled(enabled and has_part)

    def set_active_part(self, node):
        """Called by main_app when the active part changes."""
        self._active_part = node
        if node is not None:
            name = node.label or "<unnamed>"
            self._active_part_label.setText(f"Active part: {name}")
            self._active_part_label.setStyleSheet("color: orange; font-weight: bold;")
        else:
            self._active_part_label.setText("Active part: (none)")
            self._active_part_label.setStyleSheet("color: gray; font-style: italic;")
        # Update cut button state
        has_wp = self._workplane is not None
        self._cut_btn.setEnabled(has_wp and node is not None)
        self._pull_btn.setEnabled(has_wp and node is not None)

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
        self._face_stack = []
        self._pick_btn.setText("Cancel pick…")
        self._pick_status.setText(
            "Click a face to set the workplane's plane.")
        print("[WorkplaneDialog] Pick mode ON")
        if self._viewport is not None:
            from OCP.TopAbs import TopAbs_FACE
            ctx = self._viewport.context
            for ais in self._viewport._ais_shapes:
                ctx.Activate(ais, AIS_Shape.SelectionMode_s(TopAbs_FACE))
            ctx.UpdateCurrentViewer()

    def _cancel_pick_mode(self):
        self._picking_face = False
        self._face_stack = []
        self._pick_btn.setChecked(False)
        self._pick_btn.setText("Click face in viewport…")
        self._pick_status.setText("No face selected yet.")

    def receive_pick(self, raw_shape, shape_type):
        """
        Called by main_app when a viewport pick fires while this dialog
        is open and in pick mode. Only face picks are consumed here.

        On Face takes TWO face picks, matching KodaCAD's wpOnFace: the
        first face sets the workplane's plane (its normal becomes the
        W/extrusion direction), the second sets the U direction --
        rather than the plane pick alone with an auto-computed U
        direction. See WorkPlane.__init__'s existing face/faceU modes
        in src/workplane.py, which already supported this; only the
        picking UI here needed to collect a second face.

        Returns True if this pick was consumed (main_app should NOT try
        routing it anywhere else), False if it should fall through to
        the next handler (e.g. the sketch toolbar).
        """
        print(f"[WorkplaneDialog] receive_pick called: picking_face={self._picking_face}, "
              f"shape_type={shape_type}, faces_so_far={len(self._face_stack)}")
        if not self._picking_face:
            return False
        if shape_type != TopAbs_FACE:
            if self._workplane is not None:
                # SELF-HEAL: a workplane already exists, so this is
                # almost certainly a sketch pick (e.g. an intersection
                # marker) -- the user very likely didn't mean to
                # re-pick a face, they just have Step 1's button still
                # sitting there, live, the whole time they're
                # sketching. Without this, the wrong-type pick would
                # get silently absorbed here and _picking_face would
                # stay stuck True, swallowing every future pick too
                # (the same bug class already fixed once in
                # set_workplane() -- this closes the other way to hit
                # it: clicking "Click face in viewport..." again
                # mid-sketch, which set_workplane()'s guard can't catch
                # since it only runs when a NEW workplane is created).
                self._cancel_pick_mode()
                self._pick_status.setText("✓ Face selected.  Workplane created.")
                print("[WorkplaneDialog] receive_pick: non-face pick while a "
                      "workplane is already active -- self-healing (cancelling "
                      "pick mode) and letting it fall through to the sketch "
                      "toolbar instead of getting stuck.")
                return False
            self._pick_status.setText("That wasn't a face — try again.")
            return True

        self._face_stack.append(raw_shape)

        if len(self._face_stack) == 1:
            # First face -- defines the plane. Wait for a second face
            # to set the U direction (still picking; don't clear
            # _picking_face or the pick button yet).
            self._pick_status.setText(
                "✓ Plane face selected.  Now click a second face to "
                "set the U direction.")
            return True

        # Second face -- defines the U direction. Build the workplane.
        face_w = self._face_stack[0]
        face_u = self._face_stack[1]
        self._picking_face = False
        self._face_stack = []
        self._pick_btn.setChecked(False)
        self._pick_btn.setText("Re-pick face…")

        try:
            wp = WorkPlane(size=80, face=face_w, faceU=face_u)
        except Exception as e:
            QMessageBox.critical(self, "WorkPlane error",
                                 f"Could not create workplane from those "
                                 f"two faces:\n{e}")
            self._pick_status.setText("Error — try again (click the "
                                      "pick button to restart).")
            return True

        self._pick_status.setText(
            "✓ Both faces selected.  Workplane created.")
        self.set_workplane(wp)
        return True

    def set_workplane(self, wp):
        """
        Public entry point: activate this dialog for an already-built
        WorkPlane, regardless of how it was created (On Face via
        receive_pick() above, or At Origin / By 3 Points via the
        Workplane menu in main_app.py -- see DESIGN_BACKLOG item 33,
        Phase 2). Displays the workplane, enables Step 2's controls,
        and hands the workplane to the sketch toolbar.

        FIX: force-cancel any lingering face-pick mode first. If the
        user had previously started an "On Face" pick (this dialog's
        own button, or a prior menu action) and never completed or
        explicitly cancelled it, _picking_face stayed True --
        receive_pick() silently rejects non-face picks without ever
        clearing that flag, so EVERY subsequent pick (including vertex
        picks meant for the sketch toolbar's intersection markers) was
        being swallowed here instead of reaching the toolbar at all.
        Confirmed as the real cause of a report where a vertex pick
        showed up in the terminal (from the base viewer's independent
        selection print) but never reached the sketch toolbar's queue.
        """
        if self._picking_face:
            self._cancel_pick_mode()
        self._workplane = wp
        self._display_workplane()
        self._set_sketch_enabled(True)
        self._create_btn.setEnabled(True)
        if self._sketch_toolbar is not None:
            self._sketch_toolbar.set_workplane(self._workplane, self._viewport)

    # ------------------------------------------------------------------
    # Workplane display
    # ------------------------------------------------------------------

    def _display_workplane(self):
        """
        Show the workplane as:
          1. A semi-transparent green border face (CoCreate style).
          2. Two pink/magenta crosshair lines along the U and V axes
             through the workplane origin -- the construction lines
             CoCreate draws in pink to show the U/V coordinate system.
        """
        if self._viewport is None or self._workplane is None:
            return

        self._erase_workplane()

        border = self._workplane.border
        if border is None:
            return

        ctx = self._viewport.context

        # --- Border face ---
        ais_border = AIS_Shape(border)
        ais_border.SetColor(_WP_COLOR)
        ais_border.SetDisplayMode(AIS_DisplayMode.AIS_Shaded)
        ais_border.SetTransparency(0.5)
        ctx.Display(ais_border, False)
        ctx.Deactivate(ais_border)

        # --- U/V crosshair lines ---
        # Build two line segments along U and V axes, length = wp size
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
        from OCP.GC import GC_MakeSegment
        from OCP.gp import gp_Pnt
        from OCP.Prs3d import Prs3d_LineAspect
        from OCP.Aspect import Aspect_TOL_SOLID

        size = self._workplane.size
        wp = self._workplane

        def make_axis_line(p1_2d, p2_2d):
            """Make an AIS_Shape edge along the workplane U or V axis."""
            p1 = gp_Pnt(p1_2d[0], p1_2d[1], 0).Transformed(wp.Trsf)
            p2 = gp_Pnt(p2_2d[0], p2_2d[1], 0).Transformed(wp.Trsf)
            edge = BRepBuilderAPI_MakeEdge(
                GC_MakeSegment(p1, p2).Value()
            ).Edge()
            ais = AIS_Shape(edge)
            ais.SetColor(_WP_AXIS_COLOR)
            ais.SetWidth(1.5)
            return ais

        ais_u = make_axis_line((-size, 0), (size, 0))   # U axis (horizontal)
        ais_v = make_axis_line((0, -size), (0, size))   # V axis (vertical)

        for ais_line in (ais_u, ais_v):
            ctx.Display(ais_line, False)
            ctx.Deactivate(ais_line)

        ctx.UpdateCurrentViewer()
        self._viewport.update()

        # Store all three AIS objects so _erase_workplane can clean them up
        self._wp_ais = [ais_border, ais_u, ais_v]

    def _erase_workplane(self):
        """Remove the workplane display (border + crosshairs) from the viewport."""
        if self._wp_ais is not None and self._viewport is not None:
            ais_list = self._wp_ais if isinstance(self._wp_ais, list) \
                else [self._wp_ais]
            for ais in ais_list:
                self._viewport.context.Erase(ais, False)
            self._viewport.context.UpdateCurrentViewer()
            self._viewport.update()
            self._wp_ais = None

    # ------------------------------------------------------------------
    # Extrusion
    # ------------------------------------------------------------------

    def _on_create_clicked(self):
        if self._workplane is None:
            QMessageBox.warning(self, "No workplane", "Please pick a face first.")
            return

        try:
            d = float(self._depth_edit.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid input", "Depth must be a number.")
            return

        name = self._name_edit.text().strip() or "new_part"

        if d <= 0:
            QMessageBox.warning(self, "Invalid input", "Depth must be positive.")
            return

        try:
            node = self._extrude(d, name)
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "Extrusion failed",
                                 f"Could not create part:\n{e}")
            return

        # Clean up sketch and workplane display
        self._sketch_toolbar.deactivate()
        self._erase_workplane()
        self._workplane = None
        self._set_sketch_enabled(False)
        self._create_btn.setEnabled(False)
        self._pick_status.setText("Part created!  Pick another face to continue.")
        self._pick_btn.setText("Click face in viewport…")

        # Tell main_app about the new node
        self.part_created.emit(node)

    def _extrude(self, depth, name):
        """
        Extrude the current sketch profile along the workplane normal (+wDir).
        Uses whatever profile has been sketched via the toolbar.
        Returns a build123d Solid node ready to join the assembly tree.
        Raises RuntimeError if no profile exists or makeWire() fails.
        """
        from build123d import Solid, Shape
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
        from OCP.gp import gp_Vec

        wp = self._workplane

        if not wp.edgeList:
            raise RuntimeError(
                "No sketch profile found.\n\n"
                "Use the sketch toolbar (Step 2) to draw a rectangle, "
                "circle, or other profile before creating a part."
            )

        if not wp.makeWire():
            raise RuntimeError(
                "makeWire() failed -- the profile may not be closed.\n\n"
                "Tip: a rectangle (Rect tool) or circle (Circle tool) "
                "always forms a closed profile. Lines must form a "
                "closed loop."
            )

        face_bldr = BRepBuilderAPI_MakeFace(wp.wire)
        if not face_bldr.IsDone():
            raise RuntimeError("MakeFace failed.")

        # Extrude in +wDir (out of the face)
        extrude_vec = gp_Vec(wp.wDir) * depth
        prism_shape = BRepPrimAPI_MakePrism(face_bldr.Shape(), extrude_vec).Shape()

        # Round-trip through STEP to get a build123d Solid that is fully
        # XDE-registered (same as shapes from import_step), so export_step()
        # will include it correctly. Without this, export_step()'s _create_xde()
        # silently skips freshly constructed Solid nodes.
        import tempfile, os
        from build123d import Solid, export_step as b3d_export, import_step
        from OCP.BRepTools import BRepTools
        from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
        from OCP.IFSelect import IFSelect_RetDone

        # Write raw shape to a temp STEP file
        tmp = tempfile.NamedTemporaryFile(suffix='.step', delete=False)
        tmp.close()
        try:
            writer = STEPControl_Writer()
            writer.Transfer(prism_shape, STEPControl_AsIs)
            status = writer.Write(tmp.name)
            if status != IFSelect_RetDone:
                raise RuntimeError(f"Temp STEP write failed: {status}")
            # Re-import to get XDE-registered shape
            imported = import_step(tmp.name)
            # import_step returns a Solid with a spurious parent Compound
            # (same bug as documented in step_export_fix.py). The solid
            # may be the imported object itself or its first child.
            children = list(imported.children)
            if children:
                b3d_solid = children[0]
            else:
                b3d_solid = imported
            # Sever the spurious parent -- required so export_step()
            # doesn't skip it (same fix as step_export_fix.py applies
            # to the root assembly node).
            if b3d_solid.parent is not None:
                b3d_solid.parent = None
        finally:
            os.unlink(tmp.name)

        b3d_solid.label = name
        return b3d_solid

    def _on_cut_clicked(self):
        """Cut the sketched profile into the active part."""
        if self._workplane is None:
            QMessageBox.warning(self, "No workplane", "Please pick a face first.")
            return
        if self._active_part is None:
            QMessageBox.warning(self, "No active part",
                                "RMB on a part in the tree and choose "
                                "'⚙ Set Active Part' first.")
            return

        try:
            d = float(self._depth_edit.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid input", "Depth must be a number.")
            return

        if d <= 0:
            QMessageBox.warning(self, "Invalid input", "Depth must be positive.")
            return

        try:
            new_shape = self._cut(d)
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "Cut failed", f"Could not cut part:\n{e}")
            return

        # Clean up sketch and workplane display
        self._sketch_toolbar.deactivate()
        self._erase_workplane()
        self._workplane = None
        self._set_sketch_enabled(False)
        self._create_btn.setEnabled(False)
        self._cut_btn.setEnabled(False)
        self._pull_btn.setEnabled(False)
        self._pick_status.setText("Cut complete!  Pick another face to continue.")
        self._pick_btn.setText("Click face in viewport…")

        # Tell main_app to replace the part's geometry
        self.part_cut.emit(self._active_part, new_shape)

    def _cut(self, depth):
        """
        Extrude the sketch profile in the -wDir direction and subtract it
        from the active part's wrapped shape using BRepAlgoAPI_Cut.
        Returns the new TopoDS_Shape (not yet assigned to the node --
        main_app does that in _on_part_cut).
        """
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.gp import gp_Vec

        wp = self._workplane

        if not wp.edgeList:
            raise RuntimeError(
                "No sketch profile found.\n\n"
                "Use the sketch toolbar to draw the profile to cut."
            )

        if not wp.makeWire():
            raise RuntimeError(
                "makeWire() failed -- profile may not be closed."
            )

        face_bldr = BRepBuilderAPI_MakeFace(wp.wire)
        if not face_bldr.IsDone():
            raise RuntimeError("MakeFace failed.")

        # Cut tool goes in -wDir (into the material)
        cut_vec = gp_Vec(wp.wDir) * -depth
        tool = BRepPrimAPI_MakePrism(face_bldr.Shape(), cut_vec).Shape()

        # Get the current wrapped shape of the active part
        work_shape = self._active_part.wrapped

        result = BRepAlgoAPI_Cut(work_shape, tool)
        if not result.IsDone():
            raise RuntimeError("BRepAlgoAPI_Cut failed.")

        return result.Shape()

    def _on_pull_clicked(self):
        """Fuse the sketched profile onto the active part (Pull/Boss)."""
        if self._workplane is None:
            QMessageBox.warning(self, "No workplane", "Please pick a face first.")
            return
        if self._active_part is None:
            QMessageBox.warning(self, "No active part",
                                "RMB on a part in the tree and choose "
                                "'⚙ Set Active Part' first.")
            return

        try:
            d = float(self._depth_edit.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid input", "Depth must be a number.")
            return

        if d <= 0:
            QMessageBox.warning(self, "Invalid input", "Depth must be positive.")
            return

        try:
            new_shape = self._pull(d)
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "Pull failed", f"Could not add material:\n{e}")
            return

        # Clean up sketch and workplane display
        self._sketch_toolbar.deactivate()
        self._erase_workplane()
        self._workplane = None
        self._set_sketch_enabled(False)
        self._create_btn.setEnabled(False)
        self._cut_btn.setEnabled(False)
        self._pull_btn.setEnabled(False)
        self._pick_status.setText("Pull complete!  Pick another face to continue.")
        self._pick_btn.setText("Click face in viewport…")

        # Reuse part_cut signal -- same replace-in-place pattern
        self.part_cut.emit(self._active_part, new_shape)

    def _pull(self, depth):
        """
        Extrude the sketch profile in +wDir and fuse it onto the active
        part using BRepAlgoAPI_Fuse (add material / Pull / Boss).
        Returns the new TopoDS_Shape.
        """
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
        from OCP.gp import gp_Vec

        wp = self._workplane

        if not wp.edgeList:
            raise RuntimeError(
                "No sketch profile found.\n\n"
                "Use the sketch toolbar to draw the profile to add."
            )

        if not wp.makeWire():
            raise RuntimeError("makeWire() failed -- profile may not be closed.")

        face_bldr = BRepBuilderAPI_MakeFace(wp.wire)
        if not face_bldr.IsDone():
            raise RuntimeError("MakeFace failed.")

        # Pull tool goes in +wDir (out of the face, adding material)
        pull_vec = gp_Vec(wp.wDir) * depth
        tool = BRepPrimAPI_MakePrism(face_bldr.Shape(), pull_vec).Shape()

        work_shape = self._active_part.wrapped

        result = BRepAlgoAPI_Fuse(work_shape, tool)
        if not result.IsDone():
            raise RuntimeError("BRepAlgoAPI_Fuse failed.")

        return result.Shape()
        """
        Extrude the current sketch profile along the workplane normal.
        Uses whatever profile has been sketched via the toolbar.
        If no profile elements have been added, raises RuntimeError.
        Returns a build123d Solid node ready to join the assembly tree.
        """
        from build123d import Solid
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
        from OCP.gp import gp_Vec

        wp = self._workplane

        if not wp.edgeList:
            raise RuntimeError(
                "No sketch profile found.\n\n"
                "Use the sketch toolbar (Step 2) to draw a rectangle, "
                "circle, or other profile before creating a part."
            )

        if not wp.makeWire():
            raise RuntimeError(
                "makeWire() failed -- the profile may not be closed.\n\n"
                "Tip: a rectangle (Rect tool) or circle (Circle tool) "
                "always forms a closed profile. Lines must form a "
                "closed loop."
            )

        face_bldr = BRepBuilderAPI_MakeFace(wp.wire)
        if not face_bldr.IsDone():
            raise RuntimeError("MakeFace failed.")

        extrude_vec = gp_Vec(wp.wDir) * depth
        solid_shape = BRepPrimAPI_MakePrism(face_bldr.Shape(), extrude_vec).Shape()

        b3d_solid = Solid(solid_shape)
        b3d_solid.label = name
        return b3d_solid



    # ------------------------------------------------------------------
    # Cancel / close
    # ------------------------------------------------------------------

    def _on_cancel(self):
        self._cancel_pick_mode()
        self._sketch_toolbar.deactivate()
        self._erase_workplane()
        self._workplane = None
        self._set_sketch_enabled(False)
        self._create_btn.setEnabled(False)
        self.hide()

    def closeEvent(self, event):
        self._cancel_pick_mode()
        self._sketch_toolbar.deactivate()
        self._erase_workplane()
        super().closeEvent(event)

    def is_in_pick_mode(self):
        return self._picking_face
