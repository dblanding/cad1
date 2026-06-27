import sys
sys.path.insert(0, '/home/claude/cad1_working/src')
from build123d import import_step
from anytree import PreOrderIter

assy = import_step('step/as1-oc-214.stp')
for node in PreOrderIter(assy):
    color = getattr(node, 'color', None)
    label = getattr(node, 'label', '?')
    print(f"  {label:30s}  color={color}")
