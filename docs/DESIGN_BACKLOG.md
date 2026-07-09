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

### Cut/Mill also fixed in same session

After fixing part creation export, the Cut/Mill operation stopped
working visually -- the part appeared unchanged after a cut.

**Root cause:** `_on_part_cut()` in `main_app.py` was using
`Shape.cast(new_shape)` to wrap the `BRepAlgoAPI_Cut` result before
assigning to `node._wrapped`. But `Shape.cast()` returns `None` for
raw OCCT shapes (proven earlier when it returned None for
`TopAbs_SOLID`). So `cast_shape.wrapped` crashed silently, `node._wrapped`
was never updated, and the viewport showed the old uncut shape.

**Fix:** assign `node._wrapped = new_shape` directly (the raw
`TopoDS_Shape` from `BRepAlgoAPI_Cut`) and call `_rebuild_ancestors(node)`
so the modified part also exports correctly to STEP.

**Confirmed working:** both the new extruded part AND the cut plate
appear correctly in the exported STEP file, verified in CAD Assistant.

### Files changed
- `gui/main_app.py` -- added `_rebuild_ancestors()` called from both
  `_on_part_created()` and `_on_part_cut()`; removed broken
  `Shape.cast()` from `_on_part_cut()`.
- `src/step_export_fix.py` -- cleaned up (all diagnostics removed);
  remains a one-line fix for the spurious root parent bug.
- `gui/workplane_dialog.py` -- restored `_extrude()` method (was
  accidentally dropped during Cut/Mill refactor).

---

## 11. Intersection Point Snap for Sketch Tools

**Status: COMPLETE.** Confirmed working for the bottle tutorial workflow.

**What was built:**
- After each cline or ccirc is added to the workplane, `_display_intersections()`
  computes all intersection points via `wp.intersectPts()` and displays
  yellow `+` markers as `AIS_Shape` vertex objects in the viewport.
- Markers are activated for OCCT vertex selection so they show a cyan
  hover highlight when the cursor passes over them.
- Clicking a marker fires `geometry_picked` with `TopAbs_VERTEX`, which
  `main_app._on_geometry_picked` routes to
  `sketch_toolbar.receive_vertex_pick()`.
- `receive_vertex_pick` finds the nearest stored intersection point (within
  1mm tolerance) and appends its `(u, v)` to `_pending_uvs` queue.
- `_get_point()` in each tool method pops from `_pending_uvs` first; if
  the queue is empty it falls back to `QInputDialog`.

**The snap queue workflow (click points BEFORE clicking tool button):**
- 1-point tools (circle center, cline through point):
    click 1 marker → click tool button
- 2-point tools (line, rect):
    click start marker → click end marker → click tool button
- 3-point tools (arc3p):
    click pt1 → click pt2 → click pt3 → click tool button
- Mixed: snap some points, type others -- any combination works.
  Snapped points are consumed FIFO; unsnapped points show a dialog.

**Crashes fixed during development:**
- `ctx.Erase()` on vertex AIS causes segfault -- must use `ctx.Remove()`.
- Must call `ctx.ClearSelected(False)` BEFORE `ctx.Remove()` on markers,
  otherwise removing a "selected" AIS crashes OCCT.
- Do NOT activate intersection markers while face-pick mode is active
  (workplane creation step) -- only activate after the workplane is set.

**Files changed:**
- `gui/sketch_toolbar.py` -- `_display_intersections()`, `_erase_isect_ais()`,
  `receive_vertex_pick()`, `pop_pending_uv()`, `_pending_uvs` queue.
- `gui/main_app.py` -- `_on_geometry_picked()` routes `TopAbs_VERTEX` picks
  to sketch toolbar when workplane dialog is visible and toolbar is enabled.

---

## 12. Confirmed Working Workflow: Bottle Tutorial (partial)

The following workflow has been confirmed working end-to-end, verified
by successfully extruding the classic OCC bottle body profile and
exporting the result to STEP (verified in CAD Assistant):

```
1.  Load base STEP file (as1-oc-214.stp)
2.  RMB on as1 → New Sub-Assembly (e.g. "assembly")
3.  RMB on "assembly" → Set Active Assembly
4.  Click "⊞ Workplane..." button
5.  Click the top face of the plate → green workplane + pink crosshairs appear
6.  Hide everything except the workplane (uncheck plate etc. in tree)
7.  Add 6 horizontal clines: H cline at Y = 30, 15, 7.5, -7.5, -15, -30
    → yellow + markers appear at all intersections with the V cline
8.  For each straight line segment:
        click start + marker → click end + marker → click Line tool
9.  For each arc:
        click pt1 + marker → click pt2 + marker → click pt3 + marker
        → click Arc 3Pts tool
10. Profile is now a closed loop
11. Enter depth, name → click "✚ Create Part"
    → workplane erased, new solid appears in tree and viewport
12. Export STEP → new part included in exported file ✓
```

**Key UX notes from testing:**
- All parts must be hidden (only workplane visible) before trying to
  click intersection markers -- otherwise OCCT picks the part faces
  instead of the vertex markers.
- The snap workflow is click-THEN-tool (not tool-then-click). Queue up
  all the points for a tool operation, then click the tool button.
- The workplane always starts with H+V clines through the origin
  (hvcl((0,0)) in WorkPlane.__init__), so a yellow + marker at (0,0)
  appears as soon as the face is picked.

**What's NOT yet implemented (next steps for bottle tutorial):**
- **Fillet/Blend** -- `BRepFilletAPI_MakeFillet` on selected edges.
  Required for the next step of the bottle tutorial (blending the 12
  edges of the extruded body). This is the next major feature to add.
- **Pull (Fuse)** -- `BRepAlgoAPI_Fuse` to add material (complement
  to the existing Cut/Mill).
