# Design Backlog

Running list of design threads that are open, named, and deliberately
not yet built -- captured here so they don't get lost between
sessions. Add to this as new threads come up; move items to the
relevant area's own doc (or into actual code) once they're resolved.

---

## 1. Position / Mate-Align workflow (HP/CoCreate pattern)

**Status: CORE WORKFLOW COMPLETE.** The full 3-2-1 Mate/Align
sequence is working correctly with true purity of motion, confirmed
via real testing on as1-oc-214.stp (L-bracket positioned onto plate
in three steps: Mate → Align → Align).

**What's working (confirmed via real testing):**
- Position dialog (`gui/position_dialog.py`) fully functional
- Mate, Align, Align Axis, Dynamic Move all wired up and working
- **Full 3-2-1 sequence confirmed:** Mate (3 DOF) → Align (2 DOF) →
  Align (1 DOF), each step consuming only its intended DOF without
  disturbing previously-constrained DOF ("purity of motion")
- **Purity of motion fix:** `compute_mate_move`/`compute_align_move`
  decompose into: (1) pure rotation to align normals, (2) translation
  along normal only to close plane gap. No in-plane movement in step 1.
  Subsequent steps operate only in remaining free DOF.
- **Coordinate frame fix:** world-space moves converted to parent-local
  frame before applying via `_world_move_to_local()`. Required because
  pick coordinates are always world-space but `Shape.move()` operates
  in the parent node's local frame. Without this, nested nodes (e.g.
  l-bracket inside l-bracket-assembly) moved incorrectly while
  top-level nodes (whose parent is at the origin) worked fine.
- Cylindrical face body clicks automatically extract cylinder axis
  (via boundary edge circle-fit, handling BSPLINE-encoded cylinders)
- Reverse button: flips direction of last applied step and re-applies
- Back button: undoes one step correctly
- Face/edge/vertex picking restored correctly after show/hide
- Colors preserved across moves
- Moving node selected via TREE (supports part or assembly)

**Scope (deliberately narrow):** implementing just 2 of CoCreate's
positioning techniques, sufficient for the 90% use case (importing
STEP files and positioning them into an assembly):

1. **Dynamic positioning** -- drag a part to get it out of the way
   of overlapping geometry so you can see it clearly before Mate/Align.
2. **Mate/Align** -- precision placement using face/edge/axis picks.

---

### Typical workflow this supports

1. Import a STEP file → part/assembly appears at top level near origin
2. Drag it in the tree to the correct parent assembly (already works)
3. **Dynamic Move** → drag it somewhere visible, away from existing
   geometry (new)
4. **Mate/Align** → precision placement relative to the assembly (new)

---

### Mate/Align: design confirmed and implemented

