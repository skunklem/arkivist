# ui/widgets/outline/viewer.py
from PySide6 import QtCore, QtGui, QtWidgets

class OutlineViewer(QtWidgets.QPlainTextEdit):
    openFullRequested = QtCore.Signal()  # emitted on double-click

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setCursorWidth(2)
        self.setWordWrapMode(QtGui.QTextOption.NoWrap)
        # we keep selection & scrolling, but ignore edits

    # --- minimal helpers your mini tab already uses ---
    def lines(self) -> list[str]:
        return self.toPlainText().splitlines()

    def set_lines(self, lines: list[str]):
        self._suppress = True
        try:
            self.setPlainText("\n".join(lines))
        finally:
            self._suppress = False

    def clamp_and_place_cursor(self, line: int, col: int):
        line = max(0, min(line, self.blockCount()-1))
        blk  = self.document().findBlockByNumber(line)
        col  = max(0, min(col, len(blk.text())))
        c = self.textCursor()
        c.setPosition(blk.position())
        c.movePosition(QtGui.QTextCursor.Right, n=col)
        self.setTextCursor(c)

    def set_text_and_cursor(self, text: str, line: int, col: int):
        # used by your mirroring path; no undo, no signals
        self.setPlainText(text)
        self.clamp_and_place_cursor(line, col)

    def get_line_col(self) -> tuple[int, int]:
        c = self.textCursor()
        return (c.blockNumber(), c.positionInBlock())

    def get_selection_anchor_and_pos(self) -> tuple[int,int,int,int]:
        c = self.textCursor()
        a = c.anchor()
        p = c.position()
        a_blk = self.document().findBlock(a)
        p_blk = self.document().findBlock(p)
        return (a_blk.blockNumber(), a - a_blk.position(),
                p_blk.blockNumber(), p - p_blk.position())

    def set_selection_anchor_and_pos(self, aL, aC, pL, pC):
        a_blk = self.document().findBlockByNumber(aL)
        p_blk = self.document().findBlockByNumber(pL)
        c = self.textCursor()
        c.setPosition(a_blk.position() + aC)
        c.setPosition(p_blk.position() + pC, QtGui.QTextCursor.KeepAnchor)
        self.setTextCursor(c)

    # --- block all editing keys, but allow Copy/Select All ---
    def keyPressEvent(self, e: QtGui.QKeyEvent):
        m, k = e.modifiers(), e.key()
        if (m & QtCore.Qt.ControlModifier) and k in (QtCore.Qt.Key_C, QtCore.Qt.Key_A):
            return super().keyPressEvent(e)  # copy/select-all OK
        # swallow everything else (display-only)
        e.ignore()

    def mouseDoubleClickEvent(self, e: QtGui.QMouseEvent):
        self.openFullRequested.emit()  # let the mini tab decide what to do
        super().mouseDoubleClickEvent(e)