- **Shell** -- `BRepOffsetAPI_MakeThickSolid` to hollow out a solid.
- **"At Origin" workplane** -- workplane not tied to a face pick, just
  placed at the global XY/XZ/YZ plane. Useful for starting from scratch
  without any existing geometry to pick from.

---

## 13. Fillet / Blend Operation

**Status: COMPLETE.** Confirmed working including STEP export, verified
in CAD Assistant. Used to fillet all 12 edges of the bottle body (r=3mm).

**What was built:**
- `gui/fillet_dialog.py` -- floating QDockWidget with 3-step workflow:
  1. Shows active part name (synced from `_on_active_part_changed`)
  2. User clicks edges one by one -- each appears as "Edge N" in a list
  3. User enters radius and clicks "⌀ Apply Fillet"
- "⌀ Fillet..." button added to tree panel (enabled on file load)
- `main_app._on_geometry_picked` routes `TopAbs_EDGE` picks to fillet
  dialog when it's visible
- `_on_fillet_done` reuses the exact same replace-and-redisplay pattern
  as `_on_part_cut`: color preservation, `_rebuild_ancestors()`, orange
  overlay re-applied via `_on_active_part_changed(node)`

**Bugs found and fixed during development:**

### Bug 1: Missing `_on_create_part_clicked` method (AttributeError on startup)

When inserting `_on_fillet_clicked` and `_on_fillet_done` before
`_on_create_part_clicked`, the str_replace ate the `def` line of
`_on_create_part_clicked`, leaving its body as a bare docstring floating
inside `_on_fillet_done`. Result: `AttributeError: 'MainWindow' object
has no attribute '_on_create_part_clicked'` on startup.

**Fix:** restore the `def _on_create_part_clicked(self):` line.
**Lesson:** when using str_replace with a method name as the split point,
always verify the target method still exists afterward with `grep -n`.

### Bug 2: `IsSame()` fails on STEP round-tripped edges (no suitable edges)

The fillet dialog originally validated picked edges with:
```python
explorer = TopExp_Explorer(active_part.wrapped, TopAbs_EDGE)
while explorer.More():
    if explorer.Current().IsSame(edge):   # always False after round-trip
        found = True
```
And `_apply_fillet` passed the picked edges directly to `MakeFillet.Add()`.

Both failed with the same root cause: **after a STEP export/import
round-trip, the edge objects in `node.wrapped` are new C++ TopoDS objects,
even though they represent geometrically identical topology.** OCCT's
`IsSame()` checks C++ object identity (internal TShape pointer), not
geometric equality. `MakeFillet.Add(r, edge)` also requires the edge to
be the exact C++ object that exists inside the shape being filleted.

**The symptom:** `IsSame()` always returned False → "That edge is not in
the active part". After removing that check, `MakeFillet` still failed
with "There are no suitable edges for chamfer or fillet."

**The fix:** match picked edges to `node.wrapped` edges by **midpoint
coordinates** rather than object identity:

```python
# Build midpoint lookup for all edges in node.wrapped
shape_edges = []
explorer = TopExp_Explorer(work_shape, TopAbs_EDGE)
while explorer.More():
    edge = TopoDS.Edge_s(explorer.Current())
    curve = BRepAdaptor_Curve(edge)
    mid_param = (curve.FirstParameter() + curve.LastParameter()) / 2.0
    mid_pt = curve.Value(mid_param)
    shape_edges.append((edge, mid_pt))
    explorer.Next()

# For each picked edge, find the nearest edge in shape_edges
for picked_edge in self._edges:
    curve = BRepAdaptor_Curve(picked_edge)
    mid_param = (curve.FirstParameter() + curve.LastParameter()) / 2.0
    picked_mid = curve.Value(mid_param)

    best_edge = min(shape_edges,
                    key=lambda e: picked_mid.Distance(e[1]),
                    default=None)
    if best_edge and picked_mid.Distance(best_edge[1]) < 1.0:  # mm
        mk.Add(radius, best_edge[0])  # pass the WRAPPED edge
```

The edge midpoint is a reliable geometric fingerprint: two edges at
the same midpoint location in 3D space represent the same edge
regardless of which C++ TopoDS_Edge object they came from.

**General lesson:** ANY OCCT operation that requires an edge/face/vertex
to be "inside" a specific shape (MakeFillet, MakeChamfer, BRepAlgoAPI_Cut
with specific sub-shapes, etc.) will fail if you pass objects from the
AIS display or from a different import session. Always re-find the
sub-shape by geometric fingerprint (midpoint, center, normal) rather
than storing and reusing TopoDS pointers across STEP round-trips.

**This is the same root cause as the IsSame failure in the fillet
ownership check, and the same pattern that would affect any future
feature that picks sub-shapes and then passes them to OCCT modelers.**

**Files changed:**
- `gui/fillet_dialog.py` -- new file
- `gui/main_app.py` -- Fillet button, dialog instantiation, edge
  routing in `_on_geometry_picked`, `_on_fillet_clicked`,
  `_on_fillet_done`, fillet dialog sync in `_on_active_part_changed`

---

## 14. Pull / Boss Operation (Add Material to Active Part)

**Status: COMPLETE.**

**What was built:**
- "⊕ Add To Active Part" button added to workplane dialog Step 3,
  alongside the existing "✂ Cut Into Active Part" button.
- `_on_pull_clicked()` and `_pull()` methods in `workplane_dialog.py`.
- Uses `BRepAlgoAPI_Fuse(work_shape, tool)` where tool is the profile
  extruded in **+wDir** (out of the face, adding material).
- Reuses the `part_cut` signal for the replace-in-place redisplay --
  the pattern is identical to Cut/Mill.

**Difference from Cut/Mill:**
- Cut: extrudes in `-wDir`, uses `BRepAlgoAPI_Cut`
- Pull: extrudes in `+wDir`, uses `BRepAlgoAPI_Fuse`
- Create Part: extrudes in `+wDir`, adds a NEW node to the assembly

