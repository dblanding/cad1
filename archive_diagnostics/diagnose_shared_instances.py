"""
diagnose_shared_instances.py

Doug's drag-and-drop test surfaced a real question: when as1-oc-214.stp
has TWO l-bracket-assembly occurrences (per the raw STEP file, this is
encoded as a NEXT_ASSEMBLY_USAGE_OCCURRENCE -- OCCT's own XDE docs
call this an "Instance": "a replication of another shape with a
location that can be the same location or a different one" --
contrasted with "Shape: a standalone shape, which does not belong to
the assembly structure"), does build123d's import_step() preserve
that as TWO REFERENCES TO ONE SHARED PROTOTYPE, or does it silently
become two INDEPENDENT COPIES the moment Python objects exist?

This matters enormously for whether "edit one instance, see the
change in all instances" is even possible without bypassing
build123d's Compound abstraction and going straight to XCAFDoc calls.

WHAT THIS CHECKS, empirically rather than by guessing:
    1. Do the two l-bracket-assembly nodes' LEAF SOLIDS share the same
       underlying TopoDS_TShape (the actual geometric data, ignoring
       Location) -- i.e. same `.wrapped.TShape()` identity/equality?
       This is the real test for "shared prototype" at the OCCT level,
       independent of whatever Python object identity build123d
       happens to use.
    2. Do the two l-bracket-assembly Compound NODES share the same
       Python object identity, or even the same .wrapped TopoDS
       object identity? (Less likely to be true given Doug's test
       result, but worth checking directly rather than assuming.)
    3. For comparison: build TWO instances of a synthetic
       build123d-native part (no STEP round-trip at all) using the
       SAME source Solid placed at two different Locations, the
       documented/intended way to do instancing in build123d itself,
       and check whether THAT shares a TShape. This tells us whether
       sharing is something build123d/OCCT does at all, even before
       STEP enters the picture -- isolating "does this concept exist"
       from "does import_step() preserve it."
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from step_assembly_poc import load_assembly  # noqa: E402

from build123d import Box


def tshape_identity(shape):
    """
    Return something we can compare for equality/identity that
    represents the underlying geometric data, INDEPENDENT of
    Location. OCCT's TopoDS_Shape.TShape() returns the handle to the
    underlying topology data (TopoDS_TShape) -- two TopoDS_Shape
    objects with different Locations but the SAME TShape() are, by
    OCCT's own definition, sharing the same underlying geometry (this
    is literally the mechanism XCAF "Instance" semantics rely on).
    """
    try:
        tshape = shape.wrapped.TShape()
        return id(tshape), tshape
    except Exception as e:
        return f"<error: {e}>", None


def main():
    if len(sys.argv) < 2:
        print("Usage: diagnose_shared_instances.py <input.step>")
        return

    print("=== Part 1: checking the two l-bracket-assembly instances ===\n")
    assembly = load_assembly(sys.argv[1])

    l_bracket_assemblies = [c for c in assembly.children if c.label == "l-bracket-assembly"]
    print(f"Found {len(l_bracket_assemblies)} 'l-bracket-assembly' nodes at the top level.\n")

    if len(l_bracket_assemblies) < 2:
        print("Expected 2 -- something about the tree structure may have")
        print("changed, or this isn't the as1-oc-214.stp file. Stopping.")
        return

    asm_a, asm_b = l_bracket_assemblies[0], l_bracket_assemblies[1]

    print(f"Compound node Python identity: id(asm_a)={id(asm_a)}  id(asm_b)={id(asm_b)}")
    print(f"  Same Python object? {asm_a is asm_b}")

    try:
        wrapped_id_a, wrapped_id_b = id(asm_a.wrapped), id(asm_b.wrapped)
        print(f".wrapped Python identity: {wrapped_id_a} vs {wrapped_id_b}  "
              f"(same? {asm_a.wrapped is asm_b.wrapped})")
    except Exception as e:
        print(f"(could not compare .wrapped: {e})")

    # CONTAINER-LEVEL sharing test: does the l-bracket-assembly
    # COMPOUND NODE ITSELF have a shared underlying TShape between the
    # two top-level occurrences? This is a SEPARATE question from
    # whether the LEAF solids inside it are shared (tested further
    # below) -- per Doug's point, an assembly container being a "copy"
    # and its CONTENTS being "shared" are independent, coexisting
    # facts, not one combined fact. XCAF's Instance/Shape mechanism
    # applies uniformly to assemblies (TopoDS_COMPOUND, itself a
    # TopoDS_Shape) and to leaf solids alike -- there's no structural
    # reason container-level sharing couldn't exist independently of
    # leaf-level sharing.
    print("\n--- Container-level check (the l-bracket-assembly NODE itself) ---")
    container_id_a, container_tshape_a = tshape_identity(asm_a)
    container_id_b, container_tshape_b = tshape_identity(asm_b)
    print(f"l-bracket-assembly container TShape identity: {container_id_a} vs {container_id_b}")
    if container_tshape_a is not None and container_tshape_b is not None:
        try:
            print(f"  container_tshape_a.IsSame(container_tshape_b)? "
                  f"{container_tshape_a.IsSame(container_tshape_b)}")
        except Exception as e:
            print(f"  (could not call IsSame: {e})")

    # INTERMEDIATE-level check: the nut-bolt-assembly sub-assemblies.
    # Each l-bracket-assembly contains 3 of these -- checking sharing
    # at this middle layer too, not just top (assembly) and bottom
    # (leaf solid), to get the full picture across the hierarchy
    # rather than just two data points that happen to bookend it.
    print("\n--- Intermediate-level check (nut-bolt-assembly sub-assemblies) ---")
    nba_a_list = [c for c in asm_a.children if c.label == "nut-bolt-assembly"]
    nba_b_list = [c for c in asm_b.children if c.label == "nut-bolt-assembly"]
    if nba_a_list and nba_b_list:
        nba_a, nba_b = nba_a_list[0], nba_b_list[0]
        nba_id_a, nba_tshape_a = tshape_identity(nba_a)
        nba_id_b, nba_tshape_b = tshape_identity(nba_b)
        print(f"nut-bolt-assembly[0] TShape identity (asm_a's vs asm_b's): "
              f"{nba_id_a} vs {nba_id_b}")
        if nba_tshape_a is not None and nba_tshape_b is not None:
            try:
                print(f"  IsSame? {nba_tshape_a.IsSame(nba_tshape_b)}")
            except Exception as e:
                print(f"  (could not call IsSame: {e})")
        # ALSO check sharing between the 3 SIBLING nut-bolt-assembly
        # instances WITHIN the same l-bracket-assembly (asm_a) -- a
        # third, again-independent question: are repeated SIBLINGS
        # under ONE parent shared with each other?
        if len(nba_a_list) >= 2:
            sib_id_0, sib_tshape_0 = tshape_identity(nba_a_list[0])
            sib_id_1, sib_tshape_1 = tshape_identity(nba_a_list[1])
            print(f"\nWithin asm_a, nut-bolt-assembly[0] vs [1] (sibling check): "
                  f"{sib_id_0} vs {sib_id_1}")
            if sib_tshape_0 is not None and sib_tshape_1 is not None:
                try:
                    print(f"  IsSame? {sib_tshape_0.IsSame(sib_tshape_1)}")
                except Exception as e:
                    print(f"  (could not call IsSame: {e})")
    else:
        print("Could not find nut-bolt-assembly children under both -- skipping.")

    # The real test: compare the LEAF l-bracket solids' underlying
    # TShape -- this is what actually determines whether OCCT
    # considers them the same geometric data or independent copies.
    leaf_a = next((c for c in asm_a.descendants if c.label == "l-bracket"), None)
    leaf_b = next((c for c in asm_b.descendants if c.label == "l-bracket"), None)

    if leaf_a is None or leaf_b is None:
        print("\nCould not find both 'l-bracket' leaf solids -- stopping.")
        return

    id_a, tshape_a = tshape_identity(leaf_a)
    id_b, tshape_b = tshape_identity(leaf_b)
    print(f"\nLeaf 'l-bracket' TShape identity: {id_a} vs {id_b}")
    if tshape_a is not None and tshape_b is not None:
        try:
            same_tshape = tshape_a.IsSame(tshape_b)
            print(f"  tshape_a.IsSame(tshape_b)? {same_tshape}")
        except Exception as e:
            print(f"  (could not call IsSame: {e})")

    print("\n" + "=" * 70)
    print("=== Part 2: control test -- does build123d/OCCT support")
    print("    sharing AT ALL, independent of STEP import? ===\n")

    # Build ONE solid, then create two "instances" of it the
    # build123d-native way (same source shape, two different
    # Locations) -- NOT two separate Box(...) calls, which WOULD be
    # independent copies by construction. This isolates "is sharing
    # possible in this stack at all" from "does import_step()
    # specifically preserve it."
    from build123d import Location
    source = Box(10, 10, 10)
    instance_1 = source.located(Location((0, 0, 0)))
    instance_2 = source.located(Location((50, 0, 0)))

    id1, tshape1 = tshape_identity(instance_1)
    id2, tshape2 = tshape_identity(instance_2)
    print(f"Native build123d .located() instances -- TShape identity: {id1} vs {id2}")
    if tshape1 is not None and tshape2 is not None:
        try:
            print(f"  tshape1.IsSame(tshape2)? {tshape1.IsSame(tshape2)}")
        except Exception as e:
            print(f"  (could not call IsSame: {e})")

    print("\n--- Conclusion ---")
    print("Sharing must be read as MULTIPLE INDEPENDENT facts, one per")
    print("tree level checked above, not a single yes/no answer:")
    print("  - Container-level (l-bracket-assembly itself): are the two")
    print("    top-level occurrences the SAME underlying assembly")
    print("    prototype, or independent copies that happen to look alike?")
    print("  - Intermediate-level (nut-bolt-assembly): same question, one")
    print("    layer down -- both ACROSS the two l-bracket-assemblies and")
    print("    AMONG the 3 siblings within just one of them.")
    print("  - Leaf-level (l-bracket solid): is the actual geometric data")
    print("    shared, regardless of what's true at the levels above it.")
    print()
    print("Realistic, coherent outcomes include things like 'containers are")
    print("copies, but their leaf solids are shared' -- per Doug's point,")
    print("these are genuinely independent facts, not one combined one.")
    print()
    print("If Part 2 (the native build123d .located() control test) shows")
    print("TRUE sharing where Part 1 shows FALSE at the same tree level,")
    print("that isolates the gap to import_step()'s reconstruction specifically")
    print("-- meaning sharing IS achievable in this stack, just not preserved")
    print("automatically on STEP import; we'd need either a custom XCAF-level")
    print("import path, or to rely on build123d-native instancing for parts")
    print("created within the app itself.")


if __name__ == "__main__":
    main()