**Source:** PTC Creo Elements/Direct Modeling Express documentation,
read directly from the attached PDFs ("Mate or align parts and
assemblies" + "Position a part, assembly, or workplane set").

**Each step commits immediately.** "Back" undoes one step.

**Constraint types:**
- **Mate**: two faces opposing each other on the same plane (face-to-
  face contact). Constrains 3 DOF: 2 rotational + 1 normal translation.
- **Align**: two faces/elements on the SAME side of a plane (flush).
  Constrains 2 DOF (in-plane translation toward alignment).
- **Align Axis**: aligns the axes of two cylindrical/circular
  elements. Constrains up to 4 DOF at once (both translations
  perpendicular to the axis + 2 rotational). Worth exploring as an
  alternative to Mate+Align for cylindrical features.
- **Parallel**: makes faces/edges parallel without coincident
  placement (not in our minimal scope).
- **Offset**: adds a gap value to Mate or Align (not in minimal
  scope, but trivial to add).

**Key implementation details:**
- Moving node always selected via TREE (part or assembly)
- Pick coordinates are world-space (from OCCT); converted to
  parent-local frame before applying via `_world_move_to_local()`
- `compute_mate_move` / `compute_align_move`: rotate to align normals
  (using same-origin from/to planes for pure rotation), then translate
  along normal only by `gap = (pick2.point - pick1.point).dot(target_z)`
- `compute_align_axis_move`: uses full `compute_move()` since axis
  alignment constrains both orientation AND axis position

---

### Additional things completed

1. ✅ **Dynamic Move** -- implement click-to-translate for rough
   positioning before Mate/Align.
2. ✅ **AIS_Manipulator gizmo** -- the slick drag version, after
   Dynamic Move click-to-translate is working.
3. ✅ **Active Part concept** (CoCreate terminology) -- make the
   currently-selected moving node more visually prominent in the
   dialog and/or viewport so it's always clear what's about to move.

---

### Confirmed sequencing patterns (from real testing)

Two distinct positioning sequences have been confirmed working, each
suited to different geometry:

**Pattern A: 3-2-1 (prismatic/flat features)**
1. Mate two faces → 3 DOF (2 rotational + 1 normal translation)
2. Align two faces → 2 DOF (in-plane translation)
3. Align two faces → 1 DOF (remaining in-plane translation)

Confirmed: L-bracket onto plate using all face picks. Each step
consumed exactly its intended DOF without disturbing previous steps.

**Pattern B: Align Axis + Mate (cylindrical features)**
1. Align Axis (hole-to-hole or shaft-to-hole) → 4 DOF (2 rotational
   + 2 translational perpendicular to axis)
2. Mate (face-to-face along the axis direction) → 1 DOF (translation
   along axis to close the gap)

Confirmed: second L-bracket-assembly positioned using a hole in the
bracket aligned to a hole in the plate, followed by Mate of the
bracket bottom face to the plate top face. More efficient than 3-2-1
for cylindrical features (2 steps vs 3).

**CRITICAL ORDER DEPENDENCY for Pattern B:**
Align Axis MUST come before Mate. If Mate is applied first, the
subsequent Align Axis disturbs the mate (it's consuming overlapping
DOF in a conflicting way -- Align Axis constrains 2 rotational DOF
that the Mate also depends on for its plane orientation). Doing Align
Axis first leaves only 1 translational DOF (along the axis) free,
which Mate then cleanly consumes as a pure translation. The purity
of motion property only holds when constraint steps are applied in
order of decreasing DOF consumed: most-constraining step first.

**Bug found and fixed: unwanted 90-degree spin on Mate/Align.**
Root cause: `Plane(origin=p, z_dir=v)` auto-computes an x_dir based
on v's relationship to world axes. When from_z and target_z differ,
their auto-computed x_dirs differ, and `compute_move` adds a spin to
align them. Surfaced with a real STEP file (raspibot.step) where the
spin was a full 90 degrees, spoiling hole pattern alignment. Fixed via
`_make_rotation_plane()` which explicitly projects from_plane's x_dir
onto the target normal's perpendicular plane, preserving in-plane
orientation. Only visible with certain face orientations -- as1-oc-214
happened to not trigger it.


first, then work downward. Align Axis (4 DOF) before Mate (consumes
remaining 1). For prismatic features, Mate (3 DOF) before Align
(2 DOF) before Align (1 DOF) -- the natural 3-2-1 order already
respects this.



**Scope (deliberately narrow):** implementing just 2 of CoCreate's
positioning techniques, sufficient for the 90% use case (importing
STEP files and positioning them into an assembly):

1. **Dynamic positioning** -- drag a part to get it out of the way
   of overlapping geometry so you can see it clearly before Mate/Align.
2. **Mate/Align** -- precision placement using face/edge/axis picks.

---

### Typical workflow this supports

1. Import a STEP file → part/assembly appears at top level near origin
2. Drag it in the tree to the correct parent assembly (already works)
3. **Dynamic Move** → drag it somewhere visible, away from existing
   geometry (new)
4. **Mate/Align** → precision placement relative to the assembly (new)

---

### Mate/Align: fully designed

**Source:** PTC Creo Elements/Direct Modeling Express documentation,
read directly from the attached PDFs ("Mate or align parts and
assemblies" + "Position a part, assembly, or workplane set").

**The open design question is now answered:** each Mate/Align step
commits **immediately** -- "the parts or assemblies become constrained
by each mate or align step." The part moves after each pick pair, not
batched until a final Apply. "Back" undoes one step; "Clear All"
resets the constraint method but leaves the part at its current
(moved) position. This is simpler to implement than the batched
version would have been.

**Constraint types** (confirming Doug's 3-2-1 characterization):
- **Mate**: two faces opposing each other on the same plane (face-to-
  face contact). Constrains 3 DOF: the normal direction + 2
  rotational.
- **Align**: two faces/elements on the SAME side of a plane (flush).
  Constrains 2 additional DOF (in-plane translation).
- **Align Axis**: aligns the axes of two cylindrical/circular
  elements. Maps directly onto `circle_axis` DirectionRef picks,
  already proven working on real STEP geometry.
- **Parallel**: makes faces/edges parallel without coincident
  placement (not in our minimal scope, but documented for later).
- **Offset**: adds a gap value to Mate or Align (not in minimal
  scope, but trivial to add once the basic case works).

---

### Dynamic positioning: COMPLETE

**`AIS_Manipulator` gizmo implemented and confirmed working.**
All 6 DOF: 3 arrows for translation, 3 rings for rotation. Integrated
into the Position dialog as "Dynamic Move" -- select a node in the
tree, click Position, choose Dynamic Move, click Start Step, drag the
gizmo in the viewport, click Done.

Key implementation notes:
- `SyncedViewportWidget` overrides `mousePressEvent`/`mouseMoveEvent`/
  `mouseReleaseEvent` to intercept LMB drag when a manipulator part is
  detected under the cursor (`HasActiveMode()`), suppressing the normal
  orbit behavior
- `DeactivateCurrentMode()` called after `StopTransform()` to reset
  `HasActiveMode()` to False -- without this, orbit is locked out after
  the first drag (confirmed real bug, fixed)
- Slight quirkiness: one extra click away from gizmo sometimes needed
  to fully hand LMB control back to orbit. Acceptable, trainable.
- Transform applied to build123d node via `manipulator.Object()
  .LocalTransformation()` → `Location` → `Shape.move()` with
  parent-local frame conversion (same `_world_move_to_local()` as
  Mate/Align)

---

### Build order: ALL COMPLETE

1. ✅ **Mate/Align UI** -- full 3-2-1 working with purity of motion
2. ✅ **Dynamic Move** -- AIS_Manipulator gizmo, all 6 DOF
3. ✅ **Import STEP** -- load new files mid-session, re-parent
4. ✅ **Export STEP** -- export current assembly tree

---

### Next features (from Doug's list)

1. **RMB context menu in viewport** -- quick access to Fit All and
   other view commands. Simple, high value.
2. **Workplanes + part creation** -- pick a face, create a workplane,
   sketch 2D geometry, extrude/cut to make new parts. See §6 below
   for design notes.

---


---

## 2. Copy vs. Share (shared instances) -- UI confirms this is a real, deliberate feature

**Status:** parked (see `STEP_NOTES.md`'s "shared vs. copied" section
for the full earlier investigation) -- but the CoCreate "Create Copy"
dialog screenshot is new, relevant evidence worth recording here too.

![CoCreate Create Copy dialog, with Position embedded](imgs/cocreate-copy-share-with-position.png)

The dialog presents `Copy` and `Share` as an explicit, first-class
choice at the moment a part/assembly instance is created -- not an
automatic side effect of how something happens to be built. This
matches what Doug described wanting weeks ago ("ideally... know that
by looking at the assembly tree") and confirms it's a real, deliberate
CoCreate feature, not a nice-to-have invention.

Still not chasing full container-level sharing right now (per "let's
see how far we can go on the well-trodden road" -- build123d's
`import_step()`/`Compound` model doesn't preserve it, confirmed via
`diagnose_shared_instances.py`). But this screenshot is worth keeping
on file: if/when this gets revisited, the UI pattern to copy is
"Copy/Share chosen up front, Position dialog appended right after,"
not a separate later command.

---

## 3. Undo / Redo

**Status:** investigated tonight, real open question identified,
NOT YET RESOLVED -- flagging per Doug's request before further design
or implementation happens here.

**Good news:** OCCT's `TDocStd_Document` (OCAF's document class) has
real, built-in transactional undo/redo -- not partial, not
aspirational:
- `NewCommand()` opens a transaction; `CommitCommand()` closes it and
  records a delta; `Undo()`/`Redo()` step through committed deltas.
- `SetUndoLimit(n)` controls history depth (0 = disabled/default,
  negative = unlimited).
- Confirmed via OCCT's own class reference docs, not just a forum
  guess.

**Two confirmed caveats:**
- **Session-only.** Directly confirmed by an OCCT forum supervisor:
  "There is no possibility to store the undo/redo information in the
  OCCT TDocStd_Document... this is out of scope of the functionality
  of most standard applications." Undo history does not survive
  save/reload. This is normal for most CAD tools (not a red flag),
  but worth knowing going in.
- **Known footgun:** a confirmed forum bug report shows `NewCommand()`
  can hang indefinitely with NO error if you're using a custom
  `TDocStd_Application` subclass that doesn't correctly implement the
  required virtual methods -- fix was switching to the standard
  `AppStdL_Application`/`AppStd_Application` or implementing the
  methods properly. Worth remembering if `NewCommand()` ever just...
  never returns.

**THE OPEN QUESTION (not yet answered):** `TDocStd_Document`
undo/redo operates on OCAF's own data framework (TDF labels and
attributes). Our actual data model is build123d's `Compound`/anytree
Python object tree -- a layer ABOVE OCAF, not OCAF itself. It is NOT
YET VERIFIED whether mutating `Compound.children` tuples in Python
(how `remove_node`/`add_node`/the tree widget currently work) routes
through OCAF's document/transaction system at all. If it doesn't,
`TDocStd_Document`'s undo/redo has nothing to undo, and we'd need a
different strategy -- most likely an application-level undo stack
(command pattern / snapshot-diff over our own Python tree state)
instead of relying on OCAF's built-in mechanism.

**Next step when this gets picked up:** a small, isolated diagnostic
(same discipline as `diagnose_global_location.py` /
`diagnose_shared_instances.py`) -- build a tiny assembly, wrap a
`remove_node()` call in `NewCommand()`/`CommitCommand()`, call
`Undo()`, and empirically check whether the tree actually reverts.
Don't assume either way; check.

**Lowered urgency, not closed:** per the file-format discussion
below, Doug's KodaCAD-era precedent was to use frequent STEP export
as a "poor man's undo" -- acceptable, not blocking. Real undo is a
nice-to-have for now, not a prerequisite for the next phase of work.
The open technical question above (does OCAF's transaction system see
build123d-level mutations at all) is still worth answering eventually,
just not urgently.

---

## 4. File storage format

**Status:** discussed once, tentatively settled as "not a
show-stopper" -- not a final decision, but no longer fully open
either.

At least three separable questions, not one:
1. **Interchange format** -- STEP. Already solid; this is settled,
   proven extensively (see `STEP_NOTES.md`).
2. **Native/working format** -- does the app need its own save format
   for in-progress work, to round-trip things STEP can't represent
   (construction geometry, sketch history if any is kept, the
   copy/share distinction from item 2 above if STEP export doesn't
   preserve it, undo history if we ever wanted that to survive a
   session -- though see item 3's caveat that OCAF itself doesn't
   support this)?
3. If a native format is needed: roll our own (e.g. JSON schema
   wrapping STEP + extra metadata) vs. an existing OCAF persistence
   format (`BinOcaf`/`XmlOcaf`, mentioned in passing during the
   undo/redo research) vs. something else entirely.

**Precedent from the original KodaCAD project:** Doug's prior
approach was to use STEP itself as a "poor man's native format" --
just export STEP frequently, including as a stand-in for undo (export
often enough that an unwanted change can be recovered by reloading an
earlier export). Acknowledged as "kind of lame," but functional.

**Current read:** in the context of THIS project -- a from-scratch
app with no legacy users/files to migrate, and STEP round-tripping
already proven solid (including hierarchy, color, and assembled
position) -- it's reasonable to conclude native format and "real"
undo persistence are NOT show-stoppers. Frequent STEP export remains
a perfectly viable safety net, same as before. Not formally closing
this thread (a native format could still be worth building later, if
e.g. construction geometry or sketch history end up needing to be
preserved across sessions), but it's not blocking anything right now
and doesn't need a dedicated design session soon.

---

## 5. Picking -> pose -> move -> export, proven end to end (this session)

**Status: DONE.** This was the big push this session, and every piece
of it now works on real geometry from `as1-oc-214.stp`, not just
synthetic test data. Worth a permanent record since several real bugs
got found and fixed along the way -- future debugging should check
this list before assuming something new is broken.

### Picking: face, edge, and vertex selection

Two real, confirmed bugs, both fixed:

- **`AIS_Shape` selection-mode mismatch.** `AIS_Shape`'s own
  selection-mode integers are NOT guaranteed to equal
  `TopAbs_ShapeEnum`'s values -- there's a documented, sanctioned
  translation method, but this OCP build exposes it as the STATIC
  form `AIS_Shape.SelectionMode_s(TopAbs_EDGE)` (called on the class),
  not an instance method. Passing `TopAbs_FACE` directly had "worked"
  only by numeric coincidence; `TopAbs_EDGE` did not, which is why
  edge clicks were returning `TopAbs_SOLID` instead of `TopAbs_EDGE`.
  **Remember this pattern** (`Did you mean: 'X_s'?` in a traceback)
  generally -- it's the same `_s`-suffix-means-static convention that
  bit us once before during the STEP export investigation
  (`FindShape`/`FindShape_s`).
- **Default 2px selection tolerance.** OCCT's default pick tolerance
  is tiny -- fine for faces (which cover most of a solid's visible
  area) but makes edges/vertices nearly unhittable by eye. Fixed via
  `SetSelectionSensitivity()`.

Result: face, straight edge, and vertex picking all confirmed working
in the live app, including on a real multi-part, multiply-instanced
assembly.

### The `GeomType` enum comparison bug

`geom_type` returns a real `GeomType` ENUM, not a string -- confirmed
via build123d's own documented examples (`e.geom_type ==
GeomType.CIRCLE`). Every `geom_type == "CIRCLE"` string comparison in
this codebase (in BOTH `pose.py`'s original circle resolution AND
`assembly_viewer.py`'s terminal reporting) was wrong from the start --
meaning a genuinely circular edge may never have correctly hit the
"fast path" even before any of tonight's other work. Fixed at the
root in both files. Worth grepping for `== "CIRCLE"` or similar bare
string comparisons against any OTHER enum-returning property if this
class of bug is ever suspected elsewhere.

### Circle-fit fallback for non-CIRCLE-typed circular edges

Confirmed REAL (not hypothetical): `as1-oc-214.stp`'s `rod` part has
its end-cap boundary split into SEMI-circular arc edges (confirmed by
Doug's own topology analysis: the rod is 4 shells -- 2 flat circular
ends + 2 semi-cylindrical sides, joined along 2 longitudinal seams),
and at least one such arc is encoded as `geom_type == BSPLINE`, not
`CIRCLE`, despite being geometrically circular. `pose.py` now falls
back to a from-scratch least-squares circle fit (Kasa method) when
`geom_type` isn't `CIRCLE`, verified against: a full synthetic
circle, a half-circle (matching the real rod case), AND a straight
edge (which must be cleanly REJECTED, not crash -- see below). All
three are now permanent, hard-assertion regression tests in
`pose.py`'s `_self_test()`.

**A real bug found via live picking, not caught by any prior test:**
the fit's normal-estimation step sums cross products of sampled point
pairs; for COLLINEAR points (i.e. calling the circle-fit on an
ordinary STRAIGHT edge -- which happens on EVERY edge pick, since
circle_center/circle_axis is tried first regardless of geom_type),
every cross product is the zero vector, and normalizing a zero vector
throws OCCT's `Standard_ConstructionError` -- NOT a Python
`ValueError`, so it silently escaped every `except ValueError:`
handler built around the function. Confirmed via real picking: two
ordinary straight edges on `plate` crashed with this exact error.
Fixed by detecting the degenerate case explicitly and raising a clean
`ValueError` before ever calling `.normalized()`. This gap existed
because every prior test only ever fed the fit genuinely curved
input -- now covered by a dedicated straight-edge rejection test.

### Moving a part: `Shape.move()`, not `.location.position +=`

Doug's instinct -- "why do we need to do ANYTHING with the assembly
structure, we're just moving one part" -- was correct, and led
directly to both finding a real bug and the simpler, correct fix.

**The bug:** `rod.location` returns a DETACHED COPY of the shape's
location, not a live reference. `rod.location.position += delta`
mutates that throwaway copy -- no exception, because the operation is
valid Python, it just never touches `rod` itself. Confirmed via real
testing: before/after position printouts were silently IDENTICAL.
This also means the FIRST attempted fix (`rod.moved(move)` +
`remove_node()`/`add_node()` tree surgery to swap the new object in)
was unnecessary complexity that ALSO directly caused a separate,
serious bug: re-exporting after that tree surgery corrupted
`rod-assembly` into STEP header text (`Open CASCADE STEP translator
7.9.1.1`) in the exported file, confirmed via CAD Assistant.

**The fix:** `rod.move(delta)` -- build123d's own documented method,
explicitly described as "relative change of THIS object" (the other
three methods in their 2x2 matrix: `locate`=absolute+this,
`located`=absolute+copy, `moved`=relative+copy). In-place, no new
object, NO tree restructuring needed at all -- confirming Doug's
original intuition was right both in spirit and in the specific
mechanics. Verified end to end: numeric shift (50.0000mm along axis,
0.000000 perpendicular), in-memory tree print (confirmed untouched),
export, and reload in CAD Assistant (rod visibly, correctly moved).

**Test script:** `gui/test_move_rod_axially.py` -- kept as a
standalone, runnable reference for "pick real geometry -> resolve a
pose -> move a real part -> export -> verify," the first time this
full chain was exercised together.

---

## 6. Workplanes, Sketch, Part Creation and Cut/Mill

**Status: COMPLETE including STEP export.** Full workflow confirmed
working end-to-end: create sub-assembly → sketch → extrude/cut →
export STEP → verified in CAD Assistant. New parts appear with correct
hierarchy, geometry, and color in the exported file.

**What's working (confirmed via real testing):**

### Phase 1 -- Active Assembly
- RMB on any Compound node -> "► Set Active Assembly" makes it the
  target for new parts and imports.
- Active node shown in bold with ► prefix in the tree.
- "✕ Clear Active Assembly" reverts to root as implicit target.
- `tree.get_target_node()` returns active node, or root if none set.
- Both "Workplane..." and "Import STEP" use `get_target_node()`.

### Phase 2 -- Workplane Display
- `src/workplane.py` -- fully ported from kodacad to OCP bindings.
  Smoke test passes.
- `gui/workplane_dialog.py` -- floating QDockWidget: pick face ->
  show workplane -> sketch -> extrude or cut.
- Workplane displayed as semi-transparent green face with pink/magenta
  U/V crosshair lines through origin.
- All AIS objects tracked and erased cleanly after operation or cancel.

### Phase 3 -- Create/Delete Sub-Assembly
- RMB -> "📁 New Sub-Assembly..." creates empty Compound under clicked
  node. Uses `BRep_Builder.MakeCompound()` for a valid empty OCC shape
  (plain `Compound(children=[])` has no `_wrapped` and causes
  `AssertionError` in build123d's `_post_attach`).
- RMB -> "🗑 Delete" confirms then removes all leaf AIS from viewport
  using `context.Remove()` (not `Erase()` -- see item 9), removes
  from assembly data, removes from tree.

### Phase 4 -- Interactive Sketch Toolbar
- `gui/sketch_toolbar.py` -- vertical QToolBar with kodacad icons
  (converted from GIF to PNG with transparent background and color).
- Construction lines: H cline, V cline, H+V, Angled, Linear Bisector
- Construction circles: ccirc
- Profile geometry: Line, Rect (4 edges), Circle, Arc Ctr-2Pts, Arc 3Pts
- Delete Last, Clear All
- Each tool pops a QInputDialog for coordinate input -- coordinate-driven,
  no freehand mouse picking needed (same approach as kodacad).
- `rect()` adds 4 edges; `_display_profile(n_new=4)` shows all four.
- `ccircs` stores `((cx,cy), r)` -- unpack as `(cx, cy), r = cc`.

### Phase 5 -- Active Part + Cut/Mill
- RMB on any Solid (leaf) node -> "⚙ Set Active Part" marks it for
  Cut/Mill.
- Active part shown bold with ★ prefix + orange background on tree row.
- Orange wireframe overlay in viewport using `BRepBuilderAPI_Copy` of
  the shape (must be independent copy -- see item 9).
- Overlay uses `context.Remove()` not `context.Erase()`.
- Workplane dialog Step 3 shows active part name; "✂ Cut Into Active
  Part" button enabled when both workplane and active part are set.
- Cut uses `BRepAlgoAPI_Cut(work_shape, tool)` where tool is the profile
  extruded in -wDir direction (into the material).
- After cut: original color read from `_ais_shape_to_node["color_rgb"]`
  before old AIS removed, restored on new AIS after redisplay.
- "⊞ Workplane..." button (renamed from "Create Part...") opens dialog.

**Files changed this session:**
- `src/workplane.py` -- OCP port + `TopoDS.Face_s()` downcast fix
- `gui/workplane_dialog.py` -- full sketch workflow, cut, active part label
- `gui/sketch_toolbar.py` -- new file, full sketch toolbar
- `gui/icons_png/` -- new folder, 36 PNG icons from kodacad GIFs
- `gui/assembly_tree_widget.py` -- active assembly + active part, RMB menu
- `gui/assembly_viewer.py` -- `context.Remove()`, `MoveTo(-1,-1)` on RMB
- `gui/main_app.py` -- all signal wiring, color preservation, overlay

**Workflow (as built):**
1. Load STEP file -> assembly in tree and viewport.
2. RMB on sub-assembly -> "► Set Active Assembly".
3. RMB on a solid -> "⚙ Set Active Part" (orange tree row + wireframe).
4. Click "⊞ Workplane..." -> dialog opens in face-pick mode.
5. Click a face -> green workplane + pink crosshairs appears.
6. Use sketch toolbar to draw profile (rect, circle, clines, etc).
7a. Enter depth + name -> "✚ Create Part" -> new solid in tree.
7b. Enter depth -> "✂ Cut Into Active Part" -> hole/pocket cut.
8. Part retains its original color after cut.

**Next things to explore:**
1. **Revolve** -- profile around an axis via `BRepPrimAPI_MakeRevol`.
2. **Clickable cline intersections** -- `WorkPlane.intersectPts()`
   already computes them; display as vertex AIS objects for snapping.
3. **Workplane as persistent tree node** -- like CoCreate's /w1.
4. **Save/export** -- STEP export including created and cut parts.
5. **Undo** -- open question whether OCAF covers build123d mutations.

---

## 7. RMB Context Menu in Viewport

**Status: COMPLETE** (with one known issue noted below).

RMB in the viewport opens a menu with: Fit All, Fit Selected,
Reset View (isometric), View Top, View Front, View Right.

**AIS_ViewCube also added** -- orientation cube in the bottom-right
corner. Clicking edges (12) and corners (8) works correctly, animating
the camera to the corresponding view.

**Known issue: face clicks on the view cube cause a crash.**
Clicking one of the 6 face labels (TOP, FRONT, RIGHT, etc.) causes a
C++ segfault -- not catchable by Python. Root cause is inside OCCT's
animation or camera-setting code when triggered by a face owner.

**Workaround:** use the RMB menu's View Top/Front/Right items.
Avoid clicking the view cube's flat faces.

**TODO (low priority):** investigate disabling face sensitive zones,
or wait for a newer OCP build where this may be fixed upstream.

---

## 8. Lessons Learned: Qt QTreeWidgetItem Text Modification

**Context:** Setting the active assembly bold + ► prefix in the tree
took many debugging iterations. Documented to avoid repeating.

**The symptom:** `item.setText(0, "► assembly")` was called correctly
(confirmed by debug prints) but the tree displayed `<unnamed>`.

**Root cause:** `_item_to_node.get(id(item))` returned the WRONG node
because Python had reused the memory address of a previously GC'd item.
The lookup found a different node whose `label=None`, giving `<unnamed>`.

**The fix:** Read `item.text(0)` at the very start of
`_set_item_active_style`, before any other operation:

```python
def _set_item_active_style(self, item, active: bool, prefix: str):
    current_text = item.text(0)
    for p in ("► ", "★ "):
        if current_text.startswith(p):
            current_text = current_text[2:]
            break
    base_label = current_text
    font = item.font(0)
    font.setBold(active)
    item.setFont(0, font)
    item.setText(0, f"{prefix}{base_label}" if active else base_label)
```

**The lesson:** Read `item.text(0)` directly from the widget -- never
reconstruct the label from a dict keyed on `id(item)`. The widget
always has the right text; the dict lookup may not after GC.

---

## 9. Lessons Learned: OCCT AIS Shape Management

**Context:** The active-part highlight and Cut/Mill redisplay required
deep understanding of OCCT AIS management. Several subtle bugs found
and fixed -- documented here for future reference.

### Erase vs Remove

`context.Erase(ais, update)` hides the shape visually but leaves it
registered in OCCT's selection index. After `Erase`, `MoveTo()` can
still detect the shape, and erasing a "selected" shape can segfault.

`context.Remove(ais, update)` fully deregisters from ALL OCCT internal
structures. Use `Remove` when permanently replacing or deleting a shape.
Use `Erase` only for temporary hide/show toggling.

**Rule: always use `context.Remove()` when permanently replacing or
deleting an AIS shape.**

### Color bleed between AIS objects sharing a TopoDS_Shape

When two `AIS_Shape` objects are built from the SAME `TopoDS_Shape`
instance, `SetColor()` on one bleeds to the other -- OCCT stores color
in the shape's presentation layer, shared between all AIS objects
referencing the same topology.

**Fix:** use `BRepBuilderAPI_Copy(shape).Shape()` to create an
independent copy for overlay AIS objects:

```python
from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
shape_copy = BRepBuilderAPI_Copy(ais.Shape()).Shape()
overlay = AIS_Shape(shape_copy)
overlay.SetColor(orange)  # does NOT affect original shaded AIS
```

### OCCT dynamic hover highlight freezing on RMB

OCCT has two separate highlight systems:
- **Dynamic highlight** (hover): applied by `MoveTo()`. Yellow/orange
  in default OCCT style. Cleared when cursor moves to a new shape.
- **Selection highlight**: applied by `context.Select()` on click.
  Stays until `context.ClearSelected()` is called.

When a RMB context menu opens, mouse movement stops and the dynamic
highlight stays frozen on the shape under the cursor. `ClearDetected()`
does not exist in this OCP version.

**Fix:** call `context.MoveTo(-1, -1, view, True)` before showing the
RMB menu. This moves detection to an off-screen pixel, clearing the
dynamic highlight.

### Color preservation across Cut/Mill redisplay

When `BRepAlgoAPI_Cut` replaces a shape, `display_subtree()` assigns
a new palette color by position which may differ from the original.

The original color is stored in `_ais_shape_to_node[id(ais)]["color_rgb"]`
as an `(r, g, b)` tuple. Read it BEFORE removing the old AIS, then
restore it on the new AIS after redisplay.

### Overlay lifetime management

An AIS overlay built from a copied shape must be erased BEFORE the
original AIS is removed. The copied shape still shares internal OCCT
topology with the original -- if the original is removed first, the
overlay's topology becomes invalid and accessing it segfaults.

**Rule: always remove overlays BEFORE removing the source AIS.**

### Missing `_extrude` method after Cut/Mill refactor

When `_on_cut_clicked` was added to `workplane_dialog.py`, the
`_extrude` method was accidentally dropped during the refactor of
`_on_create_clicked`. The error appeared as:

```
'WorkplaneDialog' object has no attribute '_extrude'
```

**Fix:** restore `_extrude` as a separate method called by
`_on_create_clicked`. It extrudes in `+wDir` (out of the face).
`_cut` (called by `_on_cut_clicked`) extrudes in `-wDir` (into the
material) and subtracts using `BRepAlgoAPI_Cut`. Both methods share
the same `makeWire()` → `MakeFace` → `MakePrism` preamble but
diverge at the boolean operation step.

**Lesson:** when adding a sibling operation (cut alongside extrude),
factor the shared preamble into a helper so neither operation can
accidentally clobber the other's method.

---

## 10. Lessons Learned: Exporting Freshly Created Parts to STEP

**Status: RESOLVED AND CONFIRMED WORKING.**

The full workflow -- create sub-assembly → sketch on workplane → extrude
→ export to STEP -- is now confirmed working end-to-end, including the
new part appearing correctly in CAD Assistant with proper hierarchy,
geometry, and color.

**The symptom:** A newly created part appeared correctly in both the
viewport and the assembly tree (correct volume, correct label, correct
parent), but was completely absent from the exported STEP file. No
error, no warning -- just silent omission.

### The investigation (a long trail of false leads)

**False lead 1: Shape.cast() for XDE registration.**
Hypothesis: `Solid(TopoDS_Shape)` doesn't go through build123d's XDE
pipeline, so `export_step` can't write it. Tried `Shape.cast()` instead.
Result: `Shape.cast()` returned `None` for a `TopAbs_SOLID` shape, crashing
with `'NoneType' has no attribute 'label'`. Dead end.

**False lead 2: Round-trip through temporary STEP file.**
Hypothesis: write the raw shape to a temp file with `STEPControl_Writer`,
re-import with `import_step()` to get a fully XDE-registered shape.
Result: the round-trip worked (118 entities written, re-imported as a
`Solid`) but the part STILL didn't appear in the export. The re-imported
`Solid` had a spurious parent Compound (same bug as `step_export_fix.py`),
and after detaching it, it still didn't export. More investigation needed.

**False lead 3: Spurious parent on the re-imported Solid.**
After the round-trip, `b3d_solid.parent` was a phantom Compound wrapper
from the OCCT translator. Severed with `b3d_solid.parent = None`. Still
didn't fix the export. The Solid was now parentless when added to the
assembly, which caused it to appear in the tree but still be skipped by
the exporter.

**The breakthrough: reading `_create_xde` source directly.**
Added a diagnostic to `step_export_fix.py` that used `pkgutil.walk_packages`
to search all of `build123d`'s submodules for `_create_xde` and print its
source. Found it in `build123d.exporters3d`. The critical section:

```python
for node in PreOrderIter(to_export):
    if node.wrapped is None:
        continue
    parent = getattr(node, "parent", None)
    if parent is None:
        node_label = shape_tool.AddShape(node.wrapped, False)
    else:
        parent_label = label_map.get(parent, TDF_Label())
        parent_label = resolve_component_parent_label(parent_label)
        if parent_label.IsNull():
            continue          # ← THIS is where new_part was silently dropped
        node_label = shape_tool.AddComponent(parent_label, node.wrapped)
```

**The actual root cause:** `new_assembly` was created as an EMPTY
`TopoDS_Compound` (via `BRep_Builder.MakeCompound()` with nothing added).
When `_create_xde` called `shape_tool.AddShape(empty_compound, False)`,
OCCT's ShapeTool returned a **null label** for an empty compound -- it has
no sub-shapes to register. This null label was stored in `label_map`.
When `new_part` was processed next, it looked up `label_map[new_assembly]`
= null label → `parent_label.IsNull()` = True → **silently skipped.**

The assembly tree was completely correct. The node diagnostics showed
`new_part` with `type=Solid`, `parent='assembly'`, `wrapped=TopoDS_Solid`
-- indistinguishable from any imported part. The bug was entirely inside
`_create_xde`'s handling of the parent label lookup.

### The fix

After adding a new part via `_on_part_created`, call `_rebuild_ancestors()`
which walks up the anytree hierarchy and rebuilds each ancestor Compound's
`_wrapped` to be a `TopoDS_Compound` containing ALL its descendants'
shapes:

```python
def _rebuild_ancestors(self, node):
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
```

This ensures `shape_tool.AddShape(compound, False)` receives a compound
with real sub-shapes, returns a valid (non-null) label, and `_create_xde`
can then successfully register all child nodes under it.

### Key lessons

1. **`shape_tool.AddShape()` returns a null label for empty TopoDS_Compound.**
   Any Compound node in the anytree hierarchy whose `_wrapped` is an empty
   compound will silently cause ALL its descendants to be skipped in the
   STEP export. This is not an error -- OCCT just has nothing to register.

2. **The node diagnostics (PreOrderIter printout before export) was
   misleading.** Everything looked correct in the tree. The bug was not
   in the tree structure but in how OCCT's ShapeTool handled the wrapped
   shape. Always check BOTH the tree structure AND the XDE label map when
   debugging STEP export issues.

3. **`Shape.cast()` does not work on raw `TopoDS_Shape` objects from OCCT
   operations like `BRepPrimAPI_MakePrism`.** It returns `None`. Use
   `Solid(TopoDS_Shape)` directly for wrapping raw OCCT shapes.

4. **build123d's `_create_xde` is in `build123d.exporters3d`**, not
   `build123d.exporters`. Use `pkgutil.walk_packages` to search for it
   when debugging export issues.

5. **The round-trip through a temporary STEP file is unnecessary** once
   the ancestor `_wrapped` is properly rebuilt. `Solid(TopoDS_Shape)` is
   sufficient for the new part itself -- the problem was always in the
   parent Compound, not the Solid.

### Files changed
- `gui/main_app.py` -- added `_rebuild_ancestors()` called from
  `_on_part_created()`; also `_on_part_cut()` should call it after
  cut to keep exported geometry up to date.
- `src/step_export_fix.py` -- cleaned up (all diagnostics removed);
  remains a one-line fix for the spurious root parent bug.
- `gui/workplane_dialog.py` -- restored `_extrude()` method (was
  accidentally dropped during Cut/Mill refactor).