**Used in bottle tutorial:** to add the cylindrical neck (circle profile
r=7.5, depth=7mm) to the top face of the filleted bottle body.

---

## 15. Shell Operation (Hollow Out Active Part)

**Status: COMPLETE.**

**What was built:**
- `gui/shell_dialog.py` -- floating QDockWidget: select open face(s) →
  enter wall thickness → apply shell.
- "⬡ Shell..." button added to tree panel.
- `BRepOffsetAPI_MakeThickSolid.MakeThickSolidByJoin()` with negative
  thickness (shells inward).
- Same face-center-of-mass matching approach as fillet's midpoint
  matching -- required because STEP round-trip creates new C++ TopoDS
  objects (see item 13, Bug 2).
- Same replace-in-place redisplay pattern as Cut/Mill and Fillet.

**OCP API note:** the old pythonOCC API called
`BRepOffsetAPI_MakeThickSolid(shape, faces, thickness, tolerance)`
as a constructor. In OCP the constructor takes no arguments; instead
call `mk.MakeThickSolidByJoin(shape, faces, -thickness, 1e-3)` then
`mk.Build()`.

**Used in bottle tutorial:** to hollow out the completed bottle body
(after fillet and neck pull) with 1mm wall thickness, open at the top
circular face.

---

## 16. Milestone: OCC Bottle Tutorial Completed

**Date: June 24, 2026.**

The classic OpenCASCADE "bottle" tutorial has been completed entirely
within our DIY CAD application and exported to STEP, verified in
CAD Assistant. This validates the full modeling workflow.

**Operations used in order:**
1. Load base assembly (as1-oc-214.stp)
2. New sub-assembly under as1 → set active
3. ⊞ Workplane on top face of plate
4. 6 horizontal clines (Y = 30, 15, 7.5, -7.5, -15, -30)
5. 2 straight profile lines (clicking + snap points)
6. 2 arc profile segments (clicking + snap points, 3-point arc)
7. ✚ Create Part (depth=70, name="bottle") → bottle body extruded
8. ⌀ Fillet all 12 edges (r=3mm) → blended body
9. ⊕ Add To Active Part: circle (r=7.5) on top face, depth=7 → neck added
10. ⬡ Shell: select top face, thickness=1mm → bottle hollowed out
11. 💾 Export STEP → verified in CAD Assistant

**Operations implemented to support the tutorial:**
- Workplane + sketch toolbar (items 6, 11)
- Intersection point snap queue (item 11)
- Extrude new part (item 6)
- Cut/Mill (item 6)
- Pull/Boss / Add to Active Part (item 14)
- Fillet/Blend (item 13)
- Shell (item 15)
- STEP export with freshly created parts (item 10)

**What the bottle tutorial does NOT test (future work):**
- Undo/redo
- Workplane at global origin (no face pick required)
- Persistent workplane as tree node
- Revolve operation
- Chamfer (similar to fillet but with distance instead of radius)
- Part positioning / Mate-Align on newly created parts
- Saving session state (currently relying on STEP as "poor man's save")

---

## 17. Redesigned 3-2-1 Positioning (In Progress)

**Status: MATH COMPLETE, DIALOG REDESIGN PENDING.**

### Background / Motivation

The existing Mate/Align dialog (item 1) had a bug: performing Step 2
(Align) after Step 1 (Mate) would "spoil" the mate -- the part would
rotate away from the mated plane. Root cause: `compute_align_move`
applied a full 6-DOF transform without constraining the move to remain
within the mated plane.

### Correct 3-2-1 Algorithm

**Step 1 -- Rotate to flush (3 DOF consumed):**
- Given face 1 on moving part (point P1, normal N1) and face 2 on
  fixed part (point P2, normal N2):
- Compute intersection line L of the two infinite planes:
  - Direction: D = N1 × N2
  - Point on L: P = ((d1·N2 - d2·N1) × D) / |D|²
    where d1 = N1·P1, d2 = N2·P2
- Rotate moving part about L by angle = atan2(|N1×N2|, N1·N2)
- Degenerate case (|D| ≈ 0, planes parallel): pure translation along
  normal to close the gap. The rotation axis is "at infinity."
- Mate: target normal is -N2 (opposed). Align: target is +N2 (same).
- NO translation along L -- any needed translation is handled in
  steps 2 and 3.

**Step 2 -- In-plane constraint (2 DOF consumed):**
- Part stays on the mated plane (no rotation).
- Translate by (P2 - P1) with the normal component removed:
  `delta_in_plane = (P2-P1) - N·(P2-P1)·N`
- Two sub-cases (same math, different geometry):
  a) Edge-to-edge: constrains translation ⊥ to edge + rotation.
     Leaves one translational DOF along edge direction for step 3.
  b) Hole-to-hole: constrains both in-plane translations (X, Y).
     Leaves one rotational DOF (spin about normal) for step 3.

**Step 3 -- Last DOF (1 DOF consumed):**
- Translation case (after edge-to-edge step 2): same in-plane
  translation math as step 2 -- "shove into corner."
- Rotation case (after hole-to-hole step 2): rotate about mated
  normal to align an edge or reference direction.
  `angle = atan2(|d1_in_plane × d2_in_plane|, d1_in_plane · d2_in_plane)`

### New Functions in pose.py

- `find_intersection_line(P1, N1, P2, N2)` → `(point, direction)` or None
- `compute_step1_move(pick1, pick2, mate=True)` → Location
- `compute_step2_move(pick1, pick2, mated_normal)` → Location
- `compute_step3_move(pick1, pick2, mated_normal)` → Location

All four functions confirmed correct by smoke test
`src/position_math_smoke_test.py`: **35/35 checks passed.**

### Planned Dialog Redesign

