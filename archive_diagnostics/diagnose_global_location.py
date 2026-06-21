"""
diagnose_global_location.py

The viewport fix using node.global_location made things BETTER (no
more collapsed/overlapping instances) but not CORRECT (instances are
present and distinct but not in their actual assembled positions). That
specific pattern -- present-but-wrong rather than collapsed-and-wrong
-- smells like a double-application of some transform component
(e.g. composing global_location on top of a shape that already
carries SOME location internally), but guessing at build123d's exact
.moved()/.located() semantics from documentation wasn't conclusive.

This script prints the ACTUAL numbers instead of guessing: for the
two 'nut' instances under rod-assembly (known, from earlier testing,
to be at different assembled positions), print:
    - node.location          (parent-relative, per build123d docs)
    - node.global_location    (the new property, supposedly world-absolute)
    - whether node.wrapped already has a non-identity embedded
      TopLoc_Location of its own, BEFORE any of our code touches it

That last check is the one most likely to explain the bug: if
node.wrapped ALREADY has some location baked in (e.g. from how
import_step()/load_assembly builds the Compound tree), then
ADDING global_location on top via .moved() would double-apply it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from step_assembly_poc import load_assembly  # noqa: E402


def describe_location(loc, label):
    print(f"  {label}:")
    try:
        pos = loc.position
        orient = loc.orientation
        print(f"    position    = {pos}")
        print(f"    orientation = {orient}")
    except Exception as e:
        print(f"    (could not read position/orientation: {e})")
    print(f"    repr        = {loc!r}")


def main():
    if len(sys.argv) < 2:
        print("Usage: diagnose_global_location.py <input.step>")
        return

    assembly = load_assembly(sys.argv[1])

    # Find every leaf node, depth-first, printing its location info.
    def walk(node, path):
        current_path = f"{path}/{node.label}" if node.label else path
        if not node.children:
            print(f"\n=== Leaf: {current_path} ===")
            describe_location(node.location, "node.location (parent-relative)")
            try:
                describe_location(node.global_location, "node.global_location")
            except AttributeError:
                print("  node.global_location: NOT AVAILABLE on this build123d version")

            # Check whether node.wrapped ITSELF already carries a
            # non-identity TopLoc_Location before we do anything to it.
            try:
                wrapped_loc = node.wrapped.Location()
                is_identity = wrapped_loc.IsIdentity()
                print(f"  node.wrapped.Location().IsIdentity() = {is_identity}")
                if not is_identity:
                    trsf = wrapped_loc.Transformation()
                    print(f"    (non-identity! TranslationPart = {trsf.TranslationPart()})")
            except Exception as e:
                print(f"  (could not inspect node.wrapped.Location(): {e})")
        else:
            for child in node.children:
                walk(child, current_path)

    walk(assembly, "")


if __name__ == "__main__":
    main()
