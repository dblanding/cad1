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
        print(f"result loc IsIdentity: {result.Location().IsIdentity()}")

        # Try Located(original_loc) -- does it compound or replace?
        r1 = result.Located(original_loc)
        print(f"Located(orig) loc IsIdentity: {r1.Location().IsIdentity()}")
        print(f"Located(orig) then Located(gloc): {first_vertex(r1.Located(gloc))}")

        # Try Move() -- physically transforms geometry
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
        trsf = original_loc.IsIdentity  # wrong
        # Get gp_Trsf from TopLoc_Location
        trsf = original_loc.IsIdentity  
        # Correct way:
        result2 = result.Located(TLoc())  # ensure clean
        # Apply original_loc as a move (physical transform)
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
        # TopLoc_Location -> gp_Trsf via IsIdentity... no
        # Let's try: the gp_Trsf is at original_loc.IsIdentity
        # Actually: TopLoc_Location.IsIdentity() is bool
        # The gp_Trsf is obtained via... let me check
        print(f"TopLoc_Location methods: {[m for m in dir(original_loc) if not m.startswith('_')]}")
        break
