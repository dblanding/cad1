"""
shell_dialog.py

THE SHELL DIALOG -- hollows out a solid, leaving open faces.

WORKFLOW:
  1. RMB -> Set Active Part on a solid in the tree.
  2. Click Shell... in the toolbar.
  3. Click the face(s) to be left OPEN (removed) -- typically the top
     face of a bottle, or the opening of a box.
  4. Enter the wall thickness (mm, positive = inward offset).
  5. Click Apply Shell.
  6. BRepOffsetAPI_MakeThickSolid runs; the active part is replaced.

FACE MATCHING (world-space):
  Same parallel-walk pattern as fillet_dialog.py: compare picked face
  centers (world space) against face centers of work_shape walked in
  parallel with world_shape = work_shape.Located(global_loc).

LOCATION STRIPPING:
  Same issue and fix as fillet_dialog.py. See DESIGN_BACKLOG item 24.

SIGNALS:
  shell_done(node, new_shape)  -- active part node + MakeThickSolid result
"""
import sys
import os

from PySide6.QtWidgets import (
    QDialog, QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QGroupBox,
    QListWidget, QListWidgetItem, QMessageBox,
)
from PySide6.QtCore import Qt, Signal

from OCP.TopAbs import TopAbs_FACE
from OCP.TopoDS import TopoDS
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeThickSolid
from OCP.TopTools import TopTools_ListOfShape
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.gp import gp_Pnt
from OCP.TopExp import TopExp_Explorer