Three distinct sections (not radio buttons):

**Section 1: Mate/Align (3-2-1)**
- Step 1 button: "Mate" or "Align" (pick face on moving, pick face
  on fixed → rotate to flush)
- Step 2 button: "Align Edge" or "Align Axis" (pick feature on
  moving, pick feature on fixed → in-plane translate)
- Step 3 button: "Align Edge" or "Index Angle" (pick feature on
  moving, pick feature on fixed → last DOF)
- Each step shows its current state and result clearly.
- The mated_normal is remembered from Step 1 so Steps 2 and 3 can
  use it to constrain moves to the plane.

**Section 2: Align Axis**
- Single step: aligns 4 DOF (cylinder axis position + orientation).
- Pick axis on moving part, pick axis on fixed part → done.

**Section 3: Dynamic**
- AIS manipulator gizmo for rough positioning.

### Next Session
- Redesign `gui/position_dialog.py` to implement the 3-section UI
  and wire in the new pose.py functions.
- The `_world_move_to_local` transform (already in the dialog) is
  still needed since picks are in world space but `node.move()`
  operates in parent-local space.

---

## 18. 3-2-1 Positioning Dialog Redesign (COMPLETE)

**Status: COMPLETE.** All three steps working correctly including the
parallel-planes Mate case that previously required Reverse.

### What was built

**New `gui/position_dialog.py`** -- converted from `QDockWidget` to
`QDialog` (proper OS resize handles, normal window decorations).

Three sections:

**Section 1 -- Mate/Align (3-2-1), one column:**
- Step 1 — Mate: pick face on moving, pick face on fixed. Rotates
  about intersection line until flush. Reverse toggles mate↔align.
- Step 2 — Align Face: pick face on moving, pick face on fixed.
  Rotates within mated plane + translates to wall. Face type
  (flat/cylindrical) detected automatically.
- Step 3 — Complete: pick any face on moving, any face on fixed.
  Translates along the single remaining free direction only
  (mated_normal × wall_normal). Steps 1 and 2 preserved exactly.

**Section 2 -- Align Axis:** single 4-DOF step.

**Section 3 -- Dynamic:** AIS manipulator gizmo.

**New state stored between steps:**
- `_mated_normal`: set from Step 1's pick2 direction. Used by Steps
  2 and 3 to constrain moves to the mated plane.
- `_wall_normal`: set from Step 2's pick2 direction projected onto
  the mated plane. Used by Step 3 to find the single free direction.

### New math in `src/pose.py`

`find_intersection_line(P1, N1, P2, N2)`:
- Returns `(point, direction)` or `None` if planes are parallel.

`compute_step1_move(pick1, pick2, mate)`:
- Rotates about the intersection line of the two face planes.
- **Fixed parallel-planes case:** when N1 ∥ N2, four sub-cases:
  - N1 ≈ +N2, mate → 180° rotation + translation (was broken: gave
    pure translation only, same result as align)
  - N1 ≈ +N2, align → pure translation
  - N1 ≈ -N2, mate → pure translation
  - N1 ≈ -N2, align → 180° rotation + translation

`compute_step2_move(pick1, pick2, mated_normal)`:
- Projects D1 and D2 onto the mated plane.
- Rotates about mated_normal until D1_in_plane ∥ D2_in_plane
  (target = d2_proj, not -d2_proj -- parallel not anti-parallel).
- Translates by delta projected onto D2 direction (wall normal).

`compute_step3_move(pick1, pick2, mated_normal, wall_normal)`:
- free_dir = mated_normal × wall_normal (the single remaining DOF).
- Translates only along free_dir by delta·free_dir.
- No other motion -- Steps 1 and 2 preserved exactly.

### Bugs found and fixed

**Bug 1: Step 2 used anti-parallel target instead of parallel.**
`target = -d2_proj` rotated moving face to Mate with wall (normals
opposed) instead of Align (normals same). Fixed to `target = d2_proj`.

**Bug 2: Step 3 spoiled Step 2.**
`compute_step3_move` only knew about the mated plane normal, not the
wall normal. It could translate in any in-plane direction, including
perpendicular to the wall (undoing Step 2). Fixed by passing
`wall_normal` and computing `free_dir = N × W`.

**Bug 3: Reverse for Step 1 gave identity when N1 ≈ N2.**
When the moving and fixed faces start parallel (N1 ≈ N2), the
parallel-planes branch gave the SAME pure translation for both mate
and align -- the 180° flip for mate was missing entirely. Root cause:
`find_intersection_line` correctly returns None for parallel planes,
but the fallback only translated, never rotated.

**Bug 4: Reverse mode toggle was correct but math was wrong.**
Reverse correctly toggled mate↔align mode, but the underlying
`compute_step1_move` produced the same result for both when N1 ≈ N2.
Fixed in Bug 3's fix.

### UX improvements

- `QDialog` instead of `QDockWidget`: proper resize handles, normal
  window title bar, stays on top.
- Section 1 simplified from two-column (Mate/Align, Edge/Axis,
  Edge/Angle) to single-column (Mate, Align Face, Complete).
  Face type determines constraint type automatically.
- Step 2 and Step 3 buttons greyed out until Step 1 is done.
- Reverse toggles mate↔align for Step 1; flips pick2 direction for
  Steps 2, 3, and Align Axis.
- No "Start Step" button needed -- clicking a step button immediately
  begins that step's pick sequence.

### Smoke test update

`src/position_math_smoke_test.py` updated with Test 12 (edge-to-edge
with rotation). All 39 checks pass.

---

## 19. 3-2-1 Positioning: Hole/Axis Step 2 and Step 3 Fixes

**Status: COMPLETE.** Full 3-2-1 workflow confirmed working for both
the wall/corner scenario and the hole/axis scenario.

### What was fixed

