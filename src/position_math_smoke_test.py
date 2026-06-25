"""
position_math_smoke_test.py

Verifies the new 3-2-1 positioning math in pose.py.

Run with:
  uv run src/position_math_smoke_test.py
"""

import sys
import os
import math

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'gui'))

from build123d import Vector, Location, Vertex
from pose import (
    find_intersection_line,
    compute_step1_move,
    compute_step2_move,
    compute_step3_move,
)
from position_dialog import PickResult
from OCP.TopAbs import TopAbs_FACE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def vec_close(v1: Vector, v2: Vector, tol=1e-3) -> bool:
    return (v1 - v2).length < tol

def apply_to_point(move: Location, p: Vector) -> Vector:
    """Apply a Location to a point using build123d's Vertex.moved()."""
    v = Vertex(p.X, p.Y, p.Z)
    return v.moved(move).center()

def apply_to_dir(move: Location, d: Vector) -> Vector:
    """
    Apply a Location to a direction by transforming two points and
    computing the difference (rotation only, no translation effect).
    """
    origin = apply_to_point(move, Vector(0, 0, 0))
    tip    = apply_to_point(move, d.normalized())
    return (tip - origin).normalized()

def make_pick(point, direction=None):
    return PickResult(
        shape_type=TopAbs_FACE,
        raw_shape=None,
        point=Vector(*point) if not isinstance(point, Vector) else point,
        direction=Vector(*direction).normalized() if direction else None,
        label="test"
    )

n_pass = 0
n_fail = 0

def check(name, condition, detail=""):
    global n_pass, n_fail
    if condition:
        print(f"  PASS  {name}")
        n_pass += 1
    else:
        print(f"  FAIL  {name}  {detail}")
        n_fail += 1


# ---------------------------------------------------------------------------
# Test 1: find_intersection_line -- perpendicular faces (XY and XZ planes)
# ---------------------------------------------------------------------------
print("\n--- Test 1: find_intersection_line, perpendicular faces ---")

result = find_intersection_line(
    Vector(0, 0, 0), Vector(0, 0, 1),
    Vector(0, 0, 0), Vector(0, 1, 0)
)
check("returns result (not None)", result is not None)
if result:
    pt, dir_ = result
    check("direction is X axis (or -X)", abs(dir_.dot(Vector(1,0,0))) > 0.999,
          f"got dir={dir_}")
    check("point on plane 1 (z≈0)", abs(pt.Z) < 1e-4, f"z={pt.Z}")
    check("point on plane 2 (y≈0)", abs(pt.Y) < 1e-4, f"y={pt.Y}")


# ---------------------------------------------------------------------------
# Test 2: find_intersection_line -- parallel faces (should return None)
# ---------------------------------------------------------------------------
print("\n--- Test 2: find_intersection_line, parallel faces ---")

result = find_intersection_line(
    Vector(0, 0, 0), Vector(0, 0, 1),
    Vector(0, 0, 5), Vector(0, 0, 1)
)
check("returns None for parallel faces", result is None)


# ---------------------------------------------------------------------------
# Test 3: find_intersection_line -- offset perpendicular faces
# ---------------------------------------------------------------------------
print("\n--- Test 3: find_intersection_line, offset perpendicular faces ---")

result = find_intersection_line(
    Vector(0, 0, 0), Vector(0, 0, 1),
    Vector(3, 0, 0), Vector(1, 0, 0)
)
check("returns result", result is not None)
if result:
    pt, dir_ = result
    check("direction is Y axis (or -Y)", abs(dir_.dot(Vector(0,1,0))) > 0.999,
          f"got dir={dir_}")
    check("point has x≈3", abs(pt.X - 3) < 1e-4, f"x={pt.X}")
    check("point has z≈0", abs(pt.Z) < 1e-4, f"z={pt.Z}")


# ---------------------------------------------------------------------------
# Test 4: compute_step1_move -- Mate, faces at 90 degrees
# ---------------------------------------------------------------------------
print("\n--- Test 4: compute_step1_move, Mate, 90-degree faces ---")

pick1 = make_pick((0, 0, 0), (0, 0, 1))   # moving: Z face at origin
pick2 = make_pick((5, 0, 0), (1, 0, 0))   # fixed:  X face at x=5

move = compute_step1_move(pick1, pick2, mate=True)
check("move is not None", move is not None)

if move is not None:
    new_N1 = apply_to_dir(move, Vector(0, 0, 1))
    check("moving normal now opposes fixed normal (-X)",
          vec_close(new_N1, Vector(-1, 0, 0)),
          f"new_N1={new_N1}")


# ---------------------------------------------------------------------------
# Test 5: compute_step1_move -- Align, faces at 90 degrees
# ---------------------------------------------------------------------------
print("\n--- Test 5: compute_step1_move, Align, 90-degree faces ---")

pick1 = make_pick((0, 0, 0), (0, 0, 1))
pick2 = make_pick((5, 0, 0), (1, 0, 0))

move = compute_step1_move(pick1, pick2, mate=False)
check("move is not None", move is not None)

if move is not None:
    new_N1 = apply_to_dir(move, Vector(0, 0, 1))
    check("moving normal aligns with fixed normal (+X)",
          vec_close(new_N1, Vector(1, 0, 0)),
          f"new_N1={new_N1}")


# ---------------------------------------------------------------------------
# Test 6: compute_step1_move -- parallel faces, translation only
# ---------------------------------------------------------------------------
print("\n--- Test 6: compute_step1_move, parallel faces (translation) ---")

pick1 = make_pick((0, 0, 0),  (0, 0,  1))
pick2 = make_pick((0, 0, 10), (0, 0, -1))  # Mate: opposed normals

