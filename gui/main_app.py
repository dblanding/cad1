"""
main_app.py

The merge: AssemblyTreeWidget (proven standalone in
assembly_tree_widget.py) docked alongside OcctViewportWidget (proven
standalone in assembly_viewer.py), both showing the SAME loaded
assembly, wired together via Qt signals so:

    1. Checkbox in the tree -> show/hide that part in the 3D view.
    2. Click a part in the 3D view -> that row gets selected/
       highlighted in the tree.
    3. Click a row in the tree -> that part gets highlighted in the
       3D view.

DESIGN CHOICE: rather than editing OcctViewportWidget or
AssemblyTreeWidget in place, this file SUBCLASSES/extends behavior at
the integration points only (a new Qt signal on the viewport, a couple
of new methods), so both proven standalone scripts
(assembly_viewer.py, assembly_tree_widget.py) stay untouched and
still independently runnable/debuggable if something about the
INTEGRATION breaks but the pieces individually still work -- same
"isolate one variable" discipline as the rest of this project.

Usage:
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
)
from PySide6.QtCore import Qt, Signal, QTimer

from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX
from OCP.Quantity import Quantity_Color, Quantity_TypeOfColor
from OCP.AIS import AIS_Shape

sys.path.insert(0, os.path.dirname(__file__))
from assembly_viewer import OcctViewportWidget  # noqa: E402
from assembly_tree_widget import AssemblyTreeWidget  # noqa: E402
from position_dialog import PositionDialog  # noqa: E402


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

    def _display_leaf(self, node, path, palette_index):
        # Call the PROVEN base implementation first (unchanged --
        # creates the AIS_Shape, sets color/display mode, activates
        # selection, displays it, and populates
        # self._ais_shape_to_node). Then just ALSO record the reverse
        # mapping we need for set_part_visible()/highlight_node().
        super()._display_leaf(node, path, palette_index)
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
        edge_mode = AIS_Shape.SelectionMode_s(TopAbs_EDGE)
        self.context.Activate(ais_shape, edge_mode)

        # Also activate VERTEX-level picking -- needed for pose.py's
        # "vertex" PointRef kind and for Mate/Align operations that
        # target a corner point directly. Same SelectionMode()
        # translation pattern as above.
        vertex_mode = AIS_Shape.SelectionMode_s(TopAbs_VERTEX)
        self.context.Activate(ais_shape, vertex_mode)

        # THE ACTUAL FIX for "edges never get picked, only faces":
        # OCCT's default pixel tolerance for MoveTo()/Select() picking
        # is just 2 PIXELS (confirmed directly from OCCT's own class
        # reference docs). A face covers most of the visible surface
        # area of a solid, so it's almost always under the cursor --
        # but an edge (or especially a vertex -- a single POINT) has
        # essentially zero area, so the cursor has to land within ~2px
        # of the actual geometry to register at all. This is NOT a
        # selection-priority problem (multiple modes ARE active,
        # confirmed) -- it's specifically that the hit tolerance was
        # too tight to realistically land a precise pick by eye.
        # Widening it fixes this directly, per OCCT's own documented
        # SetSelectionSensitivity(object, mode, new_sensitivity) call.
        # NOTE: also fixed here -- this previously passed raw
        # TopAbs_EDGE as the mode argument, the SAME bug as the
        # Activate() calls above; now uses the correct translated
        # mode value for both edge and vertex.
        try:
            self.context.SetSelectionSensitivity(ais_shape, edge_mode, 6)
            self.context.SetSelectionSensitivity(ais_shape, vertex_mode, 8)
        except Exception as e:
            print(f"(could not widen edge/vertex selection sensitivity, "
                  f"precise picks may be hard to land: {e})")


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
            # Display in shaded mode explicitly (mode 1 = AIS_Shaded)
            # to avoid re-adding wireframe mode 0.
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
        self.context.ClearSelected(True)
        self.context.AddOrRemoveSelected(ais_shape, True)
        self.view.FitAll()  # keep the part in view; comment out if too aggressive
        self.update()


class MainWindow(QWidget):
    def __init__(self, step_path):
        super().__init__()
        self.setWindowTitle(f"CAD Assistant (ours) -- {step_path}")
        self.resize(1400, 800)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        outer_layout.addWidget(splitter)

        from PySide6.QtWidgets import QMainWindow, QDockWidget
        # MainWindow needs to be a QMainWindow to support dock widgets --
        # but we're currently a QWidget. Promote to QMainWindow by
        # re-parenting the existing layout into a central widget.
        # (This is a one-time change; all existing child widgets stay
        # the same, just hosted differently.)
        # Actually -- simplest approach: float the PositionDialog as a
        # regular QDialog window rather than a true dock, since we're
        # already a QWidget and converting to QMainWindow mid-project
        # would touch too much at once. The PositionDialog is already
        # a QDockWidget but can float standalone just fine.

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

        splitter.addWidget(tree_panel)

        # --- Right: the 3D viewport ----------------------------------
        self.viewport = SyncedViewportWidget(self)
        splitter.addWidget(self.viewport)

        splitter.setSizes([350, 1050])

        # --- Position dialog (floating dock) -------------------------
        self._position_dialog = PositionDialog(self)
        self._position_dialog.hide()
        self._position_dialog.request_redisplay.connect(self._on_redisplay_after_move)
        self._position_dialog.positioning_done.connect(self._on_positioning_done)

        # --- Wire the standard sync signals --------------------------
        self.viewport.part_selected.connect(self._on_part_selected_in_viewport)
        self.viewport.geometry_picked.connect(self._on_geometry_picked)
        self.tree.itemClicked.connect(self._on_tree_item_clicked)
        self.tree.itemChanged.connect(self._on_tree_item_changed)
        # Enable Position button when a tree row is selected.
        self.tree.itemSelectionChanged.connect(self._on_tree_selection_changed)

        self.step_path = step_path
        self._assembly = None  # set by load()

    def load(self):
        print(f"Loading {self.step_path} ...")
        self._assembly = self.viewport.load_and_display_assembly(self.step_path)
        self.tree.load_assembly_into_tree(self._assembly)
        print("Loaded into both tree and viewport.")

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
        self._position_dialog.setFloating(True)
        self._position_dialog.show()
        self._position_dialog.raise_()

    def _on_geometry_picked(self, raw_shape, shape_type):
        """
        Route a viewport pick to the position dialog when in positioning
        mode. Normal tree-sync behavior (part_selected signal) still
        fires regardless -- this is additive, not a replacement.
        """
        if self._position_dialog.isVisible() and \
                self._position_dialog.is_in_positioning_mode():
            self._position_dialog.receive_pick(raw_shape, shape_type)

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
                    self.viewport.context.Redisplay(new_ais, True)
                    # Also update stored color in the tracking dict.
                    info = self.viewport._ais_shape_to_node.get(id(new_ais))
                    if info is not None:
                        info["color_rgb"] = original_color

        self.viewport.context.UpdateCurrentViewer()
        self.viewport.update()

    def _on_positioning_done(self):
        """Positioning dialog closed -- hide it."""
        self._position_dialog.hide()


def main():
    if len(sys.argv) < 2:
        print("Usage: main_app.py <path/to/assembly.step>")
        sys.exit(1)

    step_path = sys.argv[1]

    app = QApplication(sys.argv)
    window = MainWindow(step_path)
    window.show()

    # Same deferred-load pattern proven in assembly_viewer.py: queue
    # to run after the current event loop iteration, so the window's
    # real size/native handles exist before OCCT touches them.
    QTimer.singleShot(0, window.load)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