**Bug 1: Cylinder face misidentified as wall in Step 2.**
The label check used `'circle' in label.lower()` but the cylinder
face label reads `"cylinder axis (via rim edge, ...)"`. Fixed to
`lbl.startswith('cylinder')` which correctly matches the label format
set by `resolve_pick`.

**Bug 2: Step 3 rotated about the wrong pivot point (hole scenario).**
After hole alignment in Step 2, Step 3 must rotate the moving part
about the **bolt hole axis** — the point Step 2 constrained. The
original code rotated about P1 (the picked face center in Step 3),
which introduced a spurious translation that moved the holes out of
alignment.

Root cause visible in debug output:
```
move=((73.3, -70.8, 0.0), (0, 0, 28.8°))
```
The 28.8° rotation was correct but `(73.3, -70.8)` translation showed
the pivot was wrong. The part was rotating about the face center
`(174, 107, 40)` instead of the hole center `(132.5, 88, 0)`.

**Fix:** store `_step2_pivot = pick2.point` (the fixed hole center
in world coordinates) when Step 2 detects a hole pick. Pass it to
`compute_step3_move` as the rotation axis origin. Rotating about the
hole center keeps the holes coincident -- Step 2 preserved exactly.

After fix:
```
[Step3/hole] angle=28.8deg  pivot=(132.5, 88.0, 0.0)
move=((~0, ~0, 0.0), (0, 0, 28.8°))   ← translation now ~zero
```

### State stored across steps

- `_mated_normal`: face plane normal from Step 1 (constrains Steps 2+3)
- `_wall_normal`: fixed face normal projected onto mated plane, set
  by Step 2 wall pick (constrains Step 3 to single translation DOF)
- `_step2_type`: "wall" or "hole" -- determines Step 3 behavior
- `_step2_pivot`: fixed hole center from Step 2 hole pick -- used as
  rotation axis origin in Step 3 hole scenario

### Final 3-2-1 algorithm summary

**Step 1 (Mate/Align):** rotate about intersection line of face planes.
  Parallel planes (N1 ∥ N2): translate + optional 180° rotation.
  `_mated_normal` stored from pick2.direction (±N2).

**Step 2 (Align Face):**
  - Flat face pick: rotate within mated plane until D1 ∥ D2, then
    translate along D2 to close gap to wall. Stores `_wall_normal`.
  - Cylinder face pick: translate in-plane until hole axes coincide.
    Stores `_step2_pivot` = hole center in world coords.

**Step 3 (Complete):**
  - After wall Step 2: translate along `mated_normal × wall_normal`
    only. No rotation, no other translation.
  - After hole Step 2: rotate about `_step2_pivot` along `mated_normal`
    until picked face directions align. No translation.

### Files changed
- `gui/position_dialog.py` -- `_step2_pivot` stored and passed to
  Step 3; cylinder detection fixed; section numbers removed from
  group box titles.
- `src/pose.py` -- `compute_step3_move` accepts `pivot` parameter;
  hole rotation pivots about stored hole center not pick1 point.

---

## 20. Known Issue: Double-Click in Viewport Causes Crash

**Status: UNRESOLVED.** Multiple fix attempts failed. Documented as a
known limitation.

**Symptom:** A double-click (or two rapid clicks) in the OCCT viewport
causes an immediate C++ segfault with no Python traceback. The app
exits silently.

**Root cause:** Qt's double-click event sequence is:
```
press → release → doubleClick → press → release
```
The two `release` events each call `context.Select(True)` in rapid
succession. OCCT's `AIS_InteractiveContext::Select()` is not
re-entrant -- the second call hits internal C++ selection structures
while they are still being modified by the first call, causing a
memory corruption segfault.

**Fix attempts (all failed):**

1. `mouseDoubleClickEvent` handler setting `_press_pos = None` --
   failed because the second `press` event resets it before the
   second `release` fires.

2. `_ignore_next_click` flag set in `mouseDoubleClickEvent`, checked
   in `mousePressEvent` to skip the second press -- failed because
   the segfault occurs inside C++ before Python regains control.

3. 300ms timestamp cooldown on `context.Select()` -- failed for the
   same reason: the second release arrives before Python can check
   the timestamp.

4. `QApplication.setDoubleClickInterval(1)` to suppress Qt's
   double-click detection entirely -- failed. Even with a 1ms
   double-click interval, two rapid physical clicks still fire two
   release events close enough together to trigger the crash.

**Why Python can't catch it:** A C++ segfault bypasses Python's
exception handling entirely. `try/except` around `context.Select()`
cannot catch a segmentation fault -- the process simply dies.

**Workaround:** Click deliberately and avoid rapid double-clicks in
the viewport. The crash only occurs with very rapid successive clicks.
Normal usage does not trigger it.

**All attempted fixes (all failed):**
1. `mouseDoubleClickEvent` setting `_press_pos = None`
2. `_ignore_next_click` flag checked in `mousePressEvent`
3. 300ms timestamp cooldown before `context.Select()`
4. `QApplication.setDoubleClickInterval(1)` to suppress double-click
5. `QTimer.singleShot(0, _do_select)` to defer to next event loop tick
6. `_selecting` boolean guard around `context.Select()`

All fail because the C++ segfault occurs inside `context.Select()`
itself -- the process dies before Python `finally` blocks or flags
can take effect. CPython is single-threaded so re-entrancy isn't
the issue; OCCT is simply crashing on its own internal state.

**Why CAD Assistant doesn't crash:** CAD Assistant uses OCCT's
`AIS_ViewController::HandleMouseButton()` high-level interface rather
than calling `context.Select()` directly. `AIS_ViewController` queues
and serializes selection events internally, so rapid clicks never
reach the C++ selection structures simultaneously.

**The proper long-term fix:** Refactor mouse handling in
`assembly_viewer.py` to use `AIS_ViewController` instead of calling
`context.Select()`, `view.StartRotation()` etc. directly. This is a
significant refactor but would fix this crash permanently and also
improve overall navigation robustness.

