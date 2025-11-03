# ---- Mini Outline Tab -------------------------------------------------------
from PySide6 import QtCore, QtGui, QtWidgets
# adjust path if needed:
# from ui.widgets.outline.editor import OutlineEditor
from ui.widgets.outline.viewer import OutlineViewer

class MiniOutlineTab(QtWidgets.QWidget):
    """
    A single OutlineEditor that mirrors the current chapter/version shown in OutlineWorkspace.
    - Edits here are reflected into the corresponding ChapterPane editor (and vice-versa).
    - Shares the Outline unified undo stack if you pass one (optional but recommended).
    """
    openFullRequested = QtCore.Signal()  # let MainWindow decide how to open full Outline

    def __init__(self, parent=None):
        super().__init__(parent)

        # UI setup
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(6)

        # Editor + narrow right column with the button
        self.editor = OutlineViewer(self)

        self.btnOpenFull = QtWidgets.QToolButton()
        self.btnOpenFull.setText("Full Outline")
        self.btnOpenFull.setToolTip("Show current chapter in main outline editor window")
        self.btnOpenFull.clicked.connect(self.openFullRequested.emit)

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        row.addWidget(self.editor, 1)  # editor takes all available width

        rightCol = QtWidgets.QVBoxLayout()
        rightCol.setContentsMargins(0, 0, 0, 0)
        rightCol.setSpacing(0)
        rightCol.addWidget(self.btnOpenFull, 0, QtCore.Qt.AlignTop | QtCore.Qt.AlignRight)
        rightCol.addStretch(1)

        row.addLayout(rightCol, 0)

        v.addLayout(row, 1)

        # Make mini display-only and visually normal (caret shows, but no edits)
        self.editor.setReadOnly(True)
        self.editor.setUndoRedoEnabled(False)
        self.editor.setCursorWidth(1)

        # Internal flags
        self._workspace = None
        self._chap_id   = None
        self._row       = -1
        self._mirroring = False
        self._live_conn = None
        self._debounce  = QtCore.QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(0)
        self._debounce.timeout.connect(self._mirror_from_pane)

    def _forward_command_to_pane(self, kind: str):
        p = self._pane_for_row()
        if not p: return
        uc = self._workspace.page.undoController
        if kind in ("paste","cut","enter","bulk-delete"):
            uc.force_break_next_text(p.editor)
        # backspace/delete series: handled by pane editor via _last_delete_kind,
        # we reflect the text over, so its value is respected by register_text().

    # ------- public API -------

    def set_workspace(self, ws: "OutlineWorkspace"):
        """Attach a workspace and register as the 'single mini'."""
        self._workspace = ws
        # Depending on where you placed it, set one of these:
        if hasattr(ws, "set_single_mini"):
            ws.set_single_mini(self)
        elif hasattr(ws, "page") and hasattr(ws.page, "set_single_mini"):
            ws.page.set_single_mini(self)
        # If a chapter was already selected, mirror now
        if self._chap_id is not None:
            self.set_chapter(self._chap_id)
        else:
            print("MiniOutlineTab: no chapter selected")

    def set_chapter(self, chap_id: int):
        self._chap_id = chap_id
        self._row = self._workspace.row_for_chapter_id(chap_id) if self._workspace else -1
        self._mirror_from_pane()

    def refresh_from_workspace(self):
        """Explicitly refresh (e.g., after version change in header/workspace)."""
        self._mirror_from_pane()

    # ------- private helpers -------

    def _current_pane(self):
        p = self._cached_pane
        if p is None or p.chapter.id != self._chap_id:
            p = self._pane_for_row()
            self._cached_pane = p
        return p

    def _current_outline_row(self) -> int:
        # Your workspace list might be named `list` (not list_view)
        if not self._workspace:
            return -1
        lst = getattr(self._workspace, "list", None)
        if lst is None:
            return -1
        idx = lst.currentIndex()
        return idx.row() if idx.isValid() else -1

    def _pane_for_row(self):
        """Return the ChapterPane for our row, if panes are created."""
        ws = self._workspace
        if not ws or not hasattr(ws, "page"):
            return None
        page = ws.page
        if not getattr(page, "panes", None):
            return None
        if self._row < 0 or self._row >= len(page.panes):
            return None
        return page.panes[self._row]

    def _mirror_from_pane(self):
        p = self._pane_for_row()
        self.editor.blockSignals(True)
        try:
            if not p:
                self.editor.set_lines([])
                return
            ed = p.editor
            text = ed.toPlainText()
            line, col = ed.get_line_col()
            aL, aC, pL, pC = ed.get_selection_anchor_and_pos()
            self.editor.set_text_and_cursor(text, line, col)
            self.editor.set_selection_anchor_and_pos(aL, aC, pL, pC)
        finally:
            self.editor.blockSignals(False)

    # def _mirror_from_pane(self):
    #     p = self._pane_for_row()
    #     if p:
    #         ed_src = p.editor
    #         text   = ed_src.toPlainText()
    #         line, col = ed_src.get_line_col()
    #         aL, aC, pL, pC = ed_src.get_selection_anchor_and_pos()

    #         self.editor.blockSignals(True)
    #         try:
    #             # Preserve caret/selection so the mini looks like “now”
    #             if hasattr(self.editor, "set_text_and_cursor"):
    #                 self.editor.set_text_and_cursor(text, line, col)
    #             else:
    #                 self.editor.setPlainText(text)
    #                 self.editor.clamp_and_place_cursor(line, col)
    #             if hasattr(self.editor, "set_selection_anchor_and_pos"):
    #                 self.editor.set_selection_anchor_and_pos(aL, aC, pL, pC)
    #         finally:
    #             self.editor.blockSignals(False)
    #         return

    #     # Fallback: no pane yet (e.g., outline window never opened)
    #     # Pull directly from the model so the mini can still show content.
    #     ws = self._workspace
    #     if ws and ws.model and 0 <= self._row < ws.model.rowCount():
    #         ch = ws.model.chapter_at(self._row) if hasattr(ws.model, "chapter_at") else None
    #         if ch:
    #             v = ch.active() if hasattr(ch, "active") else None
    #             lines = (v.lines if v else [])
    #             self._mirroring = True
    #             self.editor.blockSignals(True)
    #             try:
    #                 self.editor.set_lines(lines or [])
    #                 # Put caret at start for clarity when we’re not mirroring a pane
    #                 self.editor.clamp_and_place_cursor(0, 0)
    #             finally:
    #                 self.editor.blockSignals(False)
    #                 self._mirroring = False

    def closeEvent(self, ev: QtGui.QCloseEvent):
        # Tidy
        if self._live_conn:
            try:
                self._live_conn.disconnect()
            except Exception:
                pass
            self._live_conn = None
        super().closeEvent(ev)

    # ------- slots -------

    def _commit_mouse_nav(self, reason: str = "release"):
        p = self._current_pane()
        if not p: return
        ed = p.editor
        line, col = self.editor.get_line_col()
        # one-shot BEFORE for next text step on the **pane** editor
        ed._pending_nav_before = (ed.toPlainText(), (line, col))
        ed._last_delete_kind = None
        self._workspace.page.undoController.force_break_next_text(ed)
        print("_commit_mouse_nav updated cursor:", (line, col), "pane_ed", id(ed))

    def focusInEvent(self, e):
        super().focusInEvent(e)
        p = self._pane_for_row()
        if not p: return
        # tell the controller we’re in mini mode for this chapter
        self._workspace.page.undoController.set_active_surface_mini(p.chapter.id)
        # stamp BEFORE snapshot + run bump + break-next on the *pane* editor
        self._commit_mouse_nav("focus-in")

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        # optional: only if focus leaves *all* outline surfaces
        w = QtWidgets.QApplication.focusWidget()
        inside_outline = False
        if w:
            inside_outline = any(p.editor.isAncestorOf(w) or p.isAncestorOf(w)
                                for p in self._workspace.page.panes) or self.isAncestorOf(w)
        if not inside_outline:
            self._workspace.page.undoController.set_active_surface_none()

    def eventFilter(self, obj, ev):
        if obj is self.editor and ev.type() == QtCore.QEvent.MouseButtonRelease:
            self._commit_mouse_nav("release")
            # let the event continue so Qt finalizes the caret/selection
            return False
        if obj is self.editor and ev.type() == QtCore.QEvent.KeyPress:
            k, m = ev.key(), ev.modifiers()
            if (m & QtCore.Qt.AltModifier) and k in (QtCore.Qt.Key_Up, QtCore.Qt.Key_Down):
                # if you treat Alt+Up/Down as nav for breaking runs, commit here too
                self._commit_mouse_nav("nav-key")
                return False
        return super().eventFilter(obj, ev)

    # def _on_editor_text_changed(self):
    #     uc = self._workspace.page.undoController
    #     if uc.is_applying() or self._mirroring_from_pane:
    #         return

    #     p = self._current_pane()
    #     if not p: return
    #     ed = p.editor

    #     print("MINI before pane snapshot", id(ed),
    #         getattr(ed, "_last_text_snapshot_cursor", None),
    #         "pending", getattr(ed, "_pending_nav_before", None))

    #     new_text = self.editor.toPlainText()
    #     cur_line, cur_col = self.editor.get_line_col()

    #     ed._suppress_text_undo_event = True
    #     ed.blockSignals(True)
    #     try:
    #         ed.set_text_and_cursor(new_text, cur_line, cur_col)
    #     finally:
    #         ed.blockSignals(False)
    #         ed._suppress_text_undo_event = False

    #     uc.register_text(ed)  # controller will consume _pending_nav_before if present

    #     # refresh AFTER snapshots for the next keystroke
    #     ed._last_text_snapshot_text   = ed.toPlainText()
    #     ed._last_text_snapshot_cursor = ed.get_line_col()

    #     p.sync_into_model()

    def _forward_indent(self, delta: int):
        print("forward_indent", delta)
        p = self._pane_for_row()
        if not p: return
        self._applying_from_mini = True
        try:
            snap = self.editor.get_selection_anchor_and_pos()
            p.editor.blockSignals(True)
            try:
                p.editor.set_lines(self.editor.lines())
                p.editor.set_selection_anchor_and_pos(*snap)
            finally:
                p.editor.blockSignals(False)

            self._workspace.page._indent_outdent(p, delta)
            self._mirror_from_pane()
        finally:
            self._applying_from_mini = False

    def _forward_move_within(self, direction: int):
        print("forward_move_within", direction)
        p = self._pane_for_row()
        if not p: return
        self._applying_from_mini = True
        try:
            snap = self.editor.get_selection_anchor_and_pos()
            p.editor.blockSignals(True)
            try:
                p.editor.set_lines(self.editor.lines())
                p.editor.set_selection_anchor_and_pos(*snap)
            finally:
                p.editor.blockSignals(False)

            self._workspace.page._move_within(p, direction)
            self._mirror_from_pane()
        finally:
            self._applying_from_mini = False

    def _cursor_snapshot(self, ed):
        return ed.get_selection_anchor_and_pos()

    def _apply_cursor_snapshot(self, ed, snap):
        aL, aC, pL, pC = snap
        ed.set_selection_anchor_and_pos(aL, aC, pL, pC)

    def _sync_mini_into_pane(self, p):
        snap = self._cursor_snapshot(self.editor)
        p.editor.set_lines(self.editor.lines())
        self._apply_cursor_snapshot(p.editor, snap)

    def _sync_pane_back_into_mini(self, p):
        snap = self._cursor_snapshot(p.editor)
        self.editor.set_lines(p.editor.lines())
        self._apply_cursor_snapshot(self.editor, snap)

    # deal with undo/redo in mini
    def _mini_undo(self):
        print("MINI: Ctrl+Z")
        self._workspace.page.undoController.undo()

    def _mini_redo(self):
        print("MINI: Ctrl+Y")
        self._workspace.page.undoController.redo()
