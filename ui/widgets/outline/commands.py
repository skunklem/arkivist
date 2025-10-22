from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6 import QtCore, QtGui

from .constants import TAB_WIDTH

if TYPE_CHECKING:
    from .editor import OutlineEditor
    from .pane import ChapterPane
    from .page import ChaptersPage


class _Sel:
    __slots__ = ("i0","i1","caret_line","caret_col")
    def __init__(self, i0,i1,caret_line,caret_col):
        self.i0=i0; self.i1=i1; self.caret_line=caret_line; self.caret_col=caret_col

def _get_selection(editor: "OutlineEditor") -> _Sel:
    i0,i1 = editor._selected_line_range()
    cl, cc = editor._caret_line_and_column()
    return _Sel(i0,i1,cl,cc)

def _selection_flags(editor: "OutlineEditor"):
    c = editor.textCursor()
    i0, i1 = editor._selected_line_range()
    had_chars_selected = c.hasSelection()
    multi_line = (i1 > i0)
    return i0, i1, had_chars_selected, multi_line

def _capture_selection(editor: "OutlineEditor"):
    has_sel, sL, sC, eL, eC, active = editor.current_selection_line_cols()
    return has_sel, sL, sC, eL, eC, active

class IndentCommand(QtGui.QUndoCommand):
    def __init__(self, page, pane, levels: int, active_end: str | None):
        super().__init__("Indent" if levels > 0 else "Outdent")
        print("IndentCommand", "levels", levels, "active_end", active_end)
        self.page = page
        self.pane = pane
        self.ed = pane.editor
        self.levels = int(levels)
        self.tab = getattr(self.ed, "TAB", "    ")
        self.k = len(self.tab) * self.levels      # +ve indent, -ve outdent (chars)

        # Exact selection snapshot BEFORE the change (anchor + pos)
        # This is the one used elsewhere; no parallel snapshots.
        self._before_sel = self.ed.get_selection_anchor_and_pos()

        # Correctly unpack: (has_sel, s_line, s_col, e_line, e_col, active_end)
        has_sel, sL, sC, eL, eC, aend = self.ed.current_selection_line_cols()

        if not has_sel:
            # No selection → act on caret line only
            curL, curC = self.ed.get_line_col()
            sL = eL = curL
            sC = eC = curC
            aend = None

        # Normalize selection to line bounds for multi-line indent/outdent
        # NOTE: your redo/undo logic uses inclusive line range [first_line..last_line]
        self.first_line = min(sL, eL)
        self.last_line  = max(sL, eL)

        # Persist metadata used by redo/undo
        self._sel_has     = bool(has_sel)
        self._sL, self._sC = sL, sC
        self._eL, self._eC = eL, eC
        self._active_end   = aend  # "start"|"end"|None

        # If your redo/undo logic relies on per-line original texts or indent widths,
        # capture them here BEFORE the change (so redo can compute the diff, and
        # undo can restore). Example (uncomment if your redo/undo expects these):
        #
        # doc_lines = self.ed.lines()
        # self._before_lines = doc_lines[self.first_line : self.last_line + 1]
        #
        # If your implementation computes on the fly, you can omit this.
    # --- helper: shift selection columns by k for endpoints on affected lines
    def _shift_sel_for_affected(self, sel, k):
        aL, aC, pL, pC = sel
        def shift(L,C):
            if self.first_line <= L <= self.last_line:
                return (L, max(0, C + k))
            return (L, C)
        aL, aC = shift(aL, aC); pL, pC = shift(pL, pC)
        return (aL, aC, pL, pC)

    def _after_structural_apply(self):
        # keep snapshots aligned for next text step
        self.ed._last_text_snapshot_text   = self.ed.toPlainText()
        self.ed._last_text_snapshot_cursor = self.ed.get_line_col()
        # mirror mini now that apply is done
        cb = getattr(self.page.undoController, "after_apply_cb", None)
        if callable(cb):
            cb(self.ed)

    def redo(self):
        if hasattr(self.ed, "indent_lines"):
            self.ed.indent_lines(self.first_line, self.last_line, self.levels)
        else:
            lines = self.ed.lines()
            for i in range(self.first_line, self.last_line+1):
                s = lines[i]
                if self.levels > 0:
                    lines[i] = self.tab * self.levels + s
                else:
                    rm = min(len(s) - len(s.lstrip(" ")), -self.k) if self.k < 0 else 0
                    lines[i] = s[rm:]
            self.ed.set_lines(lines)
        # caret follows block
        if self._before_sel:
            # redo: shift the saved selection by k spaces (pos only; lines unchanged)
            aL, aC, pL, pC = self._shift_sel_for_affected(self._before_sel, self.k)  # your existing helper
            self.ed.set_selection_anchor_and_pos(aL, aC, pL, pC)

        self.focus_editor = self.ed  # caret ends up here
        # ensure mini changes to show structural redo
        self._after_structural_apply()

    def undo(self):
        if hasattr(self.ed, "indent_lines"):
            self.ed.indent_lines(self.first_line, self.last_line, -self.levels)
        else:
            lines = self.ed.lines()
            for i in range(self.first_line, self.last_line+1):
                s = lines[i]
                if self.levels > 0:
                    rm = min(len(s) - len(s.lstrip(" ")), self.k)
                    lines[i] = s[rm:]
                else:
                    lines[i] = self.tab * (-self.levels) + s
            self.ed.set_lines(lines)
        # restore exact pre-indent selection
        if self._before_sel:
            # undo: put it back exactly
            self.ed.set_selection_anchor_and_pos(*self._before_sel)

        self.focus_editor = self.ed  # caret ends up here
        # ensure mini changes to show structural undo
        self._after_structural_apply()

