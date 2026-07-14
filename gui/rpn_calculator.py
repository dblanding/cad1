"""
rpn_calculator.py

RPN CALCULATOR -- ported from KodaCAD's rpnCalculator.py (Doug Blanding,
dblanding@gmail.com, GPL), styled after the one in CoCreate SolidDesigner.

PORTING NOTES (differences from the KodaCAD original):
  - The T/Z/Y/X register buttons call `self.caller.valueFromCalc(value)`,
    same as KodaCAD. In BasiCAD, MainWindow.valueFromCalc() sends the
    value to whichever QLineEdit currently has keyboard focus (e.g. the
    Depth field in the Workplane dialog) rather than to a dedicated
    lineEditStack/registerCallback command system -- BasiCAD's dialogs
    take their input directly in their own fields, so this is the
    integration point that makes the calculator immediately useful
    everywhere without per-dialog wiring.
  - The "Dist" / "Len" buttons (pick-based measurement tools) call
    caller.distPtPt / caller.edgeLen in KodaCAD. BasiCAD doesn't have
    those measurement tools yet (planned for a later phase of the UI
    revision -- DESIGN_BACKLOG item 33), so they degrade gracefully:
    if the caller doesn't implement them, the button shows a status-bar
    message instead of crashing.
  - Everything else (arithmetic, stack rotation, trig, mm<->in, STO/RCL)
    is unchanged from the KodaCAD original.
"""

import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QLayout,
    QLineEdit,
    QSizePolicy,
    QToolButton,
)


class Button(QToolButton):
    """Convenience class for buttons."""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def sizeHint(self):
        size = super().sizeHint()
        size.setHeight(size.height())
        size.setWidth(max(size.width(), size.height()))
        return size


