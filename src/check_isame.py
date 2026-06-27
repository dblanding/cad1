import sys
sys.path.insert(0, 'src')
from step_assembly_poc import load_assembly
from anytree import PreOrderIter

assy = load_assembly('step/as1-oc-214.stp')
leaves = [n for n in PreOrderIter(assy) if not n.children]

print("Checking IsSame() between l-bracket instances:")
lbrackets = [n for n in leaves if n.label == 'l-bracket']
print(f"Found {len(lbrackets)} l-bracket nodes")
if len(lbrackets) >= 2:
    s1 = lbrackets[0].wrapped
    s2 = lbrackets[1].wrapped
    print(f"IsSame: {s1.IsSame(s2)}")
    print(f"IsEqual: {s1.IsEqual(s2, 1e-6)}")
    print(f"Same Python object: {s1 is s2}")
    print(f"s1 location IsIdentity: {s1.Location().IsIdentity()}")
    print(f"s2 location IsIdentity: {s2.Location().IsIdentity()}")
    # Check if underlying TShape is same
    print(f"Same TShape: {s1.TShape() == s2.TShape()}")
