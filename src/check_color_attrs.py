import sys
sys.path.insert(0, 'src')
from build123d import import_step
from anytree import PreOrderIter

assy = import_step('step/as1-oc-214.stp')
for node in PreOrderIter(assy):
    color = getattr(node, 'color', None)
    if color is not None:
        print(f"type: {type(color)}")
        print(f"dir: {[x for x in dir(color) if not x.startswith('_')]}")
        print(f"repr: {repr(color)}")
        break