class Calculator(QDialog):
    """RPN calculator styled after the one in CoCreate SolidDesigner CAD."""

    NumDigitButtons = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint
        )
        self.caller = parent
        self.setWindowTitle("RPN Calculator")

        self.mem = ""
        self.keip = False       # Flag set when keyboard entry is in progress
        self.needrup = False    # Flag signaling need to rotate up on next entry

        self.x = 0
        self.y = 0
        self.z = 0
        self.t = 0

        self.xdisplay = self.display()
        self.ydisplay = self.display()
        self.zdisplay = self.display()
        self.tdisplay = self.display()

        myblue1 = "steelblue"
        myblue2 = "darkslateblue"
        mygray = "rgb(120,120,120)"
        mygreen = "green"
        mygold = "goldenrod"
        myred = "hsv(0,255,180)"

        self.mainLayout = QGridLayout()
        self.mainLayout.setSpacing(0)
        self.mainLayout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        # Grid is 36 columns across
        self.butn("T", 0, 0, lambda state, r="t": self.pr(r), colspan=4)
        self.butn("Z", 1, 0, lambda state, r="z": self.pr(r), colspan=4)
        self.butn("Y", 2, 0, lambda state, r="y": self.pr(r), colspan=4)
        self.butn("X", 3, 0, lambda state, r="x": self.pr(r), colspan=4)
        self.mainLayout.addWidget(self.tdisplay, 0, 4, 1, 26)
        self.mainLayout.addWidget(self.zdisplay, 1, 4, 1, 26)
        self.mainLayout.addWidget(self.ydisplay, 2, 4, 1, 26)
        self.mainLayout.addWidget(self.xdisplay, 3, 4, 1, 26)
        self.butn("pi", 0, 30, self.pi, colspan=6)
        self.butn("1/x", 1, 30, lambda state, op="1/x": self.func(op), colspan=6)
        self.butn("2x", 2, 30, lambda state, op="x*2": self.func(op), colspan=6)
        self.butn("x/2", 3, 30, lambda state, op="x/2": self.func(op), colspan=6)

        self.butn("mm -> in", 4, 0, self.mm2in, colspan=12)
        self.butn("in -> mm", 4, 12, self.in2mm, colspan=12)
        self.butn("STO", 4, 24, self.storex, clr=mygreen, colspan=6)
        self.butn("RCL", 4, 30, self.recallx, clr=mygreen, colspan=6)

        self.butn("7", 5, 0, lambda state, c="7": self.keyin(c), clr=myblue1)
        self.butn("8", 5, 6, lambda state, c="8": self.keyin(c), clr=myblue1)
        self.butn("9", 5, 12, lambda state, c="9": self.keyin(c), clr=myblue1)
        self.butn("+", 5, 18, lambda state, op="+": self.calculate(op), clr=myblue2)
        self.butn("R up", 5, 24, self.rotateup, clr=mygreen, colspan=6)
        self.butn("R dn", 5, 30, self.rotatedn, clr=mygreen, colspan=6)

        self.butn("4", 6, 0, lambda state, c="4": self.keyin(c), clr=myblue1)
        self.butn("5", 6, 6, lambda state, c="5": self.keyin(c), clr=myblue1)
        self.butn("6", 6, 12, lambda state, c="6": self.keyin(c), clr=myblue1)
        self.butn("-", 6, 18, lambda state, op="-": self.calculate(op), clr=myblue2)
        self.butn("<-", 6, 24, self.trimx, clr=myred, colspan=4)
        self.butn("X<>Y", 6, 28, self.swapxy, clr=mygreen, colspan=8)

        self.butn("1", 7, 0, lambda state, c="1": self.keyin(c), clr=myblue1)
        self.butn("2", 7, 6, lambda state, c="2": self.keyin(c), clr=myblue1)
        self.butn("3", 7, 12, lambda state, c="3": self.keyin(c), clr=myblue1)
        self.butn("*", 7, 18, lambda state, op="*": self.calculate(op), clr=myblue2)
        self.butn("CL X", 7, 24, self.clearx, clr=myred)
        self.butn("CLR", 7, 30, self.clearall, clr=myred)

        self.butn("0", 8, 0, lambda state, c="0": self.keyin(c), clr=myblue1)
        self.butn(".", 8, 6, lambda state, c=".": self.keyin(c), clr=myblue2)
        self.butn("+/-", 8, 12, lambda state, op="+/-": self.calculate(op), clr=myblue2)
        self.butn("/", 8, 18, lambda state, c="/": self.calculate(c), clr=myblue2)
        self.butn("ENTER", 8, 24, self.enter, clr=mygold, colspan=12)

        self.butn("Sin", 9, 0,
                 lambda state, op="math.sin(x)": self.func(op, in_cnvrt=1),
                 clr=mygold, colspan=8)
        self.butn("Cos", 9, 8,
                 lambda state, op="math.cos(x)": self.func(op, in_cnvrt=1),
                 clr=mygold, colspan=8)
        self.butn("Tan", 9, 16,
                 lambda state, op="math.tan(x)": self.func(op, in_cnvrt=1),
                 clr=mygold, colspan=8)
        self.butn("x^2", 9, 24, lambda state, op="x*x": self.func(op), clr=mygold)
        self.butn("10^x", 9, 30, lambda state, op="10**x": self.func(op), clr=mygold)

        self.butn("ASin", 10, 0,
                 lambda state, op="math.asin(x)": self.func(op, out_cnvrt=1),
                 clr=mygold, colspan=8)
        self.butn("ACos", 10, 8,
                 lambda state, op="math.acos(x)": self.func(op, out_cnvrt=1),
                 clr=mygold, colspan=8)
        self.butn("ATan", 10, 16,
                 lambda state, op="math.atan(x)": self.func(op, out_cnvrt=1),
                 clr=mygold, colspan=8)
        self.butn("Sqrt x", 10, 24,
                 lambda state, op="math.sqrt(x)": self.func(op), clr=mygold)
        self.butn("y^x", 10, 30, lambda state, op="y**x": self.func(op), clr=mygold)

        # "Dist"/"Len" call pick-based measurement tools that don't exist
        # in BasiCAD yet (see module docstring) -- self.measure() below
        # degrades gracefully if the caller doesn't implement them.
        self.butn("Dist", 11, 0,
                 lambda: self.measure("distPtPt"), clr=mygray, colspan=8)
        self.butn("Len", 11, 8,
                 lambda: self.measure("edgeLen"), clr=mygray, colspan=8)
        self.butn("Rad", 11, 16, self.noop, clr=mygray, colspan=8)
        self.butn("Ang", 11, 24, self.noop, clr=mygray)
        self.butn("", 11, 30, self.noop, clr=mygray)

        self.setLayout(self.mainLayout)

    def butn(self, text, row, col, com=None, clr="dimgray", rowspan=1, colspan=6):
        b = Button(text)
        b.clicked.connect(com)
        b.setStyleSheet(f"color: white; background-color: {clr}")
        self.mainLayout.addWidget(b, row, col, rowspan, colspan)

    def display(self):
        d = QLineEdit("0")
        d.setAlignment(Qt.AlignmentFlag.AlignRight)
        d.setMaxLength(18)
        font = d.font()
        font.setPointSize(font.pointSize() + 2)
        d.setFont(font)
        return d

    def closeEvent(self, event):
        if self.caller is not None:
            try:
                self.caller.calculator = None
            except Exception:
                pass
        event.accept()

    def measure(self, method_name):
        """Call caller.<method_name>() if it exists, else degrade gracefully
        instead of crashing -- see module docstring."""
        method = getattr(self.caller, method_name, None) if self.caller else None
        if callable(method):
            method()
        elif self.caller is not None and hasattr(self.caller, "statusBar"):
            self.caller.statusBar().showMessage(
                f"'{method_name}' is not implemented yet.", 4000)
        else:
            print(f"'{method_name}' is not implemented yet.")

    def pr(self, register):
        """Send value to caller."""
        value = getattr(self, register)
        if self.caller is not None:
            self.caller.valueFromCalc(value)
        else:
            print(value)
        self.keip = False
        self.needrup = True

    def keyin(self, c):
        if self.keip:
            dispVal = self.xdisplay.text() + c
            self.xdisplay.setText(dispVal)
            self.x = float(dispVal)
        else:
            self.keip = True
            if self.needrup:
                self.rotateup(loop=0)
            self.xdisplay.setText("")
            if c == ".":
                c = "0."
            self.keyin(c)

    def pi(self):
        self.rotateup()
        self.x = math.pi
        self.updateDisplays()
        self.needrup = True

    def updateDisplays(self):
        self.xdisplay.setText(str(self.x))
        self.ydisplay.setText(str(self.y))
        self.zdisplay.setText(str(self.z))
        self.tdisplay.setText(str(self.t))

    def enter(self):
        self.t = self.z
        self.z = self.y
        self.y = self.x
        self.x = self.x
        self.updateDisplays()
        self.keip = False
        self.needrup = False

    def calculate(self, op):
        """Arithmetic between x and y registers, then rotate down."""
        try:
            if op == "+/-":
                self.x = self.x * -1
                self.xdisplay.setText(str(self.x))
            else:
                if op == "+":
                    res = self.y + self.x
                elif op == "-":
                    res = self.y - self.x
                elif op == "*":
                    res = self.y * self.x
                elif op == "/":
                    res = self.y / self.x
                self.x = res
                self.y = self.z
                self.z = self.t
                self.updateDisplays()
            self.keip = False
            self.needrup = True
        except (ZeroDivisionError, ValueError, TypeError):
            self.xdisplay.setText("ERROR")

    def func(self, op, in_cnvrt=0, out_cnvrt=0):
        """Evaluate function op then put result in x-register, no rotation.
        if in_cnvrt: convert input value from degrees to radians.
        if out_cnvrt: convert output value from radians to degrees."""
        x = self.x
        if in_cnvrt:
            x = x * math.pi / 180
        result = eval(op)
        if out_cnvrt:
            result = result * 180 / math.pi
        self.x = result
        self.xdisplay.setText(str(self.x))
        self.keip = False
        self.needrup = True

    def mm2in(self):
        if self.xdisplay.text():
            self.x = self.x / 25.4
            self.xdisplay.setText(str(self.x))
            self.keip = False
            self.needrup = True

    def in2mm(self):
        if self.xdisplay.text():
            self.x = self.x * 25.4
            self.xdisplay.setText(str(self.x))
            self.keip = False
            self.needrup = True

    def storex(self):
        self.mem = self.x
        self.keip = False
        self.needrup = True

    def recallx(self):
        self.rotateup()
        self.xdisplay.setText(str(self.mem))
        self.keip = False
        self.needrup = True

    def rotateup(self, loop=1):
        x = self.t
        self.t = self.z
        self.z = self.y
        self.y = self.x
        if loop:
            self.x = x
        self.updateDisplays()

    def rotatedn(self):
        x = self.x
        self.x = self.y
        self.y = self.z
        self.z = self.t
        self.t = x
        self.updateDisplays()

    def trimx(self):
        trimmedStrVal = self.xdisplay.text()[:-1]
        try:
            self.xdisplay.setText(trimmedStrVal)
            self.x = float(trimmedStrVal)
        except ValueError:
            self.clearx()

    def swapxy(self):
        self.x, self.y = (self.y, self.x)
        self.updateDisplays()

    def clearx(self):
        self.x = 0
        self.xdisplay.setText("0")

    def clearall(self):
        self.x = self.y = self.z = self.t = 0
        self.updateDisplays()

    def putx(self, value):
        if self.needrup:
            self.rotateup(loop=0)
        self.x = value
        self.xdisplay.setText(str(value))
        self.keip = False
        self.needrup = True

    def noop(self):
        pass
