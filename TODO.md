# BasiCAD TODO

This file tracks outstanding issues and future development ideas.
Both developer and user contributions welcome.

---

## 1. Broken (should work but doesn't)

### Dynamic move (AIS Manipulator) moves only active part
When using Dynamic move on a sub-assembly, only the active part
moves visually during the drag. Clicking Done applies the move
correctly to the whole assembly, but the preview is misleading.

### Mate/Align fails after Dynamic move
After using Dynamic move to mis-align a part, picking faces for
Mate/Align is not registered, leaving the dialog waiting
indefinitely.

---

## 2. Known limitations (by design, not bugs)

### Modifying a shared instance in Basicad breaks sharing
`import_step()` collapses XDE references into independent Python
objects, so modifying one instance makes it a copy. KodaCAD does
NOT have this limitation -- it modifies the XDE prototype directly.

---

## 3. Future development ideas


