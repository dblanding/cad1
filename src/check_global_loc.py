import sys
sys.path.insert(0, 'src')
from step_assembly_poc import load_assembly
from anytree import PreOrderIter
from OCP.BRep import BRep_Builder
from OCP.gp import gp_Pnt
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_VERTEX
from OCP.BRep import BRep_Tool

assy = load_assembly('step/as1-oc-214.stp')
for node in PreOrderIter(assy):
    if node.label == 'l-bracket' and not node.children:
        # Get first vertex of wrapped shape to see its actual coordinates
        exp = TopExp_Explorer(node.wrapped, TopAbs_VERTEX)
        if exp.More():
            from OCP.TopoDS import TopoDS
            v = TopoDS.Vertex_s(exp.Current())
            pt = BRep_Tool.Pnt_s(v)
            print(f"First vertex of node.wrapped: ({pt.X():.1f}, {pt.Y():.1f}, {pt.Z():.1f})")

        # Now apply global_location and check
        from OCP.TopLoc import TopLoc_Location as TLoc
        gloc = node.global_location.wrapped
        world_shape = node.wrapped.Located(gloc)
        exp2 = TopExp_Explorer(world_shape, TopAbs_VERTEX)
        if exp2.More():
            v2 = TopoDS.Vertex_s(exp2.Current())
            pt2 = BRep_Tool.Pnt_s(v2)
            print(f"First vertex after Located(global_loc): ({pt2.X():.1f}, {pt2.Y():.1f}, {pt2.Z():.1f})")

        # And with identity location (stripping)
        local_shape = node.wrapped.Located(TLoc())
        world_shape2 = local_shape.Located(gloc)
        exp3 = TopExp_Explorer(world_shape2, TopAbs_VERTEX)
        if exp3.More():
            v3 = TopoDS.Vertex_s(exp3.Current())
            pt3 = BRep_Tool.Pnt_s(v3)
            print(f"First vertex after strip+Located(global_loc): ({pt3.X():.1f}, {pt3.Y():.1f}, {pt3.Z():.1f})")
        break
