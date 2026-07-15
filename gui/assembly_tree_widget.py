"""
assembly_tree_widget.py

THE ASSEMBLY TREE -- left panel of the cad1 application.

Displays a build123d Compound assembly hierarchy as a QTreeWidget.
Each node in the tree is one row; leaf nodes are solid parts, interior
nodes are sub-assemblies (Compound containers).

FEATURES:
  - Checkboxes for show/hide each part in the 3D viewport.
  - Drag-and-drop reparenting: drag any node onto a new parent.
  - Right-click context menu on any row:
      Set Active Assembly  -- new parts and imports go under this node
      Set Active Part      -- fillet/shell/cut operate on this node
      New Sub-Assembly     -- adds an empty Compound child
      Rename               -- QInputDialog to rename the node
      Delete               -- removes the node from tree and assembly
  - Active assembly shown bold with >> prefix.
  - Active part shown with * prefix (orange background in main_app).

SIGNALS EMITTED (connected to MainWindow in main_app.py):
  node_selected(node)              -- user clicked a row (tree-to-viewport sync)
  visibility_changed(node, bool)   -- checkbox toggled
  node_reparented(node, new_parent)-- drag-and-drop completed
  active_assembly_changed(node)    -- Set Active Assembly chosen
  active_part_changed(node)        -- Set Active Part chosen
  node_deleted(node)               -- Delete chosen from context menu
  new_sub_assembly_requested(node) -- New Sub-Assembly chosen

STANDALONE USAGE:
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

    # PHASE 3 (DESIGN_BACKLOG item 33): persistent workplanes, listed
    # in their own "WP" section above the assembly tree, KodaCAD-style.
    # uid is a string like "wp1", assigned by main_app.py.
    workplane_visibility_changed = Signal(str, bool)   # uid, visible
    workplane_set_active_requested = Signal(str)        # uid
    workplane_clear_active_requested = Signal()
    workplane_delete_requested = Signal(str)             # uid

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

        # PHASE 3 (DESIGN_BACKLOG item 33): persistent workplanes, in
        # their own "WP" top-level section, above the assembly tree
        # (KodaCAD-style). Kept entirely separate from _item_to_node/
        # _make_item's build123d-node machinery -- workplanes are
        # plain Python objects (src/workplane.py's WorkPlane), not
        # build123d nodes, and have their own show/hide + RMB
        # Set-Active/Delete semantics rather than reusing "Set Active
        # Assembly"/"Set Active Part".
        self._wp_root_item = QTreeWidgetItem(["WP"])
        self._wp_root_item.setFlags(
            self._wp_root_item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
        self.addTopLevelItem(self._wp_root_item)
        self._wp_root_item.setExpanded(True)
        self._item_to_wp_uid = {}   # id(QTreeWidgetItem) -> wp uid (str)
        self._wp_uid_to_item = {}   # wp uid -> QTreeWidgetItem
        self._active_wp_uid = None
        self._active_wp_item = None
        # Set by load_assembly_into_tree() -- the fallback parent for
        # add_node_to_tree(parent_node=None). Was topLevelItem(0)
        # before the WP section existed at index 0; now tracked
        # explicitly since WP always occupies index 0.
        self._assembly_root_item = None

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
    # Public API: persistent workplanes (PHASE 3, DESIGN_BACKLOG item 33)
    # ------------------------------------------------------------------

    def add_workplane_item(self, uid, label):
        """Add a new row under the WP section. Returns the QTreeWidgetItem."""
        item = QTreeWidgetItem([label])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Checked)
        self._wp_root_item.addChild(item)
        self._item_to_wp_uid[id(item)] = uid
        self._wp_uid_to_item[uid] = item
        return item

    def remove_workplane_item(self, uid):
        """Remove a workplane's row. Clears active status if it was active."""
        item = self._wp_uid_to_item.pop(uid, None)
        if item is None:
            return
        self._item_to_wp_uid.pop(id(item), None)
        self._wp_root_item.removeChild(item)
        if self._active_wp_uid == uid:
            self._active_wp_uid = None
            self._active_wp_item = None

    def set_active_workplane(self, uid):
        """Mark `uid` as the active workplane (bold + ► prefix), matching
        the existing active-assembly/active-part visual convention.
        Pass None to clear."""
        if self._active_wp_item is not None:
            self._set_item_active_style(self._active_wp_item, False, "► ")
            self._active_wp_item = None
        self._active_wp_uid = uid
        if uid is not None:
            item = self._wp_uid_to_item.get(uid)
            if item is not None:
                self._set_item_active_style(item, True, "► ")
                self._active_wp_item = item

    def get_active_workplane_uid(self):
        return self._active_wp_uid

    # ------------------------------------------------------------------
    # Tree population
    # ------------------------------------------------------------------

    def load_assembly_into_tree(self, assembly):
        self.clear()
        self._item_to_node.clear()
        self._active_node = None
        self._active_item = None
        self._root_assembly = assembly

        # Recreate the WP section (PHASE 3, DESIGN_BACKLOG item 33) --
        # self.clear() just wiped it. Only called once at startup
        # (see main_app.py's load()), before any workplanes could
        # exist, so there's nothing to preserve here.
        self._wp_root_item = QTreeWidgetItem(["WP"])
        self._wp_root_item.setFlags(
            self._wp_root_item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
        self.addTopLevelItem(self._wp_root_item)
        self._wp_root_item.setExpanded(True)
        self._item_to_wp_uid = {}
        self._wp_uid_to_item = {}
        self._active_wp_uid = None
        self._active_wp_item = None

        root_item = self._make_item(assembly)
        self.addTopLevelItem(root_item)
        self._assembly_root_item = root_item
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
            parent_item = self._assembly_root_item
        else:
            parent_item = None
            for item_id, node in self._item_to_node.items():
                if node is parent_node:
                    parent_item = self._find_item_by_id(item_id)
                    break
            if parent_item is None:
                parent_item = self._assembly_root_item

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

        if item is self._wp_root_item:
            return  # no menu on the "WP" section header itself

        wp_uid = self._item_to_wp_uid.get(id(item))
        if wp_uid is not None:
            self._show_wp_context_menu(pos, wp_uid)
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
        act_rename = QAction("✏ Rename...", self)
        act_rename.triggered.connect(lambda: self._on_rename(item, node))
        menu.addAction(act_rename)

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

    def _show_wp_context_menu(self, pos, uid):
        """RMB menu for a row in the WP section -- Set Active / Delete,
        deliberately NOT reusing Set Active Assembly/Part's semantics."""
        menu = QMenu(self)

        act_set_active = QAction("► Set Active", self)
        act_set_active.triggered.connect(
            lambda: self.workplane_set_active_requested.emit(uid))
        menu.addAction(act_set_active)

        if uid == self._active_wp_uid:
            act_clear = QAction("✕ Clear Active", self)
            act_clear.triggered.connect(
                lambda: self.workplane_clear_active_requested.emit())
            menu.addAction(act_clear)

        menu.addSeparator()

        act_delete = QAction("🗑 Delete", self)
        act_delete.triggered.connect(
            lambda: self.workplane_delete_requested.emit(uid))
        menu.addAction(act_delete)

        menu.exec(self.viewport().mapToGlobal(pos))

    def _on_rename(self, item, node):
        """Prompt for a new name and update both the tree item and node label."""
        from PySide6.QtWidgets import QInputDialog

        # Strip any active/prefix decorations to get the base label
        current = item.text(0)
        for prefix in ("► ", "★ "):
            if current.startswith(prefix):
                current = current[2:]
                break

        new_name, ok = QInputDialog.getText(
            self, "Rename", "New name:", text=current)
        if not ok or not new_name.strip():
            return

        new_name = new_name.strip()
        node.label = new_name

        # Restore any active prefix
        prefix = ""
        if item.text(0).startswith("► "):
            prefix = "► "
        elif item.text(0).startswith("★ "):
            prefix = "★ "
        item.setText(0, f"{prefix}{new_name}")

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