move = compute_step1_move(pick1, pick2, mate=True)
check("move is not None", move is not None)

if move is not None:
    new_N1 = apply_to_dir(move, Vector(0, 0, 1))
    check("normal unchanged after translation",
          vec_close(new_N1, Vector(0, 0, 1)),
          f"new_N1={new_N1}")
    new_P1 = apply_to_point(move, Vector(0, 0, 0))
    check("point translated to z=10",
          abs(new_P1.Z - 10) < 1e-3,
          f"new_P1={new_P1}")


# ---------------------------------------------------------------------------
# Test 7: compute_step1_move -- already flush (near-identity)
# ---------------------------------------------------------------------------
print("\n--- Test 7: compute_step1_move, already flush ---")

pick1 = make_pick((0, 0, 5), (0, 0,  1))
pick2 = make_pick((0, 0, 5), (0, 0, -1))  # same plane, opposed

move = compute_step1_move(pick1, pick2, mate=True)
check("move is not None", move is not None)

if move is not None:
    new_P = apply_to_point(move, Vector(1, 2, 5))
    check("no translation for already-flush faces",
          vec_close(new_P, Vector(1, 2, 5)),
          f"new_P={new_P}")


# ---------------------------------------------------------------------------
# Test 8: compute_step2_move -- basic in-plane translation
# ---------------------------------------------------------------------------
print("\n--- Test 8: compute_step2_move, in-plane translation ---")

mated_N = Vector(0, 0, 1)
pick1 = make_pick((2, 3, 5))
pick2 = make_pick((7, 3, 5))

move = compute_step2_move(pick1, pick2, mated_N)
check("move is not None", move is not None)

if move is not None:
    new_P = apply_to_point(move, Vector(2, 3, 5))
    check("X translated by 5", abs(new_P.X - 7) < 1e-3, f"new_P={new_P}")
    check("Y unchanged",        abs(new_P.Y - 3) < 1e-3, f"new_P={new_P}")
    check("Z unchanged",        abs(new_P.Z - 5) < 1e-3, f"new_P={new_P}")


# ---------------------------------------------------------------------------
# Test 9: compute_step2_move -- normal component stripped
# ---------------------------------------------------------------------------
print("\n--- Test 9: compute_step2_move, strips normal component ---")

mated_N = Vector(0, 0, 1)
pick1 = make_pick((0, 0, 0))
pick2 = make_pick((3, 4, 99))  # Z=99 should be stripped

move = compute_step2_move(pick1, pick2, mated_N)
check("move is not None", move is not None)

if move is not None:
    new_P = apply_to_point(move, Vector(0, 0, 0))
    check("X translated by 3",          abs(new_P.X - 3) < 1e-3, f"new_P={new_P}")
    check("Y translated by 4",          abs(new_P.Y - 4) < 1e-3, f"new_P={new_P}")
    check("Z NOT translated (stripped)", abs(new_P.Z)     < 1e-3, f"new_P={new_P}")


# ---------------------------------------------------------------------------
# Test 10: compute_step3_move -- rotation about normal
# ---------------------------------------------------------------------------
print("\n--- Test 10: compute_step3_move, rotation about normal ---")

mated_N = Vector(0, 0, 1)
pick1 = make_pick((5, 5, 0), (1, 0, 0))   # moving: X edge
pick2 = make_pick((5, 5, 0), (0, 1, 0))   # fixed:  Y edge

move = compute_step3_move(pick1, pick2, mated_N)
check("move is not None", move is not None)

if move is not None:
    new_dir = apply_to_dir(move, Vector(1, 0, 0))
    check("X edge rotated to Y direction",
          vec_close(new_dir, Vector(0, 1, 0)),
          f"new_dir={new_dir}")


# ---------------------------------------------------------------------------
# Test 11: Full workflow -- Mate then in-plane translate
# ---------------------------------------------------------------------------
print("\n--- Test 11: Full workflow -- Mate then in-plane translate ---")

# Block bottom face at (0,0,10), normal (0,0,-1)
# Plate top face at (0,0,0), normal (0,0,1)
p1_s1 = make_pick((0, 0, 10), (0, 0, -1))
p2_s1 = make_pick((0, 0,  0), (0, 0,  1))

move1 = compute_step1_move(p1_s1, p2_s1, mate=True)
check("Step 1 move computed", move1 is not None)

if move1 is not None:
    new_P  = apply_to_point(move1, Vector(0, 0, 10))
    new_N  = apply_to_dir(move1,   Vector(0, 0, -1))
    check("Step 1: face at z=0",       abs(new_P.Z) < 1e-3, f"z={new_P.Z}")
    check("Step 1: normal still -Z",   vec_close(new_N, Vector(0,0,-1)), f"N={new_N}")

    # Step 2: move feature from (0,0,0) to (5,3,0)
    mated_N = Vector(0, 0, 1)
    p1_s2 = make_pick(new_P)
    p2_s2 = make_pick((5, 3, 0))

    move2 = compute_step2_move(p1_s2, p2_s2, mated_N)
    check("Step 2 move computed", move2 is not None)

    if move2 is not None:
        final_P = apply_to_point(move2, new_P)
        check("Step 2: X→5", abs(final_P.X - 5) < 1e-3, f"X={final_P.X}")
        check("Step 2: Y→3", abs(final_P.Y - 3) < 1e-3, f"Y={final_P.Y}")
        check("Step 2: Z=0 preserved", abs(final_P.Z) < 1e-3, f"Z={final_P.Z}")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n{'='*50}")
print(f"Results: {n_pass} passed, {n_fail} failed out of {n_pass+n_fail} checks")
if n_fail == 0:
    print("All tests passed.")
else:
    print("SOME TESTS FAILED -- see above.")
print('='*50)