class MoveWithinCommand(QtGui.QUndoCommand):
    def __init__(self, page, pane, i0, i1, direction, caret_line, caret_col, active_end=None):
        super().__init__("Move lines " + ("up" if direction < 0 else "down"))
        self.page = page
        self.pane = pane
        self.ed = pane.editor

        self.i0 = int(i0)
        self.i1 = int(i1)
        self.direction = -1 if direction < 0 else 1  # -1 up, +1 down
        self.caret_line = int(caret_line)
        self.caret_col = int(caret_col)
        self.active_end = active_end  # "start" | "end" | None

        lines = list(self.ed.lines())
        n = len(lines)

        # validate destination
        if self.direction < 0:     # up
            self.dest = self.i0 - 1
            self.valid = (self.dest >= 0)
        else:                      # down
            self.dest = self.i1 + 1
            self.valid = (self.dest < n)
        if not self.valid:
            return

        # snapshot before
        self._before_lines = lines[:]  # full doc snapshot (simple & reliable)
        self._before_sel = self.ed.get_selection_anchor_and_pos()  # (aL, aC, pL, pC)

        # build after-lines by swapping block with neighbor
        if self.direction < 0:  # move up -> bring neighbor above to after block
            # new = ... + block + [line_above] + ...
            self._after_lines = (
                lines[:self.i0-1] +
                lines[self.i0:self.i1+1] +
                [lines[self.i0-1]] +
                lines[self.i1+1:]
            )
            self._line_delta = -1  # selection moves up one line
        else:  # move down -> push neighbor below before block
            # new = ... + [line_below] + block + ...
            self._after_lines = (
                lines[:self.i0] +
                [lines[self.i1+1]] +
                lines[self.i0:self.i1+1] +
                lines[self.i1+2:]
            )
            self._line_delta = +1  # selection moves down one line

    def _shift_sel_by_lines(self, sel, dlines: int):
        aL, aC, pL, pC = sel
        return (aL + dlines, aC, pL + dlines, pC)

    def _after_structural_apply(self):
        # --- snapshot setting for coalesced text steps after this structural op ---
        self.ed._last_text_snapshot_text   = self.ed.toPlainText()
        self.ed._last_text_snapshot_cursor = self.ed.get_line_col()
        cb = getattr(self.page.undoController, "after_apply_cb", None)
        if callable(cb):
            cb(self.ed)

    def redo(self):
        if not self.valid: return
        # apply after lines
        self.ed._suppress_text_undo_event = True
        try:
            self.ed.set_lines(self._after_lines)
        finally:
            self.ed._suppress_text_undo_event = False

        # move selection along with the block (columns unchanged)
        aL, aC, pL, pC = self._shift_sel_by_lines(self._before_sel, self._line_delta)
        self.ed.set_selection_anchor_and_pos(aL, aC, pL, pC)

        self.focus_editor = self.ed  # caret ends up here
        # ensure mini changes to show structural undo
        self._after_structural_apply()

    def undo(self):
        if not self.valid: return
        # restore original lines
        self.ed._suppress_text_undo_event = True
        try:
            self.ed.set_lines(self._before_lines)
        finally:
            self.ed._suppress_text_undo_event = False

        # restore original selection exactly
        self.ed.set_selection_anchor_and_pos(*self._before_sel)

        self.focus_editor = self.ed  # caret ends up here
        # ensure mini changes to show structural undo
        self._after_structural_apply()


