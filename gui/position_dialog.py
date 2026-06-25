"""
position_dialog.py

The Mate/Align positioning dialog, implementing the two techniques
from the HP/CoCreate workflow documented in DESIGN_BACKLOG.md §1:

    1. Mate/Align -- precision placement using face/edge/axis picks
    2. Dynamic Move -- translate-only rough positioning (simple version)

KEY DESIGN DECISIONS (from the PTC docs read this session):

    - The node being MOVED is always selected via the TREE (not
      viewport), because it may be an assembly container with no
      single AIS_Shape of its own -- confirmed by Doug: "the
      component being moved may be a part or an assembly."

    - Each Mate/Align step commits IMMEDIATELY -- "the parts or
      assemblies become constrained by each mate or align step"
      (PTC docs, direct quote). No batching until Apply.

    - Viewport picks within a step are for GEOMETRIC REFERENCE only
      (which face/edge/axis to align). The move is always applied
      to the tree-selected node, not to the specific sub-shape picked.

    - The dialog uses an EXPLICIT TWO-STEP PROMPT sequence per
      constraint: "Pick reference on moving part" then "Pick reference
      on fixed target" -- same pattern as CoCreate's dialog, which
      makes the two roles unambiguous.

STATE MACHINE (per constraint step):
    IDLE -> WAITING_PICK1 (user clicks "Mate" / "Align" / "Align Axis")
    WAITING_PICK1 -> WAITING_PICK2 (first pick received, stored)
    WAITING_PICK2 -> IDLE (second pick received, move computed + applied)
    Any state -> IDLE (Back button: undo one step)

INTEGRATION: this dialog connects to MainWindow via two signals:
    - MainWindow provides the currently tree-selected node (the moving
      part/assembly) whenever the dialog is open.
    - MainWindow's viewport emits geometry_picked(shape, shape_type)
      when in positioning mode -- a NEW signal added to
      SyncedViewportWidget, separate from the existing part_selected
      signal (which syncs the tree, not the position dialog).
"""

import sys
import os
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

from PySide6.QtWidgets import (
    QDialog,
    QDockWidget,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QButtonGroup,
    QRadioButton,
    QGroupBox,
    QFrame,
    QScrollArea,
)
from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QFont

from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from pose import (PointRef, DirectionRef, Plane, compute_move, move_location_only,  # noqa: E402
    find_intersection_line, compute_step1_move, compute_step2_move, compute_step3_move)
from build123d import Vector  # safe at module level -- pure geometry, no OCCT context dependency

# Heavier build123d shape wrappers (Face, Edge, GeomType) are imported
# lazily inside functions to avoid triggering OCP initialization before
# the OCCT viewer context is ready -- confirmed that module-level import
# of these broke face-level selection highlighting.


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class PositionState(Enum):
    IDLE = auto()          # no active positioning operation
    WAITING_PICK1 = auto() # waiting for pick on the MOVING part
    WAITING_PICK2 = auto() # waiting for pick on the FIXED target
    DYNAMIC_MOVE = auto()  # manipulator gizmo active, user is dragging


class ConstraintType(Enum):
    MATE = "Mate"
    ALIGN = "Align"
    ALIGN_AXIS = "Align Axis"
    DYNAMIC = "Dynamic Move"


@dataclass
class PickResult:
    """Resolved geometry from a single viewport pick."""
    shape_type: object   # TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX
    raw_shape: object    # the raw TopoDS_Shape
    point: Vector        # resolved 3D point (center, midpoint, vertex)
    direction: Optional[Vector]  # resolved direction, if applicable
    label: str           # human-readable description for status display


# ---------------------------------------------------------------------------
# Geometry resolution helper (same logic as assembly_viewer.py's
# _report_selection, but returning structured data rather than printing)
# ---------------------------------------------------------------------------

