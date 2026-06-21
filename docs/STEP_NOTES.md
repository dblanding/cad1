# STEP Assembly Round-Trip — Proof of Concept

This is the single capability you said is the bar: **import a STEP
assembly, see its hierarchy, add/remove a part, export it again.**
Everything else in a CAD app is negotiable; this isn't.

**Status: confirmed working**, after finding and working around a
real bug in build123d. Full diagnosis below.

## Setup

```bash
uv add build123d
```

(or `pip install build123d` / `python -m venv` + `pip install`,
whichever you prefer — `uv` is what was actually used and confirmed
working.) On most platforms this pulls down prebuilt wheels for OCP
(the OpenCascade binding) with no compilation step.

## Running it

```bash
uv run step_assembly_poc.py                      # synthetic assembly, no file needed
uv run step_assembly_poc.py your_assembly.step    # a real STEP file
```

With no arguments, it builds a tiny 3-part assembly in memory,
exports it to STEP, re-imports it, removes one part, adds another,
and exports again — entirely self-contained. With a file argument, it
does the same against a real STEP assembly and writes
`<name>_modified.step` next to it.

## The bug, and the fix

`build123d.export_step()` fails on **any** shape returned by
`import_step()` — even with zero mutation in between — raising
`RuntimeError("Failed to write STEP file")` (`IFSelect_RetVoid`
under the hood, OCCT's "nothing done" status). Freshly-built
build123d geometry exports fine, every time; re-imported geometry
never did, on the tested install (`build123d` + `cadquery-ocp` via
`uv add build123d`, mid-2026).

**Root cause:** `import_step()` returns a `Compound` whose `.parent`
is a synthetic, invisible outer wrapper — not `None`, despite every
other property (`.children`, `.descendants`, `.label`) making it
look like a clean tree root. `export_step()`'s internal
`_create_xde()` walks the shape tree with `anytree.PreOrderIter` and
checks `getattr(node, "parent", None)` for every node, including the
very first one it visits. Because the "root" handed back by
`import_step()` isn't actually parentless, `_create_xde()` treats it
as a child needing a parent label that was never registered — every
single node (root and all descendants) gets silently skipped via an
internal `continue` guard, producing a validly-constructed but
**empty** XCAF document. The writer then reports "nothing done" with
no exception and no diagnostic message anywhere in the pipeline,
which is what made this hard to track down.

**The fix is one line:** sever that spurious parent reference before
exporting.

```python
assembly = import_step("input.step")
assembly.parent = None     # work around the import_step() bug
export_step(assembly, "output.step")
```

`src/step_export_fix.py` wraps this into a drop-in `export_step()`
replacement that calls the real, unmodified `build123d.export_step()`
— full `SetColorMode`/`SetLayerMode`/`SetNameMode`, real STEP
headers, no feature loss — with the one-line fix applied
automatically first. `step_assembly_poc.py` already imports and uses
it; you don't need to think about this elsewhere in your code as
long as you import `export_step` from `step_export_fix` instead of
from `build123d` directly.

### How this was found

By elimination, across about a dozen tests, each one ruling out a
specific hypothesis with real evidence rather than assumption:
not `remove_part`/`add_part`, not null/invalid shapes, not color,
not "calling export_step twice in a process" (fresh geometry
round-trips fine), not any combination of
`SetColorMode`/`SetLayerMode`/`SetNameMode`, not the writer's
constructor form, not STEP header construction (an earlier, *wrong*
theory — see below), not even `_create_xde()`'s document-building
logic *in general*. The actual breakthrough came from monkey-patching
the real OCCT calls (`AddShape`/`AddComponent`) to log every
invocation before calling build123d's real, unmodified
`_create_xde()` — which showed **zero** calls to either, meaning the
per-node loop never executed its body at all. That pointed at the
tree traversal itself, and printing `reimported.parent` directly
(something no earlier diagnostic had checked, since they all only
walked *downward* via `.children`) revealed the invisible wrapper
immediately.

**A note on an earlier wrong turn**, for honesty's sake: midway
through this investigation, isolating STEP header construction
appeared to reproduce the bug, leading to a (wrong) conclusion that
header construction was the cause, and a workaround that skipped it.
A follow-up test — removing *only* the header while keeping the rest
of the real writer pipeline — also failed, which disproved that
theory. The header-construction result had been a false positive,
most likely because nothing in `_create_xde()` was actually adding
shapes to the document in either test, for the real reason described
above. If you read through `archive_diagnostics/` (every diagnostic
script from this investigation, kept for reference), you'll see this
dead end recorded honestly rather than scrubbed out.

### Worth reporting upstream

This looks like a genuine, reportable bug in `build123d`'s
`import_step()`/`export_step()` interaction. `archive_diagnostics/
diagnose_strip_parent.py` is close to a ready-made minimal repro:
import any STEP file, print `.parent` on the result (non-`None`,
unexpectedly), then show that setting it to `None` before
`export_step()` fixes a reproducible `RetVoid` failure. Worth filing
against `gumyr/build123d` if you have a moment — this seems likely to
bite other people round-tripping STEP assemblies.

## Where this is likely to get interesting next

Now that the core round-trip works, expect friction in roughly this
order as you move to more complex real-world files:

- **Multiply-referenced parts** (e.g. the same bolt used 12 times).
  STEP represents this as one shape referenced by many placements
  (`NAUO` instances in AP214 terms). Worth testing on a file with a
  repeated fastener early.
- **Assembly metadata beyond name/color** — layers, custom
  properties, PMI/GD&T annotations. `import_step()` pulls name and
  color via XCAFDoc's `ColorTool`/`TDataStd_Name`; other XCAFDoc
  attribute tables aren't necessarily walked by the high-level call.
  If you need those, that's a place to drop to raw OCP/XCAFDoc calls.
- **Very large assemblies** — hundreds/thousands of parts. The
  recursive Python-level tree walk here is fine for moderate
  assemblies; profile before assuming it's fast enough for an
  interactive GUI tree widget at scale.
- **Units.** STEP files can be in inches, mm, or other units;
  `export_step()` has a `unit` parameter and `import_step()` respects
  what's in the file. Worth an explicit test with a non-mm source
  file rather than assuming.

## A note on shared vs. copied assemblies (read before being surprised later)

STEP/XCAF supports true "shared instance" semantics at any tree level
-- HP's ME-30/SolidDesigner called this "shared" vs. "copied"; OCCT's
own XDE docs use "Instance" (a replication referencing one underlying
shape) vs. "Shape" (standalone). Confirmed directly: in CAD Assistant
(an OCCT-based viewer), selecting and deleting ONE `nut-bolt-assembly`
out of six occurrences in `as1-oc-214.stp` removed all six -- strong,
independently-sourced evidence that the FILE genuinely encodes shared
container-level structure, and that OCCT's own tooling reads it
faithfully.

What we found, separately: `build123d`'s `import_step()` reconstructs
an independent Python `Compound` object per occurrence at the
container/sub-assembly level, even when the underlying TShape data is
shared (confirmed: leaf-level solids DO show shared TShape identity
on this file; container-level Compounds do not). This appears to be
specific to how `import_step()` builds its Python object tree, not a
limitation of OCCT or the STEP format itself -- a native build123d
control test (two `.located()` instances of one source shape, no STEP
involved) also showed independent TShapes, so don't assume
`.located()`/`.moved()` give you sharing either.

**Decision: we're not chasing full shared-instance semantics right
now** -- it would mean bypassing build123d's `Compound`/`import_step()`
abstraction and working with OCCT's `XCAFDoc_ShapeTool`/`TDF_Label`
reference structure directly, a meaningfully bigger scope than
anything else built so far. Our tree-editing functions
(`remove_part`/`add_part`/`remove_node`/`add_node`) all operate on
independent per-occurrence Compound objects and have no awareness of
underlying shared geometry.

**Stay alert, don't assume it's settled.** If something behaves
surprisingly later -- a delete affecting more than expected, an edit
showing up somewhere it shouldn't, an export looking different from
what was built -- container-level sharing goes on the short list of
explanations to check, not something already ruled out. The
diagnostic scripts in `archive_diagnostics/` (`diagnose_shared_instances.py`
in particular) are the place to start re-checking if this comes up.

## How this maps onto the eventual GUI

Once `step_assembly_poc.py` works reliably on real files from your
target CAD systems, the GUI layer is mostly wiring:

- `load_assembly()` → populate a `QTreeWidget` from the Compound tree
  (walk `.children` recursively, same as `print_tree()` does).
- Tree item right-click → "Delete" → `remove_part()`.
- Tree item right-click → "Add primitive..." or "Import part..." →
  `add_part()`.
- "Export STEP..." menu action → `save_assembly()` (which now uses
  the fixed `export_step()` automatically).
- 3D viewport: tessellate each leaf shape (`shape.tessellate()` in
  build123d/OCP terms) and hand the triangles to whatever you're
  rendering with (raw OpenGL via PySide6's QOpenGLWidget, or embed
  the `three-cad-viewer`/`ocp_vscode` web component if you'd rather
  not write viewport code by hand).

The point of doing it in this order is that the tree logic and STEP
I/O — the part you got stuck on years ago with PythonOCC — is now
proven solid, debugged without a GUI event loop and a 3D viewport
also in the mix.
