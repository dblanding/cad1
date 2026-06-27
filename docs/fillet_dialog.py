"""
fillet_dialog.py

Floating dialog that drives the "Select edges → Fillet" workflow.

WORKFLOW
--------
1.  User clicks "⌀ Fillet..." button in main_app (only enabled when an
    active part is set via RMB → Set Active Part).
2.  This dialog opens and activates edge-selection mode in the viewport.
3.  User clicks edges on the active part one by one. Each click adds
    that edge to the selection list shown in the dialog.
4.  User enters the fillet radius and clicks "Apply Fillet".
5.  BRepFilletAPI_MakeFillet runs on the active part's wrapped shape.
6.  The active part's geometry is replaced in-place (same pattern as
    Cut/Mill) and the viewport is updated.
7.  Dialog resets for the next fillet operation.

SIGNALS
-------
  fillet_done(node, new_shape)  -- active part node + new TopoDS_Shape
"""

import sys
import os

from PySide6.QtWidgets import (
    QDialog, QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QGroupBox,
    QListWidget, QListWidgetItem, QMessageBox,
)
from PySide6.QtCore import Qt, Signal

from OCP.TopAbs import TopAbs_EDGE
from OCP.TopoDS import TopoDS
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCP.AIS import AIS_Shape


class FilletDialog(QDialog):
    """
    Floating dock that manages: activate edge pick → accumulate edges
    → enter radius → apply fillet → replace part geometry.
    """

    # Emitted when fillet is complete. Carries (node, new_TopoDS_Shape).
    fillet_done = Signal(object, object)

    def __init__(self, parent=None, viewport=None):
        super().__init__(parent)
        self.setWindowTitle("Fillet / Blend")
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.resize(300, 420)
        self.setMinimumWidth(260)

        self._viewport = viewport
        self._active_part = None    # build123d node to fillet
        self._edges = []            # list of TopoDS_Edge objects

        self._build_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_active_part(self, node):
        """Called by main_app when the active part changes."""
        self._active_part = node
        name = node.label if node else "(none)"
        self._part_label.setText(f"Part: {name}")
        self._edges = []
        self._edge_list.clear()
        self._apply_btn.setEnabled(False)

    def receive_edge_pick(self, raw_shape, shape_type):
        """
        Called by main_app when geometry_picked fires with TopAbs_EDGE
        while this dialog is visible. Adds the edge to the selection list.
        main_app already verified the pick came from the active part's AIS,
        so we trust it without re-checking topology ownership.
        """
        if not self.isVisible():
            return
        if shape_type != TopAbs_EDGE:
            return
        if self._active_part is None:
            return

        try:
            edge = TopoDS.Edge_s(raw_shape)
        except Exception as e:
            print(f"[FilletDialog] Could not cast to edge: {e}")
            return

        self._edges.append(edge)
        n = len(self._edges)
        item = QListWidgetItem(f"Edge {n}")
        self._edge_list.addItem(item)
        self._status.setText(f"{n} edge(s) selected.")
        self._apply_btn.setEnabled(True)
        print(f"[FilletDialog] Edge {n} added.")

    def is_active(self):
        """Return True if this dialog is visible and accepting edge picks."""
        return self.isVisible() and self._active_part is not None

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Active part label
        self._part_label = QLabel("Part: (none)")
        self._part_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._part_label)

        # Edge selection
        step1 = QGroupBox("Step 1 — Select edges")
        s1_layout = QVBoxLayout(step1)
        self._status = QLabel("Click edges on the active part.")
        self._status.setWordWrap(True)
        s1_layout.addWidget(self._status)
        self._edge_list = QListWidget()
        self._edge_list.setMaximumHeight(120)
        s1_layout.addWidget(self._edge_list)

        clear_btn = QPushButton("Clear selection")
        clear_btn.clicked.connect(self._on_clear)
        s1_layout.addWidget(clear_btn)
        layout.addWidget(step1)

        # Radius
        step2 = QGroupBox("Step 2 — Radius")
        s2_layout = QHBoxLayout(step2)
        s2_layout.addWidget(QLabel("Radius:"))
        self._radius_edit = QLineEdit("3.0")
        self._radius_edit.setMaximumWidth(80)
        s2_layout.addWidget(self._radius_edit)
        s2_layout.addWidget(QLabel("mm"))
        s2_layout.addStretch()
        layout.addWidget(step2)

        # Apply
        self._apply_btn = QPushButton("⌀  Apply Fillet")
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
        self._edges = []
        self._edge_list.clear()
        self._apply_btn.setEnabled(False)
        self._status.setText("Click edges on the active part.")

    def _on_apply(self):
        if not self._edges:
            QMessageBox.warning(self, "No edges", "Select at least one edge.")
            return
        if self._active_part is None:
            QMessageBox.warning(self, "No active part",
                                "Set an active part first via RMB in the tree.")
            return

        try:
            r = float(self._radius_edit.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid radius", "Radius must be a number.")
            return
        if r <= 0:
            QMessageBox.warning(self, "Invalid radius", "Radius must be positive.")
            return

        try:
            new_shape = self._apply_fillet(r)
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "Fillet failed",
                                 f"Could not apply fillet:\n{e}")
            return

        node = self._active_part
        # Reset for next operation
        self._edges = []
        self._edge_list.clear()
        self._apply_btn.setEnabled(False)
        self._status.setText("Fillet applied. Select more edges or close.")

        self.fillet_done.emit(node, new_shape)

    def _apply_fillet(self, radius):
        """
        Apply BRepFilletAPI_MakeFillet to the active part's wrapped shape.

        The edges collected from the viewport are from the AIS display
        topology, which after a STEP round-trip are different C++ objects
        than those in node.wrapped (even though geometrically identical).
        BRepFilletAPI_MakeFillet requires edges that are actually IN the
        shape being filleted -- so we find the matching edge in node.wrapped
        by comparing midpoint coordinates, then pass THAT edge to MakeFillet.
        """
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopAbs import TopAbs_EDGE
        from OCP.BRep import BRep_Tool
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.GCPnts import GCPnts_AbscissaPoint
        from OCP.gp import gp_Pnt

        work_shape = self._active_part.wrapped

        # Pass work_shape directly to MakeFillet without stripping location.
        # The shape geometry in node._wrapped has the node's own rotation
        # baked into the coordinates. Stripping the location loses that
        # rotation. _display_leaf uses Located(global_loc) which REPLACES
        # not compounds, so mk.Shape() can be stored directly.
        mk = BRepFilletAPI_MakeFillet(work_shape)

        try:
            global_loc = self._active_part.global_location.wrapped
            world_shape = work_shape.Located(global_loc)
        except Exception:
            global_loc = None
            world_shape = work_shape

        shape_edges = []
        from OCP.TopExp import TopExp_Explorer as TPE
        local_exp = TopExp_Explorer(work_shape, TopAbs_EDGE)
        world_exp = TPE(world_shape, TopAbs_EDGE)
        while local_exp.More() and world_exp.More():
            edge_local = TopoDS.Edge_s(local_exp.Current())
            edge_world = TopoDS.Edge_s(world_exp.Current())
            try:
                curve = BRepAdaptor_Curve(edge_world)
                mid_param = (curve.FirstParameter() + curve.LastParameter()) / 2.0
                mid_pt = curve.Value(mid_param)
                shape_edges.append((edge_local, mid_pt))
            except Exception:
                pass
            local_exp.Next()
            world_exp.Next()

        matched = 0
        for picked_edge in self._edges:
            # Get midpoint of picked edge
            try:
                curve = BRepAdaptor_Curve(picked_edge)
                mid_param = (curve.FirstParameter() + curve.LastParameter()) / 2.0
                picked_mid = curve.Value(mid_param)
            except Exception:
                continue

            # Find closest edge in shape_edges
            best_edge = None
            best_dist = 1.0  # mm tolerance
            for shape_edge, shape_mid in shape_edges:
                dist = picked_mid.Distance(shape_mid)
                if dist < best_dist:
                    best_dist = dist
                    best_edge = shape_edge

            if best_edge is not None:
                mk.Add(radius, best_edge)
                matched += 1
            else:
                print(f"[FilletDialog] Warning: no matching edge found "
                      f"near {picked_mid.X():.2f}, {picked_mid.Y():.2f}, "
                      f"{picked_mid.Z():.2f}")

        if matched == 0:
            raise RuntimeError("None of the selected edges could be matched "
                               "to edges in the active part's topology.")

        print(f"[FilletDialog] Matched {matched}/{len(self._edges)} edges.")
        mk.Build()
        if not mk.IsDone():
            raise RuntimeError(
                "BRepFilletAPI_MakeFillet failed. "
                "Check that the radius is not larger than adjacent face widths."
            )
        # Restore the original location so the result has the same transform
        # as the input shape -- _display_leaf applies global_location correctly.
        return mk.Shape().Located(work_shape.Location())