def resolve_pick(raw_shape, shape_type) -> Optional[PickResult]:
    """
    Resolve a raw picked TopoDS_Shape into a PickResult with a
    point and direction, using the same pose.py PointRef/DirectionRef
    pipeline already proven working on real STEP geometry.

    For CYLINDER faces specifically: automatically extracts the
    cylinder's axis (not the face normal, which is radial/perpendicular
    to the axis). This enables the natural CoCreate-style workflow of
    clicking directly on a cylindrical surface body for Align Axis,
    rather than having to hunt for the circular rim edge. The axis is
    extracted from the underlying Geom_CylindricalSurface via OCCT's
    BRep_Tool, which directly exposes the Axis().Direction() and a
    point on the axis.

    Returns None if the pick can't be meaningfully resolved.
    """
    try:
        if shape_type == TopAbs_FACE:
            from build123d import Face as B123Face, GeomType
            face = B123Face(raw_shape)

            # Special case: CYLINDER face -> extract the cylinder axis
            # directly, not the face normal. This is the fix for
            # "counterintuitive: have to pick the rim edge instead of
            # the cylindrical body for Align Axis." Every other CAD
            # tool does this automatically; we now do too.
            #
            # KEY GUARD: only attempt this for NON-PLANAR faces.
            # A flat face (GeomType.PLANE) also has circular boundary
            # edges if it's an end cap or surrounds a hole -- but
            # when the user picks a flat face, they mean the face
            # center/normal, NOT the axis of any nearby cylinder.
            # Confirmed real bug: without this guard, picking the flat
            # end face of the rod for a Mate step was misinterpreted
            # as "cylinder axis" because it found the circular rim
            # edge on that flat face's boundary.
            is_planar = face.geom_type == GeomType.PLANE

            circular_edge_result = None
            if not is_planar:
                try:
                    from pose import _resolve_circle
                    for edge in face.edges():
                        try:
                            fit = _resolve_circle(edge)
                            circular_edge_result = PickResult(
                                shape_type, raw_shape,
                                fit.center, fit.normal,
                                f"cylinder axis (via rim edge, "
                                f"center={_fmt(fit.center)}, "
                                f"axis={_fmt(fit.normal)})"
                            )
                            break
                        except ValueError:
                            continue
                except Exception as e:
                    print(f"[position_dialog] cylinder axis via edges failed: {e}")

            if circular_edge_result is not None:
                return circular_edge_result

            # Planar face, or non-planar face with no circular edges --
            # use center + normal as before.
            point = PointRef(kind="face_center", shape=face).resolve()
            direction = DirectionRef(kind="face_normal", shape=face).resolve()
            return PickResult(shape_type, raw_shape, point, direction,
                              f"face (center={_fmt(point)}, normal={_fmt(direction)})")

        elif shape_type == TopAbs_EDGE:
            from build123d import Edge as B123Edge
            edge = B123Edge(raw_shape)
            # Try circular first (circle_center / circle_axis)
            try:
                point = PointRef(kind="circle_center", shape=edge).resolve()
                direction = DirectionRef(kind="circle_axis", shape=edge).resolve()
                return PickResult(shape_type, raw_shape, point, direction,
                                  f"circular edge (center={_fmt(point)}, axis={_fmt(direction)})")
            except ValueError:
                pass
            # Fall back to straight edge
            point = PointRef(kind="edge_midpoint", shape=edge).resolve()
            direction = DirectionRef(kind="edge_direction", shape=edge).resolve()
            return PickResult(shape_type, raw_shape, point, direction,
                              f"straight edge (mid={_fmt(point)}, dir={_fmt(direction)})")

        elif shape_type == TopAbs_VERTEX:
            from build123d import Vertex as B123Vertex
            vert = B123Vertex(raw_shape)
            point = PointRef(kind="vertex", shape=vert).resolve()
            return PickResult(shape_type, raw_shape, point, None,
                              f"vertex at {_fmt(point)}")

    except Exception as e:
        print(f"[position_dialog] resolve_pick failed: {e}")
        return None

    return None


def _fmt(v: Vector) -> str:
    """Format a Vector to 2 decimal places for status display."""
    return f"({v.X:.2f}, {v.Y:.2f}, {v.Z:.2f})"


