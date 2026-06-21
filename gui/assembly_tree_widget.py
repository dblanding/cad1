"""
assembly_tree_widget.py

The assembly tree widget, built and tested STANDALONE first -- no
viewport, no picking, no 3D at all -- same "isolate one variable"
discipline as every other piece of this project. Once this works
correctly on its own (populates from a real STEP file, checkboxes
toggle, drag-and-drop reparents intuitively), it gets wired into
assembly_viewer.py as a dock alongside the 3D view.

WHAT THIS PROVES, ON ITS OWN:
    1. The tree populates correctly from load_assembly()'s Compound
       hierarchy (reusing the same tree-walk pattern as print_tree()
       in step_assembly_poc.py -- no new traversal logic).
    2. Each row has a checkbox for show/hide (not yet wired to
       anything -- that wiring happens once this merges with the
       viewport, since AIS_Shape display/erase needs a live OCCT
       context that doesn't exist in this standalone test).
    3. Drag-and-drop reparenting works the way Doug described:
       "strictly a hierarchical thing, no relation to physical
       position" -- dropping an item ONTO another makes it a child of
       that item, full stop. No re-posing, no geometric side effects.
       Qt's QTreeWidget does this by default with
       setDragDropMode(InternalMove) -- confirmed via Qt's own docs/
       forums: "By default, QTreeWidget reparents items whenever you
       drag them: the dragged item becomes a child of the item on
       which it was dropped." We are NOT fighting that default or
       customizing it to do sibling-reordering instead -- the default
       behavior IS the desired behavior here.
    4. After a drop, the tree's NEW structure gets synced back into
       the actual assembly data using remove_node()/add_node() --
       the IDENTITY-based primitives (operate on the exact Python
       object dragged, not a label match) -- so dragging one specific
       "nut" out of six identically-labeled siblings moves exactly
       that one, never a different occurrence that happens to share
       its name. (An earlier version of this file used the label-
       based remove_part()/add_part(), which is ambiguous on files
       like as1-oc-214.stp that have multiple parts sharing a label --
       fixed before this was exercised against that ambiguity in
       practice.)

Usage:
    uv run gui/assembly_tree_widget.py step/as1-oc-214.stp
"""

import sys
import os

from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QTreeWidget,
    QTreeWidgetItem,
    QAbstractItemView,
    QLabel,
)
from PySide6.QtCore import Qt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from step_assembly_poc import load_assembly, remove_node, add_node  # noqa: E402


class AssemblyTreeWidget(QTreeWidget):
    """
    Displays a build123d Compound assembly hierarchy, one row per
    node (both sub-assemblies and leaf parts), with a checkbox per
    row for show/hide and built-in drag-and-drop reparenting.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["Part / Assembly"])
        self.setColumnCount(1)

        # Drag-and-drop reparenting, Qt's default InternalMove
        # behavior: dropping an item ONTO another makes it that
        # item's child. This matches the design decision directly --
        # purely hierarchical, no geometric side effects.
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        # Map from QTreeWidgetItem -> the build123d tree node it
        # represents, so a drop event (which only knows about widget
        # ROWS) can be translated into an actual remove_part()/
        # add_part() pair on the REAL assembly data structure.
        self._item_to_node = {}
        self._root_assembly = None  # set by load_assembly_into_tree()

    def load_assembly_into_tree(self, assembly):
        """
        Populate the tree from a build123d Compound assembly (the
        SAME object load_assembly() returns -- no new parsing, no new
        traversal logic, just a new consumer of the existing tree
        structure that's already been validated extensively against
        real STEP files in step_assembly_poc.py and assembly_viewer.py).
        """
        self.clear()
        self._item_to_node.clear()
        self._root_assembly = assembly

        root_item = self._make_item(assembly)
        self.addTopLevelItem(root_item)
        self._populate_children(root_item, assembly)
        self.expandAll()

    def _make_item(self, node):
        label = node.label or "<unnamed>"
        item = QTreeWidgetItem([label])
        item.setFlags(
            item.flags()
            | Qt.ItemFlag.ItemIsUserCheckable
            | Qt.ItemFlag.ItemIsDragEnabled
            | Qt.ItemFlag.ItemIsDropEnabled
        )
        item.setCheckState(0, Qt.CheckState.Checked)
        self._item_to_node[id(item)] = node
        return item

    def _populate_children(self, parent_item, parent_node):
        for child_node in parent_node.children:
            child_item = self._make_item(child_node)
            parent_item.addChild(child_item)
            self._populate_children(child_item, child_node)

    def dropEvent(self, event):
        """
        Let Qt perform its default InternalMove reparenting on the
        WIDGET rows first, then sync that change back into the REAL
        assembly data structure using remove_node()/add_node() -- the
        IDENTITY-based primitives, which operate on the exact node
        object dragged rather than matching by label. This matters
        concretely on files like as1-oc-214.stp, which has 6 parts
        named "nut" and 3 named "bolt": label-based matching would
        risk moving the WRONG occurrence (the first one found,
        depth-first) even when the tree widget visually shows the
        correct one being dragged. Identity-based matching has no
        such ambiguity -- it always acts on the literal node you
        dragged, never a same-named sibling.
        """
        # Identify what's being dragged and where, BEFORE Qt's
        # default handling moves the widget rows around -- we need
        # the pre-drop state to know the dragged node's CURRENT
        # parent in our own node map.
        dragged_item = self.currentItem()
        dragged_node = self._item_to_node.get(id(dragged_item)) if dragged_item else None

        target_item = self.itemAt(event.position().toPoint())
        target_node = self._item_to_node.get(id(target_item)) if target_item else None

        super().dropEvent(event)  # let Qt move the widget rows

        if dragged_node is None or target_node is None or dragged_node is target_node:
            return  # nothing meaningful to sync (e.g. dropped on empty space)

        if dragged_node is self._root_assembly:
            print("Refusing to reparent the assembly root itself.")
            return

        # Sync the REAL data: remove the dragged node from its old
        # parent, add it under the new one -- by OBJECT IDENTITY, not
        # label, so this is correct even when dragged_node shares its
        # label with other siblings elsewhere in the tree.
        removed = remove_node(dragged_node)
        if not removed:
            print(f"WARNING: could not remove {dragged_node.label!r} "
                  f"(path-unknown) from its old parent -- it may already "
                  f"be detached, or have no parent (e.g. it's the tree "
                  f"root). Tree widget and assembly data may now be out "
                  f"of sync.")
            return

        add_node(dragged_node, target_node)
        print(f"Reparented {dragged_node.label!r} under {target_node.label!r} "
              f"(by object identity -- unambiguous even with repeated labels).")


def main():
    if len(sys.argv) < 2:
        print("Usage: assembly_tree_widget.py <path/to/assembly.step>")
        sys.exit(1)

    step_path = sys.argv[1]

    app = QApplication(sys.argv)

    window = QWidget()
    window.setWindowTitle(f"Assembly tree -- {step_path}")
    window.resize(400, 600)

    layout = QVBoxLayout(window)

    hint = QLabel(
        "Drag a part/assembly onto another row to reparent it.\n"
        "Checkboxes are NOT wired to anything yet (standalone test --\n"
        "show/hide requires the live viewport, added in a later step)."
    )
    hint.setWordWrap(True)
    layout.addWidget(hint)

    tree = AssemblyTreeWidget(window)
    layout.addWidget(tree)

    print(f"Loading {step_path} ...")
    assembly = load_assembly(step_path)
    tree.load_assembly_into_tree(assembly)
    print("Loaded. Try dragging rows to reparent them.")

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
