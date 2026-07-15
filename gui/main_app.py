"""
main_app.py

THE MAIN APPLICATION -- wires together all components of cad1.

ARCHITECTURE:
  MainWindow (QMainWindow)
    |- menuBar()             -- File / Workplane / Create 3D / Modify /
    |                            Position / Utility (KodaCAD-style;
    |                            PHASE 3 of the UI revision, see
    |                            DESIGN_BACKLOG item 33 -- WorkplaneDialog
    |                            retired. Workplane creation and Create
    |                            3D (Extrude/Revolve) are pure menu +
    |                            status-bar flows now, no dialog)
    |- statusBar()           -- shared QLineEdit + Current Operation
    |                            label + End Operation button + units
    |- Calculator            (rpn_calculator.py)      -- RPN calculator,
    |                            ported from KodaCAD; sends values to
    |                            whichever QLineEdit has focus, or
    |                            straight to an armed operation's queue
    |- SyncedViewportWidget  (subclass of assembly_viewer.SyncedViewportWidget)
    |    Adds: _node_id_to_ais_shape, _apply_shape_to_node(),
    |          active-part orange overlay, edge/vertex mode management
    |- AssemblyTreeWidget    (assembly_tree_widget.py)
    |    Includes a persistent "WP" section (PHASE 3) -- workplanes
    |    created via the Workplane menu are listed here and stay until
    |    deleted via RMB, each with its own show/hide checkbox. One can
    |    be marked Active (RMB) -- Create 3D / the sketch toolbar
    |    operate on whichever workplane is currently active.
    |- SketchToolBar         (sketch_toolbar.py)       -- follows the tree's
    |                            active workplane, not a dialog
    |- solid_ops.py          -- standalone extrude()/revolve()/cut_active_part()/
    |                            pull_active_part()/apply_fillet()/apply_shell()
    |                            functions, called directly by Create 3D /
    |                            Modify Active Part menu actions (PHASE 3B,
    |                            DESIGN_BACKLOG item 33 -- FilletDialog and
    |                            ShellDialog both retired, same pure menu +
    |                            status-bar treatment as Extrude/Revolve)
    +- PositionDialog        (position_dialog.py)    -- Mate/Align workflow.
                                 Deliberately staying a dialog -- no prior
                                 KodaCAD experience to model it on, being
                                 evaluated as-is for now.

TWO-WAY SYNC (tree and viewport):
  Click part in viewport  -> viewport emits part_selected(node)
                          -> tree highlights that row
  Click row in tree       -> tree emits node_selected(node)
                          -> viewport highlights that AIS_Shape

PART MODIFICATION PATTERN (_apply_shape_to_node):
  All operations that replace a part's geometry (fillet, shell, cut, pull)
  use this single method to:
    1. Capture parent_global location BEFORE replacing _wrapped (boolean
       ops strip location; global_location breaks if we don't save it first)
    2. Replace node._wrapped with the new TopoDS_Shape
    3. Rebuild ancestor Compounds for STEP export
    4. Remove old AIS, display new AIS with override_location=parent_global
    5. Restore STEP color on new AIS
    6. Re-apply black boundary edges
    7. Refresh active-part orange overlay
  See DESIGN_BACKLOG item 24 for why parent_global (not full global_location)
  is the correct override: the result geometry is already in the node's local
  frame, so only the parent chain transform is needed.

ACTIVE PART vs. ACTIVE ASSEMBLY:
  _active_assembly  -- Compound node that receives new parts/imports.
                       Shown bold with >> prefix. Set via RMB on tree.
  _active_part      -- Solid leaf node that fillet/shell/cut operate on.
                       Shown with orange wireframe overlay in viewport.
                       Set via RMB -> Set Active Part on tree.

SHARED INSTANCES (see DESIGN_BACKLOG item 26):
  STEP files may have multiple instances of the same shape (IsSame=True).
  Modifying one instance replaces its _wrapped with a new TopoDS_Shape,
  breaking the shared reference. The modified instance becomes an
  independent copy; other instances are unaffected.

USAGE:
  uv run gui/main_app.py step/as1-oc-214.stp
"""
import sys
import os

from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QSplitter,
    QLabel,
    QMainWindow,
    QLineEdit,
    QFrame,
    QToolButton,
)
from PySide6.QtGui import QAction
from PySide6.QtCore import Qt, Signal, QTimer

from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX
from OCP.Quantity import Quantity_Color, Quantity_TypeOfColor
from OCP.AIS import AIS_Shape

sys.path.insert(0, os.path.dirname(__file__))
from assembly_viewer import OcctViewportWidget  # noqa: E402
from assembly_tree_widget import AssemblyTreeWidget  # noqa: E402
from position_dialog import PositionDialog  # noqa: E402
from rpn_calculator import Calculator  # noqa: E402
from sketch_toolbar import SketchToolBar  # noqa: E402
import solid_ops  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from workplane import WorkPlane  # noqa: E402


