
---

## 27. To-Do: Tractable Near-Term Work Items

### Copy Part/Assembly
Add a "Duplicate" option to the RMB context menu on tree nodes.
Should create a new independent node (not a shared instance) with
the same geometry, placed at the same location. User can then
reposition the copy independently.

### Workplane Enhancements
- **More workplane creation modes:** currently only face-pick is
  supported. Add: at-origin (global XY/XZ/YZ), offset from existing
  face, through three points, through an edge.
- **Project part edges as clines:** pick an edge on a part and
  project it onto the active workplane as a construction line.
  Essential for referencing existing geometry when sketching.
- **Clickable points on sketch tools:** full 2D CAD snap behavior
  as in PyurCAD -- snap to endpoints, midpoints, intersections,
  centers. Currently only cline/ccirc intersection snap is supported.

### Fix: Align Axis -- missing axial and radial positioning steps
The Align Axis section in PositionDialog only aligns the axis
direction (4 DOF). It leaves two DOFs unconstrained:
  - Axial position (slide along the axis)
  - Radial/angular position (spin around the axis)
After aligning the axis, the user needs two more steps to fully
constrain the part. Currently these are missing.

### Fix: Dynamic Move -- only active part moves, not whole assembly
When using the Dynamic (AIS Manipulator) section to move a
sub-assembly (e.g. nut_bolt_assembly), only the active PART (bolt)
moves visually during the drag. Clicking Done shows the whole
assembly was moved correctly in the data model, but the display
during dragging is misleading and confusing.
Root cause likely: the AIS Manipulator is attached to the active
part's AIS_Shape, not to all leaf shapes of the sub-assembly.

### Fix: Mate/Align fails after Dynamic Move mis-alignment
After using Dynamic Move to mis-align a sub-assembly:
  - Picking certain faces (e.g. underside of bolt head) is not
    registered by the position dialog pick handler.
  - The face pick is silently ignored, leaving the dialog waiting
    for pick 1 indefinitely.
Root cause likely: after a Dynamic move the AIS shapes are in a
state where certain faces are occluded or their AIS context state
differs from what resolve_pick() expects.

### Rename: cad1 -> Basicad
Change the application name from "cad1" to "Basicad". Changes
needed:
  - Window title in main_app.py (currently "cad1 -- {step_path}")
  - Project directory name (~/Desktop/cad1 -> ~/Desktop/basicad)
  - Any references in DESIGN_BACKLOG.md and source comments
