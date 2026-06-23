"""
workplane_smoke_test.py

Verifies that the OCP-ported workplane.py imports correctly and can
construct a default WorkPlane. No GUI needed.

Usage:
    uv run src/workplane_smoke_test.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

print("Importing workplane...")
try:
    from workplane import WorkPlane, face_normal
    print("  Import OK")
except Exception as e:
    print(f"  IMPORT FAILED: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)

print("Creating default WorkPlane (XY plane at origin)...")
try:
    wp = WorkPlane(size=100)
    print(f"  OK: origin={wp.origin.X(), wp.origin.Y(), wp.origin.Z()}")
    print(f"  wDir={wp.wDir.X():.1f}, {wp.wDir.Y():.1f}, {wp.wDir.Z():.1f}")
    print(f"  uDir={wp.uDir.X():.1f}, {wp.uDir.Y():.1f}, {wp.uDir.Z():.1f}")
    print(f"  border type={type(wp.border).__name__}")
    print(f"  clines={wp.clines}  (should have H+V through origin)")
except Exception as e:
    print(f"  FAILED: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)

print("Testing rect() profile geometry...")
try:
    wp.rect((-20, -10), (20, 10))
    print(f"  edgeList has {len(wp.edgeList)} edges (expected 4)")
    wire_ok = wp.makeWire()
    print(f"  makeWire() returned {wire_ok} (expected True)")
except Exception as e:
    print(f"  FAILED: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)

print("\nAll smoke tests passed.")
print("Next: test WorkPlane creation from a real face pick.")
