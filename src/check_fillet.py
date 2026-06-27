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
from OCP.TopLoc import TopLoc_Location as TLoc

assy = load_assembly('step/as1-oc-214.stp')
for node in PreOrderIter(assy):
    if node.label == 'l-bracket' and not node.children:
        work_shape = node.wrapped
        gloc = node.global_location.wrapped
        world_shape = work_shape.Located(gloc)

        # Find first edge in world shape
        exp = TopExp_Explorer(world_shape, TopAbs_EDGE)
        if exp.More():
            edge = TopoDS.Edge_s(exp.Current())
            curve = BRepAdaptor_Curve(edge)
            mid = (curve.FirstParameter() + curve.LastParameter()) / 2
            pt = curve.Value(mid)
            print(f"World edge midpoint (for picking): ({pt.X():.1f},{pt.Y():.1f},{pt.Z():.1f})")

        # Run fillet on work_shape directly (with location)
        mk = BRepFilletAPI_MakeFillet(work_shape)
        exp2 = TopExp_Explorer(work_shape, TopAbs_EDGE)
        if exp2.More():
            edge2 = TopoDS.Edge_s(exp2.Current())
            mk.Add(5.0, edge2)
        mk.Build()
        if mk.IsDone():
            result = mk.Shape()
            print(f"Result location IsIdentity: {result.Location().IsIdentity()}")
            # Check first vertex
            exp3 = TopExp_Explorer(result, TopAbs_VERTEX)
            if exp3.More():
                v = TopoDS.Vertex_s(exp3.Current())
                pt = BRep_Tool.Pnt_s(v)
                print(f"Fillet result vertex (raw): ({pt.X():.1f},{pt.Y():.1f},{pt.Z():.1f})")
            # Apply global_loc as _display_leaf would
            world_result = result.Located(gloc)
            exp4 = TopExp_Explorer(world_result, TopAbs_VERTEX)
            if exp4.More():
                v2 = TopoDS.Vertex_s(exp4.Current())
                pt2 = BRep_Tool.Pnt_s(v2)
                print(f"Fillet result vertex after Located(global_loc): ({pt2.X():.1f},{pt2.Y():.1f},{pt2.Z():.1f})")
        break