class ShellDialog(QDialog):
    """
    Floating dock that manages: select open faces → enter thickness
    → apply shell → replace part geometry.
    """

    shell_done = Signal(object, object)  # (node, new_TopoDS_Shape)

    def __init__(self, parent=None, viewport=None):
        super().__init__(parent)
        self.setWindowTitle("Shell")
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.resize(300, 380)
        self.setMinimumWidth(260)

        self._viewport = viewport
        self._active_part = None
        self._faces = []        # picked TopoDS_Face objects (from AIS)

        self._build_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_active_part(self, node):
        """Called by main_app when the active part changes."""
        self._active_part = node
        name = node.label if node else "(none)"
        self._part_label.setText(f"Part: {name}")
        self._faces = []
        self._face_list.clear()
        self._apply_btn.setEnabled(False)

    def receive_face_pick(self, raw_shape, shape_type):
        """
        Called by main_app when geometry_picked fires with TopAbs_FACE
        while this dialog is visible.
        """
        if not self.isVisible():
            return
        if shape_type != TopAbs_FACE:
            return
        if self._active_part is None:
            return

        try:
            face = TopoDS.Face_s(raw_shape)
        except Exception as e:
            print(f"[ShellDialog] Could not cast to face: {e}")
            return

        self._faces.append(face)
        n = len(self._faces)
        item = QListWidgetItem(f"Face {n}")
        self._face_list.addItem(item)
        self._status.setText(f"{n} face(s) selected as open.")
        self._apply_btn.setEnabled(True)
        print(f"[ShellDialog] Face {n} added.")

    def is_active(self):
        return self.isVisible() and self._active_part is not None

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self._part_label = QLabel("Part: (none)")
        self._part_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._part_label)

        step1 = QGroupBox("Step 1 — Select open face(s)")
        s1_layout = QVBoxLayout(step1)
        self._status = QLabel("Click the face(s) to leave open.")
        self._status.setWordWrap(True)
        s1_layout.addWidget(self._status)
        self._face_list = QListWidget()
        self._face_list.setMaximumHeight(100)
        s1_layout.addWidget(self._face_list)
        clear_btn = QPushButton("Clear selection")
        clear_btn.clicked.connect(self._on_clear)
        s1_layout.addWidget(clear_btn)
        layout.addWidget(step1)

        step2 = QGroupBox("Step 2 — Wall thickness")
        s2_layout = QHBoxLayout(step2)
        s2_layout.addWidget(QLabel("Thickness:"))
        self._thickness_edit = QLineEdit("1.0")
        self._thickness_edit.setMaximumWidth(80)
        s2_layout.addWidget(self._thickness_edit)
        s2_layout.addWidget(QLabel("mm"))
        s2_layout.addStretch()
        layout.addWidget(step2)

        self._apply_btn = QPushButton("⬡  Apply Shell")
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._on_apply)
        layout.addWidget(self._apply_btn)

        layout.addStretch()

        cancel_btn = QPushButton("Close")
        cancel_btn.clicked.connect(self.hide)
        layout.addWidget(cancel_btn)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _on_clear(self):
        self._faces = []
        self._face_list.clear()
        self._apply_btn.setEnabled(False)
        self._status.setText("Click the face(s) to leave open.")

    def _on_apply(self):
        if not self._faces:
            QMessageBox.warning(self, "No faces", "Select at least one open face.")
            return
        if self._active_part is None:
            QMessageBox.warning(self, "No active part",
                                "Set an active part first via RMB in the tree.")
            return
        try:
            t = float(self._thickness_edit.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid thickness",
                                "Thickness must be a number.")
            return
        if t <= 0:
            QMessageBox.warning(self, "Invalid thickness",
                                "Thickness must be positive.")
            return

        try:
            new_shape = self._apply_shell(t)
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "Shell failed",
                                 f"Could not apply shell:\n{e}")
            return

        node = self._active_part
        self._faces = []
        self._face_list.clear()
        self._apply_btn.setEnabled(False)
        self._status.setText("Shell applied. Select faces or close.")

        self.shell_done.emit(node, new_shape)

    def _apply_shell(self, thickness):
        """
        Apply BRepOffsetAPI_MakeThickSolid to the active part.

        The picked faces come from AIS display topology, not from
        node.wrapped directly (same STEP round-trip issue as fillet).
        We match each picked face to a face in node.wrapped by comparing
        face center-of-mass position, then pass THOSE faces to MakeThickSolid.
        """
        from OCP.BRep import BRep_Tool
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps

        from OCP.TopLoc import TopLoc_Location as TLoc
        work_shape = self._active_part.wrapped
        original_loc = work_shape.Location()
        # Use world-space shape for COG comparison, local faces for MakeThickSolid.
        try:
            global_loc = self._active_part.global_location.wrapped
            world_shape = work_shape.Located(global_loc)
        except Exception:
            world_shape = work_shape

        shape_faces = []
        from OCP.TopExp import TopExp_Explorer as TPE
        local_exp = TopExp_Explorer(work_shape, TopAbs_FACE)
        world_exp = TPE(world_shape, TopAbs_FACE)
        while local_exp.More() and world_exp.More():
            face_local = TopoDS.Face_s(local_exp.Current())
            face_world = TopoDS.Face_s(world_exp.Current())
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(face_world, props)
            cog = props.CentreOfMass()
            shape_faces.append((face_local, cog))
            local_exp.Next()
            world_exp.Next()

        # Match each picked face by nearest center-of-mass
        faces_to_remove = TopTools_ListOfShape()
        matched = 0
        for picked_face in self._faces:
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(picked_face, props)
            picked_cog = props.CentreOfMass()

            best_face = None
            best_dist = 1.0  # mm tolerance
            for shape_face, shape_cog in shape_faces:
                dist = picked_cog.Distance(shape_cog)
                if dist < best_dist:
                    best_dist = dist
                    best_face = shape_face

            if best_face is not None:
                faces_to_remove.Append(best_face)
                matched += 1
            else:
                print(f"[ShellDialog] Warning: no matching face found near "
                      f"{picked_cog.X():.2f}, {picked_cog.Y():.2f}, "
                      f"{picked_cog.Z():.2f}")

        if matched == 0:
            raise RuntimeError("None of the selected faces could be matched "
                               "to faces in the active part's topology.")

        print(f"[ShellDialog] Matched {matched}/{len(self._faces)} faces.")

        # Negative thickness shells inward (same as kodacad)
        mk = BRepOffsetAPI_MakeThickSolid()
        mk.MakeThickSolidByJoin(work_shape, faces_to_remove, -thickness, 1.0e-3)
        mk.Build()
        if not mk.IsDone():
            raise RuntimeError(
                "BRepOffsetAPI_MakeThickSolid failed.\n"
                "Check that the thickness is not larger than the part geometry."
            )
        return mk.Shape()