class MoveAcrossChaptersCommand(QtGui.QUndoCommand):
    def __init__(self, page, src_pane, dst_pane, i0, i1, insert_at, caret_rel, caret_col, keep_selection: bool, active_end: str | None):
        super().__init__(f"Move block to {dst_pane.titleEdit.text()}")
        print("MoveAcrossChaptersCommand", "src", src_pane.titleEdit.text(),
              "dst", dst_pane.titleEdit.text(),
              "i0", i0, "i1", i1, "insert_at", insert_at,
              "caret_rel", caret_rel, "caret_col", caret_col,
              "keep_selection", keep_selection,
              "active_end", active_end)
        self.page = page
        self.src = src_pane
        self.dst = dst_pane
        self.ed_src = src_pane.editor
        self.ed_dst = dst_pane.editor

        self.i0 = i0; self.i1 = i1
        self.insert_at = insert_at
        self.count = (i1 - i0 + 1)
        self.caret_rel = caret_rel; self.caret_col = caret_col
        self.keep_selection = keep_selection
        self.active_end = active_end or "end"

        # capture selection columns relative to block (so we can remap)
        has_sel, sL, sC, eL, eC, _ = _capture_selection(self.src.editor)
        self.sel_rel_sL = max(0, sL - i0) if has_sel else 0
        self.sel_rel_eL = max(0, eL - i0) if has_sel else 0
        self.sel_sC = sC; self.sel_eC = eC

    def _remove_slice(self, L, start, count):
        block = L[start:start+count]
        del L[start:start+count]
        return block

    def _after_structural_apply(self):
        for ed in (self.ed_src, self.ed_dst):
            ed._last_text_snapshot_text   = ed.toPlainText()
            ed._last_text_snapshot_cursor = ed.get_line_col()
        cb = getattr(self.page.undoController, "after_apply_cb", None)
        if callable(cb):
            # mirror both; callback guards mini chapter id anyway
            cb(self.ed_src); cb(self.ed_dst)

    def redo(self):
        # remove from source
        sL = self.src.editor.lines()
        if self.i0 < 0 or self.i1 >= len(sL): return
        block = self._remove_slice(sL, self.i0, self.count)
        self.src.editor.set_lines(sL)

        # insert into destination
        dL = self.dst.editor.lines()
        pos = max(0, min(self.insert_at, len(dL)))
        dL[pos:pos] = block
        self.dst.editor.set_lines(dL)

        # place caret/select in DEST
        if self.dst.btnCollapse.isChecked():
            self.dst.btnCollapse.setChecked(False)

        if self.keep_selection:
            new_sL = pos + self.sel_rel_sL
            new_eL = pos + self.sel_rel_eL
            self.dst.editor.select_line_col_range(new_sL, self.sel_sC, new_eL, self.sel_eC, active_at=self.active_end)
        else:
            target_line = pos + min(self.caret_rel, self.count - 1)
            self.dst.editor.clamp_and_place_cursor(target_line, self.caret_col)

        # highlight the destination in the left list without causing scroll/focus elsewhere
        if self.page.list_view:
            self.page._suppress_focus_on_list_change = True
            self.page.list_view.setCurrentIndex(self.page.model.index(self.page._pane_index(self.dst), 0))

        self.dst.editor.setFocus(QtCore.Qt.OtherFocusReason)
        self.src.sync_into_model(); self.dst.sync_into_model()

        self.focus_editor = self.ed_dst  # caret ends up here
        # ensure mini changes to show structural undo
        self._after_structural_apply()

    def undo(self):
        # remove from dest
        dL = self.dst.editor.lines()
        pos = max(0, min(self.insert_at, len(dL)))
        cnt = min(self.count, max(0, len(dL) - pos))
        block = self._remove_slice(dL, pos, cnt)
        self.dst.editor.set_lines(dL)

        # put back in source
        sL = self.src.editor.lines()
        sL[self.i0:self.i0] = block
        self.src.editor.set_lines(sL)

        # place caret/select back in SOURCE
        if self.keep_selection:
            self.src.editor.select_line_col_range(self.i0 + self.sel_rel_sL, self.sel_sC,
                                                  self.i0 + self.sel_rel_eL, self.sel_eC,
                                                  active_at=self.active_end)
        else:
            self.src.editor.clamp_and_place_cursor(self.i0 + min(self.caret_rel, cnt - 1), self.caret_col)

        if self.page.list_view:
            self.page._suppress_focus_on_list_change = True
            self.page.list_view.setCurrentIndex(self.page.model.index(self.page._pane_index(self.src), 0))

        self.src.editor.setFocus(QtCore.Qt.OtherFocusReason)
        self.src.sync_into_model(); self.dst.sync_into_model()

        self.focus_editor = self.ed_src  # caret ends up here
        # ensure mini changes to show structural undo
        self._after_structural_apply()
