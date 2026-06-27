import sys
sys.path.insert(0, 'src')
from step_assembly_poc import load_assembly
from anytree import PreOrderIter
from OCP.TopLoc import TopLoc_Location as TLoc
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_VERTEX
from OCP.BRep import BRep_Tool
from OCP.TopoDS import TopoDS

assy = load_assembly('step/as1-oc-214.stp')
for node in PreOrderIter(assy):
    if node.label == 'l-bracket' and not node.children:
        # Simulate what fillet does: replace _wrapped with identity-located shape
        original_wrapped = node._wrapped
        identity_shape = node._wrapped.Located(TLoc())
        node._wrapped = identity_shape

        # Now check what node.wrapped returns
        w = node.wrapped
        exp = TopExp_Explorer(w, TopAbs_VERTEX)
        if exp.More():
            v = TopoDS.Vertex_s(exp.Current())
            pt = BRep_Tool.Pnt_s(v)
            print(f"node.wrapped vertex after _wrapped=identity: ({pt.X():.1f}, {pt.Y():.1f}, {pt.Z():.1f})")
        print(f"node.wrapped.Location() IsIdentity: {w.Location().IsIdentity()}")

        # Now apply global_location as _display_leaf does
        gloc = node.global_location.wrapped
        world = w.Located(gloc)
        exp2 = TopExp_Explorer(world, TopAbs_VERTEX)
        if exp2.More():
            v2 = TopoDS.Vertex_s(exp2.Current())
            pt2 = BRep_Tool.Pnt_s(v2)
            print(f"After Located(global_loc): ({pt2.X():.1f}, {pt2.Y():.1f}, {pt2.Z():.1f})")

        # Restore
        node._wrapped = original_wrapped
        break
