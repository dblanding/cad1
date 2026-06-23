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
)
from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QFont

from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from pose import PointRef, DirectionRef, Plane, compute_move, move_location_only  # noqa: E402
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



def compute_mate_move(pick1: PickResult, pick2: PickResult):
    """
    Mate: moving face becomes coplanar with target face, normals OPPOSED.

    Only consumes the 3 DOF relevant to this constraint:
    - 2 rotational (align normals)
    - 1 translational (close the gap along the normal)

    Leaves in-plane position and spin completely free.
    Faces do NOT need to have coincident centers -- only the planes
    need to be coplanar (confirmed: Doug's explicit requirement).
    """
    if pick1.direction is None or pick2.direction is None:
        print("[position_dialog] Mate requires directed picks (faces or circular edges)")
        return None

    from build123d import Location, Vector

    # Target direction for the moving face after Mate: OPPOSED to target
    target_z = -pick2.direction

    # Step 1: pure rotation -- same origin for both planes so
    # compute_move produces rotation only, zero translation.
    from_plane = Plane(origin=pick1.point, z_dir=pick1.direction)
    to_plane_rot = Plane(origin=pick1.point, z_dir=target_z)
    rotation = compute_move(from_plane, to_plane_rot)

    # Step 2: find where pick1.point ends up after the rotation.
    # The rotation maps from_plane's origin to to_plane_rot's origin
    # (both are pick1.point) -- so the point itself doesn't move, but
    # the ORIENTATION of the face at that point changes. We need to
    # know the gap between the (now-rotated) face plane and the target
    # plane. Since the face passes through pick1.point and the rotation
    # is about pick1.point, the face plane after rotation still passes
    # through pick1.point -- just with a different normal (target_z).
    # The gap from pick1.point to the target plane (through pick2.point
    # with normal target_z) is simply:
    gap = (pick2.point - pick1.point).dot(target_z)
    translation_vec = target_z * gap

    # Step 3: compose -- rotation first (about pick1.point), then
    # translate along the normal to close the gap.
    translation = Location((translation_vec.X,
                             translation_vec.Y,
                             translation_vec.Z))
    return translation * rotation


def compute_align_move(pick1: PickResult, pick2: PickResult):
    """
    Align: moving face becomes coplanar with target face, normals SAME
    direction (flush, not face-to-face).

    Same purity-of-motion logic as Mate -- only normal rotation +
    normal-direction gap translation. No in-plane movement.
    """
    if pick1.direction is None or pick2.direction is None:
        print("[position_dialog] Align requires directed picks (faces or circular edges)")
        return None

    from build123d import Location, Vector

    # Target direction: SAME as target face normal (Align = flush)
    target_z = pick2.direction

    from_plane = Plane(origin=pick1.point, z_dir=pick1.direction)
    to_plane_rot = Plane(origin=pick1.point, z_dir=target_z)
    rotation = compute_move(from_plane, to_plane_rot)

    # Same gap logic as Mate -- rotation is about pick1.point so
    # pick1.point stays in place; just measure gap to target plane.
    gap = (pick2.point - pick1.point).dot(target_z)
    translation_vec = target_z * gap
    translation = Location((translation_vec.X,
                             translation_vec.Y,
                             translation_vec.Z))
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

