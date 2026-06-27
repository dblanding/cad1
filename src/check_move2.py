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
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform

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

        # Use BRepBuilderAPI_Transform to physically apply original_loc
        # to the geometry, then set location to identity
        trsf = original_loc.Transformation()
        builder = BRepBuilderAPI_Transform(result, trsf, True)
        result_moved = builder.Shape()
        print(f"result_moved loc IsIdentity: {result_moved.Location().IsIdentity()}")
        print(f"result_moved vertex: {first_vertex(result_moved)}")
        # Now apply global_loc
        print(f"result_moved + Located(gloc): {first_vertex(result_moved.Located(gloc))}")
        
        # Compare: original vertex after Located(gloc)
        print(f"original + Located(gloc): {first_vertex(work_shape.Located(gloc))}")
        break

# Check mk.Shape() location in detail
for node in PreOrderIter(assy):
    if node.label == 'l-bracket' and not node.children:
        work_shape = node.wrapped
        original_loc = work_shape.Location()
        mk2 = BRepFilletAPI_MakeFillet(work_shape)
        exp2 = TopExp_Explorer(work_shape, TopAbs_EDGE)
        if exp2.More():
            mk2.Add(5.0, TopoDS.Edge_s(exp2.Current()))
        mk2.Build()
        r = mk2.Shape()
        loc = r.Location()
        print(f"\nmk.Shape() Location details:")
        print(f"  IsIdentity: {loc.IsIdentity()}")
        print(f"  Transformation matrix (row1): {loc.Transformation().Value(1,1):.3f},{loc.Transformation().Value(1,2):.3f},{loc.Transformation().Value(1,3):.3f},{loc.Transformation().Value(1,4):.3f}")
        print(f"  Transformation matrix (row2): {loc.Transformation().Value(2,1):.3f},{loc.Transformation().Value(2,2):.3f},{loc.Transformation().Value(2,3):.3f},{loc.Transformation().Value(2,4):.3f}")
        print(f"  Transformation matrix (row3): {loc.Transformation().Value(3,1):.3f},{loc.Transformation().Value(3,2):.3f},{loc.Transformation().Value(3,3):.3f},{loc.Transformation().Value(3,4):.3f}")
        
        # And original_loc transformation
        print(f"\noriginal_loc Transformation:")
        print(f"  row1: {original_loc.Transformation().Value(1,1):.3f},{original_loc.Transformation().Value(1,2):.3f},{original_loc.Transformation().Value(1,3):.3f},{original_loc.Transformation().Value(1,4):.3f}")
        print(f"  row2: {original_loc.Transformation().Value(2,1):.3f},{original_loc.Transformation().Value(2,2):.3f},{original_loc.Transformation().Value(2,3):.3f},{original_loc.Transformation().Value(2,4):.3f}")
        print(f"  row3: {original_loc.Transformation().Value(3,1):.3f},{original_loc.Transformation().Value(3,2):.3f},{original_loc.Transformation().Value(3,3):.3f},{original_loc.Transformation().Value(3,4):.3f}")
        break
