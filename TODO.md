# BasiCAD TODO

This file tracks outstanding issues and future development ideas.
Both developer and user contributions welcome.

---

## 1. Broken (should work but doesn't)

* RMB in viewport
    * View TOP actually views BOTTOM
* Utility
    * Set units (Missing)

---

## 2. New Features to implement

* Additional Tools in toolbar
    * Delete all construction
    * Delete all geometry (profile types)
    * Project edge as construction lines
        * They would be c-lines, c-circles
* New buttons on calculator
    * Measure distance between 2 (parallel) faces
    * Measure angle between 2 (non-parallel) faces
### Position
* Use it and see how it feels
* Compare the Creo dialog
* Move selected (part or assy) precisely in a prescribed dof
    * Can the AIS manipulator be adpated to allow a precise value to be specified?
    * How about move between 2 points?
        * (I already have the abiltiy to align 2 cylindrical faces)
* Add function: **Copy part/assy**

---

* For saving sessions:
    * Propose saving a step file to a /tmp/sessions/folder using an appended '_n' (incrementing)
    * By default:
        * Load session would load the one with the highest value
        * Save session would increment value of n and save to the newly incremented filename.
* Add "Box Select" to grab all the edges in the bottle for filleting

---

## 3. Known limitations (by design, not bugs)

* Can't propagate modifications to other shared instances. Must first make copy, then modify.

---


