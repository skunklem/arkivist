from __future__ import annotations

from functools import partial
import json

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtCore import Qt
from ui.widgets.outline.data import Chapter, ChapterVersion, chapters_from_json, chapters_to_json
from ui.widgets.outline.model import ChaptersModel
from ui.widgets.outline.commands import IndentCommand, MoveWithinCommand, MoveAcrossChaptersCommand
from ui.widgets.outline.pane import ChapterPane
from ui.widgets.outline.undo import UnifiedUndoController


class _PaneClickFilter(QtCore.QObject):
    def __init__(self, controller, editor):
        super().__init__()
        self.uc = controller
        self.ed = editor
    def eventFilter(self, obj, ev):
        if obj is self.ed and ev.type() == QtCore.QEvent.MouseButtonRelease:
            # wait until Qt moves the caret, then snapshot & force a new step
            QtCore.QTimer.singleShot(0, self._after_release)
        return False
    def _after_release(self):
        # anchor BEFORE-pos at the caret where the user clicked
        ln, col = self.ed.get_line_col()
        print(f"calling on_nav_commit for _PaneClickFilter {ln},{col}")
        self.uc.on_nav_commit(self.ed, ln, col)
        # optional: end a delete-run started before the click
        self.ed._last_delete_kind = None

class ChaptersPage(QtWidgets.QWidget):
    def __init__(self, model: "ChaptersModel", parent=None):
        super().__init__(parent)
        self._pending_focus_row: int | None = None
        self._pending_focus_policy: str | None = None   # "first" | "last" | None
        self._suppress_focus = False
        # Connect once 
        QtWidgets.QApplication.instance().focusChanged.connect(self._on_focus_changed)

        self.model = model
        self.panes: list[ChapterPane] = []
        self._pane_by_cid: dict[int, ChapterPane] = {}
        self.single_mini = None
        self.workspace = parent
        print(f"workspace: {self.workspace}")

        # UI skeleton
        self.setLayout(QtWidgets.QVBoxLayout())
        self.layout().setContentsMargins(0,0,0,0)
        self.layout().setSpacing(0)

        self.list_view = None  # workspace may set this later

        self.undoStack = QtGui.QUndoStack(self)
        self.undoController = UnifiedUndoController(self, self.workspace)
        print("PAGE binds after_apply_cb to controller", id(self.undoController))
        self.undoController.after_apply_cb = self._after_apply_for_editor  # <- explicit post-apply mirror

        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.container = QtWidgets.QWidget()
        self.v = QtWidgets.QVBoxLayout(self.container)
        self.v.setContentsMargins(8,8,8,8)
        self.v.setSpacing(12)
        self._have_stretch = False

        self.scroll.setWidget(self.container)
        self.layout().addWidget(self.scroll, 1)

        # connect signals for the initial model (once)
        self._connect_model_signals(self.model)

        # build initial panes
        self._rebuild_all_panes()

    def suppress_focus(self, on: bool):
        self._suppress_focus = bool(on)

    def _on_pane_text_changed(self, ed):
        uc = self.undoController
        if uc.is_applying() or ed._suppress_text_undo_event:
            return

        # 2) mirror pane → mini as you already do
        mini = getattr(self.workspace.page, "single_mini", None)
        if mini and mini._chap_id == uc._chapter_id_for_editor(ed):
            mini._mirror_from_pane()

        print("PANE textChanged → mirror mini for ed", id(ed))

    # def _mirror_minis_for_editor(self, ed):
    #     print("PAGE _mirror_minis_for_editor: ctrl", id(self.undoController), "ed", id(ed))
    #     mini = getattr(self, "single_mini", None)
    #     print("PAGE mini", mini)
    #     if not mini:
    #         return
    #     try:
    #         cid = self.undoController._chapter_id_for_editor(ed)
    #     except Exception as e:
    #         print("PAGE cid lookup failed:", e); return
    #     print("PAGE mini present?", bool(mini), "mini._chap_id", getattr(mini, "_chap_id", None))
    #     print("PAGE cid", cid, "mini._chap_id", getattr(mini, "_chap_id", None))
    #     if getattr(mini, "_chap_id", None) != cid:
    #         return
    #     mini._mirror_from_pane()
    #     print("PAGE mirrored mini for cid", cid)

    # def _mirror_minis_for_editor(self, editor_or_none):
    #     """Called by the UndoController after any apply (undo/redo/structural)."""
    #     mini = getattr(self, "single_mini", None)
    #     if not mini:
    #         return
    #     # Only mirror if the mini is looking at the same chapter as the changed pane.
    #     p = None
    #     if editor_or_none:
    #         for pane in getattr(self, "panes", []):
    #             if pane.editor is editor_or_none:
    #                 p = pane
    #                 break
    #     if p is None:
    #         # No editor given (or couldn’t find it) — safe to mirror anyway
    #         mini._mirror_from_pane()
    #         return

    #     if p and hasattr(p, "chapter") and getattr(p.chapter, "id", None) == mini._chap_id:
    #         mini._mirror_from_pane()

    def _after_apply_for_editor(self, ed):
        self.undoController._refresh_all_snapshots()

        # mirror the mini for this chapter if present
        mini = self.workspace.page.single_mini
        if mini:
            try:
                if mini._chap_id == self.undoController._chapter_id_for_editor(ed):
                    mini._mirror_from_pane()
            except Exception:
                pass  # be robust — mirroring is best-effort

    def _pane_for_widget(self, w: QtWidgets.QWidget | None):
        if not w: return None
        while w and w is not self.container and not isinstance(w, ChapterPane):
            w = w.parentWidget()
        return w if isinstance(w, ChapterPane) else None

    def _on_focus_changed(self, old, new):
        active_pane = self._pane_for_widget(new)
        for p in self.panes:
            if p is not active_pane:
                # clear editor selection & caret highlight in other panes
                p.editor.clear_selection_visual()

    def attach_list_view(self, list_view: QtWidgets.QListView):
        self.list_view = list_view
        sel = self.list_view.selectionModel()
        sel.currentChanged.connect(self._on_list_current_changed)

    # Let the list drive viewport + (optionally) focus
    def _on_list_current_changed(self, current, previous):
        row = current.row()
        give_focus = not self._suppress_focus
        self.suppress_focus(False)
        # expand but only scroll if not fully visible
        if row >= 0:
            if self.panes[row].btnCollapse.isChecked():
                self.panes[row].btnCollapse.setChecked(False)
            if not self._is_pane_fully_visible(row):
                QtCore.QTimer.singleShot(0, lambda r=row: self._scroll_to_pane(r))
            if give_focus:
                fw = QtWidgets.QApplication.focusWidget()
                inside = fw and (self.panes[row] is fw or self.panes[row].isAncestorOf(fw))
                if not inside:
                    self.panes[row].editor.setFocus(QtCore.Qt.OtherFocusReason)

    # Called when user clicks inside a ChapterPane (title/desc/editor/characters)
    def _select_in_list_by_pane(self, pane: "ChapterPane"):
        if not self.list_view:
            return
        row = self._pane_index(pane)
        idx = self.model.index(row, 0)
        # Tell the list change handler NOT to give focus (we only want highlight + scroll)
        self.suppress_focus(True)
        self.list_view.setCurrentIndex(idx)

    def _is_pane_fully_visible(self, row: int) -> bool:
        if row < 0 or row >= len(self.panes): return False
        pane = self.panes[row]
        sb = self.scroll.verticalScrollBar()
        top = sb.value()
        bottom = top + self.scroll.viewport().height()
        y = pane.mapTo(self.container, QtCore.QPoint(0, 0)).y()
        return (y >= top) and (y + pane.height() <= bottom)

    def row_for_chapter_id(self, chap_id: int) -> int:
        return self.model.row_for_chapter_id(chap_id) if self.model else -1

    def focus_chapter_id(self, chap_id: int, caret = None, give_focus: bool = True):
        print("focus_chapter_id", chap_id, "caret", caret, "give_focus", give_focus)
        row = self.row_for_chapter_id(chap_id)
        if row >= 0:
            self.focus_chapter(row=row, caret=caret, give_focus=give_focus)
        
    def focus_chapter(self, row: int, caret = None, give_focus: bool = True):
        print("FOCUS request row", row, "give_focus", give_focus, "suppressed?", self._suppress_focus)
        if row < 0 or row >= len(self.panes): return
        pane = self.panes[row]
        
        print("checking for suppressed focus")
        if self._suppress_focus:
            return

        # expand if collapsed
        if pane.btnCollapse.isChecked():
            pane.btnCollapse.setChecked(False)

        # only scroll if not fully visible
        if not self._is_pane_fully_visible(row):
            QtCore.QTimer.singleShot(0, lambda r=row: self._scroll_to_pane(r))

        # only set focus if caller requested AND focus isn't already inside this pane
        if give_focus:
            fw = QtWidgets.QApplication.focusWidget()
            inside = fw and (pane is fw or pane.isAncestorOf(fw))
            print("focus inside pane?", inside)
            if not inside:
                if caret is not None:
                    print("FOCUS request caret", caret, "row", row, "pane_id", pane.chapter.id)
                    pane.editor.clamp_and_place_cursor(*caret)
                pane.editor.setFocus(QtCore.Qt.OtherFocusReason)

    def _on_rows_moved(self, src_parent, src_start, src_end, dst_parent, dst_row):
        """ Reorder panes to match chapter order shown in list."""
        # pull the moved panes
        moving = self.panes[src_start:src_end+1]
        del self.panes[src_start:src_end+1]
        insert_at = dst_row if dst_row <= len(self.panes) else len(self.panes)
        for i, p in enumerate(moving):
            self.panes.insert(insert_at + i, p)
            self.v.removeWidget(p)              # detach then reinsert to correct spot
            self.v.insertWidget(insert_at + i, p)
        # reindex pane.row so each pane talks to the correct Chapter
        for i, p in enumerate(self.panes):
            p.row = i
            p.reload_chapter_ref()              # keep self.chapter in sync with self.row
        # if a row is currently selected, re-focus it (keeps it in view)
        if self.list_view:
            idx = self.list_view.currentIndex()
            if idx.isValid():
                self.focus_chapter(idx.row())

    def _on_data_changed(self, topLeft: QtCore.QModelIndex, bottomRight: QtCore.QModelIndex, roles=None):
        """Refresh the affected panes' headers/combos after model data changes."""
        start = topLeft.row()
        end   = bottomRight.row()
        for r in range(start, end + 1):
            if 0 <= r < len(self.panes):
                p = self.panes[r]
                # keep row + chapter ref in sync
                p.row = r
                p.reload_chapter_ref()
                p.refresh_from_model()

    def _on_model_reset(self):
        """Model replaced or fully reset: rebuild all panes from scratch."""
        self._rebuild_all_panes()


    def _on_pane_version_changed(self, row: int, name: str):
        ch = self.model._chapters[row]
        if getattr(ch, "id", None) is not None:
            self.versionChanged.emit(ch.id, name)

    def _install_undo_hooks(self, pane):
        ed = pane.editor
        if getattr(ed, "_undo_hooks_installed", False):
            return

        print("installing undo hooks")
        # # PANE editors must have the Qt doc undo enabled
        # ed.setUndoRedoEnabled(True)
        # don't let Qt build its own undo stack; we'll do it ourselves
        ed.setUndoRedoEnabled(False)

        # 5) seed snapshots so first T step has a correct BEFORE
        self.undoController.bind_editor_cid(ed, pane.chapter.id)

        # # record text edits that Qt groups (1 per QUndoCommand)
        # ed.document().undoCommandAdded.connect(lambda e=ed: self.undoController.register_text(e))

        # # record one step per *textChanged* and coalesce in our controller
        # ed.textChanged.connect(lambda e=ed: self.undoController.register_text(e))

        # make the pane report *every* real text change to your controller
        ed.textChanged.connect(lambda e=ed: self.undoController.on_editor_text_changed(e))

        # 6) Nav committed (keyboard nav) → controller learns the BEFORE position & force-break
        ed.navCommitted.connect(lambda ln, col, e=ed: self.undoController.on_nav_commit(e, ln, col))

        # 7) Paste/cut/enter/bulk-delete/type-over-selection → force-break
        ed.commandIssued.connect(lambda kind, e=ed: self._on_editor_command(e, kind))

        # # SINGLE connection: Qt doc → our controller
        # ed.document().undoCommandAdded.connect(
        #     lambda e=ed: self.undoController.register_text(e)
        # )

        # # Optional: debug tap so you can see the Qt signal
        # ed.document().undoCommandAdded.connect(
        #     lambda e=ed: print("DOC undoCommandAdded", id(e))
        # )

        ed._undo_hooks_installed = True

    def _wire_pane(self, row, pane: "ChapterPane"):
        cid = pane.chapter.id
        ed = pane.editor
        self._pane_by_cid[cid] = pane
        # ensure cleanup if the pane/editor is destroyed
        pane.destroyed.connect(lambda _=None, c=cid: self._pane_by_cid.pop(c, None))

        print("WIRE pane:", row, "ed", id(ed))
        ed._page = self

        # 1) Hook doc→controller ONCE, seed snapshots
        self._install_undo_hooks(pane)                 # sets UndoRedoEnabled(True), seeds snapshots,
                                                    # connects doc.undoCommandAdded → controller.register_text
        ed._outline_undo_controller = self.undoController

        # 2) Structural commands go on the app stack
        ed.setUndoStack(self.undoStack)

        # 3) Pane↔controller: structural actions
        ed.requestMoveOutTop.connect(    lambda lines, p=pane: self.chapter_move_out(p, lines, up=True))
        ed.requestMoveOutBottom.connect( lambda lines, p=pane: self.chapter_move_out(p, lines, up=False))
        ed.requestCursorAbove.connect(   lambda p=pane: self.cursor_cross_chapter(p, up=True))
        ed.requestCursorBelow.connect(   lambda p=pane: self.cursor_cross_chapter(p, up=False))
        ed.requestIndent.connect(        lambda delta, p=pane: self._indent_outdent(p, delta))
        ed.requestMoveWithin.connect(    lambda direction, p=pane: self._move_within(p, direction))

        # 4) Pane→mini live mirror for normal typing; ignore during applying
        ed.textChanged.connect(lambda e=ed: self._on_pane_text_changed(e))

        # 8) Keep left list in sync when pane is interacted with
        pane.activated.connect(lambda p=pane: self._select_in_list_by_pane(p))

        # 9) Mark “pane” active on focus
        pane.activated.connect(lambda e=ed: self.undoController.set_active_surface_pane())

        if not hasattr(ed, "_focus_filter"):
            class _EF(QtCore.QObject):
                def __init__(self, controller, pane):
                    super().__init__()
                    self.controller = controller
                    self.pane = pane
                def eventFilter(self, obj, ev):
                    if ev.type() == QtCore.QEvent.FocusIn:
                        self.controller.set_active_surface_pane()
                        print("Pane FocusIn → set_active_surface_pane() row", row, "ed", id(pane.editor))
                    return False
            ed._focus_filter = _EF(self.undoController, pane)
            ed.installEventFilter(ed._focus_filter)

        # 9) Mouse click → nav commit (sets BEFORE & arms force-break)
        flt = _PaneClickFilter(self.undoController, ed)   # calls undoController.on_nav_commit(...)
        ed.installEventFilter(flt)
        ed._pane_click_filter = flt  # keep ref

    def _on_editor_command(self, ed, kind: str):
        # These edits should be their own atomic step
        if kind in ("paste", "cut", "enter", "bulk-delete"):
            self.undoController.force_break_next_text(ed)
        if kind in ("word-boundary", "type-replace"):
            print(f"calling on_nav_commit for _on_editor_command {kind}")
            self.undoController.on_nav_commit(ed, *ed.get_line_col())
        # backspace/delete series is handled via ed._last_delete_kind on the editor
    
    def _indent_outdent(self, pane: "ChapterPane", delta: int):
        ed   = pane.editor
        cid  = self.workspace.cid_for_editor(ed)
        i0,i1 = ed._selected_line_range()
        # BEFORE caret (where user initiated)
        caret_before = ed.get_line_col()

        # run the command (QUndoStack immediately .redo()s on push)
        has_sel, sL, sC, eL, eC, active_end = ed.current_selection_line_cols()
        cmd = IndentCommand(page=self, pane=pane, levels=delta, active_end=active_end)
        self.undoStack.push(cmd)

        # AFTER caret (where the editor left it)
        caret_after = ed.get_line_col()

        payload = {
            "op": "indent" if delta > 0 else "outdent",
            "delta": delta,
            "sel_lines": (i0, i1),

            "caret_before_cid": cid,
            "caret_before": caret_before,
            "caret_after_cid": cid,
            "caret_after": caret_after,
        }
        self.undoController.register_structural(cid, payload["op"], payload)
        # stamp last step’s AFTER state so redo has a crisp target
        self.undoController.live_update_after(cid=cid, pos=caret_after)

    def _move_within(self, pane, direction: int):
        ed   = pane.editor
        cid  = self.workspace.cid_for_editor(ed)
        i0,i1 = ed._selected_line_range()
        caret_before = ed.get_line_col()

        has_sel, sL, sC, eL, eC, active_end = ed.current_selection_line_cols()
        cmd = MoveWithinCommand(self, pane, i0, i1, direction, *ed._caret_line_and_column(),
                                active_end=active_end)
        if not cmd.valid:
            return

        self.undoStack.push(cmd)

        caret_after = ed.get_line_col()

        payload = {
            "op": "move_within",
            "sel_lines": (i0, i1),
            "delta": direction,

            "caret_before_cid": cid,
            "caret_before": caret_before,
            "caret_after_cid": cid,
            "caret_after": caret_after,
        }
        self.undoController.register_structural(cid, "move_within", payload)
        self.undoController.live_update_after(cid=cid, pos=caret_after)

    def _on_rows_inserted(self, parent, first, last):
        for r in range(first, last + 1):
            pane = ChapterPane(self.model, r, self.container)
            self._wire_pane(r, pane)
            self.panes.insert(r, pane)
            self.v.insertWidget(r, pane)

        # reindex pane.row
        for i, p in enumerate(self.panes):
            p.row = i
            p.reload_chapter_ref()

        # If we're suppressing focus (e.g. during load_from_db), do NOT schedule any focusing.
        if self._suppress_focus:
            print("suppressing focus after rows inserted")
            return

        # Default policy if not explicitly set (prevents first-time None)
        policy = self._pending_focus_policy or "first"
        self._pending_focus_policy = None

        # Which row to focus from the inserted range
        r = first if policy == "first" else last

        # Left list highlight without stealing editor focus
        if self.list_view:
            self.suppress_focus(True)
            self.list_view.setCurrentIndex(self.model.index(r, 0))

        # Defer focus/scroll until layout settles
        QtCore.QTimer.singleShot(0, partial(self._focus_newly_inserted_row, r))

    def request_focus_after_insert(self, policy: str = "first"):
        # "first": focus 'first' row of newly inserted range; "last": focus 'last'
        self._pending_focus_policy = policy

    def _focus_newly_inserted_row(self, row: int):
        if row < 0 or row >= len(self.panes):
            return
        pane = self.panes[row]
        if pane.btnCollapse.isChecked():
            pane.btnCollapse.setChecked(False)
        # robust scroll
        QtCore.QTimer.singleShot(0, lambda r=row: self._scroll_to_pane(r))
        # put caret in title
        pane.titleEdit.setFocus(QtCore.Qt.ShortcutFocusReason)
        pane.titleEdit.selectAll()

    def _scroll_to_pane(self, row: int, margin: int = 24):
        print("scroll_to_pane", row)
        if row < 0 or row >= len(self.panes):
            return
        pane = self.panes[row]
        # compute exact y and set scrollbar — avoids ensureWidgetVisible races
        y = pane.mapTo(self.container, QtCore.QPoint(0, 0)).y()
        sb = self.scroll.verticalScrollBar()
        sb.setValue(max(0, y - margin))

    def _on_rows_removed(self, parent, first, last):
        for r in range(last, first - 1, -1):
            pane = self.panes.pop(r)
            self.v.removeWidget(pane)
            pane.setParent(None)
            pane.deleteLater()
        # reindex pane.row
        for i, p in enumerate(self.panes):
            p.row = i
            p.reload_chapter_ref()

    def get_neighbor_pane(self, cid: int) -> "ChapterPane":
        """Return the pane that comes after the pane for cid, or before if cid comes last. Return None if there isn't one."""
        cids = list(self._pane_by_cid.keys())
        pane_num = cids.index(cid)
        if pane_num == len(cids) - 1:
            neighbor_pane = self._pane_by_cid.get(cids[0])
        else:
            neighbor_pane = self._pane_by_cid.get(cids[pane_num + 1])
        return neighbor_pane

    def current_outline_version_name_for(self, cid: int) -> str:
        db  = self.workspace.db
        pid = self.workspace.project_id
        v   = db.ui_pref_get(pid, f"outline_active:{cid}")
        if v:
            return v
        js  = db.ui_pref_get(pid, f"outline_versions:{cid}") or "[]"
        try:
            names = json.loads(js)
        except Exception:
            names = []
        return names[0] if names else "v1"

    def set_current_outline_version_name_for(self, cid: int, name: str):
        db  = self.workspace.db
        pid = self.workspace.project_id
        db.ui_pref_set(pid, f"outline_active:{cid}", name)

    def flush_outline_for_cid(self, cid: int):
        db = self.workspace.db
        pid = self.workspace.project_id
        ed = self.editor_for_chapter_id(cid)
        if not ed:
            return
        vname = self.current_outline_version_name_for(cid)
        lines = ed.lines()  # your OutlineEditor should expose this
        db.ui_pref_set(pid, f"outline:{cid}:{vname}", json.dumps(lines))
        # ensure versions list contains vname
        js = db.ui_pref_get(pid, f"outline_versions:{cid}") or "[]"
        try:
            names = json.loads(js)
        except Exception:
            names = []
        if vname not in names:
            names.append(vname)
            db.ui_pref_set(pid, f"outline_versions:{cid}", json.dumps(names))

    def flush_all_outline_versions(self):
        """Persist every open pane’s active outline version to ui_prefs."""
        ws = self.workspace
        db = ws.db
        pid = ws.project_id

        for pane in getattr(self, "panes", []):
            cid = pane.chapter.id
            # use your helper that fetches the current/active version name
            vname = self.current_outline_version_name_for(cid)
            # collect lines from editor
            lines = pane.editor.lines()
            # persist to ui_prefs (JSON list of lines)
            db.ui_pref_set(pid, f"outline:{cid}:{vname}", json.dumps(lines))

    def close_panes_for_deleted_chapter(self, cid: int):
        p = self._pane_by_cid.pop(cid, None)
        p.deleteLater()

        # purge controller’s snapshots/break flags for this cid
        self.undoController._purge_for_cid(cid)

        # If mini was showing it, flip out of mini surface and clear
        mini = getattr(self, "single_mini", None)
        if mini and mini._chap_id == cid:
            self.undoController.set_active_surface_none()
            mini.set_chapter(-1)       # or mini.clear()

    def pane_for_cid(self, cid: int):
        return self._pane_by_cid.get(cid)

    def editor_for_chapter_id(self, cid: int, ensure_open: bool = False):
        row = self.row_for_chapter_id(cid)
        if row < 0 or row >= len(self.panes):
            if ensure_open:
                self.focus_chapter_id(cid, give_focus=False)  # opens/expands if needed
                row = self.row_for_chapter_id(cid)
            else:
                return None
        if row < 0 or row >= len(self.panes):
            return None
        return getattr(self.panes[row], "editor", None)

    def _pane_index(self, pane: ChapterPane) -> int:
        return self.panes.index(pane)

    def _connect_model_signals(self, model):
        model.rowsInserted.connect(self._on_rows_inserted)
        model.rowsRemoved.connect(self._on_rows_removed)
        model.rowsMoved.connect(self._on_rows_moved)
        model.dataChanged.connect(self._on_data_changed)
        model.modelReset.connect(self._on_model_reset)

    def _disconnect_model_signals(self, model):
        for sig, slot in (
            (model.rowsInserted, self._on_rows_inserted),
            (model.rowsRemoved, self._on_rows_removed),
            (model.rowsMoved,   self._on_rows_moved),
            (model.dataChanged, self._on_data_changed),
            (model.modelReset,  self._on_model_reset),
        ):
            try:
                sig.disconnect(slot)
            except Exception:
                pass

    def set_model(self, model: ChaptersModel):
        """Swap to a new model and rebuild panes."""
        if model is self.model:
            return
        if self.model is not None:
            self._disconnect_model_signals(self.model)
        self.model = model
        self._connect_model_signals(self.model)
        # if the left list exists, update it too (done in workspace; optional here)
        self._rebuild_all_panes()

    def _clear_panes(self):
        for p in self.panes:
            p.setParent(None)
            p.deleteLater()
        self.panes.clear()

    def _ensure_single_stretch(self):
        # ensure a single stretch exists
        if not getattr(self, "_have_stretch", False):
            self.v.addStretch(1)
            self._have_stretch = True

    def _rebuild_all_panes(self):
        self._clear_panes()
        count = self.model.rowCount()
        if count <= 0:
            self._ensure_single_stretch()
            return

        # Reuse your existing insertion logic for consistency
        self._on_rows_inserted(QtCore.QModelIndex(), 0, count - 1)

        # Make sure exactly one stretch exists at the end
        self._ensure_single_stretch()

    # --- Cross-chapter logic ---
    def chapter_move_out(self, src_pane, _lines, up: bool):
        si = self._pane_index(src_pane)
        di = si - 1 if up else si + 1
        if di < 0 or di >= len(self.panes):
            return

        src = src_pane
        dst = self.panes[di]
        ed_src, ed_dst = src.editor, dst.editor
        src_cid = self.workspace.cid_for_editor(ed_src)
        dst_cid = self.workspace.cid_for_editor(ed_dst)

        # selection + BEFORE caret at source
        i0, i1 = ed_src._selected_line_range()
        cl, cc = ed_src._caret_line_and_column()
        caret_rel = max(0, min(cl - i0, i1 - i0))
        insert_at = len(ed_dst.lines()) if up else 0
        keep_sel  = ed_src.textCursor().hasSelection() or (i1 > i0)
        active_end = ed_src.current_selection_line_cols()[-1]

        caret_before = ed_src.get_line_col()

        # Push the command (this performs the move)
        self.undoStack.push(MoveAcrossChaptersCommand(
            self, src, dst, i0, i1, insert_at, caret_rel, cc,
            keep_selection=keep_sel, active_end=active_end
        ))

        # AFTER caret will now be in dst (your command should set it)
        caret_after = ed_dst.get_line_col()

        payload = {
            "op": "move_out",
            "dir": "up" if up else "down",
            "src_cid": src_cid,
            "dst_cid": dst_cid,
            "insert_at": insert_at,
            "sel_lines": (i0, i1),

            "caret_before_cid": src_cid,
            "caret_before": caret_before,
            "caret_after_cid": dst_cid,
            "caret_after": caret_after,
        }
        # anchor the timeline step to the destination chapter (that’s where we land)
        self.undoController.register_structural(dst_cid, "move_out", payload)
        self.undoController.live_update_after(cid=dst_cid, pos=caret_after)

    def cursor_cross_chapter(self, src_pane, up: bool):
        si = self._pane_index(src_pane)
        di = si - 1 if up else si + 1
        if di < 0 or di >= len(self.panes):
            return
        dst = self.panes[di]

        # desired column from source
        col = src_pane.editor.current_column()

        # auto-expand destination if collapsed
        if dst.btnCollapse.isChecked():
            dst.btnCollapse.setChecked(False)

        # place caret on last (up) or first (down) line, same column when possible
        target_line = (dst.editor.blockCount() - 1) if up else 0
        dst.editor.clamp_and_place_cursor(target_line, col)

        # focus after placing the caret to avoid any stray key movement
        dst.editor.setFocus()

    def collapse_all(self):
        for p in self.panes:
            if not p.btnCollapse.isChecked():
                p.btnCollapse.setChecked(True)

    def expand_all(self):
        for p in self.panes:
            if p.btnCollapse.isChecked():
                p.btnCollapse.setChecked(False)