**Status: FIXED** via AIS_ViewController refactor.

**Root cause:** Calling `context.MoveTo()` and `context.Select()`
directly from Qt mouse events is not safe -- OCCT's selection
structures are not re-entrant. Rapid clicks, double-clicks, or mouse
movement during selection can cause a C++ segfault with no Python
traceback.

**The fix:** Replace all direct `context.MoveTo()` / `context.Select()`
calls with `AIS_ViewController`, which is OCCT's own high-level input
serialization layer (the same approach used by CAD Assistant).

**How AIS_ViewController works:**
1. Qt mouse events feed data to the controller:
     `_vc.UpdateMousePosition(pt, buttons, modifiers, isEmulated)`
     `_vc.UpdateMouseButtons(pt, buttons, modifiers, isEmulated)`
     `_vc.UpdateMouseScroll(Aspect_ScrollDelta)`
2. `_vc.FlushViewEvents(context, view, True)` processes all buffered
   events. OCCT handles rotate/pan/zoom/select/hover internally,
   serialized and re-entrancy-safe.
3. `OnSelectionChanged(ctx, view)` is called by OCCT when a selection
   completes -- override this method to call `_report_selection()`.

**Button constants** (plain ints in this OCP build, not an enum):
  `Aspect_VKeyMouse_LeftButton   = 8192`
  `Aspect_VKeyMouse_MiddleButton = 16384`
  `Aspect_VKeyMouse_RightButton  = 32768`

**Files changed:** `gui/assembly_viewer.py`
  - `AIS_ViewController` instance `_vc` added in `__init__`
  - `mousePressEvent`, `mouseMoveEvent`, `mouseReleaseEvent` replaced
  - `wheelEvent` uses `_vc.UpdateMouseScroll()` + `Aspect_ScrollDelta`
  - `_flush()` helper calls `FlushViewEvents(context, view, True)`
  - `OnSelectionChanged()` override routes to `_report_selection()`
  - `mouseDoubleClickEvent` swallows double-clicks cleanly

**Result:** Clicking, double-clicking, and rapid clicking no longer
crash the application. Mouse handling is now as robust as CAD Assistant.

---

## 21. Black Edge Display: Persistence After Operations

**Status: COMPLETE.**

**What was built:**
Black face boundary edges (`SetFaceBoundaryDraw`) are applied in
`_display_leaf` for all newly displayed parts. They give the model
a crisp technical illustration look.

**Bug: edges disappeared after cut/fillet/shell/move.**
`context.Redisplay(ais, False)` resets the AIS presentation including
the drawer attributes set by `SetFaceBoundaryDraw`. So after any
operation that calls Redisplay (cut, fillet, shell, part move), the
black edges were wiped from the affected part.

**Fix:** Added `_apply_black_edges(ais=None)` to `SyncedViewportWidget`:
- Called with an AIS arg: reapplies black edges to that one shape.
- Called with no args: reapplies to ALL `_ais_shapes` and calls
  `Redisplay` on each.

Called at the end of `_on_part_cut`, `_on_fillet_done`, and
`_on_shell_done` after all color restoration is complete -- ensuring
black edges are always the last thing applied.

**Also documented in this session:**
The viewport crash on mouse move (item 20) was traced to
`context.MoveTo()` with EDGE+VERTEX active on 18+ parts. The crash
has no Python-catchable fix. The proper long-term solution is to use
`AIS_ViewController` instead of calling `context.MoveTo()` directly.

---

## 22. Minor UI Improvements

**Status: COMPLETE.**

### RMB Rename in Assembly Tree
- Added "✏ Rename..." to the RMB context menu on all tree nodes.
- Pops a `QInputDialog` pre-filled with the current label.
- Updates both `node.label` and the displayed tree item text.
- Correctly preserves `► ` (active assembly) and `★ ` (active part)
  prefixes if the item is currently active.
- File: `gui/assembly_tree_widget.py`

### All Floating Dialogs Converted to QDialog
- `WorkplaneDialog`, `FilletDialog`, `ShellDialog` converted from
  `QDockWidget` to `QDialog` (matching `PositionDialog` which was
  converted earlier in item 18).
- Result: proper OS window decorations with full-size resize handles
  on all floating operation dialogs.
- Fixed `NameError: QDialog not defined` by adding `QDialog` to
  imports and removing leftover `setAllowedAreas`/`setFeatures`
  dock-widget-specific calls that don't exist on `QDialog`.

### Black Edges Persist After Operations
- `_apply_black_edges()` helper added to `SyncedViewportWidget`.
- Called after cut/fillet/shell operations to reapply
  `SetFaceBoundaryDraw` which `context.Redisplay()` resets.
- Also called after part moves so repositioned parts retain edges.

---

## 23. STEP Color Reading Fix

**Status: COMPLETE.** Parts now display their correct STEP-embedded
colors, matching CAD Assistant, Onshape, and KodaCAD.

**Symptom:** All parts displayed in randomized palette colors instead
of their correct STEP colors (bolts=blue, nuts=red, brackets=green,
plate=yellow). The fallback palette was being used for every part.

**Root cause investigation (three layers of failure):**

1. **`node.color.to_tuple()` doesn't exist.**
   The original code called `node.color.to_tuple()` but build123d's
   `Color` class has no such method. The `try/except` silently caught
   the `AttributeError` every time and fell back to the palette.
   `Color` only has two attributes: `wrapped` and `categorical_set`.

2. **`node.color.wrapped` is `Quantity_ColorRGBA`, not `Quantity_Color`.**
   The second attempt used `node.color.wrapped.Red()` but this OCP
   build wraps colors as `Quantity_ColorRGBA` (RGBA, not RGB). That
   class has no `.Red()` method directly -- another silent fallback.

