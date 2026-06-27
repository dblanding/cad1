import sys
sys.path.insert(0, 'src')
from step_assembly_poc import load_assembly
from anytree import PreOrderIter
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_VERTEX, TopAbs_EDGE
from OCP.BRep import BRep_Tool
from OCP.TopoDS import TopoDS
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCP.TopLoc import TopLoc_Location as TLoc

assy = load_assembly('step/as1-oc-214.stp')
for node in PreOrderIter(assy):
    if node.label == 'l-bracket' and not node.children:
        work_shape = node.wrapped
        original_loc = work_shape.Location()
        gloc = node.global_location.wrapped

        def first_vertex(shape):
            exp = TopExp_Explorer(shape, TopAbs_VERTEX)
            if exp.More():
                v = TopoDS.Vertex_s(exp.Current())
                pt = BRep_Tool.Pnt_s(v)
                return f"({pt.X():.1f},{pt.Y():.1f},{pt.Z():.1f})"
            return "none"

        mk = BRepFilletAPI_MakeFillet(work_shape)
        exp = TopExp_Explorer(work_shape, TopAbs_EDGE)
        if exp.More():
            mk.Add(5.0, TopoDS.Edge_s(exp.Current()))
        mk.Build()
        result = mk.Shape()

        # Try Move() -- replaces location in-place
        result.Move(original_loc)
        print(f"After Move(orig) IsIdentity: {result.Location().IsIdentity()}")
        print(f"After Move(orig) vertex: {first_vertex(result)}")
        print(f"After Move(orig) + Located(gloc): {first_vertex(result.Located(gloc))}")
        print(f"Target: {first_vertex(work_shape.Located(gloc))}")
        break