class SyncedViewportWidget(OcctViewportWidget):
    """
    OcctViewportWidget, extended (not modified) with the two
    integration points the tree widget needs:

        - part_selected signal: fires with the resolved tree NODE
          (not just the raw AIS_Shape) whenever a click successfully
          resolves to a known part, so the tree can select/scroll to
          the matching row.
        - set_part_visible(node, visible): show/hide a specific part's
          AIS_Shape, for the tree's checkboxes to call.
        - highlight_node(node): visually highlight a part in the 3D
          view in response to a TREE selection (the reverse direction
          of part_selected), without requiring an actual click.
    """

    # Emits the tree-node dict (the SAME dict structure already stored
    # in self._ais_shape_to_node: {"label", "path", "node"}) whenever
    # a click resolves to a known part.
    part_selected = Signal(dict)

    # Emits raw geometry when clicked, for the position dialog to
    # consume in positioning mode. Carries the raw TopoDS_Shape and
    # its shape type -- position_dialog.py's resolve_pick() handles
    # the PointRef/DirectionRef resolution from there.
    geometry_picked = Signal(object, object)  # (raw_shape, shape_type)

    def __init__(self, parent=None):
        super().__init__(parent)
        # Reverse lookup: tree node (by id) -> its AIS_Shape, the
        # mirror of self._ais_shape_to_node (AIS_Shape id -> node
        # info). Built alongside the existing map in _display_leaf
        # via _register_node_shape() below, rather than duplicating
        # the tree-walk logic in load_and_display_assembly().
        self._node_id_to_ais_shape = {}

        # AIS_Manipulator state -- None when not in dynamic move mode.
        self._manipulator = None
        self._manip_dragging = False
        # All leaf AIS_Shapes under the node the manipulator is attached
        # to (target_ais is one of these). Needed so the WHOLE sub-
        # assembly moves live during drag, not just the one shape the
        # manipulator is actually Attach()-ed to.
        self._manip_leaf_shapes = []
        # id(ais) -> gp_Trsf captured at StartTransform time, for every
        # shape in _manip_leaf_shapes. Used each mouseMoveEvent to work
        # out the incremental delta and re-apply it to the siblings.
        self._manip_start_trsfs = {}

        # Make the SELECTED highlight (as opposed to the DYNAMIC/hover
        # highlight, which was already plenty visible) more visually
        # obvious -- the default was reported as "subtle." Confirmed
        # pattern from multiple OCCT forum threads: fetch (don't
        # replace) the existing Selected highlight style and modify
        # its color in place, per OCCT's own documented recommendation
        # ("better modifying existing style... to avoid unexpected
        # results due misconfiguration").
        try:
            from OCP.Prs3d import Prs3d_TypeOfHighlight
            selected_style = self.context.HighlightStyle(
                Prs3d_TypeOfHighlight.Prs3d_TypeOfHighlight_Selected
            )
            bright_highlight = Quantity_Color(
                1.0, 0.85, 0.0, Quantity_TypeOfColor.Quantity_TOC_RGB  # bright gold/yellow
            )
            selected_style.SetColor(bright_highlight)
        except Exception as e:
            print(f"(could not configure selection highlight style, "
                  f"using OCCT default instead: {e})")

    # -----------------------------------------------------------------------
    # AIS_Manipulator (Dynamic Move gizmo) support
    # -----------------------------------------------------------------------

    def attach_manipulator(self, node):
        """
        Attach an AIS_Manipulator gizmo to all leaf AIS_Shapes of the
        given node (or the node itself if it's a leaf). The gizmo
        provides 6-DOF interactive dragging: 3 arrows for translation,
        3 rings for rotation.
        """
        self.detach_manipulator()  # clean up any existing one first

        try:
            from OCP.AIS import AIS_Manipulator
        except ImportError:
            print("[manipulator] AIS_Manipulator not available in this OCP build")
            return False

        # Find a representative AIS_Shape to attach to -- use the first
        # leaf descendant (or the node itself if it's a leaf). The gizmo
        # will appear at that shape's center; since we're moving the
        # whole node, this is just a visual anchor point.
        target_ais = self._node_id_to_ais_shape.get(id(node))
        if target_ais is None:
            # node is a container -- find its first leaf descendant
            for child in node.descendants:
                target_ais = self._node_id_to_ais_shape.get(id(child))
                if target_ais is not None:
                    break

        if target_ais is None:
            print(f"[manipulator] No AIS_Shape found for node {node.label!r}")
            return False

        # Collect every leaf AIS_Shape under this node (including the
        # target itself) -- these all need to move together live during
        # the drag, not just target_ais which the gizmo is Attach()-ed to.
        leaf_shapes = []
        self_ais = self._node_id_to_ais_shape.get(id(node))
        if self_ais is not None:
            leaf_shapes.append(self_ais)
        else:
            for child in node.descendants:
                child_ais = self._node_id_to_ais_shape.get(id(child))
                if child_ais is not None:
                    leaf_shapes.append(child_ais)
        if not leaf_shapes:
            leaf_shapes = [target_ais]
        self._manip_leaf_shapes = leaf_shapes

        try:
            manip = AIS_Manipulator()
            manip.SetModeActivationOnDetection(True)

            # Disable scaling handles -- translate + rotate only.
            for attr_name in ["Scaling", "Scale", "AIS_MM_Scaling"]:
                try:
                    part_type = getattr(AIS_Manipulator, attr_name)
                    for axis in range(3):
                        manip.SetPart(axis, part_type, False)
                    break
                except AttributeError:
                    continue

            manip.Attach(target_ais)
            self.context.Display(manip, False)
            self.context.UpdateCurrentViewer()
            self.update()
            self._manipulator = manip
            print(f"[manipulator] Attached to {node.label!r}")
            return True

        except Exception as e:
            print(f"[manipulator] attach failed: {e}")
            return False

    def detach_manipulator(self):
        """Remove the manipulator gizmo from the viewport."""
        if self._manipulator is None:
            return
        try:
            self.context.Erase(self._manipulator, False)
            self._manipulator.Detach()
            self.context.UpdateCurrentViewer()
            self.update()
        except Exception as e:
            print(f"[manipulator] detach failed: {e}")
        self._manipulator = None
        self._manip_dragging = False
        self._manip_leaf_shapes = []
        self._manip_start_trsfs = {}

    def get_manipulator_transform(self):
        """
        Return the accumulated transform from the manipulator.
        AIS_Manipulator applies transforms directly to its attached
        AIS_Shape -- so the transform is already live in the shape's
        location. We return None here and instead rely on the fact
        that the shape (and therefore the build123d node's world
        geometry) has already been updated by the drag operations.
        The caller just needs to sync the build123d node's location
        to match what OCCT now shows.
        """
        # The manipulator applies transforms to the attached AIS_Shape
        # directly via its own internal mechanism during drag. After
        # Done is clicked, we need to read the shape's new location
        # from OCCT and apply it to the build123d node.
        if self._manipulator is None:
            return None
        try:
            # Get the attached object's current transformation
            from OCP.gp import gp_Trsf
            from build123d import Location
            obj = self._manipulator.Object()
            if obj is not None:
                trsf = obj.LocalTransformation()
                return Location(trsf)
        except Exception as e:
            print(f"[manipulator] get transform failed: {e}")
        return None

    def mousePressEvent(self, event):
        """Intercept LMB press to route to manipulator if cursor is over it."""
        try:
            if (event.button() == Qt.MouseButton.LeftButton
                    and self._manipulator is not None):
                x, y = int(event.position().x()), int(event.position().y())
                self.context.MoveTo(x, y, self.view, True)
                try:
                    is_manip = self._manipulator.HasActiveMode()
                except Exception:
                    is_manip = False

                if is_manip:
                    try:
                        self._manipulator.StartTransform(x, y, self.view)
                        self._manip_dragging = True
                        # Snapshot the CURRENT transform of every leaf
                        # shape (not just the one the gizmo is attached
                        # to) so mouseMoveEvent can compute deltas.
                        self._manip_start_trsfs = {
                            id(ais): ais.LocalTransformation()
                            for ais in self._manip_leaf_shapes
                        }
                        return
                    except Exception as e:
                        print(f"[manipulator] StartTransform failed: {e}")

            self._manip_dragging = False
            super().mousePressEvent(event)
        except Exception as e:
            import traceback
            print(f"[manipulator] mousePressEvent crashed: {e}")
            traceback.print_exc()

    def mouseMoveEvent(self, event):
        """Route mouse move to manipulator transform when dragging it."""
        if self._manip_dragging and self._manipulator is not None:
            x, y = int(event.position().x()), int(event.position().y())
            try:
                self._manipulator.Transform(x, y, self.view)

                # The gizmo only updated its ONE attached (target) shape.
                # Work out how much that shape moved since StartTransform,
                # and apply the SAME incremental delta to every other leaf
                # shape in the sub-assembly so the whole thing moves live.
                target_obj = self._manipulator.Object()
                if target_obj is not None and self._manip_start_trsfs:
                    start_target = self._manip_start_trsfs.get(id(target_obj))
                    if start_target is not None:
                        new_target = target_obj.LocalTransformation()
                        # delta (world-space) = new * inverse(start)
                        delta = new_target.Multiplied(start_target.Inverted())
                        for ais in self._manip_leaf_shapes:
                            if ais is target_obj:
                                continue  # gizmo already moved this one
                            start = self._manip_start_trsfs.get(id(ais))
                            if start is None:
                                continue
                            new_trsf = delta.Multiplied(start)
                            ais.SetLocalTransformation(new_trsf)
                            self.context.Redisplay(ais, False)

                self.context.UpdateCurrentViewer()
                self.update()
                return  # suppress orbit/pan
            except Exception as e:
                print(f"[manipulator] Transform failed: {e}")
                self._manip_dragging = False

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """Stop manipulator transform on LMB release."""
        if (event.button() == Qt.MouseButton.LeftButton
                and self._manip_dragging
                and self._manipulator is not None):
            try:
                self._manipulator.StopTransform()
                # CRITICAL: deactivate the current mode so HasActiveMode()
                # returns False when cursor moves away -- without this,
                # HasActiveMode() stays True and all subsequent LMB clicks
                # are intercepted as manipulator drags, locking out orbit.
                self._manipulator.DeactivateCurrentMode()
            except Exception as e:
                print(f"[manipulator] StopTransform/Deactivate failed: {e}")
            self._manip_dragging = False
            self.update()
            return  # suppress selection

        super().mouseReleaseEvent(event)

    def _display_leaf(self, node, path, palette_index, override_location=None):
        # Call the PROVEN base implementation first (unchanged --
        # creates the AIS_Shape, sets color/display mode, activates
        # selection, displays it, and populates
        # self._ais_shape_to_node). Then just ALSO record the reverse
        # mapping we need for set_part_visible()/highlight_node().
        super()._display_leaf(node, path, palette_index,
                              override_location=override_location)
        # The base method appends to self._ais_shapes -- the shape we
        # just created is the last one added.
        ais_shape = self._ais_shapes[-1]
        self._node_id_to_ais_shape[id(node)] = ais_shape

        # EXTEND selection beyond the base class's face-only mode:
        # also activate EDGE-level picking on the same shape. Needed
        # for the upcoming Mate/Align/Align-Axis pose work -- edge
        # direction and circular-edge axis/center resolution
        # (pose.py's DirectionRef "edge_direction"/"circle_axis" and
        # PointRef "circle_center") both require picking an EDGE, not
        # just a face. Confirmed via OCCT's own docs: "More than one
        # Selection Mode can be activated at the same time" -- this is
        # additive, not a replacement of the base class's face
        # activation.
        #
        # FIX (confirmed root cause of "clicking an edge returns
        # TopAbs_SOLID instead"): AIS_Shape selection-mode integers
        # are NOT guaranteed to equal TopAbs_ShapeEnum's own values --
        # "The Selection Mode for a specific shape type is returned by
        # method AIS_Shape::SelectionMode()" per OCCT's own docs. The
        # earlier version passed TopAbs_EDGE directly, which only
        # happens to be correct for FACE by numeric coincidence -- for
        # EDGE it was activating some OTHER, unintended selection
        # mode (matching whatever AIS_Shape mode shares EDGE's raw
        # enum value), which explains both "edges never highlighted"
        # and "got a SOLID back instead."
        #
        # Also: this OCP build exposes SelectionMode as the STATIC
        # form SelectionMode_s (called on the CLASS AIS_Shape, not the
        # instance) -- confirmed from the runtime AttributeError's own
        # suggested correction, same "_s" convention seen once before
        # during the STEP export investigation.
        # Only FACE selection is activated at load time.
        # Activating EDGE/VERTEX on all parts simultaneously crashes
        # OCCT's selection index on the first Select() call.
        # Edge/vertex picking still works via context.Select() without
        # explicit activation -- activation only controls hover highlight.

        # EDGE and VERTEX modes are NOT activated globally.
        # They are activated only on the active part when it is set
        # via RMB -> Set Active Part. This avoids crashing OCCT's
        # MoveTo() when traversing selection structures for 18+ parts.


    def _apply_black_edges(self, ais=None):
        """
        Reapply crisp black face boundary edges to an AIS_Shape.
        If ais is None, applies to ALL currently displayed AIS shapes.
        Call after Redisplay() or display_subtree() since those can
        reset the drawer attributes.
        """
        from OCP.Quantity import Quantity_NOC_BLACK
        from OCP.Aspect import Aspect_TOL_SOLID
        targets = [ais] if ais is not None else self._ais_shapes
        for a in targets:
            try:
                drawer = a.Attributes()
                drawer.SetFaceBoundaryDraw(True)
                drawer.FaceBoundaryAspect().SetColor(
                    Quantity_Color(Quantity_NOC_BLACK))
                drawer.FaceBoundaryAspect().SetWidth(1.0)
                drawer.FaceBoundaryAspect().SetTypeOfLine(Aspect_TOL_SOLID)
                self.context.Redisplay(a, False)
            except Exception:
                pass

    def _set_selection_mode(self, shape_type, active: bool):
        """
        Activate or deactivate a selection mode (EDGE, VERTEX, etc.)
        on all currently displayed AIS shapes.
        Called on-demand when dialogs that need edge/vertex picking
        open or close, rather than keeping all modes active all the time
        (which crashes OCCT's selection index on initial load).
        """
        mode = AIS_Shape.SelectionMode_s(shape_type)
        for ais in self._ais_shapes:
            try:
                if active:
                    self.context.Activate(ais, mode)
                else:
                    self.context.Deactivate(ais, mode)
            except Exception:
                pass

    def _report_selection(self):
        """
        Same as the base implementation (prints to terminal), PLUS
        emits part_selected so the tree widget can sync to it. We
        call the base method first so the existing, proven terminal
        output behavior is unchanged, then additionally emit the
        signal using the same lookup logic.
        """
        self.context.InitSelected()
        if not self.context.MoreSelected():
            print("Click registered, but nothing was selected (missed a part?)")
            return

        # Resolve and emit for the FIRST selected item only -- a
        # single click should sync to a single tree row, even if
        # multiple sub-shapes were somehow selected at once.
        try:
            owner_ais = self.context.SelectedInteractive()
            node_info = self._ais_shape_to_node.get(id(owner_ais))
            if node_info is not None:
                self.part_selected.emit(node_info)
        except Exception as e:
            print(f"(could not resolve owning part for sync: {e})")

        # Also emit the raw shape + type for the position dialog to
        # consume when in positioning mode. Done unconditionally here
        # (MainWindow decides whether to route it to the dialog or
        # ignore it based on whether positioning mode is active).
        try:
            shape = self.context.SelectedShape()
            shape_type = shape.ShapeType()
            self.geometry_picked.emit(shape, shape_type)
        except Exception as e:
            print(f"(could not emit geometry_picked: {e})")

        # Now run the full base reporting (prints every selected
        # face/edge/vertex, same as the standalone script).
        super()._report_selection()

    def set_part_visible(self, node, visible: bool):
        """Show or hide a part, restoring face/edge/vertex selection modes after re-showing."""
        ais_shape = self._node_id_to_ais_shape.get(id(node))
        if ais_shape is None:
            return

        if visible:
            self.context.Display(ais_shape, False)
            self.context.SetDisplayMode(ais_shape, 1, False)
            face_mode = AIS_Shape.SelectionMode_s(TopAbs_FACE)
            edge_mode = AIS_Shape.SelectionMode_s(TopAbs_EDGE)
            vertex_mode = AIS_Shape.SelectionMode_s(TopAbs_VERTEX)
            self.context.Deactivate(ais_shape, 0)
            self.context.Activate(ais_shape, face_mode)
            self.context.Activate(ais_shape, edge_mode)
            self.context.Activate(ais_shape, vertex_mode)
            try:
                self.context.SetSelectionSensitivity(ais_shape, edge_mode, 6)
                self.context.SetSelectionSensitivity(ais_shape, vertex_mode, 8)
            except Exception:
                pass
        else:
            self.context.Erase(ais_shape, False)

        self.context.UpdateCurrentViewer()
        self.update()

    def highlight_node(self, node):
        """
        Highlight a part in the 3D view in response to a TREE
        selection -- the reverse direction of part_selected. Uses
        context.HilightWithColor or, more simply and robustly,
        clears the current selection and re-selects this one shape
        programmatically so the SAME visual highlight a click would
        produce shows up here too.
        """
        ais_shape = self._node_id_to_ais_shape.get(id(node))
        if ais_shape is None:
            return  # sub-assembly container, nothing to highlight directly
        self.context.ClearSelected(False)
        self.context.AddOrRemoveSelected(ais_shape, True)
        self.view.FitAll()  # keep the part in view; comment out if too aggressive
        self.update()