3. **Correct chain: `.wrapped.GetRGB().Red/Green/Blue()`.**
   `Quantity_ColorRGBA.GetRGB()` returns a `Quantity_Color` (RGB),
   which then has `.Red()`, `.Green()`, `.Blue()` methods.

**The fix (one clean expression):**
```python
rgba = node.color.wrapped   # Quantity_ColorRGBA
rgb  = rgba.GetRGB()        # Quantity_Color
r, g, b = rgb.Red(), rgb.Green(), rgb.Blue()
```

**Confirmed working:** nuts=red (1,0,0), bolts=blue (0,0,1),
l-brackets=green (0,1,0), rod=orange, plate=yellow-green.
Colors now match all other CAD viewers that read the same STEP file.

**The colors were always in the file.** `check_colors.py` confirmed
build123d's `import_step()` reads them correctly -- they were present
on every node. The bug was entirely in how we extracted the RGB values
from the `Color` object.

**File changed:** `gui/assembly_viewer.py` -- `_display_leaf()` color
extraction.

---

## 24. Fillet/Shell/Cut Redisplay Position Fix

**Status: COMPLETE.** Fillet, shell, and cut results now display in
the correct assembled position.

### Root cause (took many attempts to find)

`BRepFilletAPI_MakeFillet` (and boolean ops like Cut/Fuse) **strip the
location tag** from their result. `mk.Shape()` always returns
`IsIdentity=True` even when the input shape had a non-identity location.

`build123d`'s `node.global_location` is computed as:
```python
reduce(lambda loc, n: loc * n.location, self.path, Location())
```
where `node.location = Location(node._wrapped.Location())`.

So when we store `node._wrapped = mk.Shape()` (identity location),
`node.location` becomes identity and `global_location` loses the node's
own rotation -- producing a completely wrong world position.

### Key discoveries along the way

- `TopoDS_Shape.Located()` REPLACES the location tag (confirmed by
  applying twice gives same result as once).
- `TopoDS_Shape.Location().IsIdentity()` returns True for `mk.Shape()`
  but the shape is NOT truly at origin -- the geometry coordinates are
  in the shape's LOCAL frame (after the node's own rotation is applied
  to the coordinates). The location tag is just stripped/missing.
- `BRepBuilderAPI_Transform`, `Move()`, and `Located()` all compound
  with the existing location rather than truly replacing it in the way
  we needed.
- `node.global_location` is derived from `_wrapped.Location()`, not
  from the tree hierarchy independently. Changing `_wrapped` changes
  `global_location`.

### The fix

Capture `node.global_location` and `node.location` BEFORE replacing
`_wrapped`. Compute the PARENT's world transform by removing the node's
own contribution:

```python
node_loc    = node.location
global_loc  = node.global_location
parent_global = Location(global_loc.wrapped.Multiplied(
                    node_loc.wrapped.Inverted()))
saved_global_location = parent_global
node._wrapped = new_shape  # identity-located result from MakeFillet
```

Then display with `override_location=saved_global_location`:
- `_display_leaf` now accepts `override_location` parameter
- New `display_node()` method passes `override_location` through
- `_on_fillet_done`, `_on_shell_done`, `_on_part_cut` all use this

