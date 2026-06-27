import sys
sys.path.insert(0, 'src')
from step_assembly_poc import load_assembly
from anytree import PreOrderIter
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_VERTEX
from OCP.BRep import BRep_Tool
from OCP.TopoDS import TopoDS
from OCP.TopLoc import TopLoc_Location as TLoc
from OCP.gp import gp_Trsf, gp_Vec

assy = load_assembly('step/as1-oc-214.stp')
for node in PreOrderIter(assy):
    if node.label == 'l-bracket' and not node.children:
        w = node.wrapped
        orig = w.Location()
        gloc = node.global_location.wrapped

        def first_vertex(shape):
            exp = TopExp_Explorer(shape, TopAbs_VERTEX)
            if exp.More():
                v = TopoDS.Vertex_s(exp.Current())
                pt = BRep_Tool.Pnt_s(v)
                return f"({pt.X():.2f},{pt.Y():.2f},{pt.Z():.2f})"
            return "none"

        # Make a simple translation loc
        t = gp_Trsf()
        t.SetTranslation(gp_Vec(100, 0, 0))
        trans_loc = TLoc(t)

        # Test: does Located() replace or compound?
        w2 = w.Located(trans_loc)
        print(f"original vertex:           {first_vertex(w)}")
        print(f"Located(trans100):         {first_vertex(w2)}")
        w3 = w2.Located(trans_loc)
        print(f"Located(trans100) x2:      {first_vertex(w3)}")
        # If replacing: same as w2. If compounding: 200 translation.

        # Now test with identity first
        w_id = w.Located(TLoc())
        print(f"\nstripped to identity:      {first_vertex(w_id)}")
        w_id2 = w_id.Located(trans_loc)
        print(f"identity+Located(trans100): {first_vertex(w_id2)}")
        break
