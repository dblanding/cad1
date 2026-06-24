"""
assembly_tree_widget.py

The assembly tree widget. Displays a build123d Compound assembly
hierarchy with one row per node, checkboxes for show/hide, drag-and-
drop reparenting, and a right-click context menu.

CONTEXT MENU ACTIONS (RMB on any row):
  - Set Active Assembly  -- makes this node the target for new parts/imports.
                            Active node shown in bold with a ► prefix.
  - New Sub-Assembly     -- adds an empty Compound child under this node.
  - Delete               -- removes this node from the tree and assembly.

ACTIVE ASSEMBLY CONCEPT:
  main_app tracks _active_node. New parts (extrude) and imported STEP
  files are added as children of _active_node. If no active node is
  set, they go under the root assembly. The active node is shown bold
  with a ► prefix in the tree. Setting a new active node clears the
  previous one.

Usage (standalone):
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
    QTreeWidgetItemIterator,
    QAbstractItemView,
    QLabel,
    QMenu,
    QInputDialog,
    QMessageBox,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QAction

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from step_assembly_poc import load_assembly, remove_node, add_node  # noqa: E402


class AssemblyTreeWidget(QTreeWidget):
    """
    Displays a build123d Compound assembly hierarchy, one row per
    node (both sub-assemblies and leaf parts), with:
      - Checkbox per row for show/hide
      - Drag-and-drop reparenting
      - RMB context menu: Set Active Assembly, New Sub-Assembly, Delete
    """

    # Emitted when the user sets a new active assembly via RMB menu.
    # Carries the node that was just made active (or None if cleared).
    active_assembly_changed = Signal(object)

    # Emitted when the active part changes (for Cut/Mill highlight).
    active_part_changed = Signal(object)  # the new active part node, or None

    # Emitted when the user requests deletion of a node via RMB menu.
    # main_app handles the viewport erase; tree widget handles tree removal.
    node_delete_requested = Signal(object)  # the node to delete

    # Emitted when the user creates a new sub-assembly via RMB menu.
    # Carries (new_compound_node, parent_node).
    sub_assembly_created = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["Part / Assembly"])
        self.setColumnCount(1)

        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        # RMB context menu
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        # id(QTreeWidgetItem) -> build123d node
        self._item_to_node = {}
        self._root_assembly = None

        # Currently active assembly node (None = root is implicit target)
        self._active_node = None
        self._active_item = None

        # Currently active part node for Cut/Mill (None = no active part)
        self._active_part = None
        self._active_part_item = None

    # ------------------------------------------------------------------
    # Public API: active assembly
    # ------------------------------------------------------------------

    @property
    def active_node(self):
        """The currently active assembly node, or None (= use root)."""
        return self._active_node

    def set_active_node(self, node, item=None):
        """Make `node` the active assembly. Pass None to clear."""
        if self._active_item is not None:
            self._set_item_active_style(self._active_item, False, "► ")
            self._active_item = None
        self._active_node = node

        if node is not None:
            if item is None:
                for item_id, n in self._item_to_node.items():
                    if n is node:
                        item = self._find_item_by_id(item_id)
                        break
            if item is not None:
                self._set_item_active_style(item, True, "► ")
                self._active_item = item

        self.active_assembly_changed.emit(node)

    def get_target_node(self):
        """Return the node new parts/imports should land under."""
        return self._active_node if self._active_node is not None \
            else self._root_assembly

    # ------------------------------------------------------------------
    # Public API: active part
    # ------------------------------------------------------------------

    @property
    def active_part(self):
        """The currently active part node for Cut/Mill, or None."""
        return self._active_part

    def get_active_part(self):
        """Return the active part node, or None."""
        return self._active_part

    def set_active_part(self, node, item=None):
        """Make `node` the active part. Pass None to clear."""
        if self._active_part_item is not None:
            self._set_item_active_style(self._active_part_item, False, "★ ")
            self._active_part_item = None
        self._active_part = node

        if node is not None:
            if item is None:
                for item_id, n in self._item_to_node.items():
                    if n is node:
                        item = self._find_item_by_id(item_id)
                        break
            if item is not None:
                self._set_item_active_style(item, True, "★ ")
                self._active_part_item = item

        self.active_part_changed.emit(node)

    def _set_item_active_style(self, item, active: bool, prefix: str):
        """Bold + prefix when active; restore normal when cleared."""
        current_text = item.text(0)
        # Strip any existing prefix (► or ★) before applying new one
        for p in ("► ", "★ "):
            if current_text.startswith(p):
                current_text = current_text[2:]
                break
        base_label = current_text

        font = item.font(0)
        font.setBold(active)
        item.setFont(0, font)
        item.setText(0, f"{prefix}{base_label}" if active else base_label)

    # ------------------------------------------------------------------
    # Tree population
    # ------------------------------------------------------------------

    def load_assembly_into_tree(self, assembly):
        self.clear()
        self._item_to_node.clear()
        self._active_node = None
        self._active_item = None
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

    def add_node_to_tree(self, new_node, parent_node=None):
        """
        Add a node (and its subtree) to the tree widget under parent_node.
        If parent_node is None, adds under the root item.
        Returns the new QTreeWidgetItem.
        """
        if parent_node is None:
            parent_item = self.topLevelItem(0)
        else:
            parent_item = None
            for item_id, node in self._item_to_node.items():
                if node is parent_node:
                    parent_item = self._find_item_by_id(item_id)
                    break
            if parent_item is None:
                parent_item = self.topLevelItem(0)

        new_item = self._make_item(new_node)
        parent_item.addChild(new_item)
        self._populate_children(new_item, new_node)
        parent_item.setExpanded(True)
        new_item.setExpanded(True)
        return new_item

    def remove_node_from_tree(self, node):
        """
        Remove a node's row from the tree widget (does NOT touch the
        assembly data structure -- caller must call remove_node() first).
        Also clears active status if the removed node was active.
        """
        if node is self._active_node:
            self.set_active_node(None)
        if node is self._active_part:
            self.set_active_part(None)

        for item_id, n in list(self._item_to_node.items()):
            if n is node:
                item = self._find_item_by_id(item_id)
                if item is not None:
                    parent = item.parent()
                    if parent is not None:
                        parent.removeChild(item)
                    else:
                        idx = self.indexOfTopLevelItem(item)
                        if idx >= 0:
                            self.takeTopLevelItem(idx)
                    # Clean up all descendants from _item_to_node
                    self._remove_item_from_map(item)
                break

    def _remove_item_from_map(self, item):
        """Recursively remove item and all its children from _item_to_node."""
        self._item_to_node.pop(id(item), None)
        for i in range(item.childCount()):
            self._remove_item_from_map(item.child(i))

    def _find_item_by_id(self, item_id):
        iterator = QTreeWidgetItemIterator(self)
        while iterator.value():
            item = iterator.value()
            if id(item) == item_id:
                return item
            iterator += 1
        return None

    # ------------------------------------------------------------------
    # RMB context menu
    # ------------------------------------------------------------------

    def _show_context_menu(self, pos):
        item = self.itemAt(pos)
        if item is None:
            return
        node = self._item_to_node.get(id(item))
        if node is None:
            return

        # Determine if this node is an assembly (has children or is root)
        # vs a leaf part. Leaf parts get a restricted menu.
        from build123d import Compound
        is_assembly = isinstance(node, Compound)
        is_root = (node is self._root_assembly)

        menu = QMenu(self)

        if is_assembly:
            # Set Active Assembly
            act_set_active = QAction("► Set Active Assembly", self)
            act_set_active.triggered.connect(
                lambda: self._on_set_active(node, item))
            menu.addAction(act_set_active)

            # Clear active -- only when this node is currently active
            if node is self._active_node:
                act_clear = QAction("✕ Clear Active Assembly", self)
                act_clear.triggered.connect(lambda: self.set_active_node(None))
                menu.addAction(act_clear)

            menu.addSeparator()

            # New Sub-Assembly
            act_new_assy = QAction("📁 New Sub-Assembly...", self)
            act_new_assy.triggered.connect(
                lambda: self._on_new_sub_assembly(node, item))
            menu.addAction(act_new_assy)

            menu.addSeparator()

        else:
            # Solid (leaf part) -- offer Set Active Part for Cut/Mill
            act_set_part = QAction("⚙ Set Active Part", self)
            act_set_part.triggered.connect(
                lambda: self._on_set_active_part(node, item))
            menu.addAction(act_set_part)

            if node is self._active_part:
                act_clear_part = QAction("✕ Clear Active Part", self)
                act_clear_part.triggered.connect(
                    lambda: self.set_active_part(None))
                menu.addAction(act_clear_part)

            menu.addSeparator()

        # Delete -- not available on root
        act_delete = QAction("🗑 Delete", self)
        act_delete.setEnabled(not is_root)
        act_delete.triggered.connect(lambda: self._on_delete(node))
        menu.addAction(act_delete)

        menu.exec(self.viewport().mapToGlobal(pos))

    def _on_set_active(self, node, item):
        self.set_active_node(node, item=item)
        print(f"Active assembly set to: {node.label!r}")

    def _on_set_active_part(self, node, item):
        self.set_active_part(node, item=item)
        print(f"Active part set to: {node.label!r}")

    def _on_new_sub_assembly(self, parent_node, parent_item):
        """Create a new empty Compound sub-assembly under parent_node."""
        from build123d import Compound
        from OCP.TopoDS import TopoDS_Compound
        from OCP.BRep import BRep_Builder

        name, ok = QInputDialog.getText(
            self, "New Sub-Assembly", "Assembly name:", text="assembly"
        )
        if not ok or not name.strip():
            return
        name = name.strip()

        # build123d requires every Compound to have a valid OCC shape.
        # An empty TopoDS_Compound (built via BRep_Builder) is the
        # smallest valid shell -- no geometry, but _wrapped is not None.
        builder = BRep_Builder()
        occ_compound = TopoDS_Compound()
        builder.MakeCompound(occ_compound)

        new_assy = Compound(label=name)
        new_assy._wrapped = occ_compound
        new_assy.label = name  # set explicitly -- Compound() may not store it

        add_node(new_assy, parent_node)

        new_item = self._make_item(new_assy)
        parent_item.addChild(new_item)
        parent_item.setExpanded(True)

        print(f"Created sub-assembly '{name}' under '{parent_node.label}'")
        self.sub_assembly_created.emit(new_assy, parent_node)

    def _on_delete(self, node):
        """Delete a node from tree + assembly data after confirmation."""
        name = node.label or "<unnamed>"
        n_desc = len(list(node.descendants)) if node.children else 0
        msg = f"Delete '{name}'"
        if n_desc:
            msg += f" and its {n_desc} descendant(s)"
        msg += "?\n\nThis cannot be undone."

        reply = QMessageBox.question(
            self, "Confirm Delete", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Signal main_app to erase from viewport first
        self.node_delete_requested.emit(node)

    # ------------------------------------------------------------------
    # Drag-and-drop reparenting
    # ------------------------------------------------------------------

    def dropEvent(self, event):
        dragged_item = self.currentItem()
        dragged_node = self._item_to_node.get(id(dragged_item)) if dragged_item else None
        target_item = self.itemAt(event.position().toPoint())
        target_node = self._item_to_node.get(id(target_item)) if target_item else None

        super().dropEvent(event)

        if dragged_node is None or target_node is None or dragged_node is target_node:
            return
        if dragged_node is self._root_assembly:
            print("Refusing to reparent the assembly root itself.")
            return

        removed = remove_node(dragged_node)
        if not removed:
            print(f"WARNING: could not remove {dragged_node.label!r} from its parent.")
            return

        add_node(dragged_node, target_node)
        print(f"Reparented '{dragged_node.label}' under '{target_node.label}'")


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
    hint = QLabel("RMB on any row for context menu.")
    hint.setWordWrap(True)
    layout.addWidget(hint)
    tree = AssemblyTreeWidget(window)
    layout.addWidget(tree)
    assembly = load_assembly(step_path)
    tree.load_assembly_into_tree(assembly)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
