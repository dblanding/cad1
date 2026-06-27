import sys
sys.path.insert(0, 'src')
from step_assembly_poc import load_assembly
from anytree import PreOrderIter
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_VERTEX
from OCP.BRep import BRep_Tool
from OCP.TopoDS import TopoDS
from OCP.TopLoc import TopLoc_Location as TLoc

assy = load_assembly('step/as1-oc-214.stp')
for node in PreOrderIter(assy):
    if node.label == 'l-bracket' and not node.children:
        w = node.wrapped
        gloc = node.global_location.wrapped
        orig_loc = w.Location()

        def first_vertex(shape):
            exp = TopExp_Explorer(shape, TopAbs_VERTEX)
            if exp.More():
                v = TopoDS.Vertex_s(exp.Current())
                pt = BRep_Tool.Pnt_s(v)
                return f"({pt.X():.1f},{pt.Y():.1f},{pt.Z():.1f})"
            return "none"

        print(f"original:              {first_vertex(w)}")
        print(f"Located(gloc):         {first_vertex(w.Located(gloc))}")  # what _display_leaf does
        
        # Simulate: strip loc, then located(orig_loc), then located(gloc)
        stripped = w.Located(TLoc())
        print(f"stripped:              {first_vertex(stripped)}")
        print(f"strip+Located(orig):   {first_vertex(stripped.Located(orig_loc))}")
        print(f"strip+orig+gloc:       {first_vertex(stripped.Located(orig_loc).Located(gloc))}")
        
        # What if we just use Located(gloc) on the stripped shape?
        print(f"strip+Located(gloc):   {first_vertex(stripped.Located(gloc))}")
        break
