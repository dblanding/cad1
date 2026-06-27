import sys, inspect
sys.path.insert(0, 'src')
from step_assembly_poc import load_assembly
from anytree import PreOrderIter

assy = load_assembly('step/as1-oc-214.stp')
for node in PreOrderIter(assy):
    if node.label == 'l-bracket' and not node.children:
        # Find where global_location is defined
        for cls in type(node).__mro__:
            if 'global_location' in cls.__dict__:
                print(f"Defined in: {cls}")
                print(inspect.getsource(cls.__dict__['global_location'].fget))
                break
        break
