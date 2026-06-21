# Design Backlog

Running list of design threads that are open, named, and deliberately
not yet built -- captured here so they don't get lost between
sessions. Add to this as new threads come up; move items to the
relevant area's own doc (or into actual code) once they're resolved.

---

## 1. Position / Mate-Align workflow (HP/CoCreate pattern)

**Status:** designed in conversation, not yet built. `pose.py`'s
`Plane`/`compute_move()` math is the proven foundation underneath
this; what's missing is the layer above it.

**The reference UX** (from CoCreate / PTC Creo Elements/Direct
Modeling, which Doug has used and wants to pattern this after):

![HP/CoCreate Position dialog](imgs/hp-position-dialog.png)

- A persistent **Position** dialog, not a one-shot pick-everything
  flow. Buttons: Direct / Selected, a Methods panel (`Dyn Pos`,
  `Mate Align`, `Two Points`, `Dimension`), and under Mate Align:
  `Mate`, `Align`, `Align Axis`, with `Offset` and `Reverse`.
- **Mate**: two faces coplanar, normals OPPOSED (face-to-face contact).
- **Align**: two faces/elements coplanar, normals SAME direction
  (flush).
- **Align Axis**: centers the axes of two cylindrical/circular
  elements (maps onto `pose.py`'s existing `circle_axis`
  `DirectionRef` kind).
- Each Mate/Align/Align-Axis operation is **partial** -- it resolves
  *some* DOF, not all 6. The user applies several in sequence
  ("repeat these steps using multiple faces to fully lock a part's
  degrees of freedom") until the part is fully constrained, or
  leaves some DOF free on purpose.
- Still **one-shot** overall, consistent with Doug's earlier decision
  (no live constraint solver) -- but the INPUT is incremental even
  though the OUTPUT is a single committed transform once the user is
  satisfied.
- Position is invoked **as part of the same flow that creates a part
  instance**, not as a separate, later command. Confirmed via a
  second CoCreate screenshot: the "Create Copy" dialog has `Dyn Pos`
  / `Mate Align` method buttons built directly into it, with an
  embedded, collapsible "Position" section -- copy-or-share and
  positioning happen as one continuous operation, not two.

**Open design question, not yet decided:** does each individual
Mate/Align/Align-Axis pick commit immediately (part visibly moves
after every single constraint), or do picks accumulate across
multiple selections with the part only actually moving once, on a
final Apply? The CoCreate UI (persistent dialog, `Back`/`Apply Prev`
buttons, not close-after-one-pick) suggests the former. Worth
deciding deliberately before building the accumulator layer.

**What this implies for `pose.py`:** needs a new layer above
`plane_from_picks()` -- something like an incremental constraint
accumulator that starts from a part's current `Location` and narrows
it with each Mate/Align/Align-Axis operation, rather than requiring a
full 3-2-1 pick in one pass (Mate/Align as currently designed assume
a complete `from_plane`/`to_plane` up front).

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

## How to use this doc

When picking a backlog item up: read its section here first, then
check whether anything in the wider conversation history /
`archive_diagnostics/` is relevant before starting fresh. Move
resolved items into the appropriate permanent doc (`STEP_NOTES.md`,
`VIEWPORT_NOTES.md`, or a new one) once they're actually built, rather
than leaving stale "status: open" text here.
