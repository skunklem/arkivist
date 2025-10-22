from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .constants import TAB_WIDTH

_NAV_KEYS = {
    QtCore.Qt.Key_Left, QtCore.Qt.Key_Right,
    QtCore.Qt.Key_Up, QtCore.Qt.Key_Down,
    QtCore.Qt.Key_Home, QtCore.Qt.Key_End,
    QtCore.Qt.Key_PageUp, QtCore.Qt.Key_PageDown,
}

class OutlineEditor(QtWidgets.QPlainTextEdit):
    requestMoveOutTop = QtCore.Signal(list)    # lines -> previous chapter
    requestMoveOutBottom = QtCore.Signal(list) # lines -> next chapter
    requestIndent = QtCore.Signal(int)      # +1 indent level, -1 outdent
    requestMoveWithin = QtCore.Signal(int)  # -1 up, +1 down (within chapter)
    requestCursorAbove = QtCore.Signal()
    requestCursorBelow = QtCore.Signal()
    requestDirtyChanged = QtCore.Signal(bool)
    commandIssued = QtCore.Signal(str)      # "paste", "cut", "enter", "bulk-delete"
    navCommitted  = QtCore.Signal(int, int) # (line, col)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWordWrapMode(QtGui.QTextOption.NoWrap)
        self._dirty = False
        # optional: monospace
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self.document().setDefaultFont(font)
        self.textChanged.connect(self._on_text_changed)
        self._undoStack: QtGui.QUndoStack | None = None
        self._suppress_text_undo_event = False  # set True when set_lines() updates doc
        self._last_text_snapshot_text = self.toPlainText()
        self._last_delete_kind = None              # "bksp" | "del" | None
        # store last known caret as (line, col)
        c = self.textCursor()
        self._last_text_snapshot_cursor = (c.blockNumber(), c.positionInBlock())

        self._text_can_undo = False
        self._text_can_redo = False
        # keep these flags in sync with Qt's internal text-undo stack
        self.undoAvailable.connect(lambda b: setattr(self, "_text_can_undo", b))
        self.redoAvailable.connect(lambda b: setattr(self, "_text_can_redo", b))

    def setUndoStack(self, stack: QtGui.QUndoStack):
        self._undoStack = stack

    def _on_text_changed(self):
        if not self._dirty:
            self._dirty = True
            self.requestDirtyChanged.emit(True)

    # ---- Character/word helpers ----
    def _is_word_char(self, ch: str) -> bool:
        return bool(ch) and (ch.isalnum() or ch == "_")

    def _in_middle_of_word(self) -> bool:
        c = self.textCursor()
        if c.hasSelection():
            return False
        col = c.positionInBlock()
        blk = c.block().text()
        left  = blk[col-1] if col > 0 else ""
        right = blk[col]   if col < len(blk) else ""
        return self._is_word_char(left) and self._is_word_char(right)

    # ---- Key classification ----
    def _is_nav_key(self, e: QtGui.QKeyEvent) -> bool:
        k = e.key()
        m = e.modifiers()

        nav_keys = {
            QtCore.Qt.Key_Left, QtCore.Qt.Key_Right, QtCore.Qt.Key_Up, QtCore.Qt.Key_Down,
            QtCore.Qt.Key_Home, QtCore.Qt.Key_End, QtCore.Qt.Key_PageUp, QtCore.Qt.Key_PageDown
        }

        # treat Ctrl+Arrows, Home/End, etc. as navigation too
        if k in nav_keys:
            return True

        # common shortcuts we do NOT consider typing:
        if e.matches(QtGui.QKeySequence.Copy) or e.matches(QtGui.QKeySequence.Paste) \
        or e.matches(QtGui.QKeySequence.Cut) or e.matches(QtGui.QKeySequence.Undo) \
        or e.matches(QtGui.QKeySequence.Redo) or e.matches(QtGui.QKeySequence.Find):
            return False

        # Ctrl/Alt combos are never "typing" here
        if (m & (QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier)):
            return False

        return False

    def _is_typing_key(self, e: QtGui.QKeyEvent) -> bool:
        k = e.key()
        m = e.modifiers()
        # exclude control/alt combos
        if (m & (QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier)):
            return False
        # we treat Tab specially (indent/outdent), so it's not typing
        if k in (QtCore.Qt.Key_Tab, QtCore.Qt.Key_Backtab):
            return False
        # Enter/Return handled separately (we’ll force a new step), not "typing"
        if k in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            return False
        # Space is typing
        if k == QtCore.Qt.Key_Space:
            return True
        # Printable text?
        t = e.text()
        return bool(t) and not t.isspace()
    
    def insertFromMimeData(self, source: QtGui.QMimeData):
        # Pasting should be a discrete T step and acts as boundary for word-coalescing
        self._force_new_text_step = True
        self._last_typed_char = "\n"
        super().insertFromMimeData(source)

    def set_lines(self, lines: list[str]):
        text = "\n".join(lines)
        self._suppress_text_undo_event = True
        try:
            self.setPlainText(text)
        finally:
            self._suppress_text_undo_event = False
        self._dirty = False
        self.requestDirtyChanged.emit(False)

    def get_line_col(self) -> tuple[int,int]:
        c = self.textCursor()
        return (c.blockNumber(), c.positionInBlock())

    def set_text_and_cursor(self, text: str, line: int, col: int):
        self._suppress_text_undo_event = True
        try:
            self.setPlainText(text)
            # clamp and place
            print(f"set_text_and_cursor: at ({line},{col})")
            self.clamp_and_place_cursor(line, col)
        finally:
            self._suppress_text_undo_event = False

        # keep local snapshots in sync so the next register_text has accurate BEFORE
        self._last_text_snapshot_text   = self.toPlainText()
        self._last_text_snapshot_cursor = self.get_line_col()

    def lines(self) -> list[str]:
        L = []
        b = self.document().begin()
        while b.isValid():
            L.append(b.text())  # preserves empty lines, including trailing final empty block
            b = b.next()
        return L

    def clear_selection_visual(self):
        c = self.textCursor()
        c.clearSelection()
        self.setTextCursor(c)
        self.viewport().update()

    # --- helpers for selection range as lines ---
    def _selected_line_range(self) -> tuple[int,int]:
        c = self.textCursor()
        a, b = c.selectionStart(), c.selectionEnd()
        if a == b:  # no selection → current line
            blk = c.block()
            i = blk.blockNumber()
            return (i, i)
        c2 = QtGui.QTextCursor(self.document())
        c2.setPosition(a); i0 = c2.blockNumber()
        c2.setPosition(b); i1 = c2.blockNumber()
        if c2.positionInBlock() == 0 and i1 > i0:  # selection ends at bol → previous line is last fully selected
            i1 -= 1
        return (i0, i1)

    def _lines_for_range(self, i0, i1):
        L = self.lines()
        return L, L[i0:i1+1]

    def _caret_line_and_column(self) -> tuple[int,int]:
        c = self.textCursor()
        return (c.blockNumber(), c.positionInBlock())

    def _leading_spaces_of_block(self, block_num: int) -> int:
        blk = self.document().findBlockByNumber(block_num)
        s = blk.text()
        return len(s) - len(s.lstrip(" "))

    def _insert_block_below_with_indent(self, indent: int):
        c = self.textCursor()
        c.movePosition(QtGui.QTextCursor.EndOfBlock)
        c.insertBlock()  # newline after current block
        if indent > 0:
            c.insertText(" " * indent)
        self.setTextCursor(c)  # caret ends after indent

    def _ensure_current_block_indent(self, indent: int):
        """Make current (new) block start with exactly `indent` spaces."""
        if indent <= 0:
            return
        c = self.textCursor()
        # We only touch if we're at col 0 right after Enter or the line is empty
        if c.positionInBlock() == 0:
            c.insertText(" " * indent)

    def _block_start_pos(self, i: int) -> int:
        b = self.document().findBlockByNumber(i)
        return b.position()

    def _block_end_pos(self, i: int) -> int:
        b = self.document().findBlockByNumber(i)
        # end of line content (before paragraph separator)
        return b.position() + max(0, b.length() - 1)

    def _block_text_len(self, i: int) -> int:
        b = self.document().findBlockByNumber(i)
        return max(0, b.length() - 1)  # excludes paragraph sep

    def _pos_from_line_col(self, i: int, col: int) -> int:
        b = self.document().findBlockByNumber(i)
        col = max(0, min(col, self._block_text_len(i)))
        return b.position() + col

    def select_line_col_range(self, s_line: int, s_col: int, e_line: int, e_col: int, active_at: str = "end"):
        s_line = max(0, s_line); e_line = max(s_line, e_line)
        s_pos  = self._pos_from_line_col(s_line, s_col)
        e_pos  = self._pos_from_line_col(e_line, e_col)
        c = self.textCursor()
        if active_at == "start":
            c.setPosition(e_pos)
            c.setPosition(s_pos, QtGui.QTextCursor.KeepAnchor)
        else:
            c.setPosition(s_pos)
            c.setPosition(e_pos, QtGui.QTextCursor.KeepAnchor)
        self.setTextCursor(c)
        self.ensureCursorVisible()

    def current_selection_line_cols(self):
        """
        Returns (has_sel, s_line, s_col, e_line, e_col, active_end)
        active_end is 'start' or 'end' (caret end) if selection exists, else None.
        """
        c = self.textCursor()
        if not c.hasSelection():
            return (False, -1, -1, -1, -1, None)
        s, e = c.selectionStart(), c.selectionEnd()
        bS = self.document().findBlock(s)
        bE = self.document().findBlock(e)
        s_line = bS.blockNumber()
        e_line = bE.blockNumber()
        s_col  = s - bS.position()
        e_col  = e - bE.position()
        active = "start" if c.position() == s else "end"
        return (True, s_line, s_col, e_line, e_col, active)

    def select_line_range(self, i0: int, i1: int, active_at: str = "end"):
        """Select lines [i0..i1] and keep the caret at the requested end."""
        i0 = max(0, i0); i1 = max(i0, i1)
        start = self._block_start_pos(i0)
        end   = self._block_end_pos(i1)
        c = self.textCursor()
        if active_at == "start":
            c.setPosition(end)
            c.setPosition(start, QtGui.QTextCursor.KeepAnchor)
        else:
            c.setPosition(start)
            c.setPosition(end, QtGui.QTextCursor.KeepAnchor)
        self.setTextCursor(c)
        self.ensureCursorVisible()

    def current_selection_active_end(self) -> str | None:
        c = self.textCursor()
        if not c.hasSelection():
            return None
        s, e = c.selectionStart(), c.selectionEnd()
        return "start" if c.position() == s else "end"

    def _line_col_from_pos(self, pos: int) -> tuple[int, int]:
        """Convert absolute doc position → (line, col), clamped safe."""
        doc = self.document()
        char_count = int(doc.characterCount())
        pos = max(0, min(int(pos), max(0, char_count - 1)))
        block = doc.findBlock(pos)
        if not block.isValid():
            return (0, 0)
        line = int(block.blockNumber())
        # clamp col so we never land on the paragraph separator for non-last blocks
        raw_col = int(pos - block.position())
        # reuse your existing text-length clamp logic
        max_col = max(0, block.length() - 1) if block.next().isValid() else max(0, block.length())
        col = max(0, min(raw_col, max_col))
        return (line, col)

    def get_selection_anchor_and_pos(self) -> tuple[int, int, int, int]:
        """Return (anchor_line, anchor_col, pos_line, pos_col)."""
        c = self.textCursor()
        a = c.anchor()
        p = c.position()
        aL, aC = self._line_col_from_pos(a)
        pL, pC = self._line_col_from_pos(p)
        return (aL, aC, pL, pC)

    def set_selection_anchor_and_pos(self, aL: int, aC: int, pL: int, pC: int) -> None:
        """Restore exact selection: set anchor first, then extend to position."""
        c = self.textCursor()
        c.setPosition(self._pos_from_line_col(aL, aC), QtGui.QTextCursor.MoveAnchor)
        c.setPosition(self._pos_from_line_col(pL, pC), QtGui.QTextCursor.KeepAnchor)
        self.setTextCursor(c)

    def set_line_col(self, line: int, col: int, keep_anchor: bool = False) -> None:
        """Convenience: place caret at (line, col)."""
        pos = self._pos_from_line_col(line, col)
        c = self.textCursor()
        c.setPosition(pos, QtGui.QTextCursor.KeepAnchor if keep_anchor else QtGui.QTextCursor.MoveAnchor)
        self.setTextCursor(c)
    
    # --- commands ---
    def _cmd_home(self):
        """
        Move the text cursor to the first non-space character of the current block.
        If the cursor is already at the first non-space character, move it to the
        start of the block (absolute column 0).

        This function mimics the behavior of the "Home" key on most text editors.
        """
        c = self.textCursor()
        block = c.block()
        text = block.text()
        first_non = len(text) - len(text.lstrip(" "))
        if c.positionInBlock() == first_non:
            # go to absolute column 0
            c.movePosition(QtGui.QTextCursor.StartOfBlock)
        else:
            # go to first non-space
            c.setPosition(block.position() + first_non)
        self.setTextCursor(c)

    def current_column(self) -> int:
        return self.textCursor().positionInBlock()

    def clamp_and_place_cursor(self, line: int, col: int):
        # Clamp to valid line, then clamp column to the *visible text* length
        line = max(0, min(line, self.blockCount()-1))
        blk  = self.document().findBlockByNumber(line)
        col  = max(0, min(col, len(blk.text())))
        c = self.textCursor()
        c.setPosition(blk.position())
        c.movePosition(QtGui.QTextCursor.Right, QtGui.QTextCursor.MoveAnchor, n=col)
        self.setTextCursor(c)

    # --- keep the visual cursor in sync in case of lag (not strictly necessary) ---
    def focusInEvent(self, ev):
        super().focusInEvent(ev)
        self.viewport().update()

    def focusOutEvent(self, ev):
        super().focusOutEvent(ev)
        self.viewport().update()

    # --- key handling ---

    # OutlineEditor.keyPressEvent – drop-in replacement
    def keyPressEvent(self, e: QtGui.QKeyEvent):
        m = e.modifiers()
        k = e.key()
        c = self.textCursor()
        has_sel = c.hasSelection()
        ctrl = bool(e.modifiers() & QtCore.Qt.ControlModifier)

        # make editors ignore ctrl+Z/Y so the app-level shortcuts win
        if ctrl and (
            k == QtCore.Qt.Key_Z or k == QtCore.Qt.Key_Y or
            ((m & QtCore.Qt.ShiftModifier) and k == QtCore.Qt.Key_Z)  # Ctrl+Shift+Z
        ):
            e.ignore()  # bubble to app-level QShortcut (workspace)
            print("EDITOR ignore Ctrl+Z/Y so app shortcut can handle it")
            return
        if ctrl and k in (QtCore.Qt.Key_Z, QtCore.Qt.Key_Y):
            print("EDITOR ignore Ctrl+Z/Y so app shortcut can handle it (2)")
            e.ignore()     # IMPORTANT: let it bubble to QAction
            return

        # Shift+Enter: insert a new line below (no split), keep indent
        if (k in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter)) and (m & QtCore.Qt.ShiftModifier):
            cur_line = c.blockNumber()
            indent = self._leading_spaces_of_block(cur_line)
            self._insert_block_below_with_indent(indent)
            return  # consumed

        # Enter: split, then ensure new line keeps previous indent
        if (k in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter)) and (m == QtCore.Qt.NoModifier):
            super().keyPressEvent(e)
            new_line = c.blockNumber()
            prev_indent = self._leading_spaces_of_block(new_line - 1) if new_line > 0 else 0
            self._ensure_current_block_indent(prev_indent)
            return

        # Backspace soft-dedent when left side is spaces
        if k == QtCore.Qt.Key_Backspace and m == QtCore.Qt.NoModifier:
            if not has_sel:
                col = c.positionInBlock()
                if col > 0:
                    left = c.block().text()[:col]
                    if left and "" == left.strip(" "):
                        n = (col - 1) % TAB_WIDTH + 1
                        c.beginEditBlock()
                        c.movePosition(QtGui.QTextCursor.Left, QtGui.QTextCursor.KeepAnchor, n)
                        if c.selectedText().replace("\u2029", "") == " " * n:
                            c.removeSelectedText()
                        else:
                            c.clearSelection(); c.deletePreviousChar()
                        c.endEditBlock()
                        self.setTextCursor(c)
                        return
            super().keyPressEvent(e)
            return

        # Tab / Shift+Tab → indent/outdent commands
        if k == QtCore.Qt.Key_Tab and m == QtCore.Qt.NoModifier:
            self.requestIndent.emit(+1); return
        if (k == QtCore.Qt.Key_Backtab) or (k == QtCore.Qt.Key_Tab and m == QtCore.Qt.ShiftModifier):
            self.requestIndent.emit(-1); return

        # Alt+Up / Alt+Down (within or cross chapter)
        if m == QtCore.Qt.AltModifier and k in (QtCore.Qt.Key_Up, QtCore.Qt.Key_Down):
            i0, i1 = self._selected_line_range()
            top_edge = (i0 == 0)
            bot_edge = (i1 == self.blockCount() - 1)
            lines = self.lines()[i0:i1+1]
            if k == QtCore.Qt.Key_Up and top_edge:
                self.requestMoveOutTop.emit(lines); return
            if k == QtCore.Qt.Key_Down and bot_edge:
                self.requestMoveOutBottom.emit(lines); return
            self.requestMoveWithin.emit(-1 if k == QtCore.Qt.Key_Up else +1)
            return

        # Up/Down at first/last line → cross chapter cursor
        if k == QtCore.Qt.Key_Up and m == QtCore.Qt.NoModifier:
            if not has_sel and c.blockNumber() == 0:
                self.requestCursorAbove.emit(); return
        if k == QtCore.Qt.Key_Down and m == QtCore.Qt.NoModifier:
            if not has_sel and c.blockNumber() == self.blockCount() - 1:
                self.requestCursorBelow.emit(); return

        # Home toggle (don’t pass to super)
        if k == QtCore.Qt.Key_Home and m == QtCore.Qt.NoModifier:
            self._cmd_home(); return

        # Tag special edits so the controller can force a break:
        if ctrl and k == QtCore.Qt.Key_V:
            self.commandIssued.emit("paste")
        elif ctrl and k == QtCore.Qt.Key_X:
            self.commandIssued.emit("cut")
        elif k in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self.commandIssued.emit("enter")
        elif k == QtCore.Qt.Key_Backspace:
            if has_sel:
                self.commandIssued.emit("bulk-delete")
            else:
                self._last_delete_kind = "backspace"
        elif k == QtCore.Qt.Key_Delete:
            if has_sel:
                self.commandIssued.emit("bulk-delete")
            else:
                self._last_delete_kind = "delete"
        else:
            # Any other key ends a delete run
            self._last_delete_kind = None

        # make a replacement of highlighted text its own step (no accidental coalesce with previous run)
        if e.text() and has_sel:
            self.commandIssued.emit("type-replace")

        # finally let Qt insert the character / do default edit
        super().keyPressEvent(e)

        # If this was a pure navigation key, bump snapshot after Qt moved the caret
        if (m == QtCore.Qt.NoModifier) and k in (
            QtCore.Qt.Key_Left, QtCore.Qt.Key_Right, QtCore.Qt.Key_Up, QtCore.Qt.Key_Down,
            QtCore.Qt.Key_Home, QtCore.Qt.Key_End, QtCore.Qt.Key_PageUp, QtCore.Qt.Key_PageDown
        ):
            QtCore.QTimer.singleShot(0, lambda: self._commit_mouse_nav("nav-key"))

    def mouseReleaseEvent(self, ev):
        super().mouseReleaseEvent(ev)
        # Clicking to move caret should start a new run and set the BEFORE
        self._commit_mouse_nav("mouse-release")

    def _commit_mouse_nav(self, reason: str = "nav"):
        # Update the BEFORE snapshot the controller will read for the next T step
        self._last_text_snapshot_cursor = self.get_line_col()
        self._last_text_snapshot_text   = self.toPlainText()
        # Arm a hard break so next keystroke becomes a NEW text step
        uc = getattr(self, "_outline_undo_controller", None)
        if uc:
            uc.force_break_next_text(self)
        # Optional: print for tracing
        print(f"_commit_mouse_nav updated cursor: {self._last_text_snapshot_cursor}, editor: {id(self)}")