Why parent's global location (not full global_location):
The fillet result geometry is already in the node's local coordinate
frame (with node's rotation applied to coordinates). Applying the full
`global_location` (which includes node's rotation) would double-apply
it. The parent's location provides only the parent-chain transforms,
which is exactly what's needed to position the node's local geometry
in world space.

### Edge matching fix (also in this session)

Fillet edge matching and shell face matching also needed the same
world-space treatment. Fixed by walking both `work_shape` (local,
for passing to `MakeFillet`) and `world_shape = work_shape.Located(gloc)`
(for midpoint comparison with world-space picked edges) in parallel.

### Shared vs. copied parts (known issue, item 25)

In KodaCAD (which uses XDE's STEP reference architecture), filleting
one l-bracket instance updates BOTH instances because they share the
same referred shape in the XDE document. Our implementation stores
`node._wrapped` per-node, so each instance is independent. The STEP
file has two l-bracket instances that share one referred shape -- we
currently treat them as independent copies. This is correct for
export but loses the shared-reference relationship. See item 25.

---

## 25. Shared vs. Copied Part Instances (FUTURE)

**Status: FUTURE INVESTIGATION.**

### The issue

STEP files use an XDE reference architecture where multiple instances
of the same part share a single referred shape at the document root.
The location of each instance is stored separately as a transform.

KodaCAD's `replace_shape()` modifies the referred shape at the document
root, so ALL instances that reference it are updated simultaneously.
This is the correct CAD behavior -- modifying a shared part updates
everywhere it appears.

Our implementation stores `node._wrapped` independently per node, so
each tree node holds its own copy of the geometry. Filleting one
l-bracket does not affect the other because they have separate `_wrapped`
objects even though the STEP file defined them as shared instances.

### What this means in practice

- Fillet/shell/cut on one instance does NOT update other instances ✗
- Each modification creates an independent copy of the part ✗
- Export to STEP will produce two separate shapes instead of one
  referred shape with two instances ✗

### Possible future fix

Adopt KodaCAD's XDE approach: use `XCAFDoc_DocumentTool_ShapeTool`
to maintain the STEP document structure, store shapes at root labels,
and apply `replace_shape()` to update all instances simultaneously.
This would require a significant refactor of `step_assembly_poc.py`
and how `node._wrapped` is managed.

Alternatively: detect shared instances on load (same referred shape
label in XDE), group them, and propagate modifications to all members
of each group.

---

## 26. Shared Instances: Modifications Create Copies (Known Limitation)

**Status: KNOWN LIMITATION -- documented, not hidden.**

### Background: what shared instances are

In a STEP file using XDE's reference architecture, multiple placements
of the same part share a single referred shape at the document root.
Each placement (instance) carries only a location transform. This is
the DRY principle applied to CAD: one definition, many placements.
The as1-oc-214.stp file has two l-bracket instances that share one
referred shape (confirmed: `IsSame()` returns True for both nodes).

### What happens when you modify a shared instance

**Current behavior:** When fillet, shell, or cut is applied to one
instance, that instance's `node._wrapped` is replaced with the
modified shape -- a new `TopoDS_Shape` with a new `TShape` pointer.
The modified instance is NO LONGER shared with the others. It has
become an independent copy.

The other instances are unaffected -- they still share the original
root shape and remain shared instances of each other.

So after filleting one l-bracket:
- The filleted l-bracket becomes an independent copy. ✗
- The other l-bracket remains a shared instance (of the original). ✓
- They are now two different parts, not two instances of one part. ✗
- Export to STEP will produce two separate shapes. ✗

### Why this happens

`import_step()` walks the XDE document and creates a Python node tree
where each instance gets its own `_wrapped` TopoDS_Shape object. The
underlying TShape pointer IS shared (IsSame=True), but `_wrapped` is
a separate Python object per node. Replacing `_wrapped` on one node
does not affect any other node.

### What the correct approach would be

In KodaCAD's XDE-aware architecture, the geometry is stored at a root
label (identity location). Modifying a part calls `replace_shape()`
on the ROOT label. Since all instances reference that root, they all
automatically reflect the change.

To do this correctly we would need to:
1. Maintain the XDE document structure through modifications
   (using `XCAFDoc_DocumentTool_ShapeTool.SetShape(root_label, ...)`).
2. Store root geometry and instance locations separately.
3. On display, apply each instance's location to the shared root geometry.

This is a significant refactor of `step_assembly_poc.py` and the node
model. It is deferred to a future session.

### Current workaround (NOT implemented -- see note below)

An earlier draft of this code added `get_shared_instances()` tracking
via `_tshape_to_nodes` and propagated modifications to all instances.
This was REMOVED because it masked the limitation rather than fixing it:
propagating a copy to all instances makes them all into independent
copies simultaneously, which is worse than the honest behavior of
making only the modified instance into a copy.

The correct user-visible behavior is: modify one instance → that
instance becomes a copy, others stay shared. This is what we have now.
Users should be aware that modifications break sharing.

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

---

## 28. Viewport Crash Fix: AIS_ViewController Refactor (COMPLETE)

**Status: COMPLETE.** See item 20 for full history. Fixed in this session.

Replaced direct `context.MoveTo()` / `context.Select()` calls with
`AIS_ViewController`, OCCT's own input serialization layer. All mouse
events (move, press, release, scroll) are now fed to the controller
via `UpdateMousePosition()`, `UpdateMouseButtons()`, `UpdateMouseScroll()`,
then processed by `FlushViewEvents(context, view, True)`. OCCT handles
rotate/pan/zoom/select/hover internally with no re-entrancy risk.

`OnSelectionChanged()` override routes OCCT's selection callback to
`_report_selection()`. Double-clicks are swallowed by
`mouseDoubleClickEvent()`.

Button constants (plain ints in this OCP build):
  Aspect_VKeyMouse_LeftButton   = 8192
  Aspect_VKeyMouse_MiddleButton = 16384
  Aspect_VKeyMouse_RightButton  = 32768

Result: clicking, double-clicking, and rapid clicking no longer crash
the application. Mouse handling is now as robust as CAD Assistant.

---

## 29. App Renamed to Basicad; Starts Without STEP File (COMPLETE)

**Status: COMPLETE.**

### Rename
Window title changed from `"cad1 -- {step_path}"` to `"Basicad"` /
`"Basicad -- {step_path}"`. Future: rename project directory from
`cad1/` to `basicad/` and update all internal references.

### Start without STEP file
`uv run gui/main_app.py` now works without a filename argument.
When no file is specified:
  - `load()` creates an empty root `Compound(label="/")` as the assembly.
  - The tree shows a single `/` root node.
  - The viewport initializes correctly (grey background, ViewCube visible).
  - `view.MustBeResized()` is called explicitly to fill the viewport.
  - Import STEP, Export STEP, Workplane, Fillet, Shell buttons all enabled.
  - `_on_import_clicked` and `_on_export_clicked` use `Path.home()` as
    the default directory when `self.step_path` is None.

This matches the CoCreate/Creo startup behavior: blank canvas, ready
for import or part creation.

---

## 30. Export/Import Cycle: Fix // Nesting Bug (COMPLETE)

**Status: COMPLETE.**

### Problem
Starting with an empty `/` root and importing a STEP file added the
imported assembly (e.g. `as1`) as a child of `/`. Exporting then wrote
`/` as the top-level node in the STEP file. Re-importing that file
added another `/` node under the existing `/`, giving `//as1`. Each
export/import cycle added another `/` level.

### Fix

**Export (_on_export_clicked):**
When the root has a single child, export that child directly instead
of the `/` wrapper:
```python
children = list(self._assembly.children)
if len(children) == 1:
    export_step(children[0], out_path)
else:
    export_step(self._assembly, out_path)
```
The exported STEP file now contains `as1` at the root, not `/`.

**Import (_on_import_clicked):**
If the imported node's label is `/` (an old exported file that still
has the wrapper), unwrap it and add its children directly:
```python
if new_node.label == '/':
    for child in list(new_node.children):
        add_node(child, target)
        self.viewport.display_subtree(child, ...)
        self.tree.add_node_to_tree(child, parent_node=target)
else:
    add_node(new_node, target)
    ...
```

### Result
Export/import cycle is now idempotent. The app can be closed and
reopened, the exported STEP file re-imported, and the session resumes
exactly where it left off with no extra hierarchy levels.