class _ListKeyFilter(QtCore.QObject):
    def __init__(self, page):
        super().__init__()
        self.page = page
    def eventFilter(self, obj, ev):
        if ev.type() == QtCore.QEvent.KeyPress and ev.key() == QtCore.Qt.Key_F2:
            row = obj.currentIndex().row()
            if row >= 0:
                self.page.focus_chapter(row, give_focus=False)
                pane = self.page.panes[row]
                pane.titleEdit.setFocus(QtCore.Qt.ShortcutFocusReason)
                pane.titleEdit.selectAll()
            return True
        return False


class OutlineWorkspace(QtWidgets.QMainWindow):
    versionChanged = QtCore.Signal(int, str)  # (chapter_row, version_name)
    chapterInsertRequested = QtCore.Signal(int, int, str)  # (book_id, insert_at_index, title)
    chapterRenameRequested = QtCore.Signal(int, str)       # (chap_id, new_title)
    chapterMoveRequested   = QtCore.Signal(int, int, int)  # (chap_id, to_book_id, to_index)
    chapterDeleteRequested = QtCore.Signal(int)            # chap_id

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Flat Outline Editor — Chapters")
        self.resize(1100, 700)

        self.model = ChaptersModel([])
        self._id_to_row = {}
        self._sel_model = None

        self.db = None               # set by main window
        self.project_id = None       # active project
        self.book_id = None          # active book

        # --- Build UI ---

        # Left: chapter list + buttons (expand/collapse all)
        left = QtWidgets.QWidget(self)
        left_v = QtWidgets.QVBoxLayout(left)
        left_v.setContentsMargins(0,0,0,0)
        # Header row
        h = QtWidgets.QHBoxLayout()
        lbl = QtWidgets.QLabel("Chapters")
        btnCollapseAll = QtWidgets.QToolButton()
        btnCollapseAll.setText("Collapse all")
        btnExpandAll = QtWidgets.QToolButton()
        btnExpandAll.setText("Expand all")
        h.addWidget(lbl); h.addStretch(1); h.addWidget(btnCollapseAll); h.addWidget(btnExpandAll)
        left_v.addLayout(h)

        # Left: list of chapters (reorderable)
        self.list = QtWidgets.QListView()
        self.list.setModel(self.model)
        # Keep panes in view + expanded when the user selects in the left list
        sel = self.list.selectionModel()
        sel.currentChanged.connect(self._on_list_current_changed)
        # turn off inline editing in the list
        self.list.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.list.setDragEnabled(True); self.list.setAcceptDrops(True); self.list.setDropIndicatorShown(True)
        self.list.setDefaultDropAction(QtCore.Qt.MoveAction)
        # ensure clicking the already-selected row still brings the pane into view/focus
        self.list.activated.connect(lambda idx: self.page.focus_chapter(idx.row(), give_focus=True))
        # add context menu for add/remove
        self._in_context_menu = False
        self.list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._on_list_context_menu)
        self.list.viewport().setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.list.viewport().customContextMenuRequested.connect(self._on_list_context_menu)
        left_v.addWidget(self.list)

        # Right: multi-chapter page
        self.page = ChaptersPage(self.model, self)
        self.page.attach_list_view(self.list) # Also call this when loading outline from chapter in main app
        self.page.set_model(self.model)
        QtWidgets.QApplication.instance().installEventFilter(self)

        self._listKeyFilter = _ListKeyFilter(self.page)
        self.list.installEventFilter(self._listKeyFilter)

        # Export/Import buttons
        btnExport = QtWidgets.QPushButton("Export JSON")
        btnImport = QtWidgets.QPushButton("Import JSON")
        btnExport.clicked.connect(self._export_json)
        btnImport.clicked.connect(self._import_json)
        tools = QtWidgets.QHBoxLayout(); tools.addStretch(1); tools.addWidget(btnImport); tools.addWidget(btnExport)
        toolsW = QtWidgets.QWidget(); toolsW.setLayout(tools)

        # wire collapse/expand all buttons
        btnCollapseAll.clicked.connect(self.page.collapse_all)
        btnExpandAll.clicked.connect(self.page.expand_all)

        # Layout
        splitter = QtWidgets.QSplitter()
        splitter.addWidget(left)
        right = QtWidgets.QWidget(); rv = QtWidgets.QVBoxLayout(right); rv.setContentsMargins(0,0,0,0)
        rv.addWidget(self.page, 1); rv.addWidget(toolsW, 0)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

    def install_global_shortcuts(self, host_widget):
        if getattr(self, "_shortcuts_installed", False):
            return
        ctrl = self.page.undoController  # already exists because ChaptersPage is built

        sc_undo = QShortcut(QKeySequence.Undo, host_widget)
        sc_undo.setContext(Qt.ApplicationShortcut)
        sc_undo.activated.connect(ctrl.undo)

        sc_redo = QShortcut(QKeySequence.Redo, host_widget)
        sc_redo.setContext(Qt.ApplicationShortcut)
        sc_redo.activated.connect(ctrl.redo)

        # Optional alias
        sc_redo2 = QShortcut(QKeySequence("Ctrl+Shift+Z"), host_widget)
        sc_redo2.setContext(Qt.ApplicationShortcut)
        sc_redo2.activated.connect(ctrl.redo)

        self._shortcuts_installed = True
        self._sc_undo, self._sc_redo, self._sc_redo2 = sc_undo, sc_redo, sc_redo2

    def set_single_mini(self, mini):
        self.page.single_mini = mini

    def _on_list_context_menu(self, pos: QtCore.QPoint):
        # suppress list-driven focus during menu open
        self.page.suppress_focus(True)
        self._in_context_menu = True

        view = self.list
        idx = view.indexAt(pos) if view.viewport() is self.sender() else view.indexAt(pos)
        if idx.isValid():
            # selecting row under cursor (don’t focus editor)
            view.setCurrentIndex(idx)

        global_pos = (view.viewport().mapToGlobal(pos)
                    if self.sender() is view.viewport()
                    else view.mapToGlobal(pos))

        menu = QtWidgets.QMenu(view)
        actAbove = menu.addAction("Insert chapter above")
        actBelow = menu.addAction("Insert chapter below")
        menu.addSeparator()
        actDelete = menu.addAction("Delete chapter")

        if not idx.isValid():
            actAbove.setEnabled(False)
            actDelete.setEnabled(False)

        chosen = menu.exec(global_pos)
        self._in_context_menu = False  # menu closed

        if chosen is None:
            return

        if chosen is actAbove:
            self._insert_chapter(idx.row())
        elif chosen is actBelow:
            self._insert_chapter(idx.row() + 1)
        elif chosen is actDelete:
            self._request_delete_row(idx.row())

    def showEvent(self, ev):
        QtWidgets.QApplication.instance().installEventFilter(self)
        super().showEvent(ev)

    def hideEvent(self, ev):
        QtWidgets.QApplication.instance().removeEventFilter(self)
        super().hideEvent(ev)

    def eventFilter(self, obj, ev):
        if ev.type() == QtCore.QEvent.KeyPress:
            mods = ev.modifiers(); key = ev.key()
            is_undo = (mods & QtCore.Qt.ControlModifier) and key == QtCore.Qt.Key_Z and not (mods & QtCore.Qt.ShiftModifier)
            is_redo =((mods & QtCore.Qt.ControlModifier) and key == QtCore.Qt.Key_Y) or \
                     ((mods & QtCore.Qt.ControlModifier) and (mods & QtCore.Qt.ShiftModifier) and key == QtCore.Qt.Key_Z)
            if is_undo or is_redo:
                fw = QtWidgets.QApplication.focusWidget()
                if isinstance(fw, QtWidgets.QLineEdit):
                    try:
                        (fw.redo() if is_redo else fw.undo())
                    except Exception:
                        pass
                    finally:
                        return True
                # Only act if focus is inside this workspace
                if self.isAncestorOf(fw):
                    (self.page.undoController.redo() if is_redo else self.page.undoController.undo())
                    return True
        return super().eventFilter(obj, ev)
    
    def _is_text_input_key(self, e: QtGui.QKeyEvent) -> bool:
        if e.text():  # printable char (includes space, punctuation, etc.)
            return True
        k = e.key()
        if k in (QtCore.Qt.Key_Backspace, QtCore.Qt.Key_Delete, QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            return True
        # treat Tab/Shift+Tab as structural (indent); we don't want text coalescing to eat those
        return False

    # called by main window when wiring the workspace
    def adopt_db_and_scope(self, db, project_id: int, book_id: int):
        self.db = db
        self.project_id = int(project_id)
        self.book_id = int(book_id)

    def most_recent_editor_or_neighbor(self):
        uc = self.page.undoController

        # Current, non-deleted chapters in visual order
        items = self.model._chapters
        existing_cids = [ch.id for ch in items]

        def _iter_reversed_steps():
            tl = uc.timeline
            cut = uc.index
            # Respect current undo pointer if present; otherwise scan full timeline
            rng = tl[:cut]
            return reversed(rng)

        def _step_kind(step):
            return step[0] if step and len(step) > 0 else None

        def _step_cid(step):
            # T: ('T', cid, b_text, b_pos, a_text, a_pos, run_id)
            # S: either ('S', cid, 'kind', payload) or ('S', cid, pane, 'kind', payload)
            if not step or len(step) < 2:
                return None
            return step[1]

        def _step_struct_kind_payload(step):
            # Returns (kind, payload) for S-steps across both shapes
            if not step or step[0] != 'S':
                return None, None
            if len(step) >= 4 and isinstance(step[2], str):
                # ('S', cid, 'kind', payload)
                return step[2], (step[3] if len(step) > 3 else None)
            if len(step) >= 5 and isinstance(step[3], str):
                # ('S', cid, pane, 'kind', payload)
                return step[3], (step[4] if len(step) > 4 else None)
            return None, None

        # 1) Most-recent edited chapter that still exists
        recent_existing_cid = None
        for st in _iter_reversed_steps():
            k = _step_kind(st)
            if k in ('T', 'S'):
                cid = _step_cid(st)
                if cid in existing_cids:
                    recent_existing_cid = cid
                    break

        if recent_existing_cid is not None:
            print("Found recent existing chapter", recent_existing_cid)
            return recent_existing_cid

        # 2) Fallback: neighbor of the last deleted chapter (prefer below)
        # position of last deleted chapter (assuming we were working on it and deleted it and now want to shoift focus to a neighbor)
        deleted_pos = None
        for st in _iter_reversed_steps():
            if _step_kind(st) == 'S':
                kind, payload = _step_struct_kind_payload(st)
                if kind == "delete_chapter":
                    deleted_pos = (payload.get("position", None))
                    break

        if existing_cids:
            if isinstance(deleted_pos, int):
                if 0 <= deleted_pos < len(existing_cids):
                    print("Found neighbor below")
                    neighbor_cid = existing_cids[deleted_pos]        # the chapter that shifted up into the gap (below)
                elif deleted_pos - 1 >= 0:
                    print("Found neighbor above")
                    neighbor_cid = existing_cids[deleted_pos - 1]    # otherwise, the one above
                else:
                    print("Found first chapter")
                    neighbor_cid = existing_cids[0]
            else:
                # No position info; pick the first visible chapter
                neighbor_cid = existing_cids[0]
                print("Found first chapter because no position info", neighbor_cid)
            return neighbor_cid

        else: # no chapters remain → intentionally do nothing
            print("No chapters remain")
            return None

    def load_from_db(self, db, project_id: int, book_id: int, focus=None):
        """
        Eagerly load chapters + versioned outlines from db.ui_prefs
        Keys used:
          outline_versions:{chap_id}  -> '["v1","v2",...]'
          outline:{chap_id}:{vname}   -> '["line1","line2",...]'
        """
        self.page.suppress_focus(True)

        try:
            # rebuild model + panes

            # keep workspace scope up to date
            self.adopt_db_and_scope(db, project_id, book_id)

            chapters: list[Chapter] = []
            # db.chapter_list(...) should return rows with ["id", "title"] at least
            for ch in db.chapter_list(project_id, book_id):
                chap_id = int(ch["id"])
                title   = str(ch["title"])

                # version names (fallback to ["v1"])
                js_names = db.ui_pref_get(project_id, f"outline_versions:{chap_id}") or "[]"
                try:
                    names = json.loads(js_names)
                except Exception:
                    names = []
                if not names:
                    names = ["v1"]

                versions: list[ChapterVersion] = []
                for name in names:
                    key = f"outline:{chap_id}:{name}"
                    js_lines = db.ui_pref_get(project_id, key) or "[]"
                    try:
                        lines = json.loads(js_lines) if js_lines else []
                    except Exception:
                        lines = []
                    versions.append(ChapterVersion(name=name, lines=lines))

                chapters.append(Chapter(id=chap_id, title=title, versions=versions))

            # swap model in (use your existing setter so panes rebind correctly)
            model = ChaptersModel(chapters)
            # If your workspace has set_model(), prefer it; it typically wires page + panes.
            # Otherwise, set both and then wire.
            self.set_model(model)
            self._wire_model_for_requests() # ensure rename/move emits are connected

            # DO NOT set any focus inside the rebuild above.
        finally:
            # release suppression after the event loop has pumped one turn,
            # so queued signals (e.g., currentChanged) can be ignored safely.
            QtCore.QTimer.singleShot(0, lambda: self.page.suppress_focus(False))

        pending = getattr(self, "_pending_focus_cid", None)

        def _finalize_focus():
            if focus is False:
                return  # honor "no focus change"
            elif isinstance(focus, tuple):
                cid, pos = focus
                print("focusing in load_from_db", focus)
                self.focus_chapter(cid, pos)  # pos may be None
            else:  # focus is None → auto
                # focus most recently edited (non-deleted) chapter
                cid = pending or self.most_recent_editor_or_neighbor()
                self._pending_focus_cid = None
                self.focus_chapter(cid, None)

        print("LOAD: focusing", focus)
        QtCore.QTimer.singleShot(0, _finalize_focus)

    def _wire_model_for_requests(self):
        # rename via model edits (pane title edit, inline tree edit, etc.)
        self.model.dataChanged.connect(self._on_model_data_changed)
        # reorders (intra-/inter-book) → tell main window to apply in DB
        self.model.rowsMoved.connect(self._on_rows_moved)

    # utility: chapter id from index (adjust to your model API)
    def _cid_for_index(self, idx: QtCore.QModelIndex) -> int:
        # Prefer a method on the model; otherwise stash id in UserRole
        return int(self.model.chapter_id_for_index(idx))

    def _insert_chapter(self, row: int):
        # Ask for a title (optional)
        title, ok = QtWidgets.QInputDialog.getText(self, "New chapter", "Title:", text="New Chapter")
        if not ok:
            return
        # focus AFTER insert; “above/append” → first is enough, “below” also first (since we calculate row+1)
        title = Chapter(title.strip() or "New Chapter")  # empty version stub inside
        # emit request for DB insert (don’t mutate the model directly)
        self.chapterInsertRequested.emit(int(self.book_id), row, title)
        # new_row = self.model.insertChapter(row, title)
        self.page.request_focus_after_insert("first") # the page will scroll/focus inside rowsInserted

    def _on_model_data_changed(self, topLeft, bottomRight, roles):
        if QtCore.Qt.EditRole not in roles:
            return
        if topLeft.column() != 0:
            return
        # emit one rename per topLeft (assume single-cell edits for titles)
        cid = self._cid_for_index(topLeft)
        new_title = str(self.model.data(topLeft, QtCore.Qt.DisplayRole) or "").strip()
        if new_title:
            self.chapterRenameRequested.emit(cid, new_title)

    # Model → rows moved (drag/drop)
    def _on_rows_moved(self, srcParent, srcStart, srcEnd, dstParent, dstRow):
        # Single-row move expected for chapter panels; extend if needed
        if srcEnd != srcStart:
            return
        idx = self.model.index(dstRow, 0, dstParent)
        cid = self._cid_for_index(idx)
        # intra-book reorder (most common)
        to_index = int(dstRow)
        to_book_id = int(self.book_id)
        self.chapterMoveRequested.emit(cid, to_book_id, to_index)

    # def _focus_new_chapter(self, row: int):
    #     # select + scroll into view; put caret in title for quick rename
    #     idx = self.model.index(row, 0)
    #     self.list.setCurrentIndex(idx)
    #     self.page.focus_chapter(row, give_focus=False)   # scroll + ensure expanded
    #     pane = self.page.panes[row]
    #     pane.titleEdit.setFocus(QtCore.Qt.ShortcutFocusReason)
    #     pane.titleEdit.selectAll()

    # def _delete_chapter(self, chap_id: int):
    #     r = self.outlineWorkspace.row_for_chapter_id(chap_id)
    #     if row < 0 or row >= self.model.rowCount():
    #         return
    #     # confirm
    #     dlg = QtWidgets.QMessageBox(self)
    #     dlg.setIcon(QtWidgets.QMessageBox.Warning)
    #     dlg.setWindowTitle("Delete chapter")
    #     dlg.setText(f"Delete “{self.model.data(self.model.index(row, 0), QtCore.Qt.DisplayRole)}”?")
    #     dlg.setInformativeText("This will delete the chapter, its outline, any alternate chapter versions, and all associated notes/to-dos.")
    #     dlg.setStandardButtons(QtWidgets.QMessageBox.Cancel | QtWidgets.QMessageBox.Yes)
    #     dlg.setDefaultButton(QtWidgets.QMessageBox.Cancel)
    #     if dlg.exec() != QtWidgets.QMessageBox.Yes:
    #         return

    #     # Use existing logic to soft-delete chapter
    #     self._soft_delete_chapter(chap_id)
    #     # Remove from workspace model as well (if not fully reloading)
    #     if r >= 0:
    #         self.outlineWorkspace.model.removeRow(r)
    #         # optional: rebuild id map
    #         self.outlineWorkspace._rebuild_id_map()
    #     # Update mini tab if it was showing the deleted chapter
    #     if getattr(self, "_current_chapter_id", None) == chap_id and hasattr(self, "tabMiniOutline"):
    #         self.tabMiniOutline.set_chapter(chap_id)  # will clear editor because row<0

        # # choose a sensible next selection
        # next_row = min(row, self.model.rowCount() - 1)
        # if next_row >= 0:
        #     idx = self.model.index(next_row, 0)
        #     self.list.setCurrentIndex(idx)
        #     self.page.focus_chapter(next_row, give_focus=False)

    def _emit_insert_after_current(self, pane):
        """Context menu / toolbar action: insert a blank chapter right after this pane."""
        ws  = self.workspace
        bid = ws.book_id
        idx = pane.row + 1
        self.chapterInsertRequested.emit(int(bid), int(idx), "New Chapter")

    def _emit_rename_current(self, pane):
        """Context menu / toolbar action: rename this pane’s chapter."""
        cid = pane.chapter.id
        title = pane.chapter.title or ""
        new, ok = QtWidgets.QInputDialog.getText(self, "Rename Chapter", "New title:", text=title)
        if ok and new.strip():
            self.chapterRenameRequested.emit(int(cid), new.strip())

    def _request_delete_row(self, row: int):
        if row < 0 or row >= self.model.rowCount():
            return
        ch = self.model._chapters[row]
        chap_id = getattr(ch, "id", None)
        if chap_id is None:
            return
        
        # confirm
        dlg = QtWidgets.QMessageBox(self)
        dlg.setIcon(QtWidgets.QMessageBox.Warning)
        dlg.setWindowTitle("Delete chapter")
        dlg.setText(f"Delete “{self.model.data(self.model.index(row, 0), QtCore.Qt.DisplayRole)}”?")
        dlg.setInformativeText("This will delete the chapter, its outline, any alternate chapter versions, and all associated notes/to-dos.")
        dlg.setStandardButtons(QtWidgets.QMessageBox.Cancel | QtWidgets.QMessageBox.Yes)
        dlg.setDefaultButton(QtWidgets.QMessageBox.Cancel)
        if dlg.exec() != QtWidgets.QMessageBox.Yes:
            return

        # Let Main handle DB + removal policy
        self.chapterDeleteRequested.emit(chap_id)

        # choose a sensible next selection
        next_row = min(row, self.model.rowCount() - 1)
        if next_row >= 0:
            idx = self.model.index(next_row, 0)
            self.list.setCurrentIndex(idx)
            self.page.focus_chapter(next_row, give_focus=False)

    def flush_outline_for_cid(self, cid: int):
        if hasattr(self, "page"):
            self.page.flush_outline_for_cid(cid)

    def _export_json(self):
        # ensure panes wrote back
        for p in self.page.panes: p.sync_into_model()
        s = chapters_to_json(self.model._chapters)
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export Outline", "outline.json", "JSON (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as f: f.write(s)

    def _import_json(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import Outline", "", "JSON (*.json)")
        if not path: return
        with open(path, "r", encoding="utf-8") as f: s = f.read()
        self.model.beginResetModel()
        self.model._chapters = chapters_from_json(s)
        self.model.endResetModel()
        # rebuild page
        self.page.setParent(None)
        self.page.deleteLater()
        self.page = ChaptersPage(self.model)
        self.centralWidget().widget(1).layout().insertWidget(0, self.page)  # put back into right-side vbox

    @QtCore.Slot(int, int, list)
    def apply_order_by_ids(self, project_id: int, book_id: int, ordered_ids: list[int]):
        if not self.model or not ordered_ids:
            return
        # Build current map: id -> row
        pos = {getattr(ch, "id", None): i for i, ch in enumerate(self.model._chapters)}
        parent = QtCore.QModelIndex()
        for target_row, chap_id in enumerate(ordered_ids):
            cur_row = pos.get(chap_id, None)
            if cur_row is None or cur_row == target_row:
                continue
            # moveRows adjusts indices; compute correct dst
            dst = target_row if cur_row > target_row else target_row + 1
            self.model.beginMoveRows(parent, cur_row, cur_row, parent, dst)
            ch = self.model._chapters.pop(cur_row)
            self.model._chapters.insert(target_row, ch)
            self.model.endMoveRows()
            # rebuild mapping for subsequent moves
            pos = {getattr(ch, "id", None): i for i, ch in enumerate(self.model._chapters)}
        # refresh id map afterwards
        self._rebuild_id_map()

    def focus_chapter(self, chap_id: int, caret = None, give_focus: bool = True):
        # convenience: route to page API
        print("focus_chapter", chap_id, "caret", caret, "give_focus", give_focus)
        if hasattr(self, "page"):
            print(f"FOCUS request cid {chap_id} row {self} give_focus True suppressed? {self.page._suppress_focus is True}")
            self.page.focus_chapter_id(chap_id=chap_id, caret=caret, give_focus=give_focus)

    def editor_for_chapter_id(self, cid: int, ensure_open: bool = False):
        return self.page.editor_for_chapter_id(cid, ensure_open) if hasattr(self, "page") else None

    # Optional: deprecate the row API, or re-route it:
    def focus_chapter_row(self, row: int, give_focus: bool = True):
        # Keep working for callers that still pass a row:
        if not hasattr(self, "page") or not self.page.model:
            return
        idx = max(0, min(row, self.page.model.rowCount()-1))
        cid = self.page.model.chapter_id_for_row(idx)
        self.focus_chapter(chap_id=cid, give_focus=give_focus)

    def versions_for_row(self, row: int) -> list[str]:
        if self.model is None:
            return []
        if row < 0 or row >= self.model.rowCount():
            return []
        ch = self.model._chapters[row]
        if not getattr(ch, "versions", None):
            ch.versions = [ChapterVersion(name="v1", lines=[])]
            ch.active_index = 0
            if row < len(self.page.panes):
                self.page.panes[row]._populate_versions_combo(select_name="v1", emit=False)
        return [v.name for v in ch.versions]

    def current_version_for_row(self, row: int) -> str | None:
        if row < 0 or row >= len(self.page.panes):
            return None
        return self.page.panes[row].verCombo.currentText()

    def select_version_for_row(self, row: int, name: str):
        if row < 0 or row >= len(self.page.panes):
            return
        self.page.flush_outline_for_cid(p.chapter.id)
        pane = self.page.panes[row]
        i = pane.verCombo.findText(name)
        if i >= 0:
            pane.verCombo.setCurrentIndex(i)

    def add_version_for_row(self, row: int, name: str, clone_from_current=True):
        if row < 0 or row >= len(self.page.panes):
            return
        self.page.panes[row]._on_add_version_with_name(name, clone_from_current=clone_from_current)

    def _on_list_current_changed(self, cur: QtCore.QModelIndex, prev: QtCore.QModelIndex):
        if self.page._suppress_focus:
            return
        print("LIST changed fired; suppressed?", self.page._suppress_focus)
        row = cur.row()
        if row >= 0:
            # bring selection into view
            self.focus_chapter_row(row, give_focus=False)

    def _hook_list_selection(self):
        sm = self.list.selectionModel()
        if sm is self._sel_model:
            return
        # disconnect old (if any)
        if self._sel_model is not None:
            try: self._sel_model.currentChanged.disconnect(self._on_list_current_changed)
            except Exception: pass
        # connect new
        sm.currentChanged.connect(self._on_list_current_changed)
        self._sel_model = sm

    def _select_initial_row(self):
        # try last viewed
        last_id = None
        try:
            # expects your Database instance on self.db; if not, pass it in or call from Main
            last_id_str = self.db.ui_pref_get(self.project_id, "outline:last_chapter_id")
            last_id = int(last_id_str) if last_id_str else None
        except Exception:
            last_id = None

        row = -1
        if last_id is not None:
            row = self.row_for_chapter_id(last_id)
        if row < 0 and self.model.rowCount() > 0:
            row = 0

        if row >= 0:
            idx = self.model.index(row, 0)
            self.list.setCurrentIndex(idx)      # highlights in the tree
            # do not collapse/expand here; just bring into view
            print("INITIAL ROW:", row)
            self.focus_chapter_row(row, give_focus=False)

    def set_model(self, model: ChaptersModel, do_initial_select: bool = True):
        self.model = model or ChaptersModel([])
        # left list
        self.list.setModel(self.model)
        # right page
        self.page.set_model(self.model)
        # id map
        self._rebuild_id_map()
        # wire exapand-on-select logic
        self._hook_list_selection()
        if do_initial_select:
            self._select_initial_row() 

    # --- mapping helpers ---
    def _rebuild_id_map(self):
        self._id_to_row = {}
        if not self.model:
            return
        for r, ch in enumerate(self.model._chapters):
            cid = getattr(ch, "id", None)
            if cid is not None:
                self._id_to_row[cid] = r

    def cid_for_editor(self, ed):
        # fast path through page map
        return self.page.undoController._cid_for_editor(ed)

    def row_for_chapter_id(self, chap_id: int) -> int:
        return self._id_to_row.get(chap_id, -1)

    # --- version helpers for Main ---
    def versions_for_chapter_id(self, chap_id: int) -> list[str]:
        row = self.row_for_chapter_id(chap_id)
        if row < 0: return []
        ch = self.model._chapters[row]
        # ensure at least one version exists
        if not getattr(ch, "versions", None):
            ch.versions = [ChapterVersion(name="v1", lines=[])]
            # also refresh the pane’s combo if already constructed
            if row < len(self.page.panes):
                self.page.panes[row]._refresh_versions_combo(select_name="v1", emit=False)
        return [v.name for v in ch.versions]

    def current_version_for_chapter_id(self, chap_id: int) -> str | None:
        row = self.row_for_chapter_id(chap_id)
        if row < 0 or row >= len(self.page.panes):
            return None
        return self.page.panes[row].versionCombo.currentText()

    def select_version_for_chapter_id(self, chap_id: int, name: str):
        row = self.row_for_chapter_id(chap_id)
        if row < 0 or row >= len(self.page.panes):
            return
        pane = self.page.panes[row]
        i = pane.versionCombo.findText(name)
        if i >= 0:
            pane.versionCombo.setCurrentIndex(i)

    def add_version_for_chapter_id(self, chap_id: int, name: str, clone_from_current=True):
        """Create a new version on that chapter; optionally clone from current."""
        row = self.row_for_chapter_id(chap_id)
        if row < 0: return
        pane = self.page.panes[row]
        # Pane already has logic to create versions; reuse it
        pane._add_version_named(name, clone_from_current=clone_from_current)  # implement this tiny helper in pane

    def expand_prev_current_next(self, current_row: int | None = None, chap_id: int | None = None):
        if current_row is None and chap_id is not None:
            current_row = self.row_for_chapter_id(chap_id)
        if current_row is None:
            # try selection or default to 0
            lst = getattr(self, "list", None)
            if lst is not None:
                idx = lst.currentIndex()
                if idx.isValid():
                    current_row = idx.row()
        if current_row is None and self.model and self.model.rowCount() > 0:
            current_row = 0
        if current_row is None:
            return

        for i, p in enumerate(self.page.panes):
            want_open = (i in (current_row - 1, current_row, current_row + 1))
            if getattr(p, "btnCollapse", None):
                p.btnCollapse.setChecked(not want_open)