# ---------------------------------------------------------------------------
# The move computation for each constraint type
# (per the PTC docs and DESIGN_BACKLOG.md §1)
# ---------------------------------------------------------------------------



def _make_rotation_plane(pick1_point, from_z, to_z):
    """
    Build a to_plane for compute_move that aligns from_z to to_z WITHOUT
    adding any spin around the normal axis.

    ROOT CAUSE of the 90-degree spin bug: Plane(origin=p, z_dir=v)
    auto-computes an x_dir that depends on v's orientation relative to
    world axes. If two planes have different z_dirs, their auto-computed
    x_dirs differ, and compute_move includes a spin around the normal to
    align them -- unwanted rotation that spoils hole pattern alignment.
    Confirmed real bug: 90-degree spin on Mate with certain STEP files.

    THE FIX: explicitly set to_plane's x_dir by projecting from_plane's
    auto-computed x_dir onto the target normal's perpendicular plane.
    This preserves the original in-plane orientation and only rotates
    what's necessary to align the normals.
    """
    from_plane = Plane(origin=pick1_point, z_dir=from_z)
    from_x = from_plane.x_dir  # build123d's auto-computed x_dir for from_z

    # Project from_x onto the plane perpendicular to to_z.
    from_x_dot_to_z = from_x.dot(to_z)
    projected = from_x - to_z * from_x_dot_to_z

    # Degenerate case: from_x is nearly parallel to to_z (faces at 90
    # degrees to each other). Fall back to auto-computed x_dir.
    if projected.length < 1e-6:
        return Plane(origin=pick1_point, z_dir=to_z)

    return Plane(origin=pick1_point, x_dir=projected.normalized(), z_dir=to_z)


def compute_mate_move(pick1: PickResult, pick2: PickResult):
    """
    Mate: moving face becomes coplanar with target face, normals OPPOSED.
    Only consumes 3 DOF: 2 rotational (align normals) + 1 translational
    (close gap along normal). No in-plane movement, no spin.
    """
    if pick1.direction is None or pick2.direction is None:
        print("[position_dialog] Mate requires directed picks (faces or circular edges)")
        return None

    from build123d import Location, Vector

    target_z = -pick2.direction  # OPPOSED for Mate

    from_plane = Plane(origin=pick1.point, z_dir=pick1.direction)
    to_plane_rot = _make_rotation_plane(pick1.point, pick1.direction, target_z)
    rotation = compute_move(from_plane, to_plane_rot)

    gap = (pick2.point - pick1.point).dot(target_z)
    translation_vec = target_z * gap
    translation = Location((translation_vec.X, translation_vec.Y, translation_vec.Z))
    return translation * rotation


def compute_align_move(pick1: PickResult, pick2: PickResult):
    """
    Align: moving face becomes coplanar with target face, normals SAME
    direction. Only consumes 3 DOF: 2 rotational + 1 translational.
    No in-plane movement, no spin.
    """
    if pick1.direction is None or pick2.direction is None:
        print("[position_dialog] Align requires directed picks (faces or circular edges)")
        return None

    from build123d import Location, Vector

    target_z = pick2.direction  # SAME for Align

    from_plane = Plane(origin=pick1.point, z_dir=pick1.direction)
    to_plane_rot = _make_rotation_plane(pick1.point, pick1.direction, target_z)
    rotation = compute_move(from_plane, to_plane_rot)

    gap = (pick2.point - pick1.point).dot(target_z)
    translation_vec = target_z * gap
    translation = Location((translation_vec.X, translation_vec.Y, translation_vec.Z))
    return translation * rotation


def compute_align_axis_move(pick1: PickResult, pick2: PickResult):
    """
    Align Axis: two cylindrical/circular axes become coincident.
    Uses full compute_move (not the purity-of-motion decomposition)
    because Align Axis constrains both orientation AND position of the
    axis -- the axis center point needs to land on the target axis,
    which is a different constraint geometry than face coplanarity.
    """
    if pick1.direction is None or pick2.direction is None:
        return None
    from_plane = Plane(origin=pick1.point, z_dir=pick1.direction)
    to_plane = Plane(origin=pick2.point, z_dir=pick2.direction)
    return compute_move(from_plane, to_plane)