class PositionDialog(QDockWidget):
    """
    The Mate/Align positioning dialog. Opens as a dock alongside the
    main window. When open, puts the viewport in "positioning mode"
    where clicks feed into this dialog's state machine rather than
    the normal tree-sync behavior.

    Connects to MainWindow via:
        - MainWindow calls set_moving_node(node) when a tree row is
          selected while the dialog is open.
        - MainWindow calls receive_pick(raw_shape, shape_type) when
          the viewport has a pick in positioning mode.
        - This dialog emits request_redisplay(node) when a move has
          been applied, so MainWindow can refresh the viewport.
        - This dialog emits positioning_done() when the user closes
          or clicks Apply, so MainWindow can exit positioning mode.
    """

    request_redisplay = Signal(object)  # node that moved
    positioning_done = Signal()

    def __init__(self, parent=None, viewport=None):
        super().__init__("Position", parent)
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea |
            Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable |
            QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )

        self._viewport = viewport     # SyncedViewportWidget, for manipulator
        self._moving_node = None      # the tree-selected node being positioned
        self._state = PositionState.IDLE
        self._constraint_type = ConstraintType.MATE
        self._pick1: Optional[PickResult] = None
        self._pick2: Optional[PickResult] = None
        self._move_history = []       # list of applied Locations (for Back)
        # Last applied step -- kept so Reverse can undo + re-apply flipped.
        self._last_pick1: Optional[PickResult] = None
        self._last_pick2: Optional[PickResult] = None
        self._last_constraint_type: Optional[ConstraintType] = None

        self._build_ui()

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _build_ui(self):
        root = QWidget()
        self.setWidget(root)
        layout = QVBoxLayout(root)
        layout.setSpacing(8)

        # --- Moving part display ----------------------------------------
        moving_box = QGroupBox("Moving part / assembly")
        moving_layout = QVBoxLayout(moving_box)
        self._moving_label = QLabel("(select a row in the tree)")
        self._moving_label.setWordWrap(True)
        font = QFont()
        font.setBold(True)
        self._moving_label.setFont(font)
        moving_layout.addWidget(self._moving_label)
        layout.addWidget(moving_box)

        # --- Constraint type radio buttons ------------------------------
        method_box = QGroupBox("Method")
        method_layout = QVBoxLayout(method_box)
        self._method_group = QButtonGroup(self)

        for i, ct in enumerate([ConstraintType.MATE, ConstraintType.ALIGN,
                   ConstraintType.ALIGN_AXIS, ConstraintType.DYNAMIC]):
            rb = QRadioButton(ct.value)
            if ct == ConstraintType.MATE:
                rb.setChecked(True)
            self._method_group.addButton(rb, i)
            rb.toggled.connect(lambda checked, c=ct: self._on_method_changed(c) if checked else None)
            method_layout.addWidget(rb)

        layout.addWidget(method_box)

        # --- Status / prompt display ------------------------------------
        status_box = QGroupBox("Status")
        status_layout = QVBoxLayout(status_box)
        self._status_label = QLabel("Choose a method and click\n'Start Step' to begin.")
        self._status_label.setWordWrap(True)
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._status_label.setMinimumHeight(80)
        status_layout.addWidget(self._status_label)
        layout.addWidget(status_box)

        # --- Pick display -----------------------------------------------
        picks_box = QGroupBox("Picks")
        picks_layout = QVBoxLayout(picks_box)
        self._pick1_label = QLabel("Pick 1 (moving): —")
        self._pick1_label.setWordWrap(True)
        self._pick2_label = QLabel("Pick 2 (fixed):  —")
        self._pick2_label.setWordWrap(True)
        picks_layout.addWidget(self._pick1_label)
        picks_layout.addWidget(self._pick2_label)
        layout.addWidget(picks_box)

        # Separator
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        # --- Reverse / Re-apply -----------------------------------------
        # The "Reverse" button flips the normal direction of the LAST
        # applied step and re-applies it -- exactly the CoCreate
        # "Reverse" button described in the PTC docs. Useful when the
        # part moved the right distance but in the wrong direction
        # (e.g. Mate landed the bracket below the plate instead of on
        # top of it). Works by undoing the last step and re-applying
        # with the flipped direction, without requiring the user to
        # re-pick both faces.
        reverse_row = QHBoxLayout()
        self._reverse_btn = QPushButton("Reverse")
        self._reverse_btn.setEnabled(False)
        self._reverse_btn.setToolTip(
            "Flip the direction of the last applied step\n"
            "and re-apply it. Use when the part moved the\n"
            "right amount but in the wrong direction."
        )
        self._reverse_btn.clicked.connect(self._on_reverse)
        reverse_row.addWidget(self._reverse_btn)
        layout.addLayout(reverse_row)

        # --- Buttons ----------------------------------------------------
        btn_layout = QVBoxLayout()

        self._start_btn = QPushButton("Start Step")
        self._start_btn.setToolTip(
            "Begin a new constraint step.\n"
            "Then click a face/edge/axis on the MOVING part,\n"
            "then one on the FIXED target."
        )
        self._start_btn.clicked.connect(self._on_start)
        btn_layout.addWidget(self._start_btn)

        self._back_btn = QPushButton("Back (undo 1 step)")
        self._back_btn.setEnabled(False)
        self._back_btn.clicked.connect(self._on_back)
        btn_layout.addWidget(self._back_btn)

        self._done_btn = QPushButton("✓  Done")
        self._done_btn.clicked.connect(self._on_done)
        font_done = QFont()
        font_done.setBold(True)
        self._done_btn.setFont(font_done)
        btn_layout.addWidget(self._done_btn)

        layout.addLayout(btn_layout)
        layout.addStretch()

        self._update_ui_state()

    # -----------------------------------------------------------------------
    # External interface (called by MainWindow)
    # -----------------------------------------------------------------------

    def set_moving_node(self, node):
        """
        Called by MainWindow whenever a tree row is selected while
        this dialog is open. Sets the part/assembly to be moved.
        """
        self._moving_node = node
        label = getattr(node, "label", "?") if node else "(none)"
        self._moving_label.setText(label)
        self._update_ui_state()

    def receive_pick(self, raw_shape, shape_type):
        """
        Called by MainWindow when the viewport has a pick while in
        positioning mode. Routes the pick into the state machine.
        """
        if self._state == PositionState.IDLE:
            return

        result = resolve_pick(raw_shape, shape_type)
        if result is None:
            self._set_status("Could not resolve that pick -- try a face,\nedge, or vertex.")
            return

        if self._state == PositionState.WAITING_PICK1:
            self._pick1 = result
            self._pick1_label.setText(f"Pick 1 (moving): {result.label}")
            self._state = PositionState.WAITING_PICK2
            self._set_status(
                f"✓ Moving reference: {result.label}\n\n"
                f"Now click a face, edge, or axis on the\nFIXED target part."
            )

        elif self._state == PositionState.WAITING_PICK2:
            self._pick2 = result
            self._pick2_label.setText(f"Pick 2 (fixed):  {result.label}")
            self._apply_current_step()

        self._update_ui_state()

    # -----------------------------------------------------------------------
    # Button handlers
    # -----------------------------------------------------------------------

    def _on_method_changed(self, constraint_type: ConstraintType):
        self._constraint_type = constraint_type

    def _on_start(self):
        """Begin a new constraint step -- or attach manipulator for Dynamic Move."""
        if self._moving_node is None:
            self._set_status("Select the part or assembly to\nmove in the tree first.")
            return
        self._pick1 = None
        self._pick2 = None
        self._pick1_label.setText("Pick 1 (moving): —")
        self._pick2_label.setText("Pick 2 (fixed):  —")

        if self._constraint_type == ConstraintType.DYNAMIC:
            # Dynamic Move: attach the AIS_Manipulator gizmo and let
            # the user drag freely. No pick-pair state machine needed.
            if self._viewport is not None:
                ok = self._viewport.attach_manipulator(self._moving_node)
                if ok:
                    self._state = PositionState.DYNAMIC_MOVE
                    self._set_status(
                        f"Dynamic Move active on\n'{self._moving_node.label}'.\n\n"
                        f"Drag the arrows (translate) or\n"
                        f"rings (rotate) in the viewport.\n\n"
                        f"Click '✓ Done' when finished."
                    )
                else:
                    self._set_status("Could not attach manipulator.\nTry a different node.")
            else:
                self._set_status("No viewport connected.")
        else:
            self._state = PositionState.WAITING_PICK1
            method_name = self._constraint_type.value
            self._set_status(
                f"{method_name}: click a face, edge, or\naxis on the MOVING part\n"
                f"('{self._moving_node.label}' or any child)."
            )
        self._update_ui_state()

    def _on_back(self):
        """Undo the most recently applied move step."""
        if not self._move_history or self._moving_node is None:
            return

        # Undo: apply the inverse of the last move.
        last_move = self._move_history.pop()
        try:
            self._moving_node.move(last_move.inverse())
            self.request_redisplay.emit(self._moving_node)
        except Exception as e:
            print(f"[position_dialog] Back failed: {e}")

        # Clear last-step memory so Reverse is no longer available.
        self._last_pick1 = None
        self._last_pick2 = None
        self._last_constraint_type = None

        # Reset to idle, clear picks.
        self._state = PositionState.IDLE
        self._pick1 = None
        self._pick2 = None
        self._pick1_label.setText("Pick 1 (moving): —")
        self._pick2_label.setText("Pick 2 (fixed):  —")
        self._set_status("Step undone. Click 'Start Step' to\ncontinue positioning.")
        self._update_ui_state()

    def _on_reverse(self):
        """
        Undo the last applied step and re-apply it with the direction
        reversed -- i.e. flip pick2's direction (and pick1's for Mate,
        where both normals determine the orientation). Equivalent to
        CoCreate's 'Reverse' button: use when the part moved the right
        distance but ended up on the wrong side.
        """
        if not self._move_history or self._moving_node is None:
            return
        if self._last_pick1 is None or self._last_pick2 is None:
            return

        # Undo the last step first.
        last_move = self._move_history.pop()
        try:
            self._moving_node.move(last_move.inverse())
            self.request_redisplay.emit(self._moving_node)
        except Exception as e:
            print(f"[position_dialog] Reverse (undo phase) failed: {e}")
            return

        # Re-build picks with direction flipped on pick2 (and pick1
        # for Mate, since Mate's orientation depends on both normals).
        from dataclasses import replace
        ct = self._last_constraint_type

        if self._last_pick2.direction is not None:
            flipped_pick2 = replace(
                self._last_pick2,
                direction=-self._last_pick2.direction
            )
        else:
            flipped_pick2 = self._last_pick2

        # For Mate, also flip pick1 so the faces re-oppose correctly.
        if ct == ConstraintType.MATE and self._last_pick1.direction is not None:
            flipped_pick1 = replace(
                self._last_pick1,
                direction=-self._last_pick1.direction
            )
        else:
            flipped_pick1 = self._last_pick1

        # Re-compute and apply with the flipped directions.
        if ct == ConstraintType.MATE:
            move = compute_mate_move(flipped_pick1, flipped_pick2)
        elif ct == ConstraintType.ALIGN:
            move = compute_align_move(flipped_pick1, flipped_pick2)
        elif ct == ConstraintType.ALIGN_AXIS:
            move = compute_align_axis_move(flipped_pick1, flipped_pick2)
        else:
            move = compute_dynamic_move(flipped_pick1, flipped_pick2)

        if move is None:
            self._set_status("Reverse: could not compute flipped move.")
            return

        try:
            self._moving_node.move(move)
            self._move_history.append(move)
            # Update stored picks to the flipped versions so Reverse
            # can be clicked again if needed.
            self._last_pick1 = flipped_pick1
            self._last_pick2 = flipped_pick2
            self.request_redisplay.emit(self._moving_node)
            self._set_status(
                f"✓ {ct.value} reversed and re-applied.\n\n"
                f"Click 'Reverse' again if still wrong,\n"
                f"'Start Step' for the next constraint,\n"
                f"or '✓ Done' when finished."
            )
        except Exception as e:
            print(f"[position_dialog] Reverse (re-apply phase) failed: {e}")
        self._update_ui_state()

    def _on_done(self):
        """Close the dialog and signal MainWindow to exit positioning mode."""
        # If Dynamic Move was active, apply the manipulator's accumulated
        # transform to the node before detaching.
        if self._state == PositionState.DYNAMIC_MOVE and self._viewport is not None:
            try:
                world_move = self._viewport.get_manipulator_transform()
                if world_move is not None and self._moving_node is not None:
                    local_move = self._world_move_to_local(world_move)
                    self._moving_node.move(local_move)
                    self.request_redisplay.emit(self._moving_node)
            except Exception as e:
                print(f"[position_dialog] applying dynamic move transform failed: {e}")
            self._viewport.detach_manipulator()

        self._state = PositionState.IDLE
        self._move_history.clear()
        self.positioning_done.emit()

    # -----------------------------------------------------------------------
    # Move application
    # -----------------------------------------------------------------------

    def _world_move_to_local(self, move):
        """
        Convert a world-space Location (as computed from world-space
        pick coordinates) into the moving node's parent-local frame.

        WHY THIS IS NEEDED:
        Pick coordinates from OCCT are always in world space. The move
        computed from two world-space picks is therefore also in world
        space. But Shape.move() applies the delta in the node's PARENT
        frame -- which is the world frame only if the parent sits at
        the origin. For deeply nested nodes (e.g. l-bracket inside
        l-bracket-assembly inside as1), the parent frame is NOT the
        world frame, and applying a world-space delta directly produces
        the wrong result (confirmed: l-bracket moved to wrong position
        while l-bracket-assembly, whose parent IS at origin, moved
        correctly with the same code).

        Fix: transform the world-space move into the parent's local
        frame using the parent's global_location (world position).
        If P is the parent's world location:
            local_move = P.inverse() * world_move * P
        This converts the move from "expressed in world frame" to
        "expressed in parent's local frame."

        If the node has no parent (top-level) or parent has identity
        location, this is a no-op and the world-space move is used
        directly (correct behavior for the simple case).
        """
        from build123d import Location
        parent = getattr(self._moving_node, 'parent', None)
        if parent is None:
            return move
        try:
            parent_world = parent.global_location
            # Check if parent is at origin (identity) -- skip transform
            if parent_world.position == (0, 0, 0):
                return move
            # Transform: express world-space move in parent-local frame
            return parent_world.inverse() * move * parent_world
        except Exception as e:
            print(f"[position_dialog] world_move_to_local failed: {e}, "
                  f"using world-space move directly")
            return move

    def _apply_current_step(self):
        """
        Both picks are in -- compute and apply the move, then return
        to IDLE ready for the next step.
        """
        if self._moving_node is None or self._pick1 is None or self._pick2 is None:
            return

        ct = self._constraint_type
        if ct == ConstraintType.MATE:
            move = compute_mate_move(self._pick1, self._pick2)
        elif ct == ConstraintType.ALIGN:
            move = compute_align_move(self._pick1, self._pick2)
        elif ct == ConstraintType.ALIGN_AXIS:
            move = compute_align_axis_move(self._pick1, self._pick2)
        elif ct == ConstraintType.DYNAMIC:
            move = compute_dynamic_move(self._pick1, self._pick2)
        else:
            move = None

        if move is None:
            self._set_status("Could not compute move from these\npicks. Try again.")
            self._state = PositionState.IDLE
            return

        # Convert world-space move to parent-local frame before applying.
        local_move = self._world_move_to_local(move)

        try:
            self._moving_node.move(local_move)
            self._move_history.append(local_move)
            # Remember the picks so Reverse can undo + re-apply flipped.
            self._last_pick1 = self._pick1
            self._last_pick2 = self._pick2
            self._last_constraint_type = ct
            self.request_redisplay.emit(self._moving_node)
            n_steps = len(self._move_history)
            self._set_status(
                f"✓ {ct.value} applied (step {n_steps}).\n\n"
                f"Click 'Reverse' if the direction was wrong,\n"
                f"'Start Step' for the next constraint,\n"
                f"or '✓ Done' when finished."
            )
        except Exception as e:
            self._set_status(f"Move failed: {e}")
            print(f"[position_dialog] move failed: {e}")

        self._state = PositionState.IDLE
        self._pick1 = None
        self._pick2 = None

    # -----------------------------------------------------------------------
    # UI helpers
    # -----------------------------------------------------------------------

    def _set_status(self, text: str):
        self._status_label.setText(text)

    def _update_ui_state(self):
        has_moving = self._moving_node is not None
        is_idle = self._state == PositionState.IDLE
        is_dynamic = self._state == PositionState.DYNAMIC_MOVE
        self._start_btn.setEnabled(has_moving and is_idle)
        self._back_btn.setEnabled(len(self._move_history) > 0 and is_idle)
        self._reverse_btn.setEnabled(
            is_idle and
            self._last_pick1 is not None and
            self._last_pick2 is not None
        )
        # Disable method radios and back/reverse while manipulator is active.
        for btn in self._method_group.buttons():
            btn.setEnabled(is_idle)

    def is_in_positioning_mode(self) -> bool:
        """MainWindow calls this to know whether clicks go to the dialog."""
        return self._state not in (PositionState.IDLE, PositionState.DYNAMIC_MOVE)
