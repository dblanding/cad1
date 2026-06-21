"""
diagnose_preorderiter.py

The traced run of the REAL _create_xde() showed something decisive:
ZERO calls to AddShape or AddComponent -- only UpdateAssemblies()
fired. That means `for node in PreOrderIter(to_export): ...` never
executed its body, for ANY node, not even the root.

But every earlier diagnostic in this investigation walked the SAME
re-imported Compound's .children/.descendants successfully (4 nodes:
root + 3 children, every time, reliably). So the tree data is fine --
something is specific to anytree's PreOrderIter not seeing it the
same way, or to _create_xde() not actually being called the way we
think.

This script isolates that precisely: call anytree.PreOrderIter
directly (the exact same import build123d's exporters3d.py uses) on
the SAME re-imported object, with NO _create_xde() involved at all,
and compare its result against .descendants (which has worked
reliably throughout this investigation).
"""

import sys
from pathlib import Path

from build123d import import_step
from anytree import PreOrderIter


def main():
    if len(sys.argv) < 2:
        print("Usage: diagnose_preorderiter.py <input.step>")
        return

    step_path = Path(sys.argv[1])
    reimported = import_step(str(step_path))
    print(f"Imported {step_path}.\n")

    print(f"type(reimported) = {type(reimported)}")
    print(f"reimported.label = {reimported.label!r}")
    print(f"reimported.children = {reimported.children!r}")
    print(f"len(reimported.children) = {len(reimported.children)}")
    print(f"reimported.parent = {reimported.parent!r}")
    print(f"reimported.is_root (if available) = "
          f"{getattr(reimported, 'is_root', 'N/A')!r}")

    print("\n--- Using .descendants (known to work reliably so far) ---")
    try:
        descendants = list(reimported.descendants)
        print(f"len(reimported.descendants) = {len(descendants)}")
        for d in descendants:
            print(f"  - {d.label!r} ({type(d).__name__})")
    except Exception as e:
        print(f"reimported.descendants raised: {type(e).__name__}: {e}")

    print("\n--- Using anytree.PreOrderIter directly (what _create_xde uses) ---")
    try:
        preorder_nodes = list(PreOrderIter(reimported))
        print(f"len(list(PreOrderIter(reimported))) = {len(preorder_nodes)}")
        for n in preorder_nodes:
            print(f"  - {n.label!r} ({type(n).__name__})  wrapped_is_None={n.wrapped is None}")
    except Exception as e:
        print(f"PreOrderIter(reimported) raised: {type(e).__name__}: {e}")

    print("\n--- Conclusion ---")
    try:
        n_preorder = len(list(PreOrderIter(reimported)))
    except Exception:
        n_preorder = -1
    n_descendants_plus_root = len(list(reimported.descendants)) + 1

    if n_preorder == 0:
        print("CONFIRMED: PreOrderIter(reimported) yields ZERO nodes, even")
        print("though .descendants works fine. This explains EVERYTHING --")
        print("_create_xde()'s loop never runs because anytree's own")
        print("PreOrderIter doesn't see this object as a valid tree root,")
        print("despite .children/.descendants working via build123d's own")
        print("(possibly different) traversal implementation.")
        print("\nThis would mean build123d's Shape class isn't fully wired")
        print("into anytree's NodeMixin machinery for objects that came")
        print("from import_step() specifically -- e.g. .children might be")
        print("a build123d-level property that doesn't go through anytree's")
        print("expected internal attribute (often _NodeMixin__children or")
        print("similar), which would make anytree's iterator find nothing")
        print("while build123d's own .children/.descendants properties")
        print("(which may not even USE anytree under the hood despite the")
        print("documentation saying so) work fine.")
    elif n_preorder == n_descendants_plus_root:
        print(f"PreOrderIter yielded {n_preorder} nodes, matching .descendants")
        print("+ root. That means PreOrderIter itself is fine here -- which")
        print("would be a genuine surprise given the traced _create_xde()")
        print("run showed zero AddShape/AddComponent calls. Worth re-running")
        print("diagnose_real_create_xde_traced.py once more to see if that")
        print("result reproduces, since this would directly contradict it.")
    else:
        print(f"PreOrderIter yielded {n_preorder} nodes; .descendants+root")
        print(f"would suggest {n_descendants_plus_root}. Partial mismatch --")
        print("worth looking at exactly which nodes are missing from the")
        print("PreOrderIter list above vs the .descendants list above it.")


if __name__ == "__main__":
    main()