def compute_dynamic_move(pick1: PickResult, pick2: PickResult):
    """
    Dynamic Move: translate the moving part so pick1's point lands
    at pick2's point, with NO rotation change (move_location_only).
    pick1 is a reference point on the moving part; pick2 is the
    destination point in the assembly.
    """
    from build123d import Vector, Location
    from_plane = Plane(origin=pick1.point, z_dir=pick1.direction or Vector(0, 0, 1))
    to_plane = Plane(origin=pick2.point, z_dir=pick1.direction or Vector(0, 0, 1))
    return move_location_only(from_plane, to_plane)


# ---------------------------------------------------------------------------
# The dialog widget itself
# ---------------------------------------------------------------------------

class PositionDialog(QDialog):
    """
    Positioning dialog with three distinct sections:

    SECTION 1: Mate/Align (3-2-1)
      Step 1 -- Rotate to flush: rotate moving part about the
                intersection line of the two face planes.
      Step 2 -- In-plane constraint: translate within the flush
                plane (no rotation). Edge or axis pick.
      Step 3 -- Last DOF: translate or rotate to remove final DOF.
      The mated_normal from Step 1 is remembered so Steps 2+3
      correctly constrain moves to the plane.

    SECTION 2: Align Axis
      Single step: aligns cylinder/circle axis (4 DOF).

    SECTION 3: Dynamic
      AIS manipulator gizmo for rough positioning.

    Each section has its own buttons and status area.
    All three share the same Back (undo) and Done buttons.
    """

    request_redisplay = Signal(object)
    positioning_done  = Signal()

    def __init__(self, parent=None, viewport=None):
        super().__init__(parent)
        self.setWindowTitle("Position")
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.resize(300, 580)
        self.setMinimumWidth(260)
        self.setMinimumHeight(480)

        self._viewport      = viewport
        self._moving_node   = None
        self._state         = PositionState.IDLE
        self._pick1: Optional[PickResult] = None
        self._pick2: Optional[PickResult] = None
        self._move_history  = []           # list of applied Locations
        self._mated_normal: Optional[Vector] = None   # set by Step 1
        self._wall_normal: Optional[Vector] = None    # set by Step 2 (D2 in-plane)
        self._active_section = "mate_align"  # "mate_align" | "align_axis" | "dynamic"
        self._active_step    = None          # "step1" | "step2" | "step3" | "axis" | "dynamic"
        self._step1_mode     = "mate"        # "mate" | "align"
        # For Reverse:
        self._last_pick1: Optional[PickResult] = None
        self._last_pick2: Optional[PickResult] = None
        self._last_step:  Optional[str]        = None

        self._build_ui()

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(6, 6, 6, 6)

        # --- Moving part display ----------------------------------------
        moving_box = QGroupBox("Moving part / assembly")
        ml = QVBoxLayout(moving_box)
        self._moving_label = QLabel("(select a row in the tree)")
        self._moving_label.setWordWrap(True)
        f = QFont(); f.setBold(True)
        self._moving_label.setFont(f)
        ml.addWidget(self._moving_label)
        layout.addWidget(moving_box)

        # ================================================================
        # SECTION 1: Mate / Align  (3-2-1)
        # ================================================================
        sec1 = QGroupBox("1 — Mate / Align  (3-2-1)")
        sec1_layout = QVBoxLayout(sec1)

        self._step1_mate_btn = QPushButton("Step 1 — Mate")
        self._step1_mate_btn.setToolTip(
            "Pick a face on the MOVING part, then a face on the fixed part.\n"
            "Rotates about the intersection line until faces are flush.\n"
            "Use Reverse if the part flips the wrong way (Align).")
        self._step1_mate_btn.clicked.connect(
            lambda: self._start_step("step1", "mate"))
        sec1_layout.addWidget(self._step1_mate_btn)

        self._step2_edge_btn = QPushButton("Step 2 — Align Face")
        self._step2_edge_btn.setToolTip(
            "Pick a face on the MOVING part, then a face on the fixed part.\n"
            "Rotates and translates within the mated plane until coplanar.\n"
            "Flat face → wall/corner constraint.\n"
            "Cylindrical face → hole axis constraint.\n"
            "Detected automatically from the face type picked.")
        self._step2_edge_btn.clicked.connect(
            lambda: self._start_step("step2", "edge"))
        sec1_layout.addWidget(self._step2_edge_btn)

        self._step3_edge_btn = QPushButton("Step 3 — Complete")
        self._step3_edge_btn.setToolTip(
            "Pick a face on the MOVING part, then a face on the fixed part.\n"
            "Translates along the single remaining free direction only.\n"
            "Steps 1 and 2 constraints are preserved exactly.")
        self._step3_edge_btn.clicked.connect(
            lambda: self._start_step("step3", "edge"))
        sec1_layout.addWidget(self._step3_edge_btn)

        layout.addWidget(sec1)

        # Unused button refs (kept so _update_ui_state doesn't crash)
        self._step1_align_btn = None
        self._step2_axis_btn  = None
        self._step3_angle_btn = None
        # ================================================================
        # SECTION 2: Align Axis
        # ================================================================
        sec2 = QGroupBox("2 — Align Axis")
        sec2_layout = QVBoxLayout(sec2)
        self._axis_btn = QPushButton("Align Axis (4 DOF)")
        self._axis_btn.setToolTip(
            "Pick cylinder/circle on moving part, then on fixed part.\n"
            "Aligns both position and direction of the axis.")
        self._axis_btn.clicked.connect(
            lambda: self._start_step("axis", None))
        sec2_layout.addWidget(self._axis_btn)
        layout.addWidget(sec2)

        # ================================================================
        # SECTION 3: Dynamic
        # ================================================================
        sec3 = QGroupBox("3 — Dynamic (AIS Manipulator)")
        sec3_layout = QVBoxLayout(sec3)
        self._dynamic_btn = QPushButton("Attach Manipulator")
        self._dynamic_btn.clicked.connect(self._on_dynamic)
        sec3_layout.addWidget(self._dynamic_btn)
        layout.addWidget(sec3)

        # ================================================================
        # Status / picks (shared)
        # ================================================================
        status_box = QGroupBox("Status")
        sl = QVBoxLayout(status_box)
        self._status_label = QLabel("Select a row in the tree,\nthen use a step button above.")
        self._status_label.setWordWrap(True)
        self._status_label.setMinimumHeight(70)
        sl.addWidget(self._status_label)
        self._pick1_label = QLabel("Pick 1 (moving): —")
        self._pick1_label.setWordWrap(True)
        self._pick2_label = QLabel("Pick 2 (fixed):  —")
        self._pick2_label.setWordWrap(True)
        sl.addWidget(self._pick1_label)
        sl.addWidget(self._pick2_label)
        layout.addWidget(status_box)

        # ================================================================
        # Shared action buttons
        # ================================================================
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        btn_row = QHBoxLayout()
        self._reverse_btn = QPushButton("Reverse")
        self._reverse_btn.setEnabled(False)
        self._reverse_btn.setToolTip("Re-apply last step with direction flipped.")
        self._reverse_btn.clicked.connect(self._on_reverse)
        btn_row.addWidget(self._reverse_btn)

        self._back_btn = QPushButton("↩ Back")
        self._back_btn.setEnabled(False)
        self._back_btn.setToolTip("Undo last applied step.")
        self._back_btn.clicked.connect(self._on_back)
        btn_row.addWidget(self._back_btn)
        layout.addLayout(btn_row)

        self._done_btn = QPushButton("✓  Done")
        f2 = QFont(); f2.setBold(True)
        self._done_btn.setFont(f2)
        self._done_btn.clicked.connect(self._on_done)
        layout.addWidget(self._done_btn)

        layout.addStretch()
        self._update_ui_state()

    # -----------------------------------------------------------------------
    # External interface
    # -----------------------------------------------------------------------

    def set_moving_node(self, node):
        self._moving_node = node
        label = getattr(node, "label", "?") if node else "(none)"
        self._moving_label.setText(label)
        self._update_ui_state()

    def receive_pick(self, raw_shape, shape_type):
        if self._state == PositionState.IDLE:
            return
        result = resolve_pick(raw_shape, shape_type)
        if result is None:
            self._set_status("Could not resolve that pick -- try a face or edge.")
            return

        if self._state == PositionState.WAITING_PICK1:
            self._pick1 = result
            self._pick1_label.setText(f"Pick 1 (moving): {result.label}")
            self._state = PositionState.WAITING_PICK2
            self._set_status(
                f"✓ Moving: {result.label}\n\n"
                f"Now pick the corresponding feature on the FIXED part."
            )
        elif self._state == PositionState.WAITING_PICK2:
            self._pick2 = result
            self._pick2_label.setText(f"Pick 2 (fixed):  {result.label}")
            self._apply_step()
        self._update_ui_state()

    def is_in_positioning_mode(self) -> bool:
        return self._state not in (PositionState.IDLE, PositionState.DYNAMIC_MOVE)

    # -----------------------------------------------------------------------
    # Step dispatch
    # -----------------------------------------------------------------------

    def _start_step(self, step: str, mode):
        """Begin a two-pick step. step = "step1"|"step2"|"step3"|"axis"."""
        if self._moving_node is None:
            self._set_status("Select the part to move in the tree first.")
            return
        if step in ("step2", "step3") and self._mated_normal is None:
            self._set_status(
                "Complete Step 1 (Mate or Align) first.\n"
                "The mated plane normal is needed for Steps 2 and 3."
            )
            return

        self._active_step = step
        self._step1_mode  = mode  # "mate"|"align"|"edge"|"axis"|"angle"|None
        self._pick1 = None
        self._pick2 = None
        self._pick1_label.setText("Pick 1 (moving): —")
        self._pick2_label.setText("Pick 2 (fixed):  —")
        self._state = PositionState.WAITING_PICK1

        prompts = {
            ("step1", "mate"):  "MATE — Step 1\nPick a face on the MOVING part.",
            ("step1", "align"): "ALIGN — Step 1\nPick a face on the MOVING part.",
            ("step2", "edge"):  "STEP 2 (Face→Wall)\nPick a FACE on the MOVING part.",
            ("step2", "axis"):  "STEP 2 (Hole Axis)\nPick a hole/circle on the MOVING part.",
            ("step3", "edge"):  "STEP 3 (Edge→Corner)\nPick an EDGE on the MOVING part.",
            ("step3", "angle"): "STEP 3 (Index Angle)\nPick a reference EDGE on the MOVING part.",
            ("axis",  None):    "ALIGN AXIS\nPick a cylinder/circle on the MOVING part.",
        }
        self._set_status(prompts.get((step, mode), "Pick on the MOVING part."))
        self._update_ui_state()

    def _apply_step(self):
        """Both picks received -- compute and apply the move."""
        if self._moving_node is None or self._pick1 is None or self._pick2 is None:
            return

        step = self._active_step
        mode = self._step1_mode
        move = None

        if step == "step1":
            move = compute_step1_move(self._pick1, self._pick2,
                                      mate=(mode == "mate"))
            if move is not None:
                # Remember the mated normal for Steps 2 and 3.
                # After step1 the moving face normal should match -pick2.direction
                # (for mate) or +pick2.direction (for align).
                N2 = self._pick2.direction
                if N2 is not None:
                    self._mated_normal = -N2 if mode == "mate" else N2

        elif step == "step2":
            move = compute_step2_move(self._pick1, self._pick2,
                                      self._mated_normal)
            if move is not None:
                # Remember the wall normal (D2 projected onto mated plane)
                # so Step 3 can constrain motion to the single remaining DOF.
                N = self._mated_normal.normalized()
                D2 = self._pick2.direction
                if D2 is not None:
                    d2_in_plane = D2 - N * D2.dot(N)
                    if d2_in_plane.length > 1e-6:
                        self._wall_normal = d2_in_plane.normalized()

        elif step == "step3":
            move = compute_step3_move(self._pick1, self._pick2,
                                      self._mated_normal,
                                      self._wall_normal)

        elif step == "axis":
            move = compute_align_axis_move(self._pick1, self._pick2)

        if move is None:
            self._set_status("Could not compute move from these picks. Try again.")
            self._state = PositionState.IDLE
            return

        local_move = self._world_move_to_local(move)
        print(f"[dialog debug] step={step} mode={mode} move={move} local_move={local_move}")
        try:
            self._moving_node.move(local_move)
            self._move_history.append(local_move)
            self._last_pick1 = self._pick1
            self._last_pick2 = self._pick2
            self._last_step  = (step, mode)
            self.request_redisplay.emit(self._moving_node)
            n = len(self._move_history)
            self._set_status(
                f"✓ {step.upper()} ({mode}) applied (step {n}).\n\n"
                f"Click Reverse if direction was wrong,\n"
                f"or continue with next step."
            )
        except Exception as e:
            self._set_status(f"Move failed: {e}")
            print(f"[position_dialog] move failed: {e}")

        self._state = PositionState.IDLE
        self._pick1 = None
        self._pick2 = None

    # -----------------------------------------------------------------------
    # Shared action handlers
    # -----------------------------------------------------------------------

    def _on_dynamic(self):
        if self._moving_node is None:
            self._set_status("Select the part to move in the tree first.")
            return
        if self._viewport is not None:
            ok = self._viewport.attach_manipulator(self._moving_node)
            if ok:
                self._state = PositionState.DYNAMIC_MOVE
                self._set_status(
                    f"Dynamic Move active on '{self._moving_node.label}'.\n\n"
                    f"Drag arrows (translate) or rings (rotate).\n"
                    f"Click '✓ Done' when finished."
                )
            else:
                self._set_status("Could not attach manipulator.")
        self._update_ui_state()

    def _on_back(self):
        if not self._move_history or self._moving_node is None:
            return
        last_move = self._move_history.pop()
        try:
            self._moving_node.move(last_move.inverse())
            self.request_redisplay.emit(self._moving_node)
        except Exception as e:
            print(f"[position_dialog] Back failed: {e}")
        self._last_pick1 = None
        self._last_pick2 = None
        self._last_step  = None
        # If we undid step1, also clear the mated normal
        if len(self._move_history) == 0:
            self._mated_normal = None
        self._state = PositionState.IDLE
        self._pick1 = None
        self._pick2 = None
        self._pick1_label.setText("Pick 1 (moving): —")
        self._pick2_label.setText("Pick 2 (fixed):  —")
        self._set_status("Step undone.")
        self._update_ui_state()

    def _on_reverse(self):
        if not self._move_history or self._moving_node is None:
            return
        if self._last_pick1 is None or self._last_pick2 is None:
            return

        last_move = self._move_history.pop()
        try:
            self._moving_node.move(last_move.inverse())
            self.request_redisplay.emit(self._moving_node)
        except Exception as e:
            print(f"[position_dialog] Reverse (undo) failed: {e}")
            return

        step, mode = self._last_step if self._last_step else ("step1", "mate")
        p1 = self._last_pick1
        p2 = self._last_pick2

        if step == "step1":
            new_mode = "align" if mode == "mate" else "mate"
            print(f"[Reverse] step1: toggling {mode} → {new_mode}")
            print(f"[Reverse] p1.direction={p1.direction}  p2.direction={p2.direction}")
            move = compute_step1_move(p1, p2, mate=(new_mode == "mate"))
            # But if original N1 ≈ N2 (faces started aligned), mate gives 180° and
            # align gives 0° (identity). In that edge case, force 180° for mate by
            # checking if the move is near-identity and flipping pick2 normal.
            if move is not None and new_mode == "mate":
                from build123d import Vertex
                test_pt = Vertex(p1.point.X, p1.point.Y, p1.point.Z)
                moved_pt = test_pt.moved(move).center()
                if (moved_pt - p1.point).length < 1e-3:
                    # Identity move -- faces already opposed, try flipping p2
                    from dataclasses import replace
                    p2_flipped = replace(p2, direction=-p2.direction) if p2.direction else p2
                    move = compute_step1_move(p1, p2_flipped, mate=True)
                    p2 = p2_flipped
                    self._last_pick2 = p2
            # Update mated_normal
            if move is not None and p2.direction is not None:
                self._mated_normal = -p2.direction if new_mode == "mate" else p2.direction
            self._last_step = (step, new_mode)
        elif step == "step2":
            from dataclasses import replace
            p2 = replace(p2, direction=-p2.direction) if p2.direction else p2
            move = compute_step2_move(p1, p2, self._mated_normal)
            self._last_pick2 = p2
        elif step == "step3":
            from dataclasses import replace
            p2 = replace(p2, direction=-p2.direction) if p2.direction else p2
            move = compute_step3_move(p1, p2, self._mated_normal, self._wall_normal)
            self._last_pick2 = p2
        elif step == "axis":
            from dataclasses import replace
            p2 = replace(p2, direction=-p2.direction) if p2.direction else p2
            move = compute_align_axis_move(p1, p2)
            self._last_pick2 = p2
        else:
            move = None

        if move is None:
            self._set_status("Reverse: could not compute flipped move.")
            return

        local_move = self._world_move_to_local(move)
        print(f"[Reverse] move={move}  local_move={local_move}")
        try:
            self._moving_node.move(local_move)
            self._move_history.append(local_move)
            self.request_redisplay.emit(self._moving_node)
            self._set_status("✓ Reversed and re-applied.")
        except Exception as e:
            print(f"[position_dialog] Reverse (re-apply) failed: {e}")
        self._update_ui_state()

    def _on_done(self):
        if self._state == PositionState.DYNAMIC_MOVE and self._viewport is not None:
            try:
                world_move = self._viewport.get_manipulator_transform()
                if world_move is not None and self._moving_node is not None:
                    local_move = self._world_move_to_local(world_move)
                    self._moving_node.move(local_move)
                    self.request_redisplay.emit(self._moving_node)
            except Exception as e:
                print(f"[position_dialog] applying dynamic move failed: {e}")
            self._viewport.detach_manipulator()

        self._state = PositionState.IDLE
        self._mated_normal = None
        self._wall_normal = None
        self._move_history.clear()
        self.positioning_done.emit()

    # -----------------------------------------------------------------------
    # World → local frame conversion (unchanged from original)
    # -----------------------------------------------------------------------

    def _world_move_to_local(self, move):
        from build123d import Location
        parent = getattr(self._moving_node, 'parent', None)
        if parent is None:
            return move
        try:
            parent_world = parent.global_location
            if parent_world.position == (0, 0, 0):
                return move
            return parent_world.inverse() * move * parent_world
        except Exception as e:
            print(f"[position_dialog] world_move_to_local failed: {e}")
            return move

    # -----------------------------------------------------------------------
    # UI helpers
    # -----------------------------------------------------------------------

    def _set_status(self, text: str):
        self._status_label.setText(text)

    def _update_ui_state(self):
        has_node = self._moving_node is not None
        is_idle  = self._state == PositionState.IDLE
        has_normal = self._mated_normal is not None

        # Step buttons enabled only when idle and node selected
        for btn in [self._step1_mate_btn, self._axis_btn, self._dynamic_btn]:
            if btn is not None:
                btn.setEnabled(has_node and is_idle)

        # Step 2 & 3 need a mated normal from Step 1
        for btn in [self._step2_edge_btn, self._step3_edge_btn]:
            if btn is not None:
                btn.setEnabled(has_node and is_idle and has_normal)

        self._back_btn.setEnabled(len(self._move_history) > 0 and is_idle)
        self._reverse_btn.setEnabled(
            is_idle and self._last_pick1 is not None)
        self._done_btn.setEnabled(True)