class MainWindow(QMainWindow):
    def __init__(self, step_path):
        super().__init__()
        self.setWindowTitle(f"Basicad -- {step_path}" if step_path else "Basicad")

        self.resize(1400, 800)

        # --- Central widget hosts the existing splitter layout, unchanged.
        # (PHASE 1 of the UI revision -- see DESIGN_BACKLOG item 33: promote
        # to QMainWindow so a real menu bar / status bar / dock widgets are
        # available, without touching any existing dialog or button wiring.)
        central = QWidget()
        self.setCentralWidget(central)
        outer_layout = QVBoxLayout(central)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        outer_layout.addWidget(splitter)

        # --- Left: tree, in its own panel with buttons -----------------
        tree_panel = QWidget()
        tree_layout = QVBoxLayout(tree_panel)
        tree_layout.setContentsMargins(4, 4, 4, 4)

        hint = QLabel(
            "Checkbox: show/hide.  Click a row: highlight in 3D.\n"
            "Drag a row onto another: reparent (hierarchy only,\n"
            "no effect on position)."
        )
        hint.setWordWrap(True)
        tree_layout.addWidget(hint)

        self.tree = AssemblyTreeWidget(tree_panel)
        tree_layout.addWidget(self.tree)

        # Position button -- opens the Mate/Align dialog for whichever
        # part/assembly is currently selected in the tree.
        from PySide6.QtWidgets import QPushButton
        self._position_btn = QPushButton("⊕  Position selected...")
        self._position_btn.setEnabled(False)
        self._position_btn.setToolTip(
            "Open the Mate/Align positioning dialog for the\n"
            "currently selected part or assembly."
        )
        self._position_btn.clicked.connect(self._on_position_clicked)
        tree_layout.addWidget(self._position_btn)

        # NOTE: the old "⊞ Workplane..." button (opened WorkplaneDialog)
        # was removed here in PHASE 3 (DESIGN_BACKLOG item 33) -- the
        # dialog is retired. Workplane creation is now purely the
        # Workplane menu (At Origin / On Face / By 3 Points), and
        # Extrude/Revolve are purely the Create 3D menu, both driven by
        # the status bar, KodaCAD-style. See _build_menu_bar().

        self._fillet_btn = QPushButton("⌀  Fillet")
        self._fillet_btn.setEnabled(False)
        self._fillet_btn.setToolTip(
            "Pick edge(s) on the active part, then enter a radius in "
            "the status bar (or send one from the calculator)."
        )
        self._fillet_btn.clicked.connect(self._on_modify_fillet)
        tree_layout.addWidget(self._fillet_btn)

        self._shell_btn = QPushButton("⬡  Shell")
        self._shell_btn.setEnabled(False)
        self._shell_btn.setToolTip(
            "Pick face(s) to remove, then enter a wall thickness in "
            "the status bar (or send one from the calculator)."
        )
        self._shell_btn.clicked.connect(self._on_modify_shell)
        tree_layout.addWidget(self._shell_btn)

        # Import button -- loads a new STEP file and adds it to the
        # current assembly at the top level, ready to be re-parented
        # and positioned.
        self._import_btn = QPushButton("📂  Import STEP...")
        self._import_btn.setEnabled(False)
        self._import_btn.setToolTip(
            "Import a STEP file and add it to the current\n"
            "assembly. Drag it in the tree to re-parent it,\n"
            "then use Position to place it correctly."
        )
        self._import_btn.clicked.connect(self._on_import_clicked)
        tree_layout.addWidget(self._import_btn)

        # Export button -- saves the current assembly state to a STEP
        # file alongside the input file, with _exported suffix.
        self._export_btn = QPushButton("💾  Export STEP...")
        self._export_btn.setEnabled(False)
        self._export_btn.setToolTip(
            "Export the current assembly (with all positioning\n"
            "applied) to a STEP file."
        )
        self._export_btn.clicked.connect(self._on_export_clicked)
        tree_layout.addWidget(self._export_btn)

        splitter.addWidget(tree_panel)

        # --- Right: the 3D viewport ----------------------------------
        self.viewport = SyncedViewportWidget(self)
        splitter.addWidget(self.viewport)

        splitter.setSizes([350, 1050])

        # --- Sketch toolbar (PHASE 2/3 of the UI revision, DESIGN_BACKLOG
        # item 33) -- KodaCAD-style: a real, persistent QToolBar docked
        # on the main window. Follows whichever workplane is currently
        # marked Active in the tree's WP section (PHASE 3 -- see
        # _on_wp_set_active_requested below), not a dialog.
        self._sketch_toolbar = SketchToolBar(self)
        self.addToolBar(Qt.ToolBarArea.RightToolBarArea, self._sketch_toolbar)

        # --- Position dialog (floating dock) -------------------------
        self._position_dialog = PositionDialog(self, viewport=self.viewport)
        self._position_dialog.hide()
        self._position_dialog.request_redisplay.connect(self._on_redisplay_after_move)
        self._position_dialog.positioning_done.connect(self._on_positioning_done)

        # --- Persistent workplanes (PHASE 3, DESIGN_BACKLOG item 33) --
        # WorkplaneDialog is retired. A workplane created via the
        # Workplane menu now registers here and in the tree's WP
        # section (uid -> dict), staying until deleted via RMB, rather
        # than being thrown away when a dialog closed. See
        # _register_new_workplane() / _on_wp_*.
        self._workplanes = {}   # uid -> {"wp", "border_ais", "visible"}
        self._wp_counter = 0
        self._active_part_tree_item = None   # tree item with orange background
        self._active_part_overlay_ais = None  # wireframe overlay AIS

        # Fillet / Mill / Pull state (PHASE 3B, DESIGN_BACKLOG item 33)
        # -- FilletDialog retired, same pure menu + status-bar treatment
        # as Extrude/Revolve. _modify_mode: None | "mill" | "pull".
        # Fillet accumulates any number of edges (non-sticky --
        # matches KodaCAD's fillet() ending after each apply, unlike
        # the sticky sketch tools).
        self._modify_mode = None
        self._fillet_picking = False
        self._fillet_edges = []
        self._shell_picking = False
        self._shell_faces = []

        # --- Wire the standard sync signals --------------------------
        self.viewport.part_selected.connect(self._on_part_selected_in_viewport)
        self.viewport.geometry_picked.connect(self._on_geometry_picked)
        self.tree.itemClicked.connect(self._on_tree_item_clicked)
        self.tree.itemChanged.connect(self._on_tree_item_changed)
        self.tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        self.tree.active_assembly_changed.connect(self._on_active_assembly_changed)
        self.tree.node_delete_requested.connect(self._on_node_delete_requested)
        self.tree.sub_assembly_created.connect(self._on_sub_assembly_created)
        self.tree.active_part_changed.connect(self._on_active_part_changed)
        # PHASE 3: persistent workplane tree signals. (Visibility
        # toggling is handled directly in _on_tree_item_changed below,
        # mirroring how regular node checkboxes work via Qt's built-in
        # itemChanged rather than a custom signal -- so there's no
        # workplane_visibility_changed connection here.)
        self.tree.workplane_set_active_requested.connect(self._on_wp_set_active_requested)
        self.tree.workplane_clear_active_requested.connect(self._on_wp_clear_active_requested)
        self.tree.workplane_delete_requested.connect(self._on_wp_delete_requested)

        self.step_path = step_path
        self._assembly = None  # set by load()

        # By-3-Points workplane creation state (PHASE 2, DESIGN_BACKLOG
        # item 33) -- mirrors KodaCAD's win.ptStack for wpBy3Pts.
        self._wp3pts_picking = False
        self._wp3pts_points = []

        # On-Face workplane creation state (PHASE 3, DESIGN_BACKLOG item
        # 33) -- mirrors KodaCAD's win.faceStack for wpOnFace. Pure
        # status-bar flow now, replaces WorkplaneDialog's Step 1.
        self._wp_onface_picking = False
        self._wp_onface_faces = []

        # Create 3D state (PHASE 3, DESIGN_BACKLOG item 33) -- Extrude
        # and Revolve, purely menu + status-bar driven, matching
        # KodaCAD's extrude()/revolve() flow exactly (see
        # _on_create3d_extrude / _on_create3d_revolve below).
        # _create3d_mode: None | "extrude" | "revolve"
        # _create3d_stage: which input we're waiting for next
        #   extrude: "length" -> "name"
        #   revolve: "axis" (2 vertex picks) -> "name"
        self._create3d_mode = None
        self._create3d_stage = None
        self._create3d_length = None
        self._create3d_points = []

        # Calculator measurement state (PHASE 2 follow-up, DESIGN_BACKLOG
        # item 33) -- mirrors KodaCAD's distPtPt/edgeLen. None, "dist",
        # or "len". Armed by the calculator's Dist/Len buttons via
        # rpn_calculator.py's measure() -> self.distPtPt()/self.edgeLen().
        self._measure_mode = None
        self._measure_points = []   # collected gp_Pnt for "dist" mode

        # --- Menu bar (PHASE 1 of the UI revision, DESIGN_BACKLOG item 33)
        # Additive only: every action here calls the SAME handler methods
        # the side-panel buttons already call. Nothing is removed, so
        # existing behavior can't regress -- this just gives a second,
        # less "bulky" way to reach the same commands, KodaCAD-style.
        self._build_menu_bar()

        # --- Status bar with a shared line edit + units label, and the
        # RPN calculator (ported from KodaCAD's rpnCalculator.py). The
        # calculator's register buttons (T/Z/Y/X) send their value to
        # whichever QLineEdit currently has keyboard focus -- this works
        # with Depth/Name/etc. fields in the existing dialogs as-is, with
        # no changes needed to those dialogs. If no line edit has focus,
        # the value goes to this shared status-bar line edit instead.
        self._build_status_bar()
        self.calculator = None

    def _build_menu_bar(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        self._add_action(file_menu, "Import STEP...", self._on_import_clicked)
        self._add_action(file_menu, "Export STEP...", self._on_export_clicked)

        workplane_menu = menubar.addMenu("&Workplane")
        self._add_action(workplane_menu, "At Origin, XY Plane",
                         self._on_workplane_at_origin)
        self._add_action(workplane_menu, "On Face...",
                         self._on_workplane_on_face)
        self._add_action(workplane_menu, "By 3 Points...",
                         self._on_workplane_by_3pts)

        # PHASE 3 (DESIGN_BACKLOG item 33): pure menu + status-bar flow,
        # matching KodaCAD's Create 3D menu exactly -- no dialog.
        # Operates on whichever workplane is currently Active in the
        # tree's WP section.
        create3d_menu = menubar.addMenu("&Create 3D")
        self._add_action(create3d_menu, "Extrude", self._on_create3d_extrude)
        self._add_action(create3d_menu, "Revolve", self._on_create3d_revolve)

        modify_menu = menubar.addMenu("&Modify Active Part")
        self._add_action(modify_menu, "Fillet", self._on_modify_fillet)
        self._add_action(modify_menu, "Mill (Cut)", self._on_modify_mill)
        self._add_action(modify_menu, "Pull (Boss)", self._on_modify_pull)
        self._add_action(modify_menu, "Shell", self._on_modify_shell)

        position_menu = menubar.addMenu("&Position")
        self._add_action(position_menu, "Position selected...",
                         self._on_position_clicked)

        utility_menu = menubar.addMenu("&Utility")
        self._add_action(utility_menu, "Calculator", self.launch_calculator)

    def _add_action(self, menu, text, handler):
        """Small helper matching KodaCAD's add_function_to_menu pattern."""
        action = QAction(text, self)
        action.setMenuRole(QAction.MenuRole.NoRole)
        action.triggered.connect(handler)
        menu.addAction(action)
        return action

    def _build_status_bar(self):
        status = self.statusBar()
        status.setSizeGripEnabled(False)

        self.lineEdit = QLineEdit()
        self.lineEdit.setMaximumWidth(160)
        self.lineEdit.returnPressed.connect(self._on_lineedit_return)
        status.addPermanentWidget(self.lineEdit)

        # "Current Operation" label + "End Operation" button (PHASE 2
        # follow-up, DESIGN_BACKLOG item 33) -- KodaCAD has these
        # always visible in the status bar (mainwindow.py's
        # currOpLabel / endOpButton, tied to registerCallback /
        # clearCallback). The sketch toolbar's own "Cancel Tool"
        # toolbar button does the same thing for sketch tools
        # specifically, but it's just an icon buried in a long
        # vertical toolbar -- easy to miss, and doesn't cover the
        # By-3-Points or On-Face picking modes. This is the general,
        # always-visible equivalent, covering all three.
        self.currOpLabel = QLabel("Current Operation: None")
        self.currOpLabel.setFrameStyle(
            QFrame.Shape.StyledPanel | QFrame.Shadow.Sunken)
        status.addPermanentWidget(self.currOpLabel)

        self.endOpButton = QToolButton()
        self.endOpButton.setText("End Operation")
        self.endOpButton.clicked.connect(self._on_end_operation)
        status.addPermanentWidget(self.endOpButton)

        self._sketch_toolbar.tool_armed.connect(self._on_tool_armed_changed)

        self.units = "mm"
        self.unitsLabel = QLabel(f"Units: {self.units} ")
        self.unitsLabel.setFrameStyle(
            QFrame.Shape.StyledPanel | QFrame.Shadow.Sunken)
        status.addPermanentWidget(self.unitsLabel)

        status.showMessage("Ready", 5000)

    def _on_tool_armed_changed(self, name):
        """SketchToolBar.tool_armed fired -- update the Current
        Operation label. Empty string means no tool is armed."""
        self.currOpLabel.setText(f"Current Operation: {name or 'None'}")

    def _on_end_operation(self):
        """
        The status bar's "End Operation" button -- cancels whichever
        operation is currently in progress, checked in the same
        priority order as _on_geometry_picked's routing:
          1. An armed sketch tool (Circle, Line, etc.)
          2. Create 3D (Extrude/Revolve)
          3. Modify Active Part (Mill/Pull)
          4. Fillet edge picking
          5. Shell face picking
          6. On-Face workplane picking
          7. By-3-Points workplane picking
          8. Calculator measurement mode (Dist/Len)
        """
        if self._sketch_toolbar.isEnabled() and \
                self._sketch_toolbar._active_tool is not None:
            self._sketch_toolbar._do_cancel_tool()
            return
        if self._create3d_mode is not None:
            self._cancel_create3d()
            self.statusBar().showMessage("Create 3D cancelled.", 4000)
            return
        if self._modify_mode is not None:
            self._cancel_modify()
            self.statusBar().showMessage("Modify Active Part cancelled.", 4000)
            return
        if self._fillet_picking:
            self._cancel_fillet()
            self.statusBar().showMessage("Fillet cancelled.", 4000)
            return
        if self._shell_picking:
            self._cancel_shell()
            self.statusBar().showMessage("Shell cancelled.", 4000)
            return
        if self._wp_onface_picking:
            self._wp_onface_picking = False
            self._wp_onface_faces = []
            self.statusBar().showMessage("On Face cancelled.", 4000)
            return
        if self._wp3pts_picking:
            self._wp3pts_picking = False
            self._wp3pts_points = []
            self.statusBar().showMessage("By 3 Points cancelled.", 4000)
            return
        if self._measure_mode is not None:
            self._cancel_measure()
            self.statusBar().showMessage("Measurement cancelled.", 4000)
            return
        self.statusBar().showMessage("Nothing to cancel.", 3000)

    def _on_lineedit_return(self):
        """
        Enter pressed in the shared status-bar line edit.

        Priority:
          1. An armed Create 3D operation (Extrude/Revolve) -- gets the
             RAW text, since it needs a string (part name) at some
             stages, not just numbers. See _advance_create3d_text().
          2. An armed Modify Active Part operation (Mill/Pull) --
             numeric only (depth/length). See _advance_modify_text().
          3. Fillet, once at least one edge is picked -- numeric only
             (radius). See _advance_fillet_text().
          4. Shell, once at least one face is picked -- numeric only
             (thickness). See _advance_shell_text().
          5. Otherwise, if a number was typed, queue it for whichever
             sketch tool needs a numeric parameter next (e.g. Circle's
             radius) -- see SketchToolBar.push_pending_float() and
             _retry_active_tool().
        """
        text = self.lineEdit.text()
        self.lineEdit.clear()

        if self._create3d_mode is not None:
            self._advance_create3d_text(text)
            return

        if self._modify_mode is not None:
            self._advance_modify_text(text)
            return

        if self._fillet_picking:
            self._advance_fillet_text(text)
            return

        if self._shell_picking:
            self._advance_shell_text(text)
            return

        try:
            value = float(text)
        except ValueError:
            return
        if self._sketch_toolbar.isEnabled():
            self._sketch_toolbar.push_pending_float(value)

    # -----------------------------------------------------------------------
    # RPN Calculator (ported from KodaCAD's rpnCalculator.py)
    # -----------------------------------------------------------------------

    def launch_calculator(self):
        if not self.calculator:
            self.calculator = Calculator(self)
        self.calculator.show()
        self.calculator.raise_()
        self.calculator.activateWindow()

    def valueFromCalc(self, value):
        """
        Receive a value pushed from the calculator (T/Z/Y/X register
        buttons).

        If a sketch tool is currently armed and waiting for input (see
        SketchToolBar._active_tool), the value goes STRAIGHT into its
        numeric queue -- no separate Enter press needed, matching
        KodaCAD's calculator -> lineEditStack -> callback flow (see
        rpnCalculator.py / mainwindow.py's valueFromCalc).

        FIX: this used to check `QApplication.focusWidget() is
        self.lineEdit` instead of checking the armed tool directly.
        That's unreliable across two separate top-level windows -- the
        calculator is its own QDialog, so clicking one of its buttons
        leaves IT as the OS-focused window, not MainWindow; the
        status-bar line edit was essentially never actually focused at
        the instant a register button was clicked, so the value fell
        through to the "just set text" fallback below (looked like it
        "sat there") and the follow-up Enter press didn't land on it
        either, since keyboard input was still going to the calculator
        window. Checking the armed tool instead sidesteps window-focus
        entirely.

        Otherwise (no tool/operation active), targets whichever
        QLineEdit currently has keyboard focus -- so the calculator is
        immediately useful with any other dialog's input fields, with
        no per-dialog wiring required. Falls back to the shared
        status-bar line edit if nothing else has focus.
        """
        if self._create3d_mode == "extrude" and self._create3d_stage == "length":
            self._advance_create3d_text(str(value))
            return
        if self._modify_mode is not None:
            self._advance_modify_text(str(value))
            return
        if self._fillet_picking and self._fillet_edges:
            self._advance_fillet_text(str(value))
            return
        if self._shell_picking and self._shell_faces:
            self._advance_shell_text(str(value))
            return
        if self._sketch_toolbar.isEnabled() and \
                self._sketch_toolbar._active_tool is not None:
            self._sketch_toolbar.push_pending_float(value)
            return
        target = QApplication.focusWidget()
        if not isinstance(target, QLineEdit):
            target = self.lineEdit
        target.setText(str(value))
        target.setFocus()

    def load(self):
        if self.step_path is None:
            from build123d import Compound
            self._assembly = Compound(label="/")
            self.tree.load_assembly_into_tree(self._assembly)
        else:
            print(f"Loading {self.step_path} ...")
            self._assembly = self.viewport.load_and_display_assembly(
                self.step_path)
            self.tree.load_assembly_into_tree(self._assembly)
            print("Loaded into both tree and viewport.")

        self._export_btn.setEnabled(True)
        self._import_btn.setEnabled(True)
        self._fillet_btn.setEnabled(True)
        self._shell_btn.setEnabled(True)

        # Force OCCT to resize its internal window to match the Qt widget.
        # Without this, the viewport only fills a corner of its allocated
        # space when started without a STEP file (no FitAll triggers resize).
        try:
            self.viewport.view.MustBeResized()
            if self.step_path is not None:
                self.viewport.view.FitAll()
            self.viewport.update()
        except Exception:
            pass


    def _on_part_selected_in_viewport(self, node_info):
        """
        A part was clicked in 3D -- find and select the matching row
        in the tree. node_info is the dict already produced by
        assembly_viewer.py's _display_leaf(): {"label", "path", "node"}.
        """
        target_node = node_info.get("node")
        if target_node is None:
            return
        for item_id, node in self.tree._item_to_node.items():
            if node is target_node:
                # Find the actual QTreeWidgetItem with this id() --
                # _item_to_node is keyed by id(item), so we need the
                # reverse walk to get the item object itself.
                matching_item = self._find_tree_item_by_id(item_id)
                if matching_item is not None:
                    self.tree.blockSignals(True)  # avoid re-triggering itemClicked
                    self.tree.setCurrentItem(matching_item)
                    self.tree.scrollToItem(matching_item)
                    self.tree.blockSignals(False)
                break

    def _find_tree_item_by_id(self, target_item_id):
        """Walk the tree to find the QTreeWidgetItem whose id() matches."""
        def walk(item):
            if id(item) == target_item_id:
                return item
            for i in range(item.childCount()):
                found = walk(item.child(i))
                if found is not None:
                    return found
            return None

        for i in range(self.tree.topLevelItemCount()):
            found = walk(self.tree.topLevelItem(i))
            if found is not None:
                return found
        return None

    def _on_tree_item_clicked(self, item, column):
        """A row was clicked in the tree -- highlight the matching part in 3D."""
        node = self.tree._item_to_node.get(id(item))
        if node is not None:
            self.viewport.highlight_node(node)

    def _on_tree_item_changed(self, item, column):
        """
        A checkbox was toggled. Two cases, since only LEAF parts have
        an AIS_Shape of their own (assembly containers don't -- their
        "shape" is just the union of their children, already shown
        separately):

            - Leaf part checkbox: show/hide that part directly.
            - Assembly/container checkbox: recursively show/hide
              EVERY LEAF DESCENDANT underneath it. This is the fix for
              "show/hide only works on parts, not assemblies" -- a
              container has nothing of its own to erase, so toggling
              its box needs to propagate down to what it actually
              contains.

        Guards against re-entrant itemChanged signals while we
        programmatically set descendants' checkboxes (each
        setCheckState() call below would otherwise re-trigger this
        same handler).
        """
        wp_uid = self.tree._item_to_wp_uid.get(id(item))
        if wp_uid is not None:
            visible = item.checkState(0) == Qt.CheckState.Checked
            self._on_wp_visibility_changed(wp_uid, visible)
            return

        node = self.tree._item_to_node.get(id(item))
        if node is None:
            return
        visible = item.checkState(0) == Qt.CheckState.Checked

        if not node.children:
            # Leaf part: direct show/hide, same as before.
            self.viewport.set_part_visible(node, visible)
            return

        # Container: recursively set the checkbox state (so the UI
        # reflects the change) AND the actual viewport visibility for
        # every leaf descendant.
        self.tree.blockSignals(True)
        try:
            self._apply_visibility_recursive(item, visible)
        finally:
            self.tree.blockSignals(False)

    def _apply_visibility_recursive(self, item, visible):
        """
        Walk a tree item's subtree, setting every row's checkbox to
        match `visible` and applying the corresponding viewport
        show/hide for every LEAF node found along the way.
        """
        node = self.tree._item_to_node.get(id(item))
        new_state = Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked
        item.setCheckState(0, new_state)

        if node is not None and not node.children:
            self.viewport.set_part_visible(node, visible)

        for i in range(item.childCount()):
            self._apply_visibility_recursive(item.child(i), visible)

    # -----------------------------------------------------------------------
    # Positioning dialog handlers
    # -----------------------------------------------------------------------

    def _on_tree_selection_changed(self):
        """Enable the Position button when something is selected in the tree."""
        selected = self.tree.selectedItems()
        self._position_btn.setEnabled(len(selected) > 0)
        # If dialog is open, update its moving node immediately.
        if self._position_dialog.isVisible() and selected:
            item = selected[0]
            node = self.tree._item_to_node.get(id(item))
            if node is not None:
                self._position_dialog.set_moving_node(node)

    def _on_position_clicked(self):
        """Open the positioning dialog for the currently selected tree node."""
        selected = self.tree.selectedItems()
        if not selected:
            return
        item = selected[0]
        node = self.tree._item_to_node.get(id(item))
        if node is None:
            return
        self._position_dialog.set_moving_node(node)
        # Show the dialog as a floating window next to the main window.
        self._position_dialog.show()
        self._position_dialog.raise_()

    def _on_geometry_picked(self, raw_shape, shape_type):
        """
        Route a viewport pick to whichever mode/toolbar is active.
        Priority (each handler can decline by leaving `consumed`
        False, letting the pick fall through to the next one):
          1. On-Face workplane creation (2 face picks)
          2. Create 3D Revolve's axis-pick stage (2 vertex picks)
          3. By-3-Points workplane vertex-pick mode
          4. Calculator measurement mode (Dist/Len)
          5. Sketch toolbar vertex pick (intersection point snap)
          6. Fillet edge picking (any number of edges on active part)
          7. Shell face picking (any number of faces on active part)
          8. Position dialog positioning mode

        PHASE 3 (DESIGN_BACKLOG item 33): WorkplaneDialog, FilletDialog,
        and ShellDialog are all retired -- On-Face, Revolve, Fillet,
        and Shell's picking used to route through dialogs; all are
        inlined here now (self-contained state on MainWindow, mirroring
        the pattern already used for By-3-Points).
        """
        from OCP.TopAbs import TopAbs_VERTEX

        type_name = {TopAbs_VERTEX: "VERTEX"}.get(shape_type, str(shape_type))
        consumed = False

        if self._wp_onface_picking and shape_type == TopAbs_FACE:
            print(f"[route] {type_name} pick -> On-Face workplane")
            self._on_wp_onface_face_picked(raw_shape)
            consumed = True

        if not consumed and self._create3d_mode == "revolve" and \
                self._create3d_stage == "axis" and shape_type == TopAbs_VERTEX:
            print(f"[route] {type_name} pick -> Create3D Revolve axis")
            self._on_create3d_vertex_picked(raw_shape)
            consumed = True

        if not consumed and self._wp3pts_picking and shape_type == TopAbs_VERTEX:
            print(f"[route] {type_name} pick -> By-3-Points")
            self._on_wp3pts_vertex_picked(raw_shape)
            consumed = True

        if not consumed and self._measure_mode == "dist" and \
                shape_type == TopAbs_VERTEX:
            print(f"[route] {type_name} pick -> distPtPt measurement")
            self._on_measure_vertex_picked(raw_shape)
            consumed = True

        if not consumed and self._measure_mode == "len" and \
                shape_type == TopAbs_EDGE:
            print(f"[route] {type_name} pick -> edgeLen measurement")
            self._on_measure_edge_picked(raw_shape)
            consumed = True

        if not consumed and shape_type == TopAbs_VERTEX and \
                self._sketch_toolbar.isEnabled():
            print(f"[route] {type_name} pick -> SketchToolBar.receive_vertex_pick")
            consumed = self._sketch_toolbar.receive_vertex_pick(raw_shape)

        if not consumed and self._fillet_picking and shape_type == TopAbs_EDGE:
            print(f"[route] {type_name} pick -> Fillet edge")
            self._on_fillet_edge_picked(raw_shape)
            consumed = True

        if not consumed and self._shell_picking and shape_type == TopAbs_FACE:
            print(f"[route] {type_name} pick -> Shell face")
            self._on_shell_face_picked(raw_shape)
            consumed = True

        if not consumed and self._position_dialog.isVisible() and \
                self._position_dialog.is_in_positioning_mode():
            self._position_dialog.receive_pick(raw_shape, shape_type)
            consumed = True

        if not consumed:
            print(f"[route] {type_name} pick matched no active handler -- "
                  f"wp_onface_picking={self._wp_onface_picking} "
                  f"create3d_mode={self._create3d_mode} "
                  f"sketch_toolbar enabled={self._sketch_toolbar.isEnabled()}")

    # -----------------------------------------------------------------------
    # Workplane menu handlers (PHASE 3 of the UI revision, DESIGN_BACKLOG
    # item 33). WorkplaneDialog is retired -- all three creation routes
    # now register a persistent workplane (tree WP section) instead of
    # opening a dialog, and none of them auto-activate it: per spec, a
    # workplane must be explicitly marked Active via RMB before Create 3D
    # or the sketch toolbar will operate on it.
    # -----------------------------------------------------------------------

    def _register_new_workplane(self, wp):
        """
        Shared registration path for all three workplane-creation
        routes: assigns a uid ("wp1", "wp2", ...), displays its border,
        and adds it to the tree's WP section. Does NOT activate it --
        the user marks a workplane Active via RMB in the tree.
        """
        self._wp_counter += 1
        uid = f"wp{self._wp_counter}"
        self._workplanes[uid] = {"wp": wp, "border_ais": None, "visible": True}
        self._display_workplane_border(uid)
        self.tree.add_workplane_item(uid, uid)
        return uid

    def _display_workplane_border(self, uid):
        """
        Show a workplane as a semi-transparent green border face plus
        pink U/V crosshair lines (CoCreate style) -- ported from the
        retired WorkplaneDialog._display_workplane(), now per-uid so
        multiple workplanes can be displayed at once.
        """
        entry = self._workplanes.get(uid)
        if entry is None:
            return
        wp = entry["wp"]
        border = wp.border
        if border is None:
            return

        from OCP.AIS import AIS_Shape, AIS_DisplayMode
        from OCP.Quantity import Quantity_Color, Quantity_TypeOfColor
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
        from OCP.GC import GC_MakeSegment
        from OCP.gp import gp_Pnt

        wp_color = Quantity_Color(0.3, 0.75, 0.4,
                                  Quantity_TypeOfColor.Quantity_TOC_RGB)
        axis_color = Quantity_Color(0.85, 0.2, 0.55,
                                    Quantity_TypeOfColor.Quantity_TOC_RGB)
        ctx = self.viewport.context

        ais_border = AIS_Shape(border)
        ais_border.SetColor(wp_color)
        ais_border.SetDisplayMode(AIS_DisplayMode.AIS_Shaded)
        ais_border.SetTransparency(0.5)
        ctx.Display(ais_border, False)
        ctx.Deactivate(ais_border)

        size = wp.size

        def make_axis_line(p1_2d, p2_2d):
            p1 = gp_Pnt(p1_2d[0], p1_2d[1], 0).Transformed(wp.Trsf)
            p2 = gp_Pnt(p2_2d[0], p2_2d[1], 0).Transformed(wp.Trsf)
            edge = BRepBuilderAPI_MakeEdge(
                GC_MakeSegment(p1, p2).Value()).Edge()
            ais = AIS_Shape(edge)
            ais.SetColor(axis_color)
            ais.SetWidth(1.5)
            return ais

        ais_u = make_axis_line((-size, 0), (size, 0))
        ais_v = make_axis_line((0, -size), (0, size))
        for ais_line in (ais_u, ais_v):
            ctx.Display(ais_line, False)
            ctx.Deactivate(ais_line)

        ctx.UpdateCurrentViewer()
        self.viewport.update()
        entry["border_ais"] = [ais_border, ais_u, ais_v]

    def _erase_workplane_border(self, uid):
        entry = self._workplanes.get(uid)
        if entry is None or entry["border_ais"] is None:
            return
        ctx = self.viewport.context
        for ais in entry["border_ais"]:
            ctx.Erase(ais, False)
        ctx.UpdateCurrentViewer()
        self.viewport.update()
        entry["border_ais"] = None

    def _on_workplane_at_origin(self):
        """Default workplane located in the X-Y plane at the origin."""
        if self._assembly is None:
            return
        wp = WorkPlane(size=80)
        uid = self._register_new_workplane(wp)
        self.statusBar().showMessage(
            f"Workplane '{uid}' created at origin, XY plane. "
            f"Right-click it in the tree and choose Set Active to use it.",
            6000)

    def _on_workplane_on_face(self):
        """
        Arm On-Face picking: 2 face picks, matching KodaCAD's wpOnFace
        -- the first sets the workplane's plane, the second sets the U
        direction. Pure status-bar flow now (PHASE 3) -- was the last
        remaining use of WorkplaneDialog's Step 1, now inlined here.
        """
        if self._assembly is None:
            return
        self._cancel_other_operations(keep="onface")
        self._wp_onface_picking = True
        self._wp_onface_faces = []
        from OCP.AIS import AIS_Shape
        ctx = self.viewport.context
        for ais in self.viewport._ais_shapes:
            ctx.Activate(ais, AIS_Shape.SelectionMode_s(TopAbs_FACE))
        ctx.UpdateCurrentViewer()
        self.statusBar().showMessage(
            "On Face: click a face to set the workplane's plane.")

    def _on_wp_onface_face_picked(self, raw_shape):
        self._wp_onface_faces.append(raw_shape)
        if len(self._wp_onface_faces) == 1:
            self.statusBar().showMessage(
                "On Face: now click a second face to set the U direction.")
            return

        face_w, face_u = self._wp_onface_faces
        self._wp_onface_picking = False
        self._wp_onface_faces = []
        try:
            wp = WorkPlane(size=80, face=face_w, faceU=face_u)
        except Exception as e:
            self.statusBar().showMessage(
                f"Could not create a workplane from those two faces: {e}",
                6000)
            return
        uid = self._register_new_workplane(wp)
        self.statusBar().showMessage(
            f"Workplane '{uid}' created on face. Right-click it in the "
            f"tree and choose Set Active to use it.", 6000)

    def _on_workplane_by_3pts(self):
        """
        Direction from pt1 to pt2 sets the workplane's W direction
        (normal); pt2 becomes the origin. Direction from pt2 to pt3
        sets the U direction. Mirrors KodaCAD's wpBy3Pts.
        """
        if self._assembly is None:
            return
        self._cancel_other_operations(keep="wp3pts")
        self._wp3pts_picking = True
        self._wp3pts_points = []
        from OCP.AIS import AIS_Shape
        ctx = self.viewport.context
        for ais in self.viewport._ais_shapes:
            ctx.Activate(ais, AIS_Shape.SelectionMode_s(TopAbs_VERTEX))
        ctx.UpdateCurrentViewer()
        self.statusBar().showMessage(
            "By 3 Points: pick point 1 (with point 2, sets the W/normal "
            "direction).")

    def _on_wp3pts_vertex_picked(self, raw_shape):
        from OCP.BRep import BRep_Tool
        from OCP.TopoDS import TopoDS
        try:
            vertex = TopoDS.Vertex_s(raw_shape)
            pnt = BRep_Tool.Pnt_s(vertex)
        except Exception as e:
            print(f"[By3Pts] Could not resolve picked vertex: {e}")
            return

        self._wp3pts_points.append(pnt)
        n = len(self._wp3pts_points)
        if n == 1:
            self.statusBar().showMessage(
                "By 3 Points: pick point 2 (becomes the workplane origin).")
        elif n == 2:
            self.statusBar().showMessage(
                "By 3 Points: pick point 3 (sets the U direction).")
        elif n == 3:
            self._finish_workplane_by_3pts()

    def _finish_workplane_by_3pts(self):
        from OCP.gp import gp_Vec, gp_Dir, gp_Ax3
        p1, p2, p3 = self._wp3pts_points
        self._wp3pts_picking = False
        self._wp3pts_points = []

        try:
            wDir = gp_Dir(gp_Vec(p1, p2))
            uDir = gp_Dir(gp_Vec(p2, p3))
            axis3 = gp_Ax3(p2, wDir, uDir)
            wp = WorkPlane(size=80, ax3=axis3)
        except Exception as e:
            self.statusBar().showMessage(
                f"Could not build a workplane from those 3 points "
                f"(are they collinear?): {e}", 6000)
            return

        uid = self._register_new_workplane(wp)
        self.statusBar().showMessage(
            f"Workplane '{uid}' created by 3 points. Right-click it in "
            f"the tree and choose Set Active to use it.", 6000)

    # -----------------------------------------------------------------------
    # Tree "WP" section signal handlers (PHASE 3, DESIGN_BACKLOG item 33)
    # -----------------------------------------------------------------------

    def _on_wp_visibility_changed(self, uid, visible):
        entry = self._workplanes.get(uid)
        if entry is None:
            return
        entry["visible"] = visible
        ctx = self.viewport.context
        if entry["border_ais"] is not None:
            for ais in entry["border_ais"]:
                if visible:
                    ctx.Display(ais, False)
                else:
                    ctx.Erase(ais, False)
        # If this is the active workplane, its live sketch AIS
        # (construction lines / profile / markers) follow too.
        if uid == self._active_wp_uid() and self._sketch_toolbar.isEnabled():
            for ais, _ in list(self._sketch_toolbar._sketch_ais):
                ctx.Display(ais, False) if visible else ctx.Erase(ais, False)
            for ais in list(self._sketch_toolbar._isect_ais):
                ctx.Display(ais, False) if visible else ctx.Erase(ais, False)
        ctx.UpdateCurrentViewer()
        self.viewport.update()

    def _active_wp_uid(self):
        return self.tree.get_active_workplane_uid()

    def _on_wp_set_active_requested(self, uid):
        entry = self._workplanes.get(uid)
        if entry is None:
            return
        # Deactivate whatever was active before (erases ITS live
        # construction-line/profile/marker AIS -- the underlying
        # WorkPlane data is untouched and will redisplay correctly if
        # reactivated later, since set_workplane() now also calls
        # _redisplay_profile()).
        if self._sketch_toolbar.isEnabled():
            self._sketch_toolbar.deactivate()
        self.tree.set_active_workplane(uid)
        self._sketch_toolbar.set_workplane(entry["wp"], self.viewport)
        self.statusBar().showMessage(f"'{uid}' is now the active workplane.", 4000)

    def _on_wp_clear_active_requested(self):
        if self._sketch_toolbar.isEnabled():
            self._sketch_toolbar.deactivate()
        self.tree.set_active_workplane(None)
        self.statusBar().showMessage("No active workplane.", 4000)

    def _on_wp_delete_requested(self, uid):
        entry = self._workplanes.get(uid)
        if entry is None:
            return
        if uid == self._active_wp_uid() and self._sketch_toolbar.isEnabled():
            self._sketch_toolbar.deactivate()
        self._erase_workplane_border(uid)
        del self._workplanes[uid]
        self.tree.remove_workplane_item(uid)
        self.statusBar().showMessage(f"Workplane '{uid}' deleted.", 4000)

    # -----------------------------------------------------------------------
    # Create 3D: Extrude / Revolve (PHASE 3, DESIGN_BACKLOG item 33).
    # Pure menu + status-bar flow, matching KodaCAD's extrude()/revolve()
    # exactly -- no dialog. Both operate on whichever workplane is
    # currently marked Active in the tree's WP section.
    # -----------------------------------------------------------------------

    def _active_workplane(self):
        """Return the active WorkPlane instance, or None."""
        uid = self._active_wp_uid()
        if uid is None:
            return None
        entry = self._workplanes.get(uid)
        return entry["wp"] if entry else None

    def _on_create3d_extrude(self):
        wp = self._active_workplane()
        if wp is None:
            self.statusBar().showMessage(
                "No active workplane. Right-click one in the tree and "
                "choose Set Active first.", 6000)
            return
        if not wp.edgeList:
            self.statusBar().showMessage(
                "The active workplane has no sketch profile yet.", 6000)
            return
        self._cancel_other_operations(keep="create3d")
        self._create3d_mode = "extrude"
        self._create3d_stage = "length"
        self._create3d_length = None
        self.currOpLabel.setText("Current Operation: extrude")
        self.statusBar().showMessage(
            "Enter extrusion length, then enter part name.")
        self.lineEdit.setFocus()

    def _on_create3d_revolve(self):
        wp = self._active_workplane()
        if wp is None:
            self.statusBar().showMessage(
                "No active workplane. Right-click one in the tree and "
                "choose Set Active first.", 6000)
            return
        if not wp.edgeList:
            self.statusBar().showMessage(
                "The active workplane has no sketch profile yet.", 6000)
            return
        self._cancel_other_operations(keep="create3d")
        self._create3d_mode = "revolve"
        self._create3d_stage = "axis"
        self._create3d_points = []
        self.currOpLabel.setText("Current Operation: revolve")
        from OCP.AIS import AIS_Shape
        ctx = self.viewport.context
        for ais in self.viewport._ais_shapes:
            ctx.Activate(ais, AIS_Shape.SelectionMode_s(TopAbs_VERTEX))
        ctx.UpdateCurrentViewer()
        self.statusBar().showMessage("Pick two points on revolve axis.")

    def _on_create3d_vertex_picked(self, raw_shape):
        """Revolve's axis picks (2 points), while _create3d_stage == 'axis'."""
        from OCP.BRep import BRep_Tool
        from OCP.TopoDS import TopoDS
        try:
            vertex = TopoDS.Vertex_s(raw_shape)
            pnt = BRep_Tool.Pnt_s(vertex)
        except Exception as e:
            print(f"[Revolve] Could not resolve picked vertex: {e}")
            return
        self._create3d_points.append(pnt)
        if len(self._create3d_points) == 1:
            self.statusBar().showMessage("Select 2nd point on revolve axis.")
        elif len(self._create3d_points) == 2:
            self._create3d_stage = "name"
            self.statusBar().showMessage("Enter part name.")
            self.lineEdit.setFocus()

    def _advance_create3d_text(self, text):
        """
        Called from _on_lineedit_return when a Create 3D operation is
        armed -- routes a typed Enter to the right stage. Extrude:
        length (float) then name (string). Revolve: name (string) only,
        after the 2 axis picks are already done.
        """
        if self._create3d_mode == "extrude":
            if self._create3d_stage == "length":
                try:
                    length = float(text)
                except ValueError:
                    self.statusBar().showMessage(
                        "Invalid length -- enter a number.", 4000)
                    return
                if length <= 0:
                    self.statusBar().showMessage(
                        "Length must be positive.", 4000)
                    return
                self._create3d_length = length
                self._create3d_stage = "name"
                self.statusBar().showMessage("Enter part name.")
                return
            if self._create3d_stage == "name":
                self._finish_create3d_extrude(text.strip() or "new_part")
                return

        if self._create3d_mode == "revolve" and self._create3d_stage == "name":
            self._finish_create3d_revolve(text.strip() or "new_part")

    def _finish_create3d_extrude(self, name):
        wp = self._active_workplane()
        try:
            node = solid_ops.extrude(wp, self._create3d_length, name)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.statusBar().showMessage(f"Extrude failed: {e}", 8000)
            self._cancel_create3d()
            return
        self._on_part_created(node)
        self._cancel_create3d()
        self.statusBar().showMessage(f"Part '{name}' created.", 4000)

    def _finish_create3d_revolve(self, name):
        wp = self._active_workplane()
        p1, p2 = self._create3d_points
        try:
            node = solid_ops.revolve(wp, p1, p2, name)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.statusBar().showMessage(f"Revolve failed: {e}", 8000)
            self._cancel_create3d()
            return
        self._on_part_created(node)
        self._cancel_create3d()
        self.statusBar().showMessage(f"Part '{name}' created.", 4000)

    def _cancel_create3d(self):
        self._create3d_mode = None
        self._create3d_stage = None
        self._create3d_length = None
        self._create3d_points = []
        self.currOpLabel.setText("Current Operation: None")

    def _cancel_other_operations(self, keep=None):
        """
        Cancel every armed operation EXCEPT `keep` (one of "sketch",
        "measure", "create3d", "wp3pts", "onface", "modify", "fillet",
        "shell", or None to cancel everything). Called at the start of every operation-arming
        entry point (BUG FIX, DESIGN_BACKLOG item 33): these all
        compete for the same vertex/edge picks in
        _on_geometry_picked's routing chain, several are sticky (stay
        armed after completing), and only a couple of pairwise cancels
        existed (distPtPt/edgeLen already cancelled the sketch tool,
        but not vice versa) -- so ANY one of them getting stuck armed,
        even from an earlier session, would silently hijack picks
        meant for whichever one the user actually clicked next, with
        no visible sign why. Reported concretely as stale measurement
        mode swallowing sketch-tool circle-center picks; this closes
        the whole bug class rather than just that one pairing.
        """
        if keep != "sketch" and self._sketch_toolbar._active_tool is not None:
            self._sketch_toolbar._do_cancel_tool()
        if keep != "measure" and self._measure_mode is not None:
            self._cancel_measure()
        if keep != "create3d" and self._create3d_mode is not None:
            self._cancel_create3d()
        if keep != "wp3pts" and self._wp3pts_picking:
            self._wp3pts_picking = False
            self._wp3pts_points = []
        if keep != "onface" and self._wp_onface_picking:
            self._wp_onface_picking = False
            self._wp_onface_faces = []
        if keep != "modify" and self._modify_mode is not None:
            self._cancel_modify()
        if keep != "fillet" and self._fillet_picking:
            self._cancel_fillet()
        if keep != "shell" and self._shell_picking:
            self._cancel_shell()

    # -----------------------------------------------------------------------
    # Calculator measurement (PHASE 2 follow-up, DESIGN_BACKLOG item 33).
    # Mirrors KodaCAD's distPtPt/edgeLen exactly -- these method names are
    # what rpn_calculator.py's measure() looks up via getattr(caller, ...).
    # Rad/Ang are left as no-ops: they're unimplemented in KodaCAD itself
    # too (its own rpnCalculator.py wires them to self.noop), so this
    # isn't a regression.
    # -----------------------------------------------------------------------

    def distPtPt(self):
        """
        Arm point-distance measurement: pick 2 points anywhere in the
        model, push the distance into the calculator's X register.
        Sticky, like sketch tools -- stays armed for another
        measurement until End Operation or a different operation
        starts. Called by the calculator's "Dist" button.
        """
        if self._assembly is None:
            return
        self._cancel_other_operations(keep="measure")
        self._measure_mode = "dist"
        self._measure_points = []
        self.currOpLabel.setText("Current Operation: dist")
        from OCP.AIS import AIS_Shape
        ctx = self.viewport.context
        for ais in self.viewport._ais_shapes:
            ctx.Activate(ais, AIS_Shape.SelectionMode_s(TopAbs_VERTEX))
        ctx.UpdateCurrentViewer()
        self.statusBar().showMessage("Dist: pick point 1.")

    def _on_measure_vertex_picked(self, raw_shape):
        from OCP.BRep import BRep_Tool
        from OCP.TopoDS import TopoDS
        try:
            vertex = TopoDS.Vertex_s(raw_shape)
            pnt = BRep_Tool.Pnt_s(vertex)
        except Exception as e:
            print(f"[distPtPt] Could not resolve picked vertex: {e}")
            return
        self._measure_points.append(pnt)
        if len(self._measure_points) == 1:
            print("[distPtPt] Point 1 captured. Waiting for point 2.")
            self.statusBar().showMessage("Dist: pick point 2.")
            return
        from OCP.gp import gp_Vec
        p1, p2 = self._measure_points
        self._measure_points = []
        dist = gp_Vec(p1, p2).Magnitude()
        if self.calculator is not None:
            self.calculator.putx(dist)
        print(f"[distPtPt] Distance = {dist:.3f} mm")
        self.statusBar().showMessage(
            f"Distance = {dist:.3f} mm  (pick 2 more points for another, "
            f"or End Operation to stop.)", 6000)
        # Sticky -- self._measure_mode stays "dist" for another round.

    def edgeLen(self):
        """
        Arm edge-length measurement: pick an edge anywhere in the
        model, push its length into the calculator's X register.
        Sticky. Called by the calculator's "Len" button.
        """
        if self._assembly is None:
            return
        self._cancel_other_operations(keep="measure")
        self._measure_mode = "len"
        self.currOpLabel.setText("Current Operation: len")
        from OCP.AIS import AIS_Shape
        ctx = self.viewport.context
        for ais in self.viewport._ais_shapes:
            ctx.Activate(ais, AIS_Shape.SelectionMode_s(TopAbs_EDGE))
        ctx.UpdateCurrentViewer()
        self.statusBar().showMessage("Len: pick an edge.")

    def _on_measure_edge_picked(self, raw_shape):
        from OCP.TopoDS import TopoDS
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.CPnts import CPnts_AbscissaPoint
        try:
            edge = TopoDS.Edge_s(raw_shape)
            length = CPnts_AbscissaPoint.Length_s(BRepAdaptor_Curve(edge))
        except Exception as e:
            self.statusBar().showMessage(
                f"Could not measure that edge: {e}", 5000)
            return
        if self.calculator is not None:
            self.calculator.putx(length)
        print(f"[edgeLen] Length = {length:.3f} mm")
        self.statusBar().showMessage(
            f"Length = {length:.3f} mm  (pick another edge, or End "
            f"Operation to stop.)", 6000)
        # Sticky -- self._measure_mode stays "len" for another round.

    def _cancel_measure(self):
        """Shared teardown for measurement mode -- used by End
        Operation and by SketchToolBar._start_tool()'s symmetric
        cancel (see sketch_toolbar.py)."""
        self._measure_mode = None
        self._measure_points = []
        self.currOpLabel.setText("Current Operation: None")

    # -----------------------------------------------------------------------
    # Modify Active Part: Fillet / Mill (cut) / Pull (boss) -- PHASE 3B,
    # DESIGN_BACKLOG item 33. FilletDialog is retired: it had its own
    # disconnected QLineEdit, so typing a radius or using the calculator
    # never worked -- the ONLY way in was clicking its own Apply button.
    # Mill/Pull (cut/add material using the active workplane's profile
    # into/onto the active part) didn't exist in BasiCAD at all before
    # this. All three are now pure menu + status-bar flows, matching
    # KodaCAD's fillet()/mill()/pull() exactly -- no dialog.
    # -----------------------------------------------------------------------

    def _on_modify_fillet(self):
        """Arm Fillet: pick any number of edges on the active part, then
        type a radius (or send one from the calculator) to apply to all
        of them at once. Non-sticky -- matches KodaCAD's fillet(), which
        ends after each apply rather than staying armed."""
        if self._assembly is None:
            return
        active = self.tree.get_active_part()
        if active is None:
            self.statusBar().showMessage(
                "No active part. Right-click one in the tree and choose "
                "'Set Active Part' first.", 6000)
            return
        self._cancel_other_operations(keep=None)
        self._fillet_picking = True
        self._fillet_edges = []
        self.currOpLabel.setText("Current Operation: fillet")
        from OCP.AIS import AIS_Shape
        ctx = self.viewport.context
        for ais in self.viewport._ais_shapes:
            ctx.Activate(ais, AIS_Shape.SelectionMode_s(TopAbs_EDGE))
        ctx.UpdateCurrentViewer()
        self.statusBar().showMessage(
            "Fillet: pick edge(s) on the active part, then enter a "
            "radius.")

    def _on_fillet_edge_picked(self, raw_shape):
        from OCP.TopoDS import TopoDS
        try:
            edge = TopoDS.Edge_s(raw_shape)
        except Exception as e:
            print(f"[Fillet] Could not cast pick to an edge: {e}")
            return
        self._fillet_edges.append(edge)
        self.statusBar().showMessage(
            f"Fillet: {len(self._fillet_edges)} edge(s) selected. Pick "
            f"more, or enter a radius.")

    def _advance_fillet_text(self, text):
        try:
            radius = float(text)
        except ValueError:
            self.statusBar().showMessage(
                "Invalid radius -- enter a number.", 4000)
            return
        if radius <= 0:
            self.statusBar().showMessage("Radius must be positive.", 4000)
            return
        if not self._fillet_edges:
            self.statusBar().showMessage(
                "No edges selected yet -- pick at least one edge first.",
                5000)
            return
        part = self.tree.get_active_part()
        if part is None:
            self.statusBar().showMessage(
                "Active part no longer available.", 5000)
            self._cancel_fillet()
            return
        try:
            new_shape = solid_ops.apply_fillet(
                part.wrapped, self._fillet_edges, radius)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.statusBar().showMessage(f"Fillet failed: {e}", 8000)
            self._cancel_fillet()
            return
        self._cancel_fillet()
        self._apply_shape_to_node(part, new_shape, operation="fillet")
        self.statusBar().showMessage("Fillet complete.", 4000)

    def _cancel_fillet(self):
        self._fillet_picking = False
        self._fillet_edges = []
        self.currOpLabel.setText("Current Operation: None")

    def _on_modify_mill(self):
        """Arm Mill (cut): the active workplane's profile is cut INTO
        the active part, extruded in the -w direction. Type a depth
        (or send one from the calculator) to complete."""
        self._start_modify("mill", "Enter milling depth (cuts into "
                           "active part).")

    def _on_modify_pull(self):
        """Arm Pull (boss): the active workplane's profile is fused
        ONTO the active part, extruded in the +w direction. Type a
        length (or send one from the calculator) to complete."""
        self._start_modify("pull", "Enter pull distance (adds to "
                           "active part).")

    def _start_modify(self, mode, prompt):
        part = self.tree.get_active_part()
        if part is None:
            self.statusBar().showMessage(
                "No active part. Right-click one in the tree and choose "
                "'Set Active Part' first.", 6000)
            return
        wp = self._active_workplane()
        if wp is None:
            self.statusBar().showMessage(
                "No active workplane. Right-click one in the tree and "
                "choose Set Active first.", 6000)
            return
        if not wp.edgeList:
            self.statusBar().showMessage(
                "The active workplane has no sketch profile yet.", 6000)
            return
        self._cancel_other_operations(keep=None)
        self._modify_mode = mode
        self.currOpLabel.setText(f"Current Operation: {mode}")
        self.statusBar().showMessage(prompt)
        self.lineEdit.setFocus()

    def _advance_modify_text(self, text):
        try:
            value = float(text)
        except ValueError:
            self.statusBar().showMessage(
                "Invalid value -- enter a number.", 4000)
            return
        part = self.tree.get_active_part()
        wp = self._active_workplane()
        if part is None or wp is None:
            self.statusBar().showMessage(
                "Active part or active workplane no longer available.",
                6000)
            self._cancel_modify()
            return
        mode = self._modify_mode
        try:
            if mode == "mill":
                new_shape = solid_ops.cut_active_part(wp, part.wrapped, value)
            else:
                new_shape = solid_ops.pull_active_part(wp, part.wrapped, value)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.statusBar().showMessage(f"{mode} failed: {e}", 8000)
            self._cancel_modify()
            return
        self._cancel_modify()
        self._apply_shape_to_node(part, new_shape, operation=mode)
        self.statusBar().showMessage(f"{mode} complete.", 4000)

    def _cancel_modify(self):
        self._modify_mode = None
        self.currOpLabel.setText("Current Operation: None")

    def _apply_shape_to_node(self, node, new_shape, operation="modify"):
        """
        Replace node._wrapped with new_shape and redisplay the node
        in its correct assembled position. Handles the global_location
        capture, ancestor rebuild, AIS removal, and redisplay.
        Used by fillet, shell, and cut to propagate to shared instances.
        """
        from build123d import Location as B123Location

        # Capture parent's global location BEFORE replacing _wrapped.
        # Boolean/fillet ops strip the location tag from their result.
        # global_location is derived from _wrapped.Location(), so it
        # breaks after we store the identity-located new_shape.
        # We need parent's transform only (not node's own rotation)
        # since the result geometry is already in node's local frame.
        node_loc = node.location
        global_loc = node.global_location
        parent_global = B123Location(
            global_loc.wrapped.Multiplied(node_loc.wrapped.Inverted()))

        node._wrapped = new_shape
        self._rebuild_ancestors(node)

        self.viewport.context.ClearSelected(False)
        old_ais = self.viewport._node_id_to_ais_shape.get(id(node))
        original_color_rgb = None
        if old_ais is not None:
            info = self.viewport._ais_shape_to_node.get(id(old_ais))
            if info:
                original_color_rgb = info.get("color_rgb")
            self.viewport.context.Deactivate(old_ais)
            self.viewport.context.Remove(old_ais, False)
            self.viewport._ais_shapes = [
                s for s in self.viewport._ais_shapes if s is not old_ais
            ]
            self.viewport._ais_shape_to_node.pop(id(old_ais), None)
            del self.viewport._node_id_to_ais_shape[id(node)]

        self.viewport.display_node(node, f"/{node.label or 'part'}",
                                   override_location=parent_global)

        if original_color_rgb is not None:
            new_ais = self.viewport._node_id_to_ais_shape.get(id(node))
            if new_ais is not None:
                from OCP.Quantity import Quantity_Color, Quantity_TypeOfColor
                r, g, b = original_color_rgb
                new_ais.SetColor(Quantity_Color(
                    r, g, b, Quantity_TypeOfColor.Quantity_TOC_RGB))
                self.viewport.context.Redisplay(new_ais, False)
                self.viewport._apply_black_edges(new_ais)
                info = self.viewport._ais_shape_to_node.get(id(new_ais))
                if info:
                    info["color_rgb"] = original_color_rgb

        self.viewport._apply_black_edges()
        self.viewport.context.UpdateCurrentViewer()
        self.viewport.update()
        self._on_active_part_changed(node)
        print(f"{operation} complete: '{node.label}' updated.")

    def _on_fillet_done(self, node, new_shape):
        """
        Fillet complete -- replace the part's geometry.
        NOTE: This makes the modified instance an independent copy --
        it is no longer a shared instance. See DESIGN_BACKLOG item 26.
        """
        self._apply_shape_to_node(node, new_shape, operation="fillet")

    def _on_modify_shell(self):
        """Arm Shell: pick any number of faces to remove, then type a
        wall thickness (or send one from the calculator) to apply.
        Non-sticky -- matches KodaCAD's shell(), which ends after each
        apply rather than staying armed."""
        if self._assembly is None:
            return
        active = self.tree.get_active_part()
        if active is None:
            self.statusBar().showMessage(
                "No active part. Right-click one in the tree and choose "
                "'Set Active Part' first.", 6000)
            return
        self._cancel_other_operations(keep=None)
        self._shell_picking = True
        self._shell_faces = []
        self.currOpLabel.setText("Current Operation: shell")
        from OCP.AIS import AIS_Shape
        ctx = self.viewport.context
        for ais in self.viewport._ais_shapes:
            ctx.Activate(ais, AIS_Shape.SelectionMode_s(TopAbs_FACE))
        ctx.UpdateCurrentViewer()
        self.statusBar().showMessage(
            "Shell: pick face(s) to remove, then enter a wall "
            "thickness.")

    def _on_shell_face_picked(self, raw_shape):
        from OCP.TopoDS import TopoDS
        try:
            face = TopoDS.Face_s(raw_shape)
        except Exception as e:
            print(f"[Shell] Could not cast pick to a face: {e}")
            return
        self._shell_faces.append(face)
        self.statusBar().showMessage(
            f"Shell: {len(self._shell_faces)} face(s) selected. Pick "
            f"more, or enter a wall thickness.")

    def _advance_shell_text(self, text):
        try:
            thickness = float(text)
        except ValueError:
            self.statusBar().showMessage(
                "Invalid thickness -- enter a number.", 4000)
            return
        if thickness <= 0:
            self.statusBar().showMessage("Thickness must be positive.", 4000)
            return
        if not self._shell_faces:
            self.statusBar().showMessage(
                "No faces selected yet -- pick at least one face first.",
                5000)
            return
        part = self.tree.get_active_part()
        if part is None:
            self.statusBar().showMessage(
                "Active part no longer available.", 5000)
            self._cancel_shell()
            return
        try:
            new_shape = solid_ops.apply_shell(
                part, self._shell_faces, thickness)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.statusBar().showMessage(f"Shell failed: {e}", 8000)
            self._cancel_shell()
            return
        self._cancel_shell()
        self._apply_shape_to_node(part, new_shape, operation="shell")
        self.statusBar().showMessage("Shell complete.", 4000)

    def _cancel_shell(self):
        self._shell_picking = False
        self._shell_faces = []
        self.currOpLabel.setText("Current Operation: None")

    def _on_part_created(self, new_node):
        """
        A new solid was extruded -- add it under the active assembly
        (or top-level assembly if none is set) and display it.
        """
        if self._assembly is None:
            return

        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
        from step_assembly_poc import add_node

        target = self.tree.get_target_node()
        add_node(new_node, target)
        print(f"New part '{new_node.label}' added under '{target.label}'.")

        # Rebuild _wrapped for all ancestor Compound nodes so that
        # export_step's _create_xde can register them in the XDE document.
        # An empty TopoDS_Compound has no sub-shapes, so AddShape returns a
        # null label and all descendants get skipped.
        self._rebuild_ancestors(new_node)

        self.viewport.display_subtree(new_node, f"/{new_node.label}")
        self.tree.add_node_to_tree(new_node, parent_node=target)
        print(f"Part '{new_node.label}' created and displayed.")

    def _rebuild_ancestors(self, node):
        """
        Walk up the tree from `node`, rebuilding each Compound ancestor's
        _wrapped to be a TopoDS_Compound containing all its descendants'
        shapes. This ensures export_step's XDE document can register every
        node with a non-null label.
        """
        from build123d import Compound
        from OCP.BRep import BRep_Builder
        from OCP.TopoDS import TopoDS_Compound
        from anytree import PreOrderIter

        parent = node.parent
        while parent is not None:
            if isinstance(parent, Compound):
                builder = BRep_Builder()
                compound = TopoDS_Compound()
                builder.MakeCompound(compound)
                # Add all descendant shapes to the compound
                for desc in PreOrderIter(parent):
                    if desc is parent:
                        continue
                    w = getattr(desc, '_wrapped', None)
                    if w is not None:
                        try:
                            builder.Add(compound, w)
                        except Exception:
                            pass
                parent._wrapped = compound
            parent = parent.parent

    def _on_redisplay_after_move(self, moved_node):
        """
        A move was applied to `moved_node` -- refresh its AIS_Shape(s)
        in the viewport to reflect the new position.

        Strategy: erase and re-display every leaf descendant of the
        moved node (or the node itself if it's a leaf), since their
        AIS_Shape objects were created with the OLD global_location
        baked in -- we need to reconstruct them with the new position.

        This is the simplest, most robust approach: same tree-walk as
        the initial load, just on the moved subtree only.
        """
        if self._assembly is None:
            return

        # Collect every leaf descendant of moved_node (or just itself).
        leaves = []
        if not moved_node.children:
            leaves = [moved_node]
        else:
            leaves = [n for n in moved_node.descendants if not n.children]

        for leaf in leaves:
            # Capture the original color BEFORE erasing the old shape.
            old_ais = self.viewport._node_id_to_ais_shape.get(id(leaf))
            original_color = None
            if old_ais is not None:
                old_info = self.viewport._ais_shape_to_node.get(id(old_ais))
                if old_info is not None:
                    original_color = old_info.get("color_rgb")
                self.viewport.context.Erase(old_ais, False)
                self.viewport._ais_shapes = [
                    s for s in self.viewport._ais_shapes if s is not old_ais
                ]
                del self.viewport._ais_shape_to_node[id(old_ais)]
                del self.viewport._node_id_to_ais_shape[id(leaf)]

            # Re-display with the new position, reusing the original
            # color if we captured it -- otherwise the palette_index
            # approach assigns a different color since the index is now
            # different (the moved leaves get added at the end of the
            # list). Passing original_color via a temporary node color
            # override isn't clean, so instead we patch the palette
            # lookup directly: if we have a stored color, temporarily
            # set node.color to a sentinel that _display_leaf will use.
            # Simpler approach: just re-display and immediately override
            # the color on the newly-created AIS_Shape.
            palette_index = len(self.viewport._ais_shapes)
            self.viewport._display_leaf(leaf, f"/{leaf.label}", palette_index)

            # Override with the original color if we had one.
            if original_color is not None:
                new_ais = self.viewport._node_id_to_ais_shape.get(id(leaf))
                if new_ais is not None:
                    from OCP.Quantity import Quantity_Color, Quantity_TypeOfColor
                    r, g, b = original_color
                    color = Quantity_Color(r, g, b, Quantity_TypeOfColor.Quantity_TOC_RGB)
                    new_ais.SetColor(color)
                    self.viewport.context.Redisplay(new_ais, False)
                    self.viewport._apply_black_edges(new_ais)
                    # Also update stored color in the tracking dict.
                    info = self.viewport._ais_shape_to_node.get(id(new_ais))
                    if info is not None:
                        info["color_rgb"] = original_color

        self.viewport.context.UpdateCurrentViewer()
        self.viewport.update()

    def _on_import_clicked(self):
        """Import a STEP file and add it to the current assembly."""
        # If the imported node is labeled '/', merge its children into the
        # current root instead of adding the '/' node as a child.
        if self._assembly is None:
            return

        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from pathlib import Path

        in_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import STEP File",
            str(Path(self.step_path).parent) if self.step_path else str(Path.home()),
            "STEP Files (*.step *.stp);;All Files (*)"
        )
        if not in_path:
            return  # user cancelled

        try:
            import sys, os
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
            from step_assembly_poc import load_assembly, add_node

            print(f"Importing {in_path} ...")
            new_node = load_assembly(in_path)
            print(f"Loaded '{new_node.label}' with "
                  f"{len(list(new_node.descendants))} descendants.")

            # Add under the active assembly (or root if none set).
            target = self.tree.get_target_node()

            # If imported file has a '/' root wrapper, unwrap it
            if new_node.label == '/':
                children_to_add = list(new_node.children)
                for child in children_to_add:
                    add_node(child, target)
                    self.viewport.display_subtree(child, f"/{child.label}")
                    self.tree.add_node_to_tree(child, parent_node=target)
                    print(f"Added '{child.label}' under '{target.label}'.")
            else:
                add_node(new_node, target)
                self.viewport.display_subtree(new_node, f"/{new_node.label}")
                self.tree.add_node_to_tree(new_node, parent_node=target)
                print(f"Added '{new_node.label}' under '{target.label}'.")

            print(f"Import complete: '{new_node.label}' is now in the tree.")
            print("Drag it to re-parent, then use Position to place it.")

        except Exception as e:
            import traceback
            print(f"Import failed: {e}")
            traceback.print_exc()
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                self,
                "Import failed",
                f"Could not import STEP file:\n{e}"
            )

    def _on_export_clicked(self):
        """Export the current assembly to a STEP file."""
        if self._assembly is None:
            return

        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog, QMessageBox

        # Default to a sensible filename alongside the input file.
        if self.step_path:
            input_path = Path(self.step_path)
            default_out = str(input_path.with_name(
                input_path.stem + "_exported" + input_path.suffix
        ))
        else:
            default_out = str(Path.home() / "assembly_exported.step")

        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Assembly as STEP",
            default_out,
            "STEP Files (*.step *.stp);;All Files (*)"
        )
        if not out_path:
            return  # user cancelled

        try:
            import sys, os
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
            from step_export_fix import export_step
            # Export only the real content under root, not the '/' wrapper itself.

            children = list(self._assembly.children)
            if len(children) == 1:
                # Single child -- export it directly (most common case)
                export_step(children[0], out_path)
            else:
                # Multiple children -- export the whole root wrapper
                export_step(self._assembly, out_path)
            
            print(f"Exported to {out_path}")
            QMessageBox.information(
                self,
                "Export complete",
                f"Assembly exported to:\n{out_path}"
            )
        except Exception as e:
            print(f"Export failed: {e}")
            QMessageBox.critical(
                self,
                "Export failed",
                f"Could not export assembly:\n{e}"
            )

    def _on_positioning_done(self):
        """Positioning dialog closed -- hide it."""
        self._position_dialog.hide()

    def _on_active_assembly_changed(self, node):
        """Active assembly was changed via RMB menu -- update status bar hint."""
        if node is not None:
            print(f"Active assembly: '{node.label}' "
                  f"-- new parts/imports will land here.")
        else:
            print("Active assembly cleared -- new parts/imports go to root.")

    def _on_active_part_changed(self, node):
        """
        Active part changed -- update orange overlay and tree highlight.
        Also activates EDGE+VERTEX selection on the new active part
        and deactivates them on the previous one. This gives edge hover
        highlighting only where it's needed (the part being worked on)
        without activating it on all 18+ parts which crashes MoveTo().
        """
        from PySide6.QtGui import QColor, QBrush
        from OCP.Quantity import Quantity_Color, Quantity_TypeOfColor
        from OCP.AIS import AIS_Shape, AIS_DisplayMode
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy

        orange = Quantity_Color(1.0, 0.55, 0.0,
                                Quantity_TypeOfColor.Quantity_TOC_RGB)
        edge_mode   = AIS_Shape.SelectionMode_s(TopAbs_EDGE)
        vertex_mode = AIS_Shape.SelectionMode_s(TopAbs_VERTEX)

        # --- Deactivate edge/vertex on previous active part ---
        prev_active = getattr(self, '_active_part_node', None)
        if prev_active is not None:
            prev_ais = self.viewport._node_id_to_ais_shape.get(id(prev_active))
            if prev_ais is not None:
                try:
                    self.viewport.context.Deactivate(prev_ais, edge_mode)
                    self.viewport.context.Deactivate(prev_ais, vertex_mode)
                except Exception:
                    pass
        self._active_part_node = node

        # --- Clear previous overlay and tree highlight ---
        prev_overlay = getattr(self, '_active_part_overlay_ais', None)
        if prev_overlay is not None:
            try:
                self.viewport.context.Deactivate(prev_overlay)
                self.viewport.context.Remove(prev_overlay, False)
            except Exception:
                pass
            self._active_part_overlay_ais = None

        prev_item = getattr(self, '_active_part_tree_item', None)
        if prev_item is not None:
            prev_item.setBackground(0, QBrush())
            self._active_part_tree_item = None

        if node is not None:
            # --- Tree highlight ---
            for item_id, n in self.tree._item_to_node.items():
                if n is node:
                    item = self.tree._find_item_by_id(item_id)
                    if item is not None:
                        item.setBackground(
                            0, QBrush(QColor(255, 140, 0, 120)))
                        self._active_part_tree_item = item
                    break

            # --- Activate edge/vertex on new active part only ---
            ais = self.viewport._node_id_to_ais_shape.get(id(node))
            if ais is not None:
                try:
                    self.viewport.context.Activate(ais, edge_mode)
                    self.viewport.context.Activate(ais, vertex_mode)
                    self.viewport.context.SetSelectionSensitivity(
                        ais, edge_mode, 6)
                    self.viewport.context.SetSelectionSensitivity(
                        ais, vertex_mode, 8)
                except Exception:
                    pass

                # --- Viewport wireframe overlay ---
                try:
                    shape_copy = BRepBuilderAPI_Copy(ais.Shape()).Shape()
                    overlay = AIS_Shape(shape_copy)
                    overlay.SetDisplayMode(AIS_DisplayMode.AIS_WireFrame)
                    overlay.SetColor(orange)
                    overlay.SetWidth(2.0)
                    self.viewport.context.Display(overlay, False)
                    self.viewport.context.Deactivate(overlay)
                    self.viewport.context.UpdateCurrentViewer()
                    self.viewport.update()
                    self._active_part_overlay_ais = overlay
                    print(f"Active part: '{node.label}' -- orange edges shown.")
                except Exception as e:
                    print(f"Active part overlay failed: {e}")
                    self._active_part_overlay_ais = None
            else:
                self._active_part_overlay_ais = None
                print(f"Active part: '{node.label}' (no AIS shape found).")

    def _suspend_active_part_overlay(self):
        """
        PHASE 2 follow-up fix, DESIGN_BACKLOG item 33: reported as
        "unable to pick center point for circle when part was
        showing." Root cause: the active part has EDGE+VERTEX
        selection activated on its own geometry (for fillet/positioning
        picks) plus a persistent orange wireframe overlay -- both
        useful normally, but directly competing with a sketch's
        intersection markers for clicks (and visually cluttering them)
        when a workplane sits on that same active part. Called by
        SketchToolBar.set_workplane() while a workplane is active;
        paired with _restore_active_part_overlay() below.
        """
        node = getattr(self, '_active_part_node', None)
        if node is None:
            return
        ais = self.viewport._node_id_to_ais_shape.get(id(node))
        overlay = getattr(self, '_active_part_overlay_ais', None)
        ctx = self.viewport.context
        if ais is not None:
            try:
                ctx.Deactivate(ais, AIS_Shape.SelectionMode_s(TopAbs_EDGE))
                ctx.Deactivate(ais, AIS_Shape.SelectionMode_s(TopAbs_VERTEX))
            except Exception as e:
                print(f"[suspend_active_part_overlay] deactivate failed: {e}")
        if overlay is not None:
            try:
                ctx.Erase(overlay, False)
            except Exception as e:
                print(f"[suspend_active_part_overlay] erase failed: {e}")
        # Also clear any stale hover/selection highlight -- matches the
        # user's own manual workaround of clicking empty space, done
        # here automatically instead.
        try:
            ctx.ClearSelected(False)
        except Exception:
            pass
        ctx.UpdateCurrentViewer()

    def _restore_active_part_overlay(self):
        """Undo _suspend_active_part_overlay() -- called by
        SketchToolBar.deactivate() when the sketch session ends."""
        node = getattr(self, '_active_part_node', None)
        if node is None:
            return
        ais = self.viewport._node_id_to_ais_shape.get(id(node))
        overlay = getattr(self, '_active_part_overlay_ais', None)
        ctx = self.viewport.context
        edge_mode = AIS_Shape.SelectionMode_s(TopAbs_EDGE)
        vertex_mode = AIS_Shape.SelectionMode_s(TopAbs_VERTEX)
        if ais is not None:
            try:
                ctx.Activate(ais, edge_mode)
                ctx.Activate(ais, vertex_mode)
                ctx.SetSelectionSensitivity(ais, edge_mode, 6)
                ctx.SetSelectionSensitivity(ais, vertex_mode, 8)
            except Exception as e:
                print(f"[restore_active_part_overlay] activate failed: {e}")
        if overlay is not None:
            try:
                ctx.Display(overlay, False)
            except Exception as e:
                print(f"[restore_active_part_overlay] display failed: {e}")
        ctx.UpdateCurrentViewer()


    def _on_part_cut(self, node, new_shape):
        """
        Cut/Mill completed -- replace the part's geometry in the viewport.
        Cut intentionally does NOT propagate to shared instances -- cutting
        into one instance leaves others unchanged.
        """
        # Erase overlay FIRST -- it refs the old shape, must go before Remove()
        overlay = getattr(self, '_active_part_overlay_ais', None)
        if overlay is not None:
            try:
                self.viewport.context.Deactivate(overlay)
                self.viewport.context.Remove(overlay, False)
            except Exception:
                pass
            self._active_part_overlay_ais = None

        self._apply_shape_to_node(node, new_shape, operation="cut")

    def _on_node_delete_requested(self, node):
        """
        Delete a node: erase its geometry from the viewport, remove it
        from the assembly data structure, then remove its row from the tree.
        Handles both leaf parts (have AIS_Shapes) and assembly containers
        (whose leaves must each be erased individually).
        """
        if self._assembly is None:
            return

        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
        from step_assembly_poc import remove_node

        # Clear OCCT selection first -- erasing a selected shape without
        # clearing selection first causes a segfault in OCCT's context.
        self.viewport.context.ClearSelected(False)

        # Erase all leaf AIS_Shapes under this node from the viewport.
        # Use Remove() not Erase() -- Erase only hides visually but leaves
        # the shape in OCCT's selection structures, breaking MoveTo().
        leaves = [node] if not node.children else \
            [n for n in node.descendants if not n.children]
        for leaf in leaves:
            ais = self.viewport._node_id_to_ais_shape.get(id(leaf))
            if ais is not None:
                self.viewport.context.Deactivate(ais)
                self.viewport.context.Remove(ais, False)
                self.viewport._ais_shapes = [
                    s for s in self.viewport._ais_shapes if s is not ais
                ]
                self.viewport._ais_shape_to_node.pop(id(ais), None)
                self.viewport._node_id_to_ais_shape.pop(id(leaf), None)

        self.viewport.context.UpdateCurrentViewer()
        self.viewport.update()

        # Remove from assembly data structure
        removed = remove_node(node)
        if not removed:
            print(f"WARNING: remove_node('{node.label}') returned False -- "
                  f"may already be detached.")

        # Remove from tree widget (also clears active if needed)
        self.tree.remove_node_from_tree(node)
        print(f"Deleted '{node.label}' from assembly.")

    def _on_sub_assembly_created(self, new_assy, parent_node):
        """
        A new empty sub-assembly was created via the tree RMB menu.
        Nothing to display in the viewport (it has no geometry yet),
        but we log it for clarity.
        """
        print(f"Sub-assembly '{new_assy.label}' created under "
              f"'{parent_node.label}'. Add parts to it via "
              f"'Set Active Assembly' then Create Part or Import STEP.")


def main():
    step_path = sys.argv[1] if len(sys.argv) >= 2 else None
    app = QApplication(sys.argv)
    window = MainWindow(step_path)
    window.show()
    QTimer.singleShot(0, window.load)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
