import sys
sys.path.insert(0, 'src')
from step_assembly_poc import load_assembly
from anytree import PreOrderIter
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_VERTEX, TopAbs_EDGE
from OCP.BRep import BRep_Tool
from OCP.TopoDS import TopoDS
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCP.BRepAdaptor import BRepAdaptor_Curve

assy = load_assembly('step/as1-oc-214.stp')
for node in PreOrderIter(assy):
    if node.label == 'l-bracket' and not node.children:
        work_shape = node.wrapped
        gloc = node.global_location.wrapped
        original_loc = work_shape.Location()

        print(f"original_loc IsIdentity: {original_loc.IsIdentity()}")

        mk = BRepFilletAPI_MakeFillet(work_shape)
        exp = TopExp_Explorer(work_shape, TopAbs_EDGE)
        if exp.More():
            edge = TopoDS.Edge_s(exp.Current())
            mk.Add(5.0, edge)
        mk.Build()
        if mk.IsDone():
            result = mk.Shape()
            print(f"mk.Shape() loc IsIdentity: {result.Location().IsIdentity()}")
            
            # Restore original loc
            result_with_loc = result.Located(original_loc)
            print(f"result_with_loc IsIdentity: {result_with_loc.Location().IsIdentity()}")
            
            # Apply global_loc as _display_leaf does
            world = result_with_loc.Located(gloc)
            exp2 = TopExp_Explorer(world, TopAbs_VERTEX)
            if exp2.More():
                v = TopoDS.Vertex_s(exp2.Current())
                pt = BRep_Tool.Pnt_s(v)
                print(f"Final vertex: ({pt.X():.1f},{pt.Y():.1f},{pt.Z():.1f})")
            print(f"Expected ~(5, 25, 20) or similar assembled position")
        break
